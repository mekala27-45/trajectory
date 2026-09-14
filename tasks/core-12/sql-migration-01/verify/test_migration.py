"""Hidden verification suite for sql-migration-01.

Half of these tests check the result and half check how it was reached, because the
migration that was rejected in review produces exactly the right column. It applies
cleanly, every row ends up with the right value, and inserting a null is refused, so a
suite that only looked at the final state would pass it.

The four that fail on it are the point of the task:

  - test_the_backfill_is_batched rejects one UPDATE over the whole table.
  - test_not_null_arrives_without_a_scan_under_access_exclusive rejects a bare
    SET NOT NULL, which takes ACCESS EXCLUSIVE and then scans the table with it held.
  - test_the_migration_bounds_how_long_it_waits_for_a_lock rejects DDL with no
    lock_timeout and no statement_timeout in front of it, and rejects setting either to
    zero, which is how you satisfy the letter of that rule while disabling it.
  - test_no_statement_runs_longer_than_the_budget measures pg_stat_statements rather than
    reading the SQL, so a backfill that looks batched but still sends one statement over
    the whole table is caught by the clock.

test_the_table_was_never_rewritten guards the other shortcut: copying into a new table
with the column already populated and renaming it over the top. That satisfies every
check on the final state and is a copy of the whole table under a lock that blocks reads.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import time
from pathlib import Path

import pytest

WORKSPACE = Path.cwd()
UP = "migrations/0002_add_status.up.sql"
DOWN = "migrations/0002_add_status.down.sql"

STATEMENT_BUDGET_MS = 750.0
"""Stated in SPEC.md. One UPDATE over the seeded table takes about seven times this."""

SEEDED_ROWS = 600_000
"""Rows seed.sql loads. Checked, because a budget in milliseconds means nothing if the
table it was measured against got smaller."""

STATUS_RULE = """
    CASE
        WHEN cancelled_at IS NOT NULL THEN 'cancelled'
        WHEN refunded_cents > 0 AND refunded_cents >= total_cents THEN 'refunded'
        WHEN refunded_cents > 0 THEN 'partly_refunded'
        WHEN shipped_at IS NOT NULL THEN 'shipped'
        ELSE 'placed'
    END
"""
"""The rule from SPEC.md, so the column is checked against the specification rather than
against whatever the migration happened to write."""

FINGERPRINT = (
    "select count(*)::text || ':' || coalesce(sum(total_cents), 0)::text || ':' "
    "|| coalesce(sum(refunded_cents), 0)::text || ':' || coalesce(sum(id), 0)::text from orders"
)

IDENTITY = "select oid::text || ':' || relfilenode::text from pg_class where relname = 'orders'"


# ---------------------------------------------------------------------- running things


def db(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run db.sh in the workspace. Never raises on a non-zero exit."""
    return subprocess.run(  # noqa: S603
        ["sh", "db.sh", *args],  # noqa: S607
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def scalar(sql: str) -> str:
    """Run a query expected to return one value and return it as text."""
    proc = db("psql", "-At", "-c", sql)
    assert proc.returncode == 0, f"query failed: {sql}\n{proc.stderr.strip()}"
    return proc.stdout.strip()


def scalar_quietly(sql: str) -> str:
    """Same, but an empty string instead of a failure. For use inside the fixture."""
    proc = db("psql", "-At", "-c", sql)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def rows(sql: str) -> list[dict[str, object]]:
    """Run a query wrapped in json_agg and return it as a list of dicts."""
    proc = db("psql", "-At", "-c", f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t")
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []


def read_sql(name: str) -> str:
    """Read a migration file, or return an empty string when it is not there."""
    path = WORKSPACE / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ------------------------------------------------------------------ reading the script


def _scan(sql: str) -> tuple[str, list[str]]:
    """Strip comments and split into top level statements.

    Written out by hand rather than as a regex because the reference answer puts the
    backfill inside a dollar quoted procedure body, and a regex that does not know about
    dollar quoting either chops that body into pieces or swallows the rest of the file.

    Returns:
        The script with its comments removed, and its top level statements.
    """
    out: list[str] = []
    statements: list[str] = []
    index = 0
    size = len(sql)
    while index < size:
        char = sql[index]
        pair = sql[index : index + 2]
        if pair == "--":
            end = sql.find("\n", index)
            index = size if end < 0 else end
            continue
        if pair == "/*":
            depth, index = 1, index + 2
            while index < size and depth:
                if sql[index : index + 2] == "/*":
                    depth, index = depth + 1, index + 2
                elif sql[index : index + 2] == "*/":
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            continue
        if char in "'\"":
            end = index + 1
            while end < size:
                if sql[end] == char:
                    if sql[end : end + 2] == char * 2:
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            out.append(sql[index:end])
            index = end
            continue
        tag = re.match(r"\$[A-Za-z_]\w*\$|\$\$", sql[index:])
        if tag:
            marker = tag.group(0)
            end = sql.find(marker, index + len(marker))
            end = size if end < 0 else end + len(marker)
            out.append(sql[index:end])
            index = end
            continue
        if char == ";":
            statements.append("".join(out).strip())
            out = []
            index += 1
            continue
        out.append(char)
        index += 1
    statements.append("".join(out).strip())

    kept = [statement for statement in statements if statement]
    return ";\n".join(kept), kept


def bare(name: str) -> str:
    """The migration with its comments removed."""
    return _scan(read_sql(name))[0]


def top_level(name: str) -> list[str]:
    """The migration's top level statements, comments removed."""
    return _scan(read_sql(name))[1]


def update_statements(name: str) -> list[str]:
    """Every UPDATE in the file, the ones inside a procedure or DO block included.

    Found by scanning for the keyword rather than by taking whole statements, because the
    interesting UPDATE is usually several plpgsql statements into a block and is not at
    the start of anything. Each hit runs to the next semicolon. Requiring a SET before
    that semicolon is what keeps `SELECT ... FOR UPDATE OF` out of the results.
    """
    script = bare(name)
    found: list[str] = []
    for match in re.finditer(r"(?is)\bupdate\b", script):
        end = script.find(";", match.end())
        chunk = script[match.start() : end if end >= 0 else len(script)]
        if re.match(r"(?is)^update\s+(?:only\s+)?[^;]{0,160}?\bset\b", chunk):
            found.append(chunk.strip())
    return found


# ------------------------------------------------------------------------- applying it


@dataclasses.dataclass(frozen=True)
class Applied:
    """What happened when the up migration was applied to a freshly seeded table."""

    ok: bool
    stderr: str
    seconds: float
    identity_before: str
    identity_after: str
    fingerprint_before: str
    timings: list[dict[str, object]]


@pytest.fixture(scope="module")
def applied() -> Applied:
    """Reseed, apply the up migration once, and record how it went.

    Swallows every failure and reports it through the record it returns. A fixture that
    raised would turn every test depending on it into an error with one shared message,
    and the point of ten tests is to say which parts of the answer hold up.
    """
    db("init")
    db("reseed")
    identity_before = scalar_quietly(IDENTITY)
    fingerprint_before = scalar_quietly(FINGERPRINT)
    db("psql", "-q", "-c", "select pg_stat_statements_reset()")

    started = time.monotonic()
    proc = db("apply", UP)
    seconds = time.monotonic() - started

    return Applied(
        ok=proc.returncode == 0,
        stderr=(proc.stderr or proc.stdout)[-2000:],
        seconds=seconds,
        identity_before=identity_before,
        identity_after=scalar_quietly(IDENTITY),
        fingerprint_before=fingerprint_before,
        timings=rows(
            "select regexp_replace(query, '\\s+', ' ', 'g') as query, "
            "round(max_exec_time::numeric, 2)::float8 as ms, toplevel "
            "from pg_stat_statements where query not ilike '%pg_stat_statements%'"
        ),
    )


# ------------------------------------------------------------------------------ tests


def test_the_up_migration_applies_to_a_freshly_seeded_table(applied: Applied) -> None:
    assert applied.ok, f"./db.sh apply {UP} failed:\n{applied.stderr}"


def test_every_row_has_the_status_the_spec_derives(applied: Applied) -> None:
    assert applied.ok, "the up migration did not apply"
    assert scalar("select count(*) from orders") == str(SEEDED_ROWS), (
        f"orders does not hold the {SEEDED_ROWS} rows seed.sql loads"
    )
    assert scalar("select count(*) from orders where status is null") == "0", (
        "rows still have a null status, so the backfill did not cover the whole table"
    )
    wrong = scalar(f"select count(*) from orders where status is distinct from ({STATUS_RULE})")
    assert wrong == "0", (
        f"{wrong} rows disagree with the ordered rule in SPEC.md. The order matters: an "
        "order that was refunded and then cancelled is cancelled"
    )
    assert scalar("select count(distinct status) from orders") == "5"


def test_a_null_status_is_refused(applied: Applied) -> None:
    assert applied.ok, "the up migration did not apply"
    # Rolled back either way: if the column does allow a null, the row must not be left
    # behind for the tests that come after this one.
    proc = db(
        "psql",
        "-c",
        "begin; insert into orders (id, placed_at, total_cents, status) "
        "values (999000001, now(), 100, null); rollback;",
    )
    assert proc.returncode != 0, "a row with a null status was accepted"
    assert re.search(r"null value|not-null|violates check constraint", proc.stderr, re.I), (
        f"the insert failed for the wrong reason:\n{proc.stderr.strip()}"
    )


def test_the_backfill_is_batched() -> None:
    updates = update_statements(UP)
    assert updates, f"{UP} never writes a value into status with an UPDATE"

    unbounded = [
        statement
        for statement in updates
        if not re.search(r"(?is)\bwhere\b", statement)
        or re.search(r"(?is)\bwhere\s+(true|1\s*=\s*1)\s*$", statement)
    ]
    assert not unbounded, (
        "the backfill sends one UPDATE over every row in the table. That is a single "
        "transaction holding a row lock on all 180 million of them until it finishes, one "
        "snapshot open for the whole run so nothing behind it can be vacuumed, and the "
        "entire table through the write ahead log before anything commits. Bound each "
        "statement to a range of the primary key and commit between batches. First "
        f"offender: {unbounded[0][:200]}"
    )
    assert re.search(r"(?is)\bloop\b", bare(UP)) or len(updates) >= 4, (
        "every UPDATE is bounded but there is only one of them, so only part of the table "
        "is backfilled. Loop over the key range"
    )


def test_not_null_arrives_without_a_scan_under_access_exclusive() -> None:
    script = bare(UP)
    set_not_null = re.search(r"(?is)\balter\s+column\s+\"?status\"?\s+set\s+not\s+null", script)
    not_valid = re.search(r"(?is)\bcheck\s*\((?P<expr>[^;]*?)\)\s*not\s+valid", script)
    validated = re.search(r"(?is)\bvalidate\s+constraint\b", script)

    proves_not_null = bool(
        not_valid
        and "status" in not_valid.group("expr").lower()
        and re.search(r"(?is)\bis\s+not\s+null\b", not_valid.group("expr"))
    )

    if set_not_null is None:
        assert proves_not_null and validated, (
            "nothing in the migration enforces a non null status. Either SET NOT NULL "
            "behind a validated CHECK, or leave the validated CHECK in place as the "
            "enforcement"
        )
        return

    assert proves_not_null and validated, (
        "SET NOT NULL on its own takes ACCESS EXCLUSIVE and then scans every row with it "
        "held, which blocks reads as well as writes for the length of the scan. Add the "
        "constraint NOT VALID first, VALIDATE it (that scan runs under SHARE UPDATE "
        "EXCLUSIVE, which reads and writes do not block on), and SET NOT NULL is then a "
        "catalogue change because the validated constraint already proves the column"
    )
    assert not_valid is not None and not_valid.start() < set_not_null.start(), (
        "the NOT VALID constraint is added after SET NOT NULL, so the scan under ACCESS "
        "EXCLUSIVE still happens"
    )
    assert validated is not None and validated.start() < set_not_null.start(), (
        "VALIDATE CONSTRAINT runs after SET NOT NULL, so the planner has nothing to use "
        "to skip the scan"
    )


def test_the_migration_bounds_how_long_it_waits_for_a_lock() -> None:
    statements = top_level(UP)
    first_lock = next(
        (
            index
            for index, statement in enumerate(statements)
            if re.match(r"(?is)^(alter|create|drop|truncate|reindex|update|insert|call)\b", statement)
        ),
        None,
    )
    assert first_lock is not None, f"{UP} does nothing"

    found: dict[str, str] = {}
    for statement in statements[:first_lock]:
        match = re.match(
            r"(?is)^set\s+(?:local\s+|session\s+)?(lock_timeout|statement_timeout)\s*"
            r"(?:=|to)\s*(?P<value>.+)$",
            statement,
        )
        if match:
            found[match.group(1).lower()] = match.group("value").strip()

    for setting in ("lock_timeout", "statement_timeout"):
        assert setting in found, (
            f"{setting} is not set before the first statement that takes a lock. DDL that "
            "queues for a lock puts every query arriving after it into the same queue, "
            "which is how a metadata change with no rewrite in it takes an application "
            "down. Fail fast and retry instead"
        )
        assert not re.fullmatch(r"'?0\s*(ms|s|min)?'?", found[setting], re.I), (
            f"{setting} is set to {found[setting]}, which disables it"
        )


def test_no_statement_runs_longer_than_the_budget(applied: Applied) -> None:
    assert applied.ok, "the up migration did not apply"
    assert applied.timings, "pg_stat_statements recorded nothing for the migration"
    assert scalar("select count(*) from orders") == str(SEEDED_ROWS), (
        f"orders holds {scalar('select count(*) from orders')} rows rather than "
        f"{SEEDED_ROWS}. The budget was measured against the seeded table, and a "
        "migration that is fast because the table shrank has not been measured at all"
    )

    offenders = sorted(
        (
            entry
            for entry in applied.timings
            if float(entry["ms"]) > STATEMENT_BUDGET_MS
            and not re.match(r"(?is)^\s*(call|do)\b", str(entry["query"]))
        ),
        key=lambda entry: -float(entry["ms"]),
    )
    assert not offenders, (
        f"{len(offenders)} statement(s) ran longer than the {STATEMENT_BUDGET_MS:.0f} ms "
        f"budget in SPEC.md. The worst took {float(offenders[0]['ms']):.0f} ms: "
        f"{str(offenders[0]['query'])[:200]}"
    )


def test_the_table_was_never_rewritten(applied: Applied) -> None:
    assert applied.ok, "the up migration did not apply"
    assert applied.identity_before, "orders was not there to begin with"
    assert applied.identity_before == applied.identity_after, (
        "orders has a different oid or relfilenode than it started with, so the migration "
        f"rewrote or replaced the table ({applied.identity_before} became "
        f"{applied.identity_after}). On 180 million rows that is the whole table copied "
        "under a lock that blocks reads"
    )


def test_the_down_migration_reverses_the_change(applied: Applied) -> None:
    assert applied.ok, "the up migration did not apply, so there is nothing to reverse"
    proc = db("apply", DOWN)
    assert proc.returncode == 0, f"./db.sh apply {DOWN} failed:\n{(proc.stderr or '')[-2000:]}"

    assert (
        scalar(
            "select count(*) from information_schema.columns "
            "where table_name = 'orders' and column_name = 'status'"
        )
        == "0"
    ), "the status column is still there after the down migration"
    assert (
        scalar(
            "select count(*) from pg_constraint where conrelid = 'orders'::regclass "
            "and pg_get_constraintdef(oid) ilike '%status%'"
        )
        == "0"
    ), "the down migration left a constraint behind"
    assert scalar(FINGERPRINT) == applied.fingerprint_before, (
        "the rows are not what 0001 and the seed left behind. A down migration that loses "
        "or rewrites data is not a down migration"
    )


def test_the_up_migration_runs_again_after_the_down_migration() -> None:
    assert db("reseed").returncode == 0, "./db.sh reseed failed"
    first = db("apply", UP)
    assert first.returncode == 0, f"the first apply failed:\n{(first.stderr or '')[-1500:]}"
    reverse = db("apply", DOWN)
    assert reverse.returncode == 0, f"the down migration failed:\n{(reverse.stderr or '')[-1500:]}"
    again = db("apply", UP)
    assert again.returncode == 0, (
        "the up migration does not run a second time after the down migration. A migration "
        f"pair has to survive a rollback and a retry:\n{(again.stderr or '')[-1500:]}"
    )
    assert scalar("select count(*) from orders where status is null") == "0"
    assert (
        scalar(f"select count(*) from orders where status is distinct from ({STATUS_RULE})") == "0"
    )
