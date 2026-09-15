"""Partial credit is more informative than a pass rate, so the parsers have to be right."""

from __future__ import annotations

import pytest

from trajectory_core.models import VerifyParser
from trajectory_core.testing import make_task
from trajectory_runner.sandbox import LocalSandbox
from trajectory_runner.verifier import (
    VerificationError,
    parse_go_test,
    parse_json_report,
    parse_output,
    parse_pytest,
    parse_tap,
    verify,
)


class TestPytestParser:
    def test_all_green(self):
        assert parse_pytest("...\n4 passed in 0.12s\n") == (4, 4)

    def test_mixed(self):
        assert parse_pytest("=== 2 failed, 5 passed in 1.20s ===") == (5, 7)

    def test_counts_errors_toward_the_total(self):
        assert parse_pytest("=== 1 error, 3 passed in 0.4s ===") == (3, 4)

    def test_skipped_tests_are_excluded_from_the_total(self):
        """Otherwise a task that skips on this platform scores credit for doing nothing."""
        assert parse_pytest("=== 3 passed, 7 skipped in 0.4s ===") == (3, 3)

    def test_xfail_is_counted_as_a_failure(self):
        assert parse_pytest("=== 2 passed, 1 xfailed in 0.1s ===") == (2, 3)

    def test_xpass_counts_as_a_pass(self):
        assert parse_pytest("=== 2 passed, 1 xpassed in 0.1s ===") == (3, 3)

    def test_no_summary_returns_nothing(self):
        assert parse_pytest("ImportError: cannot import name 'app'") is None

    def test_collection_error_with_no_tests_returns_nothing(self):
        assert (
            parse_pytest("!!! Interrupted: 1 error during collection !!!\n1 error in 0.1s") is None
        )


class TestGoTestParser:
    def test_counts_pass_and_fail_lines(self):
        output = "--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.01s)\n--- PASS: TestC (0.00s)\nFAIL\n"
        assert parse_go_test(output) == (2, 3)

    def test_indented_subtests_are_counted(self):
        assert parse_go_test("    --- PASS: TestA/sub (0.00s)\n") == (1, 1)

    def test_no_tests_returns_nothing(self):
        assert parse_go_test("build failed\n") is None

    def test_only_skipped_reports_zero_of_zero(self):
        assert parse_go_test("--- SKIP: TestA (0.00s)\n") == (0, 0)


class TestTapParser:
    def test_counts_ok_and_not_ok(self):
        assert parse_tap("ok 1 - adds\nnot ok 2 - subtracts\nok 3 - multiplies\n") == (2, 3)

    def test_all_green(self):
        assert parse_tap("ok 1 - a\nok 2 - b\n") == (2, 2)

    def test_no_assertions_returns_nothing(self):
        assert parse_tap("TAP version 13\n1..0\n") is None


class TestJsonReportParser:
    def test_reads_the_last_json_object(self):
        output = 'noise\n{"passed": 1, "total": 9}\n{"passed": 3, "total": 4}\n'
        assert parse_json_report(output) == (3, 4)

    def test_ignores_json_without_the_expected_keys(self):
        assert parse_json_report('{"status": "ok"}\n') is None

    def test_ignores_malformed_json(self):
        assert parse_json_report('{"passed": 1, "total":\n') is None

    def test_no_json_returns_nothing(self):
        assert parse_json_report("all good\n") is None


def test_exit_code_parser_defers_to_the_exit_code():
    assert parse_output(VerifyParser.EXIT_CODE, "anything at all") is None


@pytest.mark.local_verify
class TestVerifyAgainstASandbox:
    def _task(self, **kwargs):
        defaults = {
            "id": "sample-task-01",
            "image_tag": "trajectory/sample-task-01",
            "verify_cmd": 'python -m pytest -q "$VERIFY_DIR"',
        }
        defaults.update(kwargs)
        return make_task(**defaults)

    def test_a_broken_workspace_fails_with_partial_credit(self, task_dir, allow_local):
        task = self._task()
        with LocalSandbox(task, task_dir) as sandbox:
            result = verify(task, sandbox)
        assert result.passed is False
        assert result.tests_total == 2
        assert result.tests_passed == 0
        assert result.partial_credit == 0.0
        assert result.parse_ok is True

    def test_a_fixed_workspace_passes(self, task_dir, allow_local):
        task = self._task()
        with LocalSandbox(task, task_dir) as sandbox:
            sandbox.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
            result = verify(task, sandbox)
        assert result.passed is True
        assert (result.tests_passed, result.tests_total) == (2, 2)
        assert result.partial_credit == 1.0

    def test_partial_progress_is_measured(self, task_dir, allow_local):
        """Four failing tests down to one is progress, and a pass rate cannot see it."""
        task = self._task()
        (task_dir / "verify" / "test_app.py").write_text(
            "import sys\n\n"
            "sys.path.insert(0, 'src')\n"
            "from app import add\n\n\n"
            "def test_zero():\n    assert add(0, 0) == 0\n\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n"
        )
        with LocalSandbox(task, task_dir) as sandbox:
            result = verify(task, sandbox)
        assert result.passed is False
        assert (result.tests_passed, result.tests_total) == (1, 2)
        assert result.partial_credit == 0.5

    def test_hidden_tests_are_installed_by_verification_not_before(self, task_dir, allow_local):
        task = self._task()
        with LocalSandbox(task, task_dir) as sandbox:
            assert sandbox.verify_present() is False
            verify(task, sandbox)
            assert sandbox.verify_present() is True

    def test_the_exit_code_overrules_a_parser_that_missed_something(self, task_dir, allow_local):
        """A green count with a red exit code means the parser missed a collection error."""
        task = self._task(verify_cmd='python -m pytest -q "$VERIFY_DIR"; echo "2 passed"; exit 1')
        with LocalSandbox(task, task_dir) as sandbox:
            sandbox.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
            result = verify(task, sandbox)
        assert result.passed is False

    def test_unparseable_output_falls_back_to_the_exit_code(self, task_dir, allow_local):
        task = self._task(verify_cmd="echo 'totally unparseable'; exit 0")
        with LocalSandbox(task, task_dir) as sandbox:
            result = verify(task, sandbox)
        assert result.passed is True
        assert result.parse_ok is False
        assert (result.tests_passed, result.tests_total) == (1, 1)

    def test_exit_code_tasks_report_parse_ok(self, task_dir, allow_local):
        task = self._task(verify_cmd="test -f src/app.py", verify_parser=VerifyParser.EXIT_CODE)
        with LocalSandbox(task, task_dir) as sandbox:
            result = verify(task, sandbox)
        assert result.passed is True
        assert result.parse_ok is True

    def test_stderr_tail_is_kept_for_triage(self, task_dir, allow_local):
        task = self._task(verify_cmd="echo 'boom' >&2; exit 1")
        with LocalSandbox(task, task_dir) as sandbox:
            result = verify(task, sandbox)
        assert "boom" in result.stderr_tail

    def test_a_missing_verify_directory_raises_a_clear_error(
        self, sample_task, tmp_path, allow_local
    ):
        (tmp_path / "workspace").mkdir()
        with (
            LocalSandbox(sample_task, tmp_path) as sandbox,
            pytest.raises(VerificationError, match="installing the hidden tests"),
        ):
            verify(sample_task, sandbox)
