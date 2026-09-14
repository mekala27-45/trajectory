#!/usr/bin/env python3
"""Build the static data bundle the web demo reads.

The web app has two data sources and one shape. With `NEXT_PUBLIC_API_URL` set it talks to
the results API; without it, it reads these files. That is what makes the public demo work
for a stranger with no key, no account and nothing to configure: the recorded runs ship
with the repository, and the leaderboard is real data rather than an empty state waiting
for someone to sign up.

Both paths serve the identical models from `trajectory_core.models`, so the client has no
idea which one it is talking to.

    python scripts/build_web_bundle.py                          # newest run directory
    python scripts/build_web_bundle.py runs/matrix --out web/public/data
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trajectory_core.aggregate import (
    failure_mode_counts,
    failure_modes_by_difficulty,
    failure_modes_by_model,
    leaderboard,
    per_task_breakdown,
)
from trajectory_core.failure_modes import taxonomy_specs
from trajectory_core.models import (
    HARNESS_VERSION,
    DatasetIndex,
    FailureModeBreakdown,
    LeaderboardResponse,
    Run,
    RunSummary,
    SandboxBackend,
    TaskResults,
    TaskSummary,
)
from trajectory_runner.loader import discover
from trajectory_runner.store import read_runs

LOCAL_NOTE = (
    "Some rows come from the unisolated local sandbox and are labelled local. They form "
    "their own rows and are never averaged together with container runs, because the local "
    "backend cannot guarantee the agent did not see the hidden tests."
)
STUB_NOTE = (
    "Models whose identifier starts with stub: are scripted offline policies, not language "
    "models. They validate the harness and give this demo real trajectories to replay."
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", type=Path, help="Run directory to read.")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--from-fixtures",
        type=Path,
        nargs="?",
        const=Path("fixtures/recorded-runs"),
        default=None,
        help=(
            "Read the committed fixture runs instead of a run directory. This is the path "
            "CI and the static deploy take, so the demo needs nothing but the repository."
        ),
    )
    parser.add_argument("--out", type=Path, default=Path("web/public/data"))
    parser.add_argument("--tasks-root", type=Path, default=Path("tasks"))
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="Also copy the sealed run records here, as the committed fixture set.",
    )
    return parser.parse_args(argv)


def newest_run_dir(root: Path) -> Path:
    """Find the most recent run directory."""
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "runs").is_dir()]
    if not candidates:
        raise SystemExit(f"no run directories under {root}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def write_json(path: Path, payload: Any) -> None:  # noqa: ANN401  pydantic or plain data
    """Write a model or a plain object as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump_json"):
        path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def build(runs: list[Run], tasks_root: Path, out: Path) -> DatasetIndex:
    """Write every file the web app reads."""
    loaded = {t.id: t for t in discover(tasks_root)}
    summaries = [RunSummary.from_run(run) for run in runs]
    backends = sorted({s.backend for s in summaries}, key=lambda b: b.value)
    note_parts = []
    if SandboxBackend.LOCAL in backends:
        note_parts.append(LOCAL_NOTE)
    if any(s.model.startswith("stub:") for s in summaries):
        note_parts.append(STUB_NOTE)
    note = " ".join(note_parts)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    rows = leaderboard(runs)
    write_json(
        out / "leaderboard.json",
        LeaderboardResponse(
            generated_at=datetime.now(UTC),
            harness_version=HARNESS_VERSION,
            rows=rows,
            last_run_at=max((r.started_at for r in runs), default=None),
            note=note,
        ),
    )

    task_ids = sorted({run.task_id for run in runs})
    task_summaries = [
        TaskSummary.from_task(loaded[task_id].task) for task_id in task_ids if task_id in loaded
    ]
    write_json(out / "tasks.json", [t.model_dump(mode="json") for t in task_summaries])

    breakdown = per_task_breakdown(runs)
    for task_id in task_ids:
        if task_id not in loaded:
            continue
        write_json(
            out / "tasks" / f"{task_id}.json",
            TaskResults(
                task=TaskSummary.from_task(loaded[task_id].task),
                solve_rate_by_model=breakdown.get(task_id, {}),
                runs=[s for s in summaries if s.task_id == task_id],
            ),
        )

    write_json(out / "runs.json", [s.model_dump(mode="json") for s in summaries])
    for run in runs:
        write_json(out / "runs" / f"{run.id}.json", run)

    unsolved = sum(1 for run in runs if not run.solved)
    difficulty_of = {task_id: loaded[task_id].task.difficulty for task_id in loaded}
    write_json(
        out / "failures.json",
        FailureModeBreakdown(
            taxonomy=taxonomy_specs(),
            overall=failure_mode_counts(runs),
            on_solved_runs=failure_mode_counts(runs, among="solved"),
            by_model=failure_modes_by_model(runs, among="all"),
            by_difficulty={
                str(tier): counts
                for tier, counts in failure_modes_by_difficulty(
                    runs, difficulty_of, among="all"
                ).items()
            },
            unsolved_runs=unsolved,
            solved_runs=len(runs) - unsolved,
        ),
    )

    index = DatasetIndex(
        generated_at=datetime.now(UTC),
        harness_version=HARNESS_VERSION,
        suites=sorted({run.suite for run in runs}),
        models=sorted({run.config.model for run in runs}),
        backends=backends,
        run_count=len(runs),
        task_count=len(task_ids),
        solved_count=sum(1 for run in runs if run.solved),
        total_cost_usd=round(sum(run.total_cost_usd for run in runs), 6),
        total_wall_clock_s=round(sum(run.wall_clock_s for run in runs), 1),
        judge_model=next(
            (
                run.config.model
                for run in runs
                if any(hit.detector.value == "judge" for hit in run.failure_modes)
            ),
            None,
        ),
        note=note,
    )
    write_json(out / "index.json", index)
    return index


def main(argv: list[str]) -> int:
    """Build the bundle."""
    args = parse_args(argv)
    if args.from_fixtures is not None:
        source = args.from_fixtures
        runs = sorted(
            (
                Run.model_validate_json(path.read_text(encoding="utf-8"))
                for path in source.glob("*.json")
            ),
            key=lambda run: run.id,
        )
    else:
        source = args.run_dir or newest_run_dir(args.runs_root)
        runs = read_runs(source)
    if not runs:
        raise SystemExit(f"no run records under {source}")

    index = build(runs, args.tasks_root, args.out)

    if args.fixtures is not None and args.from_fixtures is None:
        if args.fixtures.exists():
            shutil.rmtree(args.fixtures)
        args.fixtures.mkdir(parents=True)
        for run in runs:
            (args.fixtures / f"{run.id}.json").write_text(
                run.model_dump_json(indent=2), encoding="utf-8"
            )
        print(f"wrote {len(runs)} fixture run(s) to {args.fixtures}")

    print(
        f"built {args.out} from {source}: {index.run_count} runs, {index.task_count} tasks, "
        f"{len(index.models)} model(s), backends {[b.value for b in index.backends]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
