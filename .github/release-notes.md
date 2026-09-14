## trajectory v0.1.0

An evaluation harness for coding agents that scores the trajectory, not just the outcome.

### What it does

Runs an agent against containerized software engineering tasks with hidden verification
tests, records the complete trajectory, and reports ten metrics plus a classified failure
mode for every unsolved run. The hidden tests are copied into the sandbox only after the
agent has stopped, and there is a test that asserts they are nowhere in the filesystem
before that.

### What is in this release

- `core-12`, twelve tasks across Python, TypeScript, Go and SQL, tiers 1 to 5, each with a
  reference solution that CI replays against its own hidden tests on every push.
- Ten trajectory metrics, each a pure function of a run record, so a metric added later
  can be applied to results published earlier.
- A ten mode failure taxonomy: seven decided by deterministic rules, three by a rubric
  judge whose self agreement is measurable and published.
- A results service and a hosted leaderboard with step by step trajectory replay.

### Install

```
pip install trajectory-eval
trajectory tasks list
trajectory run --suite core-12 --model stub:methodical
```

Read `RESULTS.md` for the published numbers and, more importantly, for the limitations
section. Twelve tasks is twelve tasks.
