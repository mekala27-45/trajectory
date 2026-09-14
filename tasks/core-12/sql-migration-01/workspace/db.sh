#!/bin/sh
# Cluster control for this workspace.
#
# There is no system PostgreSQL service and no network here. The cluster lives in
# ./pgdata, listens on a unix socket inside that directory and nothing else, and dies with
# the workspace. The migration runner and the review harness both go through this script,
# so "the database" has exactly one definition.
#
#   ./db.sh init             create the cluster, start it, apply 0001, load the seed rows
#   ./db.sh start            start it if it is not already up
#   ./db.sh stop             stop it
#   ./db.sh status
#   ./db.sh reseed           drop the table, recreate it from 0001, reload the seed rows
#   ./db.sh apply FILE       run one .sql file with ON_ERROR_STOP set
#   ./db.sh psql ARGS...     psql against the cluster: ./db.sh psql -c 'select count(*) from orders'
set -eu

PGBIN=/usr/lib/postgresql/16/bin
PGDATA="$PWD/pgdata"
PGPORT=55432
PGDB=appdb
UNPRIV_USER=postgres

if [ "$(id -u)" -eq 0 ]; then
    # PostgreSQL refuses to run as root and so does initdb. A shell that starts as root
    # has to hand the cluster to an unprivileged account, which is the same move the
    # official images make at the top of their entrypoint. Everything past this line runs
    # as $UNPRIV_USER, psql included, so the role name does not depend on who called.
    chmod o+x .. 2>/dev/null || true
    chown -R "$UNPRIV_USER" . 2>/dev/null || true
    exec setpriv --reuid="$UNPRIV_USER" --regid="$UNPRIV_USER" --clear-groups /bin/sh "$0" "$@"
fi

run_psql() {
    "$PGBIN/psql" --no-psqlrc -v ON_ERROR_STOP=1 -h "$PGDATA" -p "$PGPORT" -d "$PGDB" "$@"
}

is_up() {
    "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1
}

do_start() {
    if is_up; then
        return 0
    fi
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w -t 60 start >/dev/null
}

do_stop() {
    if is_up; then
        "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w -t 60 stop >/dev/null
    fi
}

do_seed() {
    run_psql -q -f migrations/0001_create_orders.up.sql
    run_psql -q -f seed.sql
    run_psql -q -c 'ANALYZE orders'
}

case "${1:-}" in
init)
    if [ ! -f "$PGDATA/PG_VERSION" ]; then
        # --wal-segsize keeps the write ahead log in 1MB files instead of 16MB ones, which
        # is the difference between a throwaway cluster of 80MB and one of half a gigabyte.
        "$PGBIN/initdb" -D "$PGDATA" --no-sync --wal-segsize=1 --auth-local=trust \
            --auth-host=reject --locale=C --encoding=UTF8 >/dev/null
        cat >>"$PGDATA/postgresql.conf" <<CONF

# Added by db.sh. A disposable single user cluster, tuned for repeatable timings rather
# than for durability: nothing here survives the workspace.
listen_addresses = ''
unix_socket_directories = '$PGDATA'
port = $PGPORT
fsync = off
synchronous_commit = off
full_page_writes = off
wal_level = minimal
max_wal_senders = 0
max_wal_size = '96MB'
shared_buffers = '128MB'
autovacuum = off
max_parallel_workers_per_gather = 0
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all
pg_stat_statements.max = 2000
track_activity_query_size = 4096
log_min_duration_statement = 0
log_line_prefix = '%m [%p] '
CONF
        do_start
        run_psql -q -d postgres -c "CREATE DATABASE $PGDB"
        run_psql -q -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements'
        do_seed
    else
        do_start
    fi
    ;;
start)
    do_start
    ;;
stop)
    do_stop
    ;;
status)
    "$PGBIN/pg_ctl" -D "$PGDATA" status
    ;;
reseed)
    do_start
    run_psql -q -f migrations/0001_create_orders.down.sql
    do_seed
    ;;
apply)
    if [ $# -lt 2 ]; then
        echo "usage: ./db.sh apply FILE" >&2
        exit 2
    fi
    do_start
    run_psql -f "$2"
    ;;
psql)
    shift
    do_start
    run_psql "$@"
    ;;
*)
    sed -n '2,14p' "$0"
    exit 2
    ;;
esac
