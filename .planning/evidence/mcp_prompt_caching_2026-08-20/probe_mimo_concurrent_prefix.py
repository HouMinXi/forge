#!/usr/bin/env python3
"""Concurrent identical-prefix probe: does mimo dedupe in-flight prefills?

Three truly-concurrent requests sharing one FRESH (never-sent) prefix.
If the backend dedupes in-flight prefills, calls 2-3 report
cached_tokens>0 and finish fast. If not, all three miss (cached=0),
and prefix caching can only help sequential callers -- which forge's
concurrent pass design cannot exploit.

Rounds (per surface):
  C1..C3: concurrent, fresh shared prefix, different tails
  S1:     sequential after the concurrent batch (was anything planted?)
  S2:     sequential again (confirm cache works at all on this surface)

Surfaces: anthropic (/anthropic/v1/messages) and openai (/v1/chat/
completions), same account, same model.
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API_KEY = os.environ.get("MIMO_PRO_API_KEY")
if not API_KEY:
    sys.exit("MIMO_PRO_API_KEY not set")

MODEL = "mimo-v2.5-pro"

# Fresh filler -- deliberately different text from earlier probes so
# nothing is pre-cached.
PREFIX = (
    "Concurrent prefix-cache probe, fresh corpus 2026-08-20b. "
    "The text below is inert padding representing review context.\n\n"
    + "alpha-beta-gamma-delta-epsilon-zeta-eta-theta padding line. " * 380
    + "\n\n"
)

TAILS = [
    "Answer with the single word: alpha",
    "Answer with the single word: bravo",
    "Answer with the single word: charlie",
]


def call_anthropic(tag: str, tail: str) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 32,
        "messages": [{"role": "user", "content": PREFIX + tail}],
    }
    req = urllib.request.Request(
        "https://api.xiaomimimo.com/anthropic/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            parsed = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        return {"tag": tag, "surface": "anthropic", "error": repr(exc)}
    u = parsed.get("usage", {})
    return {
        "tag": tag,
        "surface": "anthropic",
        "elapsed_s": round(time.monotonic() - t0, 3),
        "input_tokens": u.get("input_tokens"),
        "cache_read": u.get("cache_read_input_tokens"),
        "cache_creation": u.get("cache_creation_input_tokens"),
    }


def call_openai(tag: str, tail: str) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 32,
        "stream": False,
        "messages": [{"role": "user", "content": PREFIX + tail}],
    }
    req = urllib.request.Request(
        "https://api.xiaomimimo.com/v1/chat/completions",
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
    except Exception as exc:  # noqa: BLE001
        return {"tag": tag, "surface": "openai", "error": repr(exc)}
    u = parsed.get("usage", {}) or {}
    return {
        "tag": tag,
        "surface": "openai",
        "elapsed_s": round(time.monotonic() - t0, 3),
        "input_tokens": u.get("prompt_tokens"),
        "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
    }


def run_surface(surface: str) -> list:
    fn = call_anthropic if surface == "anthropic" else call_openai
    results = []
    # Concurrent batch: 3 in flight simultaneously.
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [
            ex.submit(fn, "C%d" % (i + 1), tail) for i, tail in enumerate(TAILS)
        ]
        for f in futs:
            r = f.result()
            results.append(r)
            print(json.dumps(r))
    # Sequential follow-ups on the same prefix.
    for tag in ("S1", "S2"):
        r = fn(tag, "Answer with the single word: delta")
        results.append(r)
        print(json.dumps(r))
    return results


def main():
    all_results = []
    for surface in ("anthropic", "openai"):
        all_results.extend(run_surface(surface))
    with open("/tmp/mimo_concurrent_probe_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("done")


if __name__ == "__main__":
    main()
