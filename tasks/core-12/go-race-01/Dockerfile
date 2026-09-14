# Pinned to an exact patch version. Registries were not reachable from the machine this
# was authored on, so digests could not be verified by hand. The harness records the
# resolved image ID on every run instead, which is what actually makes a number
# reproducible. See docs/writing-tasks.md.
FROM golang:1.23.4-bookworm

ARG AGENT_USER=agent

RUN useradd --create-home --uid 1000 ${AGENT_USER}

WORKDIR /workspace
COPY workspace/ /workspace/
RUN chown -R ${AGENT_USER}:${AGENT_USER} /workspace

USER ${AGENT_USER}
ENV PATH=/usr/local/go/bin:${PATH} \
    GOTOOLCHAIN=local \
    GOFLAGS=-mod=mod \
    CGO_ENABLED=1 \
    GOPROXY=off

# Warm the build cache for the agent user, including the race instrumented standard
# library. Without this the first `go test -race` of a run spends half a minute compiling
# the toolchain's own dependencies, which is time taken out of the agent's budget for a
# result that is identical on every run.
RUN go vet ./... >/dev/null 2>&1; go test -race -count=1 -run 'NoTestMatchesThis' ./... >/dev/null 2>&1; true
