# Writing tasks

This document is what turns `core-12` from one person's benchmark into something other
people can contribute to. It is also the honest answer to "why should I trust these
numbers", because the quality of a benchmark is the quality of its tasks and nothing else.

## What a good task is

**Unambiguous success criteria.** A careful reader should be able to tell whether they are
done without guessing. If two competent engineers would disagree about whether a
submission is correct, the task is measuring taste, not capability.

**Hidden tests the agent cannot read.** They live in `verify/`, they are copied into the
sandbox only after the agent has stopped, and the Dockerfile must never bake them in.
Validation rejects a `COPY` that touches `verify/` or `reference/`, and there is a test
asserting the hidden tests are nowhere in the container filesystem during the agent phase.
An agent that can read the tests will read the tests, and the benchmark is worthless from
the first time it happens quietly.

**A reference solution that actually passes.** Expressed as `reference/playbook.yaml`, a
trajectory rather than a patch. CI replays it against the hidden tests on every push. A
task whose own reference solution has rotted cannot be solved by anyone, and the suite
average moves for reasons that have nothing to do with any model.

**No network access.** Everything the task needs is in the image or the workspace. The
Dockerfile may use the network at build time; the agent phase may not. A task that depends
on a live service stops being reproducible the moment that service changes, and you will
not notice until a published number moves.

**Deterministic setup.** Same starting state on every run. `setup_cmd` runs before the
agent and before the workspace is photographed, so use it for anything that cannot be
baked into the image: decompressing a fixture, generating a seeded dataset, building a
throwaway git repository.

**A plausible wrong fix that the hidden tests reject.** This is the part most task authors
skip and it is what separates a task that measures engineering from one that measures
typing. Every task in `core-12` has one, named in its `description`:

| Task | The shortcut that must not pass |
| --- | --- |
| `ts-build-break-01` | Turning `strict` off, or suppressing with `@ts-ignore` |
| `py-flaky-test-01` | Deleting the test, loosening the assertion, seeding the global RNG |
| `py-perf-01` | Caching the answer for the committed fixture |
| `py-etl-schema-01` | A blanket `except` that silently drops the unparseable rows |
| `sql-migration-01` | A migration that produces the right column and is unsafe to run |
| `git-surgery-01` | `git reset --hard` before inspecting the reflog |

Write the check for the shortcut before you write the check for the fix. It is easy to
verify afterwards that a wrong fix really does score lower, and that number belongs in
your pull request.

**A difficulty rating justified against the reference step count.** Tier 1 means everything
needed is on screen. Tier 5 means the agent needs a correct mental model of a tool's
internal state. `reference_step_count` must equal the playbook length, so the only way to
claim a task is long is to write the steps.

## Directory layout

```
tasks/<suite>/<task-id>/
  task.yaml                Definition. id and suite come from the path, do not set them.
  Dockerfile               The agent environment. Non-root USER, pinned base image.
  workspace/               Exactly what the agent sees. The working directory at run time.
  verify/                  Hidden tests. Copied in only after the agent stops.
  reference/playbook.yaml  The reference solution, as an ordered list of tool calls.
  vendor/                  Optional. Committed generators for large fixtures.
```

Scaffold one that already validates:

```
trajectory tasks new py-import-cycle-01 --difficulty 3 --language python
```

## The reference playbook

A list of tool calls, ending with `finish`. Available tools and their arguments:

```yaml
steps:
  - tool: bash
    args: { command: "pytest -q" }
    note: "Why an expert does this, in one line."
  - tool: read_file
    args: { path: "src/app.py" }
  - tool: write_file
    args: { path: "src/app.py", content: "..." }
  - tool: list_dir
    args: { path: "." }
  - tool: finish
    args: { summary: "The cause, not just the change." }
```

Write the `note` fields for a contributor, not for a runtime. They are the only place the
reasoning behind a task's intended approach is recorded, and they are what someone reads
when they want to know whether your difficulty rating is fair.

The playbook does four jobs, which is why it is a trajectory and not a patch file:

1. It defines `reference_step_count`, the numerator of step efficiency.
2. It is what the offline scripted policies replay, so the whole pipeline can be exercised
   with no provider and no spend.
3. It is the CI gate: replay it, run the hidden tests, and a rotted task fails the build.
4. It documents the intended approach, including the judgement call the task is really
   about.

Two YAML details that will bite you. An unquoted scalar containing `": "` breaks the
parser, so quote those notes or use a `>` block. And use relative paths everywhere:
absolute `/workspace/...` paths work on the Docker backend and fail on the local one, and
validation warns about them for that reason.

## Hidden tests

Aim for six to ten independent assertions so partial credit is informative. One test that
checks everything collapses a run that fixed most of the problem into the same score as a
run that did nothing.

Say in the suite's module docstring which assertions exist to reject the plausible wrong
fix. A reader deserves to know which checks are about correctness and which are about
shortcuts.

The tests run with the workspace as the working directory and find themselves through
`$VERIFY_DIR`. Supported output parsers are `pytest`, `go_test` (run with `-v`, it counts
`--- PASS:` lines), `node_tap`, `json_report` (the last JSON object on stdout with
`passed` and `total` keys), and `exit_code`.

One trap worth knowing: if a hidden test shells out to another `pytest` and lets that
output through, the harness's parser can pick up the inner summary line and report a
wrong count. Bind the return code to a variable and keep the inner output out of the
captured stream.

## Large fixtures

Do not commit megabytes of generated data. Write a generator under `vendor/`, commit the
generator and a compressed artefact, and decompress in `setup_cmd`. `log-forensics-01`
does this: a committed script produces 40,029 deterministic log lines and the expected
findings, the workspace ships a 418 KiB gzip, and `setup_cmd` unpacks it.

Keep the generator outside `workspace/`. An agent that can read the generator is solving a
different task.

## Base images

Pin an exact patch version. Validation rejects `latest` and any floating tag.

Digests would be better and are not used here, for a reason worth stating rather than
hiding: no container registry was reachable from the machine these tasks were authored on,
so a digest could not be verified, and a digest that is wrong is worse than a tag that is
precise. Instead the harness records the resolved image ID on **every run**, which is a
stronger guarantee: a tag can move, a content address cannot, and when a published number
shifts the run record tells you whether the environment changed or the model did. CI also
records the image IDs it built from as an artefact.

If you want to move the repository to digest pinning, that is a welcome pull request.

### Create the agent at uid 10001 or above

Validation enforces it. The reason is a bug that shipped in all twelve tasks:

```
useradd: UID 1000 is not unique
```

Every task created the agent with `useradd --create-home --uid 1000`, which is the
conventional first human uid on Debian and looks entirely reasonable. It is also the uid the
official `node` images already gave to their own `node` user, so on the two TypeScript tasks
that `RUN` line failed and the image never built. Two of twelve tasks could not be built at
all, and neither the authoring machine nor a code review could see it: it only appears the
moment someone runs `docker build`.

10001 is free on every base image the suite uses and matches the uid the API image runs as.
Nothing in the harness depends on the number, only on the user being non-root and named
`agent`, so if you have a reason to pick a different one, pick another high one.

The wider lesson for a task author: a Dockerfile is not reviewed code, it is executed code,
and the only review that counts is a build. Run `trajectory tasks verify-references` on your
task before you open a pull request, which is the next section.

## What the sandbox gives you, and what it does not

The container runs as `agent`, non-root, with all capabilities dropped, no new privileges,
no network during the agent phase, a memory ceiling of 2048 MB with swap disabled, a CPU
ceiling of 2 cores, a PID ceiling of 256, and `/tmp` on a 256 MB `nosuid` tmpfs. `/workspace`
is writable and executable, because writing and running code is the task.

`/tmp` is executable too, and that is deliberate rather than accidental. It was `noexec`
once, which broke `go-race-01` outright: `go test` links the test binary into `$GOTMPDIR`,
which defaults to `/tmp`, and then runs it, so the task died on

```
fork/exec /tmp/go-build.../b001/zz_hidden.test: permission denied
```

before a single test executed. The harness reported `0/1`, which is also what it prints when
a task's every test fails, so the symptom said nothing about the cause. If you are writing a
task in a compiled language, you can rely on staging an executable in `TMPDIR`, and there is
a test asserting it.

Two ceilings worth knowing if your task is heavy. `go test -race` on the Go task peaks at
roughly 420 MB of memory and 107 MB of temporary files, both measured, so there is headroom;
if your task needs more than 2048 MB it should say so in `task.yaml` and say why in the pull
request. And the CPU ceiling is a quota, not a core count: `runtime.NumCPU` and
`os.cpu_count` still report the host's, so a build that parallelises by core count will spawn
far more processes than it gets CPU for. That is fine, just slow.

## Before you open a pull request

```
trajectory tasks validate --strict
trajectory tasks verify-references --task <your-task-id>
```

The second command asserts three things:

- the unfixed workspace **fails** its hidden tests (a task that starts green is scored by
  every agent and measures nothing)
- the reference playbook drives it to **passing**
- the run completes rather than timing out

Then measure the shortcut. Apply the plausible wrong fix by hand, run the hidden tests,
and put the score in your pull request. "The naive fix builds green and scores 4 of 9" is
the sentence that tells a reviewer your task works.

## Checklist

- [ ] `trajectory tasks validate --strict` is clean
- [ ] `trajectory tasks verify-references` passes
- [ ] The unfixed workspace fails, and the measured score of the plausible wrong fix is in
      the pull request
- [ ] No network access during the agent phase
- [ ] Dockerfile drops root, creates the agent at uid 10001 or above, pins an exact base
      version, copies neither `verify/` nor `reference/`
- [ ] `relevant_paths` covers the files the task is legitimately about, so scope creep is
      detectable
- [ ] Six to ten independent hidden assertions
- [ ] Relative paths everywhere
- [ ] No em dashes anywhere (`scripts/check_no_em_dash.py`)
- [ ] The task directory is under 1 MB
