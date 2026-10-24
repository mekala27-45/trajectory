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
three ways: the oracle must score 1.0, the untouched workspace 0.0, and the untouched
workspace with a module named after the test runner planted at its root must grade exactly
like the untouched one and never run that module. The first catches a verifier that cannot
tell a solution from its starting state; the third catches one an agent could talk into a
pass. It checks the export against its own oracle, not against a run of the native verifier,
and it is a sample, not the suite; the Harbor-model check below covers all twelve.

## ATIF trajectories

```
trajectory export atif runs/my-matrix --out atif/          # one file per run
trajectory import atif trial/agent/trajectory.json \
    --task py-perf-01 --reward 0.0                          # score any agent's run
```

**Export.** The task statement is the opening user turn, then each native step, one tool
call and its output, becomes one agent turn holding both, because ATIF only lets an
observation refer to a tool call in its own step. What ATIF has no field for travels in its
`extra` objects: exit codes, timings, the workspace manifests and the full verification
record. All 200 committed runs export, and a CI step installs Harbor itself and validates
every one against Harbor's `Trajectory` model, with every exported `task.toml` validated
against Harbor's `TaskConfig`. The check imports nothing from this repository, so it cannot
be satisfied by a local reimplementation of Harbor's rules.

**Import.** A trajectory from Claude Code, Codex, mini-swe-agent or OpenHands scores with the
metrics its record can support. Three adaptations make the mapping honest:

- Their tool names map to this harness's, so Claude Code's `Bash` and `Read` reach the
  command based metrics, and a `file_path` argument is read as `path`.
- A closing text-only turn is read as the agent's `finish`. Those agents end by saying they
  are done rather than by calling a tool, and that sentence is the claim F03 premature
  success tests. A text turn followed by more tool calls is skipped as thinking aloud.
- The result carries an `atif:` model prefix and the local backend. The leaderboard sorts
  local-backend rows last and labels them, and these are meant to be read as another
  machine's trajectory, not a measurement this harness made. Nothing yet filters an `atif:`
  row out of an aggregate, so treat the prefix as a label, not a guarantee.

**Round trip.** A native export imports back without loss. On every one of the 200 records
the steps, the outcome, the failure modes, the full score and the verification record,
exit code included, come back identical, and that is a test. A few fields are deliberately
not preserved, because they describe where the run happened rather than what it did: the run
id, the sandbox backend, which becomes local, and the image id.

**What a foreign trajectory cannot carry.** ATIF has no field for a command's exit code or
for a malformed tool call, and no workspace snapshot. This harness writes those into `extra`,
so its own round trip keeps them, but a trajectory from another agent has none of them. On a
foreign trajectory, then: the three workspace-diff rules, F02 path hallucination, F09 scope
creep and F11 grader tampering, stay silent; recovery rate and F01 and F05, which need to
know a command failed, cannot fire; and tool call validity, which counts malformed calls,
reads a clean 1.0. What does apply is every metric computed from the shape of the trajectory,
redundancy, step count and efficiency among them, and F03 premature success, read from the
closing turn. The import is a real scoring of another agent's process, with the parts its
format cannot express named rather than faked.

## Why both

Harbor task export lets a lab run these twelve tasks with its own agents and its own
infrastructure. ATIF import lets the same lab bring the trajectories back and ask the
questions a reward cannot answer: for a run recorded here, did the agent recover from a
failed command, repeat itself, say it was done while the tests were red, or plant something
in the grader's path; for a foreign trajectory, whichever of those its format carried. The
reward says whether an agent passed. The trajectory says how.
