#!/usr/bin/env python3
"""Validate every export against Harbor's own models, in an environment that has Harbor.

The interop page says the exports are Harbor's formats. This is the check that makes that
a fact rather than a claim: it loads each exported `task.toml` with Harbor's `TaskConfig`
and each ATIF file with Harbor's `Trajectory`, the pydantic models Harbor itself reads them
with. It imports nothing from this repository, so it runs in an environment that has only
Harbor in it and cannot be satisfied by a reimplementation of Harbor's rules.

Parsing is not enough for the task files, because `TaskConfig` ignores a key it does not
know. An earlier export wrote `network = false`, which Harbor dropped without a word, and
the agent got a public network. So the check also reads back the settings that matter:
the verifier hardening, the network policy and the timeouts.

    uv run trajectory export harbor --out /tmp/harbor-tasks
    uv run trajectory export atif fixtures/recorded-runs --out /tmp/atif
    uv run trajectory export atif fixtures/model-runs --out /tmp/atif
    uv run --no-project --with harbor==0.24.0 python scripts/check_harbor_models.py \\
        --tasks /tmp/harbor-tasks --atif /tmp/atif
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The verifier environment every export must carry. It mirrors VERIFY_HARDENING_ENV in
# trajectory_runner.verifier, restated here because this script imports nothing from the
# repository, and a test there holds the two together.
HARDENING = {"PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1"}


def check_tasks(root: Path) -> tuple[int, list[str]]:
    """Load every exported task.toml with Harbor and read back what matters."""
    from harbor.models.task.config import TaskConfig

    problems: list[str] = []
    checked = 0
    for task_dir in sorted(p for p in root.iterdir() if (p / "task.toml").is_file()):
        checked += 1
        name = task_dir.name
        try:
            config = TaskConfig.model_validate_toml(
                (task_dir / "task.toml").read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001  any rejection is the finding
            problems.append(f"{name}: Harbor rejected task.toml: {str(exc)[:300]}")
            continue
        missing = {k: v for k, v in HARDENING.items() if config.verifier.env.get(k) != v}
        if missing:
            problems.append(f"{name}: verifier env lacks {missing}")
        mode = getattr(config.environment.network_mode, "value", config.environment.network_mode)
        if mode != "no-network":
            problems.append(f"{name}: Harbor reads network_mode as {mode!r}, not 'no-network'")
        if not (config.verifier.timeout_sec and config.agent.timeout_sec):
            problems.append(f"{name}: Harbor read no verifier or agent timeout")
    return checked, problems


def check_trajectories(root: Path) -> tuple[int, list[str]]:
    """Validate every ATIF file with Harbor's Trajectory model."""
    from harbor.models.trajectories import Trajectory

    problems: list[str] = []
    checked = 0
    for path in sorted(root.glob("*.json")):
        checked += 1
        try:
            Trajectory.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001  any rejection is the finding
            problems.append(f"{path.name}: Harbor rejected the trajectory: {str(exc)[:300]}")
    return checked, problems


def main(argv: list[str]) -> int:
    """Validate the exports and say how many of each Harbor accepted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True, help="Exported task directories.")
    parser.add_argument("--atif", type=Path, required=True, help="Exported ATIF files.")
    args = parser.parse_args(argv)

    try:
        from importlib.metadata import version

        harbor_version = version("harbor")
    except Exception:  # noqa: BLE001
        print("Harbor is not installed in this environment.", file=sys.stderr)
        return 1

    tasks, task_problems = check_tasks(args.tasks)
    runs, run_problems = check_trajectories(args.atif)
    problems = task_problems + run_problems
    if not tasks or not runs:
        problems.append(f"nothing to check: {tasks} task(s), {runs} trajectory file(s)")

    if problems:
        print(f"Harbor {harbor_version} rejected part of the export:", file=sys.stderr)
        for line in problems[:40]:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"Harbor {harbor_version} accepted {tasks} task(s) with the hardening and no network "
        f"read back, and {runs} ATIF trajectory file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
