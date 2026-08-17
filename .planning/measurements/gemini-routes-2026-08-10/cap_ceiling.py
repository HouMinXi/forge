"""Where is onmi-gemini3.1's output cap ceiling?

4096 answers, 65536 returns 400 capability_mismatch. The 2x2 already
showed the prompt does not matter -- a two-word body and a 70KB body
behave identically at both caps -- so probing with a short body here is
valid and cheap, not the mistake it would be for a size-triggered bug.
Rejections come back in about a tenth of a second, so only the caps that
WORK cost any time.

The point is to put a proven number in gate.yaml rather than the lowest
one that happened to work.
"""
import json, os, ssl, sys, time, urllib.request, urllib.error
URL = "https://192.168.100.10:20128/v1/chat/completions"
KEY = os.environ["OMNIROUTE_API_KEY"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
print("%8s %6s %8s  %s" % ("cap", "code", "wall", "note"))
for cap in (4096, 8192, 12288, 16384, 32768):
    body = json.dumps({"model": "onmi-gemini3.1", "stream": False,
                       "max_tokens": cap,
                       "messages": [{"role": "user", "content": "say ok"}]}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        code, raw = "ERR", str(e)
    el = time.time() - t0
    try:
        d = json.loads(raw)
        note = d["error"]["code"] if "error" in d else "OK"
    except Exception:
        note = raw[:60]
    print("%8d %6s %7.1fs  %s" % (cap, code, el, note))
    time.sleep(10)
