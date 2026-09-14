"""Running one task, and running a suite.

The shape of a single run, in order: build the sandbox, photograph the workspace, drive the
agent, photograph the workspace again, install the hidden tests, run them, score the
trajectory, classify what went wrong, seal the record. Nothing in that order is arbitrary.
The workspace is photographed before the first step because two failure mode rules compare
against it, and the hidden tests go in after the agent has stopped because the whole
benchmark depends on the agent never having seen them.

Suite runs use a thread pool rather than processes. The work is almost entirely waiting on
a container or an HTTP call, threads keep the shared spend ledger honest without any
inter-process plumbing, and a job queue would be infrastructure this does not need.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog

from trajectory_core import judge as judge_module
from trajectory_core.failure_modes import classify_rules
from trajectory_core.judge import JudgeClient, JudgeError
from trajectory_core.models import (
    Run,
    RunConfig,
    RunnerFingerprint,
    RunStatus,
    SandboxBackend,
    Step,
    Task,
    ToolName,
)
from trajectory_core.scoring import rank_failure_modes, score
from trajectory_runner.agent import Budget, run_agent
from trajectory_runner.loader import LoadedTask
from trajectory_runner.policies import build_script
from trajectory_runner.providers import LiteLLMProvider, Provider, StubProvider
from trajectory_runner.sandbox import (
    SandboxError,
    build_sandbox,
    build_task_image,
    docker_server_version,
)
from trajectory_runner.store import RunWriter
from trajectory_runner.verifier import VerificationError, verify

log = structlog.get_logger(__name__)

STUB_PREFIX = "stub:"


def build_provider(config: RunConfig, loaded: LoadedTask) -> Provider:
    """Select a provider from the model identifier.

    `stub:<policy>` selects an offline scripted policy built from the task's reference
    playbook. Anything else goes to LiteLLM, which is how the harness stays vendor
    agnostic without a single branch anywhere else in the codebase.
    """
    if config.model.startswith(STUB_PREFIX):
        policy = config.model[len(STUB_PREFIX) :]
        script = build_script(policy, loaded.playbook, config.seed)
        return StubProvider(config.model, script)
    return LiteLLMProvider(
        config.model,
        temperature=config.temperature,
        seed=config.seed,
        api_base=os.environ.get("OLLAMA_API_BASE") if config.model.startswith("ollama/") else None,
    )


def config_for(
    task: Task,
    *,
    model: str,
    seed: int = 0,
    backend: SandboxBackend = SandboxBackend.DOCKER,
    temperature: float = 0.0,
    max_output_bytes: int = 16_384,
) -> RunConfig:
    """Build a run configuration from a task's own limits."""
    return RunConfig(
        model=model,
        temperature=temperature,
        max_steps=task.max_steps,
        seed=seed,
        tools_enabled=list(ToolName),
        timeout_seconds=task.timeout_seconds,
        command_timeout_seconds=task.command_timeout_seconds,
        max_output_bytes=max_output_bytes,
        sandbox_backend=backend,
    )


@dataclass(slots=True)
class ExecutionOptions:
    """Everything about how a run is executed rather than what it runs."""

    run_dir: Path
    image_tag: str | None = None
    allow_local_override: bool = False
    budget: Budget | None = None
    judge: JudgeClient | None = None
    judge_temperature: float = 0.0
    ci: bool = False


def execute(loaded: LoadedTask, config: RunConfig, options: ExecutionOptions) -> Run:
    """Run one task once, from a cold sandbox to a sealed record.

    Every failure path produces a run record. A run that could not even start its sandbox
    is still a data point, and dropping it would quietly shrink the denominator of every
    aggregate that follows.

    Args:
        loaded: The task, its directory and its reference playbook.
        config: What to run and how.
        options: Where to write, what to reuse, what to cap, who judges.

    Returns:
        A sealed run record, scored and classified.
    """
    task = loaded.task
    started_at = datetime.now(UTC)
    fingerprint = RunnerFingerprint.capture(
        sandbox_backend=config.sandbox_backend,
        docker_version=(
            docker_server_version() if config.sandbox_backend is SandboxBackend.DOCKER else None
        ),
        ci=options.ci,
    )
    run = Run(
        task_id=task.id,
        suite=task.suite,
        config=config,
        started_at=started_at,
        runner_fingerprint=fingerprint,
    )

    writer = RunWriter(options.run_dir, run.id)
    log.info("run.start", run_id=run.id, task=task.id, model=config.model, seed=config.seed)

    try:
        provider = build_provider(config, loaded)
        with build_sandbox(
            task,
            loaded.directory,
            config.sandbox_backend,
            image_tag=options.image_tag,
            allow_local_override=options.allow_local_override,
        ) as sandbox:
            run.image_id = sandbox.image_id()
            run.initial_workspace = sandbox.manifest()

            outcome = run_agent(
                task,
                provider,
                sandbox,
                max_steps=config.max_steps,
                timeout_seconds=config.timeout_seconds,
                command_timeout_seconds=config.command_timeout_seconds,
                cap_bytes=config.max_output_bytes,
                tools_enabled=config.tools_enabled,
                budget=options.budget,
                on_step=writer.append,
            )
            run.steps = outcome.steps
            run.status = outcome.status
            run.context_compressed = outcome.context_compressed
            run.error = outcome.error
            run.final_workspace = sandbox.manifest()

            try:
                run.verification = verify(task, sandbox)
            except VerificationError as exc:
                run.error = f"{run.error + '; ' if run.error else ''}verification failed: {exc}"
                log.error("run.verification_failed", run_id=run.id, task=task.id, error=str(exc))

    except SandboxError as exc:
        run.status = RunStatus.ERROR
        run.error = str(exc)
        log.error("run.sandbox_failed", run_id=run.id, task=task.id, error=str(exc))
    except Exception as exc:
        run.status = RunStatus.ERROR
        run.error = f"{type(exc).__name__}: {exc}"
        log.exception("run.failed", run_id=run.id, task=task.id)

    run.seal()
    drift = _apply_judge(run, task, options)
    run.score = score(run, task, context_drift=drift)
    writer.seal(run)

    log.info(
        "run.done",
        run_id=run.id,
        task=task.id,
        status=run.status.value,
        solved=run.solved,
        steps=len(run.steps),
        cost_usd=run.total_cost_usd,
        modes=[hit.id.value for hit in run.failure_modes],
    )
    return run


def _apply_judge(run: Run, task: Task, options: ExecutionOptions) -> float | None:
    """Classify failure modes, adding the judged ones when a judge was supplied.

    The judge runs only on unsolved runs. Judging a run that passed every hidden test
    costs money to answer a question nobody asked, and the three judged modes are all
    about how an attempt went wrong.
    """
    hits = classify_rules(run, task)
    drift: float | None = None

    if options.judge is not None and not run.solved and run.steps:
        try:
            verdict = judge_module.judge_run(
                options.judge, run, task, temperature=options.judge_temperature
            )
            drift = verdict.context_drift
            hits.extend(judge_module.verdict_to_hits(verdict))
        except JudgeError as exc:
            log.warning("run.judge_failed", run_id=run.id, task=task.id, error=str(exc)[:300])
            run.error = f"{run.error + '; ' if run.error else ''}judge failed: {exc}"

    run.failure_modes = rank_failure_modes(hits)
    return drift


# ------------------------------------------------------------------ suite runs


@dataclass(slots=True)
class Job:
    """One planned run."""

    loaded: LoadedTask
    config: RunConfig

    @property
    def label(self) -> str:
        """Human readable identity, used in progress output."""
        return f"{self.loaded.id} seed {self.config.seed}"


@dataclass(slots=True)
class SuiteResult:
    """What a suite run produced."""

    runs: list[Run] = field(default_factory=list)
    skipped: list[tuple[Job, str]] = field(default_factory=list)
    wall_clock_s: float = 0.0

    @property
    def solved(self) -> int:
        """Runs that passed every hidden test."""
        return sum(1 for run in self.runs if run.solved)

    @property
    def cost_usd(self) -> float:
        """Total provider spend."""
        return round(sum(run.total_cost_usd for run in self.runs), 6)


ProgressHook = Callable[[Job, Run | None, str], None]
"""Called when a job starts (run is None) and when it finishes."""


def plan(
    tasks: Sequence[LoadedTask],
    *,
    model: str,
    seeds: Sequence[int],
    backend: SandboxBackend,
    temperature: float = 0.0,
) -> list[Job]:
    """Build the job list for a suite run, task major so progress reads naturally."""
    return [
        Job(
            loaded,
            config_for(
                loaded.task, model=model, seed=seed, backend=backend, temperature=temperature
            ),
        )
        for loaded in tasks
        for seed in seeds
    ]


def prebuild_images(jobs: Sequence[Job], *, rebuild: bool = False) -> dict[str, str]:
    """Build every image the plan needs, once, before any run starts.

    Building inside the thread pool means four workers racing to build the same image on
    the first seed of every task. Doing it up front costs one pass and makes the progress
    output honest about where the time went.
    """
    tags: dict[str, str] = {}
    for job in jobs:
        if job.config.sandbox_backend is not SandboxBackend.DOCKER:
            continue
        if job.loaded.id in tags:
            continue
        tags[job.loaded.id] = build_task_image(
            job.loaded.task, job.loaded.directory, rebuild=rebuild
        )
    return tags


def run_suite(
    jobs: Sequence[Job],
    options: ExecutionOptions,
    *,
    parallel: int = 1,
    image_tags: dict[str, str] | None = None,
    on_progress: ProgressHook | None = None,
) -> SuiteResult:
    """Execute a plan, respecting the shared budget.

    Args:
        jobs: Planned runs.
        options: Execution options shared across every run.
        parallel: Worker count. One container per worker, so size it to the machine.
        image_tags: Pre-built image tags keyed by task id.
        on_progress: Called when each job starts and finishes.

    Returns:
        The runs that executed, the jobs skipped because the budget ran out, and the
        total wall clock.
    """
    started = time.monotonic()
    result = SuiteResult()
    tags = image_tags or {}

    def execute_one(job: Job) -> Run:
        if on_progress:
            on_progress(job, None, "running")
        per_job = ExecutionOptions(
            run_dir=options.run_dir,
            image_tag=tags.get(job.loaded.id),
            allow_local_override=options.allow_local_override,
            budget=options.budget,
            judge=options.judge,
            judge_temperature=options.judge_temperature,
            ci=options.ci,
        )
        return execute(job.loaded, job.config, per_job)

    pending = list(jobs)

    if parallel <= 1:
        for job in pending:
            if options.budget is not None and options.budget.exhausted():
                result.skipped.append((job, "budget exhausted"))
                if on_progress:
                    on_progress(job, None, "skipped")
                continue
            run = execute_one(job)
            result.runs.append(run)
            if on_progress:
                on_progress(job, run, "done")
    else:
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="trajectory") as pool:
            futures = {}
            for job in pending:
                if options.budget is not None and options.budget.exhausted():
                    result.skipped.append((job, "budget exhausted"))
                    if on_progress:
                        on_progress(job, None, "skipped")
                    continue
                futures[pool.submit(execute_one, job)] = job
            for future in as_completed(futures):
                job = futures[future]
                run = future.result()
                result.runs.append(run)
                if on_progress:
                    on_progress(job, run, "done")

    result.runs.sort(key=lambda run: run.id)
    result.wall_clock_s = round(time.monotonic() - started, 3)
    return result


def steps_of(run: Run) -> list[Step]:
    """Convenience accessor used by the replay and diff commands."""
    return list(run.steps)


# ------------------------------------------------------------ reference checks


@dataclass(slots=True)
class ReferenceCheck:
    """The result of replaying one task's reference playbook."""

    task_id: str
    unfixed_passed: int
    unfixed_total: int
    reference_passed: int
    reference_total: int
    steps: int
    status: str
    error: str | None

    @property
    def starts_broken(self) -> bool:
        """True when the untouched workspace fails its hidden tests, as a task must."""
        return self.unfixed_passed < self.unfixed_total or self.unfixed_total == 0

    @property
    def reference_solves(self) -> bool:
        """True when the reference playbook drove every hidden test green."""
        return self.reference_total > 0 and self.reference_passed == self.reference_total

    @property
    def ok(self) -> bool:
        """True when the task behaves the way a task has to."""
        return self.starts_broken and self.reference_solves and self.status == "completed"

    @property
    def reason(self) -> str:
        """Why the check failed, in a phrase fit for a table cell."""
        if self.ok:
            return "pass"
        if not self.starts_broken:
            return "starts green, so it measures nothing"
        if not self.reference_solves:
            return "the reference solution does not solve it"
        return self.error or self.status


def check_reference(
    loaded: LoadedTask,
    *,
    backend: SandboxBackend,
    run_dir: Path,
    allow_local_override: bool = False,
    ci: bool = False,
) -> ReferenceCheck:
    """Verify a task starts broken and that its reference playbook fixes it.

    Two failures this catches, both of which happen in real benchmarks and both of which
    silently flatter every agent that attempts the task. A task whose starting state
    already passes is scored by everyone. A task whose own reference solution has stopped
    passing cannot be scored by anyone, and the suite average moves for reasons that have
    nothing to do with any model.

    Args:
        loaded: The task to check.
        backend: Sandbox backend to use.
        run_dir: Where to write the reference run record.
        allow_local_override: Bypass the local backend opt in, for this project's own CI.
        ci: Stamp the run as produced on a CI runner.

    Returns:
        Before and after test counts and a verdict.
    """
    task = loaded.task
    with build_sandbox(
        task, loaded.directory, backend, allow_local_override=allow_local_override
    ) as sandbox:
        before = verify(task, sandbox)

    run = execute(
        loaded,
        config_for(task, model="stub:methodical", backend=backend),
        ExecutionOptions(run_dir=run_dir, allow_local_override=allow_local_override, ci=ci),
    )
    after = run.verification
    return ReferenceCheck(
        task_id=task.id,
        unfixed_passed=before.tests_passed,
        unfixed_total=before.tests_total,
        reference_passed=after.tests_passed if after else 0,
        reference_total=after.tests_total if after else 0,
        steps=len(run.steps),
        status=run.status.value,
        error=run.error,
    )
