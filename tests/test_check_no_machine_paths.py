"""Tests for the machine specific path gate.

Three bugs in this repository had this shape, and two of them reached CI. The tests that
matter are the ones that rebuild each of the three on purpose and assert the gate names
it, so this file quotes the real offending lines rather than inventing stand-ins.
"""

from __future__ import annotations

from pathlib import Path

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
