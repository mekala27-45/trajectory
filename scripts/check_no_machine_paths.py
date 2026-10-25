#!/usr/bin/env python
"""Fail if a tracked file hardcodes a path that exists on one machine.

Three bugs in this repository had the same shape: something true about the machine the
code was written on got committed as though it were true everywhere.

1. Five tests drove the local sandbox through a verify command that needs `pytest`, and
   passed because the authoring machine's system python happened to have it. CI's did not.
2. `playwright.config.ts` defaulted `PLAYWRIGHT_BROWSERS_PATH` to `/opt/pw-browsers`,
   where this container keeps its browsers. `playwright install chromium` does not read
   that config and installed to the default cache, `playwright test` does read it and
   looked in `/opt/pw-browsers`, and the web job failed on a missing executable.
3. `scripts/record_demo.sh` imported Playwright through
   `/home/<user>/trajectory/web/node_modules/...`, so the script that regenerates the
   README demo could only ever run for one person.

The first was caught by CI. The second and third were not, because a path that is merely
wrong somewhere else is still syntactically fine, still lints, and still passes every
test on the machine that wrote it.

So: no tracked file may contain an absolute path into a user home directory or into this
container's tool directories. Container paths (`/app`, `/workspace`, `/usr/local/go`) are
fine and common here, and are deliberately not in the list: they are real inside the image
that declares them. What is banned is a path whose existence depends on whose laptop it is.

This file and its test are exempt, because they have to name the patterns to look for.
Anything else that genuinely needs one of these strings should read it from the
environment instead, which is what the three fixes above all did.

Run records are read as JSON rather than as lines, because part of a record is a quotation.
Each step quotes what the model wrote and what the task container printed back, and a
failure mode's evidence quotes those steps. Inside the container the agent is the user
`agent`, so pip reports `/home/agent/.local`, and a model is free to type `/root/` into a
command. Those strings are evidence about the model, and editing them would falsify the
record, so they are set aside. Every other field of a record was written by the runner on
the host, where a home directory path is exactly the bug this gate exists for, so the rest
of the record is still checked: the config, the image, the fingerprint, and the run's own
error, which is where a traceback from the host would land.

Exits 0 when clean, 1 otherwise, naming every offending file and line.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Each pattern, with the reason it cannot be committed.
BANNED: dict[str, str] = {
    r"/home/(?!\$|\{)[a-z][a-z0-9._-]*/": "an absolute path into a Linux user home",
    r"/Users/(?!\$|\{)[A-Za-z][A-Za-z0-9._-]*/": "an absolute path into a macOS user home",
    r"[A-Za-z]:\\\\?Users\\\\?": "an absolute path into a Windows user profile",
    r"/opt/pw-browsers": "this container's Playwright cache, which is not where yours is",
    r"/root/\.": "an absolute path into the root account's home",
}
COMPILED = {re.compile(pattern): reason for pattern, reason in BANNED.items()}

# These two have to contain the patterns in order to search for them.
EXEMPT = {
    "scripts/check_no_machine_paths.py",
    "tests/test_check_no_machine_paths.py",
}

# Where run records live, and the fields of a record that quote the task container.
RUN_RECORDS = ("fixtures/model-runs/", "fixtures/recorded-runs/")
QUOTED_STEP_FIELDS = ("thought", "tool_name", "tool_args", "tool_output", "error")
QUOTED_VERIFICATION_FIELDS = ("stderr_tail",)
QUOTED_FAILURE_FIELDS = ("evidence",)


def tracked_text_files() -> list[Path]:
    """Every tracked file git does not consider binary, minus the exempt pair."""
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = [n for n in listed.stdout.split(b"\0") if n]
    attrs = subprocess.run(
        ["git", "check-attr", "--stdin", "-z", "binary"],
        cwd=ROOT,
        input=b"\0".join(names),
        capture_output=True,
        check=True,
    )
    fields = attrs.stdout.split(b"\0")
    binary = {
        fields[i].decode()
        for i in range(0, len(fields) - 2, 3)
        if fields[i + 2] in (b"set", b"true")
    }
    return [
        ROOT / n.decode() for n in names if n.decode() not in binary and n.decode() not in EXEMPT
    ]


def offences(path: Path) -> list[tuple[int | str, str, str]]:
    """Every banned absolute path in one file, as (where, reason, the text).

    Where is a line number, or a JSON pointer for a run record.
    """
    if is_run_record(path):
        return record_offences(path)
    return line_offences(path)


def line_offences(path: Path) -> list[tuple[int | str, str, str]]:
    """Every banned absolute path in a file read as lines, as (line, reason, the line)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found: list[tuple[int | str, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        reason = banned(line)
        if reason:
            found.append((number, reason, line.strip()[:120]))
    return found


def banned(text: str) -> str | None:
    """The reason the first banned pattern in the text cannot be committed, if any."""
    for pattern, reason in COMPILED.items():
        if pattern.search(text):
            return reason
    return None


def is_run_record(path: Path) -> bool:
    """True for a JSON file in one of the directories that hold run records."""
    try:
        relative = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return False
    return relative.endswith(".json") and relative.startswith(RUN_RECORDS)


def written_by_the_runner(record: dict[str, Any]) -> dict[str, Any]:
    """A copy of a run record without the strings it quotes from the task container."""
    kept = copy.deepcopy(record)
    sections = (
        (kept.get("steps"), QUOTED_STEP_FIELDS),
        ([kept.get("verification")], QUOTED_VERIFICATION_FIELDS),
        (kept.get("failure_modes"), QUOTED_FAILURE_FIELDS),
    )
    for entries, fields in sections:
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                for field in fields:
                    entry.pop(field, None)
    return kept


def strings(node: object, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Every string in a parsed JSON value, with its JSON pointer."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from strings(value, f"{pointer}/{index}")
    elif isinstance(node, str):
        yield pointer, node


def record_offences(path: Path) -> list[tuple[int | str, str, str]]:
    """Every banned absolute path in the fields of a run record the runner wrote.

    A file in a record directory that does not parse as a record is read as lines.
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return line_offences(path)
    if not isinstance(record, dict):
        return line_offences(path)
    found: list[tuple[int | str, str, str]] = []
    for pointer, text in strings(written_by_the_runner(record)):
        reason = banned(text)
        if reason:
            found.append((pointer, reason, text.strip()[:120]))
    return found


def main() -> int:
    """Check every tracked text file and report each machine specific path."""
    problems: list[str] = []
    checked = 0
    for path in tracked_text_files():
        checked += 1
        for where, reason, line in offences(path):
            problems.append(f"{path.relative_to(ROOT)}:{where}: {reason}\n    {line}")

    for problem in problems:
        print(problem, file=sys.stderr)

    if problems:
        print(
            f"\n{len(problems)} machine specific path(s) in {checked} tracked text files.\n"
            "Read the value from the environment instead, or resolve it relative to the\n"
            "repository. See the header of scripts/check_no_machine_paths.py.",
            file=sys.stderr,
        )
        return 1

    print(f"{checked} tracked text files, no machine specific absolute paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
