"""Does a route actually review, or does it return well-formed nothing?

Three of four Gemini runs on the chain-a diff returned findings=[] with
full excerpt coverage. In forge's accounting that is a CLEAN PASS -- it
advances the three-consecutive-clean counter -- and no signal I had been
recording could tell it apart from a careful review. Not the HTTP code,
not the wall clock, not the token counts, not schema validation. Only
len(findings) showed it, and a zero there has two possible causes: the
diff is clean, or the reviewer is not reviewing.

Known-answer validation separates them. The same diff is sent twice:

  CLEAN     chain-a as it is.
  INJECTED  chain-a plus one hunk carrying two defects from forge's own
            canary template list, a hardcoded AWS key and a string-built
            SQL query. Both are unambiguous, both are the kind of thing
            the templates exist to catch, and neither is subtle enough
            for a miss to be a judgement call.

A route that reports nothing on INJECTED is not reviewing. A route that
reports the injected defects on INJECTED and nothing on CLEAN is behaving
exactly as it should, and its clean verdict then carries information.

The positive control is the point. sn-deepseek-flash is measured on the
same two prompts because a null result everywhere would mean the
injection is too weak to detect, not that the routes are blind -- and
those two readings are indistinguishable without a route known to review.
That route produced 19033 characters of findings on this diff yesterday.

Runs serially; OmniRoute drops concurrent passes on a 15s maxWaitMs.
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
INJECTED_FILE = "src/code_forge/s3_sync.py"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# A new-file hunk, so the post-image assembler has nothing on disk to
# contradict it and the reviewer sees exactly these lines as added.
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


PROMPTS = {"CLEAN": build(base), "INJECTED": build(base + INJECT)}


def ask(model, prompt, cap, tag):
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
        with urllib.request.urlopen(req, timeout=900, context=ctx) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"code": "ERR", "wall": time.time() - t0, "note": str(e)[:60],
                "n": "-", "hit": "-"}
    el = time.time() - t0
    r = {"code": code, "wall": el, "n": "-", "hit": "-", "note": ""}
    try:
        d = json.loads(raw)
    except Exception:
        r["note"] = raw[:60].replace("\n", " ")
        return r
    if "error" in d:
        r["note"] = json.dumps(d["error"])[:70]
        return r
    m = ((d.get("choices") or [{}])[0].get("message") or {})
    text = m.get("content") or ""
    if not text.strip():
        r["note"] = "EMPTY"
        return r
    (OUT / ("canary_%s.txt" % tag)).write_text(text)
    try:
        v = validate_reviewer_json(_strip_fences(text))
    except Exception as exc:
        r["note"] = "INVALID: %s" % str(exc)[:50]
        return r
    fi = v.get("findings", [])
    r["n"] = len(fi)
    # Did it name the injected file at all? That is the whole question --
    # a defect it never mentions is one it did not find, whatever else
    # the reply says.
    r["hit"] = sum(1 for f in fi if INJECTED_FILE in (f.get("file") or ""))
    return r


# onmi-gemini3.1 carries its own cap: measured today, that combo returns
# 400 capability_mismatch at 65536 whatever the prompt is, and 200 at
# 4096. Sending it the cap gate.yaml uses would test the gateway's
# rejection rather than the model's reviewing.
MODELS = [("sn-deepseek-flash", 65536), ("onmi-gemini3.6", 65536),
          ("onmi-gemini3.1", 4096), ("agy/gemini-3.1-pro-high", 65536)]
if sys.argv[1:]:
    MODELS = [(m, 4096 if m == "onmi-gemini3.1" else 65536)
              for m in sys.argv[1:]]

print("CLEAN prompt    %7d bytes" % len(PROMPTS["CLEAN"]))
print("INJECTED prompt %7d bytes  (+ a new file with an AWS key and a"
      " string-built SQL query)" % len(PROMPTS["INJECTED"]))
print()
print("%-26s %-9s %5s %6s %8s %8s %9s  %s"
      % ("model", "diff", "cap", "code", "wall", "findings", "on-inject",
         "note"))
print("-" * 100)
for model, cap in MODELS:
    for which in ("CLEAN", "INJECTED"):
        tag = "%s_%s" % (model.replace("/", "_"), which)
        r = ask(model, PROMPTS[which], cap, tag)
        print("%-26s %-9s %5d %6s %7.1fs %8s %9s  %s"
              % (model, which, cap, r["code"], r["wall"], r["n"], r["hit"],
                 r.get("note", "")))
        time.sleep(20)
    print()
