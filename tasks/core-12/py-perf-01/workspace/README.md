# rollup

Daily rollup of the payments ledger for the finance export.

`summarise(rows)` turns a day of ledger rows into one `AccountSummary` per account:

| field         | meaning                                                          |
| ------------- | ---------------------------------------------------------------- |
| `account`     | account identifier                                               |
| `count`       | number of ledger rows for the account                            |
| `total_cents` | sum of the amounts, refunds included, so it can be negative      |
| `max_cents`   | largest single amount                                            |
| `regions`     | distinct regions the account transacted in, in first seen order  |

The returned mapping is keyed in the order the accounts first appear in the ledger. The
export downstream depends on that order, so it is part of the contract and not an
accident of the implementation.

## Budget

The export runs the rollup on every ledger it receives and has to hand the result on
inside its own window, so `summarise` gets two seconds for a 200,000 row day. Reading the
CSV is not part of that budget.

```
python3 tools/make_fixture.py     # writes data/ledger.csv, 200,000 rows
python3 bench.py                  # times summarise against the budget
python3 -m pytest -q tests        # correctness
```

`bench.py` exits non-zero when the rollup is over budget.
