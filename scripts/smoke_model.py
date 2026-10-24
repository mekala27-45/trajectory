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
import signal
import sys
import time
from types import FrameType


class _ProbeTimeoutError(Exception):
    """Raised by the alarm when the model has not answered in time."""


def _raise_timeout(signum: int, frame: FrameType | None) -> None:
    raise _ProbeTimeoutError()


def main(argv: list[str]) -> int:
    """Make one capped call and report what the model returned."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="LiteLLM model identifier to probe.")
    parser.add_argument("--timeout", type=int, default=90, help="Hard cap in seconds.")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args(argv)

    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    messages = [{"role": "user", "content": "Reply with the single word: ready."}]
    print(f"probing {args.model} (cap {args.timeout}s) ...", flush=True)

    signal.signal(signal.SIGALRM, _raise_timeout)
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
    except _ProbeTimeoutError:
        print(f"FAILED: no response within {args.timeout}s", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001  any provider error means the probe failed
        print(f"FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)

    elapsed = round(time.monotonic() - started, 1)
    choice = response.choices[0]
    content = (getattr(choice.message, "content", None) or "").strip()
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    usage = getattr(response, "usage", None)
    print(
        f"ok in {elapsed}s: finish_reason={getattr(choice, 'finish_reason', None)!r} "
        f"prompt_tokens={getattr(usage, 'prompt_tokens', 0)} "
        f"completion_tokens={getattr(usage, 'completion_tokens', 0)} "
        f"content={content[:80]!r} tool_calls={len(tool_calls)}"
    )
    if not content and not tool_calls:
        print("FAILED: the model returned neither text nor a tool call", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
