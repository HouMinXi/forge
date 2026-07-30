# Round 5 review: receipt-load crash guard (redesigned)

You are the fifth independent reviewer of one defensive fix in `forge` (a
Python code-review tool). **Read this whole page before the diff.** Four rounds
already ran. Rounds 1-3 reviewed a DIFFERENT implementation and their results
do not transfer. Round 4 cleared every axis it was given -- your job is to find
what a clean round missed, and to check round 4's own load-bearing claims
rather than inherit them.

Worktree: `/home/houminxi/code/forge/.worktrees/fix-receipt-crash`
(branch `fix/receipt-load-crash`, base `f7bd6ad`). Python here is **3.14.6**.

**Path gotcha that has now caught two people, including round 4.** This repo is
pip-installed editable from the MAIN worktree, so a plain `python3 -c "import
code_forge"` silently imports the OLD `verify.py` with no schema at all. Force
`PYTHONPATH=/home/houminxi/code/forge/.worktrees/fix-receipt-crash/src` and
verify with `python3 -c "import code_forge; print(code_forge.__file__)"` before
trusting any result.

You may read files and run experiments. You may edit a file to run an injection
test, but you MUST restore it exactly, MUST NOT `git add`, `git commit`,
`git stash`, or `git reset`, and MUST report every file you touched plus
`git status --porcelain` (expected: exactly three `M ` entries).

## The incident this fix exists for

Two receipt files in the real repo (`receipt-c2p1.json`, `receipt-c3p1.json`)
contain a raw unescaped newline inside a JSON string. `_load_receipts` had no
guard, so every commit in the repo aborted with a raw `JSONDecodeError`
traceback. The operator was pointed at the review when the problem was a file.

Three requirements, in tension, all mandatory:

- A bad receipt becomes a **reported failure naming the file**, never a crash.
- A bad receipt must NOT disappear. Skipping it would leave 8 receipts and
  blame "missing receipts", converting tampering into a lower count.
- **A healthy receipt is never rejected.** This one has been violated twice
  already in this fix's history. It gates every commit in the repo, so a
  false rejection is a repo-wide outage.

## Design history, because it changed mid-review

Rounds 1-3 reviewed a narrow `try/except (ValueError, OSError, RecursionError)`
around `json.loads`. Round 3 found that a receipt which is valid JSON but not
an object parses fine and then crashes a later check. Patching that at its call
site exposed the real problem: 6+ checks index straight into receipt fields,
and guarding them one at a time produced a regression. The approach was
replaced: validate the shape ONCE at load (`_validate_receipt_schema`), then
let the checks use plain access. **That validator is what you are reviewing.**

## Disposition of every prior finding

Round 1 (deepseek): `write_attestation` lacks a guard -- AGREED, dead code
(zero callers), out of scope. Full-path in the message -- REJECTED, directory
is fixed. `RecursionError` escapes the tuple -- ACCEPTED after I built the
repro. Message prefix reads redundantly -- ACCEPTED, fixed.

Round 2 (kimi K2.7): oversized int literal raises plain `ValueError`, not
`JSONDecodeError` -- CONFIRMED by independent repro, fixed by catching
`ValueError` itself; this superseded round 1, whose subclass enumeration was
the root error. Excluding `MemoryError` -- AGREED. Missing tests -- ACCEPTED,
added.

Round 3 (glm): four assigned axes CLEAN. Plus F5, a valid-JSON non-dict receipt
still crashes with raw `AttributeError` -- ACCEPTED, triggered the redesign.

Round 4 (LongCat): all five assigned axes CLEAN, no findings. It also corrected
me twice, and **both corrections were verified and both stand**:

| # | Round 4's correction | Verified outcome |
|---|---|---|
| C1 | My brief claimed the diff "deletes defensive guards from `_covered`". False. | **Round 4 is right.** `git diff` on `_covered` shows only added comment lines, zero `-` lines. The guard I thought I removed was one I had added earlier in the same uncommitted session; net change vs base is zero. |
| C2 | My "accepted=14" receipt count did not match its measured 12. | **Scoping difference, both correct.** Main repo root has 18 receipt files, the worktree has 12. It counted from the worktree, I counted from main. The substantive claim (0 rejected) holds under both. |

Found by me, not by any reviewer:

| # | Finding | Disposition |
|---|---------|-------------|
| P1 | Deleting `OSError` from the tuple broke no test -- fully uncovered element | CONFIRMED reachable (`glob` returns directories; `read_text` raises `IsADirectoryError`). Test added. |
| P2 | **Self-caught regression.** An early per-call-site draft replaced a non-list `anchors` with `[]` instead of reporting it, silently turning a corrupt receipt into a PASS | Fixed by the redesign. Regression test added. |
| P3 | **Self-caught regression, second one.** The schema was derived from `write_receipts()` instead of from receipts on disk. `covered_line_ranges` has two real shapes -- `{"file","start","end"}` and the string `"SKILL.md:1-1400"` -- so the validator **rejected 11 of 14 real receipts**, which would have failed every commit: the original outage from the other direction | Fixed by removing `covered_line_ranges` from the schema. Regression test added for both shapes. |

## Ground truth, measured on this host

- `_validate_receipt_schema` against every real receipt: **0 rejected**
  (14 accepted counting from the main repo, 10 counting from the worktree --
  see C2), plus the known-unparseable incident files, which correctly trip the
  `json.loads` guard rather than the schema.
- `covered_line_ranges` is excluded from the schema on purpose. `_covered()`
  indexes the dict shape and raises `TypeError` on the string shape -- **and
  does so on `main` too**. Pre-existing; deliberately not fixed inline.
- That is safe only because `_covered` is unreachable in production:
  `run_verify(..., hardened: bool = True, ...)`, `grep -n "hardened"
  src/code_forge/cli.py` returns **nothing**, and the sole production caller
  (`cli.py:1513`) always passes `diff_text`.
- Independent field-access enumeration (mine, not round 4's): the fields
  indexed anywhere in `verify.py` are anchors, code_excerpts, content, cycle,
  end, end_line, file, findings, findings_count, pass, start, start_line,
  timestamp. Every one is asserted by the schema **except `start` and `end`**,
  which appear only inside `_covered`.
- `bool` is excluded from the int check on purpose: `bool` subclasses `int` and
  `hash(True) == hash(1)`, so `"cycle": true` would otherwise collide with a
  legitimate `(1,1)` receipt key instead of being caught.
- `tests/test_verify.py` 46 passed. Full suite 2980 passed, 8 skipped, exit 0.
  ruff clean, non-ASCII gate empty.

## Attack these directions

Round 4 was clean, so do not simply re-run its checklist and agree. One clean
round on a design that has already produced two self-caught regressions is not
convergence.

1. **Re-attack "is the schema too strict", independently.** This is the axis
   that drew blood twice and the one with a repo-wide blast radius. Round 4
   said `git log -p src/code_forge/receipt.py` shows the field shapes never
   varied. **Verify that claim yourself** rather than accept it. Consider
   shapes the current writer cannot produce but a real receipt could have:
   older versions, hand-edited files, receipts written by a different tool or
   a different forge version, an interrupted write. Any healthy-receipt
   rejection is the highest-severity finding available here.

2. **Attack the tests, not just the code.** Nobody has reviewed the test
   suite adversarially yet. For each new test in `TestReceiptSchema`, does it
   actually fail when the behavior it names is broken? Break the specific
   mechanism and watch it fail, then restore. A test that passes for the wrong
   reason is how P2 survived an early draft. Pay attention to whether
   `test_real_covered_line_ranges_shapes_are_accepted` asserts anything at all
   meaningful, since it calls the validator directly rather than through
   `run_verify`.

3. **Audit round 4's clean verdict.** It claimed every indexed field is
   asserted, and that eager validation does not mask tampering. Pick the claim
   you find weakest and try to break it. If round 4 was right, say so plainly.

4. **The error contract.** Every failure path returns
   `VerifyResult(False, "corrupt receipt: <file>: <detail>", 1, cp)`. Can any
   `<detail>` leak something unhelpful or misleading -- an internal type name,
   a truncated message, a path -- such that the operator's next action is
   wrong? Is `checks_run=1` honest when validation failed before any check ran?

5. **Anything written from an assumed spec rather than a verified one.** Both
   self-caught regressions came from deriving a specification from the wrong
   source. Find any remaining instance and say what you would measure.

If an axis is clean, say so plainly -- a clean axis is a real result. If you
find something, give me the experiment, not the reasoning. I re-run everything
reported, and I have already verified two of round 4's claims by hand.

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
index 4b3c49e..8a0cc01 100644
--- a/src/code_forge/verify.py
+++ b/src/code_forge/verify.py
@@ -22,6 +22,7 @@ from itertools import combinations
 from pathlib import Path
 
 from .diff import _extract_post_image_lines, parse_diff_hunks
+from .errors import CorruptedReceiptError
 
 logger = logging.getLogger(__name__)
 
@@ -55,13 +56,112 @@ def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
     return diff_files
 
 
+# The exact field shapes write_receipts() (receipt.py) always produces.
+# _validate_receipt_schema enforces these once, here, so the 7 checks in
+# run_verify can use plain dict/attribute access instead of each carrying
+# its own copy of the same defensive isinstance guards.
+_STR_FIELDS = ("diff_sha256", "timestamp")
+_INT_FIELDS = ("cycle", "pass", "findings_count")
+_LIST_OF_DICT_FIELDS = ("findings", "anchors", "code_excerpts")
+_NESTED_SCHEMAS = {
+    "code_excerpts": {"file": str, "content": str, "start_line": int, "end_line": int},
+}
+_TYPE_LABEL = {str: "a string", int: "an integer"}
+
+
+def _is_type(value, expected_type: type) -> bool:
+    if expected_type is int:
+        # bool subclasses int in Python; a stray JSON true/false must not
+        # silently pass a cycle/pass/findings_count check as 1/0.
+        return isinstance(value, int) and not isinstance(value, bool)
+    return isinstance(value, expected_type)
+
+
+def _validate_receipt_schema(obj: dict, name: str) -> None:
+    """Raise CorruptedReceiptError if obj's field types do not match what
+    the checks below actually index into: cycle/pass/findings_count are
+    int, diff_sha256/timestamp are str, and findings/anchors/code_excerpts
+    are lists of dicts (code_excerpts further checked field-by-field,
+    since the hardened excerpt check indexes straight into it). Checked
+    once here so none of the 7 checks in run_verify need to re-guard the
+    same shape at their own call site.
+
+    covered_line_ranges is deliberately NOT checked. Receipts on disk carry
+    it in two shapes -- {"file","start","end"} and the string form
+    "path:start-end" -- and asserting either one rejects real, healthy
+    receipts written by an older forge. Nothing on the production path
+    reads it: run_verify's caller always takes the hardened branch, which
+    treats the field as self-reported audit data and ignores it.
+    """
+    for field in _STR_FIELDS:
+        if not _is_type(obj.get(field), str):
+            raise CorruptedReceiptError(
+                "%s: %s must be %s" % (name, field, _TYPE_LABEL[str]))
+    for field in _INT_FIELDS:
+        if not _is_type(obj.get(field), int):
+            raise CorruptedReceiptError(
+                "%s: %s must be %s" % (name, field, _TYPE_LABEL[int]))
+    for field in _LIST_OF_DICT_FIELDS:
+        v = obj.get(field)
+        if not isinstance(v, list) or not all(isinstance(item, dict) for item in v):
+            raise CorruptedReceiptError(
+                "%s: %s must be a list of objects" % (name, field))
+    # Safe only because the loop above already proved each of these two
+    # fields is a list of dicts -- otherwise iterating a malformed
+    # code_excerpts/covered_line_ranges here would risk the exact
+    # "call .get() on a non-dict" crash this function exists to prevent.
+    for list_field, subschema in _NESTED_SCHEMAS.items():
+        for item in obj.get(list_field, []):
+            for subfield, subtype in subschema.items():
+                if not _is_type(item.get(subfield), subtype):
+                    raise CorruptedReceiptError(
+                        "%s: %s.%s must be %s" % (
+                            name, list_field, subfield, _TYPE_LABEL[subtype]))
+
+
 def _load_receipts(rd: Path) -> list[dict]:
+    """Load every receipt-*.json in rd.
+
+    Raises CorruptedReceiptError naming the file when one cannot be read,
+    cannot be parsed, does not hold a JSON object, or does not match the
+    receipt schema (see _validate_receipt_schema). Unreadable receipts
+    are not skipped: the count and the cycle/pass matrix are themselves
+    checks, so dropping a file would report a corrupt receipt as a missing
+    one and hide the real cause.
+    """
     if not rd.exists():
         return []
-    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(rd.glob("receipt-*.json"))]
+    receipts = []
+    for f in sorted(rd.glob("receipt-*.json")):
+        try:
+            obj = json.loads(f.read_text(encoding="utf-8"))
+        except (ValueError, OSError, RecursionError) as exc:
+            # Catch ValueError itself, not its subclasses: JSONDecodeError and
+            # UnicodeDecodeError both derive from it, and so does json.loads
+            # refusing an integer literal longer than
+            # sys.get_int_max_str_digits(). Naming the subclasses let that last
+            # one through. RecursionError (deeply nested input) is a
+            # RuntimeError and still needs naming. MemoryError is left uncaught
+            # on purpose: that is a resource condition, not a bad file.
+            raise CorruptedReceiptError("%s: %s" % (f.name, exc)) from exc
+        if not isinstance(obj, dict):
+            # Every check downstream calls .get() on these. A bare array or
+            # number parses cleanly and then crashes the caller with an
+            # AttributeError, so the annotation above is enforced here.
+            raise CorruptedReceiptError(
+                "%s: expected a JSON object, got %s" % (f.name, type(obj).__name__)
+            )
+        _validate_receipt_schema(obj, f.name)
+        receipts.append(obj)
+    return receipts
 
 
 def _covered(receipt: dict) -> set[tuple[str, int]]:
+    # Reached only from the legacy branch, which run_verify's production
+    # caller never takes. Unguarded on purpose: receipts carry
+    # covered_line_ranges in two shapes and this indexes the dict one, so
+    # it raises on the string form exactly as it did before this change.
+    # Left alone rather than fixed inline -- pre-existing, separate change.
     s = set()
     for r in receipt.get("covered_line_ranges", []):
         for ln in range(r["start"], r["end"] + 1):
@@ -110,8 +210,11 @@ def run_verify(
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
@@ -128,11 +231,11 @@ def run_verify(
         return VerifyResult(False, msg, 1, cp)
     seen_keys = set()
     for r in receipts:
-        key = (r.get("cycle"), r.get("pass"))
+        key = (r["cycle"], r["pass"])
         if key in seen_keys:
             return VerifyResult(False, "duplicate receipt c%dp%d" % key, 1, cp)
         seen_keys.add(key)
-        if r.get("findings_count") != len(r.get("findings", [])):
+        if r["findings_count"] != len(r["findings"]):
             return VerifyResult(
                 False, "findings_count mismatch c%dp%d" % key, 1, cp)
     expected = {(c, p) for c in range(1, 4) for p in range(1, 4)}
@@ -148,7 +251,7 @@ def run_verify(
 
     # 3. anchors: file must be in diff
     for r in receipts:
-        for a in r.get("anchors", []):
+        for a in r["anchors"]:
             afile = a.get("file", "")
             if afile not in diff_files:
                 return VerifyResult(False, "anchor file %s not in diff" % afile, 3, cp)
diff --git a/tests/test_verify.py b/tests/test_verify.py
index b862c4f..5638653 100644
--- a/tests/test_verify.py
+++ b/tests/test_verify.py
@@ -1,7 +1,9 @@
 import hashlib
 import json
 from pathlib import Path
-from code_forge.verify import run_verify, parse_diff_files
+
+import pytest
+from code_forge.verify import run_verify, parse_diff_files, _validate_receipt_schema
 
 
 def _sha(text: str) -> str:
@@ -37,6 +39,23 @@ def _write_all(rd, diff_sha, vary=True):
             ))
 
 
+def _nine_with_one_field_set(tmp_path, field, value):
+    """Write 9 valid receipts, then set one top-level field on c2p1 to
+    value -- which may be any JSON-serializable type, not just the
+    schema-correct one. Proves a schema violation is reported by name,
+    not crashed past and not silently accepted."""
+    rd = tmp_path / ".code-forge" / "receipts"
+    rd.mkdir(parents=True)
+    (tmp_path / "src").mkdir()
+    (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n")
+    sha = _sha("diff")
+    _write_all(rd, sha)
+    bad = _receipt(2, 1, sha)
+    bad[field] = value
+    (rd / "receipt-c2p1.json").write_text(json.dumps(bad))
+    return sha
+
+
 class TestVerifyChecks:
     def test_pass_complete(self, tmp_path):
         rd = tmp_path / ".code-forge" / "receipts"
@@ -88,6 +107,217 @@ class TestVerifyChecks:
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
+    @pytest.mark.parametrize("body", ["[1, 2, 3]", "42", '"a string"', "null", "true"])
+    def test_non_object_json_reports_the_file(self, tmp_path, body):
+        # These parse cleanly, so no exception guard sees them. Every check
+        # downstream then calls .get() on the result and dies with an
+        # AttributeError pointing into verify.py instead of at the file.
+        sha = self._nine_with_one_broken(tmp_path, body)
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
+class TestReceiptSchema:
+    """A receipt with a field of the wrong type must fail verify by name,
+    not crash it and not silently pass. Schema validation is the single
+    gate every one of the 7 checks in run_verify trusts, instead of each
+    check carrying its own copy of the same defensive guard.
+
+    Two of these are regression guards for mistakes made while building
+    this fix: an early draft replaced a non-list anchors field with []
+    instead of reporting it (turning corrupt data into a false pass), and
+    a later draft derived the schema from the writer rather than from the
+    receipts on disk, so it rejected every real receipt whose
+    covered_line_ranges used the string shape.
+    """
+
+    @pytest.mark.parametrize("field,value,expected", [
+        ("cycle", [2], "cycle must be an integer"),
+        ("cycle", True, "cycle must be an integer"),
+        ("pass", "1", "pass must be an integer"),
+        ("timestamp", None, "timestamp must be a string"),
+        ("timestamp", 123, "timestamp must be a string"),
+        ("diff_sha256", 12345, "diff_sha256 must be a string"),
+        ("findings_count", "0", "findings_count must be an integer"),
+        ("findings", "not a list", "findings must be a list of objects"),
+        ("findings", [1, 2], "findings must be a list of objects"),
+        ("anchors", "not a list", "anchors must be a list of objects"),
+        ("anchors", [1], "anchors must be a list of objects"),
+        ("code_excerpts", "not a list", "code_excerpts must be a list of objects"),
+        ("code_excerpts", [1, 2, 3], "code_excerpts must be a list of objects"),
+    ])
+    def test_malformed_top_level_field_reports_the_file(
+        self, tmp_path, field, value, expected
+    ):
+        sha = _nine_with_one_field_set(tmp_path, field, value)
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert r.reason.startswith("corrupt receipt: ")
+        assert "receipt-c2p1.json" in r.reason
+        assert expected in r.reason
+
+    @pytest.mark.parametrize("list_field,item,expected", [
+        ("code_excerpts",
+         {"file": 5, "start_line": 1, "end_line": 1, "content": "x"},
+         "code_excerpts.file must be a string"),
+        ("code_excerpts",
+         {"file": "x.py", "start_line": "1", "end_line": 1, "content": "x"},
+         "code_excerpts.start_line must be an integer"),
+        ("code_excerpts",
+         {"file": "x.py", "start_line": 1, "end_line": None, "content": "x"},
+         "code_excerpts.end_line must be an integer"),
+        ("code_excerpts",
+         {"file": "x.py", "start_line": 1, "end_line": 1, "content": 5},
+         "code_excerpts.content must be a string"),
+    ])
+    def test_malformed_nested_field_reports_the_file(
+        self, tmp_path, list_field, item, expected
+    ):
+        sha = _nine_with_one_field_set(tmp_path, list_field, [item])
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert r.reason.startswith("corrupt receipt: ")
+        assert "receipt-c2p1.json" in r.reason
+        assert expected in r.reason
+
+    def test_malformed_anchors_no_longer_silently_passes(self, tmp_path):
+        sha = _nine_with_one_field_set(tmp_path, "anchors", "not a list")
+        r = run_verify(tmp_path, sha, {"src/f.py": list(range(1, 51))})
+        assert not r.passed
+        assert r.reason != "all 7 checks passed"
+
+    @pytest.mark.parametrize("ranges", [
+        [{"file": "src/code_forge/llm_invoke.py", "start": 143, "end": 151}],
+        ["SKILL.md:1-1400"],
+        [],
+    ])
+    def test_real_covered_line_ranges_shapes_are_accepted(self, tmp_path, ranges):
+        """Receipts on disk carry covered_line_ranges in both a dict shape and
+        a "path:start-end" string shape. An earlier draft of the schema
+        asserted the dict shape and rejected 11 of the 14 real receipts in
+        this repo -- turning every commit into a corrupt-receipt failure, the
+        same outage this fix exists to prevent, from the other direction.
+        Nothing on the production path reads the field, so the schema must
+        accept whatever is in it. Asserted against the gate itself: driving
+        this through run_verify without diff_text would take the legacy
+        branch into _covered(), whose crash on the string shape predates
+        this change and is left alone here.
+        """
+        receipt = _receipt(2, 1, "abc")
+        receipt["covered_line_ranges"] = ranges
+        _validate_receipt_schema(receipt, "receipt-c2p1.json")
+
+    def test_intact_receipts_still_pass_schema(self, tmp_path):
+        """The schema gate must not reject a healthy set."""
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
 
@@ -287,14 +517,17 @@ class TestHardenedVerify:
         assert "< 60%" in r.reason
 
     def test_missing_field_fail(self, tmp_path):
-        """STEP 0: excerpt with start_line missing -> excerpt missing required fields."""
+        """Excerpt with start_line missing is now caught by schema
+        validation at load time, before any of the 7 checks run -- not by
+        STEP 0 inside check 5. STEP 0 stays in place as defense in depth
+        but can no longer be reached by this particular input."""
         rd = self._rd(tmp_path)
         sha = _sha(_HARDEN_DIFF)
         diff_files = parse_diff_files(_HARDEN_DIFF)
         bad = [
             {"file": "foo.py", "start_line": 1, "end_line": 3,
              "content": "x = 1\ny = 2\nz = 3"},
-            # Missing start_line -- STEP 0 rejects before STEP A.
+            # Missing start_line -- rejected by schema validation.
             {"file": "foo.py", "end_line": 8, "content": "a = 1\nb = 2\nc = 3"},
             {"file": "bar.py", "start_line": 1, "end_line": 3,
              "content": "p = 1\nq = 2\nr = 3"},
@@ -302,4 +535,5 @@ class TestHardenedVerify:
         _write_hardened(rd, sha, excerpts=bad)
         r = run_verify(tmp_path, sha, diff_files, diff_text=_HARDEN_DIFF)
         assert not r.passed
-        assert "excerpt missing required fields" in r.reason
+        assert r.reason.startswith("corrupt receipt: ")
+        assert "code_excerpts.start_line must be an integer" in r.reason
```
