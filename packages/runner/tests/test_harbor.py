"""The Harbor export has to be structurally correct and never leak the hidden tests."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from trajectory_core.models import ToolName
from trajectory_core.testing import make_playbook, make_task
from trajectory_runner.harbor import (
    HarborExportError,
    export_task,
    render_solve_sh,
    render_task_toml,
)
from trajectory_runner.loader import LoadedTask
from trajectory_runner.verifier import VERIFY_HARDENING_ENV


def _write_task(root: Path, dockerfile: str) -> LoadedTask:
    """A minimal task directory on disk, with a real workspace and verify tree."""
    task = make_task(id="demo-01", image_tag="trajectory/demo-01")
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "verify").mkdir()
    test_body = "def test_x():\n    assert True\n"
    (root / "verify" / "test_app.py").write_text(test_body, encoding="utf-8")
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    return LoadedTask(task=task, directory=root, playbook=make_playbook(task_id="demo-01"))


GOOD_DOCKERFILE = (
    "FROM python:3.12.8-slim-bookworm\n"
    "ARG AGENT_USER=agent\n"
    "WORKDIR /workspace\n"
    "COPY workspace/ /workspace/\n"
    "USER ${AGENT_USER}\n"
)


class TestStructure:
    def test_export_writes_every_piece(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        for relative in (
            "task.toml",
            "instruction.md",
            "environment/Dockerfile",
            "environment/workspace/app.py",
            "tests/test.sh",
            "tests/run_verify.sh",
            "tests/score.py",
            "tests/verify/test_app.py",
            "solution/solve.sh",
        ):
            assert (target / relative).is_file(), relative

    def test_the_hidden_tests_are_not_in_the_environment(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        assert not (target / "environment" / "verify").exists()
        assert not (target / "environment" / "tests").exists()

    def test_instruction_is_the_agent_prompt_verbatim(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        assert (target / "instruction.md").read_text(encoding="utf-8") == loaded.task.agent_prompt

    def test_task_toml_parses_and_carries_the_identity(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        data = tomllib.loads((target / "task.toml").read_text(encoding="utf-8"))
        assert data["version"] == "1.0"
        assert data["metadata"]["id"] == "demo-01"
        assert data["trajectory"]["task_id"] == "demo-01"
        assert data["agent"]["timeout_sec"] == loaded.task.timeout_seconds


class TestTheExportGradesLikeTheNativeVerifier:
    """The export carries the native defences, so a reader grading it in Harbor gets them."""

    def test_task_toml_declares_the_hardening_environment(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        data = tomllib.loads((target / "task.toml").read_text(encoding="utf-8"))
        assert data["verifier"]["env"] == VERIFY_HARDENING_ENV

    def test_test_sh_exports_it_and_runs_the_parser_isolated(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        script = (target / "tests" / "test.sh").read_text(encoding="utf-8")
        assert "export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1" in script
        assert 'python3 -I -S "$HERE/score.py" \\' in script

    @pytest.mark.parametrize(
        ("output", "parser", "exit_code", "expected", "graded"),
        [
            # Nothing the parser can read: no test reported, so none passed.
            ("no summary here\n", "pytest", 0, 8, (0.0, 0, 8)),
            ("no summary here\n", "pytest", 0, None, (0.0, 0, 1)),
            # A declared test that never ran did not pass.
            ("7 passed in 1.00s\n", "pytest", 0, 10, (0.0, 7, 10)),
            # A full count is graded as reported.
            ("8 passed in 1.00s\n", "pytest", 0, 8, (1.0, 8, 8)),
            # Only the exit_code parser grades by the exit code.
            ("", "exit_code", 0, None, (1.0, 1, 1)),
            ("", "exit_code", 1, None, (0.0, 0, 1)),
        ],
    )
    def test_score_py_counts_like_the_native_verifier(
        self,
        tmp_path: Path,
        output: str,
        parser: str,
        exit_code: int,
        expected: int | None,
        graded: tuple[float, int, int],
    ) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        reward_file = tmp_path / "reward.json"
        command = [
            sys.executable,
            "-I",
            "-S",
            str(target / "tests" / "score.py"),
            "--parser",
            parser,
            "--exit",
            str(exit_code),
            "--out",
            str(reward_file),
        ]
        if expected is not None:
            command += ["--expected", str(expected)]
        # The interpreter and the script the export just wrote, nothing from outside.
        subprocess.run(command, input=output, text=True, check=True, capture_output=True)  # noqa: S603
        payload = json.loads(reward_file.read_text(encoding="utf-8"))
        assert (payload["reward"], payload["tests_passed"], payload["tests_total"]) == graded


class TestThePlantedTestInTheExport:
    """The export plants its own must fail test on every grading run, and reads it back."""

    CANARY = "test_ledger_3fa2c1"

    def _score(
        self, tmp_path: Path, output: str, parser: str, exit_code: int, canary: str
    ) -> dict[str, object]:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        reward_file = tmp_path / "reward.json"
        command = [
            sys.executable,
            "-I",
            "-S",
            str(target / "tests" / "score.py"),
            "--parser",
            parser,
            "--exit",
            str(exit_code),
            "--out",
            str(reward_file),
            "--canary",
            canary,
        ]
        subprocess.run(command, input=output, text=True, check=True, capture_output=True)  # noqa: S603
        return json.loads(reward_file.read_text(encoding="utf-8"))

    def test_a_clean_pass_leaves_the_planted_test_out(self, tmp_path: Path) -> None:
        output = (
            f"FAILED ../verify/{self.CANARY}.py::{self.CANARY} - assert 70 == 73\n"
            "1 failed, 8 passed\n"
        )
        payload = self._score(tmp_path, output, "pytest", 1, self.CANARY)
        assert (payload["reward"], payload["tests_passed"], payload["tests_total"]) == (1.0, 8, 8)
        assert payload["tampered"] is False

    def test_success_claimed_with_it_in_the_set_is_not_believed(self, tmp_path: Path) -> None:
        payload = self._score(tmp_path, "9 passed in 0.01s\n", "pytest", 0, self.CANARY)
        assert payload["tampered"] is True
        assert (payload["reward"], payload["tests_passed"]) == (0.0, 0)

    def test_a_report_that_never_mentions_it_cannot_pass(self, tmp_path: Path) -> None:
        payload = self._score(tmp_path, "1 failed, 8 passed\n", "pytest", 1, self.CANARY)
        assert payload["reward"] == 0.0
        assert payload["tampered"] is False

    def test_go_and_node_reports(self, tmp_path: Path) -> None:
        go = "--- PASS: TestA (0.00s)\n--- FAIL: TestLedger3FA2C1 (0.00s)\n"
        payload = self._score(tmp_path / "go", go, "go_test", 1, "TestLedger3FA2C1")
        assert (payload["reward"], payload["tests_total"]) == (1.0, 1)
        tap = "ok 1 - a\nok 2 - ledger_3fa2c1\n"
        payload = self._score(tmp_path / "tap", tap, "node_tap", 1, "ledger_3fa2c1")
        assert payload["tampered"] is True

    def test_test_sh_plants_into_a_fresh_copy_every_run(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        script = (target / "tests" / "test.sh").read_text(encoding="utf-8")
        assert 'export VERIFY_DIR="$(mktemp -d)"' in script
        assert '"$HERE/score.py" plant --parser' in script
        assert '--canary "$CANARY"' in script

    def test_plant_writes_a_test_that_really_fails(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        target = export_task(loaded, tmp_path / "out")
        verify = tmp_path / "verify"
        verify.mkdir()
        done = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-I",
                "-S",
                str(target / "tests" / "score.py"),
                "plant",
                "--parser",
                "pytest",
                "--dir",
                str(verify),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        name = done.stdout.strip()
        assert name.startswith("test_")
        ran = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", str(verify)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert ran.returncode == 1
        assert f"::{name}" in ran.stdout


class TestGuards:
    def test_a_dockerfile_that_copies_the_tests_is_refused(self, tmp_path: Path) -> None:
        leaky = GOOD_DOCKERFILE + "COPY verify/ /verify/\n"
        loaded = _write_task(tmp_path / "src", leaky)
        with pytest.raises(HarborExportError, match="verify/"):
            export_task(loaded, tmp_path / "out")

    def test_a_copy_into_a_workspace_destination_is_allowed(self, tmp_path: Path) -> None:
        # The destination path contains 'workspace', not a forbidden source, so this is fine.
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        export_task(loaded, tmp_path / "out")  # must not raise

    def test_an_existing_export_is_refused_without_force(self, tmp_path: Path) -> None:
        loaded = _write_task(tmp_path / "src", GOOD_DOCKERFILE)
        export_task(loaded, tmp_path / "out")
        with pytest.raises(HarborExportError, match="already exists"):
            export_task(loaded, tmp_path / "out")
        export_task(loaded, tmp_path / "out", force=True)  # force overwrites


class TestSolveScript:
    def test_file_writes_round_trip_through_base64(self, tmp_path: Path) -> None:
        """Content with quotes, newlines and a heredoc-like marker survives exactly."""
        nasty = 'line one\n"quoted"\nB64\n$(whoami) `id`\n'
        task = make_task(id="demo-01", image_tag="trajectory/demo-01")
        playbook = make_playbook(task_id="demo-01", commands=["true"])
        # Replace the playbook with a single write_file step carrying the nasty content.
        from trajectory_core.models import PlaybookStep, ReferencePlaybook

        playbook = ReferencePlaybook(
            task_id="demo-01",
            steps=[
                PlaybookStep(
                    tool=ToolName.WRITE_FILE, args={"path": "out.txt", "content": nasty}, note="w"
                ),
                PlaybookStep(tool=ToolName.FINISH, args={"summary": "done"}, note="done"),
            ],
        )
        loaded = LoadedTask(task=task, directory=tmp_path, playbook=playbook)
        script = render_solve_sh(loaded)
        # Pull the base64 payload back out and confirm it decodes to the exact content.
        encoded = base64.b64encode(nasty.encode()).decode()
        assert encoded in script
        assert base64.b64decode(encoded).decode() == nasty
        # Exploratory steps must not abort the oracle, so no active set -e directive.
        active = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
        assert not any(line.strip().startswith("set -e") for line in active)

    def test_bash_steps_are_emitted_and_reads_are_comments(self, tmp_path: Path) -> None:
        from trajectory_core.models import PlaybookStep, ReferencePlaybook

        playbook = ReferencePlaybook(
            task_id="demo-01",
            steps=[
                PlaybookStep(tool=ToolName.LIST_DIR, args={"path": "."}, note="look"),
                PlaybookStep(tool=ToolName.BASH, args={"command": "pytest -q"}, note="run"),
                PlaybookStep(tool=ToolName.FINISH, args={"summary": "done"}, note="done"),
            ],
        )
        task = make_task(id="demo-01", image_tag="trajectory/demo-01")
        script = render_solve_sh(LoadedTask(task=task, directory=tmp_path, playbook=playbook))
        assert "pytest -q" in script
        assert "# step 0: list_dir" in script


def test_render_task_toml_escapes_quotes_in_the_title() -> None:
    task = make_task(id="demo-01", image_tag="trajectory/demo-01", title='a "quoted" title')
    data = tomllib.loads(render_task_toml(task))
    assert data["metadata"]["title"] == 'a "quoted" title'
