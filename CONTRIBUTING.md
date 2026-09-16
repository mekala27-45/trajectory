# Contributing

The most useful contribution is a task. The second most useful is a metric with a test.

## Setup

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22, and a Docker daemon for
anything that runs an agent.

```
git clone https://github.com/mekala27-45/trajectory
cd trajectory
make install
make check
```

`make check` runs exactly what CI runs: ruff, mypy strict, the em dash guard, the test
suite with its coverage floor, and task validation. If it is green locally the pipeline is
green.

### Without make

`make` is a convenience, not a requirement, and it is not present on a default Windows
install. Every target is a thin wrapper, so the equivalents work anywhere uv does. In
PowerShell, note that `&&` is not a statement separator in Windows PowerShell 5.1: run one
command per line.

```powershell
# install uv, once
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# what `make install` does
uv sync --all-packages --all-extras

# what `make check` does
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_no_em_dash.py
uv run python scripts/check_published_numbers.py
uv run python scripts/check_line_endings.py
uv run python scripts/check_local_backend.py
uv run pytest --cov --cov-fail-under=80
uv run trajectory tasks validate --strict

# what `make matrix`, `make fixtures` and `make bundle` do
uv run python scripts/run_matrix.py --parallel 2
uv run python scripts/build_web_bundle.py runs/matrix --fixtures fixtures/recorded-runs
uv run python scripts/build_web_bundle.py --from-fixtures

# what `make tasks-references` does
uv run trajectory tasks verify-references --suite core-12
```

On Windows the Docker backend needs Docker Desktop running with its WSL2 engine, which is
what `trajectory version` reports on.

Working on the web app:

```
make bundle        # generate web/public/data from the committed fixture runs
make web-install
make web-dev
```

Working on the API:

```
make db-up         # Postgres in a container
make db-migrate
make seed          # load the committed fixture runs so there is something to look at
make api-dev
```

## Adding a task

Read [docs/writing-tasks.md](docs/writing-tasks.md) first, then:

```
trajectory tasks new my-task-01 --difficulty 3 --language python
# write workspace/, verify/ and reference/playbook.yaml
trajectory tasks validate --strict
trajectory tasks verify-references --task my-task-01
```

Both commands have to pass. `verify-references` asserts that the unfixed workspace **fails**
its hidden tests and that the reference playbook drives it to passing. A task that starts
green is scored by every agent and measures nothing.

Then measure the shortcut. Apply the plausible wrong fix by hand, run the hidden tests, and
put the number in your pull request. "The naive fix builds green and scores 4 of 9" is the
sentence that tells a reviewer your task works.

## Adding a metric

1. A pure function of a `Run` and its `Task` in `trajectory_core.scoring`. Pure, because
   that is what lets a metric added today be applied to results published last month.
2. A field on `TrajectoryScore`, with a description. The descriptions end up in the API
   schema and the web app.
3. A unit test built around a hand-constructed trajectory, with the arithmetic written out
   in the docstring. A metric nobody can check by hand is a metric nobody should trust.
4. An entry in [docs/metrics.md](docs/metrics.md) with its formula and, if the definition
   involved a judgement call, what the alternative was and why you rejected it.
5. A column in the leaderboard, if it belongs there.

Say explicitly what the metric does when it is undefined. `null` and `0.0` are different
claims, and the difference has already caught two bugs in this repository.

## Adding a failure mode

An entry in `TAXONOMY` with an id, name, definition, detection method and a concrete
example, plus a detector, plus **both** a positive and a negative test.

The negative test is the important one. A taxonomy that fires on correct behaviour gets
ignored, and once it is ignored the real signal goes with it. Every rule in this repository
has a test showing a trajectory that looks similar and is not a failure, and writing one of
those is what found the retry-loop rule's original bug.

## Standards

**No em dashes.** Anywhere: code, comments, docstrings, documentation, commit messages, web
copy. Commas, colons, parentheses, or the word "to" for ranges.
`scripts/check_no_em_dash.py` enforces it as a pre-commit hook and a CI job.

**Line endings are LF, enforced twice.** `.gitattributes` sets `* text=auto eol=lf` so a
clone on any platform gets LF in the working tree, and `scripts/check_line_endings.py` fails
the build if a tracked text file carries a carriage return.

This is not housekeeping. Five of the twelve tasks ship a shell script, and those scripts are
copied into a Linux container and run by `sh`, where a trailing carriage return is part of the
last token rather than whitespace. A valid `set -eu` becomes:

```
vendor/build_repo.sh: 10: set: Illegal option -
```

with the CR invisible in the message. Before `.gitattributes` existed, a Windows clone could
not run those five tasks at all, and it went unnoticed because the repository was authored on
Linux and CI checks out LF. It surfaced the first time anyone ran the Docker backend on
Windows. If you are on Windows and see that error, run `git add --renormalize .` followed by
`git checkout .`.

**Re-recording the matrix is one command, not sixty-four edits.**
`scripts/check_published_numbers.py` recomputes every published figure from the committed run
records, so re-recording the matrix invalidates all of them at once. That gate is the reason
two wrong numbers cannot ship again, and taken alone it would discourage the single
improvement the results section most needs.

```
uv run python scripts/run_matrix.py --parallel 2
make promote RUN_DIR=runs/<the directory that printed>
git diff
```

`promote_matrix.py` inverts the gate. Each claim carries the exact rendered string the
document must contain, so the code that detects a mismatch can repair it: it replaces the old
figure with the new one wherever the gate had already confirmed the old one was present. It
refuses to guess, reporting anything that appears twice or cannot be found, and it will not
run when the files it edits already have uncommitted changes, so `git diff` afterwards shows
exactly what it did.

It updates figures, not meaning. When the sandbox backend changes it lists every line whose
prose still describes the old backend, because those sentences become wrong at the same
instant and no number-based check can see it. Rewriting them is yours.

**Both stages of the API image share one WORKDIR, and that is load bearing.**
`scripts/check_container_paths.py` fails the build if a multi-stage Dockerfile copies a
virtualenv or a package source tree to a different absolute path than it came from.

`uv sync` installs the workspace packages as editable, which writes the absolute source path
into the virtualenv:

```
$ cat .venv/lib/python3.12/site-packages/_editable_impl_trajectory_api.pth
/app/packages/api/src
```

The builder stage used `WORKDIR /build` and the runtime stage `WORKDIR /app`. Copying the
virtualenv across moved it to a prefix whose recorded source path no longer existed, so every
container exited immediately on `ModuleNotFoundError: No module named 'trajectory_api'`. The
image built clean in 28 seconds and the tag published, because `docker build` succeeds and
nothing short of starting the container notices. The runtime stage now also runs
`RUN python -c "import trajectory_api, trajectory_core"`, so the next such break fails the
build with a readable error instead of a health check timing out.

If you are tempted to swap the editable install for `uv sync --no-editable`, read the comment
in the builder stage first. It was measured, and it is worse: uv caches the built wheel by
version, does not rebuild when the real sources arrive in the second sync, and the image
ships the empty stub `__init__.py` files from the dependency caching layer. That fails
silently, which is a strictly worse bug than the one it replaces.

**No absolute path that only exists on one machine.**
`scripts/check_no_machine_paths.py` rejects a tracked file containing a path into a user
home directory, on any of the three platforms, or into this project's container tool
directories. Read the value from the environment or resolve it against the repository root.

Three bugs here had that shape, and only the first was caught by CI:

- five tests drove a verify command needing `pytest`, and passed because the authoring
  machine's system python happened to have it
- `playwright.config.ts` defaulted `PLAYWRIGHT_BROWSERS_PATH` to the authoring container's
  browser cache. `playwright install chromium` does not read that config and installed to
  the default cache, `playwright test` does read it and looked elsewhere, and the web job
  died on a missing browser executable
- `scripts/record_demo.sh` imported Playwright through an absolute `node_modules` path, so
  the script that regenerates the README demo worked for exactly one person

A wrong path is still valid syntax, still lints, and still passes every test on the machine
that wrote it, which is why this needed a gate rather than care.

**`make check` runs the CLI tests twice, and the second run is the point.**
The `ci` workflow sets `FORCE_COLOR=1` so its logs are readable. Rich then highlights
numbers in the CLI's output, and four tests that asserted on plain substrings failed there
while passing on every developer machine: `0 error(s)` arrives as
`\x1b[1;36m0\x1b[0m\x1b[1m error(s)`, and the task id `py-failing-suite-01` arrives as
`py-failing-suite-\x1b[1;36m01\x1b[0m`, split by the number highlighter.

Two changes, because the symptom and the gap are different problems. `invoke()` in
`test_cli.py` now strips styling and keeps the unstripped text on `.raw`, so an assertion
about content cannot fail because of colour. And `make test-colour` re-runs that module with
`FORCE_COLOR=1`, so the environment CI actually uses is exercised locally. Setting
`NO_COLOR` in the invocation is not a fix: `cli.console` is a module level `Console` built
when the app imports, long before any per call environment applies.

**One version, stated the same way in nine places.** `scripts/check_version.py` reads the
four `pyproject.toml` files, the `HARNESS_VERSION` constant stamped into every run record,
the resolved versions in `uv.lock` and the heading of `.github/release-notes.md`, and fails
when they disagree. On a tagged build it also checks the tag, reading `GITHUB_REF_NAME`
itself.

Bumping a version means all of them, in this order: the four pyprojects, then
`HARNESS_VERSION`, then `uv lock`, then the release notes heading. Run
`uv run python scripts/check_version.py --tag vX.Y.Z` before pushing the tag.

The check exists because every one of those failures is silent. A wheel built with the
previous number still installs. A run record with a stale `harness_version` still validates,
in a project whose whole argument is that the run record tells you what produced a number.
And a release page announcing the previous version renders perfectly. When `v0.1.1` was
prepared, six of the nine sources still said `0.1.0`.

**Publishing to PyPI is opt in.** The `release` workflow runs on a `v*` tag and always
builds both wheels, checks their metadata, pushes the API image to GHCR and cuts a GitHub
release. It uploads to PyPI only when the repository variable `PUBLISH_TO_PYPI` is set to
`true`, because trusted publishing cannot be probed before it is used: without a publisher
configured on PyPI the upload fails, and a failed upload used to take the GitHub release
down with it. Set the variable once the publisher exists.

**Two test dependencies live outside this repository, and both announce themselves.**

A Docker daemon, for the container backend. And a host `python` that can run
`python -m pytest`, for the tests that drive a verify command through the local backend: a
task's verify command is written against the task image, where the Dockerfile installs
pytest, and the local backend has no image. Tests needing either are marked `docker` or
`local_verify` and skip with a reason naming what is missing, so a clone on a fresh machine
is green for everything that does not need them.

CI has to have both, because a silent skip in CI means a class of behaviour stops being
covered and nobody notices. `scripts/check_local_backend.py` asserts the second one and
prints the PATH and interpreter it resolved, so a mismatch is obvious from the log.

That check exists because of a specific failure. The workflow ran
`python3 -m pip install pytest`, which on a hosted runner installs for the toolcache
interpreter, while the sandbox strips the workflow's virtualenv from PATH and resolves
`/usr/bin/python`. Five tests failed with nothing but `No module named pytest` to go on,
and they had passed on the authoring machine, where the system interpreter happened to
have pytest. Install for the interpreter the sandbox resolves, named explicitly, not for
whichever `python3` comes first.

**Every number in README.md and RESULTS.md is recomputed in CI.**
`scripts/check_published_numbers.py` re-derives each published figure from the committed run
records in `fixtures/recorded-runs` and fails the build when a document disagrees. It exists
because two hand-typed figures shipped wrong: a metric cell that read `n/a` when the metric
had a value, and a failure hit count that read 177 when the records said 213. Lint reads
code and tests read code, so a number typed into a markdown table was read by nobody.

Two consequences worth knowing before you edit either document:

- Re-recording the matrix means updating the prose in the same commit. The gate names each
  figure it could not find, so the failure output is the work list.
- The check matches on the rendered value with whitespace collapsed, not on a regex over
  the sentence around it. Rewording and reflowing prose is free; changing a number is not.

**mypy strict, ruff clean.** No exceptions checked in. A `noqa` needs a reason on the same
line.

**Docstrings say why.** What the code does is visible in the code. Why it does it that way,
and what the alternative was, is not.

**Coverage floor of 80 percent.** Currently 86. The floor is a floor, not a target.

**Conventional commits.** `feat(core):`, `fix(runner):`, `docs:`, `ci:`, `test(api):`. The
body is for the reasoning, and it is worth writing: a commit that explains why a metric
returns null instead of zero has saved someone an afternoon.

## Tests that need something

Some tests need a Docker daemon or a Postgres instance. They are marked and they skip with
a reason rather than failing or, worse, silently passing against a substitute:

```
pytest -m "not docker and not postgres"   # everything that needs neither
TRAJECTORY_TEST_DATABASE_URL=... pytest   # the API suite against a real Postgres
```

The API suite never runs against SQLite. The service uses JSONB and timezone aware
timestamps, so a SQLite substitute would be testing a database it never talks to.

## Pull requests

- One thing per pull request.
- `make check` green.
- If you changed a number that appears in `RESULTS.md` or the README, rerun the measurement
  and update it. Every number in this repository is measured, and the way that stays true
  is that nobody estimates one as a stopgap.
- If you changed a metric definition, say what happens to previously published results.
  They can be rescored, which is the point of keeping the metrics pure, so say whether you
  did.

## Reporting a result that looks wrong

Attach the run record. It carries the harness version, the resolved image ID, the sandbox
backend and the full trajectory, which together answer most questions about whether the
harness moved or the environment did.
