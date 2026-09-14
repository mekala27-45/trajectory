-- Rejected in review. See README.md.
ALTER TABLE orders ADD COLUMN status text;

UPDATE orders
   SET status = CASE
                    WHEN cancelled_at IS NOT NULL THEN 'cancelled'
                    WHEN refunded_cents > 0 AND refunded_cents >= total_cents THEN 'refunded'
                    WHEN refunded_cents > 0 THEN 'partly_refunded'
                    WHEN shipped_at IS NOT NULL THEN 'shipped'
                    ELSE 'placed'
                END;

ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
