# Multi-stage, non-root, no build tooling in the runtime layer.
#
# Base images are pinned to an exact patch version rather than a digest. The machine this
# was authored on could not reach any container registry, so a digest could not be
# verified by hand, and a digest that is wrong is worse than a tag that is precise. CI
# resolves and records the digest it actually built from. See docs/writing-tasks.md.

FROM python:3.12.8-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5.24 /uv /usr/local/bin/uv

WORKDIR /build

# Copy only what the dependency resolution needs first, so a source change does not
# invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/README.md packages/core/
COPY packages/api/pyproject.toml packages/api/
COPY packages/runner/pyproject.toml packages/runner/README.md packages/runner/

RUN mkdir -p packages/core/src/trajectory_core \
    packages/api/src/trajectory_api \
    packages/runner/src/trajectory_runner \
    && touch packages/core/src/trajectory_core/__init__.py \
    packages/api/src/trajectory_api/__init__.py \
    packages/runner/src/trajectory_runner/__init__.py \
    && uv sync --frozen --no-dev --package trajectory-api

COPY packages/core/src packages/core/src
COPY packages/api/src packages/api/src
COPY packages/api/migrations packages/api/migrations
COPY packages/api/alembic.ini packages/api/alembic.ini

RUN uv sync --frozen --no-dev --package trajectory-api


FROM python:3.12.8-slim-bookworm AS runtime

ARG APP_USER=trajectory
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    TRAJECTORY_LOG_FORMAT=json

RUN useradd --create-home --uid 10001 ${APP_USER}

WORKDIR /app
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/.venv /app/.venv
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/packages/core/src /app/packages/core/src
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/packages/api/src /app/packages/api/src
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/packages/api/migrations /app/migrations
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/packages/api/alembic.ini /app/alembic.ini

USER ${APP_USER}
EXPOSE 8000

# Applies migrations, then serves. Doing it in the entrypoint rather than in a release
# command keeps a single small machine deployable with one command, and Alembic is a no-op
# when the schema is already current.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory trajectory_api.main:create_app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"
