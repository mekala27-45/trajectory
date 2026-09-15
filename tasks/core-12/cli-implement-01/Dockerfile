# Pinned to an exact patch version. Registries were not reachable from the machine this
# was authored on, so digests could not be verified by hand. The harness records the
# resolved image ID on every run instead, which is what actually makes a number
# reproducible. See docs/writing-tasks.md.
FROM python:3.12.8-slim-bookworm

ARG AGENT_USER=agent

RUN pip install --no-cache-dir pytest==8.3.4 \
    && useradd --create-home --uid 10001 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
