# Real model results

[RESULTS.md](RESULTS.md) is five scripted policies, which validate the harness and say
nothing about a model. This page is the other kind of result: five real language models
driving the same twelve tasks, end to end, on the Docker backend, on one harness version,
from two committed requests: the panel's own, and one that reran the runs the provider
ended.

## How it was measured

| | |
| --- | --- |
| Models | `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3-ultra-550b-a55b`, `minimaxai/minimax-m3.1-flash`, `openai/gpt-oss-20b`, `z-ai/glm-5.3` |
| Publishers | four: NVIDIA (two sizes), MiniMax, OpenAI's open weights, Z.ai |
| Provider | NVIDIA build, the free tier, no payment method |
| Backend | Docker. The agent works in one container; the hidden tests run in a fresh one holding only its final workspace, beside a planted test that has to fail |
| Tasks | all twelve in `core-12` |
| Seeds | two, 0 and 1, temperature 0.7 |
| Caps | each model its own job, its own call ceiling set to the most calls its runs could make, and an even share of a 34 request a minute limit. Both count the agent's calls; the judge's calls, and the provider's own retries of a failed call, fall outside them |
| Judge | `stepfun-ai/step-5-preview`, a fifth publisher, on every unsolved run |
| Harness | `0.3.0`; every record names the commit it ran from |
| Request | [`model-requests/history/2026-10-25-panel-4.json`](model-requests/history/2026-10-25-panel-4.json), run by [`model-panel.yml`](.github/workflows/model-panel.yml) as workflow run 40155008047 |
| Reruns | the 48 runs the provider ended, run once more by [`model-requests/history/2026-10-25-panel-5.json`](model-requests/history/2026-10-25-panel-5.json) as workflow run 40170690917; see below |
| Provenance | [`fixtures/model-runs.provenance.json`](fixtures/model-runs.provenance.json) |

`glm-5.3` answered in about 26 seconds a call on the free tier, so its runs had three times
each task's wall clock ceiling, up to the harness's cap of an hour, which held
`sql-migration-01` to twice its own, and four workers. The step ceiling, which bounds the
work an agent can do, is the same for every model.

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

Every line of the measured block below is rendered from the run records committed under
[`fixtures/model-runs`](fixtures/model-runs) and held to them in CI by
[`scripts/check_model_numbers.py`](scripts/check_model_numbers.py): a line that drifts from
its records fails the build. The readings after the block are prose, which the gate does
not read; every count in them can be redone from the same records.

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
| F06 | ignored test output | 7 | 14.0% |
| F07 | long horizon context loss | 7 | 14.0% |
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

The first model's solve rate less the second's, on the tasks both attempted, resampled as pairs. The last column is the share of resamples in which the first came out ahead, a tie counting half. An interval that crosses zero means these tasks do not separate the two.

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

Each cell is solved runs over sampled runs. A task whose samples all pass or all fail gives a group relative method no gradient; only mixed outcomes teach. A bold cell has a learning signal under a binary reward, and an italic cell only under partial credit. A cell marked † holds a run the provider ended, so it says less about the model than the rest. This describes the reward signal these tasks offer these models. It is not a training run.

| task | `minimax-m3.1-flash` | `nemotron-3-ultra-550b-a55b` | `nemotron-3.5-lightning-30b-a3b` | `gpt-oss-20b` | `glm-5.3` |
| --- | --- | --- | --- | --- | --- |
| cli-implement-01 | 0/2 † | 2/2 | *0/2* † | **1/2** | 0/2 † |
| git-surgery-01 | 2/2 | 2/2 | **1/2** | 0/2 † | 0/2 † |
| go-race-01 | 0/2 † | **1/2** | **1/2** | **1/2** † | 0/2 † |
| log-forensics-01 | 2/2 | 2/2 | **1/2** | *0/2* | 2/2 |
| py-dep-conflict-01 | 2/2 | 2/2 | **1/2** | 2/2 | 2/2 |
| py-etl-schema-01 | 0/2 | 2/2 | **1/2** | 0/2 † | 0/2 † |
| py-failing-suite-01 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| py-flaky-test-01 | 2/2 | 2/2 | 2/2 | 2/2 | 0/2 † |
| py-perf-01 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| sql-migration-01 | 0/2 † | 0/2 | *0/2* | *0/2* † | 0/2 † |
| ts-api-contract-01 | 2/2 | 2/2 | **1/2** | **1/2** † | **1/2** |
| ts-build-break-01 | 2/2 | 2/2 | 0/2 | 0/2 † | **1/2** |

- Tasks with a learning signal for `minimax-m3.1-flash`: 0 of 12 under a binary reward, 0 of 12 under partial credit; 3 of the 12 hold a run the provider ended
- Tasks with a learning signal for `nemotron-3-ultra-550b-a55b`: 1 of 12 under a binary reward, 1 of 12 under partial credit
- Tasks with a learning signal for `nemotron-3.5-lightning-30b-a3b`: 6 of 12 under a binary reward, 8 of 12 under partial credit; 1 of the 12 holds a run the provider ended
- Tasks with a learning signal for `gpt-oss-20b`: 3 of 12 under a binary reward, 5 of 12 under partial credit; 6 of the 12 hold a run the provider ended
- Tasks with a learning signal for `glm-5.3`: 2 of 12 under a binary reward, 2 of 12 under partial credit; 6 of the 12 hold a run the provider ended

### One group, worked

GRPO samples a group of answers to one prompt and moves each toward or away from the group's own average: its advantage is its reward less the group mean, over the group's spread. Here the prompt is a task and the group is a model's seeds on it, `nemotron-3-ultra-550b-a55b` on go-race-01, the first such group with mixed outcomes.

| seed | solved | partial credit | advantage, binary reward | advantage, partial credit |
| --- | --- | --- | --- | --- |
| 0 | no | 0.900 | -1.00 | -1.00 |
| 1 | yes | 1.000 | +1.00 | +1.00 |

A group whose samples all pass, or all fail, gets an advantage of zero for every sample under a binary reward, which is why the bold cells above are the only ones a binary reward can learn from.

### Two judges compared

- Judges compared: `nvidia_nim/stepfun-ai/step-5-preview` and `nvidia_nim/deepseek-ai/deepseek-v4.1-flash`, on 49 of the 50 runs the first judged, temperature 0

| mode | both present | first only | second only | neither | raw agreement | kappa |
| --- | --- | --- | --- | --- | --- | --- |
| F06 ignored test output | 1 | 5 | 0 | 43 | 0.90 | 0.26 |
| F07 long horizon context loss | 1 | 5 | 4 | 39 | 0.82 | 0.08 |
| F10 environment mismatch | 0 | 0 | 1 | 48 | 0.98 | 0.00 |

- Drift, between the judges: mean absolute difference 0.034, largest 0.300
- Left out of the comparison: 1 run(s) the second judge returned no valid verdict on

<!-- end measured -->

## What the trajectories show

Readings a pass rate alone would not give. Every number below is in the measured block or
is a count over the committed records that anyone can redo.

**One model is ahead, and the suite can say so; most pairs it cannot.** `nemotron-3-ultra`
solved 21 of 24, and against `nemotron-3.5-lightning`, `gpt-oss-20b` and `glm-5.3` the
whole of its head to head interval sits above zero. Against `minimax-m3.1-flash` the
interval reaches zero, so these twelve tasks do not separate the two leaders, and
`minimax` is separated only from `glm-5.3`. The middle three cannot be told apart at all.
Twelve tasks at two seeds rank a strong model against a weak one and very little else,
which is the reason the intervals are published beside the rates.

**The provider was the largest single cause of failure, and it is counted apart.** The
free tier ended 48 of the first 120 runs. `nemotron-3-ultra` lost twelve to 429s on the
first pass, eleven of them at seed 1, and its twelve reruns solved eleven: a solve rate
read without the table of how each run ended would have reported a weak seed for the
strongest model. Of the reruns, the provider ended 23, and they stand under the rule: nine
for `glm-5.3` and four for `minimax-m3.1-flash`, whose calls timed out, nine for
`gpt-oss-20b`, whose endpoint refused about one call in thirty with an HTTP 400 and an
empty body, and one for `nemotron-3.5-lightning`. Read the solve rates of those four as
lower bounds, `gpt-oss-20b`'s and `glm-5.3`'s most of all.

**`sql-migration-01` defeats every model, and half its runs end below where they
started.** Not one of the ten runs solved it. The migration the task starts from is correct
and unsafe, and passes six of its ten hidden tests; five runs ended at two or three of ten,
below where they started, because the rewrite toward a safe migration broke tests the
original passed. Of the other five, one reached nine of ten, and four ended where they
started, stopped once by the wall clock ceiling and three times by the provider. A reward
paid as raw partial credit would have paid the five that went backwards for it. `go-race-01`,
`py-etl-schema-01` and `cli-implement-01` were each solved in three runs of ten.

**Three tasks teach these models nothing.** `py-failing-suite-01` and `py-perf-01` were
solved in all ten runs, and on `py-flaky-test-01` every model solved both seeds or neither.
As RL environments for this panel they give no reward variance at all. For
`minimax-m3.1-flash` no task does: its two seeds agreed on all twelve, so at two samples it
has no learning signal anywhere, though the provider ended three of its runs, both
`go-race-01` seeds among them, and the table marks each cell that holds such a run.
`nemotron-3.5-lightning` has the most, six tasks under a binary reward and eight under
partial credit.

**Passing runs carry process problems.** 29 of the 70 solved runs had at least one
malformed step; in seven of them, all six of `glm-5.3`'s among them, the only one was a
reply with no tool call in it. `gpt-oss-20b` made a malformed call on 185 of its 484 steps:
tool names with the model's own channel markup left in, such as
`bash<|channel|>commentary`, arguments the schema does not have, such as a `depth` for
`list_dir`, arguments that did not parse, and replies with no tool call at all. It solved
11 runs anyway. F08 fired on eight solved runs, and its evidence says more than its count:
in six, every flagged command removed scratch files under `/tmp`, outside the workspace,
which a pattern cannot tell from deleting someone else's files, and in two it was
`git reset --hard` inside `git-surgery-01`'s repository, on a task about moving branches.

**Repetition splits the panel in two.** `nemotron-3-ultra` and `nemotron-3.5-lightning`
repeat commands at 0.249 and 0.198, four and three times the reference route's 0.062;
`minimax-m3.1-flash` and `glm-5.3` repeat almost nothing, at 0.012 and 0.019. The
baseline is what to read both against.

**No run tripped a grader check.** All 120 verifications ran in a fresh container with a
planted test, no report was refused, and F11 fired on none of the 120 runs. That is a
measured zero for what the checks can see. What they cannot see, a report bent test by
test, is set out in the [grader audit](docs/grader-audit.md).

## The judge

`stepfun-ai/step-5-preview`, from a fifth publisher, reads every unsolved run for the three
failure modes a rule cannot decide and for context drift. On the first pass it filled each
mode judgement in its reply as a JSON string, the schema refused that reply on 54 of the 55
unsolved runs it read, one call timed out, and all 55 were sealed with no verdict. The reply
is now read as the object it encodes. The 28 unsolved reruns were judged as they ran, and the
same judge, at the same temperature, read again the 22 first pass runs still without a
verdict, and only those, in workflow run 40179971123. The promotion checked that nothing in
those records changed but the judge's own output.

All 50 unsolved runs now carry a verdict: ignored test output on seven, long horizon
context loss on seven, environment mismatch on none, and a context drift of 0.0 on 47 of
the 50.

**A second reader does not reproduce those counts.** `deepseek-v4.1-flash`, from a family
that is neither the first judge's nor any agent's, read the same runs with the same prompt
at temperature 0, in workflow run 40191361481. It returned a verdict on 49 of the 50; on the
other the free tier timed out three times. The measured block above has each mode's two by
two table. The two judges agree on most runs, because most runs have none of the three
modes. On the runs that have one, they mostly do not agree. Of the six of those 49 runs the
first judge flagged for ignored test output, the second agreed on one and flagged no other,
a kappa of 0.26. Of the six the first flagged for context loss, the second agreed on one and
flagged four others instead, a kappa of 0.08, close to what chance alone would give.
Environment mismatch, which the first judge never found, the second found once. Their drift
scores differ by 0.034 on average, but the first judge scored 0.0 on 47 of the 50 runs, so
that agreement is mostly about drift being absent.

So read the judged modes as one model's reading of the trajectory, not a measurement of
it: the seven and seven above are what `step-5-preview` saw. Every other count on this page
comes from a rule, which is deterministic and needs no second reader. An earlier attempt at
this comparison, workflow run 40184208378, ran out of time on the free tier before it wrote
anything; [model-requests/README.md](model-requests/README.md) records both requests.

## Limitations

- **Twelve tasks at two seeds.** Every interval here is wide, and most pairs of models are
  not separated. Read the ranking as one model ahead of three of the other four, a second
  these tasks cannot separate from it, and a middle they cannot order.
- **The provider ended 23 runs that stand.** They count as attempts in every rate above,
  scored on the workspace each left, because removing them would let the provider choose
  which runs a model is scored on. 22 are unsolved; one, `gpt-oss-20b` on `go-race-01` at
  seed 1, had already left a workspace that passes every hidden test, and counts as solved.
  The table of how each run ended says which they are, and the per task table marks the
  cells that hold one. `gpt-oss-20b` and `glm-5.3` lost nine each, `minimax-m3.1-flash`
  four and `nemotron-3.5-lightning` one, so their rates are lower bounds.
- **The reruns ran on a later commit, at a lower rate.** Between the two commits nothing the
  agent was given or the grader checked changed: the same Dockerfiles, workspaces, prompts,
  tools, step and wall clock ceilings, verifier and planted test. Every model's job builds
  its own images, so image identifiers differ from one job to the next, the first pass
  included, but each is built from the same files. What changed is how the judge's reply is
  read, how the provider recognises and backs off from a 429, the total request rate, and
  the image build context, which now holds only the Dockerfile and the workspace. Both
  commits are in the provenance file and on every record.
- **One temperature, one free provider.** These are the free tier's builds of each model, at
  0.7, behind one rate limited key. The same model elsewhere, or at another temperature, may
  score differently, and `glm-5.3` ran with up to three times each task's wall clock
  ceiling because its queue was slow.
- **The judge is one model, and a second one mostly disagrees with it.** Its verdicts decide
  three failure modes and the drift metric. A second judge from another family agreed with
  it on which runs had a judged mode about as often as chance would for context loss, kappa
  0.08, and not much more for ignored test output, 0.26, so those counts are one model's
  reading. Neither judge has been checked against human labels, and agreement between two
  models would not be agreement with a person in any case.
- **One person wrote every task**, so the suite carries that person's sense of what is hard.
