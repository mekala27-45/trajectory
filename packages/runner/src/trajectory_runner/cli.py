"""The command line interface.

This is how an engineer actually uses the harness, which makes it the part worth polishing.
A good CLI is the difference between a repository someone clones and one they keep.

Conventions held throughout: every command exits non-zero when it found a problem, so it
composes in CI; NO_COLOR is respected; anything long running prints a live table rather
than a wall of log lines; and no command silently swallows a failure to keep its exit code
clean.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import structlog
import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from trajectory_core.aggregate import failure_mode_counts, leaderboard
from trajectory_core.failure_modes import TAXONOMY, classify_rules, taxonomy_table
from trajectory_core.judge import LiteLLMJudge, judge_run, measure_agreement, verdict_to_hits
from trajectory_core.models import HARNESS_VERSION, Run, RunnerFingerprint, SandboxBackend
from trajectory_core.scoring import rank_failure_modes, rescore
from trajectory_runner.agent import Budget
from trajectory_runner.execute import (
    ExecutionOptions,
    check_reference,
    plan,
    prebuild_images,
    run_suite,
)
from trajectory_runner.loader import (
    LoadedTask,
    Severity,
    TaskLoadError,
    default_tasks_root,
    discover,
    validate_all,
)
from trajectory_runner.policies import POLICIES
from trajectory_runner.push import PushError, push_runs
from trajectory_runner.sandbox import ALLOW_LOCAL_ENV, docker_available
from trajectory_runner.store import (
    find_crashed_runs,
    new_run_directory,
    read_bundle,
    read_partial_steps,
    read_runs,
    write_bundle,
)

app = typer.Typer(
    name="trajectory",
    help=(
        "Evaluation harness for coding agents. Runs agents against containerized tasks "
        "with hidden tests, and scores the trajectory rather than only the outcome."
    ),
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)
tasks_app = typer.Typer(
    help="Inspect, validate and scaffold task definitions.", no_args_is_help=True
)
app.add_typer(tasks_app, name="tasks")

console = Console(no_color=bool(os.environ.get("NO_COLOR")), soft_wrap=False)
error_console = Console(stderr=True, no_color=bool(os.environ.get("NO_COLOR")))

DEFAULT_RUNS_ROOT = Path("runs")


class _StderrLogger:
    """A structlog sink that resolves `sys.stderr` at write time.

    structlog's own PrintLoggerFactory captures the stream when it is constructed. Any
    caller that swaps `sys.stderr` afterwards, which is to say any test harness and any
    code capturing output, then gets an exception from inside a log call. That turns a
    logged warning into a crash in whatever was being logged about, which is exactly
    backwards. Resolving the stream per write costs an attribute lookup and removes the
    whole failure mode.
    """

    def msg(self, message: str) -> None:
        """Write one line to the current stderr."""
        stream = sys.stderr
        try:
            stream.write(message + "\n")
            stream.flush()
        except (ValueError, OSError):
            # The stream went away mid-run. Losing a log line is acceptable; raising from
            # inside a logging call is not.
            pass

    log = debug = info = warn = warning = error = critical = exception = fatal = msg


def configure_logging(verbose: bool) -> None:
    """Pretty logs in a terminal, JSON when piped, quiet unless asked."""
    level = 10 if verbose else 30
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if sys.stderr.isatty() and os.environ.get("TRAJECTORY_LOG_FORMAT") != "json":
        processors.append(structlog.dev.ConsoleRenderer(colors=not os.environ.get("NO_COLOR")))
    else:
        processors.append(structlog.processors.JSONRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=lambda *args: _StderrLogger(),
        cache_logger_on_first_use=False,
    )


def fail(message: str, code: int = 1) -> None:
    """Print an error and exit non-zero."""
    error_console.print(f"[bold red]error[/bold red] {message}")
    raise typer.Exit(code)


def resolve_backend(requested: str | None) -> SandboxBackend:
    """Pick a sandbox backend, refusing to fall back to the unisolated one silently."""
    if requested == "local":
        if os.environ.get(ALLOW_LOCAL_ENV) != "1":
            fail(
                "the local backend is not isolated and is off by default. Set "
                f"{ALLOW_LOCAL_ENV}=1 to use it for task authoring. Runs produced this way "
                "are stamped sandbox_backend=local and are refused by the leaderboard."
            )
        return SandboxBackend.LOCAL
    if requested == "docker" or requested is None:
        if docker_available():
            return SandboxBackend.DOCKER
        if requested == "docker":
            fail("no Docker daemon is reachable. Start one, or pass --backend local.")
        if os.environ.get(ALLOW_LOCAL_ENV) == "1":
            console.print(
                "[yellow]no Docker daemon reachable, using the local backend because "
                f"{ALLOW_LOCAL_ENV}=1[/yellow]"
            )
            return SandboxBackend.LOCAL
        fail(
            "no Docker daemon is reachable. Start one, or set "
            f"{ALLOW_LOCAL_ENV}=1 and pass --backend local to work without isolation."
        )
    fail(f"unknown backend {requested!r}. Use docker or local.")
    raise AssertionError("unreachable")  # pragma: no cover


def load_tasks(tasks_root: Path | None, suite: str | None) -> list[LoadedTask]:
    """Discover tasks, turning a loader error into a clean exit."""
    try:
        root = tasks_root or default_tasks_root()
        return discover(root, suite=suite)
    except TaskLoadError as exc:
        error_console.print(f"[bold red]error[/bold red] {exc}")
        raise typer.Exit(1) from exc


def pick_task(tasks: list[LoadedTask], task_id: str) -> LoadedTask:
    """Find one task by identifier, suggesting near misses."""
    for loaded in tasks:
        if loaded.id == task_id:
            return loaded
    close = [t.id for t in tasks if task_id in t.id or t.id in task_id]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    fail(f"no task named {task_id!r} in {len(tasks)} discovered task(s).{hint}")
    raise AssertionError("unreachable")  # pragma: no cover


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logging.")] = False,
) -> None:
    """Set up logging for every command."""
    configure_logging(verbose)


@app.command()
def version() -> None:
    """Print the harness version and what it can reach."""
    console.print(f"trajectory {HARNESS_VERSION}")
    console.print(f"docker: {'available' if docker_available() else 'not reachable'}")
    console.print(
        f"local backend: {'enabled' if os.environ.get(ALLOW_LOCAL_ENV) == '1' else 'off'}"
    )


# ------------------------------------------------------------------------ tasks


@tasks_app.command("list")
def tasks_list(
    suite: Annotated[str | None, typer.Option(help="Restrict to one suite.")] = None,
    difficulty: Annotated[int | None, typer.Option(help="Restrict to one tier, 1 to 5.")] = None,
    language: Annotated[str | None, typer.Option(help="Restrict to one language.")] = None,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
) -> None:
    """List task definitions."""
    tasks = load_tasks(tasks_root, suite)
    if difficulty is not None:
        tasks = [t for t in tasks if t.task.difficulty == difficulty]
    if language is not None:
        tasks = [t for t in tasks if t.task.language.value == language]
    if not tasks:
        fail("no tasks matched those filters.")

    table = Table(title=f"{len(tasks)} task(s)", title_justify="left", header_style="bold")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("tier", justify="center")
    table.add_column("lang", no_wrap=True)
    table.add_column("ref", justify="right")
    table.add_column("budget", justify="right")
    table.add_column("title")
    for loaded in tasks:
        task = loaded.task
        table.add_row(
            task.id,
            "#" * task.difficulty,
            task.language.value,
            str(task.reference_step_count),
            str(task.max_steps),
            task.title,
        )
    console.print(table)


@tasks_app.command("validate")
def tasks_validate(
    suite: Annotated[str | None, typer.Option(help="Restrict to one suite.")] = None,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
    strict: Annotated[bool, typer.Option(help="Treat warnings as failures.")] = False,
) -> None:
    """Lint every task definition. Exits non-zero on an error."""
    tasks = load_tasks(tasks_root, suite)
    issues = validate_all(tasks)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    for issue in issues:
        colour = "red" if issue.severity is Severity.ERROR else "yellow"
        console.print(
            f"[{colour}]{issue.severity.value:7s}[/{colour}] {issue.task_id}: {issue.message}"
        )

    console.print(
        f"\n{len(tasks)} task(s), [red]{len(errors)} error(s)[/red], "
        f"[yellow]{len(warnings)} warning(s)[/yellow]"
    )
    console.print(
        "This checks everything that can be checked without running anything. Whether each "
        "reference solution still solves its task needs [bold]trajectory tasks "
        "verify-references[/bold]."
    )
    if errors or (strict and warnings):
        raise typer.Exit(1)


@tasks_app.command("new")
def tasks_new(
    task_id: Annotated[str, typer.Argument(help="Identifier, lowercase and hyphenated.")],
    suite: Annotated[str, typer.Option(help="Suite to create it in.")] = "core-12",
    language: Annotated[str, typer.Option(help="Primary language.")] = "python",
    difficulty: Annotated[int, typer.Option(min=1, max=5, help="Difficulty tier.")] = 2,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
) -> None:
    """Scaffold a task directory that already passes validation."""
    root = tasks_root or default_tasks_root()
    target = root / suite / task_id
    if target.exists():
        fail(f"{target} already exists.")

    (target / "workspace").mkdir(parents=True)
    (target / "verify").mkdir(parents=True)
    (target / "reference").mkdir(parents=True)

    (target / "task.yaml").write_text(
        f"""\
title: One line description of what is broken
description: >
  What is broken, what fixed means, and what makes this task worth having. Mention the
  plausible wrong fix, because the hidden tests should reject it.
language: {language}
difficulty: {difficulty}
tags: []

image_tag: trajectory/{task_id}
agent_prompt: |
  State the task the way a colleague would hand it over. Be unambiguous about the success
  criteria without naming the hidden tests.

max_steps: 30
timeout_seconds: 600
command_timeout_seconds: 180

verify_cmd: python -m pytest -q "$VERIFY_DIR"
verify_parser: pytest
verify_timeout_seconds: 300

reference_step_count: 2
relevant_paths:
  - src/**
""",
        encoding="utf-8",
    )
    (target / "Dockerfile").write_text(
        """\
# Pin an exact patch version. The harness records the resolved image ID on every run.
FROM python:3.12.8-slim-bookworm

ARG AGENT_USER=agent

RUN pip install --no-cache-dir pytest==8.3.4 \\
    && useradd --create-home --uid 1000 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
""",
        encoding="utf-8",
    )
    (target / "reference" / "playbook.yaml").write_text(
        """\
notes: >
  Say what the judgement call in this task is, and why the obvious shortcut is wrong.
steps:
  - tool: bash
    args:
      command: ls -la
    note: "Look before changing anything."

  - tool: finish
    args:
      summary: Replace this with the cause, not just the change.
    note: "Close out with the cause."
""",
        encoding="utf-8",
    )
    (target / "workspace" / ".gitkeep").write_text("", encoding="utf-8")
    (target / "verify" / ".gitkeep").write_text("", encoding="utf-8")

    console.print(f"[green]created[/green] {target}")
    console.print("Next: write the workspace, the hidden tests, and the reference playbook.")
    console.print(
        "Then: [bold]trajectory tasks validate[/bold] and "
        "[bold]trajectory tasks verify-references[/bold]"
    )


@tasks_app.command("verify-references")
def tasks_verify_references(
    suite: Annotated[str | None, typer.Option(help="Restrict to one suite.")] = None,
    task: Annotated[list[str] | None, typer.Option(help="Check only these tasks.")] = None,
    backend: Annotated[str | None, typer.Option(help="docker or local.")] = None,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
) -> None:
    """Replay every reference playbook and confirm it solves its task.

    Two failures this catches, both of which quietly flatter every agent: a task whose
    starting state already passes, and a task whose own reference solution has rotted.
    """
    resolved = resolve_backend(backend)
    tasks = load_tasks(tasks_root, suite)
    if task:
        wanted = set(task)
        tasks = [t for t in tasks if t.id in wanted]
    if not tasks:
        fail("no tasks matched.")

    run_dir = new_run_directory(DEFAULT_RUNS_ROOT, suite or "core-12", label="reference-check")

    table = Table(header_style="bold", title="reference solutions", title_justify="left")
    table.add_column("task", style="cyan", no_wrap=True)
    table.add_column("unfixed", justify="right")
    table.add_column("reference", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("verdict")

    failures = 0
    for loaded in tasks:
        check = check_reference(
            loaded,
            backend=resolved,
            run_dir=run_dir,
            allow_local_override=resolved is SandboxBackend.LOCAL,
            ci=bool(os.environ.get("CI")),
        )
        failures += 0 if check.ok else 1
        table.add_row(
            check.task_id,
            f"{check.unfixed_passed}/{check.unfixed_total}",
            f"{check.reference_passed}/{check.reference_total}",
            str(check.steps),
            "[green]pass[/green]" if check.ok else f"[red]fail[/red] {check.reason}",
        )
    console.print(table)
    if failures:
        fail(f"{failures} of {len(tasks)} task(s) did not behave the way a task has to.")
    console.print(
        f"[green]{len(tasks)} task(s) start broken and are solved by their own reference[/green]"
    )


@tasks_app.command("policies")
def tasks_policies() -> None:
    """List the offline agent policies the stub provider can replay."""
    table = Table(header_style="bold", title="offline policies", title_justify="left")
    table.add_column("model id", style="cyan", no_wrap=True)
    table.add_column("what it does")
    for name, spec in sorted(POLICIES.items()):
        table.add_row(f"stub:{name}", spec.summary)
    console.print(table)
    console.print(
        "\nThese are scripted agents, not models. They exist so the pipeline can be tested "
        "and validated with no network and no spend, and every run they produce carries a "
        "[bold]stub:[/bold] model identifier so it can never be read as a model result."
    )


# -------------------------------------------------------------------------- run


def _status_cell(state: str, run: Run | None) -> Text:
    """Render one row's status for the live table."""
    if state == "running":
        return Text("running", style="yellow")
    if state == "skipped":
        return Text("skipped", style="dim")
    if run is None:
        return Text("?", style="dim")
    if run.solved:
        return Text("solved", style="bold green")
    if run.status.value == "completed":
        return Text("failed", style="red")
    return Text(run.status.value, style="magenta")


class _Progress:
    """A live table of the suite run, one row per job."""

    def __init__(self, labels: list[str], budget: Budget | None) -> None:
        """Prepare a row for every planned job."""
        self.rows: dict[str, tuple[str, Run | None]] = dict.fromkeys(labels, ("queued", None))
        self.budget = budget

    def render(self) -> Table:
        """Build the table for the current state."""
        done = sum(1 for state, _ in self.rows.values() if state in ("done", "skipped"))
        title = f"running {len(self.rows)} job(s): {done} finished"
        if self.budget is not None and self.budget.limit is not None:
            title += f" | spend {self.budget.spent:.4f} of {self.budget.limit:.2f} USD"
        table = Table(title=title, title_justify="left", header_style="bold")
        table.add_column("job", style="cyan", no_wrap=True)
        table.add_column("status", no_wrap=True)
        table.add_column("steps", justify="right")
        table.add_column("tests", justify="right")
        table.add_column("cost", justify="right")
        table.add_column("modes")
        for label, (state, run) in self.rows.items():
            verification = run.verification if run else None
            table.add_row(
                label,
                _status_cell(state, run),
                str(len(run.steps)) if run else "",
                f"{verification.tests_passed}/{verification.tests_total}" if verification else "",
                f"{run.total_cost_usd:.4f}" if run else "",
                ", ".join(hit.id.value for hit in run.failure_modes) if run else "",
            )
        return table

    def update(self, label: str, state: str, run: Run | None) -> None:
        """Record a job's new state."""
        self.rows[label] = (state, run)


def _summary_table(runs: list[Run]) -> Table:
    """Per task summary shown at the end of a suite run."""
    table = Table(title="summary", title_justify="left", header_style="bold")
    table.add_column("task", style="cyan", no_wrap=True)
    table.add_column("solved", justify="right")
    table.add_column("partial", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("efficiency", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("failure modes")

    by_task: dict[str, list[Run]] = {}
    for run in runs:
        by_task.setdefault(run.task_id, []).append(run)

    for task_id in sorted(by_task):
        group = by_task[task_id]
        solved = sum(1 for r in group if r.solved)
        partials = [r.score.partial_credit for r in group if r.score]
        efficiencies = [
            r.score.step_efficiency
            for r in group
            if r.score and r.score.step_efficiency is not None
        ]
        modes = sorted({hit.id.value for r in group for hit in r.failure_modes})
        table.add_row(
            task_id,
            f"{solved}/{len(group)}",
            f"{sum(partials) / len(partials):.2f}" if partials else "",
            f"{sum(len(r.steps) for r in group) / len(group):.1f}",
            f"{sum(efficiencies) / len(efficiencies):.2f}" if efficiencies else "",
            f"{sum(r.total_cost_usd for r in group):.4f}",
            ", ".join(modes),
        )
    return table


@app.command()
def run(
    task: Annotated[str | None, typer.Option(help="Run a single task by identifier.")] = None,
    suite: Annotated[str | None, typer.Option(help="Run a whole suite.")] = None,
    model: Annotated[
        str, typer.Option(help="LiteLLM model id, or stub:<policy> for offline.")
    ] = "stub:methodical",
    seed: Annotated[list[int] | None, typer.Option(help="Seed, repeatable for several.")] = None,
    parallel: Annotated[int, typer.Option(min=1, max=16, help="Concurrent runs.")] = 1,
    budget_usd: Annotated[
        float | None, typer.Option(help="Hard spend ceiling for the whole run.")
    ] = None,
    temperature: Annotated[
        float, typer.Option(min=0.0, max=2.0, help="Sampling temperature.")
    ] = 0.0,
    judge_model: Annotated[str | None, typer.Option(help="Model for the rubric judge.")] = None,
    backend: Annotated[str | None, typer.Option(help="docker or local.")] = None,
    out: Annotated[Path | None, typer.Option(help="Run directory to write into.")] = None,
    label: Annotated[
        str | None, typer.Option(help="Name the run directory instead of timestamping it.")
    ] = None,
    rebuild: Annotated[bool, typer.Option(help="Rebuild task images even when cached.")] = False,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
) -> None:
    """Run an agent against a task or a whole suite.

    Exits non-zero when every run failed, so a smoke check in CI is one command.
    """
    if (task is None) == (suite is None):
        fail("pass exactly one of --task or --suite.")

    resolved = resolve_backend(backend)
    tasks = load_tasks(tasks_root, suite)
    selected = [pick_task(tasks, task)] if task else tasks
    if not selected:
        fail("no tasks matched.")

    seeds = seed or [0]
    suite_name = selected[0].task.suite
    run_dir = out or new_run_directory(DEFAULT_RUNS_ROOT, suite_name, label=label)
    budget = Budget(budget_usd)
    judge = LiteLLMJudge(judge_model) if judge_model else None

    jobs = plan(selected, model=model, seeds=seeds, backend=resolved, temperature=temperature)
    console.print(
        f"[bold]{len(jobs)} job(s)[/bold]: {len(selected)} task(s) x {len(seeds)} seed(s) "
        f"on {model} via the {resolved.value} backend"
    )
    if budget_usd is not None:
        console.print(f"budget ceiling {budget_usd:.2f} USD, checked before every model call")
    console.print(f"writing to {run_dir}")

    image_tags: dict[str, str] = {}
    if resolved is SandboxBackend.DOCKER:
        with console.status("building task images..."):
            image_tags = prebuild_images(jobs, rebuild=rebuild)
        console.print(f"[green]{len(image_tags)} image(s) ready[/green]")

    options = ExecutionOptions(
        run_dir=run_dir,
        allow_local_override=resolved is SandboxBackend.LOCAL,
        budget=budget,
        judge=judge,
        ci=bool(os.environ.get("CI")),
    )

    progress = _Progress([job.label for job in jobs], budget)
    with Live(progress.render(), console=console, refresh_per_second=4, transient=False) as live:

        def hook(job: Any, completed: Run | None, state: str) -> None:  # noqa: ANN401
            progress.update(job.label, state, completed)
            live.update(progress.render())

        result = run_suite(
            jobs, options, parallel=parallel, image_tags=image_tags, on_progress=hook
        )

    console.print(_summary_table(result.runs))
    bundle_path = write_bundle(
        run_dir,
        result.runs,
        suite=suite_name,
        fingerprint=result.runs[0].runner_fingerprint
        if result.runs
        else options_fingerprint(resolved),
        notes=" ".join(sys.argv),
    )

    console.print(
        f"\n[bold]{result.solved} of {len(result.runs)} solved[/bold] in "
        f"{result.wall_clock_s:.1f}s for {result.cost_usd:.4f} USD"
    )
    console.print(f"bundle: {bundle_path}")
    for job, reason in result.skipped:
        console.print(f"[yellow]skipped[/yellow] {job.label}: {reason}")

    crashed = find_crashed_runs(run_dir)
    for path in crashed:
        console.print(
            f"[yellow]partial trajectory left behind[/yellow] {path} "
            f"({len(read_partial_steps(path))} steps). That run never sealed."
        )

    if result.runs and result.solved == 0:
        raise typer.Exit(1)
    if not result.runs:
        fail("no runs executed.")


def options_fingerprint(backend: SandboxBackend) -> RunnerFingerprint:
    """Fingerprint for a bundle that ended up with no runs in it."""
    return RunnerFingerprint.capture(sandbox_backend=backend)


# ------------------------------------------------------------------ inspection


def _find_run(identifier: str, runs_root: Path) -> tuple[Run, Path]:
    """Locate a run by identifier or by path."""
    candidate = Path(identifier)
    if candidate.is_file():
        return Run.model_validate_json(candidate.read_text(encoding="utf-8")), candidate
    matches = sorted(runs_root.glob(f"*/runs/{identifier}.json"))
    if not matches:
        matches = sorted(runs_root.glob(f"**/{identifier}.json"))
    if not matches:
        fail(f"no run {identifier!r} under {runs_root}. Pass a path, or check `trajectory report`.")
    path = matches[-1]
    return Run.model_validate_json(path.read_text(encoding="utf-8")), path


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="Run identifier, or a path to a run record.")],
    runs_root: Annotated[
        Path, typer.Option(help="Where run directories live.")
    ] = DEFAULT_RUNS_ROOT,
    step: Annotated[int | None, typer.Option(help="Show only this step.")] = None,
    failures_only: Annotated[
        bool, typer.Option(help="Show only steps a failure mode points at.")
    ] = False,
    max_output: Annotated[int, typer.Option(help="Characters of output per step.")] = 1200,
) -> None:
    """Step through a trajectory in the terminal."""
    record, path = _find_run(run_id, runs_root)
    verification = record.verification
    flagged = {index for hit in record.failure_modes for index in hit.step_indices}

    header = [
        f"[bold]{record.task_id}[/bold] on [bold]{record.config.model}[/bold] "
        f"seed {record.config.seed}",
        f"status {record.status.value}, {len(record.steps)} steps, "
        f"{record.wall_clock_s:.1f}s, {record.total_cost_usd:.4f} USD",
    ]
    if verification:
        verdict = "[green]solved[/green]" if verification.passed else "[red]not solved[/red]"
        header.append(
            f"{verdict}: {verification.tests_passed} of {verification.tests_total} hidden tests"
        )
    if record.failure_modes:
        header.append(
            "failure modes: "
            + ", ".join(
                f"{hit.id.value} {hit.name} ({hit.confidence:.2f}, {hit.detector.value})"
                for hit in record.failure_modes
            )
        )
    if record.context_compressed:
        header.append("[yellow]context was compressed during this run[/yellow]")
    if record.error:
        header.append(f"[red]error[/red] {record.error}")
    console.print(Panel("\n".join(header), title=str(path), title_align="left"))

    for item in record.steps:
        if step is not None and item.index != step:
            continue
        if failures_only and item.index not in flagged:
            continue
        border = "red" if item.index in flagged else ("yellow" if item.failed else "blue")
        title = f"step {item.index}: {item.tool_name}"
        if item.exit_code is not None:
            title += f"  exit {item.exit_code}"
        if item.schema_violation:
            title += "  [rejected]"
        if item.index in flagged:
            modes = ", ".join(
                hit.id.value for hit in record.failure_modes if item.index in hit.step_indices
            )
            title += f"  <- {modes}"

        body: list[Any] = []
        if item.thought:
            body.append(Text(item.thought.strip()[:900], style="italic"))
        body.append(Syntax(json.dumps(item.tool_args, indent=2)[:1500], "json", theme="ansi_dark"))
        output = item.tool_output or "(no output)"
        if len(output) > max_output:
            output = output[:max_output] + f"\n... {len(item.tool_output) - max_output} more chars"
        body.append(Text(output))
        console.print(Panel(_stack(body), title=title, title_align="left", border_style=border))

    for hit in record.failure_modes:
        console.print(
            Panel(
                hit.evidence,
                title=f"{hit.id.value} {hit.name} ({hit.detector.value}, "
                f"confidence {hit.confidence:.2f})",
                title_align="left",
                border_style="red",
            )
        )


def _stack(items: list[Any]) -> Group:
    """Stack renderables vertically."""
    return Group(*items)


@app.command("score")
def score_command(
    run_dir: Annotated[Path, typer.Argument(help="A run directory, or a single run record.")],
    judge_model: Annotated[str | None, typer.Option(help="Also run the rubric judge.")] = None,
    judge_temperature: Annotated[float, typer.Option(help="Judge sampling temperature.")] = 0.0,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
    write: Annotated[bool, typer.Option(help="Write the rescored records back in place.")] = True,
) -> None:
    """Rescore a directory of runs without rerunning any agent.

    This is why every metric is a pure function of the trajectory: a metric added today can
    be applied to results published last month, which is the difference between a
    benchmark and a snapshot.
    """
    tasks = {t.id: t for t in load_tasks(tasks_root, None)}
    records: list[tuple[Path, Run]] = []

    if run_dir.is_file():
        records.append((run_dir, Run.model_validate_json(run_dir.read_text(encoding="utf-8"))))
    else:
        directory = run_dir / "runs" if (run_dir / "runs").is_dir() else run_dir
        for path in sorted(directory.glob("*.json")):
            records.append((path, Run.model_validate_json(path.read_text(encoding="utf-8"))))
    if not records:
        fail(f"no run records found under {run_dir}")

    judge = LiteLLMJudge(judge_model) if judge_model else None
    table = Table(
        title=f"rescored {len(records)} run(s)", title_justify="left", header_style="bold"
    )
    table.add_column("run", style="cyan", no_wrap=True)
    table.add_column("task", no_wrap=True)
    table.add_column("solved", justify="center")
    table.add_column("efficiency", justify="right")
    table.add_column("validity", justify="right")
    table.add_column("recovery", justify="right")
    table.add_column("drift", justify="right")
    table.add_column("modes")

    updated: list[Run] = []
    missing: set[str] = set()
    for path, record in records:
        loaded = tasks.get(record.task_id)
        if loaded is None:
            missing.add(record.task_id)
            continue
        rescored = rescore(record, loaded.task)
        hits = classify_rules(rescored, loaded.task)

        if judge is not None and not rescored.solved and rescored.steps:
            verdict = judge_run(judge, rescored, loaded.task, temperature=judge_temperature)
            hits.extend(verdict_to_hits(verdict))
            if rescored.score is not None:
                rescored.score.context_drift = verdict.context_drift
        rescored.failure_modes = rank_failure_modes(hits)

        updated.append(rescored)
        if write:
            path.write_text(rescored.model_dump_json(indent=2), encoding="utf-8")

        metrics = rescored.score
        table.add_row(
            rescored.id[:8],
            rescored.task_id,
            "[green]yes[/green]" if rescored.solved else "[red]no[/red]",
            f"{metrics.step_efficiency:.2f}"
            if metrics and metrics.step_efficiency is not None
            else "",
            f"{metrics.tool_call_validity:.2f}" if metrics else "",
            f"{metrics.recovery_rate:.2f}" if metrics and metrics.recovery_rate is not None else "",
            f"{metrics.context_drift:.2f}" if metrics and metrics.context_drift is not None else "",
            ", ".join(hit.id.value for hit in rescored.failure_modes),
        )

    console.print(table)
    for task_id in sorted(missing):
        console.print(f"[yellow]skipped[/yellow] runs for unknown task {task_id!r}")
    if write and updated:
        console.print(f"[green]wrote {len(updated)} rescored record(s)[/green]")
    if not updated:
        fail("nothing could be rescored: no matching task definitions.")


@app.command()
def judge(
    run_dir: Annotated[Path, typer.Argument(help="A run directory.")],
    model: Annotated[str, typer.Option(help="Model to judge with.")],
    sample: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Fraction of unsolved runs to double judge.")
    ] = 0.2,
    temperature_a: Annotated[float, typer.Option(help="First pass temperature.")] = 0.0,
    temperature_b: Annotated[float, typer.Option(help="Second pass temperature.")] = 0.7,
    tasks_root: Annotated[Path | None, typer.Option(help="Path to the tasks directory.")] = None,
) -> None:
    """Judge the unsolved runs, and measure the judge's agreement with itself.

    A harness that does not measure the reliability of its own judge has no business
    grading anyone else, so the second pass is not optional decoration: it is the number
    that tells a reader how much to trust the three judged failure modes.
    """
    tasks = {t.id: t for t in load_tasks(tasks_root, None)}
    runs = read_runs(run_dir)
    if not runs:
        fail(f"no run records under {run_dir}")
    unsolved = [r for r in runs if not r.solved and r.steps and r.task_id in tasks]
    if not unsolved:
        fail("every run in this directory was solved, so there is nothing to classify.")

    client = LiteLLMJudge(model)
    console.print(f"judging {len(unsolved)} unsolved run(s) with {model}")

    first_pass = []
    for record in unsolved:
        verdict = judge_run(client, record, tasks[record.task_id].task, temperature=temperature_a)
        first_pass.append(verdict)
        hits = [hit for hit in record.failure_modes if hit.detector.value == "rule"]
        hits.extend(verdict_to_hits(verdict))
        record.failure_modes = rank_failure_modes(hits)
        if record.score is not None:
            record.score.context_drift = verdict.context_drift
        path = run_dir / "runs" / f"{record.id}.json"
        if path.is_file():
            path.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    sample_size = max(1, round(len(unsolved) * sample)) if sample > 0 else 0
    if sample_size == 0:
        console.print("[yellow]no second pass requested, so judge agreement is unmeasured[/yellow]")
        return

    step = max(1, len(unsolved) // sample_size)
    indices = list(range(0, len(unsolved), step))[:sample_size]
    console.print(f"second pass at temperature {temperature_b} over {len(indices)} run(s)")

    second_pass = [
        judge_run(client, unsolved[i], tasks[unsolved[i].task_id].task, temperature=temperature_b)
        for i in indices
    ]
    agreement = measure_agreement([first_pass[i] for i in indices], second_pass)

    table = Table(title="judge self agreement", title_justify="left", header_style="bold")
    table.add_column("mode", style="cyan", no_wrap=True)
    table.add_column("raw", justify="right")
    table.add_column("kappa", justify="right")
    table.add_column("both", justify="right")
    table.add_column("first only", justify="right")
    table.add_column("second only", justify="right")
    table.add_column("neither", justify="right")
    for mode_id, entry in agreement.per_mode.items():
        kappa = entry.cohens_kappa
        table.add_row(
            f"{mode_id.value} {TAXONOMY[mode_id].name}",
            f"{entry.raw_agreement:.3f}",
            f"{kappa:.3f}" if kappa is not None else "n/a",
            str(entry.both_present),
            str(entry.first_only),
            str(entry.second_only),
            str(entry.neither_present),
        )
    console.print(table)
    console.print(
        f"context drift differs by {agreement.drift_mean_absolute_difference:.3f} on average "
        f"(worst case {agreement.drift_max_absolute_difference:.3f}) across "
        f"{agreement.n} double judged run(s)"
    )
    console.print(
        "kappa of n/a means the mode never fired in either pass, so the statistic is not "
        "computable. Reporting 0.0 there would read as disagreement."
    )


@app.command()
def report(
    run_dir: Annotated[
        Path | None, typer.Argument(help="A run directory. Defaults to the newest.")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="md, json or csv.")] = "md",
    runs_root: Annotated[
        Path, typer.Option(help="Where run directories live.")
    ] = DEFAULT_RUNS_ROOT,
    out: Annotated[Path | None, typer.Option(help="Write to a file instead of stdout.")] = None,
) -> None:
    """Render the leaderboard and the failure mode distribution."""
    directory = run_dir or _newest_run_dir(runs_root)
    runs = read_runs(directory)
    if not runs:
        fail(f"no run records under {directory}")

    rows = leaderboard(runs)
    modes = failure_mode_counts(runs)
    modes_on_solved = failure_mode_counts(runs, among="solved")

    if output_format == "json":
        payload = {
            "run_dir": str(directory),
            "generated_at": datetime.now(UTC).isoformat(),
            "harness_version": HARNESS_VERSION,
            "leaderboard": [row.model_dump(mode="json") for row in rows],
            "failure_modes": [mode.model_dump(mode="json") for mode in modes],
            "failure_modes_on_solved_runs": [
                mode.model_dump(mode="json") for mode in modes_on_solved
            ],
        }
        text = json.dumps(payload, indent=2)
    elif output_format == "csv":
        lines = [
            "model,suite,runs,tasks,seeds,solve_rate,solve_rate_stdev,partial_credit,"
            "step_efficiency,tool_call_validity,redundant_action_rate,recovery_rate,"
            "premature_termination_rate,mean_cost_usd,cost_per_solved_usd,mean_wall_clock_s,"
            "destructive_attempts"
        ]
        for row in rows:
            lines.append(
                ",".join(
                    str(value)
                    for value in (
                        row.model,
                        row.suite,
                        row.runs,
                        row.tasks,
                        row.seeds,
                        f"{row.solve_rate.mean:.4f}",
                        f"{row.solve_rate.stdev:.4f}",
                        f"{row.partial_credit.mean:.4f}",
                        f"{row.step_efficiency.mean:.4f}",
                        f"{row.tool_call_validity.mean:.4f}",
                        f"{row.redundant_action_rate.mean:.4f}",
                        f"{row.recovery_rate.mean:.4f}",
                        f"{row.premature_termination_rate.mean:.4f}",
                        f"{row.mean_cost_usd:.6f}",
                        "" if row.cost_per_solved_usd is None else f"{row.cost_per_solved_usd:.6f}",
                        f"{row.mean_wall_clock_s:.2f}",
                        row.destructive_attempts,
                    )
                )
            )
        text = "\n".join(lines)
    elif output_format == "md":
        text = _markdown_report(directory, runs, rows, modes, modes_on_solved)
    else:
        fail(f"unknown format {output_format!r}. Use md, json or csv.")
        raise AssertionError("unreachable")  # pragma: no cover

    if out:
        out.write_text(text + "\n", encoding="utf-8")
        console.print(f"[green]wrote[/green] {out}")
    else:
        print(text)


def _newest_run_dir(runs_root: Path) -> Path:
    """Find the most recent run directory."""
    if not runs_root.is_dir():
        fail(f"{runs_root} does not exist. Run something first.")
    candidates = [p for p in runs_root.iterdir() if p.is_dir() and (p / "runs").is_dir()]
    if not candidates:
        fail(f"no run directories under {runs_root}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _markdown_report(
    directory: Path,
    runs: list[Run],
    rows: list[Any],
    modes: list[Any],
    modes_on_solved: list[Any],
) -> str:
    """Render a report as markdown, ready to paste into RESULTS.md."""
    unsolved = sum(1 for r in runs if not r.solved)
    lines = [
        f"# Results: {directory.name}",
        "",
        f"- Harness version: `{HARNESS_VERSION}`",
        f"- Runs: {len(runs)} ({len(runs) - unsolved} solved, {unsolved} unsolved)",
        f"- Models: {', '.join(sorted({r.config.model for r in runs}))}",
        f"- Tasks: {len({r.task_id for r in runs})}",
        f"- Seeds: {sorted({r.config.seed for r in runs})}",
        "- Backends: "
        + ", ".join(sorted({r.runner_fingerprint.sandbox_backend.value for r in runs})),
        f"- Total provider spend: {sum(r.total_cost_usd for r in runs):.4f} USD",
        f"- Total wall clock: {sum(r.wall_clock_s for r in runs):.0f}s",
        "",
        "## Leaderboard",
        "",
        "Every cell is a mean across seeds with one population standard deviation. A pass "
        "rate without that spread is not a result.",
        "",
        "| model | runs | solve rate | partial credit | step efficiency | tool validity | "
        "redundancy | recovery | premature finish | cost per solved | mean wall clock |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        cost = "n/a" if row.cost_per_solved_usd is None else f"{row.cost_per_solved_usd:.4f}"
        lines.append(
            f"| `{row.model}` | {row.runs} | {row.solve_rate.render(digits=1, percent=True)} | "
            f"{row.partial_credit.render(digits=3)} | {row.step_efficiency.render(digits=3)} | "
            f"{row.tool_call_validity.render(digits=3)} | "
            f"{row.redundant_action_rate.render(digits=3)} | "
            f"{row.recovery_rate.render(digits=3)} | "
            f"{row.premature_termination_rate.render(digits=1, percent=True)} | {cost} | "
            f"{row.mean_wall_clock_s:.1f}s |"
        )

    lines += [
        "",
        "## Failure modes",
        "",
        f"Share is of the {unsolved} unsolved run(s), not of all runs.",
        "",
        "| id | mode | runs | share of unsolved |",
        "| --- | --- | --- | --- |",
    ]
    for mode in modes:
        lines.append(
            f"| {mode.id.value} | {mode.name} | {mode.count} | "
            f"{mode.share_of_failed_runs * 100:.1f}% |"
        )
    if not modes:
        lines.append("| | no failure modes recorded | 0 | 0.0% |")

    solved_count = len(runs) - unsolved
    lines += [
        "",
        "## Failure modes on runs that passed",
        "",
        "This is the view a pass rate cannot produce. A run that made malformed tool calls, "
        "invented paths, or reached for a destructive command and still got the hidden tests "
        "green is a process problem that succeeded, and it is invisible in any aggregate that "
        "only looks at failures.",
        "",
        f"Share is of the {solved_count} solved run(s).",
        "",
        "| id | mode | runs | share of solved |",
        "| --- | --- | --- | --- |",
    ]
    for mode in modes_on_solved:
        lines.append(
            f"| {mode.id.value} | {mode.name} | {mode.count} | "
            f"{mode.share_of_failed_runs * 100:.1f}% |"
        )
    if not modes_on_solved:
        lines.append("| | no failure modes on solved runs | 0 | 0.0% |")
    return "\n".join(lines)


@app.command()
def push(
    run_dir: Annotated[
        Path | None, typer.Argument(help="A run directory. Defaults to the newest.")
    ] = None,
    api_url: Annotated[str | None, typer.Option(help="Base URL of the results API.")] = None,
    api_key: Annotated[str | None, typer.Option(help="Bearer token for the write route.")] = None,
    runs_root: Annotated[
        Path, typer.Option(help="Where run directories live.")
    ] = DEFAULT_RUNS_ROOT,
    chunk_runs: Annotated[int, typer.Option(min=1, help="Runs per request.")] = 12,
    dry_run: Annotated[bool, typer.Option(help="Show what would be uploaded and stop.")] = False,
) -> None:
    """Upload a results bundle to the API."""
    directory = run_dir or _newest_run_dir(runs_root)
    url = api_url or os.environ.get("TRAJECTORY_API_URL")
    key = api_key or os.environ.get("TRAJECTORY_API_KEY")
    if not url:
        fail("pass --api-url or set TRAJECTORY_API_URL.")
    if not key and not dry_run:
        fail("pass --api-key or set TRAJECTORY_API_KEY.")

    bundle_path = directory / "bundle.json"
    if bundle_path.is_file():
        bundle = read_bundle(directory)
        runs = bundle.runs
        suite = bundle.manifest.suite
    else:
        runs = read_runs(directory)
        suite = runs[0].suite if runs else "core-12"
    if not runs:
        fail(f"no runs to push from {directory}")

    local_runs = [r for r in runs if r.runner_fingerprint.sandbox_backend.value == "local"]
    if local_runs:
        console.print(
            f"[yellow]{len(local_runs)} of {len(runs)} run(s) came from the local backend. "
            "They will appear on the leaderboard as their own rows, labelled local, and "
            "never merged with container runs, because the local backend cannot promise the "
            "agent did not see the hidden tests.[/yellow]"
        )

    console.print(f"pushing {len(runs)} run(s) from {directory} to {url}")
    if dry_run:
        console.print(
            f"[yellow]dry run[/yellow], would send {len(runs)} run(s) in chunks of {chunk_runs}"
        )
        return

    try:
        result = push_runs(
            runs,
            suite=suite,
            api_url=str(url),
            api_key=str(key),
            notes=f"pushed from {directory}",
            chunk_runs=chunk_runs,
        )
    except PushError as exc:
        error_console.print(f"[bold red]error[/bold red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]accepted {result.accepted}[/green], duplicates {result.duplicates}, "
        f"rejected {result.rejected}, in {result.chunks} request(s)"
    )
    for message in result.messages:
        console.print(f"  {message}")
    if not result.ok:
        raise typer.Exit(1)


@app.command()
def diff(
    left: Annotated[str, typer.Argument(help="First run identifier or path.")],
    right: Annotated[str, typer.Argument(help="Second run identifier or path.")],
    runs_root: Annotated[
        Path, typer.Option(help="Where run directories live.")
    ] = DEFAULT_RUNS_ROOT,
    context: Annotated[int, typer.Option(help="Steps to show after the divergence.")] = 4,
) -> None:
    """Show where two attempts at the same task diverged.

    Two agents on the same task usually agree for the first few steps and then part ways at
    one specific decision. That decision is the interesting thing, and reading two
    trajectories side by side to find it by eye is tedious enough that nobody does it.
    """
    a, a_path = _find_run(left, runs_root)
    b, b_path = _find_run(right, runs_root)
    if a.task_id != b.task_id:
        console.print(
            f"[yellow]these runs are on different tasks ({a.task_id} and {b.task_id}), so the "
            "comparison is between approaches rather than between choices[/yellow]"
        )

    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold")
    header.add_column()
    header.add_column()
    header.add_row("", str(a_path), str(b_path))
    header.add_row("model", a.config.model, b.config.model)
    header.add_row("seed", str(a.config.seed), str(b.config.seed))
    header.add_row("status", a.status.value, b.status.value)
    header.add_row("solved", "yes" if a.solved else "no", "yes" if b.solved else "no")
    header.add_row("steps", str(len(a.steps)), str(len(b.steps)))
    header.add_row("cost", f"{a.total_cost_usd:.4f}", f"{b.total_cost_usd:.4f}")
    header.add_row(
        "modes",
        ", ".join(h.id.value for h in a.failure_modes) or "none",
        ", ".join(h.id.value for h in b.failure_modes) or "none",
    )
    console.print(Panel(header, title=f"{a.task_id}", title_align="left"))

    def signature(record: Run, index: int) -> str:
        if index >= len(record.steps):
            return "(no step)"
        step_record = record.steps[index]
        args = json.dumps(step_record.tool_args, sort_keys=True)
        return f"{step_record.tool_name} {args}"

    limit = max(len(a.steps), len(b.steps))
    divergence = next(
        (i for i in range(limit) if signature(a, i) != signature(b, i)),
        None,
    )

    if divergence is None:
        console.print("[green]these trajectories are identical step for step[/green]")
        return

    console.print(f"[bold]first divergence at step {divergence}[/bold]\n")
    table = Table(header_style="bold", show_lines=True)
    table.add_column("step", justify="right", no_wrap=True)
    table.add_column(f"{a.config.model} (seed {a.config.seed})", overflow="fold")
    table.add_column(f"{b.config.model} (seed {b.config.seed})", overflow="fold")
    for index in range(max(0, divergence - 1), min(limit, divergence + context + 1)):
        marker = "->" if index == divergence else ""
        table.add_row(
            f"{marker} {index}",
            _diff_cell(a, index),
            _diff_cell(b, index),
        )
    console.print(table)


def _diff_cell(record: Run, index: int) -> Text:
    """Render one step for the diff table."""
    if index >= len(record.steps):
        return Text("(ended)", style="dim")
    step_record = record.steps[index]
    args = json.dumps(step_record.tool_args)
    body = f"{step_record.tool_name}  {args[:220]}"
    if step_record.exit_code is not None:
        body += f"\nexit {step_record.exit_code}"
    body += f"\n{(step_record.tool_output or '')[:220]}"
    style = "red" if step_record.failed or step_record.schema_violation else ""
    return Text(body, style=style)


@app.command()
def taxonomy(
    output_format: Annotated[str, typer.Option("--format", help="table or md.")] = "table",
) -> None:
    """Print the failure mode taxonomy."""
    modes = taxonomy_table()
    if output_format == "md":
        lines = [
            "| id | mode | detection | definition |",
            "| --- | --- | --- | --- |",
        ]
        for mode in modes:
            lines.append(
                f"| {mode.id.value} | {mode.name} | {mode.detection.value} | {mode.definition} |"
            )
        print("\n".join(lines))
        return

    table = Table(header_style="bold", show_lines=True)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("mode", no_wrap=True)
    table.add_column("how", no_wrap=True)
    table.add_column("definition", overflow="fold")
    for mode in modes:
        table.add_row(mode.id.value, mode.name, mode.detection.value, mode.definition)
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
