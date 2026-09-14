"""The provider layer is where a model's mistakes become data, so it gets tested hard."""

import json
from types import SimpleNamespace

import pytest
from trajectory_core.testing import make_playbook
from trajectory_runner.policies import build_script
from trajectory_runner.providers import (
    ChatMessage,
    LiteLLMProvider,
    ModelReply,
    PlannedCall,
    Provider,
    ProviderError,
    StubProvider,
    ToolSpec,
    _is_retryable,
)


def _messages(n: int = 3) -> list[ChatMessage]:
    return [ChatMessage(role="user", content="x" * 400) for _ in range(n)]


TOOLS = [ToolSpec(name="bash", description="run a command", parameters={"type": "object"})]


class TestWireStructures:
    def test_chat_message_omits_empty_fields(self):
        assert ChatMessage(role="user", content="hi").to_wire() == {"role": "user", "content": "hi"}

    def test_chat_message_carries_tool_results(self):
        wire = ChatMessage(role="tool", content="ok", tool_call_id="c1", name="bash").to_wire()
        assert wire == {"role": "tool", "content": "ok", "tool_call_id": "c1", "name": "bash"}

    def test_tool_spec_renders_a_function_definition(self):
        wire = TOOLS[0].to_wire()
        assert wire["type"] == "function"
        assert wire["function"]["name"] == "bash"

    def test_model_reply_knows_whether_it_asked_for_a_tool(self):
        assert ModelReply(thought="done", tool_calls=[]).has_tool_call is False
        planned = PlannedCall(name="bash", arguments={"command": "ls"})
        assert ModelReply(thought=None, tool_calls=[planned.to_tool_call(0)]).has_tool_call is True


class TestStubProvider:
    def test_satisfies_the_provider_protocol(self):
        assert isinstance(StubProvider("stub:methodical", []), Provider)

    def test_emits_the_script_in_order(self):
        script = [
            PlannedCall(name="bash", arguments={"command": "ls"}, thought="look around"),
            PlannedCall(name="finish", arguments={"summary": "done"}, thought="done"),
        ]
        provider = StubProvider("stub:methodical", script)
        first = provider.reply(_messages(), TOOLS)
        second = provider.reply(_messages(), TOOLS)
        assert first.tool_calls[0].name == "bash"
        assert first.thought == "look around"
        assert second.tool_calls[0].name == "finish"
        assert provider.exhausted is True

    def test_emits_finish_once_the_script_runs_out(self):
        """A scripted run must terminate even when the environment surprises the script."""
        provider = StubProvider("stub:methodical", [])
        reply = provider.reply(_messages(), TOOLS)
        assert reply.tool_calls[0].name == "finish"
        assert reply.tool_calls[0].arguments["summary"] == "Script exhausted."

    def test_preserves_a_deliberate_parse_error(self):
        script = [
            PlannedCall(
                name="grep_files",
                arguments={},
                raw_arguments='{"pattern": ',
                parse_error="tool arguments are not valid JSON",
            )
        ]
        call = StubProvider("stub:sloppy", script).reply(_messages(), TOOLS).tool_calls[0]
        assert call.parse_error is not None
        assert call.raw_arguments == '{"pattern": '

    def test_costs_nothing_by_default(self):
        provider = StubProvider("stub:methodical", [PlannedCall(name="finish")])
        assert provider.estimate_cost(_messages()) == 0.0
        assert provider.reply(_messages(), TOOLS).cost_usd == 0.0

    def test_synthetic_price_exercises_cost_accounting(self):
        provider = StubProvider(
            "stub:pricey", [PlannedCall(name="finish")], price_per_1k_tokens=3.0
        )
        estimate = provider.estimate_cost(_messages())
        assert estimate > 0
        assert provider.reply(_messages(), TOOLS).cost_usd > 0

    def test_token_count_grows_with_the_conversation(self):
        provider = StubProvider("stub:methodical", [])
        assert provider.count_tokens(_messages(1)) < provider.count_tokens(_messages(8))

    def test_remaining_counts_down(self):
        provider = StubProvider("stub:x", [PlannedCall(name="finish")] * 3)
        assert provider.remaining == 3
        provider.reply(_messages(), TOOLS)
        assert provider.remaining == 2

    def test_is_deterministic_for_a_given_policy_and_seed(self):
        playbook = make_playbook()
        left = [c.name for c in build_script("sloppy", playbook, seed=7)]
        right = [c.name for c in build_script("sloppy", playbook, seed=7)]
        assert left == right


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


def _fake_response(
    *, content: str | None, calls: list[_FakeCall], prompt: int = 120, completion: int = 30
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=calls)
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeLiteLLM:
    """Minimal stand in for the LiteLLM module, so parsing is tested without a network."""

    drop_params = False
    suppress_debug_info = False

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls = 0
        self.last_kwargs: dict | None = None

    def completion(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response

    def completion_cost(self, completion_response=None):
        return 0.0042

    def token_counter(self, model=None, messages=None):
        return 999

    def cost_per_token(self, model=None, prompt_tokens=0, completion_tokens=0):
        return (0.001, 0.002)

    def get_model_info(self, model):
        return {"max_input_tokens": 200_000}


def _provider_with(fake: _FakeLiteLLM, **kwargs) -> LiteLLMProvider:
    provider = LiteLLMProvider(
        "anthropic/test-model", max_attempts=kwargs.pop("max_attempts", 1), **kwargs
    )
    provider._litellm = fake
    return provider


class TestLiteLLMProvider:
    def test_satisfies_the_provider_protocol(self):
        assert isinstance(LiteLLMProvider("anthropic/test-model"), Provider)

    def test_parses_a_well_formed_tool_call(self):
        fake = _FakeLiteLLM(
            _fake_response(
                content="Let me look at the tests.",
                calls=[_FakeCall("c1", "bash", json.dumps({"command": "pytest -q"}))],
            )
        )
        reply = _provider_with(fake).reply(_messages(), TOOLS)
        assert reply.thought == "Let me look at the tests."
        assert reply.tool_calls[0].name == "bash"
        assert reply.tool_calls[0].arguments == {"command": "pytest -q"}
        assert reply.tool_calls[0].parse_error is None
        assert (reply.tokens_in, reply.tokens_out) == (120, 30)
        assert reply.cost_usd == pytest.approx(0.0042)

    def test_malformed_json_arguments_become_data_not_an_exception(self):
        """A model emitting broken JSON is a measured failure mode, never a crash."""
        fake = _FakeLiteLLM(
            _fake_response(content=None, calls=[_FakeCall("c1", "bash", '{"command": ')])
        )
        call = _provider_with(fake).reply(_messages(), TOOLS).tool_calls[0]
        assert call.parse_error is not None
        assert call.raw_arguments == '{"command": '
        assert call.arguments == {}

    def test_non_object_arguments_are_rejected_with_a_readable_message(self):
        fake = _FakeLiteLLM(_fake_response(content=None, calls=[_FakeCall("c1", "bash", '"ls"')]))
        call = _provider_with(fake).reply(_messages(), TOOLS).tool_calls[0]
        assert call.parse_error is not None
        assert "JSON object" in call.parse_error

    def test_a_reply_with_no_tool_calls_is_valid(self):
        fake = _FakeLiteLLM(_fake_response(content="I think I am finished.", calls=[]))
        reply = _provider_with(fake).reply(_messages(), TOOLS)
        assert reply.has_tool_call is False
        assert reply.thought == "I think I am finished."

    def test_seed_and_api_base_are_forwarded_when_set(self):
        fake = _FakeLiteLLM(_fake_response(content=None, calls=[]))
        provider = _provider_with(fake, seed=11, api_base="http://localhost:11434")
        provider.reply(_messages(), TOOLS)
        assert fake.last_kwargs is not None
        assert fake.last_kwargs["seed"] == 11
        assert fake.last_kwargs["api_base"] == "http://localhost:11434"

    def test_seed_is_omitted_when_unset(self):
        fake = _FakeLiteLLM(_fake_response(content=None, calls=[]))
        _provider_with(fake).reply(_messages(), TOOLS)
        assert fake.last_kwargs is not None
        assert "seed" not in fake.last_kwargs

    def test_retries_a_transient_failure_then_gives_up(self, monkeypatch):
        monkeypatch.setattr("trajectory_runner.providers.time.sleep", lambda _s: None)
        fake = _FakeLiteLLM(error=RuntimeError("rate limit exceeded, please retry"))
        provider = _provider_with(fake, max_attempts=3)
        with pytest.raises(ProviderError, match="failed after 3 attempt"):
            provider.reply(_messages(), TOOLS)
        assert fake.calls == 3

    def test_does_not_retry_a_deterministic_failure(self, monkeypatch):
        """A bad request retried four times is four identical bad requests."""
        monkeypatch.setattr("trajectory_runner.providers.time.sleep", lambda _s: None)
        fake = _FakeLiteLLM(error=ValueError("model does not support tool use"))
        provider = _provider_with(fake, max_attempts=4)
        with pytest.raises(ProviderError):
            provider.reply(_messages(), TOOLS)
        assert fake.calls == 1

    def test_context_window_comes_from_the_registry(self):
        assert _provider_with(_FakeLiteLLM()).context_window() == 200_000

    def test_context_window_falls_back_when_the_model_is_unknown(self):
        class Broken(_FakeLiteLLM):
            def get_model_info(self, model):
                raise KeyError(model)

        assert _provider_with(Broken()).context_window() == 128_000

    def test_token_count_falls_back_to_a_character_heuristic(self):
        class Broken(_FakeLiteLLM):
            def token_counter(self, model=None, messages=None):
                raise RuntimeError("no tokenizer for this model")

        assert _provider_with(Broken()).count_tokens(_messages()) > 0

    def test_cost_estimate_falls_back_to_zero_for_unpriced_models(self):
        class Broken(_FakeLiteLLM):
            def cost_per_token(self, model=None, prompt_tokens=0, completion_tokens=0):
                raise KeyError("unpriced")

        assert _provider_with(Broken()).estimate_cost(_messages()) == 0.0

    def test_cost_falls_back_to_zero_when_the_response_cannot_be_priced(self):
        class Broken(_FakeLiteLLM):
            def completion_cost(self, completion_response=None):
                raise KeyError("unpriced")

        fake = Broken(_fake_response(content=None, calls=[]))
        assert _provider_with(fake).reply(_messages(), TOOLS).cost_usd == 0.0


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Rate limit reached"),
        RuntimeError("upstream connection error"),
        RuntimeError("503 service unavailable"),
        TimeoutError("timed out"),
    ],
)
def test_transient_failures_are_retryable(exc):
    assert _is_retryable(exc) is True


@pytest.mark.parametrize(
    "exc",
    [ValueError("invalid api key"), KeyError("model not found"), RuntimeError("context too long")],
)
def test_deterministic_failures_are_not_retryable(exc):
    assert _is_retryable(exc) is False
