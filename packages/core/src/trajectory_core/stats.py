"""How sure a number is, and what a group of samples would teach.

The leaderboard's spread is the standard deviation across seeds. It answers one question,
whether running the same tasks again would move the number, and it is silent on another
that matters more: whether the number would hold on tasks the suite does not have. Twelve
tasks is a small sample of what a coding agent faces, so the honest interval comes from
resampling the tasks themselves.

Everything here is computed over tasks first. A task contributes the mean of its own
samples and every task weighs the same, so a task sampled three times does not count three
times as much as one sampled once, and an interval reflects how much the result leans on
which tasks happened to be in the suite.

- `bootstrap_interval` is a two stage percentile bootstrap: tasks drawn with replacement,
  then each drawn task's samples drawn with replacement. With twelve tasks it is wide, and
  that width is the finding.
- `paired_difference` compares two models on the tasks both attempted, resampling the
  shared tasks so the comparison is paired rather than between two independent averages.
- `pass_at_k` is the unbiased estimator of Chen et al. (2021): the chance that at least one
  of k samples drawn from a task's n solves it, averaged over tasks.
- `group_advantages` is the group relative advantage GRPO trains on: each sample's reward
  less the group mean, over the group's spread. A group whose samples all score the same has
  zero advantage everywhere, which is why a task every sample passes, or every sample fails,
  teaches nothing.

Every bootstrap here is seeded, so a published interval can be recomputed exactly.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from trajectory_core.models import Run

DEFAULT_RESAMPLES = 4000
DEFAULT_CONFIDENCE = 0.95
DEFAULT_SEED = 20261025


@dataclass(frozen=True, slots=True)
class Interval:
    """A point estimate with a percentile interval around it."""

    estimate: float
    low: float
    high: float

    def render(self, *, percent: bool = True, digits: int = 1) -> str:
        """The estimate and its interval, as a table cell."""
        if percent:
            return (
                f"{self.estimate * 100:.{digits}f}% "
                f"[{self.low * 100:.{digits}f}, {self.high * 100:.{digits}f}]"
            )
        return f"{self.estimate:.{digits}f} [{self.low:.{digits}f}, {self.high:.{digits}f}]"


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k for one task: n samples, c of them correct, k drawn.

    Raises:
        ValueError: When the counts are impossible or k exceeds n.
    """
    if n < 1 or not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError(f"pass@k needs 1 <= k <= n and 0 <= c <= n, got n={n} c={c} k={k}")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def outcomes_by_task(runs: Iterable[Run], metric: str = "solved") -> dict[str, list[float]]:
    """Each task's samples of a metric, in seed order, skipping runs where it is undefined."""
    by_task: dict[str, list[float]] = defaultdict(list)
    for run in sorted(runs, key=lambda r: (r.task_id, r.config.seed, r.id)):
        if run.score is None:
            continue
        value = getattr(run.score, metric, None)
        if value is None:
            continue
        by_task[run.task_id].append(float(value))
    return dict(by_task)


def task_mean(per_task: Mapping[str, Sequence[float]]) -> float:
    """The mean over tasks of each task's mean: every task weighs the same."""
    means = [statistics.fmean(values) for values in per_task.values() if values]
    if not means:
        raise ValueError("no task has a sample")
    return statistics.fmean(means)


def _percentiles(draws: list[float], confidence: float) -> tuple[float, float]:
    draws.sort()
    tail = (1.0 - confidence) / 2.0
    low = draws[max(0, math.floor(tail * len(draws)))]
    high = draws[min(len(draws) - 1, math.ceil((1.0 - tail) * len(draws)) - 1)]
    return low, high


def _resample(rng: random.Random, values: Sequence[float]) -> float:
    return statistics.fmean(rng.choice(values) for _ in values)


def bootstrap_interval(
    per_task: Mapping[str, Sequence[float]],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """A two stage percentile bootstrap of the task mean: tasks, then samples within each."""
    tasks = sorted(task for task, values in per_task.items() if values)
    if not tasks:
        raise ValueError("no task has a sample")
    rng = random.Random(seed)  # noqa: S311  a reproducible resample, not a secret
    draws = [
        statistics.fmean(_resample(rng, per_task[rng.choice(tasks)]) for _ in tasks)
        for _ in range(resamples)
    ]
    low, high = _percentiles(draws, confidence)
    return Interval(task_mean({task: per_task[task] for task in tasks}), low, high)


def paired_difference(
    first: Mapping[str, Sequence[float]],
    second: Mapping[str, Sequence[float]],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> tuple[Interval, float, int]:
    """First minus second on the tasks both have, resampled as pairs.

    Returns:
        The difference with its interval, the share of resamples in which the first came
        out ahead (ties count half), and the number of shared tasks.

    Raises:
        ValueError: When the two share no task.
    """
    shared = sorted(task for task in set(first) & set(second) if first[task] and second[task])
    if not shared:
        raise ValueError("the two have no task in common")
    estimate = statistics.fmean(
        statistics.fmean(first[task]) - statistics.fmean(second[task]) for task in shared
    )
    rng = random.Random(seed)  # noqa: S311  a reproducible resample, not a secret
    draws: list[float] = []
    for _ in range(resamples):
        picked = [rng.choice(shared) for _ in shared]
        draws.append(
            statistics.fmean(
                _resample(rng, first[task]) - _resample(rng, second[task]) for task in picked
            )
        )
    ahead = (sum(1 for d in draws if d > 0) + 0.5 * sum(1 for d in draws if d == 0)) / len(draws)
    low, high = _percentiles(draws, confidence)
    return Interval(estimate, low, high), round(ahead, 4), len(shared)


def suite_pass_at_k(per_task: Mapping[str, Sequence[float]], k: int) -> float | None:
    """pass@k averaged over the tasks with at least k samples, or None when none has."""
    values = [
        pass_at_k(len(samples), sum(1 for s in samples if s >= 1.0), k)
        for samples in per_task.values()
        if len(samples) >= k
    ]
    return statistics.fmean(values) if values else None


def group_advantages(rewards: Sequence[float], *, epsilon: float = 1e-6) -> list[float]:
    """GRPO's group relative advantage: reward less the group mean, over the group's spread.

    The spread is the population standard deviation of the group. A group whose rewards
    are all equal has an advantage of zero for every sample, whatever the reward was.
    """
    if not rewards:
        return []
    mean = statistics.fmean(rewards)
    spread = statistics.pstdev(rewards)
    return [(reward - mean) / (spread + epsilon) for reward in rewards]


@dataclass(frozen=True, slots=True)
class ModelStatistics:
    """One model's solve rate and partial credit with intervals, and its pass@k."""

    model: str
    tasks: int
    samples: int
    solve_rate: Interval
    partial_credit: Interval
    pass_at: dict[int, float]


def model_statistics(runs: Sequence[Run], model: str) -> ModelStatistics:
    """Intervals and pass@k for one model's runs."""
    mine = [run for run in runs if run.config.model == model]
    solved = outcomes_by_task(mine, "solved")
    partial = outcomes_by_task(mine, "partial_credit")
    most = max((len(samples) for samples in solved.values()), default=0)
    pass_at = {
        k: value for k in range(1, most + 1) if (value := suite_pass_at_k(solved, k)) is not None
    }
    return ModelStatistics(
        model=model,
        tasks=len(solved),
        samples=sum(len(samples) for samples in solved.values()),
        solve_rate=bootstrap_interval(solved),
        partial_credit=bootstrap_interval(partial),
        pass_at=pass_at,
    )
