"""Turn a vendor order export into the warehouse format.

The output contract lives in CONTRACT.md and is the only thing downstream of here that is
guaranteed. Rows that cannot be converted go to the rejects file with a reason, because a
loader that drops a row is worse than a loader that fails: the row is gone and the totals
are quietly wrong.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

FIELDS = (
    "order_id",
    "customer_name",
    "region",
    "currency",
    "amount_cents",
    "discount_cents",
    "net_cents",
    "placed_on",
)
"""The warehouse columns, in the order CONTRACT.md lists them."""


@dataclass(frozen=True)
class LoadReport:
    """What one run of the loader did.

    Attributes:
        read: Data rows in the source file.
        written: Rows written to the warehouse file.
        rejected: Rows written to the rejects file.
    """

    read: int
    written: int
    rejected: int


def _record(row: dict[str, str]) -> dict[str, object]:
    """Turn one source row into a warehouse record.

    Args:
        row: One row of the export, field name to raw string.

    Returns:
        The record, with the contract's fields in the contract's order.

    Raises:
        ValueError: If the order total is not an integer.
    """
    amount = int(row["amount_cents"])
    return {
        "order_id": row["order_id"],
        "customer_name": row["customer_name"],
        "region": row["region_code"],
        "currency": row["currency"],
        "amount_cents": amount,
        "discount_cents": 0,
        "net_cents": amount,
        "placed_on": row["placed_at"],
    }


def transform_file(
    source: str | Path,
    destination: str | Path,
    rejects: str | Path,
) -> LoadReport:
    """Load one export into the warehouse format.

    Args:
        source: Vendor export CSV.
        destination: Warehouse file to write, one JSON object per line.
        rejects: Rejects file to write, one JSON object per line.

    Returns:
        Counts for the run.
    """
    read = written = rejected = 0

    with Path(source).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        with (
            Path(destination).open("w", encoding="utf-8") as out,
            Path(rejects).open("w", encoding="utf-8") as bad,
        ):
            # The header is line 1, so the first data row is line 2.
            for line, row in enumerate(reader, start=2):
                read += 1
                try:
                    record = _record(row)
                except ValueError:
                    bad.write(
                        json.dumps({"line": line, "reason": "bad_amount", "raw": row}) + "\n"
                    )
                    rejected += 1
                    continue
                out.write(json.dumps(record) + "\n")
                written += 1

    return LoadReport(read=read, written=written, rejected=rejected)
