"""Reading and writing results.

Everything here is synchronous SQLModel against Postgres. Aggregation is done in Python by
`trajectory_core.aggregate` rather than in SQL, for one reason worth stating plainly: the
CLI's report and this service's leaderboard have to produce identical numbers, and the only
way to guarantee that is for both to call the same function. At the scale this service is
built for the query cost is irrelevant, and the seam for moving it later is right here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from trajectory_api.tables import BundleRow, FailureHitRow, RunRow, StepRow, TaskRow
from trajectory_core.models import (
    BundleManifest,
    Detector,
    Language,
    Run,
    RunConfig,
    RunnerFingerprint,
    RunStatus,
    RunSummary,
    SandboxBackend,
    Step,
    TaskSummary,
    Verification,
    WorkspaceManifest,
)

log = structlog.get_logger(__name__)


# ------------------------------------------------------------------- ingest


def run_exists(session: Session, run_id: str) -> bool:
    """Whether a run has already been stored."""
    return session.get(RunRow, run_id) is not None


def store_run(session: Session, run: Run, *, bundle_id: str | None = None) -> None:
    """Insert one run, its steps and its failure hits.

    The caller is responsible for skipping runs that already exist. Ingest is idempotent on
    run identifier so that re-pushing after a network failure is safe, which matters more
    than it sounds: if retrying is unsafe, nobody retries, and results are lost instead.
    """
    verification = run.verification
    metrics = run.score

    session.add(
        RunRow(
            id=run.id,
            schema_version=run.schema_version,
            harness_version=run.harness_version,
            bundle_id=bundle_id,
            task_id=run.task_id,
            suite=run.suite,
            model=run.config.model,
            seed=run.config.seed,
            backend=run.runner_fingerprint.sandbox_backend.value,
            status=run.status.value,
            solved=run.solved,
            tests_passed=verification.tests_passed if verification else 0,
            tests_total=verification.tests_total if verification else 0,
            verification_exit_code=verification.exit_code if verification else None,
            verification_parse_ok=verification.parse_ok if verification else True,
            stderr_tail=verification.stderr_tail if verification else "",
            partial_credit=metrics.partial_credit if metrics else 0.0,
            step_efficiency=metrics.step_efficiency if metrics else None,
            tool_call_validity=metrics.tool_call_validity if metrics else 1.0,
            redundant_action_rate=metrics.redundant_action_rate if metrics else 0.0,
            recovery_rate=metrics.recovery_rate if metrics else None,
            premature_termination=metrics.premature_termination if metrics else False,
            context_drift=metrics.context_drift if metrics else None,
            destructive_attempts=metrics.destructive_attempts if metrics else 0,
            steps_count=len(run.steps),
            commands_count=metrics.total_commands if metrics else 0,
            schema_violations=metrics.schema_violations if metrics else 0,
            failed_commands=metrics.failed_commands if metrics else 0,
            cost_usd=run.total_cost_usd,
            wall_clock_s=run.wall_clock_s,
            image_id=run.image_id,
            context_compressed=run.context_compressed,
            error=run.error,
            config=run.config.model_dump(mode="json"),
            fingerprint=run.runner_fingerprint.model_dump(mode="json"),
            initial_workspace=run.initial_workspace.model_dump(mode="json"),
            final_workspace=run.final_workspace.model_dump(mode="json"),
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
    )
    # Flush the parent before adding the children. Steps and failure hits carry a foreign
    # key to runs, and without a declared relationship SQLAlchemy has no ordering
    # information, so an autoflush triggered by the next read can try to insert a child
    # first. One explicit flush is cheaper than a relationship that exists only to teach
    # the ORM something the code already knows.
    session.flush()

    for step in run.steps:
        session.add(
            StepRow(
                run_id=run.id,
                idx=step.index,
                timestamp=step.timestamp,
                thought=step.thought,
                tool_name=step.tool_name,
                tool_args=step.tool_args,
                tool_output=step.tool_output,
                exit_code=step.exit_code,
                duration_ms=step.duration_ms,
                tokens_in=step.tokens_in,
                tokens_out=step.tokens_out,
                cost_usd=step.cost_usd,
                truncated=step.truncated,
                schema_violation=step.schema_violation,
                error=step.error,
            )
        )

    for hit in run.failure_modes:
        session.add(
            FailureHitRow(
                run_id=run.id,
                mode_id=hit.id.value,
                name=hit.name,
                confidence=hit.confidence,
                detector=hit.detector.value,
                evidence=hit.evidence,
                step_indices=hit.step_indices,
            )
        )


def store_bundle_manifest(session: Session, manifest: BundleManifest) -> str:
    """Record an ingest for provenance, returning its identifier.

    Kept because "which push produced this number, from which machine, at which harness
    version" is the first question anyone asks when a published figure looks wrong.
    """
    existing = session.get(BundleRow, manifest.bundle_id)
    if existing is None:
        session.add(
            BundleRow(
                id=manifest.bundle_id,
                suite=manifest.suite,
                harness_version=manifest.harness_version,
                schema_version=manifest.schema_version,
                content_sha256=manifest.content_sha256,
                run_count=manifest.run_count,
                models=list(manifest.models),
                fingerprint=manifest.fingerprint.model_dump(mode="json"),
                notes=manifest.notes,
            )
        )
    return manifest.bundle_id


def upsert_task(session: Session, task: TaskSummary) -> None:
    """Store or refresh public task metadata."""
    row = session.get(TaskRow, task.id)
    values = {
        "suite": task.suite,
        "title": task.title,
        "description": task.description,
        "language": task.language.value,
        "difficulty": task.difficulty,
        "tags": list(task.tags),
        "max_steps": task.max_steps,
        "reference_step_count": task.reference_step_count,
        "updated_at": datetime.now(UTC),
    }
    if row is None:
        session.add(TaskRow(id=task.id, **values))
    else:
        for key, value in values.items():
            setattr(row, key, value)


def delete_run(session: Session, run_id: str) -> None:
    """Remove a run and everything hanging off it. Used by the tests."""
    session.execute(delete(StepRow).where(col(StepRow.run_id) == run_id))
    session.execute(delete(FailureHitRow).where(col(FailureHitRow.run_id) == run_id))
    row = session.get(RunRow, run_id)
    if row is not None:
        session.delete(row)


# --------------------------------------------------------------------- read


def _summary(row: RunRow, modes: list[str]) -> RunSummary:
    """Turn a run row and its mode identifiers into a summary."""
    from trajectory_core.models import FailureModeId

    return RunSummary(
        id=row.id,
        task_id=row.task_id,
        suite=row.suite,
        model=row.model,
        seed=row.seed,
        backend=SandboxBackend(row.backend),
        status=RunStatus(row.status),
        solved=row.solved,
        tests_passed=row.tests_passed,
        tests_total=row.tests_total,
        partial_credit=row.partial_credit,
        step_efficiency=row.step_efficiency,
        tool_call_validity=row.tool_call_validity,
        redundant_action_rate=row.redundant_action_rate,
        recovery_rate=row.recovery_rate,
        premature_termination=row.premature_termination,
        context_drift=row.context_drift,
        destructive_attempts=row.destructive_attempts,
        steps=row.steps_count,
        cost_usd=row.cost_usd,
        wall_clock_s=row.wall_clock_s,
        failure_modes=[FailureModeId(mode) for mode in modes],
        started_at=row.started_at,
    )


def modes_by_run(session: Session, run_ids: list[str]) -> dict[str, list[str]]:
    """Fetch failure mode identifiers for a set of runs, ranked by confidence."""
    if not run_ids:
        return {}
    statement = (
        select(FailureHitRow)
        .where(col(FailureHitRow.run_id).in_(run_ids))
        .order_by(col(FailureHitRow.confidence).desc(), col(FailureHitRow.mode_id))
    )
    grouped: dict[str, list[str]] = {}
    for hit in session.exec(statement):
        grouped.setdefault(hit.run_id, []).append(hit.mode_id)
    return grouped


def list_run_summaries(
    session: Session,
    *,
    suite: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    task_id: str | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[RunSummary]:
    """Load run summaries, filtered."""
    statement = select(RunRow)
    if suite:
        statement = statement.where(RunRow.suite == suite)
    if backend:
        statement = statement.where(RunRow.backend == backend)
    if model:
        statement = statement.where(RunRow.model == model)
    if task_id:
        statement = statement.where(RunRow.task_id == task_id)
    if since:
        statement = statement.where(col(RunRow.started_at) >= since)
    statement = statement.order_by(col(RunRow.id))
    if limit:
        statement = statement.limit(limit)

    rows = list(session.exec(statement))
    modes = modes_by_run(session, [row.id for row in rows])
    return [_summary(row, modes.get(row.id, [])) for row in rows]


def reconstruct_runs(summaries: list[RunSummary]) -> list[Run]:
    """Rebuild the minimum `Run` objects the core aggregation needs.

    The aggregation works on `Run`, and the leaderboard only reads the score, the config
    and the fingerprint. Rebuilding those three from a summary avoids loading every
    trajectory to compute a table, which for 180 runs is a few megabytes of steps that
    nothing in the answer depends on.
    """
    from trajectory_core.models import FailureModeHit, TrajectoryScore

    runs: list[Run] = []
    for summary in summaries:
        score = TrajectoryScore(
            solved=summary.solved,
            partial_credit=summary.partial_credit,
            step_efficiency=summary.step_efficiency,
            tool_call_validity=summary.tool_call_validity,
            redundant_action_rate=summary.redundant_action_rate,
            recovery_rate=summary.recovery_rate,
            premature_termination=summary.premature_termination,
            context_drift=summary.context_drift,
            cost_usd=summary.cost_usd,
            wall_clock_s=summary.wall_clock_s,
            destructive_attempts=summary.destructive_attempts,
            total_steps=summary.steps,
            total_commands=0,
            schema_violations=0,
            failed_commands=0,
        )
        runs.append(
            Run(
                id=summary.id,
                task_id=summary.task_id,
                suite=summary.suite,
                config=RunConfig(
                    model=summary.model,
                    max_steps=1,
                    seed=summary.seed,
                    timeout_seconds=30,
                    sandbox_backend=summary.backend,
                ),
                started_at=summary.started_at,
                finished_at=summary.started_at,
                status=summary.status,
                verification=Verification(
                    passed=summary.solved,
                    tests_passed=summary.tests_passed,
                    tests_total=summary.tests_total,
                    duration_ms=0,
                    exit_code=0 if summary.solved else 1,
                ),
                score=score,
                failure_modes=[
                    FailureModeHit(
                        id=mode,
                        name=mode.value,
                        confidence=1.0,
                        detector=Detector.RULE,
                        evidence="stored",
                    )
                    for mode in summary.failure_modes
                ],
                runner_fingerprint=RunnerFingerprint(
                    os="stored",
                    os_release="stored",
                    arch="stored",
                    python_version="stored",
                    cpu_count=1,
                    sandbox_backend=summary.backend,
                ),
            )
        )
    return runs


def get_run(session: Session, run_id: str, *, with_steps: bool = True) -> Run | None:
    """Rebuild one full run record from the database."""
    row = session.get(RunRow, run_id)
    if row is None:
        return None

    from trajectory_core.models import FailureModeHit, FailureModeId, TrajectoryScore

    steps: list[Step] = []
    if with_steps:
        statement = select(StepRow).where(col(StepRow.run_id) == run_id).order_by(col(StepRow.idx))
        steps = [
            Step(
                index=step.idx,
                timestamp=step.timestamp,
                thought=step.thought,
                tool_name=step.tool_name,
                tool_args=step.tool_args,
                tool_output=step.tool_output,
                exit_code=step.exit_code,
                duration_ms=step.duration_ms,
                tokens_in=step.tokens_in,
                tokens_out=step.tokens_out,
                cost_usd=step.cost_usd,
                truncated=step.truncated,
                schema_violation=step.schema_violation,
                error=step.error,
            )
            for step in session.exec(statement)
        ]

    hits_statement = (
        select(FailureHitRow)
        .where(col(FailureHitRow.run_id) == run_id)
        .order_by(col(FailureHitRow.confidence).desc(), col(FailureHitRow.mode_id))
    )
    hits = [
        FailureModeHit(
            id=FailureModeId(hit.mode_id),
            name=hit.name,
            confidence=hit.confidence,
            detector=Detector(hit.detector),
            evidence=hit.evidence,
            step_indices=list(hit.step_indices),
        )
        for hit in session.exec(hits_statement)
    ]

    return Run(
        id=row.id,
        schema_version=row.schema_version,
        task_id=row.task_id,
        suite=row.suite,
        config=RunConfig.model_validate(row.config),
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=RunStatus(row.status),
        steps=steps,
        verification=Verification(
            passed=row.solved,
            tests_passed=row.tests_passed,
            tests_total=row.tests_total,
            stderr_tail=row.stderr_tail,
            duration_ms=0,
            exit_code=row.verification_exit_code if row.verification_exit_code is not None else 0,
            parse_ok=row.verification_parse_ok,
        ),
        score=TrajectoryScore(
            solved=row.solved,
            partial_credit=row.partial_credit,
            step_efficiency=row.step_efficiency,
            tool_call_validity=row.tool_call_validity,
            redundant_action_rate=row.redundant_action_rate,
            recovery_rate=row.recovery_rate,
            premature_termination=row.premature_termination,
            context_drift=row.context_drift,
            cost_usd=row.cost_usd,
            wall_clock_s=row.wall_clock_s,
            destructive_attempts=row.destructive_attempts,
            total_steps=row.steps_count,
            total_commands=row.commands_count,
            schema_violations=row.schema_violations,
            failed_commands=row.failed_commands,
        ),
        failure_modes=hits,
        harness_version=row.harness_version,
        image_id=row.image_id,
        runner_fingerprint=RunnerFingerprint.model_validate(row.fingerprint),
        initial_workspace=WorkspaceManifest.model_validate(row.initial_workspace),
        final_workspace=WorkspaceManifest.model_validate(row.final_workspace),
        context_compressed=row.context_compressed,
        error=row.error,
    )


def get_trajectory(
    session: Session, run_id: str, *, offset: int = 0, limit: int = 200
) -> tuple[int, list[Step]] | None:
    """Return a page of a trajectory, and the total step count."""
    if session.get(RunRow, run_id) is None:
        return None
    total = int(
        session.exec(
            select(func.count()).select_from(StepRow).where(col(StepRow.run_id) == run_id)
        ).one()
    )
    statement = (
        select(StepRow)
        .where(col(StepRow.run_id) == run_id)
        .order_by(col(StepRow.idx))
        .offset(offset)
        .limit(limit)
    )
    steps = [
        Step(
            index=step.idx,
            timestamp=step.timestamp,
            thought=step.thought,
            tool_name=step.tool_name,
            tool_args=step.tool_args,
            tool_output=step.tool_output,
            exit_code=step.exit_code,
            duration_ms=step.duration_ms,
            tokens_in=step.tokens_in,
            tokens_out=step.tokens_out,
            cost_usd=step.cost_usd,
            truncated=step.truncated,
            schema_violation=step.schema_violation,
            error=step.error,
        )
        for step in session.exec(statement)
    ]
    return total, steps


def list_tasks(session: Session, *, suite: str | None = None) -> list[TaskSummary]:
    """Public task metadata. Never includes anything about the hidden tests."""
    statement = select(TaskRow)
    if suite:
        statement = statement.where(TaskRow.suite == suite)
    statement = statement.order_by(col(TaskRow.id))
    return [
        TaskSummary(
            id=row.id,
            suite=row.suite,
            title=row.title,
            description=row.description,
            language=Language(row.language),
            difficulty=row.difficulty,
            tags=list(row.tags),
            max_steps=row.max_steps,
            reference_step_count=row.reference_step_count,
        )
        for row in session.exec(statement)
    ]


def get_task(session: Session, task_id: str) -> TaskSummary | None:
    """One task's public metadata."""
    tasks = [task for task in list_tasks(session) if task.id == task_id]
    return tasks[0] if tasks else None


def difficulty_map(session: Session) -> dict[str, int]:
    """Task identifier to difficulty tier, for the failure mode breakdown."""
    return {row.id: row.difficulty for row in session.exec(select(TaskRow))}


def counts(session: Session) -> dict[str, int]:
    """Row counts, for the index endpoint and the metrics page."""
    return {
        "runs": int(session.exec(select(func.count()).select_from(RunRow)).one()),
        "steps": int(session.exec(select(func.count()).select_from(StepRow)).one()),
        "tasks": int(session.exec(select(func.count()).select_from(TaskRow)).one()),
        "bundles": int(session.exec(select(func.count()).select_from(BundleRow)).one()),
    }
