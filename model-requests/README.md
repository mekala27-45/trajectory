# Model requests

Every real model figure in this repository starts as a file here, so what was asked for,
and under which caps, sits in the same history as the code that produced it.

| file | what changing it on `main` does |
| --- | --- |
| `panel.json` | starts [`model-panel.yml`](../.github/workflows/model-panel.yml): one probe call per model, then one job per model that passed |
| `probe.json` | starts [`model-probe.yml`](../.github/workflows/model-probe.yml): one probe call per candidate, and no task runs |
| `rejudge.json` | starts [`rejudge.yml`](../.github/workflows/rejudge.yml): the panel's own judge reads again the unsolved runs whose judging failed, and only those |
| `judge.json` | starts [`judge-agreement.yml`](../.github/workflows/judge-agreement.yml): a second judge from another family reads a fixed sample of the runs the first judge read, after any running panel |

`scripts/plan_panel.py` validates the first two, `scripts/rejudge.py` the third and
`scripts/judge_agreement.py` the fourth, before anything is spent. A panel that has run is
copied into `history/` under the date it ran, so the request behind every promoted record
stays readable without digging through commits.

## The panels so far

| request | models | what happened |
| --- | --- | --- |
| [`history/2026-10-25-panel-1.json`](history/2026-10-25-panel-1.json) | minimax-m3.1-flash, gpt-oss-20b, and nemotron-3.5-lightning's four cut seed 1 tasks | Four of the six new models were left out at the probe: one was not served to this key, and three did not answer within 90 seconds. The judge was not served either. Cancelled part way: the runs showed files the agent wrote were owned by root, which the agent could not edit in place. |
| [`history/2026-10-25-panel-2.json`](history/2026-10-25-panel-2.json) | nemotron-3-ultra-550b-a55b, glm-5.3 | Cancelled before it started, for the same fix. |
| [`history/2026-10-25-panel-3.json`](history/2026-10-25-panel-3.json) | the five below | Cancelled within minutes: its records would have said harness 0.2.0 while running code that version never had, so the version was raised to 0.3.0 and the panel restarted. |
| [`history/2026-10-25-panel-4.json`](history/2026-10-25-panel-4.json) | nemotron-3.5-lightning, nemotron-3-ultra, minimax-m3.1-flash, gpt-oss-20b, glm-5.3 | Ran to the end: 120 records, every model on harness 0.3.0 with the fixed write and the hardened verifier, workflow run 40155008047. The free tier ended 48 of them, a timeout or a 429 through every retry or an HTTP 400, 13 before the first step. The judge sent its verdict in a shape the schema refused on 54 of the 55 unsolved runs it read and timed out on the other, so no unsolved run was judged. |
| [`history/2026-10-25-panel-5.json`](history/2026-10-25-panel-5.json) | the same five | Reran exactly the 48 runs the provider ended, at a lower total rate, with a provider that backs off longer after a 429 and a judge that reads the shape it sends, workflow run 40170690917. Each rerun replaced its run at promotion and stands whatever it scored. The provider ended 23 of the 48 again, nine each for gpt-oss-20b and glm-5.3, and they stand as attempts: 22 unsolved, and one whose workspace already passed every hidden test. |
| `panel.json` | the same five | The request panel 5 ran, unchanged. Changing it starts the next panel. |

## The judge requests so far

| request | judge | what happened |
| --- | --- | --- |
| `rejudge.json` | step-5-preview, the panel's own | Read again the 22 unsolved first pass runs still without a verdict after the reruns, and only those, at the same temperature, workflow run 40179971123. All 22 now carry a verdict, and the promotion checked that nothing in any record changed but the judge's own output. |
| [`history/2026-10-25-judge-1.json`](history/2026-10-25-judge-1.json) | deepseek-v4.1-flash, then gemma-4-31b-it, then laguna-xs-2.1 | Asked a second judge, from a family that is neither the first judge's nor any agent's, to read all 50 runs the first judge read, workflow run 40184208378. It hit the step's 100 minute limit with nothing written: deepseek-v4.1-flash timed out three times at 180 seconds on the first run and was passed over, and gemma-4-31b-it, which answered that run, timed out on run after run. |
| `judge.json` | the same three, in the same order | The same measurement with room for a slow queue: a call may wait 420 seconds, six workers share 20 requests a minute, and the script sends no new run after 80 minutes and names any it did not reach. |
