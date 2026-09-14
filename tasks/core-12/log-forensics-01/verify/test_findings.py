"""Hidden verification suite for log-forensics-01.

The expected values are generated alongside the log by `vendor/generate_log.py`, so they
are derived from the data rather than typed in by hand. Each field is a separate test,
which means partial credit reflects how much of the incident the agent actually worked out
instead of collapsing to a single pass or fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WORKSPACE = Path.cwd()
EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text())

REQUIRED_KEYS = {
    "root_cause_service",
    "trigger_event_id",
    "trigger_timestamp",
    "resolution_event_id",
    "error_signature",
    "impacted_services",
    "total_5xx",
    "line_count",
}


@pytest.fixture(scope="module")
def findings() -> dict[str, object]:
    path = WORKSPACE / "findings.json"
    if not path.is_file():
        pytest.fail("findings.json was not written. See SCHEMA.md for the required shape.")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        pytest.fail(f"findings.json is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        pytest.fail(f"findings.json must hold a JSON object, found {type(payload).__name__}")
    return payload


def test_schema_keys_are_exactly_as_specified(findings: dict[str, object]) -> None:
    missing = REQUIRED_KEYS - set(findings)
    extra = set(findings) - REQUIRED_KEYS
    assert not missing, f"missing keys: {sorted(missing)}"
    assert not extra, f"unexpected keys: {sorted(extra)}"


def test_root_cause_service(findings: dict[str, object]) -> None:
    assert findings.get("root_cause_service") == EXPECTED["root_cause_service"]


def test_trigger_event(findings: dict[str, object]) -> None:
    assert findings.get("trigger_event_id") == EXPECTED["trigger_event_id"]
    assert findings.get("trigger_timestamp") == EXPECTED["trigger_timestamp"]


def test_resolution_event(findings: dict[str, object]) -> None:
    assert findings.get("resolution_event_id") == EXPECTED["resolution_event_id"]


def test_error_signature_identifies_the_causal_failure(findings: dict[str, object]) -> None:
    signature = str(findings.get("error_signature", "")).strip().lower()
    assert signature, "error_signature is empty"
    assert signature in EXPECTED["error_signature"] or EXPECTED["error_signature"] in signature, (
        f"error_signature {signature!r} does not identify the causal failure"
    )


def test_impacted_services(findings: dict[str, object]) -> None:
    assert findings.get("impacted_services") == EXPECTED["impacted_services"]


def test_total_5xx(findings: dict[str, object]) -> None:
    assert findings.get("total_5xx") == EXPECTED["total_5xx"]


def test_line_count(findings: dict[str, object]) -> None:
    assert findings.get("line_count") == EXPECTED["line_count"]


def test_the_log_was_not_edited() -> None:
    log = WORKSPACE / "app.log"
    assert log.is_file(), "app.log is missing"
    assert sum(1 for _ in log.open()) == EXPECTED["line_count"], (
        "app.log no longer has the number of lines it started with"
    )
