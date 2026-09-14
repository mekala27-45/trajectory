"""The tool surface offered to the agent.

Five tools, deliberately. Every extra tool is one more thing that behaves differently
between models, and the point of this harness is to compare models rather than to compare
how carefully each one was prompted for a bespoke interface.

Each tool's arguments are a Pydantic model, and the JSON schema advertised to the provider
is generated from that same model. One definition, so the schema a model is told to
satisfy and the validation its output actually meets cannot drift apart.

Validation failures are never raised. A malformed tool call is recorded as a step carrying
`schema_violation`, and a readable error goes back to the model so it can try again. The
rate of those is one of the ten reported metrics, so it has to survive as data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trajectory_core.models import ToolName
from trajectory_runner.providers import ToolSpec
from trajectory_runner.sandbox import Sandbox

READ_FILE_MAX_LINES = 400
LIST_DIR_MAX_ENTRIES = 300


class _Args(BaseModel):
    """Base for tool argument models. Unknown arguments are a violation, not a warning."""

    model_config = ConfigDict(extra="forbid")


class BashArgs(_Args):
    """Arguments for `bash`."""

    command: str = Field(
        min_length=1,
        description="Shell command to run from the working directory.",
    )


class ReadFileArgs(_Args):
    """Arguments for `read_file`."""

    path: str = Field(min_length=1, description="File path relative to the working directory.")


class WriteFileArgs(_Args):
    """Arguments for `write_file`."""

    path: str = Field(min_length=1, description="File path relative to the working directory.")
    content: str = Field(description="Full contents to write. The file is replaced, not appended.")


class ListDirArgs(_Args):
    """Arguments for `list_dir`."""

    path: str = Field(default=".", description="Directory path relative to the working directory.")


class FinishArgs(_Args):
    """Arguments for `finish`."""

    summary: str = Field(
        min_length=1, description="What you changed and why you believe the task is complete."
    )


_ARG_MODELS: dict[str, type[_Args]] = {
    ToolName.BASH.value: BashArgs,
    ToolName.READ_FILE.value: ReadFileArgs,
    ToolName.WRITE_FILE.value: WriteFileArgs,
    ToolName.LIST_DIR.value: ListDirArgs,
    ToolName.FINISH.value: FinishArgs,
}

_DESCRIPTIONS: dict[str, str] = {
    ToolName.BASH.value: (
        "Run a shell command in the working directory and get back its stdout, stderr and "
        "exit code. The command runs with a timeout, and very large output is cut. There is "
        "no interactive input, so never run a command that waits for a prompt."
    ),
    ToolName.READ_FILE.value: (
        f"Read a text file. Returns at most {READ_FILE_MAX_LINES} lines and tells you when "
        "it had to stop early."
    ),
    ToolName.WRITE_FILE.value: (
        "Write a text file, creating parent directories as needed. This replaces the whole "
        "file, so include the complete contents rather than a fragment."
    ),
    ToolName.LIST_DIR.value: (
        "List one level of a directory. Directory names come back with a trailing slash."
    ),
    ToolName.FINISH.value: (
        "Declare the task complete. Call this only after you have verified your change "
        "actually works, because the run stops here and nothing you meant to do afterwards "
        "will happen."
    ),
}


def _schema_for(model: type[_Args]) -> dict[str, Any]:
    """Generate a provider ready JSON schema from an argument model."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def tool_specs(enabled: list[ToolName]) -> list[ToolSpec]:
    """Build the tool definitions advertised to the model.

    Args:
        enabled: Tools to offer, in the order they should be presented.

    Returns:
        One spec per enabled tool.
    """
    return [
        ToolSpec(
            name=name.value,
            description=_DESCRIPTIONS[name.value],
            parameters=_schema_for(_ARG_MODELS[name.value]),
        )
        for name in enabled
    ]


@dataclass(slots=True)
class ToolResult:
    """What happened when a tool call was executed."""

    output: str
    exit_code: int | None = None
    truncated: bool = False
    error: str | None = None
    schema_violation: bool = False
    is_finish: bool = False
    duration_ms: int = 0


def _violation(message: str, started: float) -> ToolResult:
    """Build the result for a call that never reached the sandbox."""
    return ToolResult(
        output=message,
        error=message,
        schema_violation=True,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _readable_validation_error(exc: ValidationError, tool: str) -> str:
    """Turn a Pydantic error into something a model can act on."""
    parts: list[str] = []
    for item in exc.errors():
        location = ".".join(str(p) for p in item["loc"]) or "(root)"
        parts.append(f"{location}: {item['msg']}")
    expected = ", ".join(sorted(_ARG_MODELS[tool].model_fields))
    return f"Invalid arguments for {tool}: " + "; ".join(parts) + f". Expected fields: {expected}."


def dispatch(
    sandbox: Sandbox,
    name: str,
    raw_args: dict[str, Any],
    *,
    parse_error: str | None = None,
    enabled: list[ToolName],
    command_timeout_s: int,
    cap_bytes: int,
) -> ToolResult:
    """Validate and execute one tool call.

    Args:
        sandbox: Execution environment.
        name: Tool name as the model asked for it.
        raw_args: Arguments as the model supplied them.
        parse_error: Set when the provider could not parse the arguments at all.
        enabled: Tools currently offered.
        command_timeout_s: Per command timeout for `bash`.
        cap_bytes: Output cap applied to command output.

    Returns:
        The result, including whether the call was a schema violation. Never raises for
        anything the model did: the failure is the measurement.
    """
    started = time.monotonic()
    allowed = {tool.value for tool in enabled}

    if parse_error is not None:
        return _violation(
            f"Your tool arguments could not be parsed: {parse_error}. "
            "Send valid JSON matching the tool schema.",
            started,
        )

    if name not in allowed:
        return _violation(
            f"There is no tool named {name!r}. Available tools: {', '.join(sorted(allowed))}.",
            started,
        )

    try:
        args = _ARG_MODELS[name].model_validate(raw_args)
    except ValidationError as exc:
        return _violation(_readable_validation_error(exc, name), started)

    try:
        return _execute(
            sandbox, name, args, command_timeout_s=command_timeout_s, cap_bytes=cap_bytes
        )
    except FileNotFoundError as exc:
        return ToolResult(
            output=f"No such file or directory: {exc}",
            error=str(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except ValueError as exc:
        return ToolResult(
            output=str(exc),
            error=str(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _execute(
    sandbox: Sandbox,
    name: str,
    args: _Args,
    *,
    command_timeout_s: int,
    cap_bytes: int,
) -> ToolResult:
    """Run a validated tool call against the sandbox."""
    started = time.monotonic()

    if isinstance(args, BashArgs):
        result = sandbox.exec(args.command, timeout_s=command_timeout_s, cap_bytes=cap_bytes)
        output = result.combined
        if result.timed_out:
            output = f"{output}\n[command exceeded {command_timeout_s}s and was killed]".strip()
        if result.truncated:
            output = f"{output}\n[output truncated at {cap_bytes} bytes]"
        return ToolResult(
            output=output or "[no output]",
            exit_code=result.exit_code,
            truncated=result.truncated,
            duration_ms=result.duration_ms,
        )

    if isinstance(args, ReadFileArgs):
        text, truncated = sandbox.read_file(args.path, max_lines=READ_FILE_MAX_LINES)
        if truncated:
            text = f"{text}\n[truncated at {READ_FILE_MAX_LINES} lines]"
        return ToolResult(
            output=text or "[empty file]",
            truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    if isinstance(args, WriteFileArgs):
        sandbox.write_file(args.path, args.content)
        lines = args.content.count("\n") + 1
        return ToolResult(
            output=f"Wrote {len(args.content)} bytes ({lines} lines) to {args.path}.",
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    if isinstance(args, ListDirArgs):
        entries = sandbox.list_dir(args.path)
        truncated = len(entries) > LIST_DIR_MAX_ENTRIES
        shown = entries[:LIST_DIR_MAX_ENTRIES]
        body = "\n".join(shown) if shown else "[empty directory]"
        if truncated:
            body = f"{body}\n[{len(entries) - LIST_DIR_MAX_ENTRIES} more entries not shown]"
        return ToolResult(
            output=body,
            truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    if isinstance(args, FinishArgs):
        return ToolResult(
            output="Run ended by the agent.",
            is_finish=True,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    raise AssertionError(f"unhandled tool {name}")  # pragma: no cover
