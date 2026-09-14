"""Tests for the payments rollup.

Small, hand worked ledgers. These say what the rollup means, not how fast it is.
"""

from rollup import Row, summarise


def test_rolls_up_a_single_account():
    rows = [
        Row("acct-1", "eu-west", 1000, "2026-06-01"),
        Row("acct-1", "eu-west", 2500, "2026-06-01"),
        Row("acct-1", "us-east", 400, "2026-06-02"),
    ]
    summary = summarise(rows)["acct-1"]
    assert summary.count == 3
    assert summary.total_cents == 3900
    assert summary.max_cents == 2500
    assert summary.regions == ("eu-west", "us-east")


def test_accounts_are_keyed_in_first_seen_order():
    rows = [
        Row("zeta", "eu-west", 100, "2026-06-01"),
        Row("alpha", "eu-west", 100, "2026-06-01"),
        Row("zeta", "eu-west", 100, "2026-06-02"),
        Row("mid", "us-east", 100, "2026-06-02"),
    ]
    assert list(summarise(rows)) == ["zeta", "alpha", "mid"]


def test_refunds_lower_the_total_and_do_not_become_the_peak():
    rows = [
        Row("acct-2", "eu-north", 5000, "2026-06-03"),
        Row("acct-2", "eu-north", -1500, "2026-06-04"),
    ]
    summary = summarise(rows)["acct-2"]
    assert summary.total_cents == 3500
    assert summary.max_cents == 5000


def test_an_account_of_only_refunds_keeps_its_largest_refund_as_the_peak():
    rows = [
        Row("acct-3", "sa-east", -900, "2026-06-05"),
        Row("acct-3", "sa-east", -200, "2026-06-06"),
    ]
    summary = summarise(rows)["acct-3"]
    assert summary.total_cents == -1100
    assert summary.max_cents == -200


def test_an_empty_ledger_rolls_up_to_nothing():
    assert summarise([]) == {}
