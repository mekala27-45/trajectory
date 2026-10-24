"""Provider selection and the suite-level caps that stop a run before it starts."""

from __future__ import annotations

from pathlib import Path

import pytest

from trajectory_core.models import SandboxBackend
from trajectory_core.testing import make_config, make_playbook, make_task
from trajectory_runner.agent import CallBudget
from trajectory_runner.execute import ExecutionOptions, build_provider, plan, run_suite
from trajectory_runner.loader import LoadedTask
from trajectory_runner.providers import LiteLLMProvider, StubProvider


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
