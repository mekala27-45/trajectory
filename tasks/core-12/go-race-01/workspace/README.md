# aggregator

Per endpoint latency aggregation for the request path. One `agg.Registry` is created at
start up and shared for the life of the process.

## Who touches a Registry

- Every request goroutine calls `Observe` when a handler returns. Thousands a second,
  spread over a handful of endpoints.
- One exporter goroutine calls `Endpoints`, `Snapshot`, `SnapshotAll` and `Observed` on a
  one second tick, then `Reset` once the publish succeeds.

## The contract

1. Every call is safe from any goroutine. No caller locks anything.
2. No update is lost. `Observed()` is the number of `Observe` calls that have been taken,
   and the per endpoint counts sum to it.
3. A `Snapshot` never returns a half written aggregate.
4. Reads of the same endpoint run at the same time. `quantile` walks the histogram in
   place and is the expensive part of a read, so a registry that puts every reader in a
   queue turns a scrape of a hundred endpoints into a stall on the exporter tick. One
   exclusive lock around the whole type satisfies points 1 to 3 and breaks this one.
5. `Reset` drops every recorded sample, including anything the registry is holding to one
   side of the map.

## Running it

    export PATH=/usr/local/go/bin:$PATH

    go test -race ./...     # the suite, under the race detector
    go vet ./...
    go run ./cmd/aggd       # synthetic load, prints the exporter's report

There is no network and no module outside the standard library.
