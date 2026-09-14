# Pinned to an exact patch version. See docs/writing-tasks.md on why digests are recorded
# per run rather than written into the Dockerfile.
FROM node:22.12.0-bookworm-slim

ARG AGENT_USER=agent

# The compiler is installed globally so the task needs neither node_modules nor a network.
RUN npm install --global typescript@5.7.2 \
    && npm cache clean --force \
    && useradd --create-home --uid 1000 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
