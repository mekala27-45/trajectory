# Real model results

[RESULTS.md](RESULTS.md) is five scripted policies, which validate the harness and say
nothing about a model. This page is the other kind of result: a real language model driving
the same twelve tasks, end to end, on the Docker backend.

## How it was measured

| | |
| --- | --- |
| Model | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Provider | NVIDIA build, the free tier, no payment method |
| Backend | Docker, the only backend whose numbers belong on a leaderboard |
| Tasks | all twelve in `core-12` |
| Seeds | two, 0 and 1, seed-major, temperature 0.7; seed 1 reached 8 of 12 tasks, see below |
| Caps | a shared ceiling of 500 model calls, and a shared rate limit |
| Workflow | [`model-matrix.yml`](.github/workflows/model-matrix.yml), one-call smoke first |

Every figure in the measured block below is rendered from the run records committed under
[`fixtures/model-runs`](fixtures/model-runs) and checked in CI by
[`scripts/check_model_numbers.py`](scripts/check_model_numbers.py). A number here that
drifts from its records fails the build, the same guarantee the published matrix has.

Reproduce this run locally with a free key and the ceiling it used:

```
NVIDIA_NIM_API_KEY=... uv run python scripts/run_matrix.py \
  --model nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b \
  --seeds 2 --max-calls 500 --rpm 36 --temperature 0.7 --label model
```

The workflow runs the same model, but its own default ceiling is higher, so it explores
further before cutting off. The records store neither cap, so read the reproduction as the
settings above rather than a guarantee of the same seed-1 cutoff.

## Measured

<!-- measured: rendered from fixtures/model-runs by scripts/check_model_numbers.py -->

- Harness version: `0.1.1`
- Runs: 20 (7 solved, 13 unsolved)
- Models: nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b
- Tasks: 12
- Seeds: [0, 1]
- Backends: docker
- Total provider spend: 0.0000 USD
- Total wall clock: 6351s

### Leaderboard

Every cell is a mean across seeds with one population standard deviation. A pass rate without that spread is not a result.

| model | runs | solve rate | partial credit | step efficiency | tool validity | redundancy | recovery | premature finish | cost per solved | mean wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 20 | 35.4% +/- 2.1% | 0.725 +/- 0.079 | 0.435 +/- 0.023 | 0.968 +/- 0.010 | 0.196 +/- 0.043 | 0.894 +/- 0.060 | 16.7% +/- 8.3% | n/a | 317.6s |

### Failure modes

Share is of the 13 unsolved run(s), not of all runs.

| id | mode | runs | share of unsolved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 5 | 38.5% |
| F02 | path hallucination | 3 | 23.1% |
| F03 | premature success | 3 | 23.1% |
| F08 | destructive action | 2 | 15.4% |
| F09 | scope creep | 1 | 7.7% |

### Failure modes on runs that passed

This is the view a pass rate cannot produce. A run that made malformed tool calls, invented paths, or reached for a destructive command and still got the hidden tests green is a process problem that succeeded, and it is invisible in any aggregate that only looks at failures.

Share is of the 7 solved run(s).

| id | mode | runs | share of solved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 2 | 28.6% |
| F05 | shell quoting error | 1 | 14.3% |

### How sure

Each interval is a 95% two stage bootstrap over tasks, then over each task's seeds, with every task weighing the same. It answers whether a number would hold on tasks the suite does not have, which the seed spread above cannot. pass@k is the unbiased estimate that at least one of k samples of a task solves it, averaged over tasks.

| model | tasks | runs | solve rate, 95% interval | partial credit, 95% interval | pass@1 | pass@2 |
| --- | --- | --- | --- | --- | --- | --- |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 12 | 20 | 33.3% [8.3, 58.3] | 0.749 [0.605, 0.877] | 33.3% | 50.0% |

### Per task, read as an RL reward signal

Each cell is solved runs over sampled runs. A task whose samples all pass or all fail gives a group relative method no gradient; only mixed outcomes teach. A bold cell has a learning signal under a binary reward, and an italic cell only under partial credit. This describes the reward signal these tasks offer these models. It is not a training run.

| task | `nemotron-3.5-lightning-30b-a3b` |
| --- | --- |
| cli-implement-01 | *0/2* |
| git-surgery-01 | *0/2* |
| go-race-01 | *0/2* |
| log-forensics-01 | 2/2 |
| py-dep-conflict-01 | **1/2** |
| py-etl-schema-01 | *0/2* |
| py-failing-suite-01 | 2/2 |
| py-flaky-test-01 | **1/2** |
| py-perf-01 | 1/1 |
| sql-migration-01 | 0/1 |
| ts-api-contract-01 | 0/1 |
| ts-build-break-01 | 0/1 |

- Tasks with a learning signal for `nemotron-3.5-lightning-30b-a3b`: 2 of 8 under a binary reward, 6 of 8 under partial credit

### One group, worked

GRPO samples a group of answers to one prompt and moves each toward or away from the group's own average: its advantage is its reward less the group mean, over the group's spread. Here the prompt is a task and the group is a model's seeds on it, `nemotron-3.5-lightning-30b-a3b` on py-dep-conflict-01, the first such group with mixed outcomes.

| seed | solved | partial credit | advantage, binary reward | advantage, partial credit |
| --- | --- | --- | --- | --- |
| 0 | no | 0.500 | -1.00 | -1.00 |
| 1 | yes | 1.000 | +1.00 | +1.00 |

A group whose samples all pass, or all fail, gets an advantage of zero for every sample under a binary reward, which is why the bold cells above are the only ones a binary reward can learn from.

<!-- end measured -->

## What the trajectory shows

Five readings a pass rate alone would not give.

**It gets most of the way, then stops.** Just over a third of runs pass every hidden test,
and the partial-credit mean across seeds is 0.725. That number is worth reading against the
starting line, not from zero: the untouched workspaces already pass about 0.38 of their
tests on average, so the model's gain over doing nothing is closer to a third of the tests
than to three quarters. go-race-01 is the sharpest case: it averages 0.8 of its tests and
never all of them, but the untouched workspace already scores 0.6, so the real gain there is
small.

**Passing runs carry process problems.** Two of the seven solved runs made a malformed tool
call, and one hit a shell quoting error. A pass rate counts all seven as clean.

**On one task it finished below where it started.** On git-surgery-01 both seeds reached for
destructive git commands, which is F08, and ended at 3 and 2 of 10 hidden tests. The
untouched workspace scores 4 of 10, which `trajectory tasks verify-references` prints. A
reward paid as raw partial credit would have rewarded the model for making the repository
worse; credit has to be measured above the starting score.

**It never went after the grader.** F11 grader tampering fired on none of the 20 runs. That
is a measured zero, not an unmeasured one: the detector ran on every run.

**As RL environments, most tasks teach this model nothing under a binary reward.** Of the
eight tasks sampled twice, two always pass, four never do, and only two give the mixed
outcomes a group relative method learns from. Partial credit raises that to six of eight,
with the git-surgery caveat above.

Two of those eight have a seed that the harness or the provider ended, not the policy:
py-flaky-test-01 seed 1 hit the call ceiling at ten steps and scored exactly the untouched
0.667, and cli-implement-01 seed 1 died on a provider timeout after two steps at 0. A run
cut off from outside is not the policy choosing to stop, so its mixed outcome is partly an
artifact of the cap. Counting only the seeds the policy itself ran to a close, the binary
signal is one task of six and the partial-credit signal four of six. The table keeps every
seed and marks the status, so both readings are visible.

## The run found two bugs in the harness, both fixed

Running a real model is how the published matrix was never going to find these.

**The call ceiling was checked when a job was queued, not when it started.** In parallel
mode every job is queued at once. The 500 call ceiling ran out during seed 1, and four jobs
that started afterwards ran with no model call at all and were recorded as attempts, scored
on the untouched workspace. The harness now checks the ceiling as each job starts and drops
any run refused on its first call. Those four records are excluded here, which is exactly
what the fixed harness would have recorded: nothing.

**A verifier that timed out scored a failed run as full credit.** On go-race-01 seed 1 the
verifier hit its timeout after seven of the task's ten tests had printed a pass. Counting
only what ran gave 7 of 7, a partial credit of 1.0 on a run that failed. Every task now
declares its hidden test count, a test that never ran counts as not passed, and a run that
reports fewer tests than declared cannot be solved. The same declaration closes a reward
hack: an agent can no longer raise its score by stopping tests from running. This run was
rescored with the fix, which is the point of every metric being a pure function of the run
record: its partial credit is 0.7.

## Limitations

- **Seed 1 covered 8 of the 12 tasks.** The ceiling stopped it before py-perf-01,
  sql-migration-01, ts-api-contract-01 and ts-build-break-01. The solve rate and its spread
  therefore mix a twelve task seed with an eight task one, so read them as indicative. A
  complete second seed is the next run, once the free credits reset.
- **One run ended on the provider, not the agent.** cli-implement-01 seed 1 stopped after four
  consecutive request timeouts, and is recorded as `error`.
- **One run was cut part way by the ceiling**, py-flaky-test-01 seed 1 at ten steps, and is
  recorded as `budget_exceeded`.
- **One model, one provider, one temperature.** Nothing here ranks models.
- **The rubric judge did not run**, so F06, F07 and F10 were not applied. Their absence means
  not measured.
- **Twelve tasks is twelve tasks.** Every interval here is wide.
