#!/usr/bin/env python3
"""Plan a model panel from a committed request: several real models, one job each.

One model on the leaderboard says how that model did. It cannot say whether the tasks tell
models apart, which is the question an evaluation has to answer before anyone trusts it. A
panel answers it, and this turns a request file into the jobs that run one.

The request is a committed JSON file, so every model figure the repository publishes has a
reviewable record of what was asked for and under which caps, in the same history as the
code that produced it.

Every model is probed with one tool call before its job is planned. A model that fails the
probe is left out with the reason in the summary, instead of failing a job four hundred
calls in, and the rest of the panel runs regardless.

The jobs share one key, and a free key is rate limited across every model it calls, so the
total rate in the request is split evenly between the jobs that run at once. The call
ceiling defaults to the most calls the requested tasks and seeds could make, every run
taken to its step ceiling, so a seed is never cut part way through.

    uv run python scripts/plan_panel.py model-requests/panel.json           # validate, print
    uv run python scripts/plan_panel.py model-requests/panel.json --smoke   # probe each first
    uv run python scripts/plan_panel.py --summarise incoming/               # count the records
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trajectory_runner.loader import LoadedTask, discover
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "scripts" / "smoke_model.py"

# A provider prefix, then a vendor path. Nothing a shell would treat specially, because the
# identifier ends up on a command line in the workflow.
MODEL_RE = re.compile(r"^[a-z0-9_]+(/[A-Za-z0-9._:-]+)+$")
# The free NVIDIA key is limited to about forty requests a minute across every model.
RPM_CEILING = 40.0
TOP_LEVEL = {
    "purpose",
    "suite",
    "temperature",
    "rpm_total",
    "max_parallel",
    "parallel",
    "seeds",
    "judge_model",
    "models",
}
ENTRY_KEYS = {"model", "tasks", "seeds", "max_calls", "note"}


class RequestError(ValueError):
    """The request file asks for something the panel cannot run."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One model in the panel, with the slice of the matrix it runs."""

    model: str
    tasks: tuple[str, ...]
    seeds: tuple[int, ...]
    max_calls: int
    note: str = ""

    @property
    def slug(self) -> str:
        """A short name for the job and its artifact, unique within a panel."""
        name = self.model.rsplit("/", 1)[-1].lower()
        return re.sub(r"[^a-z0-9.-]+", "-", name).strip("-")

    def command(
        self,
        *,
        suite: str,
        rpm: float,
        temperature: float,
        parallel: int,
        judge_model: str | None,
    ) -> list[str]:
        """The run_matrix arguments for this entry's job."""
        args = [
            "--suite",
            suite,
            "--model",
            self.model,
            "--label",
            f"panel-{self.slug}",
            "--max-calls",
            str(self.max_calls),
            "--rpm",
            f"{rpm:g}",
            "--parallel",
            str(parallel),
            "--order",
            "seed",
            "--temperature",
            f"{temperature:g}",
        ]
        for seed in self.seeds:
            args += ["--seed", str(seed)]
        for task in self.tasks:
            args += ["--task", task]
        if judge_model:
            args += ["--judge-model", judge_model]
        return args


@dataclass(frozen=True, slots=True)
class Request:
    """A validated panel request."""

    purpose: str
    suite: str
    temperature: float
    rpm_total: float
    max_parallel: int
    parallel: int
    judge_model: str | None
    entries: tuple[Entry, ...]


def worst_case_calls(tasks: list[LoadedTask], seeds: tuple[int, ...]) -> int:
    """The most model calls these runs could make: every run taken to its step ceiling."""
    return len(seeds) * sum(loaded.task.max_steps for loaded in tasks)


def _seeds(value: Any, where: str) -> tuple[int, ...]:  # noqa: ANN401  parsed JSON
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(seed, int) and not isinstance(seed, bool) for seed in value)
    ):
        raise RequestError(f"{where}: seeds must be a non-empty list of integers")
    if any(seed < 0 for seed in value) or len(set(value)) != len(value):
        raise RequestError(f"{where}: seeds must be distinct and non-negative")
    return tuple(sorted(value))


def _number(body: dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RequestError(f"{key} must be a number")
    if not low <= value <= high:
        raise RequestError(f"{key} must be between {low:g} and {high:g}, got {value}")
    return float(value)


def parse_request(body: Any, tasks: list[LoadedTask]) -> Request:  # noqa: ANN401  parsed JSON
    """Validate a request against the suite it names, and fill in the defaults."""
    if not isinstance(body, dict):
        raise RequestError("the request must be a JSON object")
    unknown = sorted(set(body) - TOP_LEVEL)
    if unknown:
        raise RequestError(f"unknown keys: {', '.join(unknown)}")
    purpose = body.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise RequestError("purpose is required: say what the panel is for")
    suite = body.get("suite", "core-12")
    if not isinstance(suite, str):
        raise RequestError("suite must be a string")

    by_id = {loaded.id: loaded for loaded in tasks}
    default_seeds = _seeds(body.get("seeds", [0, 1]), "seeds")

    judge_model = body.get("judge_model")
    if judge_model is not None and (
        not isinstance(judge_model, str) or not MODEL_RE.match(judge_model)
    ):
        raise RequestError(f"judge_model is not a model identifier: {judge_model!r}")

    raw = body.get("models")
    if not isinstance(raw, list) or not raw:
        raise RequestError("models must be a non-empty list")
    entries: list[Entry] = []
    for index, item in enumerate(raw):
        where = f"models[{index}]"
        if not isinstance(item, dict):
            raise RequestError(f"{where} must be an object")
        extra = sorted(set(item) - ENTRY_KEYS)
        if extra:
            raise RequestError(f"{where}: unknown keys: {', '.join(extra)}")
        model = item.get("model")
        if isinstance(model, str) and model.startswith("stub:"):
            raise RequestError(f"{where}: a scripted policy is not a model")
        if not isinstance(model, str) or not MODEL_RE.match(model):
            raise RequestError(f"{where}: not a model identifier: {model!r}")
        wanted = item.get("tasks", [])
        if not isinstance(wanted, list) or not all(isinstance(task, str) for task in wanted):
            raise RequestError(f"{where}: tasks must be a list of task identifiers")
        missing = sorted(set(wanted) - set(by_id))
        if missing:
            raise RequestError(f"{where}: not in {suite}: {', '.join(missing)}")
        chosen = [by_id[task] for task in sorted(set(wanted))] or list(tasks)
        seeds = _seeds(item["seeds"], where) if "seeds" in item else default_seeds
        ceiling = worst_case_calls(chosen, seeds)
        max_calls = item.get("max_calls", ceiling)
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
            raise RequestError(f"{where}: max_calls must be a positive integer")
        note = item.get("note", "")
        if not isinstance(note, str):
            raise RequestError(f"{where}: note must be a string")
        entries.append(
            Entry(
                model=model,
                tasks=tuple(loaded.id for loaded in chosen) if wanted else (),
                seeds=seeds,
                max_calls=max_calls,
                note=note,
            )
        )

    slugs = Counter(entry.slug for entry in entries)
    clashes = sorted(slug for slug, count in slugs.items() if count > 1)
    if clashes:
        raise RequestError(f"two entries share a job name: {', '.join(clashes)}")

    return Request(
        purpose=purpose.strip(),
        suite=suite,
        temperature=_number(body, "temperature", 0.7, 0.0, 2.0),
        rpm_total=_number(body, "rpm_total", 34.0, 1.0, RPM_CEILING),
        max_parallel=int(_number(body, "max_parallel", 4, 1, 20)),
        parallel=int(_number(body, "parallel", 2, 1, 8)),
        judge_model=judge_model,
        entries=tuple(entries),
    )


def rate_per_job(request: Request, running: int) -> float:
    """Each job's share of the key's rate, rounded down so the shares never overshoot."""
    at_once = max(1, min(request.max_parallel, running))
    return math.floor(request.rpm_total / at_once * 10) / 10


def probe(model: str, timeout: int = 90) -> tuple[bool, str]:
    """One tool call through the smoke script, in its own process so its hard cap holds."""
    try:
        done = subprocess.run(
            [sys.executable, str(SMOKE), "--model", model, "--tool", "--timeout", str(timeout)],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"no answer within {timeout + 60}s"
    lines = (done.stdout + "\n" + done.stderr).splitlines()
    detail = next(
        (line for line in reversed(lines) if line.startswith(("OK ", "FAILED"))),
        lines[-1] if lines else "no output",
    )
    return done.returncode == 0, detail.strip()


def plan(request: Request, passing: list[Entry], judge_model: str | None) -> list[dict[str, str]]:
    """The matrix the workflow fans out over: one object per job."""
    rpm = rate_per_job(request, len(passing))
    return [
        {
            "model": entry.model,
            "slug": entry.slug,
            "args": shlex.join(
                entry.command(
                    suite=request.suite,
                    rpm=rpm,
                    temperature=request.temperature,
                    parallel=request.parallel,
                    judge_model=judge_model,
                )
            ),
        }
        for entry in passing
    ]


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")[:160]


def plan_summary(
    request: Request,
    outcomes: list[tuple[Entry, bool | None, str]],
    judge: tuple[str, bool | None, str] | None,
    jobs: list[dict[str, str]],
) -> str:
    """A markdown account of what was asked for and what will run."""
    lines = [
        "## Model panel plan",
        "",
        request.purpose,
        "",
        f"Suite {request.suite}, temperature {request.temperature:g}, "
        f"{request.rpm_total:g} requests a minute shared by up to {request.max_parallel} "
        f"jobs at once, so {rate_per_job(request, len(jobs)):g} each.",
        "",
        "| model | tasks | seeds | call ceiling | probe |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry, ok, detail in outcomes:
        status = "not probed" if ok is None else ("ok" if ok else "left out")
        tasks = ", ".join(entry.tasks) if entry.tasks else "all"
        seeds = ", ".join(str(seed) for seed in entry.seeds)
        lines.append(
            f"| `{entry.model}` | {tasks} | {seeds} | {entry.max_calls} "
            f"| {status}: {_cell(detail)} |"
        )
    if judge is not None:
        model, ok, detail = judge
        status = "not probed" if ok is None else ("ok" if ok else "left out, no judge")
        lines += ["", f"Judge `{model}`: {status}: {_cell(detail)}"]
    lines += ["", f"{len(jobs)} job(s) planned."]
    return "\n".join(lines) + "\n"


def records_summary(directory: Path) -> str:
    """Per model counts over the records a panel delivered."""
    runs = read_runs(directory) if directory.is_dir() else []
    lines = ["## Model panel records", ""]
    if not runs:
        return "\n".join([*lines, "No sealed run records arrived."]) + "\n"
    lines += ["| model | runs | solved | tasks | seeds |", "| --- | --- | --- | --- | --- |"]
    for model in sorted({run.config.model for run in runs}):
        mine = [run for run in runs if run.config.model == model]
        solved = sum(1 for run in mine if run.solved)
        tasks = len({run.task_id for run in mine})
        seeds = ", ".join(str(seed) for seed in sorted({run.config.seed for run in mine}))
        lines.append(f"| `{model}` | {len(mine)} | {solved} | {tasks} | {seeds} |")
    return "\n".join(lines) + "\n"


def _append(path: str | None, text: str) -> None:
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str]) -> int:
    """Validate a request, optionally probe it, and emit the job matrix."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", type=Path, help="The request JSON file.")
    parser.add_argument("--tasks-root", type=Path, default=ROOT / "tasks")
    parser.add_argument("--smoke", action="store_true", help="Probe every model first.")
    parser.add_argument("--github-output", help="Append the job matrix to this file.")
    parser.add_argument("--summary", help="Append the markdown summary to this file.")
    parser.add_argument(
        "--summarise", type=Path, help="Count the run records in a directory and stop."
    )
    args = parser.parse_args(argv)

    if args.summarise is not None:
        text = records_summary(args.summarise)
        print(text, end="")
        _append(args.summary, text)
        return 0
    if args.request is None:
        parser.error("a request file is required")

    try:
        body = json.loads(args.request.read_text(encoding="utf-8"))
        suite = body.get("suite", "core-12") if isinstance(body, dict) else "core-12"
        request = parse_request(body, discover(args.tasks_root, suite=str(suite)))
    except (OSError, json.JSONDecodeError, RequestError) as exc:
        print(f"cannot plan the panel: {exc}", file=sys.stderr)
        return 2

    outcomes: list[tuple[Entry, bool | None, str]] = []
    for entry in request.entries:
        if args.smoke:
            ok, detail = probe(entry.model)
            outcomes.append((entry, ok, detail))
            print(f"{'ok  ' if ok else 'FAIL'} {entry.model}: {detail}", flush=True)
        else:
            outcomes.append((entry, None, "planned without a probe"))

    judge: tuple[str, bool | None, str] | None = None
    judge_model = request.judge_model
    if judge_model is not None:
        if args.smoke:
            ok, detail = probe(judge_model)
            judge = (judge_model, ok, detail)
            print(f"{'ok  ' if ok else 'FAIL'} judge {judge_model}: {detail}", flush=True)
            if not ok:
                judge_model = None
        else:
            judge = (judge_model, None, "planned without a probe")

    passing = [entry for entry, ok, _ in outcomes if ok is not False]
    jobs = plan(request, passing, judge_model)
    summary = plan_summary(request, outcomes, judge, jobs)
    print(summary, end="")
    _append(args.summary, summary)
    _append(
        args.github_output,
        f"entries={json.dumps(jobs)}\nmax_parallel={request.max_parallel}\ncount={len(jobs)}\n",
    )
    if not jobs:
        print("no model passed its probe, so there is nothing to run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
