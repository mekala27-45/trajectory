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


def test_only_names_exact_runs():
    pairs = run_matrix.only_pairs(["py-perf-01:1", "go-race-01:0", "py-perf-01:1"], TASKS)
    assert pairs == {("py-perf-01", 1), ("go-race-01", 0)}
    assert run_matrix.only_pairs(None, TASKS) is None


@pytest.mark.parametrize("value", ["py-perf-01", "py-perf-01:x", ":1", "nope-01:0"])
def test_a_malformed_or_unknown_run_is_refused(value: str):
    with pytest.raises(ValueError):
        run_matrix.only_pairs([value], TASKS)


def test_only_runs_nothing_but_the_named_pairs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Two runs named across two tasks and two seeds plan two jobs, not four."""
    planned: list[tuple[str, int]] = []

    def fake_prebuild(jobs):
        planned.extend((job.loaded.id, job.config.seed) for job in jobs)
        raise SystemExit(0)  # stop before anything runs

    monkeypatch.setattr(run_matrix, "docker_available", lambda: True)
    monkeypatch.setattr(run_matrix, "prebuild_images", fake_prebuild)
    args = [
        "--model",
        "nvidia_nim/v/m",
        "--only",
        "py-perf-01:1",
        "--only",
        "go-race-01:0",
        "--tasks-root",
        str(ROOT / "tasks"),
        "--out",
        str(tmp_path),
    ]
    with pytest.raises(SystemExit):
        run_matrix.main(args)
    assert sorted(planned) == [("go-race-01", 0), ("py-perf-01", 1)]
