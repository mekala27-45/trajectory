"""Hidden verification suite for cli-implement-01.

This is the only task in the suite with no visible tests, so every assertion here is
derived from SPEC.md rather than from anything the agent could have run.

Five of these guard against shortcuts that make the worked examples come out right while
the specification does not hold. Splitting a record on every `|` instead of the first
three makes a message containing a pipe look malformed, which the spec calls out and the
sample input contains. Taking the first and last line of the file as the span passes the
example only if the file happens to be sorted, and the sample is deliberately not. Skipping
malformed lines and printing a result anyway is the common reading of "malformed input",
and the spec makes it fatal and silent. Fixed column widths pass the examples and fail the
moment one service is dropped by `--number`. And `argparse.FileType` reports a missing file
as a usage error, where the spec separates exit 3 from exit 2.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = "logstat.py"

SAMPLE = (
    "2026-03-01T09:15:04Z|INFO|gateway|GET /health 200\n"
    "2026-03-01T09:15:09Z|WARN|inventory|pool 3/4 in use\n"
    "2026-03-01T09:14:58Z|ERROR|inventory|pool exhausted\n"
    "2026-03-01T09:16:00Z|INFO|checkout|order 1841 placed\n"
    "2026-03-01T09:16:02Z|ERROR|checkout|timeout calling inventory\n"
    "2026-03-01T09:16:02Z|ERROR|gateway|503 for /cart | upstream inventory\n"
    "2026-03-01T09:17:30Z|DEBUG|gateway|retry budget 2 left\n"
    "2026-03-01T09:17:31Z|FATAL|inventory|giving up\n"
)


def run(args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    """Invoke the tool the way the specification says it is invoked."""
    return subprocess.run(  # noqa: S603
        [sys.executable, TOOL, *args],
        input=stdin,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.fixture()
def sample(tmp_path: Path) -> str:
    """The sample input from SPEC.md, on disk."""
    path = tmp_path / "sample.log"
    path.write_text(SAMPLE, encoding="utf-8")
    return str(path)


def test_count_matches_the_worked_example(sample: str) -> None:
    result = run(["count", sample])
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"DEBUG 1\nINFO  2\nWARN  1\nERROR 3\nFATAL 1\nTOTAL 8\n"


def test_count_starts_at_min_level_and_accepts_both_spellings(sample: str) -> None:
    spaced = run(["count", "--min-level", "ERROR", sample])
    assert spaced.returncode == 0, spaced.stderr
    assert spaced.stdout == b"ERROR 3\nFATAL 1\nTOTAL 4\n"

    joined = run(["count", "--min-level=WARN", sample])
    assert joined.returncode == 0, joined.stderr
    assert joined.stdout == b"WARN  1\nERROR 3\nFATAL 1\nTOTAL 5\n"


def test_top_matches_the_worked_example(sample: str) -> None:
    result = run(["top", "--min-level", "ERROR", sample])
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"inventory 2\ncheckout  1\ngateway   1\n"


def test_top_breaks_ties_by_name_and_sizes_columns_from_what_it_prints(sample: str) -> None:
    two = run(["top", "--number", "2", sample])
    assert two.returncode == 0, two.stderr
    assert two.stdout == b"gateway   3\ninventory 3\n"

    # One line printed, so the name column is as wide as "gateway" and no wider.
    one = run(["top", "--number", "1", sample])
    assert one.returncode == 0, one.stderr
    assert one.stdout == b"gateway 3\n"


def test_span_uses_timestamp_order_rather_than_file_order(sample: str) -> None:
    result = run(["span", sample])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        b"first 2026-03-01T09:14:58Z\nlast  2026-03-01T09:17:31Z\nsecs  153\n"
    )

    lone = run(["span", "--min-level", "FATAL", sample])
    assert lone.returncode == 0, lone.stderr
    assert lone.stdout == (
        b"first 2026-03-01T09:17:31Z\nlast  2026-03-01T09:17:31Z\nsecs  0\n"
    )


def test_reads_standard_input_when_no_file_is_given() -> None:
    piped = run(["top", "--number", "2"], stdin=SAMPLE.encode())
    assert piped.returncode == 0, piped.stderr
    assert piped.stdout == b"gateway   3\ninventory 3\n"

    counted = run(["count"], stdin=SAMPLE.encode())
    assert counted.returncode == 0, counted.stderr
    assert counted.stdout == b"DEBUG 1\nINFO  2\nWARN  1\nERROR 3\nFATAL 1\nTOTAL 8\n"


def test_empty_input_is_not_an_error_for_count_or_top() -> None:
    zeros = b"DEBUG 0\nINFO  0\nWARN  0\nERROR 0\nFATAL 0\nTOTAL 0\n"
    for stdin in (b"", b"\n", b"\n\n   \t\n\n"):
        counted = run(["count"], stdin=stdin)
        assert counted.returncode == 0, (stdin, counted.stderr)
        assert counted.stdout == zeros, stdin

        topped = run(["top"], stdin=stdin)
        assert topped.returncode == 0, (stdin, topped.stderr)
        assert topped.stdout == b"", stdin


def test_span_with_nothing_to_summarise_exits_5_and_prints_nothing() -> None:
    empty = run(["span"], stdin=b"")
    assert empty.returncode == 5
    assert empty.stdout == b""
    assert empty.stderr == b"logstat: no records matched\n"

    filtered = run(
        ["span", "--min-level", "ERROR"],
        stdin=b"2026-03-01T09:15:04Z|INFO|gateway|nothing severe here\n",
    )
    assert filtered.returncode == 5
    assert filtered.stdout == b""
    assert filtered.stderr == b"logstat: no records matched\n"


def test_a_malformed_line_is_fatal_silent_and_reported_by_lowest_line_number() -> None:
    mixed = (
        "2026-03-01T09:15:04Z|INFO|gateway|503 for /cart | upstream inventory\n"
        "\n"
        "2026-03-01T09:15:05Z|TRACE|gateway|TRACE is not a level\n"
        "not a record at all\n"
    )
    result = run(["count"], stdin=mixed.encode())
    assert result.returncode == 4
    assert result.stdout == b""
    assert result.stderr == b"logstat: malformed record on line 3\n"

    single_line_cases = [
        " 2026-03-01T09:15:04Z|INFO|gateway|leading space",
        "2026-02-30T09:15:04Z|INFO|gateway|no such day",
        "2026-2-01T09:15:04Z|INFO|gateway|month is not padded",
        "2026-03-01T09:15:04Z|INFO||service is empty",
        "2026-03-01T09:15:04Z|INFO|gateway",
        "2026-03-01T09:15:04Z|info|gateway|level is lower case",
    ]
    for line in single_line_cases:
        for subcommand in ("count", "top", "span"):
            bad = run([subcommand], stdin=f"{line}\n".encode())
            assert bad.returncode == 4, (subcommand, line, bad.stderr)
            assert bad.stdout == b"", (subcommand, line)
            assert bad.stderr == b"logstat: malformed record on line 1\n", (subcommand, line)


def test_usage_errors_exit_2_and_a_missing_file_exits_3(sample: str, tmp_path: Path) -> None:
    usage_cases = [
        [],
        ["counts", sample],
        ["count", "--min-level", "warn", sample],
        ["count", "--min-level", "WARNING", sample],
        ["count", "--min-level"],
        ["count", "--min-level="],
        ["top", "--number", "0", sample],
        ["top", "--number", "-1", sample],
        ["top", "--number", "two", sample],
        ["span", "--number", "2", sample],
        ["count", "--number", "2", sample],
        ["count", "--colour", sample],
        ["count", sample, sample],
    ]
    for args in usage_cases:
        result = run(args)
        assert result.returncode == 2, (args, result.returncode, result.stdout, result.stderr)
        assert result.stdout == b"", args
        assert result.stderr != b"", args

    missing = run(["count", str(tmp_path / "not-here.log")])
    assert missing.returncode == 3, (missing.returncode, missing.stdout, missing.stderr)
    assert missing.stdout == b""
    assert missing.stderr != b""
