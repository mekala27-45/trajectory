"""Daily rollup of the payments ledger.

One run of `summarise` turns a day of ledger rows into one summary per account: how many
payments, what they came to, the largest single payment, and which regions the account
transacted in. The finance export runs this on every ledger it receives, so it has a
budget: the rollup of a 200,000 row day has to finish in under two seconds.

Accounts come out in the order they first appear in the ledger, not sorted. That is what
the export downstream of this expects, and sorting several thousand accounts by
identifier would put the day's largest account halfway down the file for no reason.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

HEADER = ["account", "region", "amount_cents", "occurred_on"]


class Row(NamedTuple):
    """One ledger row as the exporter writes it."""

    account: str
    region: str
    amount_cents: int
    occurred_on: str


@dataclass(frozen=True)
class AccountSummary:
    """What one account did over the ledger.

    Attributes:
        account: Account identifier.
        count: Number of ledger rows for the account.
        total_cents: Sum of the amounts, refunds included, so this can be negative.
        max_cents: Largest single amount, which is the largest refund when every row is
            a refund.
        regions: Distinct regions the account transacted in, in first seen order.
    """

    account: str
    count: int
    total_cents: int
    max_cents: int
    regions: tuple[str, ...]


def load_rows(path: str | Path) -> list[Row]:
    """Read a ledger CSV into rows.

    Args:
        path: Path to a ledger CSV with the standard header.

    Returns:
        The rows in file order.

    Raises:
        ValueError: If the header is not the one the exporter writes.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != HEADER:
            raise ValueError(f"expected header {HEADER}, found {header}")
        return [
            Row(account, region, int(amount), occurred_on)
            for account, region, amount, occurred_on in reader
        ]


def summarise(rows: Iterable[Row]) -> dict[str, AccountSummary]:
    """Roll a ledger up to one summary per account.

    Args:
        rows: Ledger rows, in any order.

    Returns:
        Account identifier to summary, keyed in the order the accounts first appear in
        the input.
    """
    accounts: list[str] = []
    counts: list[int] = []
    totals: list[int] = []
    peaks: list[int] = []
    regions: list[list[str]] = []

    for row in rows:
        try:
            slot = accounts.index(row.account)
        except ValueError:
            # First time this account has been seen, so start its running figures from
            # this row rather than from zero, which would be wrong for a refund.
            accounts.append(row.account)
            counts.append(1)
            totals.append(row.amount_cents)
            peaks.append(row.amount_cents)
            regions.append([row.region])
            continue

        counts[slot] += 1
        totals[slot] += row.amount_cents
        if row.amount_cents > peaks[slot]:
            peaks[slot] = row.amount_cents
        if row.region not in regions[slot]:
            regions[slot].append(row.region)

    return {
        account: AccountSummary(
            account=account,
            count=counts[index],
            total_cents=totals[index],
            max_cents=peaks[index],
            regions=tuple(regions[index]),
        )
        for index, account in enumerate(accounts)
    }
