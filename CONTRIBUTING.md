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
