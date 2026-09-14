"""Public read routes.

Everything here is unauthenticated on purpose. The project exists so that a stranger can
look at the results, and putting a signup in front of that would defeat it.

No route here can return anything about a hidden test. `TaskSummary` omits the
verification command by construction, and the service only ever receives summaries, so
there is nothing to leak even by accident.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from trajectory_api import queries
from trajectory_api.db import get_session
from trajectory_core.aggregate import (
    failure_mode_counts,
    failure_modes_by_difficulty,
    failure_modes_by_model,
    leaderboard,
    per_task_breakdown,
)
from trajectory_core.failure_modes import taxonomy_specs
from trajectory_core.models import (
    HARNESS_VERSION,
    DatasetIndex,
    FailureModeBreakdown,
    LeaderboardResponse,
    Run,
    RunSummary,
    SandboxBackend,
    TaskResults,
    TaskSummary,
)

router = APIRouter(prefix="/v1", tags=["results"])

LOCAL_NOTE = (
    "Rows labelled local came from the unisolated local sandbox. They form their own rows "
    "and are never averaged together with container runs, because the local backend cannot "
    "guarantee the agent did not see the hidden tests."
)
STUB_NOTE = (
    "Models whose identifier starts with stub: are scripted offline policies, not language "
    "models. They validate the harness and supply the recorded trajectories in this demo."
)

SessionDep = Annotated[Session, Depends(get_session)]


def _note(summaries: list[RunSummary]) -> str:
    """Build the caveat a reader needs in order not to misread the numbers."""
    parts = []
    if any(s.backend is SandboxBackend.LOCAL for s in summaries):
        parts.append(LOCAL_NOTE)
    if any(s.model.startswith("stub:") for s in summaries):
        parts.append(STUB_NOTE)
    return " ".join(parts)


@router.get("/index", summary="What this service holds", response_model=DatasetIndex)
def index(session: SessionDep) -> DatasetIndex:
    """Summarise the stored dataset, so a client knows what it is looking at."""
    summaries = queries.list_run_summaries(session)
    return DatasetIndex(
        generated_at=datetime.now(UTC),
        harness_version=HARNESS_VERSION,
        suites=sorted({s.suite for s in summaries}),
        models=sorted({s.model for s in summaries}),
        backends=sorted({s.backend for s in summaries}, key=lambda b: b.value),
        run_count=len(summaries),
        task_count=len({s.task_id for s in summaries}),
        solved_count=sum(1 for s in summaries if s.solved),
        total_cost_usd=round(sum(s.cost_usd for s in summaries), 6),
        total_wall_clock_s=round(sum(s.wall_clock_s for s in summaries), 1),
        judge_model=None,
        note=_note(summaries),
    )


@router.get(
    "/leaderboard", summary="Aggregate metrics per model", response_model=LeaderboardResponse
)
def get_leaderboard(
    session: SessionDep,
    suite: Annotated[str | None, Query(description="Restrict to one suite.")] = None,
    backend: Annotated[
        SandboxBackend | None, Query(description="Restrict to one sandbox backend.")
    ] = None,
    since: Annotated[
        datetime | None, Query(description="Only runs started at or after this time.")
    ] = None,
) -> LeaderboardResponse:
    """Every aggregate metric per model, with its spread across seeds."""
    summaries = queries.list_run_summaries(
        session, suite=suite, backend=backend.value if backend else None, since=since
    )
    runs = queries.reconstruct_runs(summaries)
    return LeaderboardResponse(
        generated_at=datetime.now(UTC),
        harness_version=HARNESS_VERSION,
        suite=suite,
        rows=leaderboard(runs, suite=suite, backend=backend),
        last_run_at=max((s.started_at for s in summaries), default=None),
        note=_note(summaries),
    )


@router.get("/tasks", summary="Public task metadata", response_model=list[TaskSummary])
def get_tasks(
    session: SessionDep,
    suite: Annotated[str | None, Query(description="Restrict to one suite.")] = None,
) -> list[TaskSummary]:
    """Task metadata. Never includes the hidden tests or the command that runs them."""
    return queries.list_tasks(session, suite=suite)


@router.get(
    "/tasks/{task_id}/results", summary="Per model results for one task", response_model=TaskResults
)
def get_task_results(task_id: str, session: SessionDep) -> TaskResults:
    """Which models can do this particular thing, and every run that tried."""
    task = queries.get_task(session, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no task {task_id!r} is registered"
        )
    summaries = queries.list_run_summaries(session, task_id=task_id)
    runs = queries.reconstruct_runs(summaries)
    return TaskResults(
        task=task,
        solve_rate_by_model=per_task_breakdown(runs).get(task_id, {}),
        runs=summaries,
    )


@router.get("/runs", summary="Run summaries", response_model=list[RunSummary])
def get_runs(
    session: SessionDep,
    suite: Annotated[str | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    task_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[RunSummary]:
    """Run summaries without their trajectories."""
    return queries.list_run_summaries(
        session, suite=suite, model=model, task_id=task_id, limit=limit
    )


@router.get("/runs/{run_id}", summary="One run with its score and failure modes")
def get_run(
    run_id: str,
    session: SessionDep,
    include_steps: Annotated[
        bool, Query(description="Include the trajectory. Off for a lighter response.")
    ] = True,
) -> Run:
    """One full run record."""
    run = queries.get_run(session, run_id, with_steps=include_steps)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r} is stored"
        )
    return run


@router.get("/runs/{run_id}/trajectory", summary="A page of one trajectory")
def get_trajectory(
    run_id: str,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, object]:
    """Paginated steps.

    Paginated because a trajectory can be hundreds of steps with sixteen kilobytes of
    output each, and the replay viewer should render the first screen without waiting for
    the last one.
    """
    result = queries.get_trajectory(session, run_id, offset=offset, limit=limit)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r} is stored"
        )
    total, steps = result
    return {
        "run_id": run_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "steps": [step.model_dump(mode="json") for step in steps],
    }


@router.get(
    "/failure-modes",
    summary="The taxonomy plus counts by model and by difficulty",
    response_model=FailureModeBreakdown,
)
def get_failure_modes(
    session: SessionDep,
    suite: Annotated[str | None, Query()] = None,
    among: Annotated[
        str,
        Query(
            description=(
                "Population for the by model and by difficulty slices: unsolved, solved or "
                "all. The solved view is the one a pass rate cannot produce."
            ),
            pattern="^(unsolved|solved|all)$",
        ),
    ] = "all",
) -> FailureModeBreakdown:
    """The taxonomy with counts, sliced the two ways that are actionable."""
    summaries = queries.list_run_summaries(session, suite=suite)
    runs = queries.reconstruct_runs(summaries)
    unsolved = sum(1 for s in summaries if not s.solved)
    scope: str = among
    return FailureModeBreakdown(
        taxonomy=taxonomy_specs(),
        overall=failure_mode_counts(runs),
        on_solved_runs=failure_mode_counts(runs, among="solved"),
        by_model=failure_modes_by_model(runs, among=scope),  # type: ignore[arg-type]
        by_difficulty={
            str(tier): counts
            for tier, counts in failure_modes_by_difficulty(
                runs,
                queries.difficulty_map(session),
                among=scope,  # type: ignore[arg-type]
            ).items()
        },
        unsolved_runs=unsolved,
        solved_runs=len(summaries) - unsolved,
    )
