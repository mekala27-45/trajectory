"""Tests for the machine specific path gate.

Three bugs in this repository had this shape, and two of them reached CI. The tests that
matter are the ones that rebuild each of the three on purpose and assert the gate names
it, so this file quotes the real offending lines rather than inventing stand-ins.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import check_no_machine_paths as gate
import pytest

ROOT = Path(__file__).resolve().parent.parent

# The three lines as they were actually committed.
PLAYWRIGHT_CONFIG_LINE = 'process.env.PLAYWRIGHT_BROWSERS_PATH ??= "/opt/pw-browsers";'
DEMO_SCRIPT_LINE = (
    'import { chromium } from "/home/claude/trajectory/web/node_modules/'
    '@playwright/test/index.mjs";'
)
DEMO_SCRIPT_ENV_LINE = (
    'export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"'
)


class TestTheRepositoryAsCommitted:
    def test_no_tracked_file_hardcodes_a_machine_path(self) -> None:
        found = [
            f"{path.relative_to(ROOT)}:{number}: {reason}"
            for path in gate.tracked_text_files()
            for number, reason, _ in gate.offences(path)
        ]
        assert found == []

    def test_it_actually_looks_at_the_files_the_bugs_were_in(self) -> None:
        checked = {p.relative_to(ROOT).as_posix() for p in gate.tracked_text_files()}
        assert "web/playwright.config.ts" in checked
        assert "scripts/record_demo.sh" in checked

    def test_only_the_gate_and_its_own_test_are_exempt(self) -> None:
        assert sorted(gate.EXEMPT) == [
            "scripts/check_no_machine_paths.py",
            "tests/test_check_no_machine_paths.py",
        ]

    def test_main_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main() == 0
        assert "no machine specific absolute paths" in capsys.readouterr().out


class TestTheThreeRealBugs:
    def test_the_playwright_browsers_default_is_caught(self, tmp_path: Path) -> None:
        f = tmp_path / "playwright.config.ts"
        f.write_text(f"import x from 'y';\n{PLAYWRIGHT_CONFIG_LINE}\n", encoding="utf-8")
        found = gate.offences(f)
        assert [number for number, _, _ in found] == [2]
        assert "Playwright cache" in found[0][1]

    def test_the_absolute_node_modules_import_is_caught(self, tmp_path: Path) -> None:
        f = tmp_path / "record_demo.sh"
        f.write_text(f"#!/usr/bin/env bash\n{DEMO_SCRIPT_LINE}\n", encoding="utf-8")
        found = gate.offences(f)
        assert found, "the absolute home directory import was not caught"
        assert "Linux user home" in found[0][1]

    def test_the_overridable_default_is_caught_too(self, tmp_path: Path) -> None:
        # It was overridable, which made it look harmless. It still encoded one machine.
        f = tmp_path / "record_demo.sh"
        f.write_text(f"#!/usr/bin/env bash\n{DEMO_SCRIPT_ENV_LINE}\n", encoding="utf-8")
        assert gate.offences(f)


class TestWhatStaysAllowed:
    @pytest.mark.parametrize(
        "line",
        [
            "WORKDIR /app",
            "COPY --from=builder /app/.venv /app/.venv",
            "ENV PATH=/usr/local/go/bin:${PATH}",
            "WORKDIR /workspace",
            "cd /workspace && pytest",
            "#!/usr/bin/env bash",
            'export HOME="$HOME"',
            'path = Path.home() / ".cache"',
            # A variable standing in for the home is the fix, not the bug.
            'import x from "${HOME}/thing.mjs"',
            'import y from "$HOME/thing.mjs"',
        ],
    )
    def test_container_and_relative_paths_are_fine(self, tmp_path: Path, line: str) -> None:
        f = tmp_path / "somefile"
        f.write_text(line + "\n", encoding="utf-8")
        assert gate.offences(f) == [], line

    @pytest.mark.parametrize(
        "line",
        [
            'x = "/home/claude/trajectory/web"',
            'x = "/Users/ajay/Projects/trajectory"',
            'x = "C:\\\\Users\\\\mekal\\\\trajectory"',
            'x = "/root/.cache/ms-playwright"',
        ],
    )
    def test_every_banned_shape_is_caught(self, tmp_path: Path, line: str) -> None:
        f = tmp_path / "somefile"
        f.write_text(line + "\n", encoding="utf-8")
        assert gate.offences(f), line

    def test_one_line_is_reported_once_even_with_two_offences(self, tmp_path: Path) -> None:
        f = tmp_path / "somefile"
        f.write_text('a = "/home/x/" ; b = "/opt/pw-browsers"\n', encoding="utf-8")
        assert len(gate.offences(f)) == 1

    def test_a_binary_file_does_not_raise(self, tmp_path: Path) -> None:
        f = tmp_path / "thing.bin"
        f.write_bytes(b"\x00\x01\xfe\xff")
        assert gate.offences(f) == []

    def test_a_missing_file_does_not_raise(self, tmp_path: Path) -> None:
        assert gate.offences(tmp_path / "nope") == []


# A model panel record whose steps quote pip run as the container's `agent` user.
QUOTING_RECORD = ROOT / "fixtures/model-runs/01a170ff-100f-70b9-85d5-fb1cfb52ea66.json"


def a_record(**fields: Any) -> dict[str, Any]:
    """The smallest record shape the gate reads: one step, a verification, one hit."""
    record: dict[str, Any] = {
        "id": "r1",
        "config": {"model": "scripted/reference"},
        "steps": [
            {
                "index": 0,
                "thought": "check where pip put it",
                "tool_name": "bash",
                "tool_args": {"command": "pip show libcore"},
                "tool_output": "Location: /home/agent/.local/lib/python3.12/site-packages",
                "error": None,
            }
        ],
        "verification": {"stderr_tail": "collected 4 items"},
        "failure_modes": [{"id": "F08", "evidence": "step 0: rm -rf /root/.cache"}],
        "error": None,
    }
    record.update(fields)
    return record


def write(tmp_path: Path, record: dict[str, Any]) -> Path:
    f = tmp_path / "record.json"
    f.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return f


class TestRunRecords:
    def test_both_record_directories_are_read_as_records(self) -> None:
        checked = gate.tracked_text_files()
        records = [p for p in checked if gate.is_run_record(p)]
        folders = {p.parent.relative_to(ROOT).as_posix() for p in records}
        assert folders == {"fixtures/model-runs", "fixtures/recorded-runs"}

    def test_files_beside_the_records_are_still_read_as_lines(self) -> None:
        assert not gate.is_run_record(ROOT / "fixtures/model-runs.provenance.json")
        assert not gate.is_run_record(ROOT / "fixtures/mutation/core-12.json")

    def test_the_record_ci_flagged_quotes_the_agent_home_and_passes(self) -> None:
        # The steps really do say /home/agent, which is why reading lines failed CI,
        # and the fields the runner wrote do not, which is why this record is clean.
        assert "/home/agent/" in QUOTING_RECORD.read_text(encoding="utf-8")
        assert gate.line_offences(QUOTING_RECORD)
        assert gate.offences(QUOTING_RECORD) == []

    def test_what_the_container_said_is_set_aside(self, tmp_path: Path) -> None:
        assert gate.record_offences(write(tmp_path, a_record())) == []

    def test_a_host_path_in_the_runs_own_error_is_caught(self, tmp_path: Path) -> None:
        traceback = 'File "/home/claude/.venv/lib/python3.12/site-packages/litellm/main.py"'
        found = gate.record_offences(write(tmp_path, a_record(error=traceback)))
        assert [(where, reason) for where, reason, _ in found] == [
            ("/error", "an absolute path into a Linux user home")
        ]

    def test_a_host_path_in_the_config_is_caught(self, tmp_path: Path) -> None:
        config = {"model": "scripted/reference", "task_dir": "/Users/ajay/trajectory/tasks"}
        found = gate.record_offences(write(tmp_path, a_record(config=config)))
        assert [where for where, _, _ in found] == ["/config/task_dir"]

    def test_a_step_field_the_runner_writes_is_still_checked(self, tmp_path: Path) -> None:
        record = a_record()
        record["steps"][0]["timestamp"] = "/root/.cache"
        found = gate.record_offences(write(tmp_path, record))
        assert [where for where, _, _ in found] == ["/steps/0/timestamp"]

    def test_setting_quotes_aside_leaves_the_file_alone(self, tmp_path: Path) -> None:
        record = a_record()
        before = json.dumps(record, sort_keys=True)
        gate.written_by_the_runner(record)
        assert json.dumps(record, sort_keys=True) == before

    def test_a_file_that_is_not_a_record_is_read_as_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.json"
        f.write_text('{"error": "/home/claude/x",\n', encoding="utf-8")
        assert [where for where, _, _ in gate.record_offences(f)] == [1]
        f.write_text('["/home/claude/x"]\n', encoding="utf-8")
        assert [where for where, _, _ in gate.record_offences(f)] == [1]
