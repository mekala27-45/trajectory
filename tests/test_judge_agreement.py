"""Two judges compared: what is read back, what is left out, and what the report says."""

from __future__ import annotations

import json
from pathlib import Path

import judge_agreement as agreement
import pytest

from trajectory_core.judge import JudgeVerdict, ModeJudgement, ScriptedJudge
from trajectory_core.models import Detector, FailureModeHit, FailureModeId, Run
from trajectory_runner.loader import discover
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
MODEL_RUNS = ROOT / "fixtures" / "model-runs"


def _unsolved() -> list[Run]:
    return [run for run in read_runs(MODEL_RUNS) if not run.solved and run.steps]


def _judged(run: Run, *, drift: float, hits: list[FailureModeHit], error: str = "") -> Run:
    assert run.score is not None
    return run.model_copy(
        update={
            "score": run.score.model_copy(update={"context_drift": drift}),
            "failure_modes": hits,
            "error": error or None,
        }
    )


def _hit(mode: FailureModeId, detector: Detector, confidence: float = 0.8) -> FailureModeHit:
    return FailureModeHit(
        id=mode, name=mode.value, confidence=confidence, detector=detector, evidence="step 3"
    )


def _verdict(drift: float, *, ignored: bool = False, lost: bool = False) -> JudgeVerdict:
    def judgement(present: bool) -> ModeJudgement:
        return ModeJudgement(present=present, confidence=0.7 if present else 0.0)

    return JudgeVerdict(
        context_drift=drift,
        ignored_test_output=judgement(ignored),
        long_horizon_context_loss=judgement(lost),
        environment_mismatch=judgement(False),
    )


def test_the_first_verdict_is_read_back_from_judge_hits_only():
    base = _unsolved()[0]
    run = _judged(
        base,
        drift=0.4,
        hits=[
            _hit(FailureModeId.IGNORED_TEST_OUTPUT, Detector.JUDGE, 0.8),
            # A rule hit says nothing about what the judge decided.
            _hit(FailureModeId.RETRY_LOOP, Detector.RULE, 1.0),
        ],
    )
    verdict = agreement.first_verdict(run)
    assert verdict.context_drift == 0.4
    assert verdict.ignored_test_output.present
    assert verdict.ignored_test_output.confidence == 0.8
    assert not verdict.long_horizon_context_loss.present
    assert not verdict.environment_mismatch.present


def test_only_runs_the_first_judge_read_are_compared():
    first, second, third = _unsolved()[:3]
    solved = next(run for run in read_runs(MODEL_RUNS) if run.solved)
    judged = _judged(first, drift=0.1, hits=[])
    failed = _judged(second, drift=0.2, hits=[], error="judge failed: timed out")
    unjudged = third  # no drift recorded: the judge never ran
    kept = agreement.judged_runs([judged, failed, unjudged, solved])
    assert [run.id for run in kept] == [judged.id]


@pytest.mark.parametrize(
    ("size", "fraction", "expected"), [(10, 0.5, 5), (10, 1.0, 10), (3, 0.1, 1)]
)
def test_the_sample_is_fixed_and_spread(size: int, fraction: float, expected: int):
    runs = _unsolved()[:size]
    picked = agreement.sample_runs(runs, fraction)
    assert len(picked) == min(expected, len(runs))
    assert picked == agreement.sample_runs(list(reversed(runs)), fraction)
    assert len({run.id for run in picked}) == len(picked)


def test_two_judges_compared_end_to_end():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    one, two = _unsolved()[:2]
    first_pass = [
        _judged(one, drift=0.2, hits=[_hit(FailureModeId.IGNORED_TEST_OUTPUT, Detector.JUDGE)]),
        _judged(two, drift=0.6, hits=[]),
    ]
    # The second judge agrees about the first run's ignored output and finds context loss in
    # the second run that the first judge did not.
    client = ScriptedJudge([_verdict(0.3, ignored=True), _verdict(0.5, lost=True)])
    pairs = agreement.second_opinions(first_pass, tasks, client, parallel=1)
    report = agreement.agreement_report(
        pairs,
        first_judge="first",
        second_judge="second",
        temperature=0.0,
        eligible=2,
        sampled=2,
    )
    assert report["compared_runs"] == 2
    modes = report["per_mode"]
    assert isinstance(modes, dict)
    assert modes["F06"]["both_present"] == 1
    assert modes["F06"]["neither_present"] == 1
    assert modes["F06"]["raw_agreement"] == 1.0
    assert modes["F07"]["second_only"] == 1
    assert modes["F07"]["raw_agreement"] == 0.5
    assert report["drift_mean_absolute_difference"] == pytest.approx(0.1)
    assert report["runs"] == sorted([one.id, two.id])


def test_a_run_the_second_judge_cannot_judge_is_left_out():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    one, two = _unsolved()[:2]
    first_pass = [_judged(one, drift=0.2, hits=[]), _judged(two, drift=0.6, hits=[])]
    client = ScriptedJudge([_verdict(0.3)])  # runs out after one verdict
    pairs = agreement.second_opinions(first_pass, tasks, client, parallel=1)
    assert [run.id for run, _ in pairs] == [one.id]


def _request(tmp_path: Path, **body: object) -> Path:
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_a_request_is_checked_before_any_call(tmp_path: Path):
    good = agreement.read_request(
        _request(
            tmp_path,
            second_judges=["nvidia_nim/deepseek-ai/deepseek-v4.1-flash", "nvidia_nim/g/gemma"],
            sample=1.0,
        )
    )
    assert good.second_judges == (
        "nvidia_nim/deepseek-ai/deepseek-v4.1-flash",
        "nvidia_nim/g/gemma",
    )
    assert good.sample == 1.0
    assert good.rpm == 15.0
    with pytest.raises(agreement.RequestError, match="unknown key"):
        agreement.read_request(_request(tmp_path, second_judges=["a/b/c"], judge="a/b/c"))
    with pytest.raises(agreement.RequestError, match="free tier"):
        agreement.read_request(_request(tmp_path, second_judges=["a/b/c"], rpm=60))
    with pytest.raises(agreement.RequestError, match="fraction"):
        agreement.read_request(_request(tmp_path, second_judges=["a/b/c"], sample=0))
    with pytest.raises(agreement.RequestError, match="identifiers"):
        agreement.read_request(_request(tmp_path, second_judges=["no spaces allowed"]))
    with pytest.raises(agreement.RequestError, match="identifiers"):
        agreement.read_request(_request(tmp_path, second_judges=[]))
    with pytest.raises(agreement.RequestError, match="twice"):
        agreement.read_request(_request(tmp_path, second_judges=["a/b/c", "a/b/c"]))


def test_an_unnamed_first_judge_is_refused():
    with pytest.raises(agreement.RequestError, match="not named"):
        agreement.check_independence("nvidia_nim/deepseek-ai/deepseek-v4.1-flash", None, set())


@pytest.mark.parametrize(
    ("second", "reason"),
    [
        ("nvidia_nim/stepfun-ai/step-5-preview", "is the first judge"),
        ("nvidia_nim/stepfun-ai/step-5-mini", "first judge's family"),
        ("nvidia_nim/openai/gpt-oss-20b", "family of an agent"),
        ("nvidia_nim/openai/gpt-oss-120b", "family of an agent"),
    ],
)
def test_the_second_judge_has_to_come_from_somewhere_else(second: str, reason: str):
    first = "nvidia_nim/stepfun-ai/step-5-preview"
    agents = {"nvidia_nim/openai/gpt-oss-20b"}
    with pytest.raises(agreement.RequestError, match=reason):
        agreement.check_independence(second, first, agents)
    agreement.check_independence("nvidia_nim/deepseek-ai/deepseek-v4.1-flash", first, agents)


def test_a_candidate_that_cannot_judge_is_passed_over():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    run = _judged(_unsolved()[0], drift=0.2, hits=[])
    clients = {
        "nvidia_nim/a/broken": ScriptedJudge([]),  # raises: it has no verdict to give
        "nvidia_nim/b/works": ScriptedJudge([_verdict(0.1)]),
    }
    model, _, verdict = agreement.pick_judge(
        ("nvidia_nim/a/broken", "nvidia_nim/b/works"),
        run,
        tasks[run.task_id],
        temperature=0.0,
        client_for=clients.__getitem__,
    )
    assert model == "nvidia_nim/b/works"
    assert verdict.context_drift == 0.1


def test_no_working_candidate_is_an_error_that_names_each_reason():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    run = _judged(_unsolved()[0], drift=0.2, hits=[])
    with pytest.raises(agreement.RequestError, match="nvidia_nim/a/broken"):
        agreement.pick_judge(
            ("nvidia_nim/a/broken",),
            run,
            tasks[run.task_id],
            temperature=0.0,
            client_for=lambda _: ScriptedJudge([]),
        )
