"""Aggregation across runs.

Two callers need the same arithmetic: the CLI report and the leaderboard API. Putting it
here means a number in the terminal and the same number on the website came from one
implementation rather than two that agree until they do not.

The rule that shapes this module: a pass rate reported without variance across seeds is
not a result. Every aggregate is a `MetricStat` carrying a mean, a population standard
deviation and the count behind it, and metrics that are undefined for a run (step
efficiency on a failure, recovery rate with nothing to recover from) are excluded from
their own denominator rather than coerced to zero.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from trajectory_core.failure_modes import TAXONOMY
from trajectory_core.models import (
    FailureModeCount,
    FailureModeId,
    LeaderboardRow,
    MetricStat,
    Run,
)


def stat(values: Sequence[float]) -> MetricStat:
    """Summarise a list of per-run values.

    Args:
        values: One value per run, with undefined values already excluded.

    Returns:
        The mean, the population standard deviation, and the count. An empty input gives
        zeros with `n` of zero, which is how a caller can tell nothing was measured.
    """
    if not values:
        return MetricStat(mean=0.0, stdev=0.0, n=0)
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return MetricStat(mean=round(mean, 6), stdev=round(stdev, 6), n=len(values))


def seed_variance(runs: Sequence[Run], metric: str) -> MetricStat:
    """Spread of a metric across seeds, averaged within each task first.

    Reporting the standard deviation over all runs at once conflates two different things:
    variation between tasks, which is expected and large, and variation between seeds on
    the same task, which is what tells you whether a change moved the model or moved the
    dice. This collapses each task to its per-seed mean and then reports the spread of the
    per-seed suite averages, which is the number worth publishing.

    Args:
        runs: Runs for one model on one suite.
        metric: Attribute name on `TrajectoryScore`.

    Returns:
        Mean and standard deviation across seeds.
    """
    by_seed: dict[int, list[float]] = defaultdict(list)
    for run in runs:
        if run.score is None:
            continue
        value = getattr(run.score, metric, None)
        if value is None:
            continue
        by_seed[run.config.seed].append(float(value))
    per_seed = [statistics.fmean(values) for values in by_seed.values() if values]
    return stat(per_seed)


def _values(runs: Iterable[Run], metric: str) -> list[float]:
    """Collect a metric across runs, skipping runs where it is undefined."""
    out: list[float] = []
    for run in runs:
        if run.score is None:
            continue
        value = getattr(run.score, metric, None)
        if value is None:
            continue
        out.append(float(value))
    return out


def leaderboard_row(model: str, suite: str, runs: Sequence[Run]) -> LeaderboardRow:
    """Aggregate one model's runs on one suite.

    Args:
        model: Model identifier.
        suite: Suite name.
        runs: Every run for that model and suite.

    Returns:
        One leaderboard row.
    """
    scored = [run for run in runs if run.score is not None]
    solved = [run for run in scored if run.score is not None and run.score.solved]
    total_cost = round(sum(run.total_cost_usd for run in runs), 6)

    return LeaderboardRow(
        model=model,
        suite=suite,
        runs=len(runs),
        tasks=len({run.task_id for run in runs}),
        seeds=len({run.config.seed for run in runs}),
        solve_rate=seed_variance(scored, "solved"),
        partial_credit=seed_variance(scored, "partial_credit"),
        step_efficiency=seed_variance(scored, "step_efficiency"),
        tool_call_validity=seed_variance(scored, "tool_call_validity"),
        redundant_action_rate=seed_variance(scored, "redundant_action_rate"),
        recovery_rate=seed_variance(scored, "recovery_rate"),
        premature_termination_rate=seed_variance(scored, "premature_termination"),
        context_drift=seed_variance(scored, "context_drift"),
        mean_cost_usd=round(statistics.fmean(_values(runs, "cost_usd")), 6) if scored else 0.0,
        cost_per_solved_usd=(
            round(total_cost / len(solved), 6) if solved and total_cost > 0 else None
        ),
        mean_wall_clock_s=(
            round(statistics.fmean(_values(runs, "wall_clock_s")), 3) if scored else 0.0
        ),
        total_cost_usd=total_cost,
        destructive_attempts=sum(
            run.score.destructive_attempts for run in scored if run.score is not None
        ),
        last_run_at=max((run.started_at for run in runs), default=None),
    )


def leaderboard(runs: Sequence[Run], *, suite: str | None = None) -> list[LeaderboardRow]:
    """Build a full leaderboard, sorted by solve rate descending.

    Args:
        runs: Runs to aggregate.
        suite: Restrict to one suite.

    Returns:
        One row per model, best solve rate first, then by cost per solved task ascending so
        two models with the same solve rate are separated by something meaningful.
    """
    grouped: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in runs:
        if suite is not None and run.suite != suite:
            continue
        grouped[(run.config.model, run.suite)].append(run)

    rows = [
        leaderboard_row(model, suite_name, group) for (model, suite_name), group in grouped.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row.solve_rate.mean,
            row.cost_per_solved_usd if row.cost_per_solved_usd is not None else float("inf"),
            row.model,
        ),
    )


def failure_mode_counts(runs: Sequence[Run]) -> list[FailureModeCount]:
    """Count how many runs carry each failure mode.

    The denominator is unsolved runs, not all runs. A mode firing on 19 percent of failed
    runs is an actionable number; the same count expressed as a share of every run buries
    it under the tasks that went fine.
    """
    unsolved = [run for run in runs if not run.solved]
    counts: Counter[FailureModeId] = Counter()
    for run in unsolved:
        for hit in {mode.id for mode in run.failure_modes}:
            counts[hit] += 1

    denominator = len(unsolved) or 1
    return sorted(
        (
            FailureModeCount(
                id=mode_id,
                name=TAXONOMY[mode_id].name,
                count=count,
                share_of_failed_runs=round(count / denominator, 6),
            )
            for mode_id, count in counts.items()
        ),
        key=lambda entry: (-entry.count, entry.id.value),
    )


def failure_modes_by_model(runs: Sequence[Run]) -> dict[str, list[FailureModeCount]]:
    """Failure mode distribution per model."""
    grouped: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        grouped[run.config.model].append(run)
    return {model: failure_mode_counts(group) for model, group in sorted(grouped.items())}


def failure_modes_by_difficulty(
    runs: Sequence[Run], difficulty_of: dict[str, int]
) -> dict[int, list[FailureModeCount]]:
    """Failure mode distribution per task difficulty tier.

    Args:
        runs: Runs to aggregate.
        difficulty_of: Task identifier to difficulty tier.

    Returns:
        Counts keyed by tier, for tiers that actually appear in the runs.
    """
    grouped: dict[int, list[Run]] = defaultdict(list)
    for run in runs:
        tier = difficulty_of.get(run.task_id)
        if tier is not None:
            grouped[tier].append(run)
    return {tier: failure_mode_counts(group) for tier, group in sorted(grouped.items())}


def per_task_breakdown(runs: Sequence[Run]) -> dict[str, dict[str, MetricStat]]:
    """Solve rate and partial credit per task, per model.

    Used by the task detail page, where the question is not which model is best overall but
    which models can do this particular thing.
    """
    grouped: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in runs:
        grouped[(run.task_id, run.config.model)].append(run)

    out: dict[str, dict[str, MetricStat]] = defaultdict(dict)
    for (task_id, model), group in grouped.items():
        out[task_id][model] = stat(_values(group, "solved"))
    return dict(out)
