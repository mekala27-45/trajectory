## trajectory v0.1.1

A fix release. `v0.1.0` passed every gate in the repository and then failed five of six CI
jobs the first time it ran on a machine with a Docker daemon, which the authoring machine
did not have. Nothing below is a logic error. Six of the eight are the same bug in different
clothes: something true about one machine, committed as though it were true everywhere.

### Two of the twelve task images could never be built

Every task Dockerfile created the agent at uid 1000, the conventional first human uid on
Debian. It is also the uid the official `node` images already give their own `node` user, so
both TypeScript images failed at `useradd` and never existed. Now 10001, and validation
rejects a task that creates the agent below that.

### Five tasks could not run from a Windows clone

No `.gitattributes`, so git checked the tasks' shell scripts out with CRLF, and `sh` reads a
trailing carriage return as part of the last token. A valid `set -eu` became
`vendor/build_repo.sh: 10: set: Illegal option -`, with the character causing it invisible in
the message. `.gitattributes` now pins LF and a check rejects a carriage return in anything a
container executes.

### The API image built clean and could not import its own code

`uv sync` installs the workspace packages as editable, which records the absolute source path
inside the virtualenv. The two build stages used different working directories, so the
runtime image looked for a path that was not in it and every container exited on
`ModuleNotFoundError: No module named 'trajectory_api'` before the server started. The image
now smoke tests its own imports at build time, and a check holds the invariant statically.

If you pulled `ghcr.io/.../api:0.1.0`, it does not start. This is the fix.

### One task was unrunnable and the harness reported it as a low score

The sandbox mounted `/tmp` with `noexec`. `go test` links its test binary into `$GOTMPDIR`
and then executes it, so the Go task died before a single test ran. With no result lines to
count, the exit code fallback recorded `0/1`, which is also what the harness prints when a
task's every test fails: the symptom pointed away from the cause. `noexec` is gone, with the
reasoning recorded, and the size cap stays.

`tasks verify-references` now prints what the hidden tests actually said instead of a verdict,
and distinguishes "every test failed" from "the tests never ran".

### The rest

- The Playwright config pinned a browser directory that existed on one machine, so the two CI
  steps resolved different paths and the web job failed on a missing browser.
- Five tests needed `pytest` on the host and passed only because the authoring machine
  happened to have it. They declare it now, and CI installs it for the interpreter the
  sandbox actually resolves.
- A tagged release produced no release: PyPI trusted publishing cannot be probed before use,
  it failed, and the release job depended on it. PyPI is opt in now.
- The field kept "for triage" stored stderr, while every test runner here reports on stdout,
  so it was empty on all 180 recorded runs including the 25 that failed. It stores both
  streams.

### New checks, because a lesson decays and a gate does not

`check_line_endings`, `check_container_paths`, `check_no_machine_paths`, `check_version`, and
`promote_matrix` for re-recording the published matrix without retyping 64 figures by hand.

687 tests, 86.7 percent coverage against an enforced floor.

### Install

```
pip install trajectory-eval
trajectory tasks list
trajectory run --suite core-12 --model stub:methodical
```

Read `RESULTS.md` for the published numbers and, more importantly, for the limitations
section. Twelve tasks is twelve tasks.
