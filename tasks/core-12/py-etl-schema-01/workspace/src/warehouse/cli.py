"""Command line front end for the warehouse loader."""

from __future__ import annotations

import sys
from pathlib import Path

from warehouse.pipeline import transform_file

USAGE = "usage: load.py <source.csv> <orders.jsonl> <rejects.jsonl>"


def main(argv: list[str]) -> int:
    """Load one export and report what happened.

    Args:
        argv: Source path, destination path, rejects path.

    Returns:
        Process exit status.
    """
    if len(argv) != 3:
        print(USAGE, file=sys.stderr)
        return 2

    source, destination, rejects = argv
    for path in (destination, rejects):
        parent = Path(path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)

    report = transform_file(source, destination, rejects)
    print(f"read {report.read}, wrote {report.written}, rejected {report.rejected}")
    return 0
