#!/usr/bin/env python
"""Fail if a tracked text file carries a carriage return.

Five of the twelve tasks ship a shell script, and those scripts are copied into a Linux
container and executed by `sh`. A carriage return at the end of a line is not whitespace to
`sh`: it is part of the last token. A valid `set -eu` becomes

    vendor/build_repo.sh: 10: set: Illegal option -

with the CR invisible in the error. The same applies to a task's Dockerfile, to the
`verify_cmd` a task declares, and to any expected-output fixture whose bytes are compared.

`.gitattributes` sets `* text=auto eol=lf`, so a fresh clone on any platform gets LF. This
check is the other half: it stops a CRLF file being committed in the first place, from an
editor configured to write them or a merge tool that rewrote a file wholesale.

Skips anything `.gitattributes` marks binary, so the wheelhouse, the gzipped forensics log
and the README GIF are never read.

Exits 0 when clean, 1 otherwise, naming every offending file and its first bad line.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CR = b"\r"


def tracked_text_files() -> list[Path]:
    """Every tracked file git does not consider binary, as absolute paths.

    Asks git rather than guessing by extension, so the answer follows `.gitattributes`
    and a new binary type never has to be added here as well.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    names = [n for n in listed.stdout.split(b"\0") if n]

    attrs = subprocess.run(
        ["git", "check-attr", "--stdin", "-z", "binary"],
        cwd=ROOT,
        input=b"\0".join(names),
        capture_output=True,
        check=True,
    )
    # check-attr -z emits path, attribute, value, repeating.
    fields = attrs.stdout.split(b"\0")
    binary = {
        fields[i].decode()
        for i in range(0, len(fields) - 2, 3)
        if fields[i + 2] in (b"set", b"true")
    }

    return [ROOT / n.decode() for n in names if n.decode() not in binary]


def first_cr_line(path: Path) -> int | None:
    """Line number of the first line ending in a carriage return, or None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if CR not in data:
        return None
    for number, line in enumerate(data.split(b"\n"), start=1):
        if line.endswith(CR) or CR in line:
            return number
    return None


def main() -> int:
    """Check every tracked text file and report each one that carries a CR."""
    offenders: list[tuple[Path, int]] = []
    checked = 0
    for path in tracked_text_files():
        checked += 1
        line = first_cr_line(path)
        if line is not None:
            offenders.append((path, line))

    for path, line in offenders:
        print(
            f"{path.relative_to(ROOT)}:{line}: carriage return in a text file",
            file=sys.stderr,
        )

    if offenders:
        print(
            f"\n{len(offenders)} of {checked} tracked text files carry a carriage return.\n"
            "These are executed inside Linux containers, where a CR is part of the command\n"
            "rather than whitespace. Convert them to LF. `.gitattributes` sets\n"
            "`* text=auto eol=lf`, so `git add --renormalize .` usually does it.",
            file=sys.stderr,
        )
        return 1

    print(f"{checked} tracked text files, no carriage returns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
