"""Held-out adversary for the verify-gate-alignment review.

Deliberately NOT described in BRIEF.md. Frozen and run before the round-1
model results were read, so the models cannot have shaped it and it cannot
have been shaped to match them.

Pre-registered predictions, written before first execution:

  A1  branch PASSES an attestation whose last three consecutive cycles every
      one carry a real finding. Nothing in check 1 requires findings_count to
      be zero -- it only requires findings_count == len(findings). Under the
      OLD rule (cycles must be exactly 1,2,3) a review that reset its counter
      wrote c1..c5 and failed on numbering, which accidentally masked the
      missing zero-check. The new last-three-consecutive rule removes that
      accident. Prediction: branch PASS (hole exposed), main FAIL (numbering).

  A2  branch SKIPS an excerpt that has no overlap with the post-image at
      shift 0 but overlaps and mismatches at shift +2. attempts[0][1] is None
      in that case, so the code continues instead of rejecting. Prediction:
      branch PASS (excerpt silently tolerated).

Usage:
    PYTHONPATH=<tree>/src python3 heldout_adversary.py
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from code_forge.verify import run_verify

SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def receipt(cycle, pass_n, diff_sha, findings_n=0, covered=(1, 50)):
    finds = [
        {"severity": "MAJOR", "file": "src/f.py", "line": 2,
         "description": "unresolved finding %d" % i}
        for i in range(findings_n)
    ]
    return {
        "cycle": cycle, "pass": pass_n,
        "skill": SKILLS[(pass_n - 1) % len(SKILLS)],
        "diff_sha256": diff_sha,
        "timestamp": "2026-05-28T10:%02d:00Z" % (cycle * 3 + pass_n),
        "findings_count": len(finds), "findings": finds,
        "anchors": [{"file": "src/f.py", "line": 1, "text": "def f():"}],
        "code_excerpts": [
            {"file": "src/f.py", "start_line": 1, "end_line": 3,
             "content": "def f():\n    return 1\n", "rationale": "checked"}
        ],
        "covered_line_ranges": [
            {"file": "src/f.py", "start": covered[0], "end": covered[1]}
        ],
    }


def a1_findings_survive_the_window():
    """Nine receipts, cycles 3/4/5, every one carrying a live finding."""
    root = Path(tempfile.mkdtemp())
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("def f():\n    return 1\n")
    d = sha("diff")
    for c in (3, 4, 5):
        off = (c - 3) * 10
        for p in (1, 2, 3):
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
                json.dumps(receipt(c, p, d, findings_n=1,
                                   covered=(1 + off, 45 + off))))
    return run_verify(root, d, {"src/f.py": list(range(1, 51))})


A2_DIFF = (
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
# post-image lines the diff carries: 1..10.


def a2_no_overlap_at_zero_mismatch_nearby():
    """start_line 40 is outside the post-image entirely; content is wrong.

    At shift 0 there is no overlap, so _compare_at returns (None, None).
    Every other shift is also outside. The excerpt therefore reaches the
    bad_line-is-None branch and is skipped rather than judged.
    """
    root = Path(tempfile.mkdtemp())
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("def f():\n    return 1\n")
    d = sha(A2_DIFF)
    for c in (1, 2, 3):
        for p in (1, 2, 3):
            r = receipt(c, p, d)
            r["code_excerpts"] = [
                {"file": "src/f.py", "start_line": 1, "end_line": 10,
                 "content": ("def f():\n    return 1\n\n\ndef g():\n"
                             "    return 2\n\n\ndef h():\n    return 3\n"),
                 "rationale": "real block, correct anchor"},
                {"file": "src/f.py", "start_line": 40, "end_line": 41,
                 "content": "def totally_invented():\n    return 999\n",
                 "rationale": "fabricated, anchored outside the post-image"},
            ]
            (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(json.dumps(r))
    return run_verify(root, d, {"src/f.py": list(range(1, 11))},
                      diff_text=A2_DIFF)


def main():
    import code_forge.verify as v
    print("verify.py under test: %s" % v.__file__)
    print("md5: %s" % hashlib.md5(
        Path(v.__file__).read_bytes()).hexdigest())
    print()
    for name, fn, predicted in (
        ("A1 findings survive the last-three window",
         a1_findings_survive_the_window, "PASS"),
        ("A2 fabricated excerpt anchored outside the post-image",
         a2_no_overlap_at_zero_mismatch_nearby, "PASS"),
    ):
        try:
            res = fn()
            got = "PASS" if res.passed else "FAIL"
            print("%-52s predicted=%s got=%s%s" % (
                name, predicted, got,
                "" if got == predicted else "   <-- PREDICTION MISSED"))
            if not res.passed:
                print("      reason: %s" % res.reason)
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            print("%-52s ERROR %s: %s" % (name, type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
