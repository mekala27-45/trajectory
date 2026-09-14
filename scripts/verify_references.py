#!/usr/bin/env python3
"""Assert that every task's reference solution still solves it, and that the task is broken.

Two failures this catches, both of which happen in real benchmarks and both of which
silently flatter every agent that attempts the task:

* A task whose starting state already passes its hidden tests. Every agent scores it.
* A task whose own reference solution stopped passing. No agent can score it, and the
  suite average drops for reasons that have nothing to do with any model.

CI runs this on every push with the Docker backend. Run it locally with the local backend
by exporting TRAJECTORY_ALLOW_LOCAL_SANDBOX=1.

    python scripts/verify_references.py                 # every task
    python scripts/verify_references.py py-perf-01 ...   # named tasks
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog

from trajectory_core.models import SandboxBackend, ToolName
from trajectory_runner.agent import run_agent
from trajectory_runner.loader import LoadedTask, discover
from trajectory_runner.policies import build_script
from trajectory_runner.providers import StubProvider
from trajectory_runner.sandbox import build_sandbox, docker_available
from trajectory_runner.verifier import verify


def _quiet() -> None:
    """Keep harness logging out of the report unless something goes wrong."""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))


def _backend() -> SandboxBackend:
    """Prefer Docker, fall back to the local backend when the operator opted in."""
    if docker_available():
        return SandboxBackend.DOCKER
    if os.environ.get("TRAJECTORY_ALLOW_LOCAL_SANDBOX") == "1":
        return SandboxBackend.LOCAL
    raise SystemExit(
        "no Docker daemon reachable. Start one, or set TRAJECTORY_ALLOW_LOCAL_SANDBOX=1 to "
        "check task definitions without isolation."
    )


def check(loaded: LoadedTask, backend: SandboxBackend) -> bool:
    """Verify one task. Returns True when it behaves as a task should."""
    task, directory = loaded.task, loaded.directory
    allow_local = backend is SandboxBackend.LOCAL

    with build_sandbox(task, directory, backend, allow_local_override=allow_local) as sandbox:
        before = verify(task, sandbox)

    provider = StubProvider("stub:methodical", build_script("methodical", loaded.playbook, 0))
    with build_sandbox(task, directory, backend, allow_local_override=allow_local) as sandbox:
        outcome = run_agent(
            task,
            provider,
            sandbox,
            max_steps=task.max_steps,
            timeout_seconds=task.timeout_seconds,
            command_timeout_seconds=task.command_timeout_seconds,
            cap_bytes=16_384,
            tools_enabled=list(ToolName),
        )
        after = verify(task, sandbox)

    ok = after.passed and not before.passed and outcome.status.value == "completed"
    verdict = "PASS" if ok else "FAIL"
    print(
        f"{verdict}  {task.id:24s} unfixed {before.tests_passed}/{before.tests_total} "
        f"-> reference {after.tests_passed}/{after.tests_total}  "
        f"{len(outcome.steps)} steps  {outcome.status.value}"
    )

    if not ok:
        if before.passed:
            print("   the unfixed workspace already passes, so this task measures nothing")
        for step in outcome.steps:
            if step.exit_code not in (0, None) or step.schema_violation:
                print(f"   step {step.index} {step.tool_name} rc={step.exit_code}")
                print("   " + step.tool_output[-700:].replace("\n", "\n   "))
        if after.stderr_tail:
            print("   verify stderr: " + after.stderr_tail[-900:])
    return ok


def main(argv: list[str]) -> int:
    """Check the named tasks, or every task."""
    _quiet()
    backend = _backend()
    wanted = set(argv) or None
    tasks = [t for t in discover(Path("tasks")) if wanted is None or t.id in wanted]
    if not tasks:
        print(f"no tasks matched {sorted(wanted or [])}", file=sys.stderr)
        return 1
    failures = sum(0 if check(t, backend) else 1 for t in tasks)
    print(f"{len(tasks)} task(s) checked on the {backend.value} backend, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
