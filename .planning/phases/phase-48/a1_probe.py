#!/usr/bin/env python3
"""Phase 48 T0 diagnostic: does sn-deepseek-flash still report
finish_reason=length on its ~16384-token output clamp?

One bounded llm_invoke call against the gate.yaml default deepseek
backend with a prompt engineered to exceed the clamp. Prints exactly one
verdict line:

  A1_PROBE kind=<kind> msg_first_200=<message[:200]> chain=<cause chain>
  A1_PROBE unexpected_success output_tokens=<n>
  A1_PROBE not_run reason=<exception text>

The call runs with continuation_breaker=TruncationBreaker(threshold=1):
the first truncation event trips the breaker before any continuation
request is issued, so the probe observes the raw truncation outcome
instead of a masked recovery. kind=truncated therefore confirms that the
finish_reason=length detection fired (the detection site raised the
payload-carrying truncation error); the message text itself may come
from TruncationBreakerError rather than the raw detection site, which
the chain line disambiguates.

Never prints the API key; never prints more than 200 chars of any
message (messages can contain prompt echoes). This script makes no
source changes and is kept on disk after the phase as drift evidence.
"""
from __future__ import annotations

import sys

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import (
    LLMInvokeError,
    TruncationBreaker,
    llm_invoke,
)

# Match the gate.yaml deepseek entry. headers mirrors the entry's
# User-Agent, which the gateway's bot filtering expects.
BACKEND = BackendConfig(
    name="deepseek",
    type="api",
    format="openai",
    base_url="https://192.168.100.10:20128",
    api_key_env="OMNIROUTE_API_KEY",
    model="sn-deepseek-flash",
    max_tokens=65536,
    reasoning_effort="low",
    timeout_s=600,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    },
)

PROMPT = (
    "Return a JSON object with a 'findings' array of 1500 entries. "
    "Each entry must be an object with id, severity, file, line, and a "
    "description field of at least 150 characters of varied prose. "
    "Emit nothing but the JSON."
)


def _chain_text(exc: BaseException) -> str:
    """Bounded cause/context chain, type + first 200 chars per link."""
    parts = []
    link = exc.__cause__ or exc.__context__
    depth = 0
    while link is not None and depth < 3:
        parts.append(
            "%s: %r" % (type(link).__name__, str(link)[:200])
        )
        link = link.__cause__ or link.__context__
        depth += 1
    return " <- ".join(parts)


def main() -> None:
    # Threshold 1: the first truncation event trips the breaker, so the
    # probe sees the raw truncation outcome and issues no continuation.
    breaker = TruncationBreaker(threshold=1)
    try:
        result = llm_invoke(
            PROMPT, backend=BACKEND, max_attempts=1, timeout_s=600,
            continuation_breaker=breaker,
        )
    except LLMInvokeError as exc:
        print(
            "A1_PROBE kind=%s msg_first_200=%r chain=%r"
            % (exc.kind, str(exc)[:200], _chain_text(exc))
        )
        return
    except Exception as exc:  # noqa: BLE001 -- verdict line is the whole point
        print("A1_PROBE not_run reason=%r" % str(exc)[:200])
        return
    print(
        "A1_PROBE unexpected_success output_tokens=%d"
        % result.usage.output_tokens
    )


if __name__ == "__main__":
    sys.exit(main())
