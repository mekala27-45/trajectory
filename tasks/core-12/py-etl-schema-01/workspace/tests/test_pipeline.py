"""Tests for the warehouse loader.

These cover April's export, which is the only one that has ever loaded. Everything they
assert comes from CONTRACT.md.
"""

import json

from warehouse import FIELDS, transform_file

APRIL = "data/orders_2026_04.csv"


def load(tmp_path, source=APRIL):
    """Run the loader into a temporary directory and read both files back."""
    destination = tmp_path / "orders.jsonl"
    rejects = tmp_path / "rejects.jsonl"
    report = transform_file(source, destination, rejects)
    records = [json.loads(line) for line in destination.read_text().splitlines() if line]
    bad = [json.loads(line) for line in rejects.read_text().splitlines() if line]
    return report, records, bad


def test_every_row_is_accounted_for(tmp_path):
    report, records, bad = load(tmp_path)
    assert report.read == 14
    assert report.written == len(records) == 13
    assert report.rejected == len(bad) == 1
    assert report.read == report.written + report.rejected


def test_records_carry_exactly_the_contract_fields(tmp_path):
    _, records, _ = load(tmp_path)
    for record in records:
        assert list(record) == list(FIELDS)


def test_fields_are_mapped_from_the_source(tmp_path):
    _, records, _ = load(tmp_path)
    first = records[0]
    assert first["order_id"] == "SO-41001"
    assert first["customer_name"] == "Ada Lovelace"
    assert first["region"] == "eu-west"
    assert first["currency"] == "EUR"
    assert first["amount_cents"] == 125000
    assert first["placed_on"] == "2026-04-02"


def test_april_has_no_discounts_so_net_equals_amount(tmp_path):
    _, records, _ = load(tmp_path)
    for record in records:
        assert record["discount_cents"] == 0
        assert record["net_cents"] == record["amount_cents"]


def test_a_row_without_a_price_is_rejected_with_a_reason(tmp_path):
    _, _, bad = load(tmp_path)
    assert bad[0]["line"] == 8
    assert bad[0]["reason"] == "bad_amount"
    assert bad[0]["raw"]["order_id"] == "SO-41007"
