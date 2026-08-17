"""Do the two Gemini routes still fail the way gate.yaml says they do?

This is a check against recorded claims, not an open survey. gate.yaml
carries a specific failure for each, and the useful outcome is either
confirming it or showing it has changed:

  onmi-gemini3.6  reaches gemini-3.6-flash-high, a Flash. Recorded
                  failure: empty content on security-adjacent code.
  onmi-gemini3.1  reaches gemini-pro-agent, not a 3.1 Pro at all.
                  Recorded failure: nothing back in 400s on an 11.8KB
                  prompt that the direct Pro answered in 104s. That claim
                  is why the gemini-pro entry below it reaches
                  agy/gemini-3.1-pro-high instead of the combo.

agy/gemini-3.1-pro-high is measured alongside because it is what gate.yaml
actually runs today; without it, a bad number from the combo has nothing
to be bad relative to.

Method carries yesterday's lessons on oc-ds-flash-free forward:

  - PRODUCTION prompt size. The same 70KB real L1 prompt every route was
    measured on yesterday, so the numbers are comparable across all four.
    A parameter or a route can behave one way on a probe and another way
    at this size; yesterday one did.
  - The DOWNSTREAM criterion, with its preprocessing. forge's api path
    runs _strip_fences before anything sees the content, so validating
    the raw body would be stricter than the pipeline and would fail a
    reply that is merely fenced.
  - n=2 per route. Yesterday the same route, same arm, returned parseable
    JSON once and unparseable JSON once. One run cannot see that, and for
    a review backend it is the property that decides usability: a bad
    parse becomes a CONFIRMED INFRA finding, which resets the cycle
    counter, so an intermittent one means a review never converges.
  - Every body saved, so a verdict can be re-checked without re-spending
    the call. Yesterday one could not be.
  - Liveness first, short body. It cannot see a content failure, but it
    separates a dead route from a broken one before a long run is spent.

Runs strictly serially: OmniRoute drops concurrent passes on a 15s
maxWaitMs, so parallelism here would corrupt the thing being measured.
"""
import json
import os
import pathlib
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

WT = "/home/houminxi/code/forge/.claude/worktrees/postimage-window/src"
sys.path.insert(0, WT)
from code_forge.cli import _assemble_post_image
from code_forge.diff import annotated_diff_prompt_block
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, validate_reviewer_json
from code_forge.llm_invoke import _strip_fences

URL = "https://192.168.100.10:20128/v1/chat/completions"
KEY = os.environ["OMNIROUTE_API_KEY"]
CWD = pathlib.Path("/home/houminxi/code/forge")
ROLE = "structural code reviewer: correctness and logic errors"
OUT = pathlib.Path(".")

MODELS = sys.argv[1:] or [
    "onmi-gemini3.6", "onmi-gemini3.1", "agy/gemini-3.1-pro-high",
]

diff = subprocess.run(
    ["git", "-C", str(CWD), "diff", "c72ff06", "chain-a-rebuild"],
    capture_output=True, text=True).stdout
assert diff.strip(), "empty diff -- nothing to measure"
post, digest = _assemble_post_image(CWD, diff, context_lines=40)

PROMPT = "You are a " + ROLE + ". Review this diff.\n" + REVIEW_JSON_CONTRACT
if post:
    PROMPT += "\n## Post-Image (current file content)\n" + post + "\n"
if digest:
    PROMPT += "\n## Conventions Digest\n" + digest + "\n"
PROMPT += annotated_diff_prompt_block(diff)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def call(model, prompt, cap, timeout_s):
    body = json.dumps({
        "model": model, "stream": False, "max_tokens": cap,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return "ERR", "%s: %s" % (type(e).__name__, e), time.time() - t0


def measure(model, run, cap=65536, timeout_s=900):
    code, raw, el = call(model, PROMPT, cap, timeout_s)
    r = {"code": code, "wall": el, "comp": None, "reason": 0,
         "content": 0, "valid": "-", "note": ""}
    if code == "ERR":
        r["valid"] = "NO REPLY"
        r["note"] = raw[:70]
        return r
    try:
        d = json.loads(raw)
    except Exception:
        r["valid"] = "UNPARSED ENVELOPE"
        r["note"] = raw[:70].replace("\n", " ")
        return r
    if "error" in d:
        r["valid"] = "GATEWAY ERROR"
        r["note"] = json.dumps(d["error"])[:70]
        return r
    u = d.get("usage") or {}
    ch = (d.get("choices") or [{}])[0]
    m = ch.get("message") or {}
    text = m.get("content") or ""
    r["comp"] = u.get("completion_tokens")
    r["reason"] = len(m.get("reasoning_content") or "")
    r["content"] = len(text)
    r["finish"] = ch.get("finish_reason")
    if not text.strip():
        r["valid"] = "EMPTY"
        r["note"] = "finish_reason=%s" % ch.get("finish_reason")
        return r
    tag = model.replace("/", "_")
    (OUT / ("gm_body_%s_r%d.txt" % (tag, run))).write_text(text)
    try:
        validate_reviewer_json(_strip_fences(text))
        r["valid"] = "VALID"
    except Exception as exc:
        r["valid"] = "INVALID"
        r["note"] = ("%s: %s" % (type(exc).__name__, exc))[:70]
    return r


print("prompt=%d bytes (real L1, same as yesterday's runs)  max_tokens=65536"
      % len(PROMPT))
print()

RUNS = 2
for model in MODELS:
    code, raw, el = call(model, "say ok", 64, 120)
    live = "200" if code == 200 else "%s %s" % (code, raw[:60].replace("\n", " "))
    print("=== %s" % model)
    print("    liveness (short): %s in %.1fs" % (live, el))
    if code != 200:
        print("    skipping the long runs: a route that cannot answer a")
        print("    two-word prompt tells us nothing about a 70KB one.")
        print()
        continue
    print("    %-5s %6s %9s %8s %9s %8s %-10s %s"
          % ("run", "code", "wall", "comp", "reason", "content", "valid", "note"))
    for i in range(1, RUNS + 1):
        time.sleep(20)
        r = measure(model, i)
        print("    %-5d %6s %8.1fs %8s %9s %8s %-10s %s"
              % (i, r["code"], r["wall"], r.get("comp"), r.get("reason"),
                 r.get("content"), r.get("valid"), r.get("note", "")))
    print()
