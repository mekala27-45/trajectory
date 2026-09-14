"""The data contract is the product, so it gets tested like one."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trajectory_core.models import (
    SCHEMA_VERSION,
    BundleManifest,
    FailureModeHit,
    FailureModeId,
    Language,
    ResultsBundle,
    Run,
    RunnerFingerprint,
    RunStatus,
    SandboxBackend,
    Step,
    Task,
    TaskSummary,
    ToolName,
    Verification,
)
from trajectory_core.testing import (
    bash_steps,
    finish_step,
    make_run,
    make_step,
    make_task,
    make_verification,
)


class TestTask:
    def test_round_trips_through_json(self):
        task = make_task()
        restored = Task.model_validate_json(task.model_dump_json())
        assert restored == task

    def test_rejects_an_unknown_field(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            make_task(sneaky_extra="nope")

    def test_rejects_a_bad_identifier(self):
        for bad in ["Has-Capitals", "under_scores", "trailing-", "-leading", "double--hyphen"]:
            with pytest.raises(ValidationError):
                make_task(id=bad)

    def test_accepts_conventional_identifiers(self):
        for good in ["py-failing-suite-01", "go-race-01", "sql-migration-01", "a1"]:
            assert make_task(id=good).id == good

    def test_network_access_demands_a_reason(self):
        with pytest.raises(ValidationError, match="network_reason"):
            make_task(network_allowed=True)
        task = make_task(
            network_allowed=True, network_reason="Pulls a pinned wheel from a local mirror."
        )
        assert task.network_allowed is True

    def test_reference_step_count_must_fit_in_the_budget(self):
        with pytest.raises(ValidationError, match="exceeds max_steps"):
            make_task(max_steps=5, reference_step_count=6)

    def test_difficulty_is_bounded(self):
        with pytest.raises(ValidationError):
            make_task(difficulty=0)
        with pytest.raises(ValidationError):
            make_task(difficulty=6)

    def test_summary_hides_the_verification_command(self):
        task = make_task(verify_cmd="pytest -q /verify/test_secret.py")
        summary = TaskSummary.from_task(task)
        serialized = summary.model_dump_json()
        assert "verify" not in serialized
        assert "test_secret" not in serialized
        assert summary.id == task.id
        assert summary.difficulty == task.difficulty


class TestStep:
    def test_failed_is_true_only_for_non_zero_exit_codes(self):
        assert make_step(0, command="false", exit_code=1).failed is True
        assert make_step(0, command="true", exit_code=0).failed is False
        assert make_step(0, tool=ToolName.FINISH.value, exit_code=None).failed is False

    def test_keeps_tool_arguments_exactly_as_the_model_sent_them(self):
        step = make_step(0, tool="grep_files", args={"pattern": 42, "nested": {"a": [1, 2]}})
        restored = Step.model_validate_json(step.model_dump_json())
        assert restored.tool_args == {"pattern": 42, "nested": {"a": [1, 2]}}

    def test_unknown_tool_names_are_data_not_errors(self):
        step = make_step(0, tool="definitely_not_a_tool", schema_violation=True)
        assert step.tool_name == "definitely_not_a_tool"
        assert step.schema_violation is True


class TestVerification:
    def test_partial_credit_is_the_pass_fraction(self):
        v = make_verification(passed=False, tests_passed=3, tests_total=8)
        assert v.partial_credit == pytest.approx(0.375)

    def test_partial_credit_without_a_test_count_falls_back_to_the_verdict(self):
        passed = Verification(
            passed=True, tests_passed=0, tests_total=0, duration_ms=1, exit_code=0, parse_ok=False
        )
        failed = Verification(
            passed=False, tests_passed=0, tests_total=0, duration_ms=1, exit_code=1, parse_ok=False
        )
        assert passed.partial_credit == 1.0
        assert failed.partial_credit == 0.0


class TestRun:
    def test_round_trips_with_steps_and_verification(self):
        run = make_run(
            [*bash_steps("ls", "pytest -q"), finish_step(2)],
            verification=make_verification(),
        )
        restored = Run.model_validate_json(run.model_dump_json())
        assert restored.id == run.id
        assert [s.tool_name for s in restored.steps] == ["bash", "bash", "finish"]
        assert restored.verification is not None
        assert restored.verification.passed is True

    def test_computed_fields_survive_serialization(self):
        run = make_run(
            [
                make_step(0, command="ls", cost_usd=0.02),
                make_step(1, command="pytest", cost_usd=0.03),
            ],
            verification=make_verification(),
            finished_after_s=12.5,
        )
        payload = json.loads(run.model_dump_json())
        assert payload["wall_clock_s"] == 12.5
        assert payload["solved"] is True
        assert payload["total_cost_usd"] == pytest.approx(0.05)

    def test_completed_status_does_not_mean_solved(self):
        """The single most common bug in harnesses of this kind, so it gets a test."""
        run = make_run(
            [finish_step(0)],
            verification=make_verification(passed=False, tests_passed=1, tests_total=4),
            status=RunStatus.COMPLETED,
        )
        assert run.status is RunStatus.COMPLETED
        assert run.solved is False

    def test_wall_clock_is_zero_while_the_run_is_open(self):
        run = make_run([])
        run.finished_at = None
        assert run.wall_clock_s == 0.0

    def test_seal_is_idempotent(self):
        run = make_run([])
        first = run.finished_at
        run.seal(finished_at=datetime(2030, 1, 1, tzinfo=UTC))
        assert run.finished_at == first

    def test_seal_closes_an_open_run(self):
        run = make_run([])
        run.finished_at = None
        run.seal(finished_at=datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC))
        assert run.wall_clock_s == 5.0

    def test_default_id_is_a_sortable_uuid7(self):
        ids = [make_run([]).id for _ in range(200)]
        assert ids == sorted(ids)


class TestFingerprint:
    def test_capture_reports_the_current_machine(self):
        fp = RunnerFingerprint.capture(sandbox_backend=SandboxBackend.LOCAL)
        assert fp.sandbox_backend is SandboxBackend.LOCAL
        assert fp.cpu_count >= 1
        assert fp.python_version.startswith("3.")
        assert fp.docker_version is None

    def test_capture_records_docker_when_present(self):
        fp = RunnerFingerprint.capture(
            sandbox_backend=SandboxBackend.DOCKER, docker_version="27.3.1", ci=True
        )
        assert fp.docker_version == "27.3.1"
        assert fp.ci is True


class TestBundle:
    def test_round_trips(self):
        run = make_run([make_step(0, command="ls")], verification=make_verification())
        bundle = ResultsBundle(
            manifest=BundleManifest(
                suite="core-12",
                run_count=1,
                models=[run.config.model],
                fingerprint=run.runner_fingerprint,
            ),
            runs=[run],
        )
        restored = ResultsBundle.model_validate_json(bundle.model_dump_json())
        assert restored.manifest.schema_version == SCHEMA_VERSION
        assert restored.runs[0].id == run.id


class TestFailureModeHit:
    def test_confidence_is_bounded(self):
        with pytest.raises(ValidationError):
            FailureModeHit(
                id=FailureModeId.RETRY_LOOP,
                name="retry loop",
                confidence=1.5,
                detector="rule",
                evidence="x",
            )

    def test_serializes_the_taxonomy_identifier(self):
        hit = FailureModeHit(
            id=FailureModeId.PREMATURE_SUCCESS,
            name="premature success",
            confidence=1.0,
            detector="rule",
            evidence="called finish at step 3 while 2 of 5 tests fail",
            step_indices=[3],
        )
        assert json.loads(hit.model_dump_json())["id"] == "F03"


def test_language_enum_covers_the_four_supported_languages():
    assert {lang.value for lang in Language} == {"python", "typescript", "go", "sql", "any"}
