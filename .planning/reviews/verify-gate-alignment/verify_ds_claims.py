"""Independent verification of the deepseek round-1 findings.

A sub-model briefing is a claim to verify, never evidence. Each case below
reconstructs the finding's stated input from scratch and reports what the
code actually does, on both trees.

Cases:
  F1  fabricated excerpt whose nominal window sits just past the post-image,
      so shift 0 does not overlap but shifts -1..-3 overlap AND mismatch.
      deepseek claims: branch PASS (hole), main FAIL.
  F2  15 receipts, cycles 2/3/4 x passes 1..5. deepseek claims branch PASS,
      main FAIL, and that the "%d/9" message is then untrue.
  F3  cycles 9/10/11 with timestamps rising by cycle. deepseek claims branch
      FAILS on "timestamps not monotonic" because _load_receipts sorts by
      FILENAME and "c10" sorts before "c9". A false rejection.
  C4  MY OWN claim under test: I said change (a) newly exposes the missing
      zero-findings check. deepseek says main passes a dirty cycle 3 in a
      [1,2,3] set, so the hole was always reachable and my framing overstated
      it. If main PASSES here, deepseek is right and I was wrong.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from code_forge.verify import run_verify

SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


# post-image lines 1..12
DIFF12 = (
    "diff --git a/src/f.py b/src/f.py\n"
    "--- a/src/f.py\n"
    "+++ b/src/f.py\n"
    "@@ -1,2 +1,12 @@\n"
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
    "+\n"
    "+\n"
)
REAL_1_10 = ("def f():\n    return 1\n\n\ndef g():\n    return 2\n"
             "\n\ndef h():\n    return 3\n")


def base(cycle, pass_n, diff_sha, findings_n=0, ts=None, covered=(1, 12)):
    finds = [{"severity": "MAJOR", "file": "src/f.py", "line": 2,
              "description": "live finding %d" % i} for i in range(findings_n)]
    return {
        "cycle": cycle, "pass": pass_n,
        "skill": SKILLS[(pass_n - 1) % len(SKILLS)],
        "diff_sha256": diff_sha,
        "timestamp": ts or "2026-05-28T10:%02d:00Z" % (cycle * 5 + pass_n),
        "findings_count": len(finds), "findings": finds,
        "anchors": [{"file": "src/f.py", "line": 1, "text": "def f():"}],
        "code_excerpts": [
            {"file": "src/f.py", "start_line": 1, "end_line": 10,
             "content": REAL_1_10, "rationale": "real"}],
        "covered_line_ranges": [
            {"file": "src/f.py", "start": covered[0], "end": covered[1]}],
    }


def _tree():
    root = Path(tempfile.mkdtemp())
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("def f():\n    return 1\n")
    return root, rd


def f1_fabricated_just_past_the_post_image():
    root, rd = _tree()
    d = sha(DIFF12)
    for c in (1, 2, 3):
        for p in (1, 2, 3):
            r = base(c, p, d)
            r["code_excerpts"].append({
                "file": "src/f.py", "start_line": 13, "end_line": 14,
                "content": "def zzz():\n    return 99\n",
                "rationale": "fabricated; nominal window just past line 12"})
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps(r))
    return run_verify(root, d, {"src/f.py": list(range(1, 13))},
                      diff_text=DIFF12)


def f2_passes_beyond_three():
    root, rd = _tree()
    d = sha(DIFF12)
    n = 0
    for c in (2, 3, 4):
        for p in (1, 2, 3, 4, 5):
            n += 1
            r = base(c, p, d, ts="2026-05-28T10:%02d:00Z" % n)
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps(r))
    return run_verify(root, d, {"src/f.py": list(range(1, 13))},
                      diff_text=DIFF12)


def f3_two_digit_cycle_numbers():
    root, rd = _tree()
    d = sha(DIFF12)
    n = 0
    for c in (9, 10, 11):
        for p in (1, 2, 3):
            n += 1
            r = base(c, p, d, ts="2026-05-28T10:%02d:00Z" % n)
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps(r))
    return run_verify(root, d, {"src/f.py": list(range(1, 13))},
                      diff_text=DIFF12)


def c4_dirty_cycle_three_in_a_one_two_three_set():
    """My own claim under test. Cycles 1,2,3 -- the shape main accepts --
    with every receipt of cycle 3 carrying a live finding."""
    root, rd = _tree()
    d = sha(DIFF12)
    for c in (1, 2, 3):
        for p in (1, 2, 3):
            r = base(c, p, d, findings_n=(1 if c == 3 else 0))
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps(r))
    return run_verify(root, d, {"src/f.py": list(range(1, 13))},
                      diff_text=DIFF12)


CASES = [
    ("F1 fabricated excerpt just past post-image", f1_fabricated_just_past_the_post_image),
    ("F2 passes 4 and 5 inside the counted cycles", f2_passes_beyond_three),
    ("F3 cycles 9/10/11 (two-digit filenames)", f3_two_digit_cycle_numbers),
    ("C4 dirty cycle 3 inside a plain [1,2,3] set", c4_dirty_cycle_three_in_a_one_two_three_set),
]


def main():
    import code_forge.verify as v
    print("tree: %s" % v.__file__)
    print("md5 : %s\n" % hashlib.md5(Path(v.__file__).read_bytes()).hexdigest())
    for name, fn in CASES:
        try:
            res = fn()
            print("%-46s %s" % (name, "PASS" if res.passed else "FAIL"))
            if not res.passed:
                print("%-46s   reason: %s" % ("", res.reason))
        except Exception as exc:  # noqa: BLE001
            print("%-46s ERROR %s: %s" % (name, type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
