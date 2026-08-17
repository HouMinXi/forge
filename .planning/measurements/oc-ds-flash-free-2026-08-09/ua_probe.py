"""Is the 1010 explained by the User-Agent alone?

The incident record (OmniRoute memory incident_oc_ds_flash_free_1010) says
the gateway forwards the caller's User-Agent upstream, where Cloudflare
reads it as a non-browser signature. If that is the whole story, then ONE
client (urllib) sending three different UA strings should split: a
browser-shaped UA passes, the Python-shaped default does not.

Three things the record makes mandatory here:

  - The payload must be LONG. Its 2x2 shows both clients returning 200 on
    a short body, so a short probe cannot fail and proves nothing.
  - Order matters. The passing cases run FIRST, so that if the last one
    fails it cannot be blamed on a pool some earlier failure knocked over.
  - max_tokens must be big. This model spends the budget on reasoning
    before it answers; a small cap returns 502 "reasoning consumed N/N",
    which is a DIFFERENT failure and would be easy to misread as the block.

A short request runs first as a LIVENESS check. That is not a contradiction
of the first rule: a short body cannot see the 1010, but it can see the
pool being down (503 ALL_ACCOUNTS_INACTIVE), and if the pool is down all
three real cases fail identically and the run means nothing.

Written to disk so the result can be re-derived without this session.
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

# Long body: a real file, which is what the reporter used. "say ok" is the
# case the record already showed cannot trigger it.
src = subprocess.run(
    ["git", "-C", "/home/houminxi/code/forge", "show",
     "c72ff06:src/code_forge/verify.py"],
    capture_output=True, text=True).stdout
assert len(src) > 20000, "source too short to reproduce: %d bytes" % len(src)
LONG = "Review this code and reply with JSON findings:\n\n" + src

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def ask(ua, prompt, cap):
    hdrs = {"Content-Type": "application/json",
            "Authorization": "Bearer " + KEY}
    if ua is not None:
        hdrs["User-Agent"] = ua
    body = json.dumps({
        "model": MODEL, "stream": False, "max_tokens": cap,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers=hdrs, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=420, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return "ERR", "%s: %s" % (type(e).__name__, e), time.time() - t0


def classify(code, body):
    """Name the failure, so 1010 is never confused with the token budget."""
    if "1010" in body:
        return "BLOCKED 1010"
    if "ALL_ACCOUNTS_INACTIVE" in body or "all upstream accounts" in body:
        return "POOL DOWN"
    if "reasoning consumed" in body:
        return "TOKEN BUDGET"
    if code == 200:
        return "OK"
    return "other %s" % code


print("model=%s  long prompt=%d bytes  max_tokens=16384"
      % (MODEL, len(LONG)))
print()

# Liveness first. Short on purpose: it cannot see the 1010, but it is the
# only cheap way to tell a live pool from a dead one before spending 20min.
code, body, el = ask(CHROME, "say ok", 64)
print("liveness (short body, Chrome UA): %s %s in %.1fs" %
      (code, classify(code, body), el))
if classify(code, body) == "POOL DOWN":
    print("\nPool is down. Every case below would fail identically and the")
    print("run would prove nothing about the User-Agent. Stopping.")
    sys.exit(2)
print()

print("%-26s %6s %9s  %-14s %s"
      % ("User-Agent sent", "code", "wall", "outcome", "body head"))
print("-" * 108)

CASES = [
    ("Chrome 131", CHROME),
    ("curl/8.18.0", "curl/8.18.0"),
    ("(urllib default)", None),
]
results = []
for label, ua in CASES:
    code, body, el = ask(ua, LONG, 16384)
    verdict = classify(code, body)
    results.append((label, verdict))
    print("%-26s %6s %8.1fs  %-14s %s"
          % (label, code, el, verdict, body[:60].replace("\n", " ")))
    time.sleep(20)

print()
outcomes = {v for _, v in results}
if len(outcomes) == 1:
    print("VERDICT: all three identical (%s). The User-Agent is NOT the"
          % outcomes.pop())
    print("         discriminator under these conditions.")
else:
    print("VERDICT: the User-Agent changes the outcome. %s"
          % ", ".join("%s=%s" % r for r in results))
