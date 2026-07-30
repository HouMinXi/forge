# Round 4 review: receipt-load crash guard (redesigned)

You are the fourth independent reviewer of one defensive fix in `forge`
(a Python code-review tool). Three rounds already ran. **Read this whole
page before the diff** -- the design CHANGED after round 3, so the earlier
rounds reviewed a different implementation, and re-raising a settled point
without new substance costs us both a round.

Worktree: `/home/houminxi/code/forge/.worktrees/fix-receipt-crash`
(branch `fix/receipt-load-crash`, base `f7bd6ad`). Python here is **3.14.6**.

You may read files and run experiments in that worktree. You may edit a file
to run an injection test, but you MUST restore it exactly, MUST NOT
`git add`, `git commit`, `git stash`, or `git reset`, and MUST report at the
end every file you touched plus `git diff --stat` and `git status --porcelain`
(expected: exactly three `M ` entries, errors.py / verify.py / test_verify.py).

## The incident this fix exists for

Two receipt files in the real repo (`receipt-c2p1.json`, `receipt-c3p1.json`,
written 2026-06-01) contain a raw unescaped newline inside a JSON string.
`_load_receipts` had no guard, so every commit in the repo aborted: the
pre-commit hook printed "receipt verification failed", and `code-forge verify`
produced a raw `JSONDecodeError` traceback. The operator was pointed at the
review when the problem was a file.

Requirements, in tension, both mandatory:

- A bad receipt must become a **reported failure naming the file**, never a crash.
- A bad receipt must NOT disappear. Skipping the file would leave 8 receipts and
  blame "missing receipts", converting tampering into a lower count.
- **A healthy receipt must never be rejected.** Violating this recreates the
  original outage from the other direction. This one has already been violated
  twice in this fix's history (see P2 below) -- attack it hardest.

## What changed after round 3, and why

Rounds 1-3 reviewed a narrow `try/except (ValueError, OSError, RecursionError)`
around `json.loads`. Round 3 (glm) found F5: a receipt that is *valid JSON but
not an object* (e.g. `[1,2,3]`) parses fine and then crashes a later check with
a raw `AttributeError` -- a different code path and a different exception class
than anything rounds 1-2 covered.

Patching F5 at its call site exposed the real problem: there were 6+ places
where a check indexes straight into receipt fields, and guarding them one at a
time produced a regression (P2 below). So the approach was replaced: validate
the receipt's shape ONCE at load time (`_validate_receipt_schema`), then let
the 7 checks use plain unguarded dict access. **That validator is what you are
reviewing.** The try/except from rounds 1-3 is still there, unchanged, beneath it.

## Disposition of every prior finding

Round 1 (deepseek):

| # | Finding | Disposition |
|---|---------|-------------|
| F1 | `write_attestation` calls `_load_receipts` with no try/except | AGREED, NOT FIXED. Repo-wide grep confirms zero callers -- dead code. Out of scope, recorded. |
| F2 | error message uses `f.name` not full path | REJECTED. Directory is always `cwd/.code-forge/receipts`; reviewer itself rated it informational. |
| F3 | `RecursionError` from deeply nested JSON escapes the tuple | Reviewer rated "theoretical". I built the repro (`"["*100000`), it escaped. Severity framing rejected, FINDING accepted and fixed. |
| F4 | `"corrupt receipt %s"` reads redundantly | ACCEPTED, fixed to `"corrupt receipt: %s"`. |

Round 2 (kimi):

| # | Finding | Disposition |
|---|---------|-------------|
| Q1 | `json.loads` on an oversized int literal raises plain `ValueError`, not `JSONDecodeError` | CONFIRMED by independent repro end-to-end. Fixed by catching `ValueError` itself. Supersedes round 1: enumerating subclasses was the root error, F3 patched one symptom of it. |
| Q2 | excluding `MemoryError` is correct | AGREED, no change. Precedent at `cli.py:1833`. |
| Q3 | no oversized-int test; nothing asserts the prefix | BOTH ACCEPTED, tests added. |
| Q4 | diff has no out-of-scope content | AGREED. |

Round 3 (glm) -- all four assigned axes came back CLEAN (catch-too-broad,
test portability, caller contract, exception containment). Plus:

| # | Finding | Disposition |
|---|---------|-------------|
| F5 | a valid-JSON non-dict receipt still crashes with raw `AttributeError` | ACCEPTED. Triggered the redesign above. Now caught by the `isinstance(obj, dict)` check in `_load_receipts`. |

Found by me, not by any reviewer:

| # | Finding | Disposition |
|---|---------|-------------|
| P1 | Deleting `OSError` from the tuple broke **no test** -- that element was fully uncovered | CONFIRMED reachable: `glob` returns directories, `read_text` on one raises `IsADirectoryError`. Test added. |
| P2 | **Regression, self-caught.** An early per-call-site draft replaced a non-list `anchors` with `[]` instead of reporting it -- silently converting a corrupt receipt into a PASS | Fixed by the schema redesign. Permanent regression test added. |
| P3 | **Regression, self-caught, second one.** The schema was derived from `write_receipts()` instead of from the receipts on disk. `covered_line_ranges` exists in two real shapes -- `{"file","start","end"}` and the string `"SKILL.md:1-1400"` -- so the validator **rejected 11 of the 14 real receipts in this repo**, which would have failed every commit: the original outage, reproduced from the other direction | Fixed by removing `covered_line_ranges` from the schema entirely (see ground truth below). Regression test added asserting both real shapes are accepted. |

## Ground truth established, do not re-derive unless you doubt it

Every line below is measured on this host, not reasoned:

- `_validate_receipt_schema` against all real receipts in
  `.code-forge/receipts/` and `evidence/fabrication-receipts-20260601/`:
  **accepted=14, rejected=0**, plus the 4 known-unparseable incident files.
  (Before the P3 fix this was accepted=3, rejected=11.)
- `covered_line_ranges` really does hold both shapes on disk. `_covered()`
  indexes the dict shape, so on the string shape it raises
  `TypeError: string indices must be integers` -- **and it does so on `main`
  too**. Pre-existing, deliberately not fixed inline per project rule.
- `_covered` is reached only from the legacy branch. `run_verify(...,
  hardened: bool = True, ...)`; `grep -n "hardened" src/code_forge/cli.py`
  returns **nothing**, and the sole production call site
  (`cli.py:1513`) always passes `diff_text`. So the legacy branch, and
  therefore `covered_line_ranges`, is unreachable in production.
  `verify.py:269` documents the field as "self-reported, not measured --
  audit-only. Ignored here."
- Injection at the fix site: putting `covered_line_ranges` back into
  `_LIST_OF_DICT_FIELDS` makes
  `test_real_covered_line_ranges_shapes_are_accepted[ranges1]` fail with
  `CorruptedReceiptError`. Restored -> 46 passed.
- `bool` is excluded from the int check on purpose: `bool` subclasses `int`,
  and `hash(True) == hash(1)`, so a stray `"cycle": true` would otherwise
  collide with a legitimate `(1,1)` receipt key in check 1's set.
- `run_verify` has exactly one production caller: `cli.py:1513`.
  `_load_receipts` has two: `run_verify` and the dead `write_attestation`.
- `tests/test_verify.py`: 46 passed. Full project suite: 2980 passed, 8 skipped,
  exit 0. ruff clean. No non-ASCII in the diff.

## Attack these five directions

Round 3 cleared the "what escapes the guard" axis and the four axes it was
given. The design has since changed, so those results do not transfer. These
are the axes that match the new design.

1. **Is the schema still too strict somewhere else?** This is the axis that
   has already drawn blood twice (P2, P3), so give it the most time. The
   validator asserts shapes for `diff_sha256`, `timestamp`, `cycle`, `pass`,
   `findings_count`, `findings`, `anchors`, `code_excerpts`, and the nested
   fields of `code_excerpts`. For each: is there any real receipt -- on disk,
   in git history, or producible by an older `write_receipts()` -- that would
   now be rejected? Check `git log -p src/code_forge/receipt.py` for shape
   changes over time. A rejection of a healthy receipt is the highest-severity
   finding available in this review.

2. **Is the schema too loose?** The 7 checks now use unguarded access because
   they trust the validator. Enumerate every field access in `run_verify` and
   in the helpers it calls on the hardened path, and confirm each one is
   covered by `_STR_FIELDS`, `_INT_FIELDS`, `_LIST_OF_DICT_FIELDS`, or
   `_NESTED_SCHEMAS`. Any access to a field the validator does not assert is
   a crash waiting for the right malformed input -- name it with the input
   that reaches it. Pay attention to `skill` and `pass_status`, which appear
   in real receipts but in no schema tuple.

3. **Does validating eagerly hide the real diagnosis?** Validation now runs
   over the whole file at load time, before any of the 7 checks. A receipt
   that is both type-malformed and semantically tampered will now report the
   type error. Does that mask any tamper the checks would otherwise have
   named, in a way that makes the operator's next action wrong?

4. **Did removing the per-check guards leave a hole?** The diff deletes
   defensive guards from `_covered`, from check 1 (the `(cycle, pass)` set
   key and the `findings_count` comparison) and from check 3 (anchors).
   For each deleted guard, is the schema genuinely a superset of what it
   used to catch? A guard removed on the assumption of coverage that does
   not actually hold is exactly how P2 happened.

5. **Meta, and I mean this literally.** Both self-caught regressions came
   from deriving a specification from the wrong source -- from the current
   writer, or from imagination -- instead of from the real readers and the
   real data. Look through this diff for anything else that was written
   from an assumed spec rather than a verified one, and say what you would
   have measured to check it.

If an axis is clean, say so plainly -- a clean axis is a real result and I
will not read it as laziness. If you find something, give me the experiment
that shows it, not the reasoning that suggests it. I re-run everything
reported, so a claim I cannot reproduce costs us a round.

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
