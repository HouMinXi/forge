"""Can oc-ds-flash-free do a REAL forge review pass, once the 1010 is gone?

Everything measured so far used a synthetic prompt -- "review this file"
plus one source file, 27KB. forge's real L1 prompt is nearly twice that
(48172 est. tokens on the chain-a diff with a whole-file post-image), and
the failure this route has is that reasoning fills a hard 16384-token
output clamp and leaves nothing for the answer. A bigger prompt is exactly
where that comes back.

So the prompt here is assembled as cli.py:_make_subagent_spawn assembles
it, and the success criterion is forge's own validate_reviewer_json rather
than "content is non-empty". A reply of 6798 characters that does not
satisfy the contract is not a review pass; length is not the question the
pipeline asks.

Three arms, all with the browser User-Agent that ua_probe.py showed is
required to get past Cloudflare at all:

  none     the baseline that returned 0 content on the smaller prompt
  effort   reasoning_effort: low     -> forge field reasoning_effort
  disabled thinking: {type: disabled} -> forge field thinking_type

max_tokens is 16384 on purpose: measurement showed completion stops dead
on 16384 whatever max_tokens says, on two unrelated prompts, so a larger
number here would only misdescribe what the route actually does.
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
MODEL = sys.argv[1] if len(sys.argv) > 1 else "oc-ds-flash-free"
CWD = pathlib.Path("/home/houminxi/code/forge")
ROLE = "structural code reviewer: correctness and logic errors"
CHROME = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, "
          "like Gecko) Chrome/131.0.0.0 Safari/537.36")

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


def ask(extra, tag="x"):
    payload = {"model": MODEL, "stream": False, "max_tokens": 16384,
               "messages": [{"role": "user", "content": PROMPT}]}
    payload.update(extra)
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY,
                 "User-Agent": CHROME})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900, context=ctx) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"code": "ERR", "wall": time.time() - t0,
                "note": "%s: %s" % (type(e).__name__, e)}
    el = time.time() - t0
    r = {"code": code, "wall": el, "comp": None, "content": 0,
         "reason": 0, "valid": "-", "note": ""}
    try:
        d = json.loads(raw)
    except Exception:
        r["note"] = raw[:80]
        return r
    if "error" in d:
        r["note"] = json.dumps(d["error"])[:80]
        return r
    u = d.get("usage") or {}
    m = (d.get("choices") or [{}])[0].get("message") or {}
    body = m.get("content") or ""
    r["comp"] = u.get("completion_tokens")
    r["content"] = len(body)
    r["reason"] = len(m.get("reasoning_content") or "")
    r["finish"] = (d.get("choices") or [{}])[0].get("finish_reason")
    if not body.strip():
        r["valid"] = "EMPTY"
        return r
    # Kept so a verdict can be re-checked without re-spending the request.
    pathlib.Path("oc_body_%s.txt" % tag).write_text(body)
    # forge's own criterion, and its own preprocessing: the api path runs
    # _strip_fences before anything sees the content (llm_invoke.py:1113),
    # so validating the raw body would be a stricter test than the pipeline
    # applies and would fail a reply that is only wrapped in a code fence.
    try:
        validate_reviewer_json(_strip_fences(body))
        r["valid"] = "VALID"
    except Exception as exc:
        r["valid"] = "INVALID"
        r["note"] = ("%s: %s" % (type(exc).__name__, exc))[:80]
    return r


print("model=%s  prompt=%d bytes (real L1 shape)  max_tokens=16384  UA=Chrome"
      % (MODEL, len(PROMPT)))
print("%-24s %5s %8s %8s %8s %8s %8s  %s"
      % ("arm", "code", "wall", "comp", "reason", "content", "valid", "note"))
print("-" * 110)

ALL_ARMS = [
    ("none (baseline)", "none", {}),
    ("reasoning_effort=low", "effort", {"reasoning_effort": "low"}),
    ("thinking=disabled", "disabled", {"thinking": {"type": "disabled"}}),
]
# Re-run one arm by naming its tag, so a re-check of a single verdict does
# not have to re-spend the other two requests.
want = set(sys.argv[2:])
ARMS = [a for a in ALL_ARMS if not want or a[1] in want]
saved = {}
for label, tag, extra in ARMS:
    r = ask(extra, tag)
    saved[label] = r
    print("%-24s %5s %7.1fs %8s %8s %8s %8s  %s"
          % (label, r["code"], r["wall"], r.get("comp"), r.get("reason"),
             r.get("content"), r.get("valid"), r.get("note", "")))
    time.sleep(20)

print()
ok = [k for k, v in saved.items() if v.get("valid") == "VALID"]
if ok:
    print("USABLE: %s" % ", ".join(ok))
else:
    print("USABLE: none. The 1010 is fixed by the User-Agent, but this route")
    print("        still cannot return a review under the 16384 output clamp.")
