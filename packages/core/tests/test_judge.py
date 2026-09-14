"""The judge is a language model, so what gets tested is everything around it.

The prompt, the schema, the retry on an invalid reply, the conversion to hits, and the
agreement statistic all have to be right whether or not the model behind them is. The
agreement statistic in particular is the number that tells a reader how much to trust the
three judged modes, so it gets tested against hand-constructed contingency tables.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from trajectory_core.judge import (
    SYSTEM_PROMPT,
    VERDICT_TOOL_NAME,
    JudgeError,
    JudgeVerdict,
    LiteLLMJudge,
    ModeAgreement,
    ModeJudgement,
    ScriptedJudge,
    build_user_prompt,
    judge_run,
    measure_agreement,
    render_trajectory,
    verdict_to_hits,
)
from trajectory_core.models import Detector, FailureModeId, RunStatus, ToolName
from trajectory_core.testing import (
    bash_steps,
    finish_step,
    make_run,
    make_step,
    make_task,
    make_verification,
)


def absent(**kwargs) -> ModeJudgement:
    return ModeJudgement(present=False, confidence=0.0, **kwargs)


def present(
    confidence: float = 0.9, evidence: str = "step 4 ignored the KeyError"
) -> ModeJudgement:
    return ModeJudgement(present=True, confidence=confidence, evidence=evidence, step_indices=[4])


def verdict(
    *, drift: float = 0.1, f06: bool = False, f07: bool = False, f10: bool = False
) -> JudgeVerdict:
    return JudgeVerdict(
        context_drift=drift,
        drift_reasoning="The last third kept working on the failing test.",
        ignored_test_output=present() if f06 else absent(),
        long_horizon_context_loss=present() if f07 else absent(),
        environment_mismatch=present() if f10 else absent(),
    )


@pytest.fixture
def run():
    steps = [
        make_step(0, command="ls -la", output="src\ntests"),
        make_step(1, command="pytest -q", exit_code=1, output="3 failed, 3 passed"),
        make_step(
            2,
            tool=ToolName.READ_FILE.value,
            args={"path": "src/app.py"},
            exit_code=None,
            output="def add(a, b):\n    return a - b",
        ),
        make_step(3, command="sed -i s/-/+/ src/app.py"),
        make_step(4, command="pytest -q", exit_code=0, output="6 passed"),
        finish_step(5, "Fixed the sign."),
    ]
    return make_run(steps, verification=make_verification(tests_passed=6, tests_total=6))


class TestVerdictSchema:
    def test_round_trips(self):
        v = verdict(f06=True)
        assert JudgeVerdict.model_validate_json(v.model_dump_json()) == v

    def test_confidence_is_bounded(self):
        with pytest.raises(ValidationError):
            ModeJudgement(present=True, confidence=1.4)

    def test_drift_is_bounded(self):
        with pytest.raises(ValidationError):
            verdict(drift=1.2)

    def test_named_fields_map_back_to_taxonomy_identifiers(self):
        """One field per mode, so a model cannot duplicate, omit, or invent one."""
        assert set(verdict().mode_map()) == {
            FailureModeId.IGNORED_TEST_OUTPUT,
            FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
            FailureModeId.ENVIRONMENT_MISMATCH,
        }

    def test_the_schema_the_model_sees_has_a_description_on_every_mode(self):
        schema = JudgeVerdict.model_json_schema()
        for field in ("ignored_test_output", "long_horizon_context_loss", "environment_mismatch"):
            assert schema["properties"][field]["description"]
        assert schema["properties"]["context_drift"]["description"]


class TestRendering:
    def test_includes_the_task_statement_and_the_hidden_test_result(self, run):
        text = render_trajectory(run, make_task())
        assert "Make the tests pass." in text
        assert "6 of 6 passed" in text
        assert "### step 0: bash" in text

    def test_marks_rejected_calls(self):
        run = make_run([make_step(0, tool="nope", schema_violation=True)])
        assert "rejected by the harness as malformed" in render_trajectory(run, make_task())

    def test_notes_when_the_harness_compressed_context(self, run):
        run.context_compressed = True
        assert "compressed older tool outputs" in render_trajectory(run, make_task())

    def test_long_output_is_elided_in_the_middle(self):
        run = make_run([make_step(0, command="cat big", output="x" * 5000)])
        text = render_trajectory(run, make_task(), output_chars=200)
        assert "chars elided" in text
        assert len(text) < 2000

    def test_a_long_trajectory_keeps_the_start_and_the_end(self):
        """Metric 8 is about the final third, so the end is the part that must survive."""
        steps = [make_step(i, command=f"echo step-{i}", output="y" * 400) for i in range(80)]
        run = make_run(steps)
        text = render_trajectory(run, make_task(), output_chars=300, total_chars=6000)
        assert "middle step(s) elided" in text
        assert "echo step-0" in text
        assert "echo step-79" in text

    def test_the_prompt_restates_the_rubric_and_asks_for_the_tool(self, run):
        prompt = build_user_prompt(run, make_task())
        assert "F06" in prompt
        assert "F07" in prompt
        assert "F10" in prompt
        assert "record_verdict" in prompt
        assert "final third" in prompt

    def test_the_system_prompt_demands_evidence_and_allows_hedging(self):
        assert "step numbers" in SYSTEM_PROMPT
        assert "below 0.6" in SYSTEM_PROMPT


class TestScriptedJudge:
    def test_replays_verdicts_in_order(self, run):
        judge = ScriptedJudge([verdict(drift=0.2), verdict(drift=0.7)])
        assert judge_run(judge, run, make_task()).context_drift == pytest.approx(0.2)
        assert judge_run(judge, run, make_task()).context_drift == pytest.approx(0.7)

    def test_records_the_temperature_it_was_asked_for(self, run):
        judge = ScriptedJudge([verdict(), verdict()])
        judge_run(judge, run, make_task(), temperature=0.0)
        judge_run(judge, run, make_task(), temperature=0.7)
        assert [temperature for _, temperature in judge.calls] == [0.0, 0.7]

    def test_running_out_is_an_error_not_a_default_verdict(self, run):
        with pytest.raises(JudgeError, match="ran out of verdicts"):
            judge_run(ScriptedJudge([]), run, make_task())


class _FakeLiteLLM:
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = payloads
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        arguments = payload if isinstance(payload, str) else json.dumps(payload)
        call = SimpleNamespace(
            id="c1", function=SimpleNamespace(name=VERDICT_TOOL_NAME, arguments=arguments)
        )
        message = SimpleNamespace(content=None, tool_calls=[call] if payload is not None else None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")]
        )


def _judge_with(payloads: list[object], **kwargs) -> LiteLLMJudge:
    judge = LiteLLMJudge("anthropic/test-model", **kwargs)
    judge._litellm = _FakeLiteLLM(payloads)
    return judge


class TestLiteLLMJudge:
    def test_the_verdict_schema_is_presented_as_a_forced_tool(self):
        """Structured by construction, not by parsing prose."""
        definition = LiteLLMJudge.tool_definition()
        assert definition["function"]["name"] == VERDICT_TOOL_NAME
        properties = definition["function"]["parameters"]["properties"]
        assert "context_drift" in properties
        assert "ignored_test_output" in properties

    def test_parses_a_valid_verdict(self, run):
        payload = json.loads(verdict(drift=0.3, f06=True).model_dump_json())
        judge = _judge_with([payload])
        result = judge_run(judge, run, make_task())
        assert result.context_drift == pytest.approx(0.3)
        assert result.ignored_test_output.present is True

    def test_forces_the_tool_call(self, run):
        payload = json.loads(verdict().model_dump_json())
        judge = _judge_with([payload])
        judge_run(judge, run, make_task())
        sent = judge._litellm.calls[0]
        assert sent["tool_choice"]["function"]["name"] == VERDICT_TOOL_NAME
        assert len(sent["tools"]) == 1

    def test_retries_with_the_validation_error_when_the_reply_does_not_fit(self, run):
        """A model that returns something the schema rejects is told exactly what was wrong."""
        good = json.loads(verdict().model_dump_json())
        judge = _judge_with([{"context_drift": 9.0}, good], max_attempts=2)
        result = judge_run(judge, run, make_task())
        assert result.context_drift == pytest.approx(0.1)
        second_call = judge._litellm.calls[1]
        assert "was rejected" in second_call["messages"][-1]["content"]

    def test_gives_up_rather_than_substituting_a_default_verdict(self, run):
        """A silent default would poison the published numbers invisibly."""
        judge = _judge_with([{"nope": 1}, {"nope": 2}], max_attempts=2)
        with pytest.raises(JudgeError, match="no valid verdict"):
            judge_run(judge, run, make_task())

    def test_a_reply_with_no_tool_call_is_an_error(self, run):
        judge = _judge_with([None], max_attempts=1)
        with pytest.raises(JudgeError):
            judge_run(judge, run, make_task())

    def test_malformed_json_is_retried_not_regex_parsed(self, run):
        good = json.loads(verdict().model_dump_json())
        judge = _judge_with(['{"context_drift": 0.1,', good], max_attempts=2)
        assert judge_run(judge, run, make_task()).context_drift == pytest.approx(0.1)

    def test_api_base_is_forwarded_for_local_models(self, run):
        payload = json.loads(verdict().model_dump_json())
        judge = _judge_with([payload], api_base="http://localhost:11434")
        judge_run(judge, run, make_task())
        assert judge._litellm.calls[0]["api_base"] == "http://localhost:11434"


class TestVerdictToHits:
    def test_only_modes_judged_present_become_hits(self):
        hits = verdict_to_hits(verdict(f06=True, f10=True))
        assert {hit.id.value for hit in hits} == {"F06", "F10"}
        assert all(hit.detector is Detector.JUDGE for hit in hits)

    def test_an_all_clear_verdict_produces_nothing(self):
        assert verdict_to_hits(verdict()) == []

    def test_hits_carry_the_evidence_and_steps_the_judge_gave(self):
        hit = verdict_to_hits(verdict(f07=True))[0]
        assert hit.evidence == "step 4 ignored the KeyError"
        assert hit.step_indices == [4]

    def test_a_finding_with_no_evidence_is_labelled_rather_than_left_blank(self):
        v = JudgeVerdict(
            context_drift=0.0,
            ignored_test_output=ModeJudgement(present=True, confidence=0.5),
            long_horizon_context_loss=absent(),
            environment_mismatch=absent(),
        )
        assert verdict_to_hits(v)[0].evidence == "the judge reported no evidence"

    def test_hits_are_ranked_by_confidence(self):
        v = JudgeVerdict(
            context_drift=0.0,
            ignored_test_output=present(confidence=0.4),
            long_horizon_context_loss=present(confidence=0.95),
            environment_mismatch=absent(),
        )
        assert [hit.id.value for hit in verdict_to_hits(v)] == ["F07", "F06"]


class TestModeAgreementStatistic:
    MODE = FailureModeId.IGNORED_TEST_OUTPUT

    def test_perfect_agreement_on_a_mode_that_varies_gives_kappa_one(self):
        """5 both, 5 neither, no disagreement: observed 1.0, chance 0.5, kappa 1.0."""
        entry = ModeAgreement(
            self.MODE, both_present=5, first_only=0, second_only=0, neither_present=5
        )
        assert entry.raw_agreement == 1.0
        assert entry.cohens_kappa == 1.0

    def test_chance_level_agreement_gives_kappa_zero(self):
        """25 in every cell: observed 0.5, chance 0.5, kappa 0.0."""
        entry = ModeAgreement(
            self.MODE, both_present=25, first_only=25, second_only=25, neither_present=25
        )
        assert entry.raw_agreement == 0.5
        assert entry.cohens_kappa == 0.0

    def test_a_mode_that_never_fired_has_no_kappa(self):
        """Two passes that both say absent every time agree perfectly and say nothing.

        Reporting 0.0 there would read as disagreement, which is the opposite of the truth,
        so the statistic reports that it is not computable.
        """
        entry = ModeAgreement(
            self.MODE, both_present=0, first_only=0, second_only=0, neither_present=20
        )
        assert entry.raw_agreement == 1.0
        assert entry.cohens_kappa is None

    def test_strong_but_imperfect_agreement(self):
        """8 both, 10 neither, 1 each way. Observed 18/20 = 0.9, kappa 0.798."""
        entry = ModeAgreement(
            self.MODE, both_present=8, first_only=1, second_only=1, neither_present=10
        )
        assert entry.raw_agreement == 0.9
        assert entry.cohens_kappa == pytest.approx(0.798, abs=1e-3)

    def test_asymmetric_disagreement_is_visible_in_the_table(self):
        """One pass seeing the mode ten times and the other twice is its own problem."""
        entry = ModeAgreement(
            self.MODE, both_present=2, first_only=8, second_only=0, neither_present=10
        )
        assert entry.first_only == 8
        assert entry.second_only == 0
        assert entry.n == 20

    def test_an_empty_comparison_has_no_kappa(self):
        entry = ModeAgreement(
            self.MODE, both_present=0, first_only=0, second_only=0, neither_present=0
        )
        assert entry.cohens_kappa is None
        assert entry.raw_agreement == 0.0


class TestMeasureAgreement:
    def test_counts_every_cell_and_the_drift_spread(self):
        first = [verdict(drift=0.0, f06=True), verdict(drift=0.2), verdict(drift=0.9, f07=True)]
        second = [verdict(drift=0.1, f06=True), verdict(drift=0.2, f06=True), verdict(drift=0.5)]
        result = measure_agreement(first, second)

        f06 = result.per_mode[FailureModeId.IGNORED_TEST_OUTPUT]
        assert (f06.both_present, f06.first_only, f06.second_only, f06.neither_present) == (
            1,
            0,
            1,
            1,
        )

        f07 = result.per_mode[FailureModeId.LONG_HORIZON_CONTEXT_LOSS]
        assert (f07.both_present, f07.first_only, f07.second_only, f07.neither_present) == (
            0,
            1,
            0,
            2,
        )

        # drift differences: 0.1, 0.0, 0.4 -> mean 0.1667, max 0.4
        assert result.drift_mean_absolute_difference == pytest.approx(0.1667, abs=1e-3)
        assert result.drift_max_absolute_difference == pytest.approx(0.4)
        assert result.n == 3

    def test_mean_raw_agreement_averages_the_modes(self):
        first = [verdict(f06=True), verdict()]
        second = [verdict(f06=True), verdict()]
        assert measure_agreement(first, second).mean_raw_agreement == 1.0

    def test_mismatched_passes_are_rejected(self):
        with pytest.raises(ValueError, match="same runs in the same order"):
            measure_agreement([verdict()], [verdict(), verdict()])

    def test_an_empty_comparison_does_not_raise(self):
        result = measure_agreement([], [])
        assert result.n == 0
        assert result.drift_mean_absolute_difference == 0.0


def test_a_run_status_is_visible_to_the_judge():
    run = make_run(bash_steps("a"), status=RunStatus.TIMEOUT)
    assert "RUN STATUS: timeout" in render_trajectory(run, make_task())
