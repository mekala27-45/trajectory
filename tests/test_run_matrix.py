"""Selecting a slice of the matrix, so a cut seed can be finished on its own."""

from __future__ import annotations

from pathlib import Path

import pytest
import run_matrix

from trajectory_runner.loader import discover

ROOT = Path(__file__).resolve().parent.parent
TASKS = discover(ROOT / "tasks", suite="core-12")


def test_no_filter_keeps_the_whole_suite():
    assert run_matrix.select_tasks(TASKS, None) == TASKS


def test_a_filter_keeps_suite_order():
    chosen = run_matrix.select_tasks(TASKS, ["ts-build-break-01", "py-perf-01"])
    assert [loaded.id for loaded in chosen] == ["py-perf-01", "ts-build-break-01"]


def test_an_unknown_task_is_refused_rather_than_skipped():
    with pytest.raises(ValueError, match="py-perf-99"):
        run_matrix.select_tasks(TASKS, ["py-perf-01", "py-perf-99"])


def test_explicit_seeds_override_the_count():
    assert run_matrix.seed_list(3, None) == [0, 1, 2]
    assert run_matrix.seed_list(3, [1]) == [1]
    assert run_matrix.seed_list(3, [2, 1, 2]) == [1, 2]
    with pytest.raises(ValueError, match="non-negative"):
        run_matrix.seed_list(3, [-1])


def test_a_bad_slice_stops_before_anything_runs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(run_matrix, "docker_available", lambda: True)
    args = ["--model", "nvidia_nim/v/m", "--task", "nope-01", "--tasks-root", str(ROOT / "tasks")]
    assert run_matrix.main(args) == 2
