#!/usr/bin/env python3
"""Read again, with the same judge, the runs a panel's judge could not read.

A panel judges every unsolved run as it finishes. When the judge's reply cannot be read,
the run is sealed without a verdict and its error says why. A later fix to how replies are
read does not reach those runs by itself, because a sealed run is never re-run. This asks
the same judge model, at the same temperature, for a verdict on exactly those runs and on
no others. A verdict already recorded is never asked for twice, so nothing the first pass
decided changes.

Two steps, like a panel.

    # in CI, with the key: writes every record, read again or not, and a summary
    uv run python scripts/rejudge.py run fixtures/model-runs --out rejudged
    # rejudged/records/*.json and rejudged/rejudge-summary.json

    # locally, after review: checks that only the judge's output changed, then replaces
    uv run python scripts/rejudge.py promote /tmp/rejudged --run-id <workflow run id>

The request lives in model-requests/rejudge.json and has to name the judge the panel ran,
which the provenance file records: a second model would mix two judges in one record set.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from plan_panel import annotate

from trajectory_core.judge import JudgeClient, JudgeError, LiteLLMJudge, judge_run, verdict_to_hits
from trajectory_core.models import Detector, Run, Task
from trajectory_core.scoring import rank_failure_modes
from trajectory_runner.agent import RateLimiter
from trajectory_runner.loader import discover
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "model-runs"
PROVENANCE = ROOT / "fixtures" / "model-runs.provenance.json"
REQUEST = ROOT / "model-requests" / "rejudge.json"
REQUEST_KEYS = {"purpose", "judge_model", "parallel", "rpm"}
SUMMARY = "rejudge-summary.json"
RECORDS = "records"  # beside the summary, never with it: every reader globs *.json
JUDGE_FAILED = "judge failed: "
RPM_CEILING = 40.0


class RejudgeError(ValueError):
    """The request or the records cannot be used as they are."""


def needs_judge(run: Run) -> bool:
    """An unsolved run the judge was asked about and could not answer for."""
    return (
        not run.solved
        and bool(run.steps)
        and run.score is not None
        and run.score.context_drift is None
        and JUDGE_FAILED in (run.error or "")
    )


def without_judge_failure(error: str | None) -> str | None:
    """The run's own error, with the judge's failure note taken off the end.

    The note is always last, because judging happens after the run is sealed, and whatever
    the run itself reported before it stays.
    """
    if not error or JUDGE_FAILED not in error:
        return error
    kept = error.split(JUDGE_FAILED, 1)[0].rstrip().rstrip(";").rstrip()
    return kept or None


def read_again(run: Run, task: Task, client: JudgeClient, *, temperature: float = 0.0) -> Run:
    """The run with the judge's verdict in it, the rule hits and everything else as they were."""
    verdict = judge_run(client, run, task, temperature=temperature)
    assert run.score is not None
    rules = [hit for hit in run.failure_modes if hit.detector is Detector.RULE]
    return run.model_copy(
        deep=True,
        update={
            "failure_modes": rank_failure_modes([*rules, *verdict_to_hits(verdict)]),
            "score": run.score.model_copy(update={"context_drift": verdict.context_drift}),
            "error": without_judge_failure(run.error),
        },
    )


def read_request(path: Path, judge_model: str | None) -> tuple[str, int, float]:
    """The judge, the workers and the rate, checked against the panel's own judge."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise RejudgeError("the request is a JSON object")
    unknown = sorted(set(body) - REQUEST_KEYS)
    if unknown:
        raise RejudgeError(f"unknown key(s): {', '.join(unknown)}")
    if judge_model is None:
        raise RejudgeError("no promoted panel names its judge; promote the panel first")
    if body.get("judge_model") != judge_model:
        raise RejudgeError(
            f"the request names {body.get('judge_model')!r} but the panel was judged by "
            f"{judge_model!r}, and one record set reads with one judge"
        )
    parallel = int(body.get("parallel", 3))
    rpm = float(body.get("rpm", 15.0))
    if not 1 <= parallel <= 8:
        raise RejudgeError("parallel is between 1 and 8")
    if not 0.0 < rpm <= RPM_CEILING:
        raise RejudgeError(f"rpm is above 0 and at most {RPM_CEILING:g}, the free tier's limit")
    return judge_model, parallel, rpm


def run_command(args: argparse.Namespace) -> int:
    """Read again every run the judge could not read, and write the whole record set."""
    provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
    try:
        judge_model, parallel, rpm = read_request(args.request, provenance.get("judge_model"))
    except (RejudgeError, ValueError, OSError) as exc:
        print(f"cannot run the request: {exc}", file=sys.stderr)
        annotate("error", "rejudge request refused", str(exc))
        return 2

    tasks = {loaded.id: loaded.task for loaded in discover(args.tasks_root)}
    runs = read_runs(args.records)
    todo = [run for run in runs if needs_judge(run) and run.task_id in tasks]
    print(f"{len(todo)} of {len(runs)} run(s) need the judge again")
    client = LiteLLMJudge(judge_model)
    limiter = RateLimiter(rpm)
    failed: dict[str, str] = {}
    lock = threading.Lock()

    def one(run: Run) -> Run:
        limiter.acquire()
        try:
            return read_again(run, tasks[run.task_id], client)
        except JudgeError as exc:
            with lock:
                failed[run.id] = str(exc)[:600]
            annotate("warning", f"still no verdict for {run.id}", str(exc)[:600])
            return run

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        updated = dict(zip([run.id for run in todo], pool.map(one, todo), strict=True))

    records = args.out / RECORDS
    records.mkdir(parents=True, exist_ok=True)
    for run in runs:
        record = updated.get(run.id, run)
        (records / f"{run.id}.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    summary = {
        "judge_model": judge_model,
        "read_again": sorted(run_id for run_id in updated if run_id not in failed),
        "still_unjudged": sorted(failed),
        "reasons": failed,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (args.out / SUMMARY).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    read = len(summary["read_again"])
    annotate("notice", "rejudge", f"{read} of {len(todo)} run(s) now carry a verdict")
    print(f"{read} of {len(todo)} run(s) now carry a verdict; wrote {args.out}")
    return 0


def changed_beyond_the_judge(before: Run, after: Run) -> list[str]:
    """Every difference other than the three a judge's verdict is allowed to make."""
    left = before.model_dump(mode="json")
    right = after.model_dump(mode="json")
    problems = [
        key
        for key in left
        if key not in {"failure_modes", "score", "error"} and left[key] != right[key]
    ]
    left_score = dict(left["score"] or {})
    right_score = dict(right["score"] or {})
    left_score.pop("context_drift", None)
    right_score.pop("context_drift", None)
    if left_score != right_score:
        problems.append("score")
    rules = [hit for hit in left["failure_modes"] if hit["detector"] == "rule"]
    if [hit for hit in right["failure_modes"] if hit["detector"] == "rule"] != rules:
        problems.append("rule failure modes")
    if after.error != without_judge_failure(before.error):
        problems.append("error")
    return problems


def promote_command(args: argparse.Namespace) -> int:
    """Replace the records the judge read again, after checking nothing else moved."""
    summary = json.loads((args.rejudged / SUMMARY).read_text(encoding="utf-8"))
    current = {run.id: run for run in read_runs(args.fixtures)}
    incoming = {run.id: run for run in read_runs(args.rejudged / RECORDS)}
    if set(incoming) != set(current):
        print("the re-judged set is not the committed set of runs", file=sys.stderr)
        return 2
    replaced = []
    for run_id in summary["read_again"]:
        problems = changed_beyond_the_judge(current[run_id], incoming[run_id])
        if problems:
            print(f"{run_id} changed beyond the judge's output: {problems}", file=sys.stderr)
            return 2
        target = args.fixtures / f"{run_id}.json"
        target.write_text(incoming[run_id].model_dump_json(indent=2), encoding="utf-8")
        replaced.append(run_id)

    meta = json.loads(args.provenance.read_text(encoding="utf-8"))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    meta["rejudged"] = {
        "workflow_run_id": args.run_id,
        "workflow": ".github/workflows/rejudge.yml",
        "judge_model": summary["judge_model"],
        "read_again": replaced,
        "still_unjudged": summary["still_unjudged"],
        "at_commit": head or "not recorded",
        "promoted_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    args.provenance.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"replaced {len(replaced)} record(s); {len(summary['still_unjudged'])} still unjudged")
    if args.render:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_model_numbers.py"), "--write"],
            check=True,
        )
    return 0


def main(argv: list[str]) -> int:
    """Dispatch to the CI step or the local promotion."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Read the runs again, in CI.")
    run.add_argument("records", type=Path)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--request", type=Path, default=REQUEST)
    run.add_argument("--provenance", type=Path, default=PROVENANCE)
    run.add_argument("--tasks-root", type=Path, default=ROOT / "tasks")

    promote = commands.add_parser("promote", help="Replace the records, after review.")
    promote.add_argument("rejudged", type=Path)
    promote.add_argument("--run-id", required=True)
    promote.add_argument("--fixtures", type=Path, default=FIXTURES)
    promote.add_argument("--provenance", type=Path, default=PROVENANCE)
    promote.add_argument(
        "--no-render",
        dest="render",
        action="store_false",
        help="Leave MODEL-RESULTS.md alone.",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    return promote_command(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
