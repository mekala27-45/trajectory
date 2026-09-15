"""Tests for the reference check verdict and how a failure is reported.

`trajectory tasks verify-references` is the gate that decides whether a task is a valid
task. It had no tests, which is how it came to report `0/1` and the phrase "the reference
solution does not solve it" for a task whose hidden suite never compiled, discarding the
compiler output it was holding at the time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from trajectory_runner.cli import _print_reference_failure
from trajectory_runner.execute import ReferenceCheck


def check(**overrides: object) -> ReferenceCheck:
    """A passing check, with the fields under test overridden."""
    defaults: dict[str, object] = {
        "task_id": "py-perf-01",
        "unfixed_passed": 2,
        "unfixed_total": 9,
        "reference_passed": 9,
        "reference_total": 9,
        "steps": 11,
        "status": "completed",
        "error": None,
    }
    return ReferenceCheck(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestTheVerdict:
    def test_a_healthy_task_passes(self) -> None:
        c = check()
        assert (c.starts_broken, c.reference_solves, c.ok) == (True, True, True)
        assert c.reason == "pass"

    def test_a_task_that_starts_green_measures_nothing(self) -> None:
        c = check(unfixed_passed=9, unfixed_total=9)
        assert c.starts_broken is False
        assert c.ok is False
        assert "measures nothing" in c.reason

    def test_a_task_with_no_discovered_tests_counts_as_broken_at_the_start(self) -> None:
        # Zero of zero is not "already passing", it is "nothing ran".
        assert check(unfixed_passed=0, unfixed_total=0).starts_broken is True

    def test_a_rotted_reference_solution_is_named_as_such(self) -> None:
        c = check(reference_passed=7, reference_total=9)
        assert c.reference_solves is False
        assert c.reason == "the reference solution does not solve it"

    def test_a_suite_that_never_ran_is_distinguished_from_one_that_failed(self) -> None:
        """The distinction that cost an hour on go-race-01.

        A hidden suite that fails to compile and a hidden suite where every test failed
        both arrive as 0 of 1, because the parser finds no result lines and the exit code
        fallback reports one notional test. They are completely different problems.
        """
        compiled_and_failed = check(reference_passed=0, reference_total=9)
        never_ran = check(reference_passed=0, reference_total=1, reference_parse_ok=False)
        assert compiled_and_failed.reason == "the reference solution does not solve it"
        assert never_ran.reason == "the hidden tests never ran, so nothing was measured"
        assert compiled_and_failed.reason != never_ran.reason

    def test_an_incomplete_run_is_not_ok_even_with_a_full_score(self) -> None:
        assert check(status="timeout").ok is False

    def test_the_captured_output_defaults_to_empty_rather_than_none(self) -> None:
        c = check()
        assert (c.unfixed_output, c.reference_output) == ("", "")
        assert (c.unfixed_parse_ok, c.reference_parse_ok) == (True, True)


class TestTheFailureReport:
    def _render(self, c: ReferenceCheck, run_dir: Path) -> str:
        console = Console(record=True, width=100, no_color=True)
        import trajectory_runner.cli as cli

        original, cli.console = cli.console, console
        try:
            _print_reference_failure(c, run_dir)
        finally:
            cli.console = original
        return console.export_text()

    def test_it_prints_the_hidden_test_output(self, tmp_path: Path) -> None:
        c = check(
            reference_passed=0,
            reference_total=9,
            reference_output="agg/aggregator.go:69: undefined: sync.RWMutex",
        )
        out = self._render(c, tmp_path)
        assert "undefined: sync.RWMutex" in out

    def test_it_names_the_task_and_the_verdict(self, tmp_path: Path) -> None:
        out = self._render(check(reference_passed=0, reference_total=9), tmp_path)
        assert "py-perf-01" in out
        assert "does not solve it" in out

    def test_it_explains_an_exit_code_fallback(self, tmp_path: Path) -> None:
        c = check(reference_passed=0, reference_total=1, reference_parse_ok=False)
        out = self._render(c, tmp_path)
        assert "no test result lines" in out
        assert "never reached a test" in out

    def test_it_falls_back_to_the_unfixed_output_when_the_run_produced_none(
        self, tmp_path: Path
    ) -> None:
        c = check(
            unfixed_passed=9,
            unfixed_total=9,
            unfixed_output="collected 9 items, 9 passed",
            reference_output="",
        )
        out = self._render(c, tmp_path)
        assert "9 passed" in out

    def test_silence_is_reported_as_silence_rather_than_an_empty_panel(
        self, tmp_path: Path
    ) -> None:
        c = check(reference_passed=0, reference_total=9)
        out = self._render(c, tmp_path)
        assert "no output at all" in out

    def test_it_includes_the_run_error_when_there_was_one(self, tmp_path: Path) -> None:
        c = check(status="error", error="SandboxError: setup_cmd exited 2", reference_total=0)
        assert "setup_cmd exited 2" in self._render(c, tmp_path)

    def test_it_points_at_the_full_run_record(self, tmp_path: Path) -> None:
        out = self._render(check(reference_passed=0, reference_total=9), tmp_path)
        assert "runs" in out

    @pytest.mark.parametrize("length", [10, 2500, 40_000])
    def test_a_long_output_is_truncated_rather_than_flooding_the_terminal(
        self, tmp_path: Path, length: int
    ) -> None:
        c = check(reference_passed=0, reference_total=9, reference_output="x" * length)
        out = self._render(c, tmp_path)
        assert len(out) < 6000
