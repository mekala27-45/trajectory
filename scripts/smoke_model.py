#!/usr/bin/env python3
"""One model call, to prove a key and a model identifier before a matrix spends on them.

A free tier gives a fixed number of credits a month. The most expensive way to discover
that a key is missing, a model name has a typo, or a base URL is wrong is to find out on
run 400 of a matrix, after the first 399 have already been spent on a model that was never
going to answer. This makes exactly one call and says what came back.

    uv run python scripts/smoke_model.py --model nvidia_nim/deepseek-ai/deepseek-v4.1-flash

A hard wall-clock cap guards the call. A provider that accepts the connection and then
never answers would otherwise hang a CI job for its whole timeout; here the alarm fires,
the probe fails, and the matrix it gates never starts. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from types import FrameType

_TIMEOUT_SECONDS = 0  # set in main, read by the alarm handler


def _hard_timeout(signum: int, frame: FrameType | None) -> None:
    """Exit the process the instant the cap is reached.

    Raising an exception here is not enough: LiteLLM wraps the call in its own broad
    handling and would swallow it, spend the one-shot alarm, and hang for the rest of the
    job. os._exit cannot be caught, so the probe always stops on time.
    """
    sys.stderr.write(f"FAILED: no response within {_TIMEOUT_SECONDS}s\n")
    sys.stderr.flush()
    os._exit(1)


def main(argv: list[str]) -> int:
    """Make one capped call and report what the model returned."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="LiteLLM model identifier to probe.")
    parser.add_argument("--timeout", type=int, default=90, help="Hard cap in seconds.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Generous on purpose. A reasoning model spends its budget on hidden reasoning "
        "before it writes a word, so a small cap reads as an empty reply.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args(argv)

    global _TIMEOUT_SECONDS
    _TIMEOUT_SECONDS = args.timeout

    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    messages = [{"role": "user", "content": "Reply with the single word: ready."}]
    print(f"probing {args.model} (cap {args.timeout}s) ...", flush=True)

    signal.signal(signal.SIGALRM, _hard_timeout)
    signal.alarm(args.timeout)
    started = time.monotonic()
    try:
        response = litellm.completion(
            model=args.model,
            messages=messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
        )
    except Exception as exc:  # noqa: BLE001  any provider error means the probe failed
        print(f"FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)

    elapsed = round(time.monotonic() - started, 1)
    choice = response.choices[0]
    finish = getattr(choice, "finish_reason", None)
    content = (getattr(choice.message, "content", None) or "").strip()
    reasoning = (getattr(choice.message, "reasoning_content", None) or "").strip()
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    usage = getattr(response, "usage", None)
    summary = (
        f"in {elapsed}s: finish_reason={finish!r} "
        f"prompt_tokens={getattr(usage, 'prompt_tokens', 0)} "
        f"completion_tokens={getattr(usage, 'completion_tokens', 0)} "
        f"reasoning_chars={len(reasoning)} "
        f"content={content[:60]!r} tool_calls={len(tool_calls)}"
    )
    if not content and not tool_calls:
        hint = (
            " It reasoned but never answered; raise --max-tokens."
            if reasoning or finish == "length"
            else ""
        )
        print(f"FAILED: empty reply {summary}.{hint}", file=sys.stderr)
        return 1
    print(f"OK {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
