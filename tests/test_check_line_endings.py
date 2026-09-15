"""Tests for the line ending gate.

The bug this guards against was invisible on the machine that wrote the code and on CI,
because both check out LF. It only appeared on a Windows clone, where git had rewritten
every shell script to CRLF and `sh` inside the container read the carriage return as part
of the command. So the tests that matter here are the ones that build a CRLF file on
purpose and assert the guard sees it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_line_endings as gate
import pytest

ROOT = Path(__file__).resolve().parent.parent


class TestTheRepositoryAsCommitted:
    def test_no_tracked_text_file_carries_a_carriage_return(self) -> None:
        offenders = [
            (path, line)
            for path in gate.tracked_text_files()
            if (line := gate.first_cr_line(path)) is not None
        ]
        assert offenders == [], "CRLF in: " + ", ".join(
            f"{p.relative_to(ROOT)}:{line}" for p, line in offenders
        )

    def test_it_actually_looks_at_the_shell_scripts(self) -> None:
        # The files the bug broke. If a future .gitattributes change marks them binary,
        # the guard would silently stop reading them and this test says so.
        checked = {p.relative_to(ROOT).as_posix() for p in gate.tracked_text_files()}
        scripts = {
            p.relative_to(ROOT).as_posix() for p in (ROOT / "tasks").rglob("*.sh") if p.is_file()
        }
        assert scripts, "no task shell scripts found, so this test proves nothing"
        assert scripts <= checked

    def test_binaries_are_excluded(self) -> None:
        # Reading the gzipped forensics log or a wheel as text would be slow and would
        # false-positive on any 0x0d byte inside compressed data.
        checked = {p.relative_to(ROOT).as_posix() for p in gate.tracked_text_files()}
        assert "docs/demo.gif" not in checked
        assert not any(name.endswith(".whl") for name in checked)
        assert not any(name.endswith(".gz") for name in checked)

    def test_main_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main() == 0
        assert "no carriage returns" in capsys.readouterr().out


class TestDetection:
    def test_a_crlf_line_is_found_and_numbered(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sh"
        f.write_bytes(b"#!/bin/sh\nset -eu\r\necho hi\n")
        assert gate.first_cr_line(f) == 2

    def test_an_lf_only_file_is_clean(self, tmp_path: Path) -> None:
        f = tmp_path / "build.sh"
        f.write_bytes(b"#!/bin/sh\nset -eu\necho hi\n")
        assert gate.first_cr_line(f) is None

    def test_a_bare_cr_mid_line_is_found_too(self, tmp_path: Path) -> None:
        # An old Mac line ending, or a stray CR inside a quoted string. Either would
        # reach the container and mean something the author did not intend.
        f = tmp_path / "x.sh"
        f.write_bytes(b'echo "a\rb"\n')
        assert gate.first_cr_line(f) == 1

    def test_an_unreadable_path_does_not_raise(self, tmp_path: Path) -> None:
        assert gate.first_cr_line(tmp_path / "does-not-exist") is None


class TestTheFailureItPrevents:
    """The reason the guard exists, asserted rather than described."""

    def test_sh_rejects_a_crlf_script_with_that_exact_error(self, tmp_path: Path) -> None:
        script = tmp_path / "build_repo.sh"
        script.write_bytes(b"#!/bin/sh\nset -eu\necho reached\n".replace(b"\n", b"\r\n"))
        result = subprocess.run(  # noqa: S603  fixed argv, no shell, no user input
            # `sh` by name on purpose: the bug is about what the shell a container
            # actually resolves does with the file, not about one absolute path.
            ["sh", str(script)],  # noqa: S607  see above
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode != 0
        assert "Illegal option" in result.stderr or "illegal option" in result.stderr
        assert "reached" not in result.stdout

    def test_the_same_script_with_lf_runs_fine(self, tmp_path: Path) -> None:
        script = tmp_path / "build_repo.sh"
        script.write_bytes(b"#!/bin/sh\nset -eu\necho reached\n")
        result = subprocess.run(  # noqa: S603  fixed argv, no shell, no user input
            # `sh` by name on purpose: the bug is about what the shell a container
            # actually resolves does with the file, not about one absolute path.
            ["sh", str(script)],  # noqa: S607  see above
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert "reached" in result.stdout
