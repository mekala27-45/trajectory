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
from trajectory_runner.sandbox import Sandbox

log = structlog.get_logger(__name__)

STDERR_TAIL_BYTES = 4000

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


def verify(task: Task, sandbox: Sandbox) -> Verification:
    """Install the hidden tests, run them, and turn the result into a `Verification`.

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
        sandbox.install_verify()
    except Exception as exc:
        raise VerificationError(f"installing the hidden tests for {task.id} failed: {exc}") from exc

    result = sandbox.exec(
        task.verify_cmd,
        timeout_s=task.verify_timeout_seconds,
        cap_bytes=262_144,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    parsed = parse_output(task.verify_parser, result.combined)
    exit_passed = result.exit_code == 0

    if parsed is None:
        verification = Verification(
            passed=exit_passed,
            tests_passed=1 if exit_passed else 0,
            tests_total=1,
            stderr_tail=result.stderr[-STDERR_TAIL_BYTES:],
            duration_ms=duration_ms,
            exit_code=result.exit_code,
            parse_ok=task.verify_parser is VerifyParser.EXIT_CODE,
        )
    else:
        tests_passed, tests_total = parsed
        verification = Verification(
            # The exit code is authoritative. A parser that reports everything green while
            # the runner exited non-zero has missed something, usually a collection error.
            passed=exit_passed and tests_passed == tests_total and tests_total > 0,
            tests_passed=tests_passed,
            tests_total=tests_total,
            stderr_tail=result.stderr[-STDERR_TAIL_BYTES:],
            duration_ms=duration_ms,
            exit_code=result.exit_code,
            parse_ok=True,
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
