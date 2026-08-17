"""Can bonsai-local review, and can it say so in JSON?

Same known-answer test the four OmniRoute routes took on 2026-08-10, run
against the local llama.cpp server so the numbers sit on one scale. The
prompts are rebuilt by the same code from the same two refs, so they are
byte-identical to the recorded run and the comparison is a comparison.

  CLEAN     git diff c72ff06 chain-a-rebuild, as it is.
  INJECTED  the same plus a new file carrying a hardcoded AWS key and a
            string-built SQL query.

sn-deepseek-flash scored 2/2 on INJECTED and 0 on CLEAN in that run and
stands as the positive control; nothing here needs to re-measure it.

What this is really asking is narrower than "is it any good". Today's
measurements said a review backend dies of JSON, not of judgement: three
Gemini routes found both planted defects and still failed the pipeline
because they could not escape quote-dense code into a parseable reply.
Q2_0 is aggressive quantisation and structured-output fidelity is the
first thing that usually goes, so the reply is judged with forge's own
downstream criterion -- _strip_fences then validate_reviewer_json -- and
the raw body is kept either way. An unparseable reply that names the
planted defects is a different finding from a reply that misses them.

Throughput is recorded because forge needs nine passes to converge, not
one. A route that reviews perfectly in forty minutes a pass is a route
that cannot gate a commit.

Small-prompt probe first, and it can only fail this: a control honoured
on a probe-sized input says nothing about a 70KB one (measured today on
oc-ds-flash-free, memory feedback_control_param_honoured_by_size). It is
here to stop a dead server from costing an hour, not to predict a pass.
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

WT = "/home/houminxi/code/forge/.claude/worktrees/postimage-window/src"
sys.path.insert(0, WT)
from code_forge.cli import _assemble_post_image
from code_forge.diff import annotated_diff_prompt_block
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, validate_reviewer_json
from code_forge.llm_invoke import _strip_fences

URL = "http://192.168.100.11:8081/v1/chat/completions"
MODEL = "Ternary-Bonsai-27B-Q2_0.gguf"
CAP = 65536
CWD = pathlib.Path("/home/houminxi/code/forge")
ROLE = "structural code reviewer: correctness and logic errors"
OUT = pathlib.Path(__file__).parent
INJECTED_FILE = "src/code_forge/s3_sync.py"

INJECT = '''diff --git a/src/code_forge/s3_sync.py b/src/code_forge/s3_sync.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/code_forge/s3_sync.py
@@ -0,0 +1,14 @@
+import boto3
+
+API_KEY = "AKIAIOSFODNN7EXAMPLE"
+SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
+
+
+def connect():
+    return boto3.client("s3", aws_access_key_id=API_KEY,
+                        aws_secret_access_key=SECRET)
+
+
+def find_run(db, name):
+    cur = db.execute("SELECT * FROM runs WHERE name = '" + name + "'")
+    return cur.fetchone()
'''

base = subprocess.run(
    ["git", "-C", str(CWD), "diff", "c72ff06", "chain-a-rebuild"],
    capture_output=True, text=True).stdout
assert base.strip(), "empty diff"


def build(diff):
    post, digest = _assemble_post_image(CWD, diff, context_lines=40)
    p = "You are a " + ROLE + ". Review this diff.\n" + REVIEW_JSON_CONTRACT
    if post:
        p += "\n## Post-Image (current file content)\n" + post + "\n"
    if digest:
        p += "\n## Conventions Digest\n" + digest + "\n"
    return p + annotated_diff_prompt_block(diff)


def ask(prompt, cap, tag, timeout):
    body = json.dumps({
        "model": MODEL, "stream": False, "max_tokens": cap,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"code": "ERR", "wall": time.time() - t0, "n": "-", "hit": "-",
                "reas": "-", "cont": "-", "tps": "-",
                "note": type(e).__name__ + ": " + str(e)[:50]}
    el = time.time() - t0
    r = {"code": code, "wall": el, "n": "-", "hit": "-", "reas": "-",
         "cont": "-", "tps": "-", "note": ""}
    (OUT / ("raw_%s.json" % tag)).write_text(raw)
    try:
        d = json.loads(raw)
    except Exception:
        r["note"] = raw[:70].replace("\n", " ")
        return r
    if "error" in d:
        r["note"] = json.dumps(d["error"])[:70]
        return r
    m = ((d.get("choices") or [{}])[0].get("message") or {})
    text = m.get("content") or ""
    reasoning = m.get("reasoning_content") or ""
    r["reas"] = len(reasoning)
    r["cont"] = len(text)
    u = d.get("usage") or {}
    comp = u.get("completion_tokens")
    if comp and el > 0:
        r["tps"] = "%.1f" % (comp / el)
    if not text.strip():
        r["note"] = "EMPTY CONTENT (finish=%s comp=%s)" % (
            (d.get("choices") or [{}])[0].get("finish_reason"), comp)
        return r
    (OUT / ("body_%s.txt" % tag)).write_text(text)
    try:
        v = validate_reviewer_json(_strip_fences(text))
    except Exception as exc:
        r["note"] = "INVALID: %s" % str(exc)[:50]
        return r
    fi = v.get("findings", [])
    r["n"] = len(fi)
    r["hit"] = sum(1 for f in fi if INJECTED_FILE in (f.get("file") or ""))
    return r


HDR = ("%-10s %5s %8s %9s %9s %8s %9s  %s"
       % ("diff", "code", "wall", "reasoning", "content", "findings",
          "on-inject", "note"))


def row(label, r):
    print("%-10s %5s %7.1fs %9s %9s %8s %9s  %s"
          % (label, r["code"], r["wall"], r["reas"], r["cont"], r["n"],
             r["hit"], r["note"]))
    sys.stdout.flush()


print("endpoint %s" % URL)
print("model    %s  cap=%d" % (MODEL, CAP))
print()

print("=== probe: is it alive and can it emit the contract at all? ===")
print("A pass here predicts nothing about the 70KB prompts below.")
print(HDR)
print("-" * 104)
probe = ("You are a " + ROLE + ". Review this diff.\n" + REVIEW_JSON_CONTRACT
         + annotated_diff_prompt_block(INJECT))
p = ask(probe, 4096, "probe", 900)
row("PROBE", p)
print("probe prompt %d bytes, tok/s %s" % (len(probe), p["tps"]))
if p["code"] == "ERR":
    print("\nserver unreachable; stopping before the expensive prompts")
    raise SystemExit(1)
print()

PROMPTS = {"CLEAN": build(base), "INJECTED": build(base + INJECT)}
print("=== known-answer canary, same prompts the OmniRoute routes took ===")
print("CLEAN prompt    %7d bytes" % len(PROMPTS["CLEAN"]))
print("INJECTED prompt %7d bytes" % len(PROMPTS["INJECTED"]))
print(HDR)
print("-" * 104)
for which in ("CLEAN", "INJECTED"):
    r = ask(PROMPTS[which], CAP, which, 3600)
    row(which, r)
    print("           tok/s %s" % r["tps"])
