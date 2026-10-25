#!/usr/bin/env python3
"""Promote a model panel's records onto the leaderboard, with where they came from.

A panel delivers its run records to a branch named for the workflow run. Promoting them is a
separate, reviewed commit, and this is the whole of that step: read the records, refuse
anything that does not belong, replace the committed real model records with them, write a
provenance file beside them, and regenerate the measured block of MODEL-RESULTS.md.

    git fetch origin model-panel-<run id>
    git archive origin/model-panel-<run id> fixtures/model-panel | tar -x -C /tmp/panel
    uv run python scripts/promote_panel.py /tmp/panel/fixtures/model-panel --run-id <run id>

The previous records are replaced rather than added to, because a leaderboard row mixes
nothing: every record a promotion leaves behind comes from one panel, one request and one
harness version, which the provenance file states and this script checks. Git keeps the
records it replaced.

Every record is rescored with the scoring code at the commit doing the promotion, which the
provenance file names beside the commits the runs came from. A metric fixed after a panel
started is then applied to its records the same way it was applied to everything already
published. Rescoring is offline: the trajectories, the judge's output and the failure modes
are carried over unchanged.

Refuses to run when a record is a scripted policy, when the records disagree about the
harness version, or when two records share an identifier.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from trajectory_core.models import Run, Task
from trajectory_core.scoring import rescore
from trajectory_runner.loader import discover
from trajectory_runner.providers import ended_by_provider

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "model-runs"
# Beside the records, never among them: every tool that reads the records globs *.json.
PROVENANCE = ROOT / "fixtures" / "model-runs.provenance.json"
# Measured on the records a promotion replaces, so it goes with them.
AGREEMENT = ROOT / "fixtures" / "judge-agreement.json"
REQUEST = ROOT / "model-requests" / "panel.json"


class PromotionError(ValueError):
    """The records cannot be promoted as they are."""


def load(directory: Path) -> list[Run]:
    """Every record in the directory, validated, in identifier order."""
    paths = sorted(directory.glob("*.json"))
    runs = [Run.model_validate_json(path.read_text(encoding="utf-8")) for path in paths]
    if not runs:
        raise PromotionError(f"no run records under {directory}")
    stubs = sorted({run.config.model for run in runs if run.config.model.startswith("stub:")})
    if stubs:
        raise PromotionError(f"a scripted policy is not a model: {', '.join(stubs)}")
    versions = sorted({run.harness_version for run in runs})
    if len(versions) > 1:
        raise PromotionError(f"the records come from several harness versions: {versions}")
    ids = [run.id for run in runs]
    if len(set(ids)) != len(ids):
        raise PromotionError("two records share an identifier")
    return sorted(runs, key=lambda run: run.id)


def provider_ended(run: Run) -> bool:
    """A run the model provider ended: its calls kept failing until the retries ran out.

    That is a measurement of the provider at the time, not of the model, and it is the
    only kind of run a rerun is allowed to replace.
    """
    return ended_by_provider(run)


def _key(run: Run) -> tuple[str, str, int]:
    return run.config.model, run.task_id, run.config.seed


def merge_reruns(runs: list[Run], reruns: list[Run]) -> tuple[list[Run], list[dict[str, object]]]:
    """Put each rerun in the place of the provider ended run it repeats.

    The rule is fixed before the reruns are read: a rerun replaces the run with the same
    model, task and seed, that run has to be one the provider ended, and the rerun stands
    whatever it scored, so nothing is chosen by its outcome. A rerun of a run the agent
    itself finished is refused: replacing an agent's failure would be cherry picking.
    """
    by_key = {_key(run): run for run in runs}
    seen: set[tuple[str, str, int]] = set()
    replaced: list[dict[str, object]] = []
    for rerun in sorted(reruns, key=_key):
        key = _key(rerun)
        if key in seen:
            raise PromotionError(f"two reruns of {key}")
        seen.add(key)
        original = by_key.get(key)
        if original is None:
            raise PromotionError(f"a rerun of {key} has no run to replace")
        if not provider_ended(original):
            raise PromotionError(
                f"{key} was not ended by the provider ({original.status.value}), so it stands"
            )
        by_key[key] = rerun
        replaced.append(
            {
                "model": key[0],
                "task": key[1],
                "seed": key[2],
                "original_run": original.id,
                "original_steps": len(original.steps),
                "original_error": (original.error or "")[:300],
                "rerun": rerun.id,
                "rerun_provider_ended": provider_ended(rerun),
            }
        )
    return sorted(by_key.values(), key=lambda run: run.id), replaced


def rescored(runs: list[Run], tasks: dict[str, Task]) -> list[Run]:
    """Every record scored by the current scoring code, its trajectory untouched."""
    missing = sorted({run.task_id for run in runs} - set(tasks))
    if missing:
        raise PromotionError(f"no task definition for {', '.join(missing)}")
    return [rescore(run, tasks[run.task_id]) for run in runs]


def _head() -> str:
    """The commit doing the promotion, which is the scoring code the records now carry."""
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return done.stdout.strip() or "not recorded"


def provenance(
    runs: list[Run],
    *,
    run_id: str,
    request: dict[str, object],
    scored_at_commit: str = "not recorded",
) -> dict[str, object]:
    """Where these records came from, stated once beside them."""
    models = sorted({run.config.model for run in runs})
    return {
        "panel_run_id": run_id,
        "workflow": ".github/workflows/model-panel.yml",
        "request": request,
        "judge_model": request.get("judge_model"),
        "harness_version": runs[0].harness_version,
        "source_commits": sorted(
            {run.runner_fingerprint.source_commit or "not recorded" for run in runs}
        ),
        "scored_at_commit": scored_at_commit,
        "models": {
            model: {
                "runs": sum(1 for run in runs if run.config.model == model),
                "solved": sum(1 for run in runs if run.config.model == model and run.solved),
                "seeds": sorted({run.config.seed for run in runs if run.config.model == model}),
                "tasks": len({run.task_id for run in runs if run.config.model == model}),
            }
            for model in models
        },
        "promoted_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def promote(
    runs: list[Run],
    meta: dict[str, object],
    fixtures: Path,
    provenance_path: Path,
    agreement_path: Path | None = None,
) -> bool:
    """Replace the committed records with these, and write the provenance beside them.

    A judge agreement measured on the records being replaced is removed with them, since
    it says nothing about the new ones. Returns whether one was removed.
    """
    fixtures.mkdir(parents=True, exist_ok=True)
    for old in fixtures.glob("*.json"):
        old.unlink()
    for run in runs:
        (fixtures / f"{run.id}.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    provenance_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if agreement_path is not None and agreement_path.exists():
        agreement_path.unlink()
        return True
    return False


def main(argv: list[str]) -> int:
    """Promote, then regenerate the measured block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path, help="Directory holding the panel's records.")
    parser.add_argument("--run-id", required=True, help="The model-panel workflow run.")
    parser.add_argument("--request", type=Path, default=REQUEST, help="The request it ran.")
    parser.add_argument("--tasks-root", type=Path, default=ROOT / "tasks")
    parser.add_argument(
        "--rerun", type=Path, help="Records of a panel that reran provider ended runs."
    )
    parser.add_argument("--rerun-id", help="The model-panel workflow run of the rerun.")
    parser.add_argument("--rerun-request", type=Path, help="The request the rerun ran.")
    args = parser.parse_args(argv)
    if bool(args.rerun) != bool(args.rerun_id):
        parser.error("--rerun and --rerun-id go together")

    tasks = {loaded.id: loaded.task for loaded in discover(args.tasks_root)}
    replaced: list[dict[str, object]] = []
    try:
        runs = load(args.records)
        if args.rerun:
            reruns = load(args.rerun)
            if {run.harness_version for run in reruns} != {runs[0].harness_version}:
                raise PromotionError("the rerun ran a different harness version")
            runs, replaced = merge_reruns(runs, reruns)
        runs = rescored(runs, tasks)
    except PromotionError as exc:
        print(f"cannot promote: {exc}", file=sys.stderr)
        return 2
    request = json.loads(args.request.read_text(encoding="utf-8"))
    meta = provenance(runs, run_id=args.run_id, request=request, scored_at_commit=_head())
    if args.rerun:
        rerun_request = (
            json.loads(args.rerun_request.read_text(encoding="utf-8"))
            if args.rerun_request
            else None
        )
        meta["reruns"] = {
            "panel_run_id": args.rerun_id,
            "request": rerun_request,
            "rule": (
                "a rerun replaces the run with the same model, task and seed, only when the "
                "provider ended that run, and stands whatever it scored"
            ),
            "replaced": replaced,
        }
    if promote(runs, meta, FIXTURES, PROVENANCE, AGREEMENT):
        print(f"removed {AGREEMENT.name}: it was measured on the records this replaces")
    print(f"promoted {len(runs)} records from panel run {args.run_id} into {FIXTURES.name}")
    for model, counts in meta["models"].items():  # type: ignore[attr-defined]
        print(f"  {model}: {counts['solved']} of {counts['runs']} solved")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_model_numbers.py"), "--write"], check=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
