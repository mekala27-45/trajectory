#!/usr/bin/env python3
"""Export tasks to Harbor and prove the exports grade correctly, for CI.

An exporter is only trustworthy if its output grades the way the native harness does. So
this exports the suite and, for a sample of tasks, runs each one three times through the
exported verifier: once after the reference oracle has solved it, which must score a
reward of 1; once on the untouched workspace, which must score 0; and once on the
untouched workspace with a module named after the test runner planted at its root, which
must never run and must leave the grade exactly as the untouched one. A task that cannot
tell its own solution from its starting state has a broken verifier, and one that runs a
file the agent planted has an unhardened one. Neither may ship.

It is a sample, not the suite, and it checks the export against its oracle, not against a
run of the native verifier.

The grade runs with the workspace and reward paths pointed at a temporary directory, so no
container and no Docker daemon are needed. Only the task's own language runtime is, which
is why the default sample is the pure-Python tasks.

    uv run python scripts/check_harbor_export.py
    uv run python scripts/check_harbor_export.py --task py-failing-suite-01 --task cli-implement-01
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from trajectory_runner.harbor import export_task
from trajectory_runner.loader import default_tasks_root, discover

DEFAULT_SAMPLE = ("cli-implement-01", "py-failing-suite-01")


# A module named after the test runner, dropped at the workspace root. It only records
# that it ran. If the exported verifier put the working directory first on the import path,
# `python -m pytest` would run this file instead of pytest, which is the gap an audit found
# in the export before it carried the native hardening.
PLANTED_MARKER = ".planted-module-ran"
PLANTED_PYTEST = (
    "# Planted by check_harbor_export. It records that it ran and does nothing else.\n"
    "from pathlib import Path\n"
    f"Path(__file__).with_name({PLANTED_MARKER!r}).write_text('ran')\n"
)


def grade(task_dir: Path, *, solve: bool, plant: bool = False) -> dict[str, object]:
    """Grade one exported task in a throwaway workspace, optionally after the oracle.

    With `plant`, a module named after the test runner is written to the workspace root
    first, so the grade shows whether the exported verifier carries the hardening.
    """
    workspace = Path(tempfile.mkdtemp(prefix="harbor-grade-"))
    logs = Path(tempfile.mkdtemp(prefix="harbor-logs-"))
    try:
        source = task_dir / "environment" / "workspace"
        for item in source.iterdir():
            dest = workspace / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        if plant:
            (workspace / "pytest.py").write_text(PLANTED_PYTEST, encoding="utf-8")
        env = {"HARBOR_WORKSPACE": str(workspace), "LOGS_VERIFIER_DIR": str(logs)}
        if solve:
            subprocess.run(
                ["bash", str(task_dir / "solution" / "solve.sh")],
                env={**_base_env(), **env},
                capture_output=True,
                check=False,
                timeout=600,
            )
        subprocess.run(
            ["bash", str(task_dir / "tests" / "test.sh")],
            env={**_base_env(), **env},
            capture_output=True,
            check=False,
            timeout=600,
        )
        reward_file = logs / "reward.json"
        if not reward_file.is_file():
            raise SystemExit(f"{task_dir.name}: verifier wrote no reward.json")
        payload: dict[str, object] = json.loads(reward_file.read_text(encoding="utf-8"))
        if plant:
            payload["planted_ran"] = (workspace / PLANTED_MARKER).exists()
        return payload
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(logs, ignore_errors=True)


def _base_env() -> dict[str, str]:
    """A minimal environment that still carries PATH, so bash finds python3, go, node."""
    import os

    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL") if k in os.environ}


def main(argv: list[str]) -> int:
    """Export the suite and grade the sampled tasks both ways."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", help="Task id to grade, repeatable.")
    parser.add_argument("--out", type=Path, default=None, help="Export directory.")
    args = parser.parse_args(argv)

    tasks = discover(default_tasks_root())
    by_id = {loaded.id: loaded for loaded in tasks}
    out = args.out or Path(tempfile.mkdtemp(prefix="harbor-export-"))
    out.mkdir(parents=True, exist_ok=True)

    for loaded in tasks:
        export_task(loaded, out, force=True)
    print(f"exported {len(tasks)} task(s) to {out}")

    sample = args.task or list(DEFAULT_SAMPLE)
    failures: list[str] = []
    for task_id in sample:
        if task_id not in by_id:
            failures.append(f"{task_id}: not found in the suite")
            continue
        task_dir = out / task_id
        oracle = grade(task_dir, solve=True)
        nop = grade(task_dir, solve=False)
        planted = grade(task_dir, solve=False, plant=True)
        counts = ("reward", "tests_passed", "tests_total")
        held = planted.get("planted_ran") is False and all(
            planted.get(k) == nop.get(k) for k in counts
        )
        ok = oracle.get("reward") == 1.0 and nop.get("reward") == 0.0 and held
        mark = "ok" if ok else "FAIL"
        print(
            f"  [{mark}] {task_id}: oracle reward={oracle.get('reward')} "
            f"({oracle.get('tests_passed')}/{oracle.get('tests_total')}), "
            f"nop reward={nop.get('reward')} "
            f"({nop.get('tests_passed')}/{nop.get('tests_total')}), "
            f"planted module ran={planted.get('planted_ran')}"
        )
        if not ok:
            failures.append(
                f"{task_id}: expected oracle 1.0, nop 0.0, and a planted module that never "
                f"runs; got {oracle.get('reward')}, {nop.get('reward')} and "
                f"ran={planted.get('planted_ran')}"
            )

    if failures:
        print("\nHarbor export grading failed:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"\n{len(sample)} task(s) graded three ways: every oracle scored 1.0, every nop 0.0, "
        "and no planted module ran."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
