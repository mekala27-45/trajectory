"""Liveness and metrics."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlmodel import Session

from trajectory_api import __version__
from trajectory_api.db import engine, ping
from trajectory_api.queries import counts
from trajectory_core.models import HARNESS_VERSION

log = structlog.get_logger(__name__)

router = APIRouter(tags=["operations"])

RUNS_STORED = Gauge("trajectory_runs_stored", "Run records stored in the database")
STEPS_STORED = Gauge("trajectory_steps_stored", "Trajectory steps stored in the database")
TASKS_STORED = Gauge("trajectory_tasks_known", "Tasks the service knows about")


@router.get("/healthz", summary="Liveness, including the database")
def healthz(response: Response) -> dict[str, Any]:
    """Report whether the service can serve requests.

    Checks the database rather than only that the process is up. A health check that
    returns 200 while the database is unreachable teaches the platform to keep routing
    traffic at a machine that cannot answer anything, which is worse than no check.
    """
    database_ok = ping()
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if database_ok else "degraded",
        "api_version": __version__,
        "harness_version": HARNESS_VERSION,
        "database": "ok" if database_ok else "unreachable",
    }


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
def metrics() -> Response:
    """Expose row counts in Prometheus format."""
    try:
        with Session(engine()) as session:
            totals = counts(session)
        RUNS_STORED.set(totals["runs"])
        STEPS_STORED.set(totals["steps"])
        TASKS_STORED.set(totals["tasks"])
    except Exception as exc:  # noqa: BLE001  metrics must never take the service down
        log.warning("metrics.gauges_unavailable", error=str(exc)[:200])
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
