"""Verification of the gemini-3.6 round-1 BLOCKER 1.

Claim: _compare_at skips excerpt lines whose numbers fall outside the
post-image (ln not in file_lines -> continue), so an excerpt that opens with
one genuine line and then appends invented ones returns (True, None) on the
strength of that single overlap.

deepseek examined the same behaviour and excluded it as pre-existing rather
than introduced. Both trees are therefore run: if main behaves identically
the flaw is inherited, if only the branch accepts it the rewrite introduced
it. Either way the code carrying it now is new.

A second, genuine excerpt witnesses the hunk so the run reaches STEP C --
without it the attestation dies earlier at STEP A and the test proves
nothing.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from code_forge.verify import run_verify

SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]

DIFF = (
    "diff --git a/src/f.py b/src/f.py\n"
    "--- a/src/f.py\n"
    "+++ b/src/f.py\n"
    "@@ -1,2 +1,10 @@\n"
    " def f():\n"
    "     return 1\n"
    "+\n"
    "+\n"
    "+def g():\n"
    "+    return 2\n"
    "+\n"
    "+\n"
    "+def h():\n"
    "+    return 3\n"
)
WHOLE = ("def f():\n    return 1\n\n\ndef g():\n    return 2\n"
         "\n\ndef h():\n    return 3\n")
# post-image: 1..10, line 10 is "    return 3"

TAIL_FABRICATION = (
    "    return 3\n"          # line 10, genuine -- the single overlap
    "SECRET_BACKDOOR = 1\n"   # line 11, absent from the post-image
    "os.system(payload)\n"    # line 12, absent from the post-image
)


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def build():
    root = Path(tempfile.mkdtemp())
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("def f():\n    return 1\n")
    d = sha(DIFF)
    n = 0
    for c in (1, 2, 3):
        for p in (1, 2, 3):
            n += 1
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps({
                "cycle": c, "pass": p, "skill": SKILLS[(p - 1) % 3],
                "diff_sha256": d,
                "timestamp": "2026-05-28T10:%02d:00Z" % n,
                "findings_count": 0, "findings": [],
                "anchors": [{"file": "src/f.py", "line": 1,
                             "text": "def f():"}],
                "code_excerpts": [
                    {"file": "src/f.py", "start_line": 1, "end_line": 10,
                     "content": WHOLE, "rationale": "genuine witness"},
                    {"file": "src/f.py", "start_line": 10, "end_line": 12,
                     "content": TAIL_FABRICATION,
                     "rationale": "one real line then two invented ones"},
                ],
                "covered_line_ranges": [
                    {"file": "src/f.py", "start": 1, "end": 10}],
            }))
    return root, d


def main():
    import code_forge.verify as v
    print("tree: %s" % v.__file__)
    print("md5 : %s" % hashlib.md5(Path(v.__file__).read_bytes()).hexdigest())
    root, d = build()
    res = run_verify(root, d, {"src/f.py": list(range(1, 11))},
                     diff_text=DIFF)
    print("tail-fabrication excerpt accepted: %s" % ("YES -- hole confirmed"
                                                     if res.passed else "no"))
    if not res.passed:
        print("  rejected with: %s" % res.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
