#!/usr/bin/env python3
"""Probe mimo-pro OPENAI endpoint (what the office forge config uses).

The office config.yaml points mimo-pro at https://api.xiaomimimo.com/v1
with format: openai -- a different API surface from the /anthropic
endpoint the Z66 config uses. Z66's probe proved the ANTHROPIC surface
does automatic prefix caching. This probe answers whether the OPENAI
surface does too, via usage.prompt_tokens_details.cached_tokens.

Rounds:
  A1-A3: byte-identical prompt 3x  (repeat behavior)
  C1-C2: same 4K-token prefix, different final instruction
         (discriminates prefix caching from exact-request replay)
"""
import json
import os
import sys
import time
import urllib.request

API_KEY = os.environ.get("MIMO_API_KEY")
if not API_KEY:
    sys.exit("MIMO_API_KEY not set")

URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.5-pro"

PREFIX = (
    "You are reviewing a code diff. Context below is filler for a "
    "cache probe; ignore its content.\n\n"
    + "This is filler context representing a diff review payload. " * 400
    + "\n\n"
)


def call(tag: str, tail: str) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 32,
        "stream": False,
        "messages": [{"role": "user", "content": PREFIX + tail}],
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            parsed = json.loads(resp.read())
        err = None
    except Exception as exc:  # noqa: BLE001
        return {"tag": tag, "error": repr(exc)}
    elapsed = time.monotonic() - t0
    usage = parsed.get("usage", {}) or {}
    return {
        "tag": tag,
        "elapsed_s": round(elapsed, 3),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens_details": usage.get("prompt_tokens_details"),
        "finish_reason": (
            parsed.get("choices", [{}])[0].get("finish_reason")
            if parsed.get("choices") else None
        ),
        "err": err,
    }


def main():
    results = []
    for i in range(1, 4):
        r = call("A%d" % i, "Respond with exactly the single word: ok")
        results.append(r)
        print(json.dumps(r))
    results.append(call("C1", "Respond with exactly the single word: one"))
    print(json.dumps(results[-1]))
    results.append(call("C2", "Respond with exactly the single word: two"))
    print(json.dumps(results[-1]))
    with open("/tmp/mimo_openai_probe_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("done")


if __name__ == "__main__":
    main()
