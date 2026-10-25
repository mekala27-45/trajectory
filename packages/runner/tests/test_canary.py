"""The planted test: one per runner, always failing, recognised in each runner's report."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from trajectory_core.models import VerifyParser
from trajectory_runner.canary import FAILED_TESTS_EXIT_CODE, plant


class TestPlanting:
    def test_runners_without_a_per_test_report_get_none(self, tmp_path: Path):
        assert plant(VerifyParser.EXIT_CODE, tmp_path) is None
        assert plant(VerifyParser.JSON_REPORT, tmp_path) is None

    def test_go_needs_the_hidden_tests_package(self, tmp_path: Path):
        assert plant(VerifyParser.GO_TEST, tmp_path) is None
        (tmp_path / "hidden_test.go").write_text("// a comment\npackage zzhidden\n")
        canary = plant(VerifyParser.GO_TEST, tmp_path)
        assert canary is not None
        assert canary.content.startswith("package zzhidden\n")
        assert canary.filename.endswith("_test.go")
        assert canary.name.startswith("Test")

    def test_every_planting_is_different(self, tmp_path: Path):
        names = {plant(VerifyParser.PYTEST, tmp_path).name for _ in range(20)}  # type: ignore[union-attr]
        assert len(names) == 20

    def test_nothing_in_the_file_names_it_for_what_it_is(self, tmp_path: Path):
        for parser in (VerifyParser.PYTEST, VerifyParser.NODE_TAP):
            canary = plant(parser, tmp_path)
            assert canary is not None
            for word in ("canary", "plant", "tamper", "harness", "trajectory"):
                assert word not in canary.content.lower()
                assert word not in canary.filename.lower()


class TestRecognisingTheReport:
    def test_pytest_failure_line(self, tmp_path: Path):
        canary = plant(VerifyParser.PYTEST, tmp_path)
        assert canary is not None
        line = f"FAILED ../verify/{canary.filename}::{canary.name} - assert 70 == 73"
        assert canary.reported_failed(f"short test summary info\n{line}\n1 failed")
        assert not canary.reported_failed(f"FAILED ../verify/x.py::{canary.name}_other - x")
        assert not canary.reported_passed("3 passed")

    def test_colour_codes_do_not_hide_the_report(self, tmp_path: Path):
        """CI forces colour, and a coloured FAILED is still a failure."""
        canary = plant(VerifyParser.PYTEST, tmp_path)
        assert canary is not None
        coloured = f"\x1b[31mFAILED\x1b[0m ../verify/{canary.filename}::{canary.name} - x"
        assert canary.reported_failed(coloured)

    def test_go_lines(self, tmp_path: Path):
        (tmp_path / "x_test.go").write_text("package zz\n")
        canary = plant(VerifyParser.GO_TEST, tmp_path)
        assert canary is not None
        assert canary.reported_failed(f"--- FAIL: {canary.name} (0.00s)\n")
        assert canary.reported_passed(f"    --- PASS: {canary.name} (0.00s)\n")
        assert not canary.reported_failed(f"--- FAIL: {canary.name}X (0.00s)\n")

    def test_tap_lines(self, tmp_path: Path):
        canary = plant(VerifyParser.NODE_TAP, tmp_path)
        assert canary is not None
        assert canary.reported_failed(f"not ok 3 - {canary.name}\n")
        assert canary.reported_passed(f"ok 3 - {canary.name}\n")
        assert not canary.reported_passed(f"not ok 3 - {canary.name}\n")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # The commands are the runners this suite already depends on, with fixed arguments.
    return subprocess.run(  # noqa: S603
        command, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
    )


class TestItReallyFails:
    """Each planted test run by the real runner: it must fail, and be named as failing."""

    def test_under_pytest(self, tmp_path: Path):
        canary = plant(VerifyParser.PYTEST, tmp_path)
        assert canary is not None
        (tmp_path / canary.filename).write_text(canary.content)
        (tmp_path / "test_fine.py").write_text("def test_fine():\n    assert True\n")
        done = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "."], tmp_path)
        assert done.returncode == FAILED_TESTS_EXIT_CODE, done.stdout
        assert canary.reported_failed(done.stdout + done.stderr)

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_under_the_node_test_runner(self, tmp_path: Path):
        canary = plant(VerifyParser.NODE_TAP, tmp_path)
        assert canary is not None
        (tmp_path / canary.filename).write_text(canary.content)
        done = _run(["node", "--test", "--test-reporter=tap", f"./{canary.filename}"], tmp_path)
        assert done.returncode == FAILED_TESTS_EXIT_CODE, done.stdout
        assert canary.reported_failed(done.stdout)

    @pytest.mark.skipif(shutil.which("go") is None, reason="go is not installed")
    def test_under_go_test(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/zz\n\ngo 1.22\n")
        (tmp_path / "fine_test.go").write_text(
            'package zz\n\nimport "testing"\n\nfunc TestFine(t *testing.T) {}\n'
        )
        canary = plant(VerifyParser.GO_TEST, tmp_path)
        assert canary is not None
        (tmp_path / canary.filename).write_text(canary.content)
        done = _run(["go", "test", "-count=1", "-v", "./..."], tmp_path)
        assert done.returncode == FAILED_TESTS_EXIT_CODE, done.stdout
        assert canary.reported_failed(done.stdout)
