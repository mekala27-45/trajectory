## trajectory v0.2.0

The first release that measures a real language model, and the first that checks whether
an agent tried to cheat its grader. v0.1.x validated the harness with five scripted
policies. This release points the same twelve tasks at a real model for free, exports every
task to the Harbor format and every run to ATIF, scores other agents' trajectories, and
closes the cheapest reward-hacking surface a reused-container verifier has.

### Real models, for free

`nvidia/nemotron-3.5-lightning-30b-a3b`, through NVIDIA's free tier, at two seeds on the
Docker backend. Seven of twenty runs solved, and the average run passed 0.725 of its hidden
tests: it gets most of the way and stops. Two of the seven solved runs still made a
malformed tool call. On git-surgery both seeds reached for destructive git and finished
below the untouched workspace. F11 grader tampering fired on none. Every figure is in
`MODEL-RESULTS.md`, rendered from the twenty committed run records and gated in CI.

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
cannot be solved, and partial credit divides by the declaration. That also closes a reward
hack: an agent can no longer raise its score by stopping tests from running.
`verify-references` fails a task whose reference runs a different count.

### Harbor tasks and ATIF trajectories

`trajectory export harbor` writes every task as a Harbor task directory: the instruction,
an environment with the workspace baked in and the tests left out, a verifier that writes a
reward on every exit path, and the reference playbook as an oracle. CI grades a sample both
ways with no Docker: the oracle must score 1.0 and the untouched workspace 0.0.

`trajectory export atif` writes runs as ATIF trajectories, and all 180 recorded runs
validate against Harbor's own models. `trajectory import atif` scores a trajectory from
Claude Code, Codex, mini-swe-agent or OpenHands with the same metrics and failure modes as a
native run: their tool names map to this harness's, and a closing text turn reads as that
agent's finish, so F03 premature success applies to an agent that says done with the tests
red. A native export round-trips losslessly; on all 180 records the score comes back
identical. See `docs/interop.md`.

### The grader audit

The verifier ran the hidden tests with the agent's workspace at the front of the import
path, so a file the agent dropped there, named after a module the tests import, would have
been loaded in its place. Verification now runs with `PYTHONSAFEPATH` and
`PYTHONNOUSERSITE`. All twelve references still start broken and solve with it on.

Failure mode **F11 grader tampering** names the attempt: an agent that leaves an interpreter
or test-runner hook behind. It is reported rather than blocked, because the verifier already
stops the hook running, and because a run that gamed its grader is exactly what a reward
model must never be paid to repeat. The taxonomy is now eleven modes, eight by rule and three
by the judge. See `docs/grader-audit.md`.

### The results as an RL reward signal

`MODEL-RESULTS.md` ends with each task read as a reward signal: a task whose samples all pass
or all fail gives a group relative method no gradient. Under a binary reward, two of the
eight tasks sampled twice give this model anything to learn from. Partial credit raises that
to six, with a caveat the git-surgery result makes concrete: credit has to be measured above
the starting score. It describes the signal; it is not a training run.

### Fixes

- Timestamps came back in the database server's zone rather than UTC.
- The markdown report labelled every result with the harness version checked out at render
  time rather than the version that recorded the runs.
- An API test pinned the taxonomy at ten modes; it now checks the endpoint serves every
  identifier the harness knows.

### New checks

`check_harbor_export` grades exported tasks both ways. `check_model_numbers` holds every
real model figure to its committed records, the guarantee the published matrix has had since
v0.1.1. The CI test step now names a failing test and the coverage total in annotations, so
a failure says what it is.

801 tests in CI, 89 percent coverage against an enforced floor of 80.

### Install

```
pip install trajectory-eval
trajectory export harbor --out harbor-tasks
trajectory import atif trajectory.json --task py-perf-01 --reward 0
trajectory run --suite core-12 --model stub:methodical
```

Read `MODEL-RESULTS.md` and `RESULTS.md`, and the limitations in both, before quoting
either. Twelve tasks is twelve tasks.
