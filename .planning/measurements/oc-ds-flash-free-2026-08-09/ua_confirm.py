"""Does the proposed gate.yaml entry actually work end to end?

ua_probe.py established that the User-Agent is the 1010 discriminator: one
client, three UA strings, and only the Python default was blocked. It also
disproved a claim I had written into gate.yaml -- that max_tokens 16384 is
enough. Both browser-UA cases got PAST Cloudflare and then died at 502
"reasoning consumed", because this model spends the budget on reasoning
before it answers and a real review prompt needs a real answer, not the
18-character reply the earlier 16000-cap measurement produced.

So this run tests the CONFIG, not the mechanism: browser UA plus the same
65536 the paid deepseek entry carries. Full body is kept this time -- the
previous run truncated it to 60 characters and the token accounting was the
part worth reading.
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
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 65536

CHROME = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, "
          "like Gecko) Chrome/131.0.0.0 Safari/537.36")

src = subprocess.run(
    ["git", "-C", "/home/houminxi/code/forge", "show",
     "c72ff06:src/code_forge/verify.py"],
    capture_output=True, text=True).stdout
assert len(src) > 20000, "source too short: %d bytes" % len(src)
PROMPT = "Review this code and reply with JSON findings:\n\n" + src

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

hdrs = {"Content-Type": "application/json",
        "Authorization": "Bearer " + KEY,
        "User-Agent": CHROME}
body = json.dumps({
    "model": MODEL, "stream": False, "max_tokens": CAP,
    "messages": [{"role": "user", "content": PROMPT}],
}).encode()

print("model=%s  cap=%d  prompt=%d bytes  UA=Chrome 131"
      % (MODEL, CAP, len(PROMPT)))
req = urllib.request.Request(URL, data=body, headers=hdrs, method="POST")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=900, context=ctx) as r:
        code, raw = r.status, r.read().decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    code, raw = e.code, e.read().decode("utf-8", "replace")
except Exception as e:
    code, raw = "ERR", "%s: %s" % (type(e).__name__, e)
el = time.time() - t0

print("HTTP %s in %.1fs" % (code, el))
try:
    d = json.loads(raw)
except Exception:
    print("body (unparsed):", raw[:1500])
    sys.exit(1)

if "error" in d:
    print("ERROR body:", json.dumps(d["error"])[:1200])
    sys.exit(1)

u = d.get("usage") or {}
msg = (d.get("choices") or [{}])[0].get("message") or {}
content = msg.get("content") or ""
reasoning = msg.get("reasoning_content") or ""
print("usage:", json.dumps(u))
print("reasoning chars: %d   content chars: %d" % (len(reasoning), len(content)))
print("content head:", content[:300].replace("\n", " "))
