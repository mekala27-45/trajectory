# Model requests

Every real model figure in this repository starts as a file here, so what was asked for,
and under which caps, sits in the same history as the code that produced it.

| file | what changing it on `main` does |
| --- | --- |
| `panel.json` | starts [`model-panel.yml`](../.github/workflows/model-panel.yml): one probe call per model, then one job per model that passed |
| `probe.json` | starts [`model-probe.yml`](../.github/workflows/model-probe.yml): one probe call per candidate, and no task runs |

`scripts/plan_panel.py` validates both before anything is spent. A panel that has run is
copied into `history/` under the date it ran, so the request behind every promoted record
stays readable without digging through commits.

## The panels so far

| request | models | what happened |
| --- | --- | --- |
| [`history/2026-10-25-panel-1.json`](history/2026-10-25-panel-1.json) | minimax-m3.1-flash, gpt-oss-20b, and nemotron-3.5-lightning's four cut seed 1 tasks | Four of the six new models were left out at the probe: one was not served to this key, and three did not answer within 90 seconds. The judge was not served either. Cancelled part way: the runs showed files the agent wrote were owned by root, which the agent could not edit in place. |
| [`history/2026-10-25-panel-2.json`](history/2026-10-25-panel-2.json) | nemotron-3-ultra-550b-a55b, glm-5.3 | Cancelled before it started, for the same fix. |
| `panel.json` | nemotron-3.5-lightning, nemotron-3-ultra, minimax-m3.1-flash, gpt-oss-20b, glm-5.3 | Every model on one harness, with the fix and the hardened verifier. glm-5.3 runs with three times each task's wall clock ceiling, because its provider queue took about 26 seconds a call. |
