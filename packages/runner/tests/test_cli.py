"""The CLI is the surface people touch, so its contracts get tested.

What matters here is not the cosmetics: it is that exit codes are right, that the
unisolated backend cannot be selected by accident, and that the commands compose.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trajectory_core.models import Run
from trajectory_runner.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parents[3]


def invoke(*args: str, env: dict[str, str] | None = None):
    merged = {**os.environ, "NO_COLOR": "1", "COLUMNS": "200", **(env or {})}
    return runner.invoke(app, list(args), env=merged, catch_exceptions=False)


class TestLogging:
    def test_a_log_call_never_raises_when_the_stream_was_swapped(self):
        """structlog's own factory caches sys.stderr, which turns a warning into a crash."""
        import io
        import sys as sys_module

        import structlog

        from trajectory_runner.cli import configure_logging

        original = sys_module.stderr
        replacement = io.StringIO()
        try:
            sys_module.stderr = replacement
            configure_logging(verbose=True)
            replacement.close()
            structlog.get_logger("test").warning("this must not raise")
        finally:
            sys_module.stderr = original

    def test_log_output_goes_to_the_current_stream(self):
        import io
        import sys as sys_module

        import structlog

        from trajectory_runner.cli import configure_logging

        original = sys_module.stderr
        captured = io.StringIO()
        try:
            sys_module.stderr = captured
            configure_logging(verbose=True)
            structlog.get_logger("test").warning("a distinctive message")
        finally:
            sys_module.stderr = original
        assert "a distinctive message" in captured.getvalue()


class TestPlumbing:
    def test_version_reports_what_it_can_reach(self):
        result = invoke("version")
        assert result.exit_code == 0
        assert "trajectory" in result.output
        assert "docker:" in result.output

    def test_help_lists_every_command(self):
        result = invoke("--help")
        for command in ("run", "replay", "score", "judge", "report", "push", "diff", "tasks"):
            assert command in result.output

    def test_taxonomy_prints_all_ten_modes(self):
        result = invoke("taxonomy", "--format", "md")
        assert result.exit_code == 0
        for index in range(1, 11):
            assert f"F{index:02d}" in result.output

    def test_no_color_is_respected(self):
        result = invoke("taxonomy", "--format", "md")
        assert "\x1b[" not in result.output


class TestTasks:
    def test_list_shows_the_suite(self):
        result = invoke("tasks", "list", "--tasks-root", str(REPO / "tasks"))
        assert result.exit_code == 0
        assert "py-failing-suite-01" in result.output

    def test_list_filters_by_difficulty(self):
        result = invoke("tasks", "list", "--difficulty", "1", "--tasks-root", str(REPO / "tasks"))
        assert result.exit_code == 0
        assert "py-failing-suite-01" in result.output
        assert "git-surgery-01" not in result.output

    def test_list_exits_non_zero_when_nothing_matches(self):
        result = invoke("tasks", "list", "--language", "cobol", "--tasks-root", str(REPO / "tasks"))
        assert result.exit_code == 1

    def test_validate_passes_on_the_shipped_suite(self):
        """The suite in this repository has to validate, or CI is lying."""
        result = invoke("tasks", "validate", "--tasks-root", str(REPO / "tasks"))
        assert result.exit_code == 0
        assert "0 error(s)" in result.output

    def test_validate_exits_non_zero_on_a_broken_task(self, tmp_path: Path):
        broken = tmp_path / "core-12" / "broken-01"
        broken.mkdir(parents=True)
        (broken / "task.yaml").write_text(
            "title: Broken\ndescription: x\nlanguage: python\ndifficulty: 2\n"
            "image_tag: t/broken-01\nagent_prompt: x\nmax_steps: 10\ntimeout_seconds: 60\n"
            'verify_cmd: pytest "$VERIFY_DIR"\nreference_step_count: 2\n'
        )
        (broken / "reference").mkdir()
        (broken / "reference" / "playbook.yaml").write_text(
            "steps:\n  - tool: bash\n    args: {command: ls}\n  - tool: finish\n"
            "    args: {summary: done}\n"
        )
        result = invoke("tasks", "validate", "--tasks-root", str(tmp_path))
        assert result.exit_code == 1
        assert "error" in result.output

    def test_new_scaffolds_a_task_that_validates(self, tmp_path: Path):
        (tmp_path / "core-12").mkdir(parents=True)
        created = invoke("tasks", "new", "demo-scaffold-01", "--tasks-root", str(tmp_path))
        assert created.exit_code == 0
        assert (tmp_path / "core-12" / "demo-scaffold-01" / "task.yaml").is_file()
        assert (tmp_path / "core-12" / "demo-scaffold-01" / "reference" / "playbook.yaml").is_file()

    def test_new_refuses_to_overwrite(self, tmp_path: Path):
        (tmp_path / "core-12" / "taken-01").mkdir(parents=True)
        result = invoke("tasks", "new", "taken-01", "--tasks-root", str(tmp_path))
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_policies_are_labelled_as_scripted_rather_than_models(self):
        result = invoke("tasks", "policies")
        assert result.exit_code == 0
        assert "stub:methodical" in result.output
        assert "scripted agents, not models" in result.output


class TestBackendSelection:
    def test_the_unisolated_backend_cannot_be_selected_without_opting_in(self):
        """The whole point of the opt in is that it cannot happen by accident."""
        result = invoke(
            "run",
            "--task",
            "py-failing-suite-01",
            "--backend",
            "local",
            "--tasks-root",
            str(REPO / "tasks"),
            env={"TRAJECTORY_ALLOW_LOCAL_SANDBOX": ""},
        )
        assert result.exit_code == 1
        assert "not isolated and is off by default" in result.output

    def test_asking_for_docker_when_there_is_none_is_an_error_not_a_fallback(self):
        from trajectory_runner.sandbox import docker_available

        if docker_available():
            pytest.skip("a Docker daemon is reachable here")
        result = invoke(
            "run",
            "--task",
            "py-failing-suite-01",
            "--backend",
            "docker",
            "--tasks-root",
            str(REPO / "tasks"),
            env={"TRAJECTORY_ALLOW_LOCAL_SANDBOX": "1"},
        )
        assert result.exit_code == 1
        assert "no Docker daemon" in result.output


class TestRunAndInspect:
    """End to end through the CLI with no network, which is what CI runs."""

    def _run_once(
        self, tmp_path: Path, policy: str = "stub:methodical", task: str = "py-failing-suite-01"
    ):
        out = tmp_path / "runs"
        result = invoke(
            "run",
            "--task",
            task,
            "--model",
            policy,
            "--backend",
            "local",
            "--out",
            str(out),
            "--tasks-root",
            str(REPO / "tasks"),
            env={"TRAJECTORY_ALLOW_LOCAL_SANDBOX": "1"},
        )
        return result, out

    @pytest.mark.slow
    def test_a_reference_policy_solves_and_exits_zero(self, tmp_path: Path):
        result, out = self._run_once(tmp_path)
        assert result.exit_code == 0, result.output
        assert "1 of 1 solved" in result.output
        records = list((out / "runs").glob("*.json"))
        assert len(records) == 1
        record = Run.model_validate_json(records[0].read_text())
        assert record.solved is True
        assert record.score is not None
        assert record.score.step_efficiency == 1.0
        assert record.runner_fingerprint.sandbox_backend.value == "local"

    @pytest.mark.slow
    def test_a_run_that_solves_nothing_exits_non_zero(self, tmp_path: Path):
        """So a smoke check in CI is one command with no parsing."""
        result, _ = self._run_once(tmp_path, policy="stub:hasty")
        assert result.exit_code == 1
        assert "0 of 1 solved" in result.output

    @pytest.mark.slow
    def test_a_bundle_is_written_with_a_verifiable_content_hash(self, tmp_path: Path):
        from trajectory_runner.store import bundle_content_hash, read_bundle

        _, out = self._run_once(tmp_path)
        bundle = read_bundle(out)
        assert bundle.manifest.run_count == 1
        assert bundle.manifest.content_sha256 == bundle_content_hash(bundle.runs)

    @pytest.mark.slow
    def test_replay_renders_the_trajectory_and_its_failure_modes(self, tmp_path: Path):
        _, out = self._run_once(tmp_path, policy="stub:sloppy")
        record = Run.model_validate_json(next((out / "runs").glob("*.json")).read_text())
        result = invoke("replay", str(next((out / "runs").glob("*.json"))))
        assert result.exit_code == 0
        assert record.task_id in result.output
        assert "F01" in result.output

    @pytest.mark.slow
    def test_report_renders_every_format(self, tmp_path: Path):
        _, out = self._run_once(tmp_path)
        for fmt in ("md", "json", "csv"):
            result = invoke("report", str(out), "--format", fmt)
            assert result.exit_code == 0, fmt
        payload = json.loads(invoke("report", str(out), "--format", "json").output)
        assert payload["leaderboard"][0]["model"] == "stub:methodical"

    def test_report_rejects_an_unknown_format(self, tmp_path: Path):
        (tmp_path / "runs").mkdir(parents=True)
        result = invoke("report", str(tmp_path), "--format", "xml")
        assert result.exit_code == 1

    @pytest.mark.slow
    def test_score_recomputes_without_rerunning_anything(self, tmp_path: Path):
        _, out = self._run_once(tmp_path)
        result = invoke("score", str(out), "--tasks-root", str(REPO / "tasks"))
        assert result.exit_code == 0
        assert "rescored 1 run" in result.output

    @pytest.mark.slow
    def test_diff_finds_the_divergence_between_two_policies(self, tmp_path: Path):
        _, left = self._run_once(tmp_path / "a", policy="stub:methodical")
        _, right = self._run_once(tmp_path / "b", policy="stub:thrasher")
        result = invoke(
            "diff",
            str(next((left / "runs").glob("*.json"))),
            str(next((right / "runs").glob("*.json"))),
        )
        assert result.exit_code == 0
        assert "first divergence at step" in result.output

    @pytest.mark.slow
    def test_push_dry_run_warns_about_local_runs(self, tmp_path: Path):
        _, out = self._run_once(tmp_path)
        result = invoke("push", str(out), "--api-url", "http://localhost:8000", "--dry-run")
        assert result.exit_code == 0
        assert "never merged with container runs" in result.output

    def test_push_without_a_url_is_an_error(self, tmp_path: Path):
        (tmp_path / "runs").mkdir(parents=True)
        result = invoke("push", str(tmp_path), env={"TRAJECTORY_API_URL": ""})
        assert result.exit_code == 1


class TestRunArgumentChecks:
    def test_exactly_one_of_task_or_suite_is_required(self):
        both = invoke("run", "--task", "x", "--suite", "core-12")
        neither = invoke("run")
        assert both.exit_code == 1
        assert neither.exit_code == 1
        assert "exactly one of --task or --suite" in both.output

    def test_an_unknown_task_suggests_near_misses(self):
        result = invoke(
            "run",
            "--task",
            "py-failing",
            "--backend",
            "local",
            "--tasks-root",
            str(REPO / "tasks"),
            env={"TRAJECTORY_ALLOW_LOCAL_SANDBOX": "1"},
        )
        assert result.exit_code == 1
        assert "Did you mean" in result.output
        assert "py-failing-suite-01" in result.output
