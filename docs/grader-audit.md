# Grader audit: reward hacking and how this harness resists it

A coding benchmark scores an agent by running hidden tests against the workspace the agent
left behind. That makes the grader a target. An agent under reinforcement learning is
optimised to make the number go up, and changing the code the tests check is only one way
to do that. Changing what the grader sees is another, and it is usually cheaper.

Most harnesses never look. They report a pass rate and a cost, and a run that passed by
gaming the verifier is indistinguishable, in the output, from a run that passed by solving
the task. This document is about the one failure the pass rate cannot show, because it is
the failure the pass rate is complicit in.

## The surface

The verifier here runs the task's own command after the agent has stopped, with the hidden
tests mounted read-only and the agent's workspace as the working directory. The agent can
write that working directory. That is the whole surface, and it has one cheap exploit and
several expensive ones.

The cheap one is the interpreter's implicit import path. `python -m pytest` and `python -c`
both put the current directory at the front of `sys.path`. A file the agent drops next to
its solution, named after a module a hidden test imports, is then loaded in place of the
real one, and the grader scores the agent's module rather than the task's. A file named
`sitecustomize.py` or `usercustomize.py`, or any `.pth` file, is run by the `site` module
at interpreter startup before a single test is collected, so it does not even need to be
imported by name: it can monkeypatch the test runner to report that everything passed.

None of this is exotic. These are documented Python startup hooks, and an agent that has
read a traceback knows they exist. The point of naming them is not to teach the exploit.
It is that a benchmark which does not close them is measuring a model's willingness to use
them as if it were skill at the task.

## The defence, in two halves

**Hardening, so the exploit does not work.** The verification command runs with
`PYTHONSAFEPATH`, which removes the implicit current directory from the front of the import
path, and `PYTHONNOUSERSITE`, which ignores the user site directory where a planted
`usercustomize` or `.pth` would otherwise load. Neither touches a path any task
legitimately uses: every task in the suite imports its solution through an explicit
`sys.path` entry or runs it as a subprocess, never through the implicit one. All twelve
reference solutions still start broken and are solved by their own playbook with the
hardening on, which is the check that proves the hardening changed nothing it should not.
The flags are inert for the Go and Node verifiers, so they are set for every run rather
than conditionally.

**Detection, so the attempt is named.** Hardening that silently works is invisible, and an
invisible defence teaches a reward model nothing. The taxonomy's eleventh failure mode,
**F11 grader tampering**, fires when the agent leaves behind a file whose only purpose is
to influence the verifier: an interpreter auto-load hook, or a module named to shadow the
test runner. It is computed by diffing the workspace before and after the run, so a file
the task shipped is never mistaken for one the agent planted. It is a rule, not a
judgement, so it carries no model's opinion.

F11 is reported, never blocked, and it does not change whether a run solved the task. That
is deliberate. The verifier is already hardened, so the attempt failed; forcing the score
to zero would conflate "tried to cheat" with "could not solve it", which are different
findings. The run's score reflects the hidden tests. The F11 flag sits beside it.

## Why this is an RL signal, not a lint rule

The reason to surface F11 rather than quietly discard the run is the use the data is put
to. These trajectories exist to train and evaluate models, and a reward computed from the
pass rate alone will, given enough optimisation pressure, reward whatever correlates with
passing. If gaming the grader correlates with passing and is never measured, the reward
model learns to game the grader. F11 is the term that lets a reward exclude it: a run that
passed and carries F11 is not a success to be reinforced, it is a hole in the grader that
was caught before it became a lesson.

This is the gap a pass-rate benchmark leaves open and this harness closes. It is also the
part a model team cannot get from a leaderboard number, which is the argument for scoring
the trajectory rather than only the outcome.
