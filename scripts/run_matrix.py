#!/usr/bin/env python3
"""Run the published measurement matrix.

The matrix is five offline reference policies against all twelve tasks at three seeds,
which is 180 runs. Every number in RESULTS.md comes from here, and the script is committed
so anyone can reproduce it end to end with one command.

These are scripted policies, not models. They exist to validate the harness itself and to
give the public demo real trajectories to replay, and every run carries a `stub:` model
identifier so nothing downstream can read them as model results. Swap the policy list for
real model identifiers, add a key, and the same script produces the model matrix.

    python scripts/run_matrix.py                      # the published matrix
    python scripts/run_matrix.py --model anthropic/claude-sonnet-4-5 --seeds 3
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import structlog

from trajectory_core.models import SandboxBackend
from trajectory_runner.agent import Budget
from trajectory_runner.execute import ExecutionOptions, plan, prebuild_images, run_suite
from trajectory_runner.loader import discover
from trajectory_runner.sandbox import ALLOW_LOCAL_ENV, docker_available
from trajectory_runner.store import new_run_directory, write_bundle

POLICIES = ["methodical", "hasty", "thrasher", "sloppy", "reckless"]
DEFAULT_SEEDS = 3


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="core-12")
    parser.add_argument(
        "--model",
        action="append",
        help="Model identifier, repeatable. Defaults to the five offline policies.",
    )
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--label", default="matrix")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--tasks-root", type=Path, default=Path("tasks"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the matrix and write one bundle covering all of it."""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))
    args = parse_args(argv)

    if docker_available():
        backend = SandboxBackend.DOCKER
    elif os.environ.get(ALLOW_LOCAL_ENV) == "1":
        backend = SandboxBackend.LOCAL
        print(
            "no Docker daemon reachable, running on the local backend. Every run will be "
            "stamped sandbox_backend=local and will form its own leaderboard rows.",
            file=sys.stderr,
        )
    else:
        print(
            f"no Docker daemon reachable. Start one, or set {ALLOW_LOCAL_ENV}=1 to run "
            "without isolation.",
            file=sys.stderr,
        )
        return 1

    tasks = discover(args.tasks_root, suite=args.suite)
    models = args.model or [f"stub:{name}" for name in POLICIES]
    seeds = list(range(args.seeds))

    run_dir = new_run_directory(args.out, args.suite, label=args.label)
    budget = Budget(args.budget_usd)
    options = ExecutionOptions(
        run_dir=run_dir,
        allow_local_override=backend is SandboxBackend.LOCAL,
        budget=budget,
        ci=bool(os.environ.get("CI")),
    )

    total = len(tasks) * len(models) * len(seeds)
    print(
        f"matrix: {len(models)} model(s) x {len(tasks)} task(s) x {len(seeds)} seed(s) "
        f"= {total} runs on the {backend.value} backend, parallel {args.parallel}"
    )
    print(f"writing to {run_dir}")

    started = time.monotonic()
    all_runs = []
    completed = 0

    for model in models:
        jobs = plan(tasks, model=model, seeds=seeds, backend=backend)
        image_tags = prebuild_images(jobs) if backend is SandboxBackend.DOCKER else {}

        def progress(job: object, run: object, state: str) -> None:
            nonlocal completed
            if state != "done":
                return
            completed += 1
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed else 0
            remaining = (total - completed) / rate if rate else 0
            print(
                f"  [{completed:3d}/{total}] {getattr(job, 'label', '?'):32s} "
                f"{'solved' if getattr(run, 'solved', False) else 'failed':6s} "
                f"eta {remaining / 60:.1f}m",
                flush=True,
            )

        result = run_suite(
            jobs, options, parallel=args.parallel, image_tags=image_tags, on_progress=progress
        )
        all_runs.extend(result.runs)
        solved = sum(1 for r in result.runs if r.solved)
        print(
            f"{model}: {solved}/{len(result.runs)} solved in {result.wall_clock_s:.0f}s "
            f"for {result.cost_usd:.4f} USD",
            flush=True,
        )

    elapsed = time.monotonic() - started
    bundle = write_bundle(
        run_dir,
        all_runs,
        suite=args.suite,
        fingerprint=all_runs[0].runner_fingerprint,
        notes=" ".join(["python", "scripts/run_matrix.py", *argv]),
    )
    solved = sum(1 for r in all_runs if r.solved)
    print(
        f"\n{len(all_runs)} runs, {solved} solved, "
        f"{sum(r.total_cost_usd for r in all_runs):.4f} USD, {elapsed / 60:.1f} minutes"
    )
    print(f"bundle: {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
