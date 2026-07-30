You are reviewing a defect fix in code-forge, a Python code-review tool.
Be adversarial. Report real defects only, with file:line. If you find
nothing, say so plainly -- do not invent findings.

## Incident being fixed
Two receipt files on disk contained a raw newline inside a JSON string
value. verify.py's _load_receipts had no error handling, so ONE bad file
crashed every code commit in the repo with an unhandled JSONDecodeError,
while the git hook reported 'receipt verification failed' -- pointing the
operator at the review rather than at the file.

## Design decision to attack
Malformed receipts are reported (raise + catch -> VerifyResult(False)),
NOT skipped. Rationale: the receipt COUNT and the cycle/pass matrix are
themselves verify checks, so dropping an unreadable file would report
corruption as absence ('missing receipts: 8/9').
Sibling runtime.py:153 DOES skip+warn, deliberately, because smoke
receipts are best-effort. Is that distinction sound here?

## Questions
1. Can the guard be bypassed, or does any path still reach a raw crash?
2. Is the exception set (JSONDecodeError, OSError, UnicodeDecodeError)
   complete for read_text + json.loads on an attacker-controlled file?
3. write_attestation (verify.py, ~line 386) also calls _load_receipts
   and does NOT catch. Reachable? It has no callers in the repo.
4. Do the tests actually fail if the fix is removed, or do they assert
   something weaker than they claim?

## The diff
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
index 4b3c49e..db8eefa 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -22,6 +22,7 @@ from itertools import combinations
 from pathlib import Path
 
 from .diff import _extract_post_image_lines, parse_diff_hunks
+from .errors import CorruptedReceiptError
 
 logger = logging.getLogger(__name__)
 
@@ -56,9 +57,22 @@ def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
 
 
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
+        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
+            raise CorruptedReceiptError("%s: %s" % (f.name, exc)) from exc
+    return receipts
 
 
 def _covered(receipt: dict) -> set[tuple[str, int]]:
@@ -110,8 +124,11 @@ def run_verify(
     hardened: bool = True,
     diff_text: str | None = None,
 ) -> VerifyResult:
-    receipts = _load_receipts(cwd / ".code-forge" / "receipts")
     cp = 0
+    try:
+        receipts = _load_receipts(cwd / ".code-forge" / "receipts")
+    except CorruptedReceiptError as exc:
+        return VerifyResult(False, "corrupt receipt %s" % exc, 1, cp)
 
     # 1. completeness: 9 receipts, cycle/pass matrix, findings_count
     # Known design constraint: expects exactly cycles 1-3 x passes 1-3.
diff --git a/tests/test_verify.py b/tests/test_verify.py
index b862c4f..84cd487 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -88,6 +88,71 @@ class TestVerifyChecks:
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
