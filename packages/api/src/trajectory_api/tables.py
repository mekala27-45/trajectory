"""The database schema.

Shape of the decision: run summaries are columns, trajectories and failure hits are rows,
and aggregation happens in Python.

Summaries are columns because every leaderboard and task query filters and sorts on them.
Steps are rows because the trajectory endpoint is paginated and slicing a JSON blob in the
application to serve page four of a hundred is the wrong shape. Failure hits are rows
because the failure mode endpoint groups by mode across runs.

Aggregation stays in `trajectory_core.aggregate` rather than moving into SQL. At this size
the query cost is nothing, and there is one implementation of the arithmetic, so a number
in the terminal and the same number on the website came from the same code rather than
from two implementations that agree until they do not. If the run count reaches six
figures this is the first thing to move, and the seam is already here: swap the loop in
`queries.py` for a materialised view and nothing above it changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Column, Field, SQLModel, Text


def _now() -> datetime:
    """Current time in UTC."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """`timestamp with time zone` that always comes back in UTC.

    Postgres hands an aware timestamp back in the session time zone. On a server whose
    default zone is UTC that is invisible; on one set to America/New_York the API served
    `07:00:00-05:00` for an instant stored as `12:00:00Z`. Same instant, different string,
    and every client comparing timestamps as text breaks. Normalising here holds whatever
    the server, the pooler or the session is configured as, which a connection option
    cannot promise behind a transaction pooler that refuses startup parameters.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert an aware value to UTC on the way out."""
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC)


def _timestamp(**kwargs: object) -> Column[datetime]:
    """A timezone aware timestamp column, returned in UTC.

    SQLAlchemy's default DateTime maps to `timestamp without time zone`, which silently
    drops the offset on the way in and hands back a naive value on the way out. Every
    timestamp in a run record is UTC aware by construction, and a store that quietly
    strips that turns a reproducible record into an ambiguous one.
    """
    return Column(UtcDateTime(), **kwargs)  # type: ignore[arg-type]


class TaskRow(SQLModel, table=True):
    """Public task metadata. Never holds anything about the hidden tests."""

    __tablename__ = "tasks"

    id: str = Field(primary_key=True, max_length=64)
    suite: str = Field(index=True, max_length=64)
    title: str = Field(max_length=200)
    description: str = Field(sa_column=Column(Text, nullable=False))
    language: str = Field(max_length=32)
    difficulty: int = Field(index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    max_steps: int
    reference_step_count: int
    updated_at: datetime = Field(default_factory=_now, sa_column=_timestamp(nullable=False))


class BundleRow(SQLModel, table=True):
    """One ingest request, kept for provenance."""

    __tablename__ = "bundles"

    id: str = Field(primary_key=True, max_length=64)
    suite: str = Field(index=True, max_length=64)
    harness_version: str = Field(max_length=32)
    schema_version: int
    content_sha256: str = Field(max_length=64)
    run_count: int
    models: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    fingerprint: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    notes: str = Field(default="", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=_now, sa_column=_timestamp(nullable=False))
    ingested_at: datetime = Field(
        default_factory=_now, sa_column=_timestamp(nullable=False, index=True)
    )


class RunRow(SQLModel, table=True):
    """One run, summarised into columns the leaderboard can query."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_model_suite_backend", "model", "suite", "backend"),
        Index("ix_runs_task_model", "task_id", "model"),
    )

    id: str = Field(primary_key=True, max_length=64)
    schema_version: int
    harness_version: str = Field(max_length=32)
    bundle_id: str | None = Field(default=None, foreign_key="bundles.id", index=True)

    task_id: str = Field(index=True, max_length=64)
    suite: str = Field(index=True, max_length=64)
    model: str = Field(index=True, max_length=200)
    seed: int
    backend: str = Field(index=True, max_length=16)

    status: str = Field(max_length=32)
    solved: bool = Field(index=True)
    tests_passed: int
    tests_total: int
    verification_exit_code: int | None = Field(default=None)
    verification_parse_ok: bool = Field(default=True)
    verification_canary: str | None = Field(default=None, max_length=64)
    verification_tampered: bool = Field(default=False)
    stderr_tail: str = Field(default="", sa_column=Column(Text, nullable=False))

    partial_credit: float
    step_efficiency: float | None = Field(default=None)
    tool_call_validity: float
    redundant_action_rate: float
    recovery_rate: float | None = Field(default=None)
    premature_termination: bool
    context_drift: float | None = Field(default=None)
    destructive_attempts: int
    steps_count: int
    commands_count: int
    schema_violations: int
    failed_commands: int
    cost_usd: float
    wall_clock_s: float

    image_id: str | None = Field(default=None, max_length=128)
    context_compressed: bool = Field(default=False)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    fingerprint: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    initial_workspace: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    final_workspace: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )

    started_at: datetime = Field(sa_column=_timestamp(nullable=False, index=True))
    finished_at: datetime | None = Field(default=None, sa_column=_timestamp(nullable=True))
    ingested_at: datetime = Field(
        default_factory=_now, sa_column=_timestamp(nullable=False, index=True)
    )


class StepRow(SQLModel, table=True):
    """One step of one trajectory. Rows because the trajectory endpoint is paginated."""

    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("run_id", "idx", name="uq_steps_run_idx"),)

    pk: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=64)
    idx: int
    timestamp: datetime = Field(sa_column=_timestamp(nullable=False))
    thought: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tool_name: str = Field(max_length=120)
    tool_args: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    tool_output: str = Field(default="", sa_column=Column(Text, nullable=False))
    exit_code: int | None = Field(default=None)
    duration_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    truncated: bool
    schema_violation: bool
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class FailureHitRow(SQLModel, table=True):
    """One failure mode found in one run. Rows because the failures endpoint groups by mode."""

    __tablename__ = "failure_hits"
    __table_args__ = (
        Index("ix_failure_hits_mode_run", "mode_id", "run_id"),
        UniqueConstraint("run_id", "mode_id", name="uq_failure_hits_run_mode"),
    )

    pk: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=64)
    mode_id: str = Field(index=True, max_length=8)
    name: str = Field(max_length=64)
    confidence: float
    detector: str = Field(max_length=8)
    evidence: str = Field(default="", sa_column=Column(Text, nullable=False))
    step_indices: list[int] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))


ALL_TABLES = (TaskRow, BundleRow, RunRow, StepRow, FailureHitRow)
