"""Post-hoc verification against the hidden tests.

Verification happens after the agent has stopped, never during. The hidden tests are
copied into the sandbox at that point and not before, which is the property that keeps the
benchmark meaningful.

Output is parsed into a passed and total count rather than reduced to a boolean, because
partial credit is far more informative than a pass rate: an agent that took four failing
tests down to one did something, and a benchmark that reports both as "failed" throws that
away. When the output cannot be parsed the run falls back to the exit code and says so
through `parse_ok`, rather than inventing a count.
"""

from __future__ import annotations

import json
import re
import time

import structlog

from trajectory_core.models import Task, Verification, VerifyParser
from trajectory_runner.canary import FAILED_TESTS_EXIT_CODE, Canary, plant
from trajectory_runner.sandbox import ExecResult, Sandbox

log = structlog.get_logger(__name__)

# Combined, not stderr. pytest, `go test` and every other runner in the suite print
# their results and their failure detail on stdout, so a stderr only tail captured
# nothing on all 180 recorded runs, including the 25 that failed.
OUTPUT_TAIL_BYTES = 4000

# Environment the verification command runs under, over and above the task's own. These
# close the cheapest way an agent can make a grader lie: the interpreter's implicit load
# paths. PYTHONSAFEPATH drops the current directory from the front of sys.path, so a file
# the agent wrote into its workspace cannot shadow a module the hidden tests import by
# simply sharing its name. PYTHONNOUSERSITE ignores the user site directory, so a planted
# usercustomize or a .pth file there does not run at interpreter startup. Neither touches
# the paths a task legitimately uses: every task in the suite imports its solution through
# an explicit sys.path entry or runs it as a subprocess, never through the implicit one,
# so this is pure hardening with no change to what passes. It is set for every verifier,
# not only the Python ones, because it is inert for go and node.
VERIFY_HARDENING_ENV = {"PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1"}

_PYTEST_SUMMARY = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b")
_GO_PASS = re.compile(r"^\s*--- PASS:", re.MULTILINE)
_GO_FAIL = re.compile(r"^\s*--- FAIL:", re.MULTILINE)
_GO_SKIP = re.compile(r"^\s*--- SKIP:", re.MULTILINE)
_TAP_OK = re.compile(r"^\s*ok\s+\d+", re.MULTILINE)
_TAP_NOT_OK = re.compile(r"^\s*not ok\s+\d+", re.MULTILINE)


class VerificationError(RuntimeError):
    """Raised when verification could not be executed at all."""


def parse_pytest(output: str) -> tuple[int, int] | None:
    """Read a pytest summary line.

    Counts passed, failed, errored and unexpectedly passing tests toward the total.
    Skipped tests are excluded on purpose: a task whose hidden tests skip on this platform
    would otherwise score partial credit for doing nothing.

    Args:
        output: Combined pytest output.

    Returns:
        Passed and total counts, or None when no summary could be found.
    """
    counts: dict[str, int] = {}
    for match in _PYTEST_SUMMARY.finditer(output):
        key = match.group(2).rstrip("s")
        counts[key] = counts.get(key, 0) + int(match.group(1))
    if not counts:
        return None
    passed = counts.get("passed", 0) + counts.get("xpassed", 0)
    failed = counts.get("failed", 0) + counts.get("xfailed", 0)
    errors = counts.get("error", 0)

    if passed == 0 and failed == 0 and errors:
        # A collection error means the runner never discovered the tests, so the number of
        # tests is unknown. Reporting "0 of 2" would invent a denominator. Returning None
        # falls back to the exit code with parse_ok cleared, which says so honestly.
        return None

    total = passed + failed + errors
    if total == 0:
        return None
    return passed, total


def parse_go_test(output: str) -> tuple[int, int] | None:
    """Count `--- PASS` and `--- FAIL` lines from `go test -v`."""
    passed = len(_GO_PASS.findall(output))
    failed = len(_GO_FAIL.findall(output))
    skipped = len(_GO_SKIP.findall(output))
    total = passed + failed
    if total == 0:
        return None if skipped == 0 else (0, 0)
    return passed, total


def parse_tap(output: str) -> tuple[int, int] | None:
    """Count TAP `ok` and `not ok` lines, as emitted by the Node test runner."""
    failed = len(_TAP_NOT_OK.findall(output))
    passed = len(_TAP_OK.findall(output))
    total = passed + failed
    if total <= 0:
        return None
    return passed, total


def parse_json_report(output: str) -> tuple[int, int] | None:
    """Read the last JSON object in the output, expecting `passed` and `total`.

    Used by tasks whose verification is not a test runner at all, for example a task whose
    hidden check reads a findings file the agent was asked to produce.
    """
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "passed" in payload and "total" in payload:
            return int(payload["passed"]), int(payload["total"])
    return None


_PARSERS = {
    VerifyParser.PYTEST: parse_pytest,
    VerifyParser.GO_TEST: parse_go_test,
    VerifyParser.NODE_TAP: parse_tap,
    VerifyParser.JSON_REPORT: parse_json_report,
}


def parse_output(parser: VerifyParser, output: str) -> tuple[int, int] | None:
    """Apply the task's parser to verification output."""
    if parser is VerifyParser.EXIT_CODE:
        return None
    return _PARSERS[parser](output)


def _judge_canary(
    canary: Canary, result: ExecResult, parsed: tuple[int, int], duration_ms: int, tail: str
) -> Verification:
    """Read a parsed report that had the planted test in its set.

    Three outcomes. The runner reported the planted test failing and exited the way a
    runner does when a test failed: the report is believed, and the planted test comes out
    of the counts. The runner claimed success, or reported the planted test passing: the
    report is not believed, nothing counts as passed, and the run is marked tampered. The
    runner never mentioned the planted test, which a run cut short by a timeout or a crash
    can do honestly: the counts stand, but nothing confirms them, so the run cannot pass.
    """
    tests_passed, tests_total = parsed
    output = result.combined

    def record(passed: bool, counted: int, total: int, *, tampered: bool = False) -> Verification:
        return Verification(
            passed=passed,
            tests_passed=counted,
            tests_total=total,
            stderr_tail=tail,
            duration_ms=duration_ms,
            exit_code=result.exit_code,
            parse_ok=True,
            canary=canary.name,
            tampered=tampered,
        )

    if result.exit_code == 0 or canary.reported_passed(output):
        return record(False, 0, max(tests_total - 1, 1), tampered=True)
    if not canary.reported_failed(output):
        return record(False, tests_passed, tests_total)
    real_total = tests_total - 1
    clean = result.exit_code == FAILED_TESTS_EXIT_CODE
    return record(clean and real_total > 0 and tests_passed == real_total, tests_passed, real_total)


def verify(task: Task, sandbox: Sandbox) -> Verification:
    """Isolate the workspace, install the hidden tests, run them, and read the result.

    The workspace is first moved into a clean environment where the backend has one, so
    nothing the agent left running or wrote outside the workspace is present while the
    tests run. A must fail test is planted among the hidden tests where the runner reports
    per test, so a report of every test passing can be checked: see `canary`.

    Args:
        task: The task being verified.
        sandbox: The sandbox the agent has just finished working in.

    Returns:
        The verification record, including partial credit where it could be measured.

    Raises:
        VerificationError: If the hidden tests could not be installed or executed.
    """
    started = time.monotonic()
    try:
        sandbox.isolate_for_verify()
        canary = plant(task.verify_parser, sandbox.task_dir / "verify")
        sandbox.install_verify(extra=canary.files() if canary else None)
    except Exception as exc:
        raise VerificationError(f"installing the hidden tests for {task.id} failed: {exc}") from exc

    result = sandbox.exec(
        task.verify_cmd,
        timeout_s=task.verify_timeout_seconds,
        cap_bytes=262_144,
        env=VERIFY_HARDENING_ENV,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    parsed = parse_output(task.verify_parser, result.combined)
    exit_passed = result.exit_code == 0
    tail = result.combined[-OUTPUT_TAIL_BYTES:]

    if parsed is not None and canary is not None:
        reported = _judge_canary(canary, result, parsed, duration_ms, tail)
        if reported.tampered:
            log.warning("verify.tampered", task=task.id, canary=canary.name)
    elif parsed is None:
        # Only a task whose parser is the exit code is graded by the exit code. For any
        # other parser, output it cannot read means no test reported, and the result is
        # marked unparsed so the count below fails it closed.
        reported = Verification(
            passed=exit_passed,
            tests_passed=1 if exit_passed else 0,
            tests_total=1,
            stderr_tail=tail,
            duration_ms=duration_ms,
            exit_code=result.exit_code,
            parse_ok=task.verify_parser is VerifyParser.EXIT_CODE,
            canary=canary.name if canary else None,
        )
    else:
        tests_passed, tests_total = parsed
        reported = Verification(
            # The exit code is authoritative. A parser that reports everything green while
            # the runner exited non-zero has missed something, usually a collection error.
            passed=exit_passed and tests_passed == tests_total and tests_total > 0,
            tests_passed=tests_passed,
            tests_total=tests_total,
            stderr_tail=tail,
            duration_ms=duration_ms,
            exit_code=result.exit_code,
            parse_ok=True,
        )

    # A test the task declares but the runner never reported did not pass. Without this,
    # a verifier that times out after seven of ten scores 7 of 7, and a solution that ends
    # the test process with exit code 0 before the first test runs scores as solved.
    # Rescoring applies the same rule, so a record made before it reads the same way.
    verification = reported.counting_declared(task.expected_tests)
    if verification != reported:
        log.warning(
            "verify.tests_missing",
            task=task.id,
            reported=f"{reported.tests_passed}/{reported.tests_total}",
            counted=f"{verification.tests_passed}/{verification.tests_total}",
            parse_ok=reported.parse_ok,
        )

    log.info(
        "verify.done",
        task=task.id,
        passed=verification.passed,
        tests=f"{verification.tests_passed}/{verification.tests_total}",
        exit_code=result.exit_code,
        timed_out=result.timed_out,
    )
    return verification
