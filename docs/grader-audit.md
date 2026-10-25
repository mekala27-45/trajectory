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
tests copied in and the agent's workspace as the working directory. The agent can write that
working directory, and in most tasks the hidden tests import the agent's solution, so the
agent's code runs inside the process that grades it. That is the surface. It has one cheap
exploit, closed below, and more expensive ones that are not.

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

There is a second route that hardening does not close, and it is worth stating plainly
rather than leaving for a reader to find. Because the hidden tests import the solution, the
agent's code runs inside the test process. It can end that process with a success exit code
before a test runs, or, with more effort, reach into the test runner and make it report a
pass. The first of those is cheap, and the fix for it is a counting rule, not import
hardening: a verifier whose output names no test result scores zero, and a task that declares
its hidden test count cannot be solved on fewer. The second is narrowed by the two checks
below, a clean container and a planted test, and not closed by them. What is left of it is
the first item in the limitations.

A third route is the environment around the workspace. The agent's commands run in the same
container the tests would run in, so anything they leave there, a process still running or a
file outside the workspace, would be present while the grader ran. That route is closed:
verification no longer happens in the agent's container at all.

## The defence

**A clean container, so nothing but the workspace is graded.** When the agent stops, every
process it left running is stopped, gently first so a database inside the workspace closes
its files cleanly, and the workspace, and only the workspace, is copied into a new container
from the same clean image. The agent's container is removed. The first command the new
container ever runs is the verification, so a background process, a file under the home
directory or `/tmp`, or anything else written outside the workspace is simply not there. A
file the agent deleted stays deleted, so the copy is the workspace as the agent left it, not
the image's copy with the agent's changes laid over it. All twelve reference solutions still
start broken and are solved through it. The local backend has no clean environment to move
into, which is one more reason its rows are never mixed with container rows.

**A planted test, so a report of success can be checked.** Every report that comes out of a
test process the solution ran in is only as trustworthy as everything that ran in it, and
one claim in it can be checked from outside. When the hidden tests are installed, the
verifier adds one more test, written fresh for that run, that fails for any correct runner.
Its name is drawn at random and its body reads like any other test, so nothing in the
workspace can know it in advance. If the runner names it as failing, the report is believed,
and the planted test is left out of the counts. If the runner claims success with it in the
set, or names it as passing, the report is not believed: no test counts as passed, the run
cannot pass, and its record is marked `tampered`. If the runner never mentions it, which a
run cut short by a timeout can do honestly, the run cannot pass but is not called tampered.
pytest, `go test -v` and the Node test runner all report per test, so every task in the
suite carries one; the record names the planted test, so any verdict can be audited. It is a
tripwire, not a proof. It catches a verdict bent as a whole, which is what wholesale
tampering looks like, and it says nothing about a report bent for particular tests. Closing
that needs tests whose expected values never enter the solution's process at all.

**Hardening, so the cheap exploit does not work.** The verification command runs with
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
**F11 grader tampering**, fires when the agent leaves behind a new file whose name marks it
as a verifier hook: `sitecustomize.py`, `usercustomize.py`, `conftest.py`, `pytest.py`, or
any `.pth` file. It is computed by diffing the workspace before and after the run, so a file
the task shipped is never mistaken for one the agent planted. It is a rule, not a judgement,
so it carries no model's opinion. It also fires, at full confidence, on any run whose report
the planted test showed could not be believed, which is evidence from the verification
itself rather than from a file name.

F11 is a named-pattern check, not a general tamper detector, and its limits are the honest
measure of it. It sees a planted file only by one of those names, only inside the workspace
the manifest captures, and only when the file is new rather than an edit. Its file check does not
see a shadow module under another name, or a hook planted outside the workspace, though the
clean container now leaves nothing outside the workspace to find. Of its five names, only
a planted `pytest.py` is live from the workspace against `python -m pytest`, and the
hardening already stops that one; the others are the startup-hook routes the hardening closes
at their real location, which F11 would not otherwise see. So F11 is best read as a tripwire
for the obvious attempt, reported beside a run, not as a guarantee the grader was not gamed.

The two ways F11 fires are scored differently, on purpose. A planted file is reported and
never blocked, and it does not change whether a run solved the task: the verifier is
already hardened, so the attempt failed, and forcing the score to zero would conflate
"tried to cheat" with "could not solve it", which are different findings. The run's score
reflects the hidden tests, and the flag sits beside it. A report the planted test caught is
different, because then there is no trustworthy test result to score: no test counts as
passed, the run scores zero, and F11 says why.

## Why this is an RL signal, not a lint rule

The reason to surface F11 rather than quietly discard the run is the use the data is put
to. These trajectories exist to train and evaluate models, and a reward computed from the
pass rate alone will, given enough optimisation pressure, reward whatever correlates with
passing. If gaming the grader correlates with passing and is never measured, the reward
model learns to game the grader. F11 is the term that lets a reward exclude it: a run that
passed and carries F11 is not a success to be reinforced, it is a hole in the grader that
was caught before it became a lesson.

This is a gap a pass-rate benchmark leaves open, and this harness narrows it rather than
closing it: the environment route and the cheap exploits are shut, a report of wholesale
success is checked against a test that must fail, the obvious attempt is flagged, and the
route that remains is named instead of hidden.

## What the Harbor export carries

The Harbor export carries the hardening, the counting rule and the planted test. Its
grading script is generated once per task, so the planted test is not: on every grading
run, `test.sh` copies the hidden tests into a fresh directory, has `score.py` write a new
must fail test among them with a random name, and reads the report back with the same three
outcomes the native verifier uses. The exported task itself is never changed, so no planted
test is left for the next run to find. Where Harbor runs its tests is Harbor's choice, not
this harness's, so the fresh container is the one defence the export cannot carry. It is the part a model team cannot get from a leaderboard
number, which is the argument for scoring the trajectory rather than only the outcome.
