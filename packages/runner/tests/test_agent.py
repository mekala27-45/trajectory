"""The loop has to stop for the right reason and record which one it was."""

from __future__ import annotations

import pytest

from trajectory_core.models import RunStatus, ToolName
from trajectory_runner.agent import (
    MAX_CONSECUTIVE_NO_TOOL,
    AgentOutcome,
    Budget,
    ChatMessage,
    compress,
    run_agent,
)
from trajectory_runner.providers import ModelReply, PlannedCall, StubProvider
from trajectory_runner.sandbox import LocalSandbox

ALL_TOOLS = list(ToolName)


@pytest.fixture
def sandbox(sample_task, task_dir, allow_local):
    with LocalSandbox(sample_task, task_dir) as sb:
        yield sb


def drive(task, sandbox, script, **kwargs):
    provider = kwargs.pop("provider", None) or StubProvider("stub:test", script)
    return run_agent(
        task,
        provider,
        sandbox,
        max_steps=kwargs.pop("max_steps", 20),
        timeout_seconds=kwargs.pop("timeout_seconds", 120),
        command_timeout_seconds=kwargs.pop("command_timeout_seconds", 20),
        cap_bytes=kwargs.pop("cap_bytes", 8192),
        tools_enabled=kwargs.pop("tools_enabled", ALL_TOOLS),
        **kwargs,
    )


FIX = "def add(a, b):\n    return a + b\n"


class TestTermination:
    def test_finish_completes_the_run(self, sample_task, sandbox):
        outcome = drive(
            sample_task,
            sandbox,
            [
                PlannedCall(name="bash", arguments={"command": "ls"}),
                PlannedCall(name="finish", arguments={"summary": "done"}),
            ],
        )
        assert outcome.status is RunStatus.COMPLETED
        assert [s.tool_name for s in outcome.steps] == ["bash", "finish"]

    def test_the_step_ceiling_is_enforced(self, sample_task, sandbox):
        script = [PlannedCall(name="bash", arguments={"command": "true"})] * 50
        outcome = drive(sample_task, sandbox, script, max_steps=4)
        assert outcome.status is RunStatus.MAX_STEPS
        assert len(outcome.steps) == 4

    def test_the_wall_clock_ceiling_is_enforced(self, sample_task, sandbox):
        script = [PlannedCall(name="bash", arguments={"command": "sleep 2"})] * 10
        outcome = drive(sample_task, sandbox, script, timeout_seconds=1, command_timeout_seconds=5)
        assert outcome.status is RunStatus.TIMEOUT
        assert len(outcome.steps) < 10

    def test_the_budget_ceiling_stops_the_run_before_the_call(self, sample_task, sandbox):
        """A hard stop checked before spending, not after."""
        provider = StubProvider(
            "stub:pricey",
            [PlannedCall(name="bash", arguments={"command": "true"})] * 20,
            price_per_1k_tokens=50.0,
        )
        outcome = drive(sample_task, sandbox, [], provider=provider, budget=Budget(0.05))
        assert outcome.status is RunStatus.BUDGET_EXCEEDED
        assert outcome.error is not None
        assert "ceiling" in outcome.error
        assert outcome.cost_usd <= 0.05

    def test_a_run_with_no_budget_is_not_stopped_for_cost(self, sample_task, sandbox):
        outcome = drive(
            sample_task, sandbox, [PlannedCall(name="finish", arguments={"summary": "x"})]
        )
        assert outcome.status is RunStatus.COMPLETED


class _NoToolProvider:
    """A model that keeps talking instead of acting."""

    model = "stub:chatty"

    def reply(self, messages, tools):
        del messages, tools
        return ModelReply(thought="I think the file is fine.", tool_calls=[])

    def count_tokens(self, messages):
        return 10

    def estimate_cost(self, messages):
        return 0.0

    def context_window(self):
        return 128_000


class _BrokenProvider:
    model = "stub:broken"

    def reply(self, messages, tools):
        from trajectory_runner.providers import ProviderError

        raise ProviderError("upstream is on fire")

    def count_tokens(self, messages):
        return 10

    def estimate_cost(self, messages):
        return 0.0

    def context_window(self):
        return 128_000


class TestDegenerateModels:
    def test_replies_without_a_tool_call_are_recorded_and_eventually_stop_the_run(
        self, sample_task, sandbox
    ):
        outcome = drive(sample_task, sandbox, [], provider=_NoToolProvider())
        assert outcome.status is RunStatus.ERROR
        assert len(outcome.steps) == MAX_CONSECUTIVE_NO_TOOL
        assert all(s.schema_violation for s in outcome.steps)
        assert outcome.error is not None
        assert "without calling a tool" in outcome.error

    def test_a_provider_failure_ends_the_run_with_the_reason(self, sample_task, sandbox):
        outcome = drive(sample_task, sandbox, [], provider=_BrokenProvider())
        assert outcome.status is RunStatus.ERROR
        assert outcome.error == "upstream is on fire"
        assert outcome.steps == []


class TestViolationsInTheLoop:
    def test_a_malformed_call_is_recorded_and_the_loop_continues(self, sample_task, sandbox):
        outcome = drive(
            sample_task,
            sandbox,
            [
                PlannedCall(
                    name="grep_files",
                    arguments={},
                    raw_arguments='{"pattern": ',
                    parse_error="Expecting value",
                ),
                PlannedCall(name="bash", arguments={"command": "echo recovered"}),
                PlannedCall(name="finish", arguments={"summary": "done"}),
            ],
        )
        assert outcome.status is RunStatus.COMPLETED
        assert outcome.steps[0].schema_violation is True
        assert outcome.steps[0].tool_args == {"__raw__": '{"pattern": '}
        assert "recovered" in outcome.steps[1].tool_output

    def test_the_error_message_goes_back_to_the_model(self, sample_task, sandbox):
        outcome = drive(
            sample_task,
            sandbox,
            [
                PlannedCall(name="not_a_tool", arguments={}),
                PlannedCall(name="finish", arguments={"summary": "done"}),
            ],
        )
        assert "Available tools" in outcome.steps[0].tool_output


class TestTrajectoryRecording:
    def test_steps_are_indexed_from_zero_and_in_order(self, sample_task, sandbox):
        outcome = drive(
            sample_task,
            sandbox,
            [
                PlannedCall(name="bash", arguments={"command": "echo 1"}),
                PlannedCall(name="bash", arguments={"command": "echo 2"}),
                PlannedCall(name="finish", arguments={"summary": "done"}),
            ],
        )
        assert [s.index for s in outcome.steps] == [0, 1, 2]

    def test_thoughts_are_recorded(self, sample_task, sandbox):
        outcome = drive(
            sample_task,
            sandbox,
            [PlannedCall(name="finish", arguments={"summary": "x"}, thought="I am done here.")],
        )
        assert outcome.steps[0].thought == "I am done here."

    def test_every_step_is_flushed_as_it_completes(self, sample_task, sandbox):
        """A run that dies at step 38 has to leave 38 steps behind."""
        flushed = []
        drive(
            sample_task,
            sandbox,
            [
                PlannedCall(name="bash", arguments={"command": "echo 1"}),
                PlannedCall(name="bash", arguments={"command": "echo 2"}),
                PlannedCall(name="finish", arguments={"summary": "done"}),
            ],
            on_step=flushed.append,
        )
        assert [s.index for s in flushed] == [0, 1, 2]

    def test_the_agent_can_actually_change_the_workspace(self, sample_task, sandbox):
        before = sandbox.manifest()
        drive(
            sample_task,
            sandbox,
            [
                PlannedCall(name="write_file", arguments={"path": "src/app.py", "content": FIX}),
                PlannedCall(name="finish", arguments={"summary": "fixed"}),
            ],
        )
        assert "src/app.py" in sandbox.manifest().changed_against(before)

    def test_outcome_sums_cost_across_steps(self, sample_task, sandbox):
        provider = StubProvider(
            "stub:pricey",
            [
                PlannedCall(name="bash", arguments={"command": "true"}),
                PlannedCall(name="finish", arguments={"summary": "x"}),
            ],
            price_per_1k_tokens=1.0,
        )
        outcome = drive(sample_task, sandbox, [], provider=provider)
        assert outcome.cost_usd > 0
        assert outcome.cost_usd == pytest.approx(sum(s.cost_usd for s in outcome.steps))


class TestCompression:
    def _conversation(self, tool_messages: int) -> list[ChatMessage]:
        messages = [ChatMessage(role="system", content="sys")]
        for i in range(tool_messages):
            messages.append(ChatMessage(role="assistant", content=f"turn {i}"))
            messages.append(
                ChatMessage(
                    role="tool", content=f"line one {i}\n" + "x" * 5000, tool_call_id=f"c{i}"
                )
            )
        return messages

    def test_compresses_the_oldest_tool_outputs_only(self):
        messages = self._conversation(10)
        compressed = compress(messages, keep_recent=4)
        assert compressed == 6
        tools = [m for m in messages if m.role == "tool"]
        assert all((t.content or "").startswith("[compressed:") for t in tools[:6])
        assert not any((t.content or "").startswith("[compressed:") for t in tools[6:])

    def test_keeps_the_first_line_so_the_summary_is_useful(self):
        messages = self._conversation(8)
        compress(messages, keep_recent=2)
        first_tool = next(m for m in messages if m.role == "tool")
        assert "line one 0" in (first_tool.content or "")

    def test_is_idempotent(self):
        messages = self._conversation(8)
        first = compress(messages, keep_recent=2)
        second = compress(messages, keep_recent=2)
        assert first == 6
        assert second == 0

    def test_does_nothing_on_a_short_conversation(self):
        messages = self._conversation(2)
        assert compress(messages, keep_recent=6) == 0

    def test_the_loop_flags_compression_on_the_run(self, sample_task, sandbox):
        """Compression changes results, so it is never silent."""
        script = [PlannedCall(name="bash", arguments={"command": "echo padding"})] * 12
        script.append(PlannedCall(name="finish", arguments={"summary": "done"}))
        provider = StubProvider("stub:tiny-window", script, context_window_tokens=200)
        outcome = drive(sample_task, sandbox, [], provider=provider, max_steps=20)
        assert outcome.context_compressed is True


def test_outcome_defaults_are_sane():
    outcome = AgentOutcome(status=RunStatus.ERROR)
    assert outcome.steps == []
    assert outcome.cost_usd == 0.0
    assert outcome.context_compressed is False
