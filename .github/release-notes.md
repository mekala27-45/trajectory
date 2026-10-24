## trajectory v0.2.0

The first release that measures a real language model, and the first that hardens the
grader against an agent that tries to cheat it. v0.1.x validated the harness with five
scripted policies. This release points the same twelve tasks at a real model for free,
exports every task to the Harbor format and every run to ATIF, validates both against
Harbor's own models, scores other agents' trajectories, and closes the cheapest
reward-hacking routes a reused-container verifier has while naming the ones it leaves open.

### Real models, for free

`nvidia/nemotron-3.5-lightning-30b-a3b`, through NVIDIA's free tier, at two seeds on the
Docker backend. Seven of twenty runs solved, and the partial-credit mean across seeds is
0.725, though the untouched workspaces already score about 0.38, so the gain over doing
nothing is about a third of the tests. Two of the seven solved runs still made a malformed
tool call. On git-surgery both seeds reached for destructive git and finished below the
untouched workspace. F11 grader tampering fired on none. The table figures are in
`MODEL-RESULTS.md`, rendered from the twenty committed run records and gated in CI; the
prose around them is read against the starting score, not from zero.

Free tiers are priced by the request, not the dollar, so the controls changed with them. A
shared call ceiling (`--max-calls`) is reserved before each call under a lock, so parallel
workers cannot overshoot it, and reports `budget_exceeded` like the dollar cap does. A
shared rate limit (`--rpm`) spaces calls across every worker. Seed-major ordering means a
run the ceiling cuts off leaves earlier seeds whole.

A one-call smoke gates the real matrix, and checks the model calls a tool, because the agent
acts only through tools. It found, for free, that the first model chosen did not exist under
that name, that the common fallback had reached end of life, and that DeepSeek V4.1 Flash
does not answer a trivial prompt inside 90 seconds. Reasoning models spend their budget on a
hidden thinking channel; the provider now keeps it for the replay and never feeds it back.

### Two measurement bugs the real run found

Neither could surface on the scripted matrix.

The call ceiling was checked when a job was queued, not when it started. A thread pool
queues every job at once, so four jobs that started after the ceiling ran out were recorded
as attempts with no model call, scored on the untouched workspace. The caps are now checked
as each job starts, and a run refused on its first call is dropped.

A verifier that timed out scored a failed run as full credit: seven of ten Go tests printed a
pass before the timeout, and dividing by what ran gave 7 of 7. Tasks now declare
`expected_tests`. A declared test that never ran counts as not passed, a run reporting fewer
cannot be solved, and partial credit divides by the declaration. `verify-references` fails a
task whose reference runs a different count.

An audit of the first fix found it missed a sharper version: output the parser cannot read
at all, which is what a solution ending the test process with exit code 0 before any test
runs produces. That fell back to the exit code and scored as solved. So a result the parser
cannot read now scores zero whatever the exit code, in the verifier and in the Harbor export
alike, and one place, `Verification.counting_declared`, holds both rules so a record reads the
way the verifier would record it today.

### Harbor tasks and ATIF trajectories

`trajectory export harbor` writes every task as a Harbor task directory: the instruction,
an environment with the workspace baked in and the tests left out, a verifier that carries
the native hardening and writes a reward on every exit path, and the reference playbook as
an oracle. CI grades a sample three ways with no Docker: the oracle must score 1.0, the
untouched workspace 0.0, and a planted test runner must never run and must not change the
grade.

Both exports are now validated against Harbor itself. A CI step installs `harbor==0.24.0`
in its own environment and checks every exported `task.toml` with Harbor's `TaskConfig` and
every ATIF file with Harbor's `Trajectory`, reading back the hardening and the no-network
policy. The check imports nothing from this repository, so a reimplementation of Harbor's
rules cannot satisfy it.

`trajectory import atif` scores a trajectory from Claude Code, Codex, mini-swe-agent or
OpenHands with the metrics its record can support: their tool names map to this harness's,
and a closing text turn reads as that agent's finish, so F03 premature success applies to an
agent that says done with the tests red. A foreign trajectory carries no exit codes, so the
metrics that need them stay silent rather than guess, which `docs/interop.md` sets out. A
native export round-trips without loss; on all 200 records the score and the verification
record, exit code included, come back identical.

### The grader audit

The verifier ran the hidden tests with the agent's workspace at the front of the import
path, so a file the agent dropped there, named after a module the tests import, would have
been loaded in its place. Verification now runs with `PYTHONSAFEPATH` and
`PYTHONNOUSERSITE`, and output the parser cannot read scores zero rather than deferring to
the exit code. All twelve references still start broken and solve with it on.

Failure mode **F11 grader tampering** flags a run that leaves a startup hook behind by one
of a few known names. It is a tripwire for the obvious attempt, not a general tamper
detector, and the grader audit now says so and names the route that stays open: where a
hidden test imports the solution, the solution runs in the grader's process. Closing that
needs verification against a copy of the workspace with the solution behind a process
boundary, which is the next piece of work. The taxonomy is now eleven modes, eight by rule
and three by the judge. See `docs/grader-audit.md`.

### The results as an RL reward signal

`MODEL-RESULTS.md` ends with each task read as a reward signal: a task whose samples all pass
or all fail gives a group relative method no gradient. Under a binary reward, two of the
eight tasks sampled twice give this model anything to learn from, and partial credit raises
that to six, with two caveats the page makes concrete: credit has to be measured above the
starting score, and two of the eight have a seed that the harness or the provider cut off
rather than the policy, so counting only the seeds the policy ran to a close the figures are
one of six and four of six. It describes the signal; it is not a training run.

### Fixes

- Timestamps came back in the database server's zone rather than UTC.
- The markdown report labelled every result with the harness version checked out at render
  time rather than the version that recorded the runs.
- An API test pinned the taxonomy at ten modes; it now checks the endpoint serves every
  identifier the harness knows.

### New checks

`check_harbor_export` grades exported tasks three ways, including against a planted test
runner. `check_harbor_models` validates every export against Harbor's own models in an
environment that has only Harbor. `check_version` now also compares the version one
workspace package pins on another, which is what broke a plain install. `check_model_numbers`
holds every real-model table figure to its committed records. The CI test step names a
failing test and the coverage total in annotations, so a failure says what it is.

Over 830 tests in CI, coverage enforced against a floor of 80.

### Install

Not on PyPI yet, so run it from a clone:

```
git clone https://github.com/mekala27-45/trajectory && cd trajectory
uv sync --all-packages
uv run trajectory export harbor --out harbor-tasks
uv run trajectory import atif trajectory.json --task py-perf-01 --reward 0
uv run trajectory run --suite core-12 --model stub:methodical
```

Read `MODEL-RESULTS.md` and `RESULTS.md`, and the limitations in both, before quoting
either. Twelve tasks is twelve tasks.
