#!/usr/bin/env python3
"""Entry point for the warehouse loader.

    python3 load.py <source.csv> <orders.jsonl> <rejects.jsonl>

A shim so the loader runs without anything on PYTHONPATH, which is how the scheduler
calls it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from warehouse.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
