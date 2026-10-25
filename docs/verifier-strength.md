# Verifier strength: how much of a near miss the hidden tests catch

A task's hidden tests are its reward function. Under reinforcement learning a policy is
pushed toward whatever the reward pays for, so the question that matters about a verifier
is not whether the reference solution passes it. Every task here already proves that in CI.
The question is whether a solution that is almost right passes it too. A suite that cannot
tell the reference from a near miss pays the near miss in full, and a policy trained on it
learns the near miss.

This page measures that directly, with mutation testing.

## How it is measured

`trajectory tasks mutate` takes each task's reference playbook, the expert trajectory that
solves it, and replays it with one small change in one of the files it writes: a comparison
off by one, a sign flipped, a condition inverted, a constant nudged, a version pin turned
around. Those are the classic mutation operators, and they are also the slips a model
makes. The hidden tests then run on the result, through the same verifier every agent run
gets, planted test and fresh container included.

- **Killed.** The hidden tests failed the mutant, twice. Counted separately when the mutant
  broke the build or crashed the suite, because catching a crash says less about a suite
  than catching a wrong answer.
- **Unstable.** The hidden tests failed the mutant once and passed it on the replay that
  confirms a kill. Scored, and not counted as caught.
- **Survived.** The hidden tests passed the mutant. Every survivor is listed by file, line
  and change, so a reader can judge it.
- **The score** is killed over scored.

Five rules keep the number honest.

- The change goes in where the reference writes the file, before the rest of the playbook
  runs, so a build step that follows compiles the mutant and not the original.
- Each task is first replayed with no change at all. A task whose unchanged replay does not
  pass would make every mutant look killed, so it is reported and not scored.
- A kill has to happen twice. A suite with a clock or a race in it can fail a run for a
  reason the change did not cause, and a reward that pays a near miss half the time has
  not caught it. The rule came from a run: see the last section.
- Only source files the reference writes are mutated, never tests, fixtures or data. A
  reference that edits a file in place with `sed` has its replacement text mutated, and in
  TypeScript the code inside a template literal's `${...}` counts as code.
- The sample is fixed per task: the same mutants on every machine, so two runs compare.

A survivor is not automatically a weak test. Some mutants are equivalent to the original,
and no test could tell them apart; a flag that only changes a log message, or a bound that
no input ever reaches, is one. The survivors are listed so that judgement can be made per
mutant rather than hidden in a percentage.

## Measured

<!-- measured: rendered from fixtures/mutation/core-12.json by scripts/check_mutation_numbers.py -->

- Backend: docker
- Harness version: `0.3.0`
- Sample: up to 12 mutants per task, fixed per task
- Mutants scored: 91 across 10 task(s)
- Killed: 72 of 91 (79.1%), 70 by a failing assertion and 2 by a crash or a failed build

| task | solution files | sites | scored | killed | by a crash | unstable | survived | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cli-implement-01 | `logstat.py` | 68 | 12 | 12 | 1 | 0 | 0 | 100.0% |
| git-surgery-01 | none | 0 | 0 | | | | | n/a: the reference changes its source with commands, not by writing a file |
| go-race-01 | `agg/aggregator.go` | 12 | 12 | 9 | 0 | 0 | 3 | 75.0% |
| log-forensics-01 | `analyse.py` | 15 | 12 | 8 | 0 | 0 | 4 | 66.7% |
| py-dep-conflict-01 | `requirements.txt` | 6 | 6 | 6 | 0 | 0 | 0 | 100.0% |
| py-etl-schema-01 | `src/warehouse/pipeline.py` | 23 | 12 | 11 | 0 | 0 | 1 | 91.7% |
| py-failing-suite-01 | none | 2 | 2 | 2 | 0 | 0 | 0 | 100.0% |
| py-flaky-test-01 | `src/sampler/core.py` | 18 | 12 | 7 | 0 | 0 | 5 | 58.3% |
| py-perf-01 | `src/rollup/core.py` | 9 | 9 | 7 | 1 | 0 | 2 | 77.8% |
| sql-migration-01 | `migrations/0002_add_status.down.sql`, `migrations/0002_add_status.up.sql` | 24 | 12 | 8 | 0 | 0 | 4 | 66.7% |
| ts-api-contract-01 | `src/orders.ts`, `src/shipments.ts`, `src/status.ts` | 0 | 0 | | | | | n/a: no operator applies to the source the reference writes |
| ts-build-break-01 | `packages/app/src/main.ts` | 2 | 2 | 2 | 0 | 0 | 0 | 100.0% |

### Survivors

Each row is a change the hidden tests did not reliably notice. Some are equivalent to the original and no test could notice them; the rest are gaps.

| task | mutant | outcome | hidden tests passed |
| --- | --- | --- | --- |
| go-race-01 | `agg/aggregator.go:36 0 -> 1` | survived | 10 of 10 |
| go-race-01 | `agg/aggregator.go:37 0 -> 1` | survived | 10 of 10 |
| go-race-01 | `agg/aggregator.go:72 > -> >=` | survived | 10 of 10 |
| log-forensics-01 | `analyse.py:23 >= -> >` | survived | 9 of 9 |
| log-forensics-01 | `analyse.py:42 2 -> 3` | survived | 9 of 9 |
| log-forensics-01 | `analyse.py:23 500 -> 501` | survived | 9 of 9 |
| log-forensics-01 | `analyse.py:41 2 -> 3` | survived | 9 of 9 |
| py-etl-schema-01 | `src/warehouse/pipeline.py:36 True -> False` | survived | 10 of 10 |
| py-flaky-test-01 | `src/sampler/core.py:44 not x -> x` | survived | 9 of 9 |
| py-flaky-test-01 | `src/sampler/core.py:46 == -> !=` | survived | 9 of 9 |
| py-flaky-test-01 | `src/sampler/core.py:134 <= -> <` | survived | 9 of 9 |
| py-flaky-test-01 | `src/sampler/core.py:134 < -> <=` | survived | 9 of 9 |
| py-flaky-test-01 | `src/sampler/core.py:142 6 -> 7` | survived | 9 of 9 |
| py-perf-01 | `src/rollup/core.py:35 True -> False` | survived | 9 of 9 |
| py-perf-01 | `src/rollup/core.py:123 > -> >=` | survived | 9 of 9 |
| sql-migration-01 | `migrations/0002_add_status.up.sql:30 < -> <=` | survived | 10 of 10 |
| sql-migration-01 | `migrations/0002_add_status.up.sql:24 10000 -> 10001` | survived | 10 of 10 |
| sql-migration-01 | `migrations/0002_add_status.up.sql:35 > -> >=` | survived | 10 of 10 |
| sql-migration-01 | `migrations/0002_add_status.up.sql:35 0 -> 1` | survived | 10 of 10 |

<!-- end measured -->

## Reading the survivors

The gate above holds every figure and every survivor to the report. What follows is a
reading of the survivors by hand, so it is a judgement rather than a measurement, and it
is written down so that it can be checked: each claim names the line it is about.

**Equivalent to the original: 6.** No input tells these apart from the reference.

- `go-race-01` `aggregator.go:72` and `py-perf-01` `core.py:123`, `> -> >=`. A running
  maximum that also reassigns on a tie stores the same value.
- `sql-migration-01` `up.sql:30`, `< -> <=`. One more batch past the highest id, which
  matches no row.
- `sql-migration-01` `up.sql:24`, `10000 -> 10001`. The batch size. Any size leaves the
  same table.
- `log-forensics-01` `analyse.py:41` and `:42`, `2 -> 3`. The indent of the JSON the
  script writes and prints. The tests parse it.

**Equivalent on this task's data only: 5.** A different input would tell these apart, so
the fix is a fixture rather than a test.

- `log-forensics-01` `analyse.py:23`, `>= -> >` and `500 -> 501`. Both stop counting a
  status of exactly 500 as a server error. The log has 502s and 503s and not one 500, so
  the count does not move. A single 500 in the log would catch both.
- `sql-migration-01` `up.sql:35`, `> -> >=` and `0 -> 1`. Both move the edge of a refund
  at zero cents. Every seeded order totals at least 500 cents and every refund is 0, 100
  or the whole total, so no row sits on the edge. One zero total order in the seed would
  catch both.
- `py-flaky-test-01` `core.py:142`, `6 -> 7`. A delay rounded to seven places instead of
  six. The tests compare a schedule with itself and with a range, never with a stored
  value.

**Gaps: 8.** Behaviour the code promises that no hidden test checks.

- `go-race-01` `aggregator.go:36` and `:37`, `0 -> 1`. The mean latency of an endpoint.
  The first change makes the mean of an empty endpoint divide by zero and panic, and the
  mean of an endpoint with one sample zero; the second makes the empty mean 1. The tests
  read the mean only of an endpoint with many samples.
- `py-flaky-test-01` `core.py:44`, `not x -> x`, and `core.py:46`, `== -> !=`. Both break
  value equality on a `Request`. The tests compare samples by request id, so no test
  depends on it.
- `py-flaky-test-01` `core.py:134`, `<= -> <` and `< -> <=`. The two ends of the allowed
  jitter range. No test passes a jitter of 0, which has to be accepted, or of 1, which has
  to be refused.
- `py-etl-schema-01` `pipeline.py:36` and `py-perf-01` `core.py:35`, `True -> False`. A
  result type that is no longer frozen, so it can be changed after it is made and can no
  longer be hashed. No test tries either.

Leaving the six equivalent mutants out, the hidden tests caught 72 of 85 near misses,
84.7%. Every gap sits at an edge: an empty input, the two ends of a range, a property no
caller in the task uses. That is the usual shape of a hand written suite, which tests the
cases the task is about, and it says where a policy could be paid for a near miss: code
that is right on the common path and wrong at the edges.

## What it does not measure

Mutation testing asks whether the tests notice a small wrong change. It does not ask
whether they notice a large one, such as a solution that special cases the test inputs, or
one that passes by changing what the grader sees; those are what the
[grader audit](grader-audit.md) is about. `git-surgery-01` has no mutable source at all,
because its reference repairs the repository with git commands rather than by writing a
file, so it is listed and not scored. The equivalent mutants stay in the published score,
because deciding which mutants are equivalent is the judgement above, and a gate cannot
check a judgement.

## Why a kill has to happen twice

The first local run of this measurement, on the unisolated backend with four mutants at a
time, killed one more mutant than the container run did: `sql-migration-01` `up.sql:35`,
`> -> >=`, failed one of ten hidden tests locally and passed all ten in Docker. That mutant
is equivalent on this data, so something other than the change failed the test. The suite
has one test with a clock in it, a time budget on every statement, and four seeded
Postgres clusters were running at once. A reward with that in it pays the same answer
differently on a busy machine, which is a reward problem in its own right, and a
measurement that counted the failure would have credited the tests with a catch they
never made. So every kill is now replayed once more and has to fail again.
