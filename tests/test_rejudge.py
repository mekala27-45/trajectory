"""Reading again the runs a panel's judge could not read: which, how, and what may change."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import rejudge

from trajectory_core.judge import JudgeVerdict, ModeJudgement, ScriptedJudge
from trajectory_core.models import Detector, FailureModeHit, FailureModeId, Run
from trajectory_runner.loader import discover
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
MODEL_RUNS = ROOT / "fixtures" / "model-runs"
FAILURE = (
    "judge failed: the judge (nvidia_nim/stepfun-ai/step-5-preview) produced no valid "
    "verdict in 3 attempt(s). Last error: ValidationError"
)
JUDGE = "nvidia_nim/stepfun-ai/step-5-preview"


def _unjudged(error: str | None = FAILURE) -> Run:
    run = next(run for run in read_runs(MODEL_RUNS) if not run.solved and run.steps)
    assert run.score is not None
    rule = FailureModeHit(
        id=FailureModeId.RETRY_LOOP,
        name="retry loop",
        confidence=1.0,
        detector=Detector.RULE,
        evidence="steps 3 to 5",
    )
    return run.model_copy(
        update={
            "score": run.score.model_copy(update={"context_drift": None}),
            "failure_modes": [rule],
            "error": error,
        }
    )


def _verdict(drift: float = 0.4) -> JudgeVerdict:
    def mode(present: bool) -> ModeJudgement:
        return ModeJudgement(
            present=present,
            confidence=0.8 if present else 0.0,
            evidence="step 4" if present else "",
        )

    return JudgeVerdict(
        context_drift=drift,
        ignored_test_output=mode(True),
        long_horizon_context_loss=mode(False),
        environment_mismatch=mode(False),
    )


def test_only_a_run_the_judge_could_not_read_is_read_again():
    assert rejudge.needs_judge(_unjudged())
    assert not rejudge.needs_judge(_unjudged(error=None))
    judged = _unjudged()
    assert judged.score is not None
    judged = judged.model_copy(
        update={"score": judged.score.model_copy(update={"context_drift": 0.2})}
    )
    assert not rejudge.needs_judge(judged)


@pytest.mark.parametrize(
    ("error", "kept"),
    [
        (FAILURE, None),
        ("verification failed: timed out; " + FAILURE, "verification failed: timed out"),
        ("provider timeout", "provider timeout"),
        (None, None),
    ],
)
def test_the_judge_failure_note_comes_off_and_the_run_error_stays(error, kept):
    assert rejudge.without_judge_failure(error) == kept


def test_reading_again_adds_the_verdict_and_keeps_everything_else():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    before = _unjudged()
    after = rejudge.read_again(before, tasks[before.task_id], ScriptedJudge([_verdict(0.4)]))
    assert after.score is not None and after.score.context_drift == 0.4
    assert after.error is None
    detectors = sorted(hit.detector.value for hit in after.failure_modes)
    assert detectors == ["judge", "rule"]
    assert rejudge.changed_beyond_the_judge(before, after) == []


def test_a_change_beyond_the_judge_is_caught():
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    before = _unjudged()
    after = rejudge.read_again(before, tasks[before.task_id], ScriptedJudge([_verdict()]))
    tampered = after.model_copy(update={"steps": after.steps[:-1]})
    assert "steps" in rejudge.changed_beyond_the_judge(before, tampered)
    dropped_rule = after.model_copy(
        update={"failure_modes": [h for h in after.failure_modes if h.detector is Detector.JUDGE]}
    )
    assert "rule failure modes" in rejudge.changed_beyond_the_judge(before, dropped_rule)


def test_the_request_has_to_name_the_panel_judge(tmp_path: Path):
    request = tmp_path / "rejudge.json"
    request.write_text(json.dumps({"judge_model": JUDGE, "parallel": 2}), encoding="utf-8")
    assert rejudge.read_request(request, JUDGE) == (JUDGE, 2, 15.0)
    with pytest.raises(rejudge.RejudgeError, match="one judge"):
        rejudge.read_request(request, "nvidia_nim/deepseek-ai/deepseek-v4.1-flash")
    with pytest.raises(rejudge.RejudgeError, match="promote the panel first"):
        rejudge.read_request(request, None)
    request.write_text(json.dumps({"judge_model": JUDGE, "rpm": 90}), encoding="utf-8")
    with pytest.raises(rejudge.RejudgeError, match="free tier"):
        rejudge.read_request(request, JUDGE)


def test_promote_replaces_only_what_was_read_again(tmp_path: Path):
    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for path in sorted(MODEL_RUNS.glob("*.json"))[:3]:
        shutil.copy2(path, fixtures / path.name)
    runs = read_runs(fixtures)
    target = next(run for run in runs if not run.solved and run.steps)
    assert target.score is not None
    unjudged = target.model_copy(
        update={
            "score": target.score.model_copy(update={"context_drift": None}),
            "error": FAILURE,
        }
    )
    (fixtures / f"{target.id}.json").write_text(unjudged.model_dump_json(indent=2))

    rejudged = tmp_path / "rejudged"
    (rejudged / rejudge.RECORDS).mkdir(parents=True)
    for run in read_runs(fixtures):
        record = run
        if run.id == target.id:
            record = rejudge.read_again(run, tasks[run.task_id], ScriptedJudge([_verdict(0.3)]))
        path = rejudged / rejudge.RECORDS / f"{run.id}.json"
        path.write_text(record.model_dump_json(indent=2))
    (rejudged / rejudge.SUMMARY).write_text(
        json.dumps({"judge_model": JUDGE, "read_again": [target.id], "still_unjudged": []})
    )
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"judge_model": JUDGE}))

    code = rejudge.main(
        [
            "promote",
            str(rejudged),
            "--run-id",
            "123",
            "--fixtures",
            str(fixtures),
            "--provenance",
            str(provenance),
            "--no-render",
        ]
    )
    assert code == 0
    promoted = Run.model_validate_json((fixtures / f"{target.id}.json").read_text())
    assert promoted.score is not None and promoted.score.context_drift == 0.3
    meta = json.loads(provenance.read_text())
    assert meta["rejudged"]["read_again"] == [target.id]
    assert meta["rejudged"]["workflow_run_id"] == "123"
