-- Seed rows for the local cluster, as close to the shape of production as a generator
-- gets: most orders ship, about one in twenty three is cancelled, and refunds are split
-- between full and partial. Overlaps are deliberate, an order can be both refunded and
-- cancelled, which is why the status rule in SPEC.md is ordered.
INSERT INTO orders (id, placed_at, total_cents, shipped_at, cancelled_at, refunded_cents)
SELECT
    g,
    timestamptz '2026-01-01 00:00:00+00' + ((g % 86400) * interval '1 second'),
    500 + (g * 37) % 250000,
    CASE WHEN g % 7 <> 0
         THEN timestamptz '2026-01-04 00:00:00+00' + ((g % 3600) * interval '1 second')
    END,
    CASE WHEN g % 23 = 0
         THEN timestamptz '2026-01-06 00:00:00+00'
    END,
    CASE WHEN g % 11 = 0 THEN 500 + (g * 37) % 250000
         WHEN g % 13 = 0 THEN 100
         ELSE 0
    END
FROM generate_series(1, 600000) AS g;
