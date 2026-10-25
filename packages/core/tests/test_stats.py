"""Intervals over tasks, paired comparisons, pass@k, and the GRPO advantage."""

from __future__ import annotations

import math

import pytest

from trajectory_core.stats import (
    Interval,
    bootstrap_interval,
    group_advantages,
    outcomes_by_task,
    paired_difference,
    pass_at_k,
    suite_pass_at_k,
    task_mean,
)


class TestPassAtK:
    def test_matches_the_closed_form(self):
        # One correct of four: pass@1 is a quarter, pass@2 is one minus C(3,2)/C(4,2).
        assert pass_at_k(4, 1, 1) == pytest.approx(0.25)
        assert pass_at_k(4, 1, 2) == pytest.approx(1 - 3 / 6)

    def test_edges(self):
        assert pass_at_k(2, 0, 1) == 0.0
        assert pass_at_k(2, 2, 1) == 1.0
        assert pass_at_k(3, 2, 2) == 1.0

    @pytest.mark.parametrize(("n", "c", "k"), [(0, 0, 1), (2, 3, 1), (2, 1, 3), (2, 1, 0)])
    def test_impossible_counts_are_refused(self, n: int, c: int, k: int):
        with pytest.raises(ValueError, match="pass@k"):
            pass_at_k(n, c, k)

    def test_the_suite_value_averages_over_tasks_with_enough_samples(self):
        per_task = {"a": [1.0, 0.0], "b": [0.0, 0.0], "c": [1.0]}
        assert suite_pass_at_k(per_task, 1) == pytest.approx((0.5 + 0.0 + 1.0) / 3)
        assert suite_pass_at_k(per_task, 2) == pytest.approx((1.0 + 0.0) / 2)
        assert suite_pass_at_k(per_task, 3) is None


PER_TASK = {f"t{i}": [1.0, 0.0] if i % 3 == 0 else [float(i % 2)] * 2 for i in range(12)}


class TestBootstrap:
    def test_the_estimate_weighs_every_task_the_same(self):
        uneven = {"a": [1.0, 1.0, 1.0, 1.0], "b": [0.0]}
        assert task_mean(uneven) == pytest.approx(0.5)
        assert bootstrap_interval(uneven).estimate == pytest.approx(0.5)

    def test_the_interval_brackets_the_estimate_and_is_reproducible(self):
        first = bootstrap_interval(PER_TASK)
        assert first.low <= first.estimate <= first.high
        assert first.high - first.low > 0
        assert bootstrap_interval(PER_TASK) == first

    def test_a_constant_result_has_no_width(self):
        assert bootstrap_interval({"a": [1.0, 1.0], "b": [1.0, 1.0]}) == Interval(1.0, 1.0, 1.0)

    def test_more_tasks_narrow_the_interval(self):
        small = {f"t{i}": [float(i % 2)] for i in range(6)}
        large = {f"t{i}": [float(i % 2)] for i in range(60)}
        width = lambda i: i.high - i.low  # noqa: E731
        assert width(bootstrap_interval(large)) < width(bootstrap_interval(small))

    def test_an_empty_set_is_refused(self):
        with pytest.raises(ValueError, match="no task"):
            bootstrap_interval({"a": []})


class TestPairedDifference:
    def test_a_clear_winner(self):
        first = {f"t{i}": [1.0, 1.0] for i in range(12)}
        second = {f"t{i}": [0.0, 1.0] if i < 3 else [0.0, 0.0] for i in range(12)}
        diff, ahead, shared = paired_difference(first, second)
        assert shared == 12
        assert diff.estimate == pytest.approx(1 - 1.5 / 12)
        assert diff.low > 0
        assert ahead == 1.0

    def test_only_shared_tasks_count(self):
        first = {"a": [1.0], "b": [1.0], "c": [0.0]}
        second = {"a": [0.0], "c": [0.0], "d": [1.0]}
        diff, _, shared = paired_difference(first, second)
        assert shared == 2
        assert diff.estimate == pytest.approx(0.5)

    def test_identical_models_are_a_coin_toss(self):
        same = {f"t{i}": [float(i % 2), float((i + 1) % 2)] for i in range(12)}
        diff, ahead, _ = paired_difference(same, same)
        assert diff.estimate == 0.0
        assert 0.3 < ahead < 0.7

    def test_nothing_shared_is_refused(self):
        with pytest.raises(ValueError, match="in common"):
            paired_difference({"a": [1.0]}, {"b": [1.0]})


class TestGroupAdvantages:
    def test_a_mixed_group_pushes_toward_the_winner(self):
        advantages = group_advantages([1.0, 0.0])
        assert advantages[0] > 0 > advantages[1]
        assert advantages[0] == pytest.approx(1.0, abs=1e-5)
        assert sum(advantages) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize("rewards", [[1.0, 1.0], [0.0, 0.0, 0.0], [0.4, 0.4]])
    def test_a_uniform_group_teaches_nothing(self, rewards: list[float]):
        assert group_advantages(rewards) == [0.0] * len(rewards)

    def test_partial_credit_keeps_a_gradient_when_every_sample_failed(self):
        advantages = group_advantages([0.3, 0.7])
        assert advantages[1] > 0 > advantages[0]
        assert not any(math.isnan(a) for a in advantages)

    def test_empty(self):
        assert group_advantages([]) == []


def test_outcomes_by_task_reads_scored_runs_in_seed_order():
    from trajectory_core.scoring import score
    from trajectory_core.testing import (
        bash_steps,
        finish_step,
        make_config,
        make_run,
        make_task,
        make_verification,
    )

    runs = []
    for seed, solved in ((1, False), (0, True)):
        run = make_run(
            [*bash_steps("ls"), finish_step(1)],
            config=make_config(seed=seed),
            verification=make_verification(
                passed=solved, tests_passed=4 if solved else 1, tests_total=4
            ),
        )
        run.score = score(run, make_task())
        runs.append(run)
    assert outcomes_by_task(runs) == {"demo-task-01": [1.0, 0.0]}
    assert outcomes_by_task(runs, "partial_credit") == {"demo-task-01": [1.0, 0.25]}
