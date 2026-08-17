"""Two questions the first Gemini run raised but could not answer.

A. Why does onmi-gemini3.1 return 400?

   Its liveness call -- "say ok", max_tokens 64 -- returned 200, and the
   real call returned 400 "No target in combo onmi-gemini3.1 supports
   structured out...". Two things differed between those calls, the
   prompt and the output cap, so neither is yet the cause. Four cells
   separate them. The full error body is kept this time; 70 characters
   was not enough to read the reason, and the reason is the finding.

   This also matters because gate.yaml records a DIFFERENT failure for
   this route -- "returned nothing in 400s" -- and a recorded claim that
   has changed shape is worth correcting rather than leaving to mislead.

B. Does onmi-gemini3.6 still return empty content on security code?

   gate.yaml puts the Gemini routes last partly on that claim. The first
   run does not test it: chain-a touches review-pipeline internals, not
   security code, so two VALID results there say nothing about the
   recorded failure. A conditional claim can only be refuted by an
   experiment that meets its condition.

   a3abdf3 is the condition. It is the commit that added header
   validation to forge -- PROTECTED_HEADER_KEYS, RFC 7230 field regexes,
   credential shielding in repr, and a documented request-smuggling
   primitive -- 4 files, +1360/-29, and 94 matches for inject / smuggle /
   credential / secret / auth / protected / CRLF.

   The suspected mechanism (OmniRoute memory
   feedback_omniroute_flash_empty_content) is a safety filter:
   finish_reason SAFETY maps to content_filter, which the gateway's
   isEmptyContentResponse does not exempt. So finish_reason is recorded
   on every empty result -- without it, "empty" cannot be told apart
   from a truncation or a refusal.
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

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def call(model, prompt, cap, timeout_s=900):
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


def build(diff):
    post, digest = _assemble_post_image(CWD, diff, context_lines=40)
    p = "You are a " + ROLE + ". Review this diff.\n" + REVIEW_JSON_CONTRACT
    if post:
        p += "\n## Post-Image (current file content)\n" + post + "\n"
    if digest:
        p += "\n## Conventions Digest\n" + digest + "\n"
    return p + annotated_diff_prompt_block(diff)


def gitdiff(*args):
    d = subprocess.run(["git", "-C", str(CWD), *args],
                       capture_output=True, text=True).stdout
    assert d.strip(), "empty diff from %s" % (args,)
    return d


CHAIN_A = build(gitdiff("diff", "c72ff06", "chain-a-rebuild"))
SECURITY = build(gitdiff("show", "a3abdf3"))

print("=" * 78)
print("A. onmi-gemini3.1: which variable causes the 400?")
print("=" * 78)
print("%-34s %6s %8s  %s" % ("cell", "code", "wall", "body"))
CELLS = [
    ("short prompt, cap 64", "say ok", 64),
    ("short prompt, cap 65536", "say ok", 65536),
    ("long prompt, cap 4096", CHAIN_A, 4096),
    ("long prompt, cap 65536", CHAIN_A, 65536),
]
for label, prompt, cap in CELLS:
    code, raw, el = call("onmi-gemini3.1", prompt, cap, 300)
    try:
        d = json.loads(raw)
        note = json.dumps(d["error"]) if "error" in d else "OK (content returned)"
    except Exception:
        note = raw[:160].replace("\n", " ")
    print("%-34s %6s %7.1fs  %s" % (label, code, el, note[:190]))
    time.sleep(15)

print()
print("=" * 78)
print("B. Security-adjacent code (a3abdf3): does the Flash go empty?")
print("=" * 78)
print("chain-a prompt   %7d bytes  (the first run's, for reference)"
      % len(CHAIN_A))
print("security prompt  %7d bytes" % len(SECURITY))
print()
print("%-28s %5s %6s %8s %8s %8s %-10s %s"
      % ("model / run", "code", "wall", "comp", "content", "finish",
         "valid", "note"))

ARMS = [("onmi-gemini3.6", 1), ("onmi-gemini3.6", 2),
        ("agy/gemini-3.1-pro-high", 1)]
for model, run in ARMS:
    time.sleep(20)
    code, raw, el = call(model, SECURITY, 65536)
    valid, note, comp, clen, fin = "-", "", None, 0, "-"
    try:
        d = json.loads(raw)
    except Exception:
        valid, note = "UNPARSED", raw[:60].replace("\n", " ")
        d = None
    if d is not None:
        if "error" in d:
            valid, note = "GATEWAY ERROR", json.dumps(d["error"])[:120]
        else:
            ch = (d.get("choices") or [{}])[0]
            m = ch.get("message") or {}
            text = m.get("content") or ""
            comp = (d.get("usage") or {}).get("completion_tokens")
            clen, fin = len(text), ch.get("finish_reason")
            if not text.strip():
                valid, note = "EMPTY", "finish_reason=%s" % fin
            else:
                tag = model.replace("/", "_")
                (OUT / ("gm_sec_%s_r%d.txt" % (tag, run))).write_text(text)
                try:
                    validate_reviewer_json(_strip_fences(text))
                    valid = "VALID"
                except Exception as exc:
                    valid = "INVALID"
                    note = ("%s: %s" % (type(exc).__name__, exc))[:60]
    print("%-28s %5s %5.1fs %8s %8s %8s %-10s %s"
          % ("%s r%d" % (model, run), code, el, comp, clen, fin, valid, note))
