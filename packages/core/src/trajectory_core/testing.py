"""Builders for trajectory records.

Hand building a `Run` with twenty steps in every test makes the test unreadable and the
expected value impossible to check by eye. These helpers keep the noise out so a test
shows only the part of the trajectory it cares about.

This ships as part of the package rather than living in the test directory because
anyone adding a metric or a failure mode rule needs exactly these builders, and a
contributor should not have to copy them out of the test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trajectory_core.models import (
    Language,
    PlaybookStep,
    ReferencePlaybook,
    Run,
    RunConfig,
    RunnerFingerprint,
    RunStatus,
    SandboxBackend,
    Step,
    Task,
    ToolName,
    Verification,
    VerifyParser,
)

START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
"""Fixed clock origin so every generated trajectory is byte reproducible."""


def make_task(**overrides: Any) -> Task:  # noqa: ANN401  overrides mirror arbitrary model fields
    """Build a valid task, overriding any field.

    Args:
        **overrides: Field values to replace on the default task.

    Returns:
        A validated task.
    """
    defaults: dict[str, Any] = {
        "id": "demo-task-01",
        "suite": "core-12",
        "title": "Demo task",
        "description": "A task used only by the test suite.",
        "language": Language.PYTHON,
        "difficulty": 2,
        "tags": ["demo"],
        "image_tag": "trajectory/demo-task-01",
        "agent_prompt": "Make the tests pass.",
        "max_steps": 30,
        "timeout_seconds": 300,
        "verify_cmd": "pytest -q /verify",
        "verify_parser": VerifyParser.PYTEST,
        "reference_step_count": 6,
    }
    defaults.update(overrides)
    return Task(**defaults)


def make_config(**overrides: Any) -> RunConfig:  # noqa: ANN401  overrides mirror model fields
    """Build a valid run configuration, overriding any field."""
    defaults: dict[str, Any] = {
        "model": "stub:methodical",
        "max_steps": 30,
        "timeout_seconds": 300,
        "sandbox_backend": SandboxBackend.LOCAL,
    }
    defaults.update(overrides)
    return RunConfig(**defaults)


def make_step(
    index: int,
    *,
    tool: str = ToolName.BASH.value,
    command: str | None = None,
    args: dict[str, Any] | None = None,
    output: str = "",
    exit_code: int | None = 0,
    duration_ms: int = 10,
    cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    truncated: bool = False,
    schema_violation: bool = False,
    thought: str | None = None,
    error: str | None = None,
) -> Step:
    """Build one step.

    Args:
        index: Position in the trajectory.
        tool: Tool name as the model asked for it.
        command: Shorthand for a bash step's `command` argument.
        args: Full tool arguments, merged with `command` when both are given.
        output: Combined stdout and stderr.
        exit_code: Process exit code, or None for non-bash tools.
        duration_ms: Wall clock milliseconds.
        cost_usd: Provider cost attributed to the step.
        tokens_in: Prompt tokens.
        tokens_out: Completion tokens.
        truncated: Whether output was capped.
        schema_violation: Whether the tool call failed validation.
        thought: Assistant text that came with the call.
        error: Harness side error.

    Returns:
        A validated step.
    """
    tool_args: dict[str, Any] = dict(args or {})
    if command is not None:
        tool_args.setdefault("command", command)
    return Step(
        index=index,
        timestamp=START + timedelta(seconds=index),
        thought=thought,
        tool_name=tool,
        tool_args=tool_args,
        tool_output=output,
        exit_code=exit_code,
        duration_ms=duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        truncated=truncated,
        schema_violation=schema_violation,
        error=error,
    )


def bash_steps(*commands: str, exit_codes: list[int] | None = None) -> list[Step]:
    """Build a sequence of bash steps from command strings.

    Args:
        *commands: Shell commands, one per step.
        exit_codes: Exit code per command, defaulting to all zero.

    Returns:
        A list of steps indexed from zero.
    """
    codes = exit_codes or [0] * len(commands)
    return [make_step(i, command=cmd, exit_code=codes[i]) for i, cmd in enumerate(commands)]


def finish_step(index: int, summary: str = "Done.") -> Step:
    """Build a `finish` step."""
    return make_step(index, tool=ToolName.FINISH.value, args={"summary": summary}, exit_code=None)


def make_verification(
    *,
    passed: bool = True,
    tests_passed: int = 4,
    tests_total: int = 4,
    exit_code: int | None = None,
    stderr_tail: str = "",
) -> Verification:
    """Build a verification result consistent with the counts given."""
    return Verification(
        passed=passed,
        tests_passed=tests_passed,
        tests_total=tests_total,
        stderr_tail=stderr_tail,
        duration_ms=1200,
        exit_code=exit_code if exit_code is not None else (0 if passed else 1),
    )


def make_run(
    steps: list[Step] | None = None,
    *,
    verification: Verification | None = None,
    status: RunStatus = RunStatus.COMPLETED,
    task_id: str = "demo-task-01",
    config: RunConfig | None = None,
    finished_after_s: float = 42.0,
    context_compressed: bool = False,
) -> Run:
    """Assemble a run from steps and a verification result."""
    return Run(
        task_id=task_id,
        suite="core-12",
        config=config or make_config(),
        started_at=START,
        finished_at=START + timedelta(seconds=finished_after_s),
        status=status,
        steps=steps if steps is not None else [],
        verification=verification,
        context_compressed=context_compressed,
        runner_fingerprint=RunnerFingerprint(
            os="Linux",
            os_release="test",
            arch="x86_64",
            python_version="3.12.3",
            cpu_count=2,
            docker_version=None,
            sandbox_backend=SandboxBackend.LOCAL,
        ),
    )


def make_playbook(
    *,
    task_id: str = "demo-task-01",
    commands: list[str] | None = None,
    summary: str = "Fixed the off by one and the tests pass.",
) -> ReferencePlaybook:
    """Build a reference playbook from a list of shell commands, closed with finish."""
    resolved = commands or [
        "ls -la",
        "pytest -q",
        "sed -i 's/<=/</' src/dates.py",
        "pytest -q",
    ]
    steps = [
        PlaybookStep(tool=ToolName.BASH, args={"command": cmd}, note=f"Step {i + 1}: {cmd}")
        for i, cmd in enumerate(resolved)
    ]
    steps.append(PlaybookStep(tool=ToolName.FINISH, args={"summary": summary}, note=summary))
    return ReferencePlaybook(task_id=task_id, steps=steps, notes="Test playbook.")


def utc(seconds: float) -> datetime:
    """Return the fixed test clock advanced by `seconds`."""
    return START + timedelta(seconds=seconds)


__all__ = [
    "START",
    "bash_steps",
    "finish_step",
    "make_config",
    "make_playbook",
    "make_run",
    "make_step",
    "make_task",
    "make_verification",
    "utc",
]
