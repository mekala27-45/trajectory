# Pinned to an exact patch version. See docs/writing-tasks.md on why digests are recorded
# per run rather than written into the Dockerfile.
FROM python:3.12.8-slim-bookworm

ARG AGENT_USER=agent

RUN pip install --no-cache-dir pytest==8.3.4 \
    && useradd --create-home --uid 10001 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
