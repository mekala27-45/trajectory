"""The results service.

Small on purpose. It ingests signed results bundles, stores them, and serves them. It has
no user accounts, no job queue, no background workers and no cache, because none of those
would do anything a single read-mostly machine cannot already do, and infrastructure added
to look serious reads as inexperience rather than as engineering.

Where the evaluation actually runs is the interesting half of the architecture, and it is
deliberately not here. See ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from trajectory_api import __version__
from trajectory_api.db import engine, ping
from trajectory_api.routes import health, ingest, read
from trajectory_api.settings import Settings, get_settings

REQUEST_ID_HEADER = "X-Request-ID"

log = structlog.get_logger(__name__)


def configure_logging(settings: Settings) -> None:
    """Structured logging, JSON in production."""
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if settings.trajectory_log_format == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    level = getattr(logging, settings.trajectory_log_level.upper(), logging.INFO)
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Check the database at startup and report it rather than failing silently.

    Settings come off `app.state` rather than from the module level loader, so an app
    built with injected settings (which is what the tests do) does not quietly fall back
    to reading the environment.
    """
    settings: Settings = app.state.settings
    configure_logging(settings)
    reachable = ping(engine())
    log.info(
        "service.start",
        api_version=__version__,
        database="reachable" if reachable else "unreachable",
        cors_origins=settings.cors_origins,
    )
    if not reachable:
        log.error(
            "service.database_unreachable",
            hint=(
                "the service will start and report degraded on /healthz. Check DATABASE_URL "
                "and whether migrations have been applied."
            ),
        )
    yield
    log.info("service.stop")


DESCRIPTION = """\
Results service for [trajectory](https://github.com/mekala27-45/trajectory), an evaluation
harness for coding agents.

Read routes are public. Write routes need a bearer token.

Evaluation does not happen here. Running an agent needs Docker, arbitrary CPU and the
ability to execute untrusted model-generated shell commands, so the runner executes on a
laptop or a CI job and pushes a results bundle to this service, which stores it and serves
it. That split is the point rather than a workaround: evaluation compute wants to be
elastic and ephemeral, and results want to be durable and queryable.

Runs produced by the harness's unisolated local sandbox are stored and served, labelled
`local`, as their own leaderboard rows. They are never averaged together with container
runs, because that backend cannot guarantee the agent did not read the hidden tests.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    resolved = settings or get_settings()
    configure_logging(resolved)

    app = FastAPI(
        title="trajectory results API",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
        servers=(
            [{"url": resolved.trajectory_public_base_url, "description": "production"}]
            if resolved.trajectory_public_base_url
            else None
        ),
    )

    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a request id to every log line and every response.

        Without this, a log line from a failing ingest and the 400 the client saw are two
        unrelated facts. With it they are the same fact, and the client can quote the id.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=request.method, path=request.url.path
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception("request.failed", duration_ms=duration_ms, error=str(exc)[:500])
            structlog.contextvars.clear_contextvars()
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error", "request_id": request_id},
                headers={REQUEST_ID_HEADER: request_id},
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers[REQUEST_ID_HEADER] = request_id
        log.info("request", status=response.status_code, duration_ms=duration_ms)
        structlog.contextvars.clear_contextvars()
        return response

    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(read.router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        """Point a browser at something useful."""
        return {
            "service": "trajectory results API",
            "version": __version__,
            "docs": "/docs",
            "leaderboard": "/v1/leaderboard",
            "repository": "https://github.com/mekala27-45/trajectory",
        }

    return app


# There is deliberately no module level `app`. Building it at import time would make
# importing this module require a database URL, which breaks every tool that imports a
# module to inspect it, the tests included. Serve it with the factory instead:
#
#     uvicorn --factory trajectory_api.main:create_app
