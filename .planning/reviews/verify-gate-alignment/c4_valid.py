"""Corrected test of the claim behind task #52.

Two earlier attempts were invalid and both are recorded here rather than
discarded:

  A1 (heldout_adversary.py) called run_verify WITHOUT diff_text, so checks
     5, 6 and 7 never executed. It proved check 1 ignores findings; it did
     not prove the real gate path accepts them.
  C4 (verify_ds_claims.py) used identical excerpts across cycles and was
     rejected by check 7 (Jaccard 1.00) before findings mattered.

Check 7 turns out to do part of the job I thought was missing: it is skipped
only when BOTH cycles of a pair are clean, so a dirty cycle inside the
counted window forces the cycles' excerpt coverage to differ. The open
question is therefore narrower than #52 states: does a review whose counted
cycles carry live findings AND genuinely re-read different code pass?

This fixture answers that. Every receipt runs hardened (diff_text supplied),
each cycle covers a different 60%+ slice, and cycles 2 and 3 carry findings.
Diagnostics are printed so the fixture can be checked rather than trusted.
"""
import hashlib
import json
import sys
import tempfile
from itertools import combinations
from pathlib import Path

from code_forge.verify import (
    _cycle_excerpt_covered, _extract_post_image_lines, _jaccard, run_verify,
)

SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]
N = 24  # post-image lines 1..N

_body = ["def f():", "    return 1"]
_added = ["v%02d = %d" % (i, i) for i in range(3, N + 1)]
POST = _body + _added
DIFF = (
    "diff --git a/src/f.py b/src/f.py\n"
    "--- a/src/f.py\n"
    "+++ b/src/f.py\n"
    "@@ -1,2 +1,%d @@\n" % N
    + " def f():\n"
    + "     return 1\n"
    + "".join("+%s\n" % ln for ln in _added)
)


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def excerpt(start, count):
    """An excerpt quoting the real post-image, lines start..start+count-1."""
    return {
        "file": "src/f.py", "start_line": start,
        "end_line": start + count - 1,
        "content": "".join("%s\n" % POST[i - 1]
                           for i in range(start, start + count)),
        "rationale": "read lines %d-%d" % (start, start + count - 1),
    }


# Three different slices, each >= 60% of the 22 added lines (3..24).
SLICES = {0: (3, 17), 1: (8, 17), 2: (3, 8)}
EXTRA = {2: (16, 9)}  # cycle index 2 needs a second block to clear the floor


def receipts_for(cycles, dirty_cycles):
    out = []
    n = 0
    for idx, c in enumerate(cycles):
        for p in (1, 2, 3):
            n += 1
            finds = ([{"severity": "MAJOR", "file": "src/f.py",
                       "line": 5 + idx,
                       "description": "live finding in cycle %d" % c}]
                     if c in dirty_cycles else [])
            exc = [excerpt(*SLICES[idx])]
            if idx in EXTRA:
                exc.append(excerpt(*EXTRA[idx]))
            out.append({
                "cycle": c, "pass": p,
                "skill": SKILLS[(p - 1) % 3],
                "diff_sha256": sha(DIFF),
                "timestamp": "2026-05-28T10:%02d:00Z" % n,
                "findings_count": len(finds), "findings": finds,
                "anchors": [{"file": "src/f.py", "line": 3,
                             "text": _added[0]}],
                "code_excerpts": exc,
                "covered_line_ranges": [
                    {"file": "src/f.py", "start": 1, "end": N}],
            })
    return out


def build(cycles, dirty_cycles):
    root = Path(tempfile.mkdtemp())
    rd = root / ".code-forge" / "receipts"
    rd.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("\n".join(POST) + "\n")
    for r in receipts_for(cycles, dirty_cycles):
        (rd / ("receipt-c%dp%d.json" % (r["cycle"], r["pass"]))).write_text(
            json.dumps(r))
    return root


def diagnostics(cycles, dirty):
    rs = receipts_for(cycles, dirty)
    post = _extract_post_image_lines(DIFF)
    all_diff = {("src/f.py", ln) for ln in post["src/f.py"]}
    print("  fixture diagnostics (must satisfy check 6 >=60%% and check 7 <=0.8):")
    for c in cycles:
        cov = _cycle_excerpt_covered(rs, c) & all_diff
        print("    cycle %d coverage %5.1f%%  findings=%d"
              % (c, 100 * len(cov) / len(all_diff),
                 sum(len(r["findings"]) for r in rs if r["cycle"] == c)))
    for a, b in combinations(cycles, 2):
        print("    jaccard c%d-c%d = %.2f"
              % (a, b, _jaccard(_cycle_excerpt_covered(rs, a),
                                _cycle_excerpt_covered(rs, b))))


def main():
    import code_forge.verify as v
    print("tree: %s" % v.__file__)
    print("md5 : %s\n" % hashlib.md5(Path(v.__file__).read_bytes()).hexdigest())
    d = sha(DIFF)
    files = {"src/f.py": list(range(1, N + 1))}
    for label, cycles, dirty in (
        ("control: all three cycles clean", (1, 2, 3), ()),
        ("cycles 2 and 3 carry live findings", (1, 2, 3), (2, 3)),
        ("all three counted cycles dirty", (1, 2, 3), (1, 2, 3)),
    ):
        print("%s" % label)
        diagnostics(cycles, dirty)
        res = run_verify(build(cycles, dirty), d, files, diff_text=DIFF)
        print("  VERDICT: %s%s\n" % ("PASS" if res.passed else "FAIL",
                                     "" if res.passed else
                                     "  (%s)" % res.reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
