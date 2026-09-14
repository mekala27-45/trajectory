"""Database engine and session handling."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

import structlog
from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

from trajectory_api.settings import Settings, get_settings

log = structlog.get_logger(__name__)

_engine: Engine | None = None


def build_engine(settings: Settings | None = None) -> Engine:
    """Create the engine.

    `pool_pre_ping` is on because the free Postgres tiers this targets close idle
    connections aggressively, and a stale connection surfacing as a 500 on the first
    request after a quiet hour is a bad look for a service whose whole job is to be up.
    """
    resolved = settings or get_settings()
    return create_engine(
        resolved.sqlalchemy_url,
        pool_size=resolved.trajectory_db_pool_size,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
    )


def engine() -> Engine:
    """Return the process wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def set_engine(new_engine: Engine | None) -> None:
    """Replace the process wide engine. Used by the tests and by nothing else."""
    global _engine
    _engine = new_engine


def create_all(target: Engine | None = None) -> None:
    """Create every table.

    Alembic owns production schema changes. This exists for tests and for a first local
    start, where running a migration chain to get an empty database is friction with no
    benefit.
    """
    SQLModel.metadata.create_all(target or engine())


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session per request."""
    with Session(engine()) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for scripts and tests, committing on success."""
    with Session(engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def ping(target: Engine | None = None) -> bool:
    """Check the database answers. Used by the liveness probe."""
    try:
        with Session(target or engine()) as session:
            session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return True
    except Exception as exc:  # noqa: BLE001  any failure here means "not healthy"
        log.warning("db.ping_failed", error=str(exc)[:300])
        return False
