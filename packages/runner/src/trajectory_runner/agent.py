"""The tool calling loop.

Four things end a run: the agent calls `finish`, it exhausts its step budget, the wall
clock runs out, or the next model call would cross the cost ceiling. Which one happened is
recorded on the run, because "the agent stopped" and "the agent was stopped" are different
findings and a status field that conflates them is useless.

Three behaviours here are worth reading the code for.

A malformed tool call is not a crash. It becomes a step with `schema_violation` set, a
readable error goes back to the model, and the loop continues. Counting those is one of
the ten metrics.

The conversation is never truncated silently. When the prompt approaches the model's
context window the oldest tool outputs are replaced with a one line summary and
`context_compressed` is set on the run, because compression changes results and a result
whose cause is invisible is worse than no result.

Every step is flushed to disk the moment it completes, before the next model call. A run
that dies at step 38 still leaves 38 usable steps behind. Losing an afternoon of API spend
to a process that died with everything in memory is a self-inflicted wound.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from trajectory_core.models import RunStatus, Step, Task, ToolName
from trajectory_runner.providers import ChatMessage, Provider, ProviderError, ToolCall
from trajectory_runner.sandbox import Sandbox
from trajectory_runner.tools import ToolResult, dispatch, tool_specs

log = structlog.get_logger(__name__)

COMPRESSION_THRESHOLD = 0.75
"""Fraction of the context window at which the oldest tool outputs get summarised."""

COMPRESSION_KEEP_RECENT = 6
"""Tool results that are never compressed, so the agent keeps its short term memory."""

MAX_CONSECUTIVE_NO_TOOL = 3
"""Replies without a tool call before the loop gives up rather than burning the budget."""

SYSTEM_PROMPT = """\
You are a software engineer working in a shell. Solve the task in the working directory.

How this works:
- You act only through the tools you have been given. There is no human to ask.
- Look before you change anything. Read the files, run the existing tests, understand what
  is actually broken rather than what looks broken.
- After you make a change, run whatever verifies it. A change you have not run is a guess.
- When you are confident the task is complete, call finish with a short summary. The run
  ends there, so do not call it while anything is still failing.

Constraints:
- Commands are not interactive. Anything that waits for input will time out.
- Keep changes scoped to the task. Do not rewrite files the task did not ask you to touch.
"""


@dataclass(slots=True)
class AgentOutcome:
    """What the loop produced."""

    status: RunStatus
    steps: list[Step] = field(default_factory=list)
    context_compressed: bool = False
    error: str | None = None

    @property
    def cost_usd(self) -> float:
        """Total provider cost across the trajectory."""
        return round(sum(s.cost_usd for s in self.steps), 6)


StepSink = Callable[[Step], None]
"""Called with every step as soon as it completes, for incremental flushing."""


def _tool_message(call: ToolCall, result: ToolResult) -> ChatMessage:
    """Build the tool result message sent back to the model."""
    body = result.output
    if result.exit_code is not None:
        body = f"exit code: {result.exit_code}\n{body}"
    return ChatMessage(role="tool", content=body, tool_call_id=call.id, name=call.name)


def _assistant_message(thought: str | None, calls: list[ToolCall]) -> ChatMessage:
    """Rebuild the assistant turn so the provider sees a consistent history."""
    return ChatMessage(
        role="assistant",
        content=thought,
        tool_calls=[
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments
                    if call.raw_arguments is not None
                    else _dump(call.arguments),
                },
            }
            for call in calls
        ]
        or None,
    )


def _dump(args: dict[str, object]) -> str:
    """Serialise tool arguments back to JSON for the conversation history."""
    import json

    return json.dumps(args)


def compress(messages: list[ChatMessage], keep_recent: int = COMPRESSION_KEEP_RECENT) -> int:
    """Replace the oldest tool outputs with a one line summary.

    Args:
        messages: Conversation, modified in place.
        keep_recent: Number of trailing tool messages left untouched.

    Returns:
        How many messages were compressed.
    """
    tool_positions = [i for i, m in enumerate(messages) if m.role == "tool"]
    candidates = tool_positions[: max(0, len(tool_positions) - keep_recent)]
    compressed = 0
    for index in candidates:
        message = messages[index]
        body = message.content or ""
        if body.startswith("[compressed:"):
            continue
        first = body.splitlines()[0] if body.splitlines() else ""
        messages[index] = ChatMessage(
            role="tool",
            content=(
                f"[compressed: {len(body)} bytes of earlier output removed. "
                f"First line: {first[:160]}]"
            ),
            tool_call_id=message.tool_call_id,
            name=message.name,
        )
        compressed += 1
    return compressed


def run_agent(
    task: Task,
    provider: Provider,
    sandbox: Sandbox,
    *,
    max_steps: int,
    timeout_seconds: int,
    command_timeout_seconds: int,
    cap_bytes: int,
    tools_enabled: list[ToolName],
    budget_usd: float | None = None,
    on_step: StepSink | None = None,
) -> AgentOutcome:
    """Run the agent against the task until something stops it.

    Args:
        task: The task being attempted.
        provider: Model to drive the loop.
        sandbox: Started sandbox holding the workspace.
        max_steps: Step ceiling.
        timeout_seconds: Wall clock ceiling for the whole loop.
        command_timeout_seconds: Ceiling for any single command.
        cap_bytes: Output cap per command.
        tools_enabled: Tools offered to the model.
        budget_usd: Hard spend ceiling checked before every model call.
        on_step: Called with each completed step, for incremental flushing.

    Returns:
        The trajectory and the reason the loop stopped.
    """
    specs = tool_specs(tools_enabled)
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=task.agent_prompt),
    ]

    outcome = AgentOutcome(status=RunStatus.ERROR)
    deadline = time.monotonic() + timeout_seconds
    spent = 0.0
    consecutive_no_tool = 0
    window = provider.context_window()

    while True:
        if len(outcome.steps) >= max_steps:
            outcome.status = RunStatus.MAX_STEPS
            break
        if time.monotonic() >= deadline:
            outcome.status = RunStatus.TIMEOUT
            break

        over_window = provider.count_tokens(messages) > window * COMPRESSION_THRESHOLD
        if over_window and compress(messages):
            outcome.context_compressed = True
            log.info("agent.context_compressed", task=task.id, steps=len(outcome.steps))

        if budget_usd is not None:
            projected = spent + provider.estimate_cost(messages)
            if projected > budget_usd:
                outcome.status = RunStatus.BUDGET_EXCEEDED
                outcome.error = (
                    f"next call was estimated at {projected - spent:.4f} USD, which would take "
                    f"the run to {projected:.4f} USD against a ceiling of {budget_usd:.4f} USD"
                )
                break

        try:
            reply = provider.reply(messages, specs)
        except ProviderError as exc:
            outcome.status = RunStatus.ERROR
            outcome.error = str(exc)
            break

        spent += reply.cost_usd

        if not reply.has_tool_call:
            consecutive_no_tool += 1
            step = _no_tool_step(
                len(outcome.steps), reply.thought, reply.tokens_in, reply.tokens_out, reply.cost_usd
            )
            outcome.steps.append(step)
            if on_step:
                on_step(step)
            messages.append(ChatMessage(role="assistant", content=reply.thought or ""))
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        "You did not call a tool. Every action has to go through a tool call. "
                        "Call one now, or call finish if the task is complete."
                    ),
                )
            )
            if consecutive_no_tool >= MAX_CONSECUTIVE_NO_TOOL:
                outcome.status = RunStatus.ERROR
                outcome.error = (
                    f"the model replied without calling a tool {consecutive_no_tool} times in a row"
                )
                break
            continue

        consecutive_no_tool = 0
        messages.append(_assistant_message(reply.thought, reply.tool_calls))

        finished = False
        for position, call in enumerate(reply.tool_calls):
            if len(outcome.steps) >= max_steps:
                outcome.status = RunStatus.MAX_STEPS
                finished = True
                break

            result = dispatch(
                sandbox,
                call.name,
                call.arguments,
                parse_error=call.parse_error,
                enabled=tools_enabled,
                command_timeout_s=command_timeout_seconds,
                cap_bytes=cap_bytes,
            )
            step = Step(
                index=len(outcome.steps),
                timestamp=datetime.now(UTC),
                thought=reply.thought if position == 0 else None,
                tool_name=call.name,
                tool_args=dict(call.arguments)
                if call.arguments
                else ({"__raw__": call.raw_arguments} if call.raw_arguments else {}),
                tool_output=result.output,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                tokens_in=reply.tokens_in if position == 0 else 0,
                tokens_out=reply.tokens_out if position == 0 else 0,
                cost_usd=reply.cost_usd if position == 0 else 0.0,
                truncated=result.truncated,
                schema_violation=result.schema_violation,
                error=result.error,
            )
            outcome.steps.append(step)
            if on_step:
                on_step(step)
            messages.append(_tool_message(call, result))

            log.debug(
                "agent.step",
                task=task.id,
                index=step.index,
                tool=step.tool_name,
                exit_code=step.exit_code,
                violation=step.schema_violation,
            )

            if result.is_finish:
                outcome.status = RunStatus.COMPLETED
                finished = True
                break

            if time.monotonic() >= deadline:
                outcome.status = RunStatus.TIMEOUT
                finished = True
                break

        if finished:
            break

    log.info(
        "agent.done",
        task=task.id,
        status=outcome.status.value,
        steps=len(outcome.steps),
        cost_usd=round(spent, 6),
    )
    return outcome


def _no_tool_step(
    index: int, thought: str | None, tokens_in: int, tokens_out: int, cost_usd: float
) -> Step:
    """Record a reply that contained no tool call at all."""
    return Step(
        index=index,
        timestamp=datetime.now(UTC),
        thought=thought,
        tool_name="(no tool call)",
        tool_args={},
        tool_output="The model replied without calling a tool.",
        exit_code=None,
        duration_ms=0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        schema_violation=True,
        error="no tool call in the model reply",
    )
