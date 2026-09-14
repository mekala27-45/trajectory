#!/usr/bin/env python3
"""Load the committed fixture runs into a database.

This is the step that makes the hosted demo work for a stranger. Without it a visitor
lands on an empty leaderboard and has to run an evaluation themselves before the site
shows anything, which nobody does. With it the live URL shows real trajectories they can
replay immediately, and the only thing anyone has to configure is nothing.

    DATABASE_URL=... TRAJECTORY_API_KEY=... python scripts/seed_db.py

Run it against production once, after the first deploy. It is idempotent on run identifier,
so running it again is safe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trajectory_api.db import create_all, session_scope
from trajectory_api.queries import run_exists, store_run, upsert_task
from trajectory_core.models import Run, TaskSummary
from trajectory_runner.loader import discover


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures/recorded-runs"))
    parser.add_argument("--tasks-root", type=Path, default=Path("tasks"))
    parser.add_argument(
        "--create-tables",
        action="store_true",
        help="Create the schema directly instead of running migrations. Local use only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Seed the database."""
    args = parse_args(argv)
    if args.create_tables:
        create_all()

    runs = sorted(
        (
            Run.model_validate_json(path.read_text(encoding="utf-8"))
            for path in args.fixtures.glob("*.json")
        ),
        key=lambda run: run.id,
    )
    if not runs:
        print(f"no fixture runs under {args.fixtures}", file=sys.stderr)
        return 1

    tasks = [TaskSummary.from_task(loaded.task) for loaded in discover(args.tasks_root)]

    inserted = 0
    skipped = 0
    with session_scope() as session:
        for task in tasks:
            upsert_task(session, task)
        for run in runs:
            if run_exists(session, run.id):
                skipped += 1
                continue
            store_run(session, run)
            inserted += 1

    print(
        f"seeded {len(tasks)} task(s); stored {inserted} run(s), skipped {skipped} already "
        f"present, from {args.fixtures}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
