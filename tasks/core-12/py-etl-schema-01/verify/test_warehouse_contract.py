"""Hidden verification suite for py-etl-schema-01.

The contract in CONTRACT.md is the thing being verified, field by field, on both the old
export and the new one.

Several of these exist to rule out fixes that make the crash go away and cost rows or
change the output. Wrapping the row loop in a blanket except and continuing is caught by
the coverage and checksum assertions, which rebuild the expected sequence of order ids
from the source file. Emitting the vendor's new column names, or adding a ninth field
because the new export has one, is caught by the field set assertion. Passing the new
DD/MM/YYYY dates through, or reading them as MM/DD/YYYY, is caught by the date assertions,
which is why the fixture contains days past the twelfth. Fixing the new layout by breaking
the old one is caught by loading April's export as well, because backward compatibility is
part of the contract rather than a courtesy.
"""

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path.cwd()
sys.path.insert(0, str(WORKSPACE / "src"))

from warehouse import FIELDS, transform_file  # noqa: E402

APRIL = WORKSPACE / "data" / "orders_2026_04.csv"
MAY = WORKSPACE / "data" / "orders_2026_05.csv"

CONTRACT_FIELDS = [
    "order_id",
    "customer_name",
    "region",
    "currency",
    "amount_cents",
    "discount_cents",
    "net_cents",
    "placed_on",
]
INTEGER_FIELDS = ("amount_cents", "discount_cents", "net_cents")
REASONS = ("missing_order_id", "bad_amount", "bad_discount", "bad_date")


def source_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    """Read a source export independently of the loader, with line numbers."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(enumerate(csv.DictReader(handle), start=2))


def run(tmp_path: Path, source: Path) -> tuple[object, list[dict], list[dict]]:
    """Load one export into a temporary directory and read both outputs back."""
    destination = tmp_path / "orders.jsonl"
    rejects = tmp_path / "rejects.jsonl"
    report = transform_file(source, destination, rejects)
    records = [json.loads(line) for line in destination.read_text().splitlines() if line.strip()]
    bad = [json.loads(line) for line in rejects.read_text().splitlines() if line.strip()]
    return report, records, bad


def digest(values) -> str:
    """Checksum a sequence of key values so a dropped row cannot hide in a count."""
    return hashlib.sha256("|".join(values).encode()).hexdigest()


def assert_no_row_was_lost(source: Path, report, records: list[dict], bad: list[dict]) -> None:
    """Assert every data row of the source came out exactly once, in order.

    The expected sequence of accepted order ids is rebuilt from the source file and the
    lines the loader says it rejected, so this holds the loader to the source rather than
    to a number written down here.
    """
    rows = source_rows(source)
    assert report.read == len(rows), f"loader read {report.read} rows, the file has {len(rows)}"
    assert report.written == len(records), "the report disagrees with the warehouse file"
    assert report.rejected == len(bad), "the report disagrees with the rejects file"
    assert report.read == report.written + report.rejected, (
        f"{report.read} rows in, {report.written} written and {report.rejected} rejected. "
        "Rows are being dropped"
    )

    by_line = {line: row for line, row in rows}
    reject_lines = [entry["line"] for entry in bad]
    assert len(set(reject_lines)) == len(reject_lines), "a row was rejected twice"
    for entry in bad:
        assert entry["line"] in by_line, (
            f"rejects file names line {entry['line']}, which has no row"
        )
        expected_id = by_line[entry["line"]]["order_id"]
        assert entry["raw"].get("order_id") == expected_id, (
            f"reject for line {entry['line']} carries a raw row from somewhere else"
        )

    kept = [by_line[line]["order_id"] for line in sorted(set(by_line) - set(reject_lines))]
    produced = [record["order_id"] for record in records]
    assert produced == kept, (
        "the warehouse file is not the source's accepted rows in source order: expected "
        f"{kept[:6]}... and got {produced[:6]}..."
    )
    assert digest(produced) == digest(kept), "checksum of the accepted order ids does not match"


def test_the_may_export_accounts_for_every_row(tmp_path):
    report, records, bad = run(tmp_path, MAY)
    assert_no_row_was_lost(MAY, report, records, bad)
    assert report.read == 18
    assert report.written == 14
    assert report.rejected == 4


def test_may_records_carry_exactly_the_contract_fields(tmp_path):
    _, records, _ = run(tmp_path, MAY)
    assert records, "the May export produced no records at all"
    for record in records:
        assert list(record) == CONTRACT_FIELDS, (
            f"record for {record.get('order_id')} has fields {list(record)}, and the "
            f"contract is {CONTRACT_FIELDS}"
        )
        assert list(FIELDS) == CONTRACT_FIELDS, "the library's FIELDS no longer match the contract"
        for field in INTEGER_FIELDS:
            value = record[field]
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"{field} came out as {type(value).__name__}, and the contract says integer"
            )
        for field in ("order_id", "customer_name", "region", "currency", "placed_on"):
            assert isinstance(record[field], str), f"{field} came out as a non string"
        assert record["order_id"].strip(), "an empty order_id reached the warehouse"


def test_the_renamed_and_split_columns_are_mapped(tmp_path):
    _, records, _ = run(tmp_path, MAY)
    by_id = {record["order_id"]: record for record in records}

    # market is the old region_code under a new name.
    rows = {row["order_id"]: row for _, row in source_rows(MAY)}
    for order_id, record in by_id.items():
        assert record["region"] == rows[order_id]["market"], f"{order_id} lost its region"
        assert record["currency"] == rows[order_id]["currency"]

    # The name arrives in two columns and the warehouse stores one.
    assert by_id["SO-52001"]["customer_name"] == "Radia Perlman"
    assert by_id["SO-52017"]["customer_name"] == "Tim Berners-Lee"
    assert by_id["SO-52011"]["customer_name"] == "Karen Sparck Jones"
    for order_id, record in by_id.items():
        row = rows[order_id]
        expected_name = f"{row['customer_first_name']} {row['customer_last_name']}"
        assert record["customer_name"] == expected_name


def test_the_new_discount_column_is_handled(tmp_path):
    _, records, _ = run(tmp_path, MAY)
    by_id = {record["order_id"]: record for record in records}

    assert by_id["SO-52001"]["discount_cents"] == 2000
    assert by_id["SO-52001"]["net_cents"] == 140000
    # An empty discount column is zero, not null and not a crash.
    assert by_id["SO-52002"]["discount_cents"] == 0
    assert by_id["SO-52002"]["net_cents"] == 86400
    assert by_id["SO-52003"]["net_cents"] == 515000

    for record in records:
        assert record["net_cents"] == record["amount_cents"] - record["discount_cents"]


def test_dates_are_normalised_to_the_contract_format(tmp_path):
    _, records, _ = run(tmp_path, MAY)
    by_id = {record["order_id"]: record for record in records}

    assert by_id["SO-52001"]["placed_on"] == "2026-05-04"
    assert by_id["SO-52005"]["placed_on"] == "2026-05-13"
    assert by_id["SO-52017"]["placed_on"] == "2026-05-29"
    assert by_id["SO-52018"]["placed_on"] == "2026-05-30"

    for record in records:
        placed = record["placed_on"]
        year, month, day = placed.split("-")
        assert (len(year), len(month), len(day)) == (4, 2, 2), f"{placed} is not YYYY-MM-DD"
        assert year == "2026"
        # Every order in this export was placed in May. A month that is not 05 means the
        # day and the month were read the wrong way round.
        assert month == "05", f"{record['order_id']} was placed in month {month}, not May"


def test_unloadable_rows_are_rejected_with_a_reason(tmp_path):
    _, _, bad = run(tmp_path, MAY)
    by_line = {entry["line"]: entry for entry in bad}
    assert sorted(by_line) == [8, 10, 13, 16], (
        f"expected the four unloadable rows at lines 8, 10, 13 and 16, got {sorted(by_line)}"
    )
    assert by_line[8]["reason"] == "bad_amount"
    assert by_line[10]["reason"] == "missing_order_id"
    assert by_line[13]["reason"] == "bad_discount"
    assert by_line[16]["reason"] == "bad_date"

    for entry in bad:
        assert sorted(entry) == ["line", "raw", "reason"], f"reject has fields {sorted(entry)}"
        assert entry["reason"] in REASONS
        assert isinstance(entry["raw"], dict) and entry["raw"], "the rejected row was not kept"
    assert by_line[8]["raw"]["amount_cents"] == "not-yet-priced"
    assert by_line[16]["raw"]["placed_at"] == "32/05/2026"


def test_the_april_export_still_loads(tmp_path):
    report, records, bad = run(tmp_path, APRIL)
    assert_no_row_was_lost(APRIL, report, records, bad)
    assert (report.read, report.written, report.rejected) == (14, 13, 1)

    by_id = {record["order_id"]: record for record in records}
    assert list(records[0]) == CONTRACT_FIELDS
    assert by_id["SO-41001"]["customer_name"] == "Ada Lovelace"
    assert by_id["SO-41001"]["region"] == "eu-west"
    assert by_id["SO-41001"]["placed_on"] == "2026-04-02"
    assert by_id["SO-41013"]["placed_on"] == "2026-04-28"
    for record in records:
        # April's layout has no discount column, so every discount is zero.
        assert record["discount_cents"] == 0
        assert record["net_cents"] == record["amount_cents"]
    assert bad[0]["line"] == 8
    assert bad[0]["reason"] == "bad_amount"


def test_loading_the_same_export_twice_gives_the_same_files(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    report_one, records_one, bad_one = run(first, MAY)
    report_two, records_two, bad_two = run(second, MAY)
    assert records_one == records_two
    assert bad_one == bad_two
    assert report_one == report_two


def test_an_unrecognised_header_is_refused(tmp_path):
    junk = tmp_path / "mystery.csv"
    junk.write_text("id,name,total\n1,Ada,100\n", encoding="utf-8")
    try:
        transform_file(junk, tmp_path / "out.jsonl", tmp_path / "bad.jsonl")
    except ValueError:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"an unknown header raised {type(exc).__name__}, and the contract says ValueError"
        ) from exc
    raise AssertionError(
        "an unknown header loaded without complaint. A layout nobody has mapped has to be "
        "refused, not guessed at"
    )


def test_the_command_line_entry_point_still_works(tmp_path):
    destination = tmp_path / "orders.jsonl"
    rejects = tmp_path / "rejects.jsonl"
    completed = subprocess.run(
        [sys.executable, "load.py", str(MAY), str(destination), str(rejects)],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    returncode = completed.returncode
    assert returncode == 0, f"load.py exited {returncode}: {completed.stderr[-1500:]}"
    records = [json.loads(line) for line in destination.read_text().splitlines() if line.strip()]
    bad = [json.loads(line) for line in rejects.read_text().splitlines() if line.strip()]
    assert len(records) == 14
    assert len(bad) == 4
    assert list(records[0]) == CONTRACT_FIELDS
