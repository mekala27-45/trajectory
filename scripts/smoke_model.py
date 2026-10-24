#!/usr/bin/env python3
"""One model call, to prove a key and a model identifier before a matrix spends on them.

A free tier gives a fixed number of credits a month. The most expensive way to discover
that a key is missing, a model name has a typo, or a base URL is wrong is to find out on
run 400 of a matrix, after the first 399 have already been spent on a model that was never
going to answer. This makes exactly one call, with the same provider the matrix uses, and
says what came back.

    uv run python scripts/smoke_model.py --model nvidia_nim/deepseek-ai/deepseek-v4-flash

Exits non-zero when the call fails, so a workflow can gate the real run on it.
"""

from __future__ import annotations

import argparse
import sys

from trajectory_runner.providers import ChatMessage, LiteLLMProvider, ProviderError, ToolSpec

PING_TOOL = ToolSpec(
    name="answer",
    description="Return a one word answer.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)

MESSAGES = [
    ChatMessage(role="system", content="You are a reachability check. Be terse."),
    ChatMessage(role="user", content="Reply with the single word: ready."),
]


def main(argv: list[str]) -> int:
    """Make one call and report what the model returned."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="LiteLLM model identifier to probe.")
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args(argv)

    provider = LiteLLMProvider(args.model, temperature=args.temperature, max_attempts=2)
    print(f"probing {args.model} ...", flush=True)
    try:
        reply = provider.reply(MESSAGES, [PING_TOOL])
    except ProviderError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    answered = bool(reply.thought) or bool(reply.tool_calls)
    print(
        f"ok: finish_reason={reply.finish_reason!r} "
        f"tokens_in={reply.tokens_in} tokens_out={reply.tokens_out} "
        f"cost_usd={reply.cost_usd} "
        f"thought={(reply.thought or '')[:80]!r} tool_calls={len(reply.tool_calls)}"
    )
    if not answered:
        print("FAILED: the model returned neither text nor a tool call", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
