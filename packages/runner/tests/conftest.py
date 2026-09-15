"""Shared fixtures for the runner tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trajectory_core.models import Task
from trajectory_core.testing import make_task
from trajectory_runner.sandbox import docker_available, local_verify_runnable

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
    """Skip tests whose environment is not present, with a reason that names what is missing.

    Two dependencies live outside this repository. A Docker daemon, for the container
    backend. And a host `python` that can run `python -m pytest`, for the tests that drive
    a verify command through the local backend: that command is written against the task
    image, where the Dockerfile installs pytest, and the local backend has no image.

    CI provides both, so these run there. A contributor missing either still gets a green
    suite for everything that does not need it, which is the difference between a project
    people contribute to and one they bounce off. The alternative is what shipped once
    already: a suite that passed on the authoring machine and failed on a runner with
    nothing but `No module named pytest` to go on.
    """
    del config
    reasons = {
        "docker": (
            docker_available,
            "no Docker daemon reachable from this environment",
        ),
        "local_verify": (
            local_verify_runnable,
            "the host `python` on the sanitised PATH cannot run `python -m pytest`, which "
            "a verify command on the local backend needs. Install pytest for that "
            "interpreter, or run these against the Docker backend.",
        ),
    }
    for marker, (available, reason) in reasons.items():
        if available():
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
