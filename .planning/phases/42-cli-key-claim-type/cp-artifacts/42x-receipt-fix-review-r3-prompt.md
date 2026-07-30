# Round 3 review: receipt-load crash guard

You are the third independent reviewer of one small defensive fix in
`forge` (a Python code-review tool). Two rounds already ran, each found a
real defect, and each defect was of a kind the previous round could not
see. Read the disposition table below before reading the diff: re-raising
a settled point without new substance is noise, and the rounds are not
converging yet, so I need you attacking the directions nobody has attacked.

Worktree: `/home/houminxi/code/forge/.worktrees/fix-receipt-crash`
(branch `fix/receipt-load-crash`, base `f7bd6ad`). Python on this host is
**3.14.6**, `sys.get_int_max_str_digits()` is 4300.

You may read files and run experiments in that worktree. You may edit a
file to run an injection test, but you MUST restore it exactly, MUST NOT
`git add`, `git commit`, `git stash`, or `git reset`, and MUST report at
the end every file you touched plus the output of `git diff` (expected:
empty) and `git status --porcelain` (expected: exactly the three `M `
entries below).

## The incident this fix exists for

Two receipt files in the real repo (`receipt-c2p1.json`,
`receipt-c3p1.json`, written 2026-06-01) contain a raw unescaped newline
inside a JSON string value. `_load_receipts` had no guard, so every code
commit in the repo aborted: the pre-commit hook printed "receipt
verification failed. Run: code-forge verify", and running that command
produced a raw `JSONDecodeError` traceback. The operator was pointed at
the review when the problem was a file.

The fix must make a bad receipt a **reported failure naming the file**,
never a crash. It must NOT make a bad receipt disappear: skipping the file
would leave 8 receipts and blame "missing receipts", converting tampering
into a lower count.

## Disposition of every prior finding

Round 1 (deepseek):

| # | Finding | Disposition |
|---|---------|-------------|
| F1 | `write_attestation` (verify.py:403) calls `_load_receipts` with no try/except | AGREED, NOT FIXED. Repo-wide grep confirms zero callers -- dead code. Recorded, out of scope for this change. |
| F2 | error message uses `f.name` not the full path | REJECTED. The directory is always `cwd/.code-forge/receipts`; the reviewer itself rated it informational. |
| F3 | `RecursionError` from deeply nested JSON escapes the tuple | Reviewer rated it "theoretical, very low". I built the repro (`"["*100000 + "]"*100000`), it escaped, so the SEVERITY framing was rejected and the FINDING accepted. Fixed by naming `RecursionError`. |
| F4 | `"corrupt receipt %s"` prefix reads redundantly | ACCEPTED, fixed to `"corrupt receipt: %s"`. |

Round 2 (kimi):

| # | Finding | Disposition |
|---|---------|-------------|
| Q1 | `json.loads` on an integer literal past `int_max_str_digits` raises a **plain `ValueError`**, not `JSONDecodeError`, so it escaped the round-1 tuple | CONFIRMED by my own independent repro, end to end through `run_verify`, in two forms (whole-file literal and an oversized `findings_count` field). Fixed by catching `ValueError` itself. This supersedes round 1's approach: enumerating subclasses was the root error, and F3 only patched one symptom of it. |
| Q2 | excluding `MemoryError` is correct | AGREED, no change. Precedent verified at `cli.py:1833`. |
| Q3 | no oversized-int test; nothing asserts the `"corrupt receipt"` prefix | BOTH ACCEPTED, both tests added. |
| Q4 | diff has no out-of-scope content | AGREED, no change. |

Found by me, not by either reviewer, via injection:

| # | Finding | Disposition |
|---|---------|-------------|
| P1 | Deleting `OSError` from the tuple broke **no test at all** -- that element was entirely uncovered | CONFIRMED reachable: `glob` returns directories too, and `read_text` on one raises `IsADirectoryError`, an `OSError` and not a `ValueError`. Fixed by adding `test_unreadable_entry_reports_the_file`. |

## Ground truth established, do not re-derive unless you doubt it

- `issubclass(json.JSONDecodeError, ValueError)` -> True
- `issubclass(UnicodeDecodeError, ValueError)` -> True
- `issubclass(RecursionError, ValueError)` -> False; `issubclass(RecursionError, RuntimeError)` -> True
- `issubclass(OSError, ValueError)` -> False
- Injection matrix on the final tuple, each run against
  `tests/test_verify.py::TestCorruptReceipt` (8 tests):
  - drop `ValueError` (round-1 tuple) -> 1 failed: `test_oversized_int_reports_the_file`
  - drop `RecursionError` -> 1 failed: `test_deeply_nested_json_reports_the_file`
  - drop `OSError` -> 1 failed: `test_unreadable_entry_reports_the_file`
  - `except ()` (guard removed) -> 7 failed, 1 passed (the healthy-set test)
- Real path, worktree code against the real corrupt receipts:
  `code-forge verify` exits 1 and prints
  `verify: FAIL -- corrupt receipt: receipt-c2p1.json: Invalid control
  character at: line 4 column 24 (char 52)`; `--quiet` exits 1 with empty
  stdout. Before the fix the same command produced a traceback.
- `run_verify` has exactly one production caller: `cli.py:1513`.
  `_load_receipts` has exactly two: `run_verify` (line 136) and the dead
  `write_attestation` (line 403).
- ruff clean, `py_compile` clean, no non-ASCII in the diff.

## Attack these four directions

Both prior rounds attacked the same axis: what still ESCAPES the guard.
That axis looks worked out. Attack the axes nobody has.

1. **Is the catch now too broad?** The tuple went from four named
   subclasses to `(ValueError, OSError, RecursionError)`. Inside that
   `try` block, can a `ValueError` arise that is NOT a property of the
   file's content -- i.e. a bug in forge's own code, or an environment
   condition -- which the guard would now mislabel as "corrupt receipt:
   <file>"? A wrong diagnosis printed confidently is worse than a
   traceback. Enumerate what can actually raise inside those two calls.

2. **Is `test_unreadable_entry_reports_the_file` portable?** It creates a
   *directory* named `receipt-c2p1.json` and relies on `read_text` raising
   `IsADirectoryError`. Does that hold on every platform this project
   claims to support? Check whether the repo claims Windows support before
   answering, and say what the test does there if it does not hold.

3. **Does the early return break the caller contract?** The new path
   returns `VerifyResult(False, "corrupt receipt: ...", 1, cp)` with
   `checks_run=1`. Read `cli.py:1513` and every other consumer of a
   `VerifyResult`. Does anything read `checks_run`/`checks_passed`
   numerically -- a ratio, a percentage, a log line, an attestation --
   in a way this early return makes wrong or misleading?

4. **Is `CorruptedReceiptError` caught in the right places, and only
   there?** It is a new exception type crossing a module boundary. Trace
   every path that can now raise it and every handler that could swallow
   it (including broad `except Exception` handlers anywhere upstream of
   `cli.py:1513`). Does any of them turn a loud failure back into a quiet
   one?

If you find nothing on an axis, say so plainly -- a clean axis is a real
result and I will not read it as laziness. If you find something, give me
the experiment that shows it, not the reasoning that suggests it. I will
re-run whatever you report, so a claim I cannot reproduce costs us both a
round.

## The diff under review

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
index 4b3c49e..1bc334f 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -22,6 +22,7 @@ from itertools import combinations
 from pathlib import Path
 
 from .diff import _extract_post_image_lines, parse_diff_hunks
+from .errors import CorruptedReceiptError
 
 logger = logging.getLogger(__name__)
 
@@ -56,9 +57,29 @@ def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
 
 
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
+        except (ValueError, OSError, RecursionError) as exc:
+            # Catch ValueError itself, not its subclasses: JSONDecodeError and
+            # UnicodeDecodeError both derive from it, and so does json.loads
+            # refusing an integer literal longer than
+            # sys.get_int_max_str_digits(). Naming the subclasses let that last
+            # one through. RecursionError (deeply nested input) is a
+            # RuntimeError and still needs naming. MemoryError is left uncaught
+            # on purpose: that is a resource condition, not a bad file.
+            raise CorruptedReceiptError("%s: %s" % (f.name, exc)) from exc
+    return receipts
 
 
 def _covered(receipt: dict) -> set[tuple[str, int]]:
@@ -110,8 +131,11 @@ def run_verify(
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
index b862c4f..8958d2e 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -88,6 +88,105 @@ class TestVerifyChecks:
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
+        assert r.reason.startswith("corrupt receipt: ")
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
+        # json.loads raises RecursionError here, a RuntimeError that no
+        # ValueError catch covers. Without naming it the guard leaks the
+        # very crash it exists to prevent.
+        deep = "[" * 100000 + "]" * 100000
+        sha = self._nine_with_one_broken(tmp_path, deep)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_oversized_int_reports_the_file(self, tmp_path):
+        # Past sys.get_int_max_str_digits() json.loads raises a plain
+        # ValueError, not a JSONDecodeError, so catching only the named
+        # subclasses let a single receipt abort verify with a traceback.
+        big = '{"cycle": 2, "pass": 1, "findings_count": ' + "9" * 5000 + "}"
+        sha = self._nine_with_one_broken(tmp_path, big)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert "receipt-c2p1.json" in r.reason
+
+    def test_unreadable_entry_reports_the_file(self, tmp_path):
+        # glob returns directories too, and read_text on one raises
+        # IsADirectoryError -- an OSError, outside the ValueError branch.
+        rd = tmp_path / ".code-forge" / "receipts"
+        rd.mkdir(parents=True)
+        sha = _sha("diff")
+        _write_all(rd, sha)
+        (rd / "receipt-c2p1.json").unlink()
+        (rd / "receipt-c2p1.json").mkdir()
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
