#!/usr/bin/env python3
"""Fail if an em dash appears anywhere in the repository.

The house style for this project uses commas, colons, parentheses or the word "to" for
ranges. This runs as a pre-commit hook and as a CI job so the rule holds without anyone
having to remember it.

Usage:
    scripts/check_no_em_dash.py [paths...]

With no arguments it walks every tracked text file under the repository root.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Em dash and its visual siblings, spelled as codepoints so this file does not trip its
# own check. En dash (U+2013) is allowed: it carries a different meaning and does not
# read as an em dash in prose.
BANNED = {
    chr(0x2014): "EM DASH",
    chr(0x2015): "HORIZONTAL BAR",
    chr(0x2E3A): "TWO-EM DASH",
    chr(0x2E3B): "THREE-EM DASH",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".venv",
    "node_modules",
    "htmlcov",
    "dist",
    "build",
    "out",
}

SKIP_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mp4",
    ".pdf",
    ".png",
    ".svg",
    ".webm",
    ".woff",
    ".woff2",
    ".zip",
}


def tracked_files() -> list[Path]:
    """Return every file git knows about, falling back to a filesystem walk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        )
        names = [n for n in out.stdout.split("\0") if n]
        if names:
            return [Path(n) for n in names]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return [p for p in Path().rglob("*") if p.is_file()]


def should_check(path: Path) -> bool:
    """Return True when the path is a text file worth scanning."""
    if not path.is_file():
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    return path.suffix.lower() not in SKIP_SUFFIXES


def main(argv: list[str]) -> int:
    """Scan the given paths, or the whole repository, and report every hit."""
    paths = [Path(a) for a in argv] if argv else tracked_files()
    failures: list[str] = []

    for path in paths:
        if not should_check(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for char, name in BANNED.items():
                col = line.find(char)
                if col >= 0:
                    failures.append(
                        f"{path}:{lineno}:{col + 1}: {name} ({char!r}) in: {line.strip()[:88]}"
                    )

    if failures:
        sys.stderr.write("em dashes are not allowed in this repository:\n")
        for failure in failures:
            sys.stderr.write(f"  {failure}\n")
        sys.stderr.write(
            f"\n{len(failures)} occurrence(s). Use a comma, a colon, parentheses, or 'to'.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
