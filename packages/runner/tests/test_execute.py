"""Provider selection and the suite-level caps that stop a run before it starts."""

from __future__ import annotations

from pathlib import Path

import pytest

from trajectory_core.models import PlaybookStep, ReferencePlaybook, SandboxBackend, ToolName
from trajectory_core.testing import make_config, make_playbook, make_task
from trajectory_runner.agent import CallBudget
from trajectory_runner.execute import (
    ExecutionOptions,
    build_provider,
    config_for,
    execute,
    plan,
    run_suite,
)
from trajectory_runner.loader import LoadedTask
from trajectory_runner.providers import LiteLLMProvider, StubProvider

FIXED_SOURCE = "def add(a, b):\n    return a + b\n"


def _solving_playbook(task_id: str) -> ReferencePlaybook:
    """A playbook that writes the fix the sample task needs, then finishes."""
    return ReferencePlaybook(
        task_id=task_id,
        steps=[
            PlaybookStep(
                tool=ToolName.WRITE_FILE,
                args={"path": "src/app.py", "content": FIXED_SOURCE},
                note="write the fix",
            ),
            PlaybookStep(tool=ToolName.FINISH, args={"summary": "fixed"}, note="done"),
        ],
    )


def _loaded(task_id: str = "demo-01") -> LoadedTask:
    return LoadedTask(
        task=make_task(id=task_id, image_tag=f"trajectory/{task_id}"),
        directory=Path("/nonexistent") / task_id,
        playbook=make_playbook(task_id=task_id),
    )


class TestBuildProvider:
    def test_a_stub_model_gives_the_offline_policy(self) -> None:
        provider = build_provider(make_config(model="stub:methodical"), _loaded())
        assert isinstance(provider, StubProvider)

    def test_nvidia_reads_its_optional_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_NIM_API_BASE", "https://example.test/v1")
        provider = build_provider(
            make_config(model="nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b"), _loaded()
        )
        assert isinstance(provider, LiteLLMProvider)
        assert provider.api_base == "https://example.test/v1"

    def test_ollama_reads_its_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
        provider = build_provider(make_config(model="ollama_chat/qwen2.5-coder:7b"), _loaded())
        assert isinstance(provider, LiteLLMProvider)
        assert provider.api_base == "http://127.0.0.1:11434"

    def test_a_plain_model_has_no_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        provider = build_provider(make_config(model="anthropic/claude-sonnet-4-5"), _loaded())
        assert isinstance(provider, LiteLLMProvider)
        assert provider.api_base is None


class TestSuiteCaps:
    def _jobs(self):
        tasks = [_loaded("alpha-01"), _loaded("beta-01")]
        return plan(tasks, model="stub:methodical", seeds=[0, 1], backend=SandboxBackend.LOCAL)

    def test_an_exhausted_call_ceiling_skips_every_job_serially(self, tmp_path: Path) -> None:
        options = ExecutionOptions(run_dir=tmp_path, call_budget=CallBudget(0))
        result = run_suite(self._jobs(), options, parallel=1)
        assert result.runs == []
        assert len(result.skipped) == 4
        assert all(reason == "call ceiling reached" for _, reason in result.skipped)

    def test_an_exhausted_call_ceiling_skips_every_job_in_parallel(self, tmp_path: Path) -> None:
        options = ExecutionOptions(run_dir=tmp_path, call_budget=CallBudget(0))
        result = run_suite(self._jobs(), options, parallel=2)
        assert result.runs == []
        assert len(result.skipped) == 4


class TestEndToEndOnLocal:
    def test_the_methodical_stub_solves_the_sample_task(
        self, task_dir, sample_task, allow_local, tmp_path
    ):
        loaded = LoadedTask(
            task=sample_task, directory=task_dir, playbook=_solving_playbook(sample_task.id)
        )
        run = execute(
            loaded,
            config_for(sample_task, model="stub:methodical", backend=SandboxBackend.LOCAL),
            ExecutionOptions(run_dir=tmp_path, allow_local_override=True),
        )
        assert run.solved
        assert run.verification is not None
        assert run.verification.tests_passed == run.verification.tests_total
        assert run.score is not None

    def test_a_suite_runs_two_stub_jobs_in_parallel(
        self, task_dir, sample_task, allow_local, tmp_path
    ):
        loaded = LoadedTask(
            task=sample_task, directory=task_dir, playbook=_solving_playbook(sample_task.id)
        )
        jobs = plan([loaded], model="stub:methodical", seeds=[0, 1], backend=SandboxBackend.LOCAL)
        options = ExecutionOptions(run_dir=tmp_path, allow_local_override=True)
        result = run_suite(jobs, options, parallel=2)
        assert len(result.runs) == 2
        assert result.solved == 2
        assert result.wall_clock_s >= 0


class _RacedBudget(CallBudget):
    """Reports headroom when a job starts, then refuses its first call.

    That is the race a thread pool produces at the ceiling, made deterministic.
    """

    def exhausted(self) -> bool:
        return False

    def take(self) -> bool:
        return False


class TestCapsAtTheBoundary:
    def _loaded(self, task_dir, sample_task):
        return LoadedTask(
            task=sample_task, directory=task_dir, playbook=_solving_playbook(sample_task.id)
        )

    def test_jobs_after_the_ceiling_are_skipped_not_recorded(
        self, task_dir, sample_task, allow_local, tmp_path
    ):
        """Each job needs two calls. Three are allowed, across four jobs."""
        loaded = self._loaded(task_dir, sample_task)
        jobs = plan(
            [loaded], model="stub:methodical", seeds=[0, 1, 2, 3], backend=SandboxBackend.LOCAL
        )
        options = ExecutionOptions(
            run_dir=tmp_path, allow_local_override=True, call_budget=CallBudget(3)
        )
        result = run_suite(jobs, options, parallel=1)
        assert len(result.runs) == 2
        assert result.runs[0].status.value == "completed"
        # Cut part way: its one call wrote the fix before the ceiling refused finish, so
        # the hidden tests pass. A cap stops the agent; it does not erase work done.
        assert result.runs[1].status.value == "budget_exceeded"
        assert len(result.runs[1].steps) == 1
        assert [reason for _, reason in result.skipped] == ["call ceiling reached"] * 2

    def test_a_run_refused_on_its_first_call_is_dropped_with_its_file(
        self, task_dir, sample_task, allow_local, tmp_path
    ):
        loaded = self._loaded(task_dir, sample_task)
        jobs = plan([loaded], model="stub:methodical", seeds=[0], backend=SandboxBackend.LOCAL)
        options = ExecutionOptions(
            run_dir=tmp_path, allow_local_override=True, call_budget=_RacedBudget(10)
        )
        result = run_suite(jobs, options, parallel=1)
        assert result.runs == []
        assert [reason for _, reason in result.skipped] == [
            "budget reached before the first model call"
        ]
        assert not list((tmp_path / "runs").glob("*.json"))


class TestDeclaredTestCount:
    def test_a_short_count_fails_the_reference_check_with_the_numbers(self):
        from trajectory_runner.execute import ReferenceCheck

        check = ReferenceCheck(
            task_id="t",
            unfixed_passed=0,
            unfixed_total=10,
            reference_passed=10,
            reference_total=10,
            steps=4,
            status="completed",
            error=None,
            expected_tests=8,
        )
        assert not check.ok
        assert check.reason == "declares 8 hidden tests but the reference ran 10"
