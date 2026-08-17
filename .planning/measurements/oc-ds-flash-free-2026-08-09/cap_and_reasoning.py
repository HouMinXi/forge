"""With the 1010 out of the way, can this route produce a review at all?

ua_confirm.py got HTTP 200 with a browser User-Agent and max_tokens 65536,
and the response was useless: completion_tokens landed on exactly 16384,
all of it reasoning, content empty. Two things could explain that and they
have different consequences.

  Q1  16384 is a hard output clamp somewhere below max_tokens. Then no
      config value forge can set buys more room, and the question becomes
      whether a review fits in 16384 total.
  Q2  The model simply reasons that long on review-shaped input. Then
      turning reasoning down leaves room for the answer.

Q1 is asked with a prompt that WANTS a long answer and barely needs
thinking -- if completion still stops dead on 16384, that number is not
about this prompt. Q2 is asked with the same review prompt as before plus
each reasoning control the OpenAI-shaped API exposes.

A note on reading these: a 200 is not a success here. The failure that
matters is content of length zero, which forge's own notes list as a
fail-open trap -- an empty review reads as "no findings".
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

URL = "https://192.168.100.10:20128/v1/chat/completions"
KEY = os.environ["OMNIROUTE_API_KEY"]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "oc-ds-flash-free"

CHROME = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, "
          "like Gecko) Chrome/131.0.0.0 Safari/537.36")

src = subprocess.run(
    ["git", "-C", "/home/houminxi/code/forge", "show",
     "c72ff06:src/code_forge/verify.py"],
    capture_output=True, text=True).stdout
REVIEW = "Review this code and reply with JSON findings:\n\n" + src

# Wants length, needs almost no thinking. If completion still stops on
# 16384 here, 16384 is not a property of the review prompt.
LONG_ANSWER = ("Write the numbers from 1 to 4000, one per line, as "
               "'N. line number N'. No commentary, no thinking, just the "
               "list. Begin immediately.")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def ask(prompt, extra, cap=65536):
    payload = {"model": MODEL, "stream": False, "max_tokens": cap,
               "messages": [{"role": "user", "content": prompt}]}
    payload.update(extra)
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY,
                 "User-Agent": CHROME},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900, context=ctx) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"label": "", "code": "ERR", "wall": time.time() - t0,
                "note": "%s: %s" % (type(e).__name__, e)}
    el = time.time() - t0
    out = {"code": code, "wall": el, "comp": None, "content": 0,
           "reason_chars": 0, "note": ""}
    try:
        d = json.loads(raw)
    except Exception:
        out["note"] = raw[:90]
        return out
    if "error" in d:
        out["note"] = json.dumps(d["error"])[:90]
        return out
    u = d.get("usage") or {}
    m = (d.get("choices") or [{}])[0].get("message") or {}
    out["comp"] = u.get("completion_tokens")
    out["content"] = len(m.get("content") or "")
    out["reason_chars"] = len(m.get("reasoning_content") or "")
    return out


CASES = [
    ("Q1 long answer, no controls", LONG_ANSWER, {}),
    ("Q2 review + effort=low", REVIEW, {"reasoning_effort": "low"}),
    ("Q2 review + enable_thinking=0", REVIEW, {"enable_thinking": False}),
    ("Q2 review + thinking=disabled", REVIEW,
     {"thinking": {"type": "disabled"}}),
]

print("model=%s  UA=Chrome 131  max_tokens=65536" % MODEL)
print("%-32s %5s %8s %8s %9s %8s"
      % ("case", "code", "wall", "comp.tok", "reasoning", "content"))
print("-" * 82)
for label, prompt, extra in CASES:
    r = ask(prompt, extra)
    print("%-32s %5s %7.1fs %8s %9s %8s  %s"
          % (label, r["code"], r["wall"], r.get("comp"),
             r.get("reason_chars"), r.get("content"), r.get("note", "")))
    time.sleep(20)
