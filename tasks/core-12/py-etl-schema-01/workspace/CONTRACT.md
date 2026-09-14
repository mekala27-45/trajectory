# Warehouse format contract

The loader turns a vendor order export (CSV) into the warehouse format, plus a rejects
file. Everything downstream of the warehouse reads this contract and nothing else, so it
is the part of this repository that is not allowed to drift.

## The warehouse format

JSON Lines: one JSON object per accepted row, in the order the rows appear in the source
file, UTF-8, newline terminated.

Each object has exactly these eight fields, in this order:

| field           | type    | meaning                                                     |
| --------------- | ------- | ----------------------------------------------------------- |
| `order_id`      | string  | Vendor order identifier. Never empty.                       |
| `customer_name` | string  | Customer's full name, given name first, single spaces.       |
| `region`        | string  | Region the order was placed in, as the vendor codes it.     |
| `currency`      | string  | ISO currency code, as the vendor sends it.                   |
| `amount_cents`  | integer | Order total in minor units, before discount.                |
| `discount_cents`| integer | Discount in minor units. Zero when the source has none.     |
| `net_cents`     | integer | `amount_cents - discount_cents`.                            |
| `placed_on`     | string  | Date the order was placed, `YYYY-MM-DD`.                    |

No other field may appear, and none of the eight may be missing. Nothing downstream reads
a ninth field, and a missing one is a null in a warehouse column that is declared NOT
NULL.

## Rejects

A row that cannot be turned into a record is written to the rejects file rather than
dropped. Same format, one JSON object per rejected row, with exactly these fields:

| field    | type    | meaning                                                          |
| -------- | ------- | ---------------------------------------------------------------- |
| `line`   | integer | Line number of the row in the source file, header counted as 1.  |
| `reason` | string  | One of the reason codes below.                                   |
| `raw`    | object  | The source row as read, source field name to string value.       |

Reason codes:

| code               | when                                                       |
| ------------------ | ---------------------------------------------------------- |
| `missing_order_id` | `order_id` is empty or whitespace                          |
| `bad_amount`       | the order total is not an integer                          |
| `bad_discount`     | the discount is present but is not an integer              |
| `bad_date`         | the placed date cannot be parsed in the source's format    |

Checked in that order, so a row with two problems is reported under the first one.

## Invariants

1. Every data row of the source appears exactly once, either in the warehouse file or in
   the rejects file. `read == written + rejected`, and no row is silently dropped.
2. Output row order matches source row order.
3. Loading the same file twice produces the same two files.

## Source layouts

Both are supported. The loader picks the layout from the header and refuses a header that
matches neither, with a `ValueError`.

April 2026 and earlier:

```
order_id,customer_name,region_code,currency,amount_cents,placed_at
```

`placed_at` is `YYYY-MM-DD`. There is no discount column, so `discount_cents` is 0 for
every row.

May 2026 onward, which is what the vendor sends now:

```
order_id,customer_first_name,customer_last_name,market,currency,amount_cents,discount_cents,placed_at
```

`market` is the old `region_code` under a new name. The customer's name arrives in two
columns. `discount_cents` is new and is empty when the order had no discount. `placed_at`
is `DD/MM/YYYY` in this layout, which is not the format the warehouse stores.
