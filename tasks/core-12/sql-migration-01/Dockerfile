# Pinned to an exact patch version. Registries were not reachable from the machine this
# was authored on, so digests could not be verified by hand. The harness records the
# resolved image ID on every run instead, which is what actually makes a number
# reproducible. See docs/writing-tasks.md.
FROM postgres:16.6-bookworm

ARG AGENT_USER=agent

# The image's entrypoint exists to bootstrap a server as the container's main process.
# This container is driven by exec and brings its own cluster up from the workspace, so
# clear it rather than relying on the entrypoint passing an unknown command through.
ENTRYPOINT []

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages pytest==8.3.4 \
    && useradd --create-home --uid 10001 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PATH=/usr/lib/postgresql/16/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PGDATABASE=appdb
