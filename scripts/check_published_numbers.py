#!/usr/bin/env python
"""Re-derive every number published in README.md and RESULTS.md from the run records.

Two figures shipped wrong before this existed. `stub:hasty` step efficiency was printed as
`n/a` when the metric has a value, and the failure hit count on passing runs was printed as
177 when the records say 213. Both survived review because prose is not checked by anything:
lint reads code, tests read code, and a hand-typed number in a markdown table is read by
nobody until a reader recomputes it.

So this recomputes each published figure from `fixtures/recorded-runs`, renders it exactly
as the document should show it, and asserts that string is present in the document. The
assertion is on the rendered value rather than on a regex over the sentence around it, which
means rewording the prose does not break the gate, and changing the data does.

Exit status is 0 when every claim holds and 1 otherwise, with each failure naming the
document, the claim and the text that should have been there.

Run it directly:

    uv run python scripts/check_published_numbers.py

Raises nothing. Every failure is reported and counted so one run lists all of them.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Literal

from trajectory_core import aggregate
from trajectory_core.models import Run
from trajectory_core.scoring import destructive_attempts

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "recorded-runs"
README = ROOT / "README.md"
RESULTS = ROOT / "RESULTS.md"


@dataclass(frozen=True)
class Claim:
    """One published figure: where it appears, what it means, and its rendered form.

    `present` is False for a claim that the text must be gone, which is how a stale
    statement gets caught: once the judge has run, a document still saying `context_drift`
    is null everywhere is wrong even though every other number checks out.
    """

    document: Path
    label: str
    text: str
    present: bool = True


def load_runs() -> list[Run]:
    """Read every committed run fixture, or exit if the directory is empty."""
    paths = sorted(FIXTURES.glob("*.json"))
    if not paths:
        print(f"no run fixtures under {FIXTURES.relative_to(ROOT)}", file=sys.stderr)
        raise SystemExit(1)
    return [Run.model_validate_json(path.read_text()) for path in paths]


def totals_claims(runs: list[Run]) -> list[Claim]:
    """The suite-level counts that head both documents."""
    solved = sum(run.solved for run in runs)
    steps = sum(len(run.steps) for run in runs)
    seconds = sum(run.wall_clock_s for run in runs)
    spend = sum(run.total_cost_usd for run in runs)
    tasks = len({run.task_id for run in runs})
    seeds = len({run.config.seed for run in runs})
    minutes = seconds / 60.0

    # The spend line is the one claim that must be exact rather than rounded. A rounded
    # zero and a measured zero read identically and mean different things.
    if spend != 0.0:
        return [
            Claim(README, "total spend is no longer zero", f"{spend:.2f} USD"),
        ]

    return [
        Claim(
            README,
            "suite totals line",
            f"{len(runs)} runs, {tasks} tasks, {seeds} seeds, {minutes:.1f} minutes, 0.00 USD.",
        ),
        Claim(RESULTS, "run count", f"| Runs | {len(runs)} |"),
        Claim(RESULTS, "solved count", f"| Solved | {solved} |"),
        Claim(RESULTS, "unsolved count", f"| Unsolved | {len(runs) - solved} |"),
        Claim(RESULTS, "recorded steps", f"| Trajectory steps recorded | {steps:,} |"),
        Claim(
            RESULTS,
            "total wall clock",
            f"| Total wall clock | {minutes:.1f} minutes ({seconds:,.0f} s) |",
        ),
    ]


def provenance_claims(runs: list[Run]) -> list[Claim]:
    """The harness and schema version the published matrix was actually recorded with.

    This is a fact about the stored data, not about the current checkout, so it must not
    follow a version bump: the matrix was measured with 0.1.0 and always will have been.
    `scripts/check_version.py` deliberately does not look at it for that reason, which
    left it ungated until a bump made the distinction matter. A mixed set of records is
    reported rather than averaged, because a matrix recorded by two harness versions is
    not one measurement.
    """
    harness = sorted({run.harness_version for run in runs})
    schema = sorted({run.schema_version for run in runs})
    if len(harness) != 1 or len(schema) != 1:
        return [
            Claim(
                RESULTS,
                "the records were produced by one harness version",
                f"harness {', '.join(harness)}, schema {', '.join(map(str, schema))}",
            )
        ]
    return [
        Claim(RESULTS, "measurement date", measured_on(runs)),
        Claim(RESULTS, "recorded harness version", f"with harness `{harness[0]}`"),
        Claim(RESULTS, "recorded schema version", f"schema version {schema[0]}"),
        *machine_claims(runs),
    ]


def machine_claims(runs: list[Run]) -> list[Claim]:
    """The machine the matrix actually ran on, from the fingerprint on every record.

    Ungated until a re-record moved the matrix from a two core Linux container to a
    twenty four core Windows desktop, at which point the document still described the old
    one down to the kernel version. Every record carries a `runner_fingerprint` precisely
    so that a published number can be traced to the environment that produced it, and the
    one place the repository summarised that fingerprint in prose was not checked against
    it.

    Runs from two different machines fail rather than publishing either, because a matrix
    measured on two machines is not one measurement.
    """
    prints = {
        (
            run.runner_fingerprint.os,
            run.runner_fingerprint.os_release,
            run.runner_fingerprint.arch,
            run.runner_fingerprint.cpu_count,
            run.runner_fingerprint.python_version,
        )
        for run in runs
    }
    if len(prints) != 1:
        return [
            Claim(
                RESULTS,
                "the records came from one machine",
                f"{len(prints)} different machines produced these runs",
            )
        ]
    name, release, arch, cpus, python = next(iter(prints))
    claims = [Claim(RESULTS, "machine", f"{name} {release}, {arch}, {cpus} vCPU, Python {python}")]

    engines = sorted({run.runner_fingerprint.docker_version or "" for run in runs})
    if engines == [""]:
        # A local backend matrix has no engine, and saying so is the honest rendering.
        claims.append(Claim(RESULTS, "container engine", "no container engine"))
    elif len(engines) == 1:
        claims.append(Claim(RESULTS, "container engine", f"Docker {engines[0]}"))
    return claims


def measured_on(runs: list[Run]) -> str:
    """The date the matrix was recorded, rendered as the document states it.

    Unguarded until a re-record made it wrong. The header read "Measured on 14 September
    2026" while the records underneath it had been replaced by a run from the 16th, and
    nothing in the repository could tell: a date is a published figure derived from the
    data, exactly like the harness version next to it, and it was the only one in that
    sentence with no claim behind it.

    UTC, because `started_at` is stored in UTC and a local rendering would depend on who
    ran the check. A span across midnight renders both ends rather than picking one.
    """
    dates = sorted({run.started_at.date() for run in runs})
    first, last = dates[0], dates[-1]
    if first == last:
        return f"Measured on {first.day} {first:%B %Y}"
    return f"Measured on {first.day} {first:%B %Y} to {last.day} {last:%B %Y}"


def leaderboard_claims(runs: list[Run]) -> list[Claim]:
    """Every per model cell on the leaderboard except one, and that one on purpose.

    The docstring here used to say "every metric cell" and covered six of the nine
    columns. Re-recording the matrix on Docker exposed what that costs: the whole mean
    wall clock column was left describing the previous machine, so the five cells summed
    to the previous total while the total row beside them carried the new one. The
    document contradicted itself and every gate passed.

    Now: solve rate, partial credit, step efficiency, tool call validity, redundant
    action rate, recovery rate, premature termination and mean wall clock. The destructive
    column is the exception, because four of its five cells are `0` and a claim of `| 0 |`
    would be satisfied by any table in the document; its one meaningful value is gated by
    `destructive_claims`, which checks the total and the per pattern split.
    """
    claims: list[Claim] = []
    for row in aggregate.leaderboard(runs):
        for label, rendered in (
            ("solve rate", row.solve_rate.render(digits=1, percent=True)),
            ("partial credit", row.partial_credit.render()),
            ("step efficiency", row.step_efficiency.render()),
            ("tool call validity", row.tool_call_validity.render()),
            ("redundant action rate", row.redundant_action_rate.render()),
            ("recovery rate", row.recovery_rate.render()),
            (
                "premature termination",
                row.premature_termination_rate.render(digits=1, percent=True),
            ),
            # Hardware rather than behaviour, which is exactly why it has to be checked:
            # it is the one column that moves when nothing about the run changed.
            ("mean wall clock", f"{row.mean_wall_clock_s:.1f} s"),
        ):
            claims.append(
                Claim(RESULTS, f"{row.model} {label}", rendered),
            )
    # The backend belongs to a row's identity, so a re-recorded matrix has to be
    # relabelled in the prose too. One claim covers it, since a mixed matrix would be
    # reported as separate rows anyway.
    backends = sorted({row.backend.value for row in aggregate.leaderboard(runs)})
    for backend in backends:
        claims.append(Claim(RESULTS, "sandbox backend label", f"| Sandbox backend | `{backend}`"))
    return claims


def step_claims(runs: list[Run]) -> list[Claim]:
    """Mean steps taken against the mean reference length, per model."""
    by_model: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_model[run.config.model].append(run)

    claims: list[Claim] = []
    for model, group in sorted(by_model.items()):
        taken = mean(len(run.steps) for run in group)
        claims.append(Claim(RESULTS, f"{model} mean steps", f"`{model}` | {taken:.1f} |"))
    return claims


def seed_claims(runs: list[Run]) -> list[Claim]:
    """The per-seed breakdown of the one model whose variance is the second finding."""
    worst = min(
        aggregate.leaderboard(runs),
        key=lambda row: row.solve_rate.mean,
    )
    claims: list[Claim] = []
    for seed in sorted({run.config.seed for run in runs}):
        group = [r for r in runs if r.config.model == worst.model and r.config.seed == seed]
        if not group:
            continue
        solved = sum(run.solved for run in group)
        rate = 100.0 * solved / len(group)
        claims.append(
            Claim(
                RESULTS,
                f"{worst.model} seed {seed}",
                f"| {seed} | {solved} of {len(group)} | {rate:.1f}% |",
            )
        )
    return claims


def failure_claims(runs: list[Run]) -> list[Claim]:
    """Failure mode counts in both reported populations, and the two prose figures."""
    claims: list[Claim] = []
    solved_total = sum(run.solved for run in runs)
    unsolved_total = len(runs) - solved_total

    populations: tuple[tuple[Literal["unsolved", "solved"], int], ...] = (
        ("unsolved", unsolved_total),
        ("solved", solved_total),
    )
    for among, denominator in populations:
        counts = aggregate.failure_mode_counts(runs, among=among)
        for entry in counts:
            share = 100.0 * entry.count / denominator if denominator else 0.0
            # The whole row, not just the two numeric cells. Three modes shared the count
            # 36 and the share 23.2%, so a claim of `| 36 | 23.2% |` was satisfied by any
            # of those rows. A wrong number on one row was therefore masked by a correct
            # identical number on another, which is exactly the quiet error this gate
            # exists to catch. It also left `promote_matrix.py` unable to tell which of
            # three identical strings to rewrite. Pinning the id and the name fixes both.
            #
            # Note what this still cannot see: presence is not position, so two rows whose
            # entire contents are exchanged leave both strings in the document and pass.
            # Catching that needs the table parsed rather than searched, and is not worth
            # the machinery for a reordering no editing mistake produces.
            claims.append(
                Claim(
                    RESULTS,
                    f"{entry.id.value} on {among} runs",
                    f"| {entry.id.value} | {entry.name} | {entry.count} | {share:.1f}% |",
                )
            )

    # The two figures that were wrong. Both documents state them in prose, so the claim is
    # the number with enough surrounding words to be unambiguous.
    on_solved = aggregate.failure_mode_counts(runs, among="solved")
    solved_hits = sum(entry.count for entry in on_solved)
    solved_modes = len(on_solved)
    carrying = sum(1 for run in runs if run.solved and run.failure_modes)
    share = 100.0 * carrying / solved_total if solved_total else 0.0

    word = {6: "Six", 5: "Five", 4: "Four", 3: "Three", 2: "Two", 1: "One"}.get(
        solved_modes, str(solved_modes)
    )
    claims.append(Claim(README, "hits on passing runs", f"fired {solved_hits} times"))
    claims.append(Claim(README, "solved runs carrying a hit", f"{carrying} of the {solved_total}"))
    claims.append(Claim(RESULTS, "modes on passing runs", f"{word} failure modes fired"))
    claims.append(Claim(RESULTS, "hits on passing runs", f"**{solved_hits} hits**"))
    claims.append(
        Claim(RESULTS, "share of solved runs carrying a hit", f"{share:.1f} percent of every run")
    )
    return claims


def destructive_claims(runs: list[Run]) -> list[Claim]:
    """The destructive command split behind the third finding."""
    kinds: Counter[str] = Counter()
    for run in runs:
        for _index, pattern, _command in destructive_attempts(run.steps):
            kinds[pattern] += 1
    if not kinds:
        return []
    total = sum(kinds.values())
    claims = [Claim(RESULTS, "destructive command total", f"issued {total}")]
    for pattern, count in kinds.most_common():
        claims.append(Claim(RESULTS, f"destructive: {pattern}", str(count)))
    return claims


def judge_claims(runs: list[Run]) -> list[Claim]:
    """What the documents may claim about the judge, given whether it ever ran."""
    judged = [run for run in runs if run.score and run.score.context_drift is not None]
    by_judge = [hit for run in runs for hit in run.failure_modes if hit.detector.value != "rule"]
    stale = f"`context_drift` (metric 8) is `null` on all {len(runs)} runs"
    if judged or by_judge:
        # The judge has run, so the document must no longer say it did not.
        return [Claim(RESULTS, "stale claim that the judge never ran", stale, present=False)]
    return [Claim(RESULTS, "context drift is null on every run", stale)]


def build_claims(runs: list[Run]) -> list[Claim]:
    """Every claim the documents are checked against, for one set of runs."""
    return [
        *totals_claims(runs),
        *provenance_claims(runs),
        *leaderboard_claims(runs),
        *step_claims(runs),
        *seed_claims(runs),
        *failure_claims(runs),
        *destructive_claims(runs),
        *judge_claims(runs),
    ]


def unmet(claims: list[Claim]) -> list[Claim]:
    """Return the claims the documents do not satisfy.

    Markdown wraps prose across lines, so a claim spanning a line break would fail on
    formatting rather than on substance. Collapsing whitespace on both sides checks the
    words and leaves the reflowing to the author.
    """
    cache: dict[Path, str] = {}
    failures: list[Claim] = []
    for claim in claims:
        if claim.document not in cache:
            cache[claim.document] = " ".join(claim.document.read_text().split())
        found = " ".join(claim.text.split()) in cache[claim.document]
        if found is not claim.present:
            failures.append(claim)
    return failures


def _display(path: Path) -> str:
    """Path relative to the repository root where possible, absolute otherwise."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    """Check every claim and report each failure. Returns a process exit status."""
    runs = load_runs()
    claims = build_claims(runs)
    failures = unmet(claims)

    for claim in failures:
        verb = (
            "recomputed from the run records, but this text is not in the document:"
            if claim.present
            else "this text is now stale and must be removed from the document:"
        )
        print(
            f"{_display(claim.document)}: {claim.label}\n  {verb}\n  {claim.text!r}",
            file=sys.stderr,
        )

    checked = len(claims)
    if failures:
        print(
            f"\n{len(failures)} of {checked} published figures do not match the run records.\n"
            "Either the documents are stale or the matrix was re-recorded. Rerun\n"
            "`uv run trajectory report runs/matrix --format md` and update the prose.",
            file=sys.stderr,
        )
        return 1

    print(f"{checked} published figures match the {len(runs)} committed run records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
