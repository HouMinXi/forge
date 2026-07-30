Round 2. Same fix, after acting on your round-1 findings.
Disposition of every round-1 finding, so you do not re-litigate:

F1 write_attestation uncaught -- AGREED, and independently confirmed
   zero callers before your review. NOT fixed here: it is dead code,
   guarding it would be speculative. Recorded for whoever revives it.
F2 f.name vs full path -- REJECTED. The receipts dir is always
   cwd/.code-forge/receipts, and cross_repo.py:409 already prefixes
   copied receipts with a repo label, so bare names stay distinct.
   Full paths would add noise to a one-line CLI message.
F3 RecursionError -- CONFIRMED BY EXPERIMENT, and it was NOT
   theoretical. A 100k-deep array escaped the guard and reproduced the
   exact crash the fix exists to prevent. FIXED: RecursionError added
   to the tuple; MemoryError deliberately left out as a resource
   condition, matching cli.py's existing 'except MemoryError: raise'.
   New test test_deeply_nested_json_reports_the_file, injection-proven
   (remove RecursionError from the tuple -> that test alone fails).
F4 redundant prefix -- ACCEPTED, now 'corrupt receipt: <name>: <why>'.

## What to attack now
1. Is the exception tuple NOW complete for read_text + json.loads?
   Name anything still escaping, or say it is complete.
2. Is excluding MemoryError right, or does it recreate the same
   unhandled-crash-on-a-bad-file problem for a large receipt?
3. Do the 6 tests assert what they claim, or is any of them weaker
   than its name?
4. Anything in the diff that is NOT about this defect.

## Current full diff
```diff
diff --git a/src/code_forge/errors.py b/src/code_forge/errors.py
index a32ba43..29fad1c 100644
--- a/src/code_forge/errors.py
+++ b/src/code_forge/errors.py
@@ -23,6 +23,16 @@ class CorruptedSnapshotError(Exception):
     """Raised when snapshot file is corrupt or unparseable."""
 
 
+class CorruptedReceiptError(Exception):
+    """Raised when a review receipt file is corrupt or unparseable.
+
+    Carries the offending filename. Receipts are the attestation that
+    review passes ran, so an unreadable one is reported rather than
+    skipped: skipping would silently turn tampering into a lower
+    receipt count.
+    """
+
+
 class CliError(Exception):
     """Raised on invalid CLI args or env values.
 
diff --git a/src/code_forge/verify.py b/src/code_forge/verify.py
index 4b3c49e..d4ea3a3 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -22,6 +22,7 @@ from itertools import combinations
 from pathlib import Path
 
 from .diff import _extract_post_image_lines, parse_diff_hunks
+from .errors import CorruptedReceiptError
 
 logger = logging.getLogger(__name__)
 
@@ -56,9 +57,30 @@ def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
 
 
 def _load_receipts(rd: Path) -> list[dict]:
+    """Load every receipt-*.json in rd.
+
+    Raises CorruptedReceiptError naming the file when one cannot be read
+    or parsed. Unreadable receipts are not skipped: the count and the
+    cycle/pass matrix are themselves checks, so dropping a file would
+    report a corrupt receipt as a missing one and hide the real cause.
+    """
     if not rd.exists():
         return []
-    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(rd.glob("receipt-*.json"))]
+    receipts = []
+    for f in sorted(rd.glob("receipt-*.json")):
+        try:
+            receipts.append(json.loads(f.read_text(encoding="utf-8")))
+        except (
+            json.JSONDecodeError,
+            OSError,
+            UnicodeDecodeError,
+            RecursionError,
+        ) as exc:
+            # RecursionError comes from json.loads on deeply nested input and
+            # is not a ValueError, so it needs naming. MemoryError is left
+            # uncaught on purpose: that is a resource condition, not a bad file.
+            raise CorruptedReceiptError("%s: %s" % (f.name, exc)) from exc
+    return receipts
 
 
 def _covered(receipt: dict) -> set[tuple[str, int]]:
@@ -110,8 +132,11 @@ def run_verify(
     hardened: bool = True,
     diff_text: str | None = None,
 ) -> VerifyResult:
-    receipts = _load_receipts(cwd / ".code-forge" / "receipts")
     cp = 0
+    try:
+        receipts = _load_receipts(cwd / ".code-forge" / "receipts")
+    except CorruptedReceiptError as exc:
+        return VerifyResult(False, "corrupt receipt: %s" % exc, 1, cp)
 
     # 1. completeness: 9 receipts, cycle/pass matrix, findings_count
     # Known design constraint: expects exactly cycles 1-3 x passes 1-3.
diff --git a/tests/test_verify.py b/tests/test_verify.py
index b862c4f..cf03e4d 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -88,6 +88,81 @@ class TestVerifyChecks:
         assert not r.passed
 
 
+class TestCorruptReceipt:
+    """A receipt that cannot be parsed must fail verify, not crash it.
+
+    The real incident: one receipt held a raw newline inside a JSON
+    string value, so every code commit in the repo aborted on an
+    unhandled JSONDecodeError while the hook reported "receipt
+    verification failed" -- pointing the operator at the review
+    instead of at the file.
+    """
+
+    def _nine_with_one_broken(self, tmp_path, broken_text):
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_all(rd, sha)
+        (rd / "receipt-c2p1.json").write_text(broken_text, encoding="utf-8")
+        return sha
+
+    def test_raw_control_char_reports_the_file(self, tmp_path):
+        # Verbatim shape of the incident: unescaped newline in a value.
+        broken = '{\n  "cycle": 2,\n  "pass": 1,\n  "skill": "qodo-review\ncode-review-expert"\n}\n'
+        sha = self._nine_with_one_broken(tmp_path, broken)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_corrupt_is_not_reported_as_missing(self, tmp_path):
+        # Guards the tempting wrong fix: skipping a bad file would leave
+        # 8 receipts and blame "missing receipts", hiding the corruption.
+        sha = self._nine_with_one_broken(tmp_path, "{ not json at all")
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "missing receipts" not in r.reason
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_truncated_json_reports_the_file(self, tmp_path):
+        sha = self._nine_with_one_broken(tmp_path, '{"cycle": 2, "pass":')
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_undecodable_bytes_report_the_file(self, tmp_path):
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        sha = _sha("diff")
+        _write_all(rd, sha)
+        (rd / "receipt-c2p1.json").write_bytes(b"\xff\xfe\x00binary")
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_deeply_nested_json_reports_the_file(self, tmp_path):
+        # json.loads raises RecursionError here, which is not a ValueError
+        # and so is not covered by JSONDecodeError. Without naming it the
+        # guard leaks the very crash it exists to prevent.
+        deep = "[" * 100000 + "]" * 100000
+        sha = self._nine_with_one_broken(tmp_path, deep)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_intact_receipts_still_pass(self, tmp_path):
+        # The guard must not reject a healthy set.
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        (tmp_path / "src").mkdir()
+        (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+        sha = _sha("diff")
+        _write_all(rd, sha)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert r.passed
+
+
 class TestReceiptVerifyE2E:
     """End-to-end: receipt writer output must pass verify checks."""
 
```
