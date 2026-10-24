# Interoperability: Harbor tasks and ATIF trajectories

A benchmark only its own harness can run is a walled garden. This one speaks the two formats
an evaluation stack built on Harbor already uses: the Harbor **task** format, for running
these tasks somewhere else, and **ATIF**, the trajectory format, for scoring an agent run
somewhere else with the metrics here.

## Harbor tasks

```
trajectory export harbor --out harbor-tasks            # the whole suite
trajectory export harbor --out harbor-tasks py-perf-01 # one task
```

Each task becomes a Harbor task directory:

| path | what it holds |
| --- | --- |
| `task.toml` | limits and metadata, Harbor schema 1.0 |
| `instruction.md` | the agent prompt, verbatim, the only thing the agent sees |
| `environment/` | the task Dockerfile with the workspace baked in, never the tests |
| `tests/test.sh` | the verifier, which writes a reward on every exit path |
| `tests/verify/` | the hidden tests, present only at grading time |
| `solution/solve.sh` | the reference playbook replayed as an oracle |

The reward is binary, solved or not, and `partial_credit` carries the fraction of hidden
tests that passed, so a task that starts four of eight green reads as reward 0 with partial
credit 0.5 rather than as a partial success. The export refuses a Dockerfile that would copy
the tests into the agent's image.

`scripts/check_harbor_export.py` runs in CI with no Docker. It grades a sample of exports
both ways: the oracle must score 1.0 and the untouched workspace 0.0. A task that cannot
tell its own solution from its starting state has a broken verifier, which is the one
defect an exporter must never ship.

## ATIF trajectories

```
trajectory export atif runs/my-matrix --out atif/          # one file per run
trajectory import atif trial/agent/trajectory.json \
    --task py-perf-01 --reward 0.0                          # score any agent's run
```

**Export.** The task statement is the opening user turn, then each native step, one tool
call and its output, becomes one agent turn holding both, because ATIF only lets an
observation refer to a tool call in its own step. What ATIF has no field for travels in its
`extra` objects: exit codes, timings, the workspace manifests and the run's outcome. All 180
recorded runs export, and all 180 validate against Harbor's own pydantic models.

**Import.** A trajectory from Claude Code, Codex, mini-swe-agent or OpenHands scores with the
same metrics and failure modes as a native run. Three adaptations make that honest:

- Their tool names map to this harness's, so Claude Code's `Bash` and `Read` reach the
  command based metrics, and a `file_path` argument is read as `path`.
- A closing text-only turn is read as the agent's `finish`. Those agents end by saying they
  are done rather than by calling a tool, and that sentence is the claim F03 premature
  success tests. A text turn followed by more tool calls is skipped as thinking aloud.
- The result carries an `atif:` model prefix and the local backend, so it never lands on a
  leaderboard. It scores another machine's trajectory; it does not measure one.

**Round trip.** An export imports back losslessly. On every one of the 180 records the
steps, the outcome, the failure modes and the full score come back identical, and that is a
test.

**The one limit.** ATIF has no workspace snapshot. Three rules compare the workspace before
and after a run: F02 path hallucination, F09 scope creep and F11 grader tampering. A native
export carries both manifests, so those three survive its round trip. A foreign trajectory
has none, so on it those three stay silent rather than guess. Every other metric and mode
applies in full.

## Why both

Harbor task export lets a lab run these twelve tasks with its own agents and its own
infrastructure. ATIF import lets the same lab bring the trajectories back and ask the
questions a reward cannot answer: did the agent recover from a failed command, did it repeat
itself, did it say it was done while the tests were red, did it try to plant something in
the grader's path. The reward says whether an agent passed. The trajectory says how.
