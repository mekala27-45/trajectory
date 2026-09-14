#!/usr/bin/env python3
"""Generate the ledger fixture the rollup is timed against.

The fixture is 200,000 rows over 6,000 accounts, which is one ordinary day for this
service. It is generated rather than committed because 200,000 rows of CSV do not belong
in a repository, and it is generated from a fixed seed so that a timing number measured on
one machine means the same thing on another.

Run from the repository root:

    python3 tools/make_fixture.py
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

ROWS = 200_000
ACCOUNTS = 6_000
SEED = 20260914
REGIONS = ("eu-west", "eu-north", "us-east", "us-west", "ap-south", "sa-east")
HEADER = ("account", "region", "amount_cents", "occurred_on")
TARGET = Path("data/ledger.csv")


def main() -> None:
    """Write the fixture, overwriting whatever was there."""
    rng = random.Random(SEED)
    # Shuffled so that first seen order has nothing to do with sorted order. Code that
    # quietly assumes the two are the same should fail its tests, not pass them by luck.
    accounts = [f"acct-{index:05d}" for index in range(ACCOUNTS)]
    rng.shuffle(accounts)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for _ in range(ROWS):
            account = accounts[rng.randrange(ACCOUNTS)]
            region = REGIONS[rng.randrange(len(REGIONS))]
            # Mostly small payments, a few large ones, and the occasional refund.
            if rng.random() < 0.02:
                amount = -rng.randrange(500, 40_000)
            elif rng.random() < 0.05:
                amount = rng.randrange(200_000, 5_000_000)
            else:
                amount = rng.randrange(100, 60_000)
            day = 1 + rng.randrange(28)
            writer.writerow([account, region, amount, f"2026-06-{day:02d}"])

    size_mb = TARGET.stat().st_size / (1024 * 1024)
    print(f"wrote {TARGET} with {ROWS} rows over {ACCOUNTS} accounts ({size_mb:.1f} MiB)")


if __name__ == "__main__":
    main()
