"""Aggregation is where a pass rate becomes a published number, so the arithmetic matters."""

from __future__ import annotations

import pytest

from trajectory_core.aggregate import (
    failure_mode_counts,
    failure_modes_by_difficulty,
    failure_modes_by_model,
    leaderboard,
    leaderboard_row,
    per_task_breakdown,
    seed_variance,
    stat,
)
from trajectory_core.models import Detector, FailureModeHit, FailureModeId, RunStatus
from trajectory_core.scoring import score
from trajectory_core.testing import (
    bash_steps,
    finish_step,
    make_config,
    make_run,
    make_task,
    make_verification,
)

TASK = make_task(reference_step_count=3)


def scored_run(
    *,
    model: str,
    seed: int,
    task_id: str = "demo-task-01",
    solved: bool = True,
    steps: int = 3,
    cost: float = 0.0,
    modes: list[FailureModeId] | None = None,
):
    commands = [f"cmd {i}" for i in range(max(0, steps - 1))]
    trajectory = [*bash_steps(*commands), finish_step(max(0, steps - 1))]
    for step in trajectory:
        step.cost_usd = cost / max(1, len(trajectory))
    run = make_run(
        trajectory,
        verification=make_verification(
            passed=solved,
            tests_passed=4 if solved else 1,
            tests_total=4,
        ),
        status=RunStatus.COMPLETED,
        task_id=task_id,
        config=make_config(model=model, seed=seed),
    )
    run.score = score(run, TASK)
    run.failure_modes = [
        FailureModeHit(
            id=mode, name=mode.value, confidence=1.0, detector=Detector.RULE, evidence="e"
        )
        for mode in (modes or [])
    ]
    return run


class TestStat:
    def test_mean_and_population_stdev(self):
        """Values 0, 1, 1, 0: mean 0.5, population stdev 0.5."""
        result = stat([0.0, 1.0, 1.0, 0.0])
        assert result.mean == 0.5
        assert result.stdev == 0.5
        assert result.n == 4

    def test_a_single_value_has_no_spread(self):
        assert stat([0.7]).stdev == 0.0

    def test_an_empty_input_reports_nothing_measured(self):
        result = stat([])
        assert (result.mean, result.stdev, result.n) == (0.0, 0.0, 0)

    def test_rendering_for_a_table_cell(self):
        assert stat([0.5, 0.5]).render(digits=1, percent=True) == "50.0% +/- 0.0%"

    def test_an_unmeasured_group_renders_as_not_applicable(self):
        """0.000 +/- 0.000 would read as a measurement of zero, which is a different claim."""
        assert stat([]).render() == "n/a"
        assert stat([]).render(percent=True) == "n/a"


class TestSeedVariance:
    def test_collapses_each_task_before_comparing_seeds(self):
        """Two tasks, three seeds. Seed 0 solves both, seed 1 solves one, seed 2 solves none.

        Per seed suite means: 1.0, 0.5, 0.0. Mean 0.5, population stdev 0.4082.
        Averaging over all nine runs at once would mix task variance into the number, which
        is exactly what makes a published standard deviation meaningless.
        """
        runs = []
        for seed, solved_tasks in enumerate([("a", "b"), ("a",), ()]):
            for task_id in ("a", "b"):
                runs.append(
                    scored_run(
                        model="m", seed=seed, task_id=task_id, solved=task_id in solved_tasks
                    )
                )
        result = seed_variance(runs, "solved")
        assert result.mean == pytest.approx(0.5)
        assert result.stdev == pytest.approx(0.40824, abs=1e-4)
        assert result.n == 3

    def test_undefined_metrics_are_excluded_rather_than_zeroed(self):
        """Step efficiency is null on failures. Counting those as 0 would reward quitting."""
        runs = [
            scored_run(model="m", seed=0, solved=True, steps=3),
            scored_run(model="m", seed=1, solved=False, steps=3),
        ]
        result = seed_variance(runs, "step_efficiency")
        assert result.n == 1
        assert result.mean == 1.0

    def test_unscored_runs_are_skipped(self):
        run = scored_run(model="m", seed=0)
        run.score = None
        assert seed_variance([run], "solved").n == 0


class TestLeaderboardRow:
    def test_aggregates_one_model(self):
        runs = [
            scored_run(model="m", seed=s, task_id=t, solved=(s < 2), cost=0.10)
            for s in range(3)
            for t in ("a", "b")
        ]
        row = leaderboard_row("m", "core-12", runs)
        assert row.runs == 6
        assert row.tasks == 2
        assert row.seeds == 3
        assert row.solve_rate.mean == pytest.approx(2 / 3)
        assert row.total_cost_usd == pytest.approx(0.60, abs=1e-6)
        assert row.mean_cost_usd == pytest.approx(0.10, abs=1e-6)
        assert row.cost_per_solved_usd == pytest.approx(0.15, abs=1e-6)

    def test_cost_per_solved_is_absent_when_nothing_was_solved(self):
        runs = [scored_run(model="m", seed=0, solved=False, cost=0.5)]
        assert leaderboard_row("m", "core-12", runs).cost_per_solved_usd is None

    def test_destructive_attempts_are_summed(self):
        from trajectory_core.testing import bash_steps as bs

        run = make_run(
            [*bs("git reset --hard", "chmod -R 777 .")],
            verification=make_verification(passed=False, tests_passed=0),
        )
        run.score = score(run, TASK)
        assert leaderboard_row("m", "core-12", [run]).destructive_attempts == 2


class TestLeaderboard:
    def test_local_backend_runs_form_their_own_rows(self):
        """Never merged with container runs, because the isolation guarantee differs."""
        from trajectory_core.models import RunnerFingerprint, SandboxBackend

        docker_run = scored_run(model="m", seed=0, solved=True)
        docker_run.runner_fingerprint = RunnerFingerprint(
            os="Linux",
            os_release="t",
            arch="x86_64",
            python_version="3.12.3",
            cpu_count=2,
            docker_version="27.3.1",
            sandbox_backend=SandboxBackend.DOCKER,
        )
        local_run = scored_run(model="m", seed=0, solved=False)
        rows = leaderboard([docker_run, local_run])
        assert len(rows) == 2
        assert {row.backend.value for row in rows} == {"docker", "local"}
        assert rows[0].backend is SandboxBackend.DOCKER

    def test_the_backend_filter_narrows_the_board(self):
        from trajectory_core.models import SandboxBackend

        runs = [scored_run(model="m", seed=0, solved=True)]
        assert leaderboard(runs, backend=SandboxBackend.DOCKER) == []
        assert len(leaderboard(runs, backend=SandboxBackend.LOCAL)) == 1

    def test_sorted_by_solve_rate_then_cost(self):
        runs = [
            *[scored_run(model="strong", seed=s, solved=True, cost=1.0) for s in range(2)],
            *[scored_run(model="cheap", seed=s, solved=True, cost=0.1) for s in range(2)],
            *[scored_run(model="weak", seed=s, solved=False, cost=0.1) for s in range(2)],
        ]
        rows = leaderboard(runs)
        assert [row.model for row in rows] == ["cheap", "strong", "weak"]

    def test_filters_by_suite(self):
        run = scored_run(model="m", seed=0)
        run.suite = "other"
        assert leaderboard([run], suite="core-12") == []
        assert len(leaderboard([run], suite="other")) == 1

    def test_an_empty_input_gives_an_empty_leaderboard(self):
        assert leaderboard([]) == []


class TestFailureModeCounts:
    def test_denominator_is_unsolved_runs_not_all_runs(self):
        """4 unsolved of 10 runs, 1 carrying F03. Share is 1/4, not 1/10.

        Expressing it over every run buries an actionable number under the tasks that
        went fine.
        """
        runs = [scored_run(model="m", seed=0, task_id=f"t{i}", solved=True) for i in range(6)]
        runs += [scored_run(model="m", seed=0, task_id=f"u{i}", solved=False) for i in range(3)]
        runs.append(
            scored_run(
                model="m",
                seed=0,
                task_id="u9",
                solved=False,
                modes=[FailureModeId.PREMATURE_SUCCESS],
            )
        )
        counts = failure_mode_counts(runs)
        assert len(counts) == 1
        assert counts[0].id is FailureModeId.PREMATURE_SUCCESS
        assert counts[0].count == 1
        assert counts[0].share_of_failed_runs == pytest.approx(0.25)

    def test_a_mode_repeated_within_one_run_counts_once(self):
        run = scored_run(
            model="m",
            seed=0,
            solved=False,
            modes=[FailureModeId.RETRY_LOOP, FailureModeId.RETRY_LOOP],
        )
        counts = failure_mode_counts([run])
        assert counts[0].count == 1

    def test_solved_runs_contribute_nothing_to_the_default_view(self):
        run = scored_run(model="m", seed=0, solved=True, modes=[FailureModeId.SCOPE_CREEP])
        assert failure_mode_counts([run]) == []

    def test_the_solved_view_is_the_one_a_pass_rate_cannot_produce(self):
        """A run that made malformed calls and passed anyway is a process problem.

        Two solved runs, one carrying F01. Share of solved is 1/2, and the default
        unsolved view reports nothing at all, which is exactly the blind spot.
        """
        runs = [
            scored_run(
                model="m",
                seed=0,
                task_id="a",
                solved=True,
                modes=[FailureModeId.TOOL_SCHEMA_VIOLATION],
            ),
            scored_run(model="m", seed=0, task_id="b", solved=True),
        ]
        assert failure_mode_counts(runs) == []
        on_solved = failure_mode_counts(runs, among="solved")
        assert len(on_solved) == 1
        assert on_solved[0].id is FailureModeId.TOOL_SCHEMA_VIOLATION
        assert on_solved[0].share_of_failed_runs == pytest.approx(0.5)

    def test_the_all_view_uses_every_run_as_the_denominator(self):
        runs = [
            scored_run(
                model="m", seed=0, task_id="a", solved=True, modes=[FailureModeId.RETRY_LOOP]
            ),
            scored_run(model="m", seed=0, task_id="b", solved=False),
            scored_run(model="m", seed=0, task_id="c", solved=True),
            scored_run(model="m", seed=0, task_id="d", solved=True),
        ]
        counts = failure_mode_counts(runs, among="all")
        assert counts[0].share_of_failed_runs == pytest.approx(0.25)

    def test_an_empty_population_does_not_divide_by_zero(self):
        assert failure_mode_counts([], among="solved") == []

    def test_sorted_by_count_descending(self):
        runs = [
            scored_run(
                model="m", seed=0, task_id="a", solved=False, modes=[FailureModeId.RETRY_LOOP]
            ),
            scored_run(
                model="m",
                seed=0,
                task_id="b",
                solved=False,
                modes=[FailureModeId.RETRY_LOOP, FailureModeId.SCOPE_CREEP],
            ),
        ]
        counts = failure_mode_counts(runs)
        assert counts[0].id is FailureModeId.RETRY_LOOP
        assert counts[0].count == 2

    def test_names_come_from_the_taxonomy(self):
        run = scored_run(model="m", seed=0, solved=False, modes=[FailureModeId.RETRY_LOOP])
        assert failure_mode_counts([run])[0].name == "retry loop"

    def test_grouping_by_model_can_use_any_population(self):
        run = scored_run(model="a", seed=0, solved=True, modes=[FailureModeId.RETRY_LOOP])
        assert failure_modes_by_model([run])["a"] == []
        assert failure_modes_by_model([run], among="all")["a"][0].id is FailureModeId.RETRY_LOOP

    def test_grouped_by_model(self):
        runs = [
            scored_run(model="a", seed=0, solved=False, modes=[FailureModeId.RETRY_LOOP]),
            scored_run(model="b", seed=0, solved=False, modes=[FailureModeId.SCOPE_CREEP]),
        ]
        grouped = failure_modes_by_model(runs)
        assert set(grouped) == {"a", "b"}
        assert grouped["a"][0].id is FailureModeId.RETRY_LOOP

    def test_grouped_by_difficulty(self):
        runs = [
            scored_run(
                model="m", seed=0, task_id="easy", solved=False, modes=[FailureModeId.RETRY_LOOP]
            ),
            scored_run(
                model="m",
                seed=0,
                task_id="hard",
                solved=False,
                modes=[FailureModeId.DESTRUCTIVE_ACTION],
            ),
        ]
        grouped = failure_modes_by_difficulty(runs, {"easy": 1, "hard": 5})
        assert set(grouped) == {1, 5}
        assert grouped[5][0].id is FailureModeId.DESTRUCTIVE_ACTION

    def test_tasks_with_no_known_difficulty_are_dropped(self):
        run = scored_run(model="m", seed=0, task_id="unknown", solved=False)
        assert failure_modes_by_difficulty([run], {}) == {}


class TestPerTaskBreakdown:
    def test_reports_solve_rate_per_task_and_model(self):
        runs = [
            scored_run(model="a", seed=0, task_id="t1", solved=True),
            scored_run(model="a", seed=1, task_id="t1", solved=False),
            scored_run(model="b", seed=0, task_id="t1", solved=True),
        ]
        breakdown = per_task_breakdown(runs)
        assert breakdown["t1"]["a"].mean == pytest.approx(0.5)
        assert breakdown["t1"]["a"].n == 2
        assert breakdown["t1"]["b"].mean == 1.0
