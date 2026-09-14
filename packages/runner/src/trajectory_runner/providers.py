"""Model providers.

The agent loop talks to exactly one interface, `Provider`. Two implementations exist.

`LiteLLMProvider` reaches any vendor LiteLLM supports, which is the entire point: a
harness that can only evaluate one vendor's models is not a harness, it is a vendor
report card. Anthropic, OpenAI, Google and a local Ollama server all arrive through the
same call with no branching in the loop.

`StubProvider` replays a scripted policy with no network access at all. It exists so the
whole pipeline (sandbox, tools, verifier, scorer, classifier, bundle, API, web) can be
exercised end to end in CI for free and deterministically. It is also how the published
harness validation numbers are produced without spending a cent, and every run it
produces is stamped with a `stub:` model name so it can never be mistaken for a model
result.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)

STUB_PREFIX = "stub:"

_RETRYABLE_SUBSTRINGS = (
    "rate limit",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "internal server error",
    "service unavailable",
    "502",
    "503",
    "529",
)


# ------------------------------------------------------------------ wire structures


@dataclass(slots=True)
class ChatMessage:
    """One entry in the conversation sent to the model."""

    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Render in the shape the chat completions API expects."""
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class ToolSpec:
    """A tool as advertised to the model."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        """Render as an OpenAI style function tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class ToolCall:
    """A single tool call the model asked for.

    `arguments` is the parsed object. When the model emits JSON that does not parse,
    `parse_error` is set and `raw_arguments` holds what it actually sent. That is not an
    error condition for the harness: malformed tool calls are one of the reported
    metrics, so they have to survive as data.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str | None = None
    parse_error: str | None = None


@dataclass(slots=True)
class ModelReply:
    """One assistant turn."""

    thought: str | None
    tool_calls: list[ToolCall]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None

    @property
    def has_tool_call(self) -> bool:
        """True when the model asked for at least one tool."""
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class PlannedCall:
    """One scripted tool call, with the reasoning text a replay viewer shows.

    Used by the offline policies. A planned call can deliberately carry a parse error, so
    a scripted agent can reproduce a malformed tool call exactly the way a real model
    emits one.
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    raw_arguments: str | None = None
    parse_error: str | None = None

    def to_tool_call(self, index: int) -> ToolCall:
        """Convert to the wire structure the agent loop consumes."""
        return ToolCall(
            id=f"stub-{index:03d}",
            name=self.name,
            arguments=dict(self.arguments),
            raw_arguments=self.raw_arguments,
            parse_error=self.parse_error,
        )


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a reply after exhausting retries."""


# ---------------------------------------------------------------------- interface


@runtime_checkable
class Provider(Protocol):
    """What the agent loop needs from a model."""

    model: str

    def reply(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelReply:
        """Produce the next assistant turn."""
        ...

    def count_tokens(self, messages: Sequence[ChatMessage]) -> int:
        """Estimate the prompt token count, used to decide when to compress context."""
        ...

    def estimate_cost(self, messages: Sequence[ChatMessage]) -> float:
        """Estimate what the next call will cost, used by the budget ceiling."""
        ...

    def context_window(self) -> int:
        """Usable prompt tokens for this model."""
        ...


# ------------------------------------------------------------------------ litellm


class LiteLLMProvider:
    """Any model LiteLLM can reach.

    Retries are deliberately narrow. Transient transport and rate limit failures are
    retried with exponential backoff and jitter; anything else propagates, because a
    harness that silently swallows a bad request produces trajectories that are wrong in
    a way nobody notices.
    """

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        request_timeout_s: float = 180.0,
        max_attempts: int = 4,
        api_base: str | None = None,
    ) -> None:
        """Configure the provider.

        Args:
            model: LiteLLM model identifier, for example `anthropic/claude-sonnet-4-5`.
            temperature: Sampling temperature.
            seed: Seed forwarded to providers that support it.
            request_timeout_s: Per request timeout.
            max_attempts: Total attempts including the first, for retryable failures.
            api_base: Override base URL, used for Ollama and self hosted gateways.
        """
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.request_timeout_s = request_timeout_s
        self.max_attempts = max_attempts
        self.api_base = api_base
        self._litellm: Any | None = None

    def _lib(self) -> Any:  # noqa: ANN401  litellm is untyped
        """Import LiteLLM lazily so the CLI starts fast and offline runs never touch it."""
        if self._litellm is None:
            import litellm

            litellm.drop_params = True
            litellm.suppress_debug_info = True
            self._litellm = litellm
        return self._litellm

    def context_window(self) -> int:
        """Usable prompt tokens, from LiteLLM's model registry."""
        try:
            info = self._lib().get_model_info(self.model)
            window = int(info.get("max_input_tokens") or info.get("max_tokens") or 0)
        except Exception:  # noqa: BLE001  registry misses are routine for new models
            window = 0
        return window or 128_000

    def count_tokens(self, messages: Sequence[ChatMessage]) -> int:
        """Count prompt tokens, falling back to a character heuristic."""
        wire = [m.to_wire() for m in messages]
        try:
            return int(self._lib().token_counter(model=self.model, messages=wire))
        except Exception:  # noqa: BLE001  unknown models have no tokenizer
            chars = sum(len(json.dumps(m)) for m in wire)
            return chars // 4

    def estimate_cost(self, messages: Sequence[ChatMessage]) -> float:
        """Estimate the cost of the next call, assuming a 1000 token completion."""
        prompt_tokens = self.count_tokens(messages)
        try:
            prompt_cost, completion_cost = self._lib().cost_per_token(
                model=self.model, prompt_tokens=prompt_tokens, completion_tokens=1000
            )
            return float(prompt_cost + completion_cost)
        except Exception:  # noqa: BLE001  unpriced models exist, do not block on them
            return 0.0

    def reply(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelReply:
        """Call the model, with bounded retries on transient failures."""
        litellm = self._lib()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_wire() for m in messages],
            "tools": [t.to_wire() for t in tools],
            "tool_choice": "auto",
            "temperature": self.temperature,
            "timeout": self.request_timeout_s,
        }
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self.api_base:
            kwargs["api_base"] = self.api_base

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = litellm.completion(**kwargs)
                return self._parse(response)
            except Exception as exc:  # noqa: BLE001  provider exception types vary widely
                last_error = exc
                if attempt == self.max_attempts or not _is_retryable(exc):
                    break
                delay = min(2.0 ** (attempt - 1), 16.0) * (0.5 + random.random())  # noqa: S311
                log.warning(
                    "provider.retry",
                    model=self.model,
                    attempt=attempt,
                    delay_s=round(delay, 2),
                    error=str(exc)[:200],
                )
                time.sleep(delay)

        raise ProviderError(
            f"{self.model} failed after {self.max_attempts} attempt(s): {last_error}"
        ) from last_error

    def _parse(self, response: Any) -> ModelReply:  # noqa: ANN401  litellm response is untyped
        """Turn a LiteLLM response into a `ModelReply`."""
        choice = response.choices[0]
        message = choice.message
        raw_calls = getattr(message, "tool_calls", None) or []

        calls: list[ToolCall] = []
        for raw in raw_calls:
            fn = raw.function
            arguments: dict[str, Any] = {}
            parse_error: str | None = None
            raw_args = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments)
            try:
                parsed = json.loads(raw_args) if raw_args else {}
                if isinstance(parsed, dict):
                    arguments = parsed
                else:
                    parse_error = (
                        f"tool arguments must be a JSON object, got {type(parsed).__name__}"
                    )
            except json.JSONDecodeError as exc:
                parse_error = f"tool arguments are not valid JSON: {exc}"
            calls.append(
                ToolCall(
                    id=str(raw.id),
                    name=str(fn.name),
                    arguments=arguments,
                    raw_arguments=raw_args,
                    parse_error=parse_error,
                )
            )

        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)

        try:
            cost = float(self._lib().completion_cost(completion_response=response))
        except Exception:  # noqa: BLE001  unpriced or local models return nothing usable
            cost = 0.0

        return ModelReply(
            thought=(message.content or None),
            tool_calls=calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            finish_reason=getattr(choice, "finish_reason", None),
        )


def _is_retryable(exc: Exception) -> bool:
    """Decide whether a provider exception is worth another attempt."""
    name = type(exc).__name__.lower()
    if any(
        token in name
        for token in (
            "ratelimit",
            "timeout",
            "apiconnection",
            "serviceunavailable",
            "internalserver",
        )
    ):
        return True
    text = str(exc).lower()
    return any(token in text for token in _RETRYABLE_SUBSTRINGS)


# --------------------------------------------------------------------------- stub


class StubProvider:
    """Replays a scripted trajectory with no network access of any kind.

    The script is fixed before the run starts, so the same seed and the same playbook
    give byte identical trajectories on any machine. When the script runs out the provider
    emits a finish call rather than raising, so a scripted run always terminates cleanly
    even if the environment made a step behave differently than the script expected.

    Cost is zero unless a synthetic price is supplied, which only the unit tests do. The
    published harness validation numbers therefore report a true zero for spend, rather
    than an invented price that would look like a measurement.
    """

    def __init__(
        self,
        model: str,
        script: Sequence[PlannedCall],
        *,
        price_per_1k_tokens: float = 0.0,
        context_window_tokens: int = 128_000,
    ) -> None:
        """Configure the scripted provider.

        Args:
            model: Model identifier to record on the run, conventionally `stub:<policy>`.
            script: Planned calls to emit, in order.
            price_per_1k_tokens: Synthetic price, used only to exercise budget accounting.
            context_window_tokens: Window reported to the agent loop.
        """
        self.model = model
        self._script = list(script)
        self._cursor = 0
        self._price_per_1k = price_per_1k_tokens
        self._context_window = context_window_tokens

    @property
    def exhausted(self) -> bool:
        """True once every planned call has been emitted."""
        return self._cursor >= len(self._script)

    @property
    def remaining(self) -> int:
        """Planned calls still to emit."""
        return max(0, len(self._script) - self._cursor)

    def context_window(self) -> int:
        """Usable prompt tokens."""
        return self._context_window

    def count_tokens(self, messages: Sequence[ChatMessage]) -> int:
        """Approximate the prompt size without loading a tokenizer.

        Four characters per token is crude, and it is the right kind of crude here: the
        number is only used to decide when to compress context, and a scripted provider
        that pulled in a real tokenizer would make the offline path slow to start for no
        gain in fidelity.
        """
        return sum(len(json.dumps(m.to_wire())) for m in messages) // 4

    def estimate_cost(self, messages: Sequence[ChatMessage]) -> float:
        """Cost of the next call under the synthetic price."""
        if self._price_per_1k <= 0:
            return 0.0
        return round(self.count_tokens(messages) / 1000.0 * self._price_per_1k, 8)

    def reply(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelReply:
        """Emit the next planned call."""
        del tools
        tokens_in = self.count_tokens(messages)
        if self.exhausted:
            call = PlannedCall(
                name="finish",
                arguments={"summary": "Script exhausted."},
                thought="I have run out of planned actions.",
            ).to_tool_call(self._cursor)
            planned_thought = "I have run out of planned actions."
        else:
            planned = self._script[self._cursor]
            call = planned.to_tool_call(self._cursor)
            planned_thought = planned.thought
        self._cursor += 1

        tokens_out = max(1, len(json.dumps(call.arguments)) // 4 + len(planned_thought) // 4)
        cost = (
            round((tokens_in + tokens_out) / 1000.0 * self._price_per_1k, 8)
            if self._price_per_1k > 0
            else 0.0
        )
        return ModelReply(
            thought=planned_thought or None,
            tool_calls=[call],
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            finish_reason="tool_calls",
        )
