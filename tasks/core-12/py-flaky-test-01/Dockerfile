# Pinned to an exact patch version. Digests are not written in here: the harness records
# the resolved image ID on every run, which is what actually makes a number reproducible.
# See docs/writing-tasks.md.
FROM python:3.12.8-slim-bookworm

ARG AGENT_USER=agent

RUN pip install --no-cache-dir pytest==8.3.4 \
    && useradd --create-home --uid 10001 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/workspace/src
