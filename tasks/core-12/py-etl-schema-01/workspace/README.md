# warehouse loader

Loads the vendor's monthly order export into the warehouse format.

```
python3 load.py data/orders_2026_04.csv out/orders.jsonl out/rejects.jsonl
python3 -m pytest -q tests
```

`CONTRACT.md` is the output contract. It is what the warehouse and everything downstream
of it are built against, so the shape of the output is not ours to change.

## State of things

April's export loads. May's does not: the vendor reshaped the file without telling anyone
and the loader falls over on the first row. The contract lists four reject reasons and
this loader has only ever needed one of them, because April's export has never contained
anything worse than a missing price.
