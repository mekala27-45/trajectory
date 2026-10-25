# Real model results

[RESULTS.md](RESULTS.md) is five scripted policies, which validate the harness and say
nothing about a model. This page is the other kind of result: five real language models
driving the same twelve tasks, end to end, on the Docker backend, from one committed
request, on one harness version.

## How it was measured

| | |
| --- | --- |
| Models | `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3-ultra-550b-a55b`, `minimaxai/minimax-m3.1-flash`, `openai/gpt-oss-20b`, `z-ai/glm-5.3` |
| Publishers | four: NVIDIA (two sizes), MiniMax, OpenAI's open weights, Z.ai |
| Provider | NVIDIA build, the free tier, no payment method |
| Backend | Docker. The agent works in one container; the hidden tests run in a fresh one holding only its final workspace, beside a planted test that has to fail |
| Tasks | all twelve in `core-12` |
| Seeds | two, 0 and 1, temperature 0.7 |
| Caps | each model its own job, its own call ceiling set to the most calls its runs could make, and an even share of a 34 request a minute limit |
| Judge | `stepfun-ai/step-5-preview`, a fifth publisher, on every unsolved run |
| Harness | `0.3.0`; every record names the commit it ran from |
| Request | [`model-requests/history/2026-10-25-panel-4.json`](model-requests/history/2026-10-25-panel-4.json), run by [`model-panel.yml`](.github/workflows/model-panel.yml) as workflow run 40155008047 |
| Reruns | the 48 runs the provider ended, run once more by [`model-requests/panel.json`](model-requests/panel.json) as workflow run 40170690917; see below |
| Provenance | [`fixtures/model-runs.provenance.json`](fixtures/model-runs.provenance.json) |

`glm-5.3` answered in about 26 seconds a call on the free tier, so its runs had three times
each task's wall clock ceiling and four workers. The step ceiling, which bounds the work an
agent can do, is the same for every model.

**The provider ended 48 of the first 120 runs**, 13 of them before the first step: a call
failed with a timeout or a 429 through every retry, or the provider answered HTTP 400. Those
records measure the free tier at the time, not the model, so exactly those 48 were run once
more, under a rule fixed before any rerun was read: each rerun replaces the run with the
same model, task and seed, only a run the provider ended can be replaced, and the rerun
stands whatever it scores. The provider ended 23 of the reruns too, and they stand. The
provenance file lists every replacement with the original run and its error, and the
original records stay on the panel's branch.

The records were rescored at promotion with the scoring code of the promoting commit, which
the provenance file names. That applied the recovery rate fix of 25 October, made after the
panel started, the same way it was applied to the scripted matrix. Rescoring reads the
record; no trajectory changed.

Every figure in the measured block below is rendered from the run records committed under
[`fixtures/model-runs`](fixtures/model-runs) and checked in CI by
[`scripts/check_model_numbers.py`](scripts/check_model_numbers.py). A number here that
drifts from its records fails the build, the same guarantee the published matrix has.

Run a panel of your own by committing a request. The workflow probes each model with one
call, runs every model that answered in its own job, and pushes the records to a branch for
review; `scripts/promote_panel.py` is the reviewed step that puts them here.

## Measured

<!-- measured: rendered from fixtures/model-runs by scripts/check_model_numbers.py -->

- Harness version: `0.3.0`
- Runs: 120 (70 solved, 50 unsolved)
- Models: nvidia_nim/minimaxai/minimax-m3.1-flash, nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b, nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b, nvidia_nim/openai/gpt-oss-20b, nvidia_nim/z-ai/glm-5.3
- Tasks: 12
- Seeds: [0, 1]
- Backends: docker
- Total provider spend: 0.0000 USD
- Total wall clock: 100133s

### Leaderboard

Every rate is a mean across seeds with one population standard deviation; cost and wall clock are plain means. A pass rate without that spread is not a result.

| model | runs | solve rate | partial credit | step efficiency | tool validity | redundancy | recovery | premature finish | cost per solved | mean wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | 24 | 87.5% +/- 4.2% | 0.929 +/- 0.004 | 0.442 +/- 0.004 | 1.000 +/- 0.000 | 0.249 +/- 0.002 | 0.997 +/- 0.003 | 4.2% +/- 4.2% | n/a | 530.0s |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | 24 | 66.7% +/- 0.0% | 0.775 +/- 0.000 | 0.400 +/- 0.001 | 0.981 +/- 0.002 | 0.012 +/- 0.007 | 1.000 +/- 0.000 | 0.0% +/- 0.0% | n/a | 828.9s |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 24 | 50.0% +/- 8.3% | 0.790 +/- 0.047 | 0.460 +/- 0.018 | 0.983 +/- 0.011 | 0.198 +/- 0.024 | 0.988 +/- 0.013 | 20.8% +/- 12.5% | n/a | 382.0s |
| `nvidia_nim/openai/gpt-oss-20b` | 24 | 45.8% +/- 4.2% | 0.650 +/- 0.019 | 0.527 +/- 0.025 | 0.643 +/- 0.036 | 0.166 +/- 0.006 | 0.944 +/- 0.056 | 8.3% +/- 8.3% | n/a | 459.7s |
| `nvidia_nim/z-ai/glm-5.3` | 24 | 41.7% +/- 8.3% | 0.662 +/- 0.035 | 0.422 +/- 0.003 | 0.980 +/- 0.002 | 0.019 +/- 0.007 | 1.000 +/- 0.000 | 0.0% +/- 0.0% | n/a | 1971.6s |

### Failure modes

Share is of the 50 unsolved run(s), not of all runs.

| id | mode | runs | share of unsolved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 15 | 30.0% |
| F02 | path hallucination | 8 | 16.0% |
| F03 | premature success | 8 | 16.0% |
| F06 | ignored test output | 4 | 8.0% |
| F07 | long horizon context loss | 3 | 6.0% |
| F04 | retry loop | 1 | 2.0% |

### Failure modes on runs that passed

This is the view a pass rate cannot produce. A run that made malformed tool calls, invented paths, or reached for a destructive command and still got the hidden tests green is a process problem that succeeded, and it is invisible in any aggregate that only looks at failures.

Share is of the 70 solved run(s).

| id | mode | runs | share of solved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 29 | 41.4% |
| F02 | path hallucination | 8 | 11.4% |
| F08 | destructive action | 8 | 11.4% |
| F09 | scope creep | 2 | 2.9% |

### How sure

Each interval is a 95% two stage bootstrap over tasks, then over each task's seeds, with every task weighing the same. It answers whether a number would hold on tasks the suite does not have, which the seed spread above cannot. pass@k is the unbiased estimate that at least one of k samples of a task solves it, averaged over tasks.

| model | tasks | runs | solve rate, 95% interval | partial credit, 95% interval | pass@1 | pass@2 |
| --- | --- | --- | --- | --- | --- | --- |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | 12 | 24 | 66.7% [41.7, 91.7] | 0.775 [0.558, 0.967] | 66.7% | 66.7% |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | 12 | 24 | 87.5% [66.7, 100.0] | 0.929 [0.792, 1.000] | 87.5% | 91.7% |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 12 | 24 | 50.0% [25.0, 75.0] | 0.790 [0.628, 0.925] | 50.0% | 75.0% |
| `nvidia_nim/openai/gpt-oss-20b` | 12 | 24 | 45.8% [20.8, 70.8] | 0.650 [0.466, 0.837] | 45.8% | 58.3% |
| `nvidia_nim/z-ai/glm-5.3` | 12 | 24 | 41.7% [16.7, 70.8] | 0.662 [0.461, 0.847] | 41.7% | 50.0% |

#### Head to head

The first model's solve rate less the second's, on the tasks both attempted, resampled as pairs. The last column is the share of resamples in which the first came out ahead. An interval that crosses zero means these tasks do not separate the two.

| first | second | shared tasks | difference, 95% interval | first ahead |
| --- | --- | --- | --- | --- |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | 12 | -20.8 points [-45.8, +0.0] | 2% |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 12 | +16.7 points [-12.5, +45.8] | 88% |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | `nvidia_nim/openai/gpt-oss-20b` | 12 | +20.8 points [-12.5, +54.2] | 91% |
| `nvidia_nim/minimaxai/minimax-m3.1-flash` | `nvidia_nim/z-ai/glm-5.3` | 12 | +25.0 points [+4.2, +50.0] | 99% |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 12 | +37.5 points [+12.5, +62.5] | 100% |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | `nvidia_nim/openai/gpt-oss-20b` | 12 | +41.7 points [+16.7, +70.8] | 100% |
| `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | `nvidia_nim/z-ai/glm-5.3` | 12 | +45.8 points [+20.8, +70.8] | 100% |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia_nim/openai/gpt-oss-20b` | 12 | +4.2 points [-20.8, +29.2] | 62% |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia_nim/z-ai/glm-5.3` | 12 | +8.3 points [-20.8, +37.5] | 69% |
| `nvidia_nim/openai/gpt-oss-20b` | `nvidia_nim/z-ai/glm-5.3` | 12 | +4.2 points [-25.0, +33.3] | 60% |

### How each run ended

| model | runs | solved | agent finished | step ceiling | wall clock ceiling | call ceiling | provider ended | other error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `minimax-m3.1-flash` | 24 | 16 | 14 | 1 | 5 | 0 | 4 | 0 |
| `nemotron-3-ultra-550b-a55b` | 24 | 21 | 18 | 5 | 1 | 0 | 0 | 0 |
| `nemotron-3.5-lightning-30b-a3b` | 24 | 12 | 17 | 6 | 0 | 0 | 1 | 0 |
| `gpt-oss-20b` | 24 | 11 | 11 | 2 | 2 | 0 | 9 | 0 |
| `glm-5.3` | 24 | 10 | 10 | 0 | 5 | 0 | 9 | 0 |

### Per task, read as an RL reward signal

Each cell is solved runs over sampled runs. A task whose samples all pass or all fail gives a group relative method no gradient; only mixed outcomes teach. A bold cell has a learning signal under a binary reward, and an italic cell only under partial credit. This describes the reward signal these tasks offer these models. It is not a training run.

| task | `minimax-m3.1-flash` | `nemotron-3-ultra-550b-a55b` | `nemotron-3.5-lightning-30b-a3b` | `gpt-oss-20b` | `glm-5.3` |
| --- | --- | --- | --- | --- | --- |
| cli-implement-01 | 0/2 | 2/2 | *0/2* | **1/2** | 0/2 |
| git-surgery-01 | 2/2 | 2/2 | **1/2** | 0/2 | 0/2 |
| go-race-01 | 0/2 | **1/2** | **1/2** | **1/2** | 0/2 |
| log-forensics-01 | 2/2 | 2/2 | **1/2** | *0/2* | 2/2 |
| py-dep-conflict-01 | 2/2 | 2/2 | **1/2** | 2/2 | 2/2 |
| py-etl-schema-01 | 0/2 | 2/2 | **1/2** | 0/2 | 0/2 |
| py-failing-suite-01 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| py-flaky-test-01 | 2/2 | 2/2 | 2/2 | 2/2 | 0/2 |
| py-perf-01 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| sql-migration-01 | 0/2 | 0/2 | *0/2* | *0/2* | 0/2 |
| ts-api-contract-01 | 2/2 | 2/2 | **1/2** | **1/2** | **1/2** |
| ts-build-break-01 | 2/2 | 2/2 | 0/2 | 0/2 | **1/2** |

- Tasks with a learning signal for `minimax-m3.1-flash`: 0 of 12 under a binary reward, 0 of 12 under partial credit
- Tasks with a learning signal for `nemotron-3-ultra-550b-a55b`: 1 of 12 under a binary reward, 1 of 12 under partial credit
- Tasks with a learning signal for `nemotron-3.5-lightning-30b-a3b`: 6 of 12 under a binary reward, 8 of 12 under partial credit
- Tasks with a learning signal for `gpt-oss-20b`: 3 of 12 under a binary reward, 5 of 12 under partial credit
- Tasks with a learning signal for `glm-5.3`: 2 of 12 under a binary reward, 2 of 12 under partial credit

### One group, worked

GRPO samples a group of answers to one prompt and moves each toward or away from the group's own average: its advantage is its reward less the group mean, over the group's spread. Here the prompt is a task and the group is a model's seeds on it, `nemotron-3-ultra-550b-a55b` on go-race-01, the first such group with mixed outcomes.

| seed | solved | partial credit | advantage, binary reward | advantage, partial credit |
| --- | --- | --- | --- | --- |
| 0 | no | 0.900 | -1.00 | -1.00 |
| 1 | yes | 1.000 | +1.00 | +1.00 |

A group whose samples all pass, or all fail, gets an advantage of zero for every sample under a binary reward, which is why the bold cells above are the only ones a binary reward can learn from.

<!-- end measured -->

## Readings

The judge read none of this panel's unsolved runs the first time: it sent each verdict in a
shape the schema refused. That is fixed, and the reruns were judged. The runs it missed are
being read again by the same judge, and the readings of this panel follow once they are,
because three of the failure modes and the drift metric depend on them. Until then the
measured block above is the result.
