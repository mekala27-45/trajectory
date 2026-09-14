# orders migrations

Schema for the orders service. `migrations/` is applied in file order by

    ./db.sh apply migrations/<file>

which is `psql -v ON_ERROR_STOP=1 -f`, so each statement runs in its own implicit
transaction. The runner does not wrap a file in a transaction block, on purpose: a batched
backfill has to be able to commit between batches, and a procedure that commits cannot run
inside a transaction block. Do not add `BEGIN` or `COMMIT` around a whole file.

## The local cluster

There is no PostgreSQL service and no network. `db.sh` owns a throwaway cluster in
`./pgdata` that talks over a unix socket inside that directory:

    ./db.sh init      create it, start it, apply 0001, load 600,000 seed rows
    ./db.sh psql -c 'select count(*) from orders'
    ./db.sh apply migrations/0002_add_status.up.sql
    ./db.sh reseed    back to the state 0001 leaves behind, seed rows included
    ./db.sh stop

It is already initialised and running. Go through `db.sh` rather than calling `psql`
directly, so that the role and the socket path are the same for everyone.

The cluster loads `pg_stat_statements` with `track = all`, so `max_exec_time` per statement
is readable after a migration, nested statements included:

    ./db.sh psql -c "select toplevel, calls, round(max_exec_time::numeric,1) ms, query
                       from pg_stat_statements order by max_exec_time desc limit 10"

`select pg_stat_statements_reset()` before a run you want to measure.

## 0002, as it stands

`0002_add_status.up.sql` and `0002_add_status.down.sql` add and backfill `orders.status`.
They produce the right answer on the local table and they were rejected in review:

> This is three statements and two of them are outages. The `UPDATE` is one transaction
> over the whole table: on the production table that is twenty minutes of row locks, one
> snapshot held open for all of it, and the entire table through the write ahead log.
> The `SET NOT NULL` then takes `ACCESS EXCLUSIVE` and scans 180 million rows while
> holding it, which stops reads as well as writes. Neither statement bounds how long it
> will wait for a lock, so either one can queue behind a slow query and take every query
> that arrives after it into the same queue.
>
> Same column, same values, same two files. SPEC.md has what I will be checking.

Rewrite both files. `SPEC.md` has the column's definition and the five rules the review
applies.
