You are reviewing a small change to forge, a code-review gate tool. Be adversarial.
Your job is to find defects, not to approve. If you find none, say so plainly.

## What forge's receipts are

A "receipt" is a JSON file a reviewer writes attesting they read specific code.
It carries code_excerpts: [{file, start_line, end_line, content}]. Check 6 of the
verify pipeline requires those excerpts to cover >= 60% of the changed lines --
this is what makes a receipt evidence of reading rather than a claim of reading.

## The defect being fixed

_excerpt_covered credited the FULL declared start_line..end_line span toward
that 60% floor without ever looking at `content`. So a receipt could declare
lines 1-1000, paste three lines, and clear the floor. Proven by injection:
with the fix reverted, a receipt declaring 9 lines while showing 3 returns
"all 7 checks passed".

The upstream excerpt-content check does not catch this: it compares content
against the post-image line by line, so it only inspects lines the content
actually has.

## The diff

diff --git a/src/code_forge/verify.py b/src/code_forge/verify.py
index 11ea400..0c5817d 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -188,8 +188,15 @@ def _excerpt_covered(receipt: dict) -> set[tuple[str, int]]:
         f = exc.get("file", "")
         start = exc.get("start_line", 0)
         end = exc.get("end_line", 0)
+        content = exc.get("content", "")
         if isinstance(start, int) and isinstance(end, int) and f:
-            for ln in range(start, end + 1):
+            # Credit only the lines the excerpt actually shows. The declared
+            # range used to be trusted on its own, so claiming 1-1000 while
+            # pasting three lines earned 1000 lines toward the 60% floor in
+            # check 6 -- and the content check upstream never noticed,
+            # because it only compares lines the content actually has.
+            shown = len(content.splitlines()) if isinstance(content, str) else 0
+            for ln in range(start, min(end, start + shown - 1) + 1):
                 s.add((f, ln))
     return s
 
diff --git a/tests/test_verify.py b/tests/test_verify.py
index 66d77c6..d4bdb9c 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -557,6 +557,48 @@ class TestHardenedVerify:
         assert not r.passed
         assert "< 60%" in r.reason
 
+    def test_wide_range_with_thin_content_earns_no_extra_coverage(self, tmp_path):
+        """Check 6 credits only lines an excerpt actually shows.
+
+        Each excerpt below spans a whole hunk but pastes a single line. The
+        content that is present matches the post-image, so the excerpt check
+        passes -- it only compares lines the content actually has. Crediting
+        the declared span would score 9/9 and pass the floor on three lines
+        of evidence; crediting what is shown scores 3/9 and fails.
+        """
+        rd = self._rd(tmp_path)
+        sha = _sha(_HARDEN_DIFF)
+        diff_files = parse_diff_files(_HARDEN_DIFF)
+        inflated = [
+            {"file": "foo.py", "start_line": 1, "end_line": 3,
+             "content": "x = 1"},
+            {"file": "foo.py", "start_line": 6, "end_line": 8,
+             "content": "a = 1"},
+            {"file": "bar.py", "start_line": 1, "end_line": 3,
+             "content": "p = 1"},
+        ]
+        _write_hardened(rd, sha, excerpts=inflated)
+        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
+        assert not r.passed
+        assert "< 60%" in r.reason
+
+    def test_repeated_excerpts_still_read_as_rubber_stamp(self, tmp_path):
+        """Check 7 shares _excerpt_covered with check 6.
+
+        Capping credit at the shown lines shrinks what check 7 compares, so
+        pin the case it exists to catch: cycles that paste the same excerpts
+        still produce identical coverage sets and still trip the overlap
+        ceiling. Findings are present because check 7 skips pairs where both
+        cycles came back clean.
+        """
+        rd = self._rd(tmp_path)
+        sha = _sha(_HARDEN_DIFF)
+        diff_files = parse_diff_files(_HARDEN_DIFF)
+        _write_hardened(rd, sha, findings=[{"severity": "L2", "note": "x"}])
+        r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
+        assert not r.passed
+        assert "Jaccard" in r.reason
+
     def test_missing_field_fail(self, tmp_path):
         """Excerpt with start_line missing is now caught by schema
         validation at load time, before any of the 7 checks run -- not by

## Ground truth I measured (challenge it if you think it is wrong)

- 252 receipt files on disk under ~/code, 828 excerpts total.
  369 exact (content lines == declared span), 264 shorter, 195 longer.
- Declared lines 27567, content-backed 26418 = 95.8%.
- I REJECTED the alternative fix "reject any excerpt whose content does not
  fill its declared span" because it would reject 459 of 828 real excerpts.
- _excerpt_covered also feeds check 7 (Jaccard rubber-stamp detector, fails
  when overlap > 0.8). Measured 111 cycle-pairs on disk: 0 cross the 0.8
  boundary in either direction under this change.
- run_verify has ONE production caller (cli.py:1513) which always passes
  diff_text as a str; `hardened` is never passed False in src/. The legacy
  branch is dead in production.

## Attack these specifically

1. The arithmetic: `min(end, start + shown - 1)`. What happens when shown==0?
   when content is not a str? when start > end? when content has trailing
   newlines? Is there an off-by-one?
2. Does this WEAKEN check 7 in any case I have not measured? Construct a
   receipt pair that rubber-stamps but now passes.
3. Does splitlines() behave the way the code assumes for content with \r\n,
   a trailing \n, or a single line with no newline?
4. Is the new test test_wide_range_with_thin_content_earns_no_extra_coverage
   actually asserting what its docstring claims? Could it pass for the wrong
   reason?
5. Is there a THIRD consumer of _excerpt_covered or _cycle_excerpt_covered I
   have missed?
6. Can a fabricator still inflate coverage some other way after this fix?

Report findings with file:line and a concrete failing input. Do not invent
line numbers -- quote the actual code. If a claim of mine is wrong, say which.
