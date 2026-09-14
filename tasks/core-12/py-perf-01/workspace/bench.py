#!/usr/bin/env python3
"""Time the rollup against the budget the finance export has to meet.

Only `summarise` is timed. Reading the CSV is the exporter's cost, not the rollup's, and
mixing the two hides which one is actually slow.

    python3 bench.py [path]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from rollup import load_rows, summarise  # noqa: E402

BUDGET_S = 2.0


def main(argv: list[str]) -> int:
    """Load the ledger, time one rollup, and report against the budget."""
    path = argv[0] if argv else "data/ledger.csv"
    rows = load_rows(path)

    started = time.perf_counter()
    summary = summarise(rows)
    elapsed = time.perf_counter() - started

    print(f"{len(rows)} rows, {len(summary)} accounts")
    print(f"summarise took {elapsed:.3f}s against a budget of {BUDGET_S:.1f}s")
    if elapsed >= BUDGET_S:
        print("OVER BUDGET")
        return 1
    print("within budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
