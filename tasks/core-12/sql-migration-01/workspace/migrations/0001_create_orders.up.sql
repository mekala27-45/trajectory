-- Applied before you got here. It is what ./db.sh reseed recreates.
CREATE TABLE orders (
    id             bigint      PRIMARY KEY,
    placed_at      timestamptz NOT NULL,
    total_cents    integer     NOT NULL,
    shipped_at     timestamptz,
    cancelled_at   timestamptz,
    refunded_cents integer     NOT NULL DEFAULT 0
);

CREATE INDEX orders_placed_at_idx ON orders (placed_at);
