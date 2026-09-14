"""Ingesting a results bundle.

Three properties, each of which exists because of a specific way this goes wrong.

Idempotent on run identifier, so re-pushing after a timeout is safe. If retrying is
unsafe, nobody retries, and results are lost rather than duplicated.

The content hash is recomputed on arrival, so a truncated upload is rejected instead of
half stored. The alternative surfaces weeks later as a suite that mysteriously has eleven
tasks and a leaderboard nobody can reproduce.

The schema version is checked, so a bundle from a newer harness is refused with a readable
message rather than silently losing the fields this version does not know about.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from trajectory_api.db import get_session
from trajectory_api.queries import run_exists, store_bundle_manifest, store_run, upsert_task
from trajectory_api.security import require_api_key
from trajectory_api.settings import get_settings
from trajectory_core.models import SCHEMA_VERSION, ResultsBundle, TaskSummary

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["ingest"])


def _content_hash(bundle: ResultsBundle) -> str:
    """Recompute the bundle's content hash from its runs."""
    import hashlib

    digest = hashlib.sha256()
    for run in sorted(bundle.runs, key=lambda r: r.id):
        digest.update(run.model_dump_json(exclude_none=False).encode())
    return digest.hexdigest()


@router.post(
    "/runs",
    status_code=status.HTTP_200_OK,
    summary="Ingest a results bundle",
    dependencies=[Depends(require_api_key)],
)
def ingest(
    bundle: ResultsBundle,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Store the runs in a bundle.

    Args:
        bundle: The manifest and its runs.
        session: Database session.

    Returns:
        Counts of accepted, duplicate and rejected runs, plus anything the caller should
        know about how the results will be presented.

    Raises:
        HTTPException: 400 on a schema version this service does not understand, a content
            hash that does not match, or a bundle larger than the configured ceiling.
    """
    settings = get_settings()

    if bundle.manifest.schema_version != SCHEMA_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"this service understands schema version {SCHEMA_VERSION}, the bundle "
                f"declares {bundle.manifest.schema_version}. Upgrade the service or "
                "downgrade the harness rather than storing a record it cannot read back."
            ),
        )

    if len(bundle.runs) > settings.trajectory_max_bundle_runs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{len(bundle.runs)} runs exceeds the {settings.trajectory_max_bundle_runs} "
                "run ceiling for one request. Push in chunks; the CLI does this for you."
            ),
        )

    if bundle.manifest.content_sha256:
        recomputed = _content_hash(bundle)
        if recomputed != bundle.manifest.content_sha256:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "the bundle's content hash does not match its runs, which means the "
                    "upload was truncated or altered in transit. Nothing was stored."
                ),
            )

    if bundle.manifest.run_count != len(bundle.runs):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"the manifest declares {bundle.manifest.run_count} runs but the bundle "
                f"carries {len(bundle.runs)}."
            ),
        )

    bundle_id = store_bundle_manifest(session, bundle.manifest)

    accepted = 0
    duplicates = 0
    local_runs = 0
    for run in bundle.runs:
        if run_exists(session, run.id):
            duplicates += 1
            continue
        store_run(session, run, bundle_id=bundle_id)
        accepted += 1
        if run.runner_fingerprint.sandbox_backend.value == "local":
            local_runs += 1

    session.commit()

    message = ""
    if local_runs:
        message = (
            f"{local_runs} of the accepted runs came from the unisolated local sandbox. "
            "They are stored and served, as their own leaderboard rows labelled local, and "
            "are never averaged together with container runs."
        )

    log.info(
        "ingest.done",
        bundle=bundle_id,
        accepted=accepted,
        duplicates=duplicates,
        suite=bundle.manifest.suite,
    )
    return {
        "bundle_id": bundle_id,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": 0,
        "message": message,
    }


@router.post(
    "/tasks",
    status_code=status.HTTP_200_OK,
    summary="Register public task metadata",
    dependencies=[Depends(require_api_key)],
)
def register_tasks(
    tasks: list[TaskSummary],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, int]:
    """Store or refresh task metadata.

    Takes `TaskSummary`, not `Task`. The full task model carries the verification command,
    and a service that never receives it cannot leak it.
    """
    for task in tasks:
        upsert_task(session, task)
    session.commit()
    log.info("tasks.registered", count=len(tasks))
    return {"registered": len(tasks)}
