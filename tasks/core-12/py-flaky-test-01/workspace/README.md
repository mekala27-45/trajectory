# sampler

Sampling helpers for the request auditor.

`sample_requests` draws a sample of production requests for a human reviewer.
`retry_delays` builds the jittered backoff schedule the uploader uses. Both are random on
purpose: a fixed sample keeps missing whatever it missed last time, and a backoff without
jitter synchronises every retrying client.

## Running the tests

```
python -m pytest -q tests
```

## Known problem

`tests/test_sampler.py::test_sample_covers_every_tenant` fails on CI roughly one run in
four. It passes when rerun, which is why it has been ignored for two months. It is now
blocking a merge queue, so it has to be fixed rather than rerun.
