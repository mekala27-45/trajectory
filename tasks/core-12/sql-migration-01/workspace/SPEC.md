# orders.status

## What the column is

`orders.status` is a text column, `NOT NULL`, holding exactly one of:

    cancelled  refunded  partly_refunded  shipped  placed

## How it is derived

The rule is ordered. The first line that matches wins, which matters: an order can be
both refunded and cancelled, and a cancelled order is `cancelled`.

| condition                                             | status            |
|-------------------------------------------------------|-------------------|
| `cancelled_at IS NOT NULL`                            | `cancelled`       |
| `refunded_cents > 0 AND refunded_cents >= total_cents`| `refunded`        |
| `refunded_cents > 0`                                  | `partly_refunded` |
| `shipped_at IS NOT NULL`                              | `shipped`         |
| anything else                                         | `placed`          |

## What the review measures

The local table has 600,000 rows. The production one has 180 million and takes writes the
whole time the migration is running, so every rule below is about the lock, not the result.

1. **No statement runs longer than 750 ms.** Every statement the server executes is timed,
   including the ones inside a `DO` block or a procedure. A `CALL` or `DO` is not itself
   timed against the budget: it is a driver for the statements inside it, and those are
   what hold locks.
2. **The backfill is batched.** One `UPDATE` over the whole table holds a row lock on every
   row it has touched until it finishes, keeps one snapshot open for its duration, and
   writes the entire table to the write ahead log in one go. Batches bounded by primary
   key, each its own transaction, do not.
3. **`lock_timeout` and `statement_timeout` are set, to something other than zero, before
   the first DDL statement.** A DDL statement that queues for a lock puts every later
   query on the table behind it in the same queue. Failing fast and retrying is the
   difference between a slow migration and an outage.
4. **`NOT NULL` arrives without a scan under `ACCESS EXCLUSIVE`.** `ALTER COLUMN ... SET
   NOT NULL` on its own takes `ACCESS EXCLUSIVE` and then scans the whole table while
   holding it. A `CHECK` constraint added `NOT VALID` takes that lock for a catalogue write
   only, `VALIDATE CONSTRAINT` does the scan under `SHARE UPDATE EXCLUSIVE`, which reads
   and writes do not block on, and `SET NOT NULL` is then a catalogue change because the
   validated constraint already proves the column.
5. **The table is not rewritten and not replaced.** Its `oid` and its `relfilenode` are the
   same before and after. Copying into a new table and renaming it is a rewrite of 180
   million rows under a lock that blocks everything.

Both files are checked. The down migration has to leave the table exactly as 0001 built
it, and the up migration has to run again afterwards.
