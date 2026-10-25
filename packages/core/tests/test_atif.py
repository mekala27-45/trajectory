"""ATIF out and back in: valid for Harbor, lossless for this harness, useful for others."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from trajectory_core.atif import (
    ATIF_VERSION,
    IMPORT_PREFIX,
    AtifError,
    atif_to_run,
    run_to_atif,
    validate,
)
from trajectory_core.models import FailureModeId, Run, SandboxBackend
from trajectory_core.testing import make_task

ROOT = Path(__file__).resolve().parents[3]
# Every committed record: the scripted matrix and the real model runs, which carry
# reasoning, provider timeouts and judge output the scripted ones never do.
RECORDED = sorted(
    path
    for directory in ("recorded-runs", "model-runs")
    for path in (ROOT / "fixtures" / directory).glob("*.json")
)


def _task_for(run: Run):
    """The real task definition, so the round trip is scored against true reference data."""
    import yaml

    from trajectory_core.models import Task

    data = yaml.safe_load((ROOT / "tasks" / run.suite / run.task_id / "task.yaml").read_text())
    return Task(id=run.task_id, suite=run.suite, **data)


@pytest.fixture(scope="module")
def recorded() -> list[Run]:
    return [Run.model_validate_json(path.read_text(encoding="utf-8")) for path in RECORDED]


class TestExport:
    def test_every_recorded_run_exports_to_a_valid_document(self, recorded: list[Run]):
        for run in recorded:
            doc = run_to_atif(run, _task_for(run))
            validate(doc)
            assert doc["schema_version"] == ATIF_VERSION
            assert doc["steps"][0]["source"] == "user"
            assert len(doc["steps"]) == len(run.steps) + 1
            assert doc["final_metrics"]["total_steps"] == len(doc["steps"])

    def test_each_observation_refers_to_its_own_steps_tool_call(self, recorded: list[Run]):
        doc = run_to_atif(recorded[0], _task_for(recorded[0]))
        for step in doc["steps"][1:]:
            call_ids = {call["tool_call_id"] for call in step["tool_calls"]}
            for result in step["observation"]["results"]:
                assert result["source_call_id"] in call_ids


class TestRoundTrip:
    def test_a_native_export_imports_back_without_loss(self, recorded: list[Run]):
        """Every recorded run: same steps, failure modes, score and verification record."""
        for run in recorded:
            task = _task_for(run)
            back = atif_to_run(run_to_atif(run, task), task)
            assert [(s.tool_name, s.tool_args, s.tool_output, s.exit_code) for s in back.steps] == [
                (s.tool_name, s.tool_args, s.tool_output, s.exit_code) for s in run.steps
            ]
            assert back.solved == run.solved
            assert back.status == run.status
            assert {hit.id for hit in back.failure_modes} == {hit.id for hit in run.failure_modes}
            assert back.score is not None and run.score is not None
            assert back.score.model_dump() == run.score.model_dump(), run.id
            # The verification record survives: counts, exit code and parse flag. Rebuilding
            # it from counts alone used to turn the timed-out Go run, 7 of 10 with exit 124,
            # into a solved 7 of 7.
            if run.verification is None:
                assert back.verification is None
            else:
                assert back.verification is not None
                assert back.verification.tests_passed == run.verification.tests_passed
                assert back.verification.tests_total == run.verification.tests_total
                assert back.verification.exit_code == run.verification.exit_code
                assert back.verification.parse_ok == run.verification.parse_ok
                assert back.verification.passed == run.verification.passed

    def test_an_import_is_kept_off_the_leaderboard(self, recorded: list[Run]):
        task = _task_for(recorded[0])
        back = atif_to_run(run_to_atif(recorded[0], task), task)
        assert back.config.sandbox_backend is SandboxBackend.LOCAL


def _foreign(*calls: tuple[str, dict, str]) -> dict:
    """An ATIF document shaped like another agent's, with its own tool names."""
    steps: list[dict] = [{"step_id": 1, "source": "user", "message": "Fix the failing test."}]
    for name, arguments, output in calls:
        call_id = f"toolu_{len(steps):02d}"
        steps.append(
            {
                "step_id": len(steps) + 1,
                "source": "agent",
                "message": "",
                "tool_calls": [
                    {"tool_call_id": call_id, "function_name": name, "arguments": arguments}
                ],
                "observation": {"results": [{"source_call_id": call_id, "content": output}]},
            }
        )
    return {
        "schema_version": "ATIF-v1.6",
        "session_id": "s-1",
        "agent": {"name": "claude-code", "version": "2.0.0", "model_name": "some-model"},
        "steps": steps,
    }


class TestForeignImport:
    def test_tool_names_and_arguments_are_mapped_to_this_harness(self):
        doc = _foreign(
            ("Read", {"file_path": "src/app.py"}, "def add(a, b): ..."),
            ("Bash", {"command": "pytest -q"}, "1 failed"),
        )
        run = atif_to_run(doc, make_task())
        assert [step.tool_name for step in run.steps] == ["read_file", "bash"]
        assert run.steps[0].tool_args == {"path": "src/app.py"}
        assert run.config.model == f"{IMPORT_PREFIX}some-model"

    def test_a_repeated_command_is_named_a_retry_loop(self):
        doc = _foreign(*[("Bash", {"command": "pytest -q"}, "1 failed")] * 4)
        run = atif_to_run(doc, make_task())
        assert FailureModeId.RETRY_LOOP in {hit.id for hit in run.failure_modes}

    def test_the_reward_decides_whether_it_solved(self):
        doc = _foreign(("Bash", {"command": "pytest -q"}, "1 passed"))
        assert atif_to_run(doc, make_task(), reward=1.0).solved
        assert not atif_to_run(doc, make_task(), reward=0.0).solved

    def test_a_reward_is_spread_over_the_declared_test_count(self):
        """A reward carries no counts, so it reads as all or none of the declared tests."""
        doc = _foreign(("Bash", {"command": "pytest -q"}, "1 passed"))
        solved = atif_to_run(doc, make_task(expected_tests=9), reward=1.0)
        assert solved.verification is not None
        assert (solved.verification.tests_passed, solved.verification.tests_total) == (9, 9)
        missed = atif_to_run(doc, make_task(expected_tests=9), reward=0.0)
        assert missed.verification is not None
        assert (missed.verification.tests_passed, missed.verification.tests_total) == (0, 9)

    def test_a_text_turn_between_tool_calls_is_skipped(self):
        doc = _foreign(("Bash", {"command": "ls"}, "src"), ("Bash", {"command": "pwd"}, "/w"))
        doc["steps"].insert(2, {"step_id": 0, "source": "agent", "message": "Thinking."})
        for number, step in enumerate(doc["steps"], 1):
            step["step_id"] = number
        run = atif_to_run(doc, make_task())
        assert [step.tool_name for step in run.steps] == ["bash", "bash"]

    def test_a_closing_text_turn_is_read_as_the_agents_finish(self):
        doc = _foreign(("Bash", {"command": "pytest -q"}, "1 failed"))
        doc["steps"].append({"step_id": 3, "source": "agent", "message": "All done."})
        run = atif_to_run(doc, make_task(), reward=0.0)
        assert [step.tool_name for step in run.steps] == ["bash", "finish"]
        assert run.steps[-1].tool_args == {"summary": "All done."}
        # It stopped and said done with the tests still failing: premature success.
        assert FailureModeId.PREMATURE_SUCCESS in {hit.id for hit in run.failure_modes}

    def test_a_trajectory_that_ends_mid_action_has_no_finish(self):
        doc = _foreign(("Bash", {"command": "pytest -q"}, "1 failed"))
        run = atif_to_run(doc, make_task(), reward=0.0)
        assert [step.tool_name for step in run.steps] == ["bash"]


class TestValidation:
    def _valid(self) -> dict:
        return _foreign(("Bash", {"command": "ls"}, "src"))

    def test_steps_must_be_sequential_from_one(self):
        doc = self._valid()
        doc["steps"][1]["step_id"] = 5
        with pytest.raises(AtifError, match="sequential"):
            validate(doc)

    def test_tool_calls_are_only_valid_on_agent_steps(self):
        doc = self._valid()
        doc["steps"][0]["tool_calls"] = []
        with pytest.raises(AtifError, match="only valid on agent steps"):
            validate(doc)

    def test_an_observation_must_point_at_its_own_step(self):
        doc = self._valid()
        doc["steps"][1]["observation"]["results"][0]["source_call_id"] = "elsewhere"
        with pytest.raises(AtifError, match="not one of that step"):
            validate(doc)

    def test_undeclared_top_level_fields_are_refused(self):
        doc = copy.deepcopy(self._valid())
        doc["custom"] = {}
        with pytest.raises(AtifError, match="use extra"):
            validate(doc)

    def test_an_unknown_version_is_refused(self):
        doc = self._valid()
        doc["schema_version"] = "ATIF-v9"
        with pytest.raises(AtifError, match="schema_version"):
            validate(doc)
