# Review order: forge verify gate, three defects on one branch

You are reviewing a git diff. Your output is a review, not a patch. Do not
edit files. Do not run git commands that change state.

## Scope fence

Review ONLY the diff in `diff.patch` (branch `defects/verify-gate` vs its
merge base). Two files:

    src/code_forge/verify.py     118 lines changed
    tests/test_verify.py         365 lines changed

Everything else in the repository is out of scope. If a problem you see lives
in an unchanged file, say so explicitly and mark it OUT-OF-SCOPE rather than
counting it as a finding against this diff.

## What the code is

`code_forge/verify.py` is a commit gate. Before a commit is allowed, a
reviewer model must have produced nine "receipts" (3 review cycles x 3
passes). Each receipt carries `code_excerpts`: quoted source lines the
reviewer claims it read, each tagged with `file`, `start_line`, `end_line`,
`content`. `run_verify` checks those receipts against the actual diff. Its
purpose is anti-fabrication: proving the reviewer really looked at the code
rather than inventing plausible commentary.

The checks run in order, and ANY failure fails the whole attestation:

    1. all nine cycle/pass slots present
    2. every receipt's diff hash matches the diff being committed
    3. anchor files appear in the diff
    4. timestamps monotonic
    5. STEP A: each hunk is witnessed by >=1 overlapping excerpt
       STEP C: each excerpt's content matches the diff post-image
    6. coverage floor

## The three changes under review

**(a) Which cycles count.** Previously verify demanded cycles numbered
exactly 1, 2, 3. A review that ran four cycles (because cycle 3 surfaced a
finding and reset the counter) had receipts c1..c4 and failed with "missing
cycle/pass combinations" even though three consecutive clean cycles existed.
The change accepts the LAST three consecutive cycles.

**(b) Out-of-hunk excerpts.** Previously one excerpt whose lines fell outside
any hunk failed the entire attestation.

**(c) Excerpt anchor tolerance -- STEP C.** This is the change I am least
sure of and want attacked hardest.

Measured problem: on a real review of a real diff, 21 of 78 excerpts failed
STEP C. Inspecting them, the content was genuine file content, but
`start_line` named a line 1-2 above where the quoted block actually began.
Cause: the reviewer sets `start_line` to where the CHANGE starts and quotes
`content` from where the readable code starts; the blank lines that separate
Python definitions sit between the two. Nothing in any prompt ever stated
that the two must be the same anchor.

The change: instead of comparing only at `start_line`, try shifts
(0, 1, -1, 2, -2, 3, -3) and accept the excerpt if the WHOLE quoted block
matches contiguously at any one of them.

The anti-fabrication argument for accepting this, which you should try to
break: fabricated content matches at NO shift, because the whole block must
match line-for-line against the post-image; a model that can reproduce real
file content has read the file, which is the property STEP C exists to
establish; the bounded window only makes the pointer slightly imprecise.

## Ground truth already established (do not re-derive, but you may challenge)

    pytest full suite         2994 passed, 4 skipped
    ruff                      clean
    non-ASCII gate            clean
    bug injection at (c):
      shift set -> (0,)                 "accepted" test FAILS   (window load-bearing)
      shift set -> (0,1,-1,2,-2,3,-3,6,-6)
                                        "beyond window" test FAILS (bound load-bearing)
    known-answer run on 78 real excerpts:
      old STEP C  57/78 pass
      new STEP C  63/78 pass
      residual 15 failures are a DIFFERENT cause (the reviewer double-escapes
      backslashes when serializing source lines into JSON). Out of scope here.

## What I want from you

Attack the diff. In priority order:

1. Can a FABRICATED excerpt now pass STEP C that would have failed before?
   Construct the concrete input if you think so.
2. Does change (a) admit a receipt set that is not actually three
   consecutive clean cycles? Cycle numbering, gaps, duplicates, ordering.
3. Does change (b) open a path where an attestation passes with no real
   coverage?
4. Correctness bugs in the new code: control flow, off-by-one, dead code,
   unreachable branches, error messages that name the wrong line.
5. Do the new tests actually bite? A test that passes for the wrong reason
   is worse than no test. Name any assertion that would stay green if the
   code under it were broken.

## Output contract

For each finding:

    SEVERITY: BLOCKER | MAJOR | MINOR | NIT
    FILE:LINE: <path>:<line in the NEW file>
    CLAIM: one sentence, what is wrong
    FAILURE: concrete input or sequence -> concrete wrong outcome
    EVIDENCE: quote the exact lines from the diff you are relying on

End with a single line:

    SCORECARD: blocker=<n> major=<n> minor=<n> nit=<n>

## Honest failure is pre-authorized

Zero findings is an acceptable and expected answer. Do not manufacture
findings to look thorough. If you cannot construct a concrete failing input
for a suspicion, report it as a QUESTION section at the end, not as a
finding. If you did not or could not examine something, say so plainly.
Inventing a file:line that does not exist in the diff is the worst possible
outcome and will be checked.

## diff.patch
```diff
diff --git a/src/code_forge/verify.py b/src/code_forge/verify.py
index 1571e63..f2ac681 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -246,11 +246,9 @@ def run_verify(
     except CorruptedReceiptError as exc:
         return VerifyResult(False, "corrupt receipt: %s" % exc, 1, cp)
 
-    # 1. completeness: 9 receipts, cycle/pass matrix, findings_count
-    # Known design constraint: expects exactly cycles 1-3 x passes 1-3.
-    # Reviews that take >3 total rounds write cycle 4+ receipts and fail
-    # this check. Intended for post-convergence verification only (the last
-    # 3 consecutive clean cycles produce the authoritative 9 receipts).
+    # 1. completeness: last 3 consecutive cycles x passes 1-3, findings_count.
+    # Reviews that take >3 rounds write cycle 4+ receipts; the last 3
+    # consecutive clean cycles are what matters, regardless of their numbers.
     if len(receipts) < 9:
         msg = "missing receipts: %d/9" % len(receipts)
         if len(receipts) == 0:
@@ -268,9 +266,23 @@ def run_verify(
         if r["findings_count"] != len(r["findings"]):
             return VerifyResult(
                 False, "findings_count mismatch c%dp%d" % key, 1, cp)
-    expected = {(c, p) for c in range(1, 4) for p in range(1, 4)}
-    if seen_keys != expected:
-        return VerifyResult(False, "missing cycle/pass combinations", 1, cp)
+    # Verify the LAST 3 consecutive cycles, whatever their numbers.
+    cycles = sorted({r["cycle"] for r in receipts})
+    if len(cycles) < 3:
+        return VerifyResult(
+            False, "fewer than 3 cycles: %d" % len(cycles), 1, cp)
+    last_three = cycles[-3:]
+    for i in range(len(last_three) - 1):
+        if last_three[i + 1] - last_three[i] != 1:
+            return VerifyResult(
+                False,
+                "last 3 cycles not consecutive: %s" % last_three,
+                1, cp)
+    for c in last_three:
+        for p in range(1, 4):
+            if (c, p) not in seen_keys:
+                return VerifyResult(
+                    False, "missing cycle %d/pass %d" % (c, p), 1, cp)
     cp += 1
 
     # 2. hash
@@ -338,6 +350,13 @@ def run_verify(
                     )
 
         # STEP B: excerpt-to-hunk anchoring
+        # Out-of-hunk excerpts are allowed (not rejected) when STEP A
+        # coverage is satisfied.  They are NOT verified against the working
+        # tree because that introduces TOCTOU (the diff is immutable at
+        # verify time; the working tree is not).  The 60% coverage floor
+        # (check 6) makes padding with out-of-hunk excerpts pointless:
+        # they do not intersect all_diff, so they cannot raise coverage.
+        # Fabrication detection for in-hunk excerpts (STEP C) is unchanged.
         for exc in all_excerpts:
             content = exc.get("content", "")
             if not content or not content.strip():
@@ -355,18 +374,9 @@ def run_verify(
             # Exempt files (binary/rename/mode-change) pass without overlap check --
             # they have no hunks in hunk_map, so hunk anchoring cannot be verified.
             # This is intentional: exempt files produce no coverage obligation.
-            if exc["file"] in hunk_map:
-                overlaps = any(
-                    max(exc["start_line"], h["start"]) <= min(exc["end_line"], h["end"])
-                    for h in hunk_map[exc["file"]]
-                )
-                if not overlaps and exc["file"] not in exempt_files:
-                    return VerifyResult(
-                        False,
-                        "excerpt %s:%d-%d not in any diff hunk" % (
-                            exc["file"], exc["start_line"], exc["end_line"]),
-                        5, cp,
-                    )
+            # Out-of-hunk excerpts (file in hunk_map but no overlap) are allowed
+            # when STEP A coverage is satisfied.  The 60% floor makes padding
+            # pointless (out-of-hunk lines do not intersect all_diff).
 
         # STEP C: content verification against diff post-image
         # The diff is immutable at verify time -- no TOCTOU with working tree.
@@ -375,34 +385,60 @@ def run_verify(
         # post-image but cannot distinguish "covers only context lines" from "covers
         # actual changed lines." A reviewer can pass STEP C by citing only context
         # lines around the change. The 60% coverage floor (check 6) mitigates this.
+        def _compare_at(actual_lines, file_lines, first_line, last_line):
+            """Compare an excerpt to the post-image anchored at first_line.
+
+            Returns (True, None) when every overlapping line matches,
+            (False, line) naming the first that does not, and (None, None)
+            when the excerpt and the post-image do not overlap here.
+            """
+            overlap = 0
+            for i, ln in enumerate(range(first_line, last_line + 1)):
+                if i >= len(actual_lines) or ln not in file_lines:
+                    continue
+                overlap += 1
+                if actual_lines[i].rstrip() != file_lines[ln].rstrip():
+                    return False, ln
+            return (True, None) if overlap else (None, None)
+
         for exc in all_excerpts:
             actual_lines = exc.get("content", "").splitlines()
-            excerpt_line_map = {}
-            for i, ln in enumerate(range(exc["start_line"], exc["end_line"] + 1)):
-                if i < len(actual_lines):
-                    excerpt_line_map[ln] = actual_lines[i]
-
             file_lines = post_image.get(exc["file"], {})
-            overlap_lines = set(excerpt_line_map.keys()) & set(file_lines.keys())
 
-            if overlap_lines:
-                def normalize(s):
-                    return s.rstrip()
-                for ln in sorted(overlap_lines):
-                    if normalize(excerpt_line_map[ln]) != normalize(file_lines[ln]):
-                        return VerifyResult(
-                            False,
-                            "excerpt content mismatch at %s:%d (line %d)" % (
-                                exc["file"], exc["start_line"], ln),
-                            5, cp,
-                        )
+            # A reviewer anchors start_line on where the change is but quotes
+            # from where the code starts reading, and a blank line between the
+            # two shifts every comparison below. Measured on a real review:
+            # a third of otherwise faithful excerpts failed this way, none by
+            # more than two lines. So look for the anchor nearby rather than
+            # only at start_line -- content that is not in the file still
+            # matches at no shift at all, which is what this check is for.
+            attempts = [
+                _compare_at(
+                    actual_lines, file_lines,
+                    exc["start_line"] + shift, exc["end_line"] + shift,
+                )
+                for shift in (0, 1, -1, 2, -2, 3, -3)
+            ]
+            if any(ok for ok, _ in attempts):
+                continue
+            bad_line = attempts[0][1]
+            if bad_line is None:
+                # Nothing overlaps the post-image here, so there is nothing
+                # this check can verify. Coverage is checks 3 and 6.
+                continue
+            return VerifyResult(
+                False,
+                "excerpt content mismatch at %s:%d (line %d)" % (
+                    exc["file"], exc["start_line"], bad_line),
+                5, cp,
+            )
         cp += 1
 
         # 6. excerpt-derived coverage >= 60%
         # covered_line_ranges is self-reported, not measured -- audit-only. Ignored here.
         all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
         if all_diff:
-            for c in range(1, 4):
+            for c in last_three:
                 cov = _cycle_excerpt_covered(receipts, c) & all_diff
                 if len(cov) / len(all_diff) < 0.6:
                     return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
@@ -422,7 +458,7 @@ def run_verify(
                 cycle_findings[cyc] = []
             cycle_findings[cyc].extend(r.get("findings", []))
 
-        for a, b in combinations(range(1, 4), 2):
+        for a, b in combinations(last_three, 2):
             if not cycle_findings.get(a) and not cycle_findings.get(b):
                 continue
             cov_a = _cycle_excerpt_covered(receipts, a)
@@ -477,7 +513,7 @@ def run_verify(
         # 6. legacy coverage >= 60% (self-reported covered_line_ranges)
         all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
         if all_diff:
-            for c in range(1, 4):
+            for c in last_three:
                 cov = _cycle_covered(receipts, c) & all_diff
                 if len(cov) / len(all_diff) < 0.6:
                     return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
@@ -492,7 +528,7 @@ def run_verify(
                 cycle_findings[cyc] = []
             cycle_findings[cyc].extend(r.get("findings", []))
 
-        for a, b in combinations(range(1, 4), 2):
+        for a, b in combinations(last_three, 2):
             if not cycle_findings.get(a) and not cycle_findings.get(b):
                 continue
             j = _jaccard(_cycle_covered(receipts, a), _cycle_covered(receipts, b))
diff --git a/tests/test_verify.py b/tests/test_verify.py
index 3ea3d94..8bb4e71 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -10,10 +10,13 @@ def _sha(text: str) -> str:
     return hashlib.sha256(text.encode()).hexdigest()
 
 
+_SKILLS = ["qodo-review", "code-review-expert", "adversarial-qe"]
+
+
 def _receipt(cycle, pass_n, diff_sha, covered_start=1, covered_end=50):
     return {
         "cycle": cycle, "pass": pass_n,
-        "skill": ["qodo-review", "code-review-expert", "adversarial-qe"][pass_n - 1],
+        "skill": _SKILLS[(pass_n - 1) % len(_SKILLS)],
         "diff_sha256": diff_sha,
         "timestamp": "2026-05-28T10:%02d:00Z" % (cycle * 3 + pass_n),
         "findings_count": 0, "findings": [],
@@ -107,6 +110,81 @@ class TestVerifyChecks:
         assert not r.passed
 
 
+_SHIFT_DIFF = (
+    "diff --git a/src/f.py b/src/f.py\n"
+    "--- a/src/f.py\n"
+    "+++ b/src/f.py\n"
+    "@@ -1,2 +1,10 @@\n"
+    " def f():\n"
+    "     return 1\n"
+    "+\n"
+    "+\n"
+    "+def g():\n"
+    "+    return 2\n"
+    "+\n"
+    "+\n"
+    "+def h():\n"
+    "+    return 3\n"
+)
+_SHIFT_FILES = {"src/f.py": list(range(1, 11))}
+# Post-image: 1 "def f():"  2 "    return 1"  3 ""  4 ""
+#             5 "def g():"  6 "    return 2"  7 ""  8 ""
+#             9 "def h():" 10 "    return 3"
+_REAL_BLOCK = "def g():\n    return 2\n\n\ndef h():\n    return 3\n"
+
+
+class TestExcerptAnchorTolerance:
+    """A reviewer points start_line at the change and quotes from the code.
+
+    Measured on a real review: a third of faithful excerpts named a line one
+    or two above where their content actually began, because the added block
+    opened with the blank lines that separate definitions. Rejecting those
+    voided whole attestations over an alignment convention nobody stated.
+    """
+
+    def _nine(self, tmp_path, start_line, end_line, content):
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha(_SHIFT_DIFF)
+        for c in range(1, 4):
+            for p in range(1, 4):
+                r = _receipt(c, p, sha)
+                r["code_excerpts"] = [{
+                    "file": "src/f.py", "start_line": start_line,
+                    "end_line": end_line, "content": content,
+                    "rationale": "checked",
+                }]
+                (rd / ("receipt-c%dp%d.json" % (c, p))).write_text(
+                    json.dumps(r))
+        return sha
+
+    def _verify(self, tmp_path, start_line, end_line, content):
+        sha = self._nine(tmp_path, start_line, end_line, content)
+        return run_verify(tmp_path, sha, _SHIFT_FILES, diff_text=_SHIFT_DIFF)
+
+    def test_content_two_lines_below_start_line_is_accepted(self, tmp_path):
+        # The block really opens at 5; the reviewer said 3, where the change
+        # opens -- the two lines between them are the blank separator.
+        r = self._verify(tmp_path, 3, 10, _REAL_BLOCK)
+        assert r.passed, r.reason
+
+    def test_content_absent_from_the_file_is_still_rejected(self, tmp_path):
+        # The reason the shift is bounded: invented code matches at no
+        # offset, so tolerating one must not tolerate this.
+        r = self._verify(tmp_path, 3, 4, "def zzz():\n    return 99\n")
+        assert not r.passed
+        assert "excerpt content mismatch" in r.reason
+
+    def test_content_beyond_the_shift_window_is_still_rejected(self, tmp_path):
+        # Real content, but named six lines above where it lives: the pointer
+        # stops being evidence of where the reviewer actually looked.
+        r = self._verify(tmp_path, 3, 4, "def h():\n    return 3\n")
+        assert not r.passed
+        assert "excerpt content mismatch" in r.reason
+
+
 class TestCorruptReceipt:
     """A receipt that cannot be parsed must fail verify, not crash it.
 
@@ -672,6 +750,291 @@ class TestInvertedExcerptRange:
         _validate_receipt_schema(receipt, "test.json")
 
 
+def _write_cycles(rd, diff_sha, cycles):
+    """Write receipts for arbitrary cycle numbers (list of ints), 3 passes each.
+    Total receipts = len(cycles) * 3. For <3 cycles this is <9, which
+    triggers the 'missing receipts' check before the cycle check.
+    Coverage range spans the full diff (lines 1-50) to avoid triggering
+    the 60% floor on any cycle."""
+    for c in cycles:
+        for p in range(1, 4):
+            name = "receipt-c%dp%d.json" % (c, p)
+            (rd / name).write_text(json.dumps(
+                _receipt(c, p, diff_sha, 1, 50)
+            ))
+
+
+class TestLastThreeConsecutiveCycles:
+    """ITEM A: verify the LAST 3 consecutive cycles, whatever their numbers."""
+
+    def test_cycles_2_3_4_pass(self, tmp_path):
+        """Cycles 2-4 complete -> PASS (last 3 consecutive)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [2, 3, 4])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
+        assert r.passed, f"last 3 consecutive should pass, got: {r.reason}"
+
+    def test_cycles_1_2_4_fail(self, tmp_path):
+        """Cycles 1,2,4 -> FAIL (last 3 not consecutive: 2,3,4 missing 3)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [1, 2, 4])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed, f"cycles 1,2,4 should fail, got: {r.reason}"
+        assert "not consecutive" in r.reason
+
+    def test_cycles_1_2_fail(self, tmp_path):
+        """Only cycles 1-2 -> FAIL (fewer than 3 cycles).
+        Need 9+ receipts to pass the length check, but only 2 unique cycles."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        # Write 5 passes per cycle (cycle 1 uses passes 1-5, cycle 2 uses passes 1-5)
+        # to get 10 receipts (>9) but only 2 unique cycles
+        skills = ["qodo-review", "code-review-expert", "adversarial-qe",
+                  "qodo-review", "code-review-expert"]
+        for c in [1, 2]:
+            for p in range(1, 6):
+                name = "receipt-c%dp%d.json" % (c, p)
+                receipt = _receipt(c, p, sha, 1, 50)
+                receipt["skill"] = skills[p - 1]
+                (rd / name).write_text(json.dumps(receipt))
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed, f"cycles 1,2 should fail, got: {r.reason}"
+        assert "fewer than 3 cycles" in r.reason
+
+    def test_cycles_5_6_7_pass(self, tmp_path):
+        """Cycles 5-7 -> PASS (last 3 consecutive, high numbers)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [5, 6, 7])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert r.passed, f"cycles 5-7 should pass, got: {r.reason}"
+
+    def test_cycles_1_2_3_4_pass(self, tmp_path):
+        """Cycles 1-4 -> PASS (last 3 are 2,3,4, consecutive)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [1, 2, 3, 4])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert r.passed
+
+    def test_cycles_1_3_4_fail(self, tmp_path):
+        """Cycles 1,3,4 -> FAIL (last 3 not consecutive: 2 missing)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [1, 3, 4])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed, f"cycles 1,3,4 should fail, got: {r.reason}"
+        assert "not consecutive" in r.reason
+
+    def test_cycles_2_3_4_missing_pass_fail(self, tmp_path):
+        """Cycles 2-4 consecutive but cycle 4 missing pass 3 -> FAIL."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        # Write cycles 2-3 complete, cycle 4 only passes 1-2 (8 receipts total,
+        # but we need 9+ to pass the length check; add cycle 1 with 3 passes)
+        for c in [1, 2, 3]:
+            for p in range(1, 4):
+                name = "receipt-c%dp%d.json" % (c, p)
+                (rd / name).write_text(json.dumps(_receipt(c, p, sha, 1, 50)))
+        for p in range(1, 3):  # only passes 1-2 for cycle 4
+            name = "receipt-c4p%d.json" % (p,)
+            (rd / name).write_text(json.dumps(_receipt(4, p, sha, 1, 50)))
+        # 11 receipts total, last 3 cycles are 2,3,4 but 4 is missing pass 3
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed, f"missing pass should fail, got: {r.reason}"
+        assert "missing cycle 4/pass 3" in r.reason
+
+
+class TestOutOfHunkExcerpts:
+    """ITEM B: out-of-hunk excerpts allowed when STEP A coverage satisfied."""
+
+    def test_u3_receipt_set_passes(self, tmp_path):
+        """1 excerpt in hunk + 1 stray, full hunk coverage -> PASS."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        # Correct unidiff format
+        diff_content = (
+            "diff --git a/src/f.py b/src/f.py\n"
+            "--- a/src/f.py\n"
+            "+++ b/src/f.py\n"
+            "@@ -1,2 +1,2 @@\n"
+            " def f():\n"
+            "-    return 1\n"
+            "+    return 2\n"
+        )
+        (tmp_path / "src" / "f.py").write_text(
+            "def f():\n    return 2\n"
+        )
+        sha = _sha(diff_content)
+        diff_files = parse_diff_files(diff_content)
+        # 3 cycles, 3 passes each, excerpt in hunk + 1 stray (lines 10-12, beyond diff)
+        for c in range(1, 4):
+            for p in range(1, 4):
+                receipt = _receipt(c, p, sha)
+                # The in-hunk excerpt content must match post-image lines 1-2
+                receipt["code_excerpts"][0]["content"] = "def f():\n    return 2"
+                # Add a stray excerpt (lines 10-12, outside any hunk)
+                receipt["code_excerpts"].append({
+                    "file": "src/f.py", "start_line": 10, "end_line": 12,
+                    "content": "# context\n# more context\n# end",
+                    "rationale": "context"
+                })
+                name = "receipt-c%dp%d.json" % (c, p)
+                (rd / name).write_text(json.dumps(receipt))
+        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
+        assert r.passed
+
+    def test_excerpt_content_mismatch_still_fails(self, tmp_path):
+        """Excerpt content that contradicts the post-image must still fail."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        diff_content = (
+            "diff --git a/src/f.py b/src/f.py\n"
+            "--- a/src/f.py\n"
+            "+++ b/src/f.py\n"
+            "@@ -1,2 +1,2 @@\n"
+            " def f():\n"
+            "-    return 1\n"
+            "+    return 2\n"
+        )
+        (tmp_path / "src" / "f.py").write_text(
+            "def f():\n    return 2\n"
+        )
+        sha = _sha(diff_content)
+        diff_files = parse_diff_files(diff_content)
+        for c in range(1, 4):
+            for p in range(1, 4):
+                receipt = _receipt(c, p, sha)
+                receipt["code_excerpts"][0]["content"] = "def f():\n    return 999\n"
+                name = "receipt-c%dp%d.json" % (c, p)
+                (rd / name).write_text(json.dumps(receipt))
+        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
+        assert not r.passed, f"content mismatch should fail, got: {r.reason}"
+        assert "content mismatch" in r.reason
+
+    def test_unwitnessed_hunk_still_fails(self, tmp_path):
+        """A hunk with no excerpt witness must still fail."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        diff_content = (
+            "diff --git a/src/f.py b/src/f.py\n"
+            "--- a/src/f.py\n"
+            "+++ b/src/f.py\n"
+            "@@ -1,2 +1,2 @@\n"
+            " def f():\n"
+            "-    return 1\n"
+            "+    return 2\n"
+            "@@ -10,2 +10,2 @@\n"
+            " def g():\n"
+            "-    return 3\n"
+            "+    return 4\n"
+        )
+        (tmp_path / "src" / "f.py").write_text(
+            "def f():\n    return 2\n\ndef g():\n    return 4\n"
+        )
+        sha = _sha(diff_content)
+        diff_files = parse_diff_files(diff_content)
+        for c in range(1, 4):
+            for p in range(1, 4):
+                receipt = _receipt(c, p, sha)
+                receipt["code_excerpts"] = [{
+                    "file": "src/f.py", "start_line": 1, "end_line": 2,
+                    "content": "def f():\n    return 2\n",
+                    "rationale": "checked"
+                }]
+                name = "receipt-c%dp%d.json" % (c, p)
+                (rd / name).write_text(json.dumps(receipt))
+        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
+        assert not r.passed, f"unwitnessed hunk should fail, got: {r.reason}"
+        assert "unwitnessed hunk" in r.reason
+
+    def test_stray_file_not_in_diff_rejected(self, tmp_path):
+        """Excerpt referencing a file absent from diff -> FAIL (not in diff)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        diff_content = (
+            "diff --git a/src/f.py b/src/f.py\n"
+            "--- a/src/f.py\n"
+            "+++ b/src/f.py\n"
+            "@@ -1,2 +1,2 @@\n"
+            " def f():\n"
+            "-    return 1\n"
+            "+    return 2\n"
+        )
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 2\n")
+        sha = _sha(diff_content)
+        diff_files = parse_diff_files(diff_content)
+        for c in range(1, 4):
+            for p in range(1, 4):
+                receipt = _receipt(c, p, sha)
+                receipt["code_excerpts"][0]["content"] = "def f():\n    return 2"
+                # Add excerpt for a file that does not appear in the diff at all
+                receipt["code_excerpts"].append({
+                    "file": "src/other.py", "start_line": 1, "end_line": 2,
+                    "content": "x = 1\n",
+                    "rationale": "stray"
+                })
+                name = "receipt-c%dp%d.json" % (c, p)
+                (rd / name).write_text(json.dumps(receipt))
+        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
+        assert not r.passed, "excerpt for file not in diff should fail"
+        assert "not in diff" in r.reason
+
+
+class TestNonConsecutiveEarlierCycles:
+    """ITEM A edge case: non-consecutive earlier cycles with consecutive last 3."""
+
+    def test_gap_before_last_three_pass(self, tmp_path):
+        """Cycles [1,3,5,6,7] -> PASS (last 3 are 5,6,7 consecutive)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [1, 3, 5, 6, 7])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
+        assert r.passed, f"expected PASS for last 3 consecutive, got: {r.reason}"
+
+    def test_gap_in_last_three_fail(self, tmp_path):
+        """Cycles [1,3,5,7,8] -> FAIL (last 3 are 5,7,8 not consecutive)."""
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_cycles(rd, sha, [1, 3, 5, 7, 8])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 50))})
+        assert not r.passed, f"expected FAIL for non-consecutive last 3, got: {r.reason}"
+        assert "not consecutive" in r.reason
+
+
 class TestCoveredStringShape:
     """_covered must tolerate both dict and string shapes of
     covered_line_ranges."""
```
