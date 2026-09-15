# Pinned to an exact patch version. Registries were not reachable from the machine this
# was authored on, so digests could not be verified by hand. The harness records the
# resolved image ID on every run instead, which is what actually makes a number
# reproducible. See docs/writing-tasks.md.
FROM node:22.12.0-bookworm-slim

ARG AGENT_USER=agent

# The compiler is global so the task needs neither node_modules nor a network. The
# generator is plain node with no dependencies.
RUN npm install --global typescript@5.7.2 \
    && npm cache clean --force \
    && useradd --create-home --uid 10001 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
