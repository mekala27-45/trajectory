"""Shared fixtures for the runner tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from trajectory_core.models import Task
from trajectory_core.testing import make_task
from trajectory_runner.sandbox import docker_available

DOCKERFILE = """\
FROM python:3.12-slim-bookworm
ARG AGENT_USER=agent
RUN useradd --create-home --uid 1000 ${AGENT_USER}
RUN pip install --no-cache-dir pytest==8.3.4
WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace
USER ${AGENT_USER}
"""

APP_SOURCE = "def add(a, b):\n    return a - b\n"
FIXED_SOURCE = "def add(a, b):\n    return a + b\n"
HIDDEN_TEST = """\
import sys

sys.path.insert(0, "src")
from app import add


def test_add():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -1) == -2
"""


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """A complete task directory: Dockerfile, workspace, hidden tests, no reference."""
    root = tmp_path / "sample-task-01"
    (root / "workspace" / "src").mkdir(parents=True)
    (root / "verify").mkdir(parents=True)
    (root / "workspace" / "src" / "app.py").write_text(APP_SOURCE)
    (root / "workspace" / "README.md").write_text("# Sample\n")
    (root / "verify" / "test_app.py").write_text(HIDDEN_TEST)
    (root / "Dockerfile").write_text(DOCKERFILE)
    return root


@pytest.fixture
def sample_task() -> Task:
    """A task definition matching `task_dir`."""
    return make_task(
        id="sample-task-01",
        image_tag="trajectory/sample-task-01",
        verify_cmd='python -m pytest -q "$VERIFY_DIR"',
        relevant_paths=["src/**"],
    )


@pytest.fixture
def allow_local(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Opt in to the local sandbox for the duration of a test."""
    monkeypatch.setenv("TRAJECTORY_ALLOW_LOCAL_SANDBOX", "1")
    yield


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip Docker marked tests when no daemon is reachable.

    CI always has a daemon, so these run there. A contributor on a machine without one
    still gets a green suite for everything that does not need it, which is the
    difference between a project people contribute to and one they bounce off.
    """
    del config
    if docker_available():
        return
    skip = pytest.mark.skip(reason="no Docker daemon reachable from this environment")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
