"""`plan` decides what a capped run leaves behind, so its order is worth pinning.

The real model matrix runs under a call ceiling that is reached on purpose, so the order
in which jobs are planned is the difference between a partial result that is whole seeds
and one that is a ragged edge nobody can average.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trajectory_core.models import SandboxBackend
from trajectory_core.testing import make_playbook, make_task
from trajectory_runner.execute import plan
from trajectory_runner.loader import LoadedTask


def _loaded(task_id: str) -> LoadedTask:
    """A LoadedTask with just enough shape for planning, no directory on disk."""
    return LoadedTask(
        task=make_task(id=task_id, image_tag=f"trajectory/{task_id}"),
        directory=Path("/nonexistent") / task_id,
        playbook=make_playbook(task_id=task_id),
    )


TASKS = [_loaded("alpha-01"), _loaded("beta-01")]
SEEDS = [0, 1]


def _grid(jobs: list) -> list[tuple[str, int]]:
    return [(job.loaded.id, job.config.seed) for job in jobs]


def test_task_major_groups_every_seed_of_a_task() -> None:
    jobs = plan(TASKS, model="stub:methodical", seeds=SEEDS, backend=SandboxBackend.LOCAL)
    assert _grid(jobs) == [
        ("alpha-01", 0),
        ("alpha-01", 1),
        ("beta-01", 0),
        ("beta-01", 1),
    ]


def test_seed_major_groups_every_task_at_a_seed() -> None:
    jobs = plan(
        TASKS, model="stub:methodical", seeds=SEEDS, backend=SandboxBackend.LOCAL, order="seed"
    )
    assert _grid(jobs) == [
        ("alpha-01", 0),
        ("beta-01", 0),
        ("alpha-01", 1),
        ("beta-01", 1),
    ]


def test_a_cut_after_a_whole_seed_leaves_no_partial_seed() -> None:
    """The point of seed-major: truncating the plan leaves complete seeds."""
    jobs = plan(
        TASKS, model="stub:methodical", seeds=SEEDS, backend=SandboxBackend.LOCAL, order="seed"
    )
    completed = _grid(jobs[: len(TASKS)])  # as if the ceiling stopped the suite here
    assert {seed for _, seed in completed} == {0}
    assert {task for task, _ in completed} == {"alpha-01", "beta-01"}


def test_an_unknown_order_is_refused() -> None:
    with pytest.raises(ValueError, match="order must be"):
        plan(TASKS, model="stub:methodical", seeds=SEEDS, backend=SandboxBackend.LOCAL, order="x")
