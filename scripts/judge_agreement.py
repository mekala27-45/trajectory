#!/usr/bin/env python3
"""How much a second judge, from another model family, agrees with the first.

Three failure modes and the context drift metric come from a language model reading the
trajectory. A number a model produced is only as trustworthy as the model's agreement with
another reader, so this measures it: a second judge from a different family reads a fixed
sample of the runs the first judge read, and the two are compared mode by mode, with raw
agreement, Cohen's kappa, and the full two by two table.

The first judge's verdicts are read back from the records rather than asked for again. A
judged mode the record carries as a judge hit was present; one it does not carry was absent,
because a verdict that judged a mode absent writes no hit. The record's context drift is the
first judge's drift. Runs whose inline judging failed are left out, since they have no first
verdict to compare against.

    uv run python scripts/judge_agreement.py fixtures/model-runs --out judge-agreement.json

The settings come from model-requests/judge.json, so the measurement has a reviewable
record of what was asked for, and the first judge comes from the provenance file a panel
promotion writes. A candidate from the first judge's family, or from the family of any
agent under test, is refused before any call is made. The request lists candidates in
order, and the first that returns a valid verdict judges the sample. Writes a JSON report;
MODEL-RESULTS.md renders it once it is committed as fixtures/judge-agreement.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from plan_panel import annotate

from trajectory_core.judge import (
    JudgeClient,
    JudgeError,
    JudgeVerdict,
    LiteLLMJudge,
    ModeJudgement,
    judge_run,
    measure_agreement,
)
from trajectory_core.models import Detector, FailureModeId, Run, Task
from trajectory_runner.agent import RateLimiter
from trajectory_runner.loader import discover
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
REQUEST = ROOT / "model-requests" / "judge.json"
PROVENANCE = ROOT / "fixtures" / "model-runs.provenance.json"
REQUEST_KEYS = {"purpose", "second_judges", "sample", "parallel", "rpm", "temperature"}
MAX_CANDIDATES = 4
MODEL_RE = re.compile(r"^[a-z0-9_]+(/[A-Za-z0-9._:-]+)+$")
RPM_CEILING = 40.0
ANNOTATED_FAILURES = 5
JUDGED = (
    FailureModeId.IGNORED_TEST_OUTPUT,
    FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
    FailureModeId.ENVIRONMENT_MISMATCH,
)


def judged_runs(runs: list[Run]) -> list[Run]:
    """The runs the first judge read: unsolved, with steps, and no judging failure."""
    return [
        run
        for run in runs
        if not run.solved
        and run.steps
        and run.score is not None
        and run.score.context_drift is not None
        and "judge failed" not in (run.error or "")
    ]


def first_verdict(run: Run) -> JudgeVerdict:
    """The first judge's verdict, read back from what it wrote into the record."""
    hits = {hit.id: hit for hit in run.failure_modes if hit.detector is Detector.JUDGE}

    def judgement(mode: FailureModeId) -> ModeJudgement:
        hit = hits.get(mode)
        if hit is None:
            return ModeJudgement(present=False, confidence=0.0)
        return ModeJudgement(
            present=True,
            confidence=hit.confidence,
            evidence=hit.evidence[:1200],
            step_indices=hit.step_indices,
        )

    assert run.score is not None and run.score.context_drift is not None
    return JudgeVerdict(
        context_drift=run.score.context_drift,
        ignored_test_output=judgement(FailureModeId.IGNORED_TEST_OUTPUT),
        long_horizon_context_loss=judgement(FailureModeId.LONG_HORIZON_CONTEXT_LOSS),
        environment_mismatch=judgement(FailureModeId.ENVIRONMENT_MISMATCH),
    )


def sample_runs(runs: list[Run], fraction: float) -> list[Run]:
    """An evenly spread, fixed sample in identifier order."""
    ordered = sorted(runs, key=lambda run: run.id)
    size = max(1, round(len(ordered) * fraction)) if ordered else 0
    if size >= len(ordered):
        return ordered
    step = len(ordered) / size
    return [ordered[int(index * step)] for index in range(size)]


def second_opinions(
    runs: list[Run],
    tasks: dict[str, Task],
    client: JudgeClient,
    *,
    parallel: int = 4,
    temperature: float = 0.0,
    rpm: float | None = None,
) -> list[tuple[Run, JudgeVerdict]]:
    """The second judge's verdict on each run, leaving out the runs it could not judge.

    Calls are spaced across every worker by one limiter, because a free tier counts
    requests per key and a thread pool is a burst by construction.
    """
    limiter = RateLimiter(rpm)
    failed: list[str] = []
    lock = threading.Lock()

    def second(run: Run) -> JudgeVerdict | None:
        limiter.acquire()
        try:
            return judge_run(client, run, tasks[run.task_id], temperature=temperature)
        except JudgeError as exc:
            print(f"  second judge failed on {run.id}: {str(exc)[:200]}", file=sys.stderr)
            with lock:
                failed.append(run.id)
                first_few = len(failed) <= ANNOTATED_FAILURES
            if first_few:
                # The checks API serves annotations when the log is out of reach.
                annotate("warning", f"second judge failed on {run.id}", str(exc)[:600])
            return None

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        verdicts = list(pool.map(second, runs))
    return [(run, verdict) for run, verdict in zip(runs, verdicts, strict=True) if verdict]


def agreement_report(
    pairs: list[tuple[Run, JudgeVerdict]],
    *,
    first_judge: str | None,
    second_judge: str,
    temperature: float,
    eligible: int,
    sampled: int,
) -> dict[str, object]:
    """Both judges compared, mode by mode, in the shape MODEL-RESULTS.md renders."""
    agreement = measure_agreement([first_verdict(run) for run, _ in pairs], [v for _, v in pairs])
    return {
        "first_judge": first_judge,
        "second_judge": second_judge,
        "temperature": temperature,
        "eligible_runs": eligible,
        "sampled_runs": sampled,
        "compared_runs": agreement.n,
        "per_mode": {
            mode.value: {
                "raw_agreement": entry.raw_agreement,
                "cohens_kappa": entry.cohens_kappa,
                "both_present": entry.both_present,
                "first_only": entry.first_only,
                "second_only": entry.second_only,
                "neither_present": entry.neither_present,
            }
            for mode, entry in agreement.per_mode.items()
            if mode in JUDGED
        },
        "drift_mean_absolute_difference": agreement.drift_mean_absolute_difference,
        "drift_max_absolute_difference": agreement.drift_max_absolute_difference,
        "runs": sorted(run.id for run, _ in pairs),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


class RequestError(ValueError):
    """The request cannot be run as written."""


def _family(model: str) -> str:
    """The publisher segment of a model identifier, which is the family for this purpose."""
    parts = model.split("/")
    return parts[1] if len(parts) > 2 else parts[0]


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """What model-requests/judge.json asks for.

    `second_judges` is an ordered list of candidates. The first one that returns a valid
    verdict on the first sampled run judges them all, and the ones before it are reported
    with the reason they were passed over. A free tier serves a model one week and not the
    next, so a request that names a fallback costs one call instead of one more workflow.
    """

    second_judges: tuple[str, ...]
    sample: float = 0.5
    parallel: int = 4
    rpm: float = 15.0
    temperature: float = 0.0


def read_request(path: Path) -> JudgeRequest:
    """The committed request, with every setting checked before a call is made."""
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise RequestError("the request is a JSON object")
    unknown = sorted(set(body) - REQUEST_KEYS)
    if unknown:
        raise RequestError(f"unknown key(s): {', '.join(unknown)}")
    candidates = body.get("second_judges")
    if (
        not isinstance(candidates, list)
        or not 1 <= len(candidates) <= MAX_CANDIDATES
        or not all(isinstance(model, str) and MODEL_RE.match(model) for model in candidates)
    ):
        raise RequestError(
            f"second_judges lists 1 to {MAX_CANDIDATES} provider/publisher/model identifiers"
        )
    if len(set(candidates)) != len(candidates):
        raise RequestError("second_judges names a model twice")
    request = JudgeRequest(
        second_judges=tuple(candidates),
        sample=float(body.get("sample", 0.5)),
        parallel=int(body.get("parallel", 4)),
        rpm=float(body.get("rpm", 15.0)),
        temperature=float(body.get("temperature", 0.0)),
    )
    if not 0.0 < request.sample <= 1.0:
        raise RequestError("sample is a fraction in (0, 1]")
    if not 1 <= request.parallel <= 8:
        raise RequestError("parallel is between 1 and 8")
    if not 0.0 < request.rpm <= RPM_CEILING:
        raise RequestError(f"rpm is above 0 and at most {RPM_CEILING:g}, the free tier's limit")
    if not 0.0 <= request.temperature <= 1.0:
        raise RequestError("temperature is between 0 and 1")
    return request


def check_independence(second: str, first: str | None, agents: set[str]) -> None:
    """Refuse a second judge that could not give an independent reading.

    The point is a reading from somewhere else. Another model from the first judge's
    publisher shares too much training to count as a second opinion, and a model from the
    family of one of the agents under test could favour its sibling's trajectory, which is
    the bias a second reading exists to rule out. A first judge nobody named is refused
    too: the family check would pass by default, and the report would compare against a
    judge it cannot name.
    """
    if first is None:
        raise RequestError(
            "the first judge is not named. Promote the panel first, which writes "
            "fixtures/model-runs.provenance.json with the judge it ran"
        )
    if second == first:
        raise RequestError(f"{second} is the first judge")
    if _family(second) == _family(first):
        raise RequestError(f"{second} comes from the first judge's family, {_family(first)}")
    sibling = sorted(agent for agent in agents if _family(agent) == _family(second))
    if sibling:
        raise RequestError(
            f"{second} comes from the family of an agent it would judge, {sibling[0]}"
        )


def pick_judge(
    candidates: tuple[str, ...],
    run: Run,
    task: Task,
    *,
    temperature: float,
    client_for: Callable[[str], JudgeClient] = LiteLLMJudge,
) -> tuple[str, JudgeClient, JudgeVerdict]:
    """The first candidate that returns a valid verdict on `run`, with that verdict.

    Raises:
        RequestError: When no candidate returns one.
    """
    reasons: list[str] = []
    for model in candidates:
        client = client_for(model)
        try:
            verdict = judge_run(client, run, task, temperature=temperature)
        except JudgeError as exc:
            reasons.append(f"{model}: {str(exc)[:300]}")
            annotate("warning", f"passed over {model}", str(exc)[:600])
            continue
        return model, client, verdict
    raise RequestError("no candidate returned a valid verdict. " + " | ".join(reasons))


def main(argv: list[str]) -> int:
    """Judge the sample again with a second model and write the agreement report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path, help="Directory of judged run records.")
    parser.add_argument("--request", type=Path, default=REQUEST, help="The committed request.")
    parser.add_argument("--provenance", type=Path, default=PROVENANCE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, default=ROOT / "tasks")
    args = parser.parse_args(argv)

    runs = read_runs(args.records)
    first_judge: str | None = None
    if args.provenance.is_file():
        first_judge = json.loads(args.provenance.read_text(encoding="utf-8")).get("judge_model")
    agents = {run.config.model for run in runs}
    try:
        request = read_request(args.request)
        for candidate in request.second_judges:
            check_independence(candidate, first_judge, agents)
    except (RequestError, ValueError, OSError) as exc:
        print(f"cannot run the request: {exc}", file=sys.stderr)
        annotate("error", "judge request refused", str(exc))
        return 2

    tasks = {loaded.id: loaded.task for loaded in discover(args.tasks_root)}
    eligible = [run for run in judged_runs(runs) if run.task_id in tasks]
    if not eligible:
        print("no run in these records was judged, so there is nothing to compare", file=sys.stderr)
        annotate("error", "nothing to compare", "no run in these records was judged")
        return 1
    chosen = sample_runs(eligible, request.sample)
    print(f"{len(eligible)} judged run(s); the second judge reads {len(chosen)}")
    try:
        model, client, opening = pick_judge(
            request.second_judges,
            chosen[0],
            tasks[chosen[0].task_id],
            temperature=request.temperature,
        )
    except RequestError as exc:
        print(str(exc), file=sys.stderr)
        annotate("error", "no second judge", str(exc)[:900])
        return 1
    print(f"second judge: {model}")
    rest = second_opinions(
        chosen[1:],
        tasks,
        client,
        parallel=request.parallel,
        temperature=request.temperature,
        rpm=request.rpm,
    )
    pairs = [(chosen[0], opening), *rest]
    report = agreement_report(
        pairs,
        first_judge=first_judge,
        second_judge=model,
        temperature=request.temperature,
        eligible=len(eligible),
        sampled=len(chosen),
    )
    report["passed_over"] = list(request.second_judges[: request.second_judges.index(model)])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"compared {report['compared_runs']} run(s); wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
