# Results: core-12

Measured on 14 September 2026 with harness `0.1.0`, schema version 1.

**Read the [Methodology and limitations](#methodology-and-limitations) section before
quoting anything here.** Two things about this run matter more than any number in it: the
models are scripted offline policies rather than language models, and the sandbox was the
unisolated local backend rather than Docker. Both are stated in full below, and both are
one command away from being fixed by anyone who clones this repository.

## What was run

| | |
| --- | --- |
| Suite | `core-12`, 12 tasks, difficulty tiers 1 to 5 |
| Models | 5 scripted offline policies |
| Seeds | 3 per task per model |
| Runs | 180 |
| Solved | 155 |
| Unsolved | 25 |
| Trajectory steps recorded | 2,090 |
| Total wall clock | 47.9 minutes (2,872 s) |
| Total provider spend | **0.00 USD** |
| Sandbox backend | `local` (unisolated, see limitations) |
| Machine | Linux 6.18.44, x86_64, 2 vCPU, Python 3.12.3 |
| Reproduce with | `python scripts/run_matrix.py --parallel 2` |

The spend is a true zero rather than a rounded one. Offline policies make no provider
calls and report no synthetic price, so this is a measurement and not an estimate dressed
up as one.

## Leaderboard

Every cell is a mean across seeds with one population standard deviation. A pass rate
reported without its spread is not a result, and neither is any other metric here, so the
spread is printed even where it is zero. The only non-zero spread that a summary table
would have hidden is `stub:hasty` redundancy at 0.014 +/- 0.010.

| model | runs | solve rate | partial credit | step efficiency | tool validity | redundancy | recovery | premature finish | destructive | wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `stub:methodical` | 36 | **100.0% +/- 0.0%** | 1.000 +/- 0.000 | **1.000 +/- 0.000** | **1.000 +/- 0.000** | **0.062 +/- 0.000** | 1.000 +/- 0.000 | 0.0% | 0 | 14.9 s |
| `stub:sloppy` | 36 | **100.0% +/- 0.0%** | 1.000 +/- 0.000 | 0.766 +/- 0.000 | **0.922 +/- 0.000** | 0.052 +/- 0.000 | 1.000 +/- 0.000 | 0.0% | 0 | 14.7 s |
| `stub:thrasher` | 36 | **100.0% +/- 0.0%** | 1.000 +/- 0.000 | 0.664 +/- 0.000 | 1.000 +/- 0.000 | **0.493 +/- 0.000** | **0.833 +/- 0.136** | 0.0% | 0 | 20.3 s |
| `stub:reckless` | 36 | 97.2% +/- 3.9% | 0.981 +/- 0.027 | 0.766 +/- 0.001 | 1.000 +/- 0.000 | 0.045 +/- 0.000 | 1.000 +/- 0.000 | 2.8% +/- 3.9% | **72** | 17.1 s |
| `stub:hasty` | 36 | **33.3% +/- 23.6%** | 0.674 +/- 0.167 | 1.000 +/- 0.000 | 1.000 +/- 0.000 | **0.014 +/- 0.010** | 1.000 +/- 0.000 | **66.7% +/- 23.6%** | 0 | 12.8 s |

Cost per solved task is `n/a` for every row because spend was zero.

Step efficiency is scored only on runs that solved the task, which is why `stub:hasty`
reads 1.000 in that column while sitting last on solve rate. On the third of its runs
where quitting early happened to be enough, it used no more steps than the reference
route; the two thirds it abandoned contribute nothing, because scoring efficiency on a
failed run would reward giving up. Its seed spread is computed over two seeds rather
than three, since seed 1 solved nothing and therefore produced no value to average.
The ratio table below is the honest view of how many steps it actually took.

Steps taken against the reference route, which is the same information as step efficiency
seen from the other side:

| model | mean steps | mean reference steps | ratio |
| --- | --- | --- | --- |
| `stub:hasty` | 6.7 | 10.1 | 0.67 |
| `stub:methodical` | 10.1 | 10.1 | 1.00 |
| `stub:reckless` | 13.1 | 10.1 | 1.30 |
| `stub:sloppy` | 13.1 | 10.1 | 1.30 |
| `stub:thrasher` | 15.1 | 10.1 | 1.50 |

## Per task

Overall solve rate across all five policies, and per policy.

| task | tier | overall | methodical | sloppy | thrasher | reckless | hasty |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `py-failing-suite-01` | 1 | 80.0% | 100% | 100% | 100% | 100% | 0% |
| `py-dep-conflict-01` | 2 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `ts-build-break-01` | 2 | 80.0% | 100% | 100% | 100% | 100% | 0% |
| `log-forensics-01` | 2 | 80.0% | 100% | 100% | 100% | 100% | 0% |
| `py-flaky-test-01` | 3 | 80.0% | 100% | 100% | 100% | 100% | 0% |
| `py-perf-01` | 3 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `py-etl-schema-01` | 3 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `cli-implement-01` | 3 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `ts-api-contract-01` | 3 | 80.0% | 100% | 100% | 100% | 100% | 0% |
| `go-race-01` | 4 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `sql-migration-01` | 4 | 93.3% | 100% | 100% | 100% | 100% | 67% |
| `git-surgery-01` | 5 | 73.3% | 100% | 100% | 100% | **67%** | 0% |

## Failure modes

### On unsolved runs

The conventional view. Share is of the 25 unsolved runs.

| id | mode | runs | share of unsolved |
| --- | --- | --- | --- |
| F03 | premature success | 25 | 100.0% |
| F08 | destructive action | 1 | 4.0% |
| F09 | scope creep | 1 | 4.0% |

### On runs that passed

The view a pass rate cannot produce. Share is of the 155 solved runs.

| id | mode | runs | share of solved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 36 | 23.2% |
| F02 | path hallucination | 36 | 23.2% |
| F05 | shell quoting error | 36 | 23.2% |
| F04 | retry loop | 35 | 22.6% |
| F08 | destructive action | 35 | 22.6% |
| F09 | scope creep | 35 | 22.6% |

Every one of those 36 runs passed every hidden test. In a conventional report they are
indistinguishable from a clean solve.

### By policy

| model | modes fired |
| --- | --- |
| `stub:methodical` | none |
| `stub:sloppy` | F01 x36, F02 x36, F05 x36 |
| `stub:thrasher` | F04 x35 |
| `stub:reckless` | F08 x36, F09 x36, F03 x1 |
| `stub:hasty` | F03 x24 |

## Three findings

### 1. Pass rate could not tell three of the five policies apart. Three metrics could.

`stub:methodical`, `stub:sloppy` and `stub:thrasher` all scored **100.0% +/- 0.0%**. On a
conventional leaderboard they are the same row three times, and a model team comparing
them would have nothing to work with.

The trajectory metrics separate them on the first glance:

- `sloppy` has a tool call validity of **0.922**, meaning roughly 8 percent of its tool
  calls were rejected before reaching the sandbox. It referenced a file that was never in
  the workspace on every single run, and shipped a shell command with an unbalanced quote
  on every single run. Step efficiency 0.766.
- `thrasher` has a redundant action rate of **0.493**: about half of every command it
  issued was a byte-identical repeat of an earlier one. Its recovery rate is **0.833 +/-
  0.136**, the only non-zero seed variance in that column, and its step efficiency is
  **0.664**, the worst of any policy that solved anything.
- `methodical` sits at the ceiling on all three.

Six failure modes fired on runs that passed, **213 hits** spread over **106 of the 155
solved runs**, which is 68.4 percent of every run this suite counts as a success. The
failure mode table as a benchmark normally reports it, scoped to unsolved runs, shows
**none of them**. That gap is the entire argument for scoring trajectories, and it is the
reason this repository reports both populations.

### 2. A single-seed pass rate for one policy could have been published as anything from 0% to 50%.

`stub:hasty` scored **33.3% +/- 23.6%** overall. The per-seed breakdown:

| seed | solved | rate |
| --- | --- | --- |
| 0 | 6 of 12 | 50.0% |
| 1 | 0 of 12 | 0.0% |
| 2 | 6 of 12 | 50.0% |

A one-seed evaluation of this policy, on these exact tasks, would have published either
0 percent or 50 percent as the number, with no indication that the other was equally
available. That is a 50 point swing produced entirely by the seed.

The standard deviation is 23.6 points. Any change to this policy that moved its pass rate
by less than about 24 points would be indistinguishable from noise at three seeds, and
nobody looking at a bare `33%` would know that.

This is a scripted policy whose variance is injected on purpose, so the magnitude is
constructed. The mechanism is not: it is exactly why a pass rate without its spread is not
a result, and why `MetricStat` carries a standard deviation everywhere rather than a bare
float.

### 3. The one run that a destructive command actually broke was broken by the command that looked harmless.

`stub:reckless` issued 72 destructive commands across its 36 runs: `git reset --hard` 36
times, a download piped to a shell 24 times, and `chmod -R 777 .` 12 times. It still solved
35 of 36. So the finding is not "destructive commands cause failures", because mostly they
did not.

The single failure is `git-surgery-01`, seed 1, and the trajectory says exactly what
happened. The policy ran `chmod -R 777 .` at step 1, before it had looked at anything. Git
tracks the executable bit, so `git status` at step 2 reported four modified files that
nobody had edited:

```
## feature
 M README.md
 M src/csvio.py
 M src/fetcher.py
 M src/retry.py
```

It then recovered the dropped commit correctly: found it with `git fsck --dangling`, named
it with a ref, and rebuilt the branch. At step 9 it tried to rebase onto `main` and git
refused:

```
error: cannot rebase: You have unstaged changes.
error: Please commit or stash them.
```

The rebase never happened, the branch was left in the wrong shape, and the policy called
`finish` two steps later anyway. 3 of 10 hidden tests passed.

The permissions change and the failure are eight steps apart and look unrelated. The run
record connects them: the destructive action metric flagged the `chmod` at step 1, the
recovery-after-failure window shows what happened at step 9, and F03 records that the agent
declared success regardless. A pass rate reports this policy at 97 percent and says nothing
about any of it.

For contrast, the same policy on the same task at seeds 0 and 2 ran its destructive command
at steps 4 and 2 respectively and solved the task. The difference between a 97 percent
policy and a 100 percent one, on this task, was *when* in the trajectory the destructive
command landed.

## Judge self-agreement

**Not measured.** No model API key was available on the machine that produced this matrix,
so the rubric judge did not run.

The consequence is specific rather than vague: `context_drift` (metric 8) is `null` on all
180 runs, and the three judge-decided failure modes (F06 ignored test output,
F07 long-horizon context loss, F10 environment mismatch) were never applied. They appear in
the taxonomy with counts of zero, and that zero means "not measured", not "did not happen".

The measurement is implemented, tested against a scripted judge, and one command away:

```
trajectory judge runs/matrix --model <model> --sample 0.2
```

It judges every unsolved run at temperature 0, judges a 20 percent sample again at
temperature 0.7, and reports per-mode raw agreement, Cohen's kappa, and the mean and worst
absolute difference in the drift score. The nightly CI workflow runs it whenever a key is
present as a repository secret.

Until that number exists, treat the three judged modes as unpopulated columns rather than
as evidence of anything.

## Methodology and limitations

Written carefully, because it is the part of this document that decides whether the rest of
it is worth anything.

### These are not model results

The five models are scripted offline policies, not language models. Each one replays a
task's reference playbook with a characteristic defect injected: `methodical` follows it
exactly, `hasty` stops part way and declares success, `thrasher` repeats commands instead
of adapting, `sloppy` makes malformed calls and invents paths, `reckless` runs destructive
commands it was never asked for. Their identifiers all start with `stub:` and the web app
labels them "scripted policy" on every row.

What this matrix therefore is: an end-to-end validation of the harness on real containers
with real hidden tests, and a demonstration that the trajectory metrics detect the things
they claim to detect. Every number is genuinely measured.

What it is not: a comparison of language models. Nothing here says anything about any
model's capability. The same `scripts/run_matrix.py` produces the model matrix with a key
and a different `--model` list, and the nightly workflow is already wired to do it.

### The sandbox was not isolated

Every run in this matrix carries `sandbox_backend: local`, because no Docker daemon was
reachable on the machine that produced it. The local backend runs commands in a temporary
directory on the host. It is not an isolation boundary, and the specific weakness is that
the host filesystem is visible, so the hidden tests for the task being run are reachable by
absolute path. The scripted policies do not do that, and their trajectories are recorded in
full so anyone can check, but the guarantee does not hold and so the results do not carry
it.

This is why the leaderboard keys rows by sandbox backend and labels every row here `local`.
Local rows are never averaged together with container rows.

**This is the single thing a reader should discount, and it takes about 25 minutes to fix
on any machine with Docker:**

```
make install
trajectory tasks verify-references --backend docker   # proves the twelve tasks in containers
make matrix                                           # 180 runs, real isolation
make fixtures && make bundle                          # re-record and rebuild the demo data
```

No code changes. The rows relabel themselves from `local` to `docker`, because the backend
is part of a row's identity rather than a footnote. CI already runs the Docker path on
every push: the `tasks` job builds all twelve images and replays every reference solution
in real containers.

### Twelve tasks is twelve tasks

This is a small suite. It is not comprehensive, it is not state of the art, and it is not
the first of anything. A solve rate on twelve tasks has wide confidence intervals no matter
how many seeds you run, and a model that is good at these twelve is not thereby good at
software engineering.

### One person wrote every task

So every task carries that person's biases about what is interesting, what is hard, and
what a reasonable solution looks like. The distribution skews toward the kinds of failures
that person has debugged: dependency resolution, schema drift, git internals, accidental
quadratic loops. There is nothing here about reading an unfamiliar large codebase, nothing
about API design, nothing about anything a front-end engineer would recognise as their job.
[docs/writing-tasks.md](docs/writing-tasks.md) exists so that stops being true.

### Step efficiency depends on a reference solution that may not be optimal

The denominator of step efficiency is the length of a playbook one person wrote. Where that
playbook is clumsy, agents look better than they are; where it is unusually slick, they look
worse. Task validation enforces that the number matches the playbook length, so it cannot
be inflated by hand, but it cannot make a mediocre reference route into a good one.

Read step efficiency as a ratio against one competent human route, not as a score.

### The judge is a language model

And it did not run here at all. See [Judge self-agreement](#judge-self-agreement). When it
does run, its self-agreement is reported alongside its verdicts, because a harness that
does not measure the reliability of its own judge has no business grading anyone else.

### Redundant action rate has a floor above zero

Re-running a test suite after changing the code is a byte-identical repeat command and
exactly the right thing to do. The reference policy's rate on this suite is **0.062**, which
is the floor for `core-12`, not zero. Read an agent's rate against that floor.

### Wall clock is not comparable across machines

Every wall clock figure here comes from one 2 vCPU machine running two evaluations in
parallel. It is useful for comparing policies within this matrix and useless for comparing
against a number produced anywhere else.

### The harness is version 0.1.0

Metric definitions will change. When they do, previously recorded runs can be rescored,
because every metric is a pure function of the run record: `trajectory score <run-dir>`
recomputes everything without rerunning an agent. That is why the metrics are pure, and it
is the mechanism by which these numbers stay comparable to later ones.

## Reproducing this

```
git clone https://github.com/mekala27-45/trajectory
cd trajectory
make install

# validate the suite and prove every reference solution still solves its task
trajectory tasks validate --strict
trajectory tasks verify-references --backend docker

# the matrix in this document
python scripts/run_matrix.py --parallel 2

# the tables in this document
trajectory report runs/matrix --format md
```

Or with real models, which is what the harness is for:

```
export ANTHROPIC_API_KEY=...
trajectory run --suite core-12 --model anthropic/<model> \
  --seed 0 --seed 1 --seed 2 --parallel 4 --budget-usd 10.00
trajectory judge runs/<dir> --model anthropic/<model> --sample 0.2
trajectory report runs/<dir> --format md
```

The `--budget-usd` ceiling is checked before every model call and is shared across parallel
runs. Running a suite overnight and finding a bill in the hundreds is the most common
self-inflicted wound in this category of project.
