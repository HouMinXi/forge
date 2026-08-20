#!/usr/bin/env python3
"""Probe mimo-pro's raw usage JSON for anthropic-style cache fields.

Sends the SAME prompt three times in a row to the same anthropic-format
endpoint llm_invoke.py's _invoke_anthropic() uses, and prints the full
raw `usage` object every time -- not just input_tokens/output_tokens,
which is all forge's own code currently reads (llm_invoke.py:1766).

If cache_read_input_tokens / cache_creation_input_tokens show up and
change shape across calls 1->2->3, that is direct evidence the backend
does prefix caching and forge is silently discarding the field, not
that the MCP path sent a near-empty prompt. If those fields are absent
or stay zero/unset regardless of repetition, the earlier MCP result's
low input_tokens has a different cause.

No cache_control breakpoints are sent here on purpose (round 1) --
this matches what forge's real body currently looks like. A round 2
run should add cache_control blocks and compare.
"""
import json
import os
import sys
import time
import urllib.request

API_KEY = os.environ.get("MIMO_PRO_API_KEY")
if not API_KEY:
    sys.exit("MIMO_PRO_API_KEY not set (export it from pass first)")

URL = "https://api.xiaomimimo.com/anthropic/v1/messages"
MODEL = "mimo-v2.5-pro"

# A prompt sized close to a real forge L1 pass but cheap to repeat:
# padded with literal, byte-identical filler so three calls share an
# identical prefix (required for prefix caching to have any chance of
# firing at all).
FILLER = (
    "This is filler context representing a diff review payload. " * 400
)
PROMPT = (
    "You are reviewing a code diff. Context below is filler for a "
    "cache probe; ignore its content.\n\n"
    + FILLER
    + "\n\nRespond with exactly the single word: ok"
)


def call(round_num: int, use_cache_control: bool) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    if use_cache_control:
        # Anthropic prefix-caching breakpoint format.
        body["messages"] = [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
        }]
    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers=headers)
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    elapsed = time.monotonic() - t0
    parsed = json.loads(raw)
    return {
        "round": round_num,
        "cache_control_sent": use_cache_control,
        "elapsed_s": round(elapsed, 3),
        "usage_raw": parsed.get("usage", {}),
        "stop_reason": parsed.get("stop_reason"),
        "content_preview": (
            parsed.get("content", [{}])[0].get("text", "")[:60]
            if parsed.get("content") else None
        ),
    }


def main():
    results = []
    # Phase A: three identical calls, NO cache_control -- matches
    # forge's current real request shape exactly.
    for i in range(1, 4):
        r = call(i, use_cache_control=False)
        results.append(r)
        print(json.dumps(r, indent=2))
        print("---")

    # Phase B: three identical calls WITH cache_control, same prompt
    # text, to see whether the backend's cache behavior differs when
    # the breakpoint is explicit vs when forge sends the current
    # bare-string body.
    for i in range(1, 4):
        r = call(i, use_cache_control=True)
        r["round"] = "B%d" % i
        results.append(r)
        print(json.dumps(r, indent=2))
        print("---")

    out_path = "/tmp/mimo_cache_probe_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print("Wrote %s" % out_path)


if __name__ == "__main__":
    main()
