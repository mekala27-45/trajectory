"""Fixtures for the API tests.

These run against a real Postgres, never SQLite. The service uses JSONB columns, a
timezone aware timestamp type, and partial index behaviour that SQLite does not have, so a
suite that passes on SQLite would be testing a database this service never talks to.

Three ways to get one, in order: an explicit `TRAJECTORY_TEST_DATABASE_URL`, which is what
CI sets from its Postgres service container; a testcontainers instance, when a Docker
daemon is reachable; otherwise the suite skips with a reason rather than silently passing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlmodel import Session

from trajectory_api import db as db_module
from trajectory_api import security
from trajectory_api.db import build_engine, create_all
from trajectory_api.main import create_app
from trajectory_api.settings import Settings
from trajectory_core.models import (
    HARNESS_VERSION,
    SCHEMA_VERSION,
    BundleManifest,
    Language,
    ResultsBundle,
    Run,
    RunnerFingerprint,
    RunStatus,
    SandboxBackend,
    TaskSummary,
)
from trajectory_core.scoring import score
from trajectory_core.testing import (
    bash_steps,
    finish_step,
    make_config,
    make_run,
    make_step,
    make_task,
    make_verification,
)

TEST_API_KEY = "test-api-key-value"
_ENV_URL = "TRAJECTORY_TEST_DATABASE_URL"


def _resolve_database_url() -> tuple[str, object | None]:
    """Find a Postgres to test against, returning the URL and anything to shut down."""
    explicit = os.environ.get(_ENV_URL)
    if explicit:
        return explicit, None

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover
        pytest.skip(f"set {_ENV_URL}, or install testcontainers with a Docker daemon")

    from trajectory_runner.sandbox import docker_available

    if not docker_available():
        pytest.skip(
            f"no Docker daemon for testcontainers and no {_ENV_URL}. These tests need a "
            "real Postgres: the service uses JSONB and timezone aware timestamps, so a "
            "SQLite substitute would be testing a database it never talks to."
        )

    container = PostgresContainer("postgres:16.6-bookworm", driver="psycopg")
    container.start()
    return container.get_connection_url(), container


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A Postgres URL for the session."""
    url, container = _resolve_database_url()
    try:
        yield url
    finally:
        if container is not None and hasattr(container, "stop"):
            container.stop()


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    """Settings pointed at the test database."""
    return Settings(
        database_url=database_url,
        trajectory_api_key=TEST_API_KEY,
        trajectory_cors_origins="http://localhost:3000,https://trajectory.example",
        trajectory_log_format="console",
        trajectory_rate_limit_writes_per_minute=1000,
    )


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    """An engine against the test database, with the schema created."""
    built = build_engine(settings)
    create_all(built)
    yield built
    built.dispose()


@pytest.fixture(autouse=True)
def _wire(monkeypatch: pytest.MonkeyPatch, engine: Engine, settings: Settings) -> Iterator[None]:
    """Point the module level engine, settings and limiter at the test instances."""
    monkeypatch.setattr("trajectory_api.settings.get_settings", lambda: settings)
    monkeypatch.setattr("trajectory_api.db.get_settings", lambda: settings)
    monkeypatch.setattr("trajectory_api.security.get_settings", lambda: settings)
    monkeypatch.setattr("trajectory_api.routes.ingest.get_settings", lambda: settings)
    db_module.set_engine(engine)
    security.set_limiter(
        security.RateLimiter(per_minute=settings.trajectory_rate_limit_writes_per_minute)
    )
    yield
    db_module.set_engine(None)
    security.set_limiter(None)


@pytest.fixture(autouse=True)
def _clean(engine: Engine) -> Iterator[None]:
    """Empty every table between tests, so ordering cannot hide a bug."""
    with Session(engine) as session:
        session.exec(  # type: ignore[call-overload]
            text("TRUNCATE failure_hits, steps, runs, bundles, tasks")
        )
        session.commit()
    yield


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A test client for the application."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    """Headers for a write request."""
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


# ------------------------------------------------------------------ builders


def build_run(
    *,
    task_id: str = "py-failing-suite-01",
    model: str = "stub:methodical",
    seed: int = 0,
    solved: bool = True,
    backend: SandboxBackend = SandboxBackend.DOCKER,
    steps: int = 4,
    cost: float = 0.0,
    status: RunStatus = RunStatus.COMPLETED,
) -> Run:
    """A scored run ready to ingest."""
    trajectory = [
        *bash_steps(*[f"cmd {index} for {task_id}" for index in range(max(1, steps - 1))]),
        finish_step(max(1, steps - 1)),
    ]
    for step in trajectory:
        step.cost_usd = cost / len(trajectory)
    run = make_run(
        trajectory,
        verification=make_verification(
            passed=solved, tests_passed=8 if solved else 3, tests_total=8
        ),
        status=status,
        task_id=task_id,
        config=make_config(model=model, seed=seed, sandbox_backend=backend),
        finished_after_s=12.5,
    )
    run.runner_fingerprint = RunnerFingerprint(
        os="Linux",
        os_release="test",
        arch="x86_64",
        python_version="3.12.3",
        cpu_count=2,
        docker_version="27.3.1" if backend is SandboxBackend.DOCKER else None,
        sandbox_backend=backend,
    )
    run.image_id = "sha256:" + "a" * 64 if backend is SandboxBackend.DOCKER else None
    run.score = score(run, make_task(id=task_id, reference_step_count=3))
    return run


def build_bundle(runs: list[Run], *, suite: str = "core-12", notes: str = "test") -> ResultsBundle:
    """A bundle with a correct content hash."""
    from trajectory_runner.store import bundle_content_hash

    ordered = sorted(runs, key=lambda run: run.id)
    return ResultsBundle(
        manifest=BundleManifest(
            suite=suite,
            harness_version=HARNESS_VERSION,
            schema_version=SCHEMA_VERSION,
            run_count=len(ordered),
            models=sorted({run.config.model for run in ordered}),
            content_sha256=bundle_content_hash(ordered),
            fingerprint=ordered[0].runner_fingerprint,
            notes=notes,
        ),
        runs=ordered,
    )


def build_task(task_id: str = "py-failing-suite-01", difficulty: int = 1) -> TaskSummary:
    """Public task metadata."""
    return TaskSummary(
        id=task_id,
        suite="core-12",
        title=f"Title for {task_id}",
        description="What is broken and what fixed means.",
        language=Language.PYTHON,
        difficulty=difficulty,
        tags=["demo"],
        max_steps=25,
        reference_step_count=6,
    )


def utc_now() -> datetime:
    """Current UTC time."""
    return datetime.now(UTC)


__all__ = [
    "TEST_API_KEY",
    "build_bundle",
    "build_run",
    "build_task",
    "make_step",
    "utc_now",
]
