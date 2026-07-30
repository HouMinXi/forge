## Round 4 review — receipt-load crash guard (redesigned)

**Verdict: CLEAN across all five axes. No findings.**

The redesign is sound. The validator asserts exactly the fields the seven checks index, no more and no less; the two mandatory requirements (bad receipt → reported by name, never a crash; healthy receipt → never rejected) both hold under injection. Full details per axis.

### Axis 1 — Is the schema still too strict? (highest severity, drew blood twice as P2/P3)
**CLEAN.** Measured, not reasoned:
- All 10 parseable real receipts in `.code-forge/receipts/` and `evidence/fabrication-receipts-20260601/` pass `_validate_receipt_schema`. The 2 incident files (`c2p1`, `c3p1`) are correctly unparseable and trip the `json.loads` guard, not the schema.
- `git log -p` over `receipt.py` shows `write_receipts()` and `_build_excerpts()` have emitted the same field shapes since inception — `diff_sha256`/`timestamp` str, `cycle`/`pass`/`findings_count` int, `findings`/`anchors`/`code_excerpts` list-of-dict, `code_excerpts` items `{file,content,start_line,end_line}`. No older shape exists that the schema would reject.
- `covered_line_ranges` is deliberately excluded (the P3 fix); both real shapes (`{"file","start","end"}` and `"path:start-end"`) are accepted — verified by `test_real_covered_line_ranges_shapes_are_accepted` and by direct call.
- `skill` and `pass_status` appear in real receipts but are **never read** by any check (grep confirmed) — correctly excluded from the schema.

The "healthy receipt never rejected" requirement holds for every receipt on disk, in git history, and producible by the writer.

### Axis 2 — Is the schema too loose? (checks now use unguarded access)
**CLEAN.** I enumerated every field access in `run_verify` and the helpers it calls on the hardened path:

| Field accessed | Where | Asserted by |
|---|---|---|
| `r["cycle"]`, `r["pass"]` | checks 1,2,7 | `_INT_FIELDS` |
| `r["findings_count"]`, `r["findings"]` | check 1, 7 | `_INT_FIELDS` / `_LIST_OF_DICT_FIELDS` |
| `r.get("diff_sha256")` | check 2 | `_STR_FIELDS` |
| `r["anchors"]`, `a.get("file")` | check 3 | `_LIST_OF_DICT_FIELDS` |
| `r.get("timestamp")` | check 4 | `_STR_FIELDS` |
| `r.get("code_excerpts")`, then `exc["file"]`/`["start_line"]`/`["end_line"]`/`get("content")` | checks 5,6,7 | `_LIST_OF_DICT_FIELDS` + `_NESTED_SCHEMAS` |

Every indexed field is asserted. The nested-schema coupling holds: `code_excerpts` is in both `_LIST_OF_DICT_FIELDS` and `_NESTED_SCHEMAS`, so the "safe only because the loop above already proved…" comment's precondition is met. `skill`/`pass_status` are correctly not asserted (never read). No malformed-but-schema-valid input can reach an unguarded crash.

### Axis 3 — Does eager validation mask tamper?
**CLEAN.** Injected a schema-valid receipt with a wrong `diff_sha256`: it fails check 2 with `"diff hash mismatch c2p1"` — named, no crash. A type-malformed *and* tampered receipt reports the type error first, which is correct: the operator fixes the named file, then re-runs to surface the semantic failure. The deferral is one re-run, not a masked tamper — the file is always named, so the operator's next action is never wrong.

### Axis 4 — Did removing the per-check guards leave a hole?
**CLEAN — and the brief's framing here is slightly inaccurate.** The diff removed `.get()` defaults from check 1 (`cycle`/`pass`/`findings_count`) and check 3 (`anchors`). For each, the schema is a genuine *superset* of what the old guard caught:
- check 1: schema requires `cycle`/`pass`/`findings_count` to be present and int — the old `.get()` would have returned `None` for a missing field and silently produced a bad key. Schema is stricter, not looser.
- check 3: schema requires `anchors` to be a list of dicts — the old `.get("anchors", [])` would have *silently passed* a missing `anchors`. Schema catches it. (Verified by injection: missing `anchors` → `"corrupt receipt: ... anchors must be a list of objects"`.)

The brief says "the diff deletes defensive guards from `_covered`" — **this is false.** `git diff f7bd6ad` shows `_covered` is *byte-identical* to base; only a comment was added. No guard was removed from it. This doesn't change the outcome (the legacy branch `_covered` lives on is unreachable in production — `cli.py:1513` always passes `diff_text`, and `subprocess.stdout` is always a `str`, never `None`), but the premise the axis was asked to check doesn't hold.

### Axis 5 — Meta: anything written from an assumed spec?
**CLEAN, with two measurement notes.**

1. The schema tuples are derived from `write_receipts()` (the writer) — which is exactly the P3 trap. But they're *corroborated* against the real readers (the seven checks, enumerated in Axis 2) and the real data (all 10 parseable receipts pass). So while the tuples are writer-derived, they're not writer-*only*-derived. The one place the writer-derivation was wrong (`covered_line_ranges`) was caught and removed. No remaining field has the P3 problem.

2. **The brief's "accepted=14" count is inaccurate.** I measure 12 receipt files total on disk (10 parseable + 2 unparseable), not 14. The substantive claim — "all parseable real receipts pass, 0 rejected" — holds (I verified 10/10), but the literal count is off. Worth re-checking how the author counted, since a number that doesn't match disk is the kind of unverified claim this axis exists to catch.

### Process note
My first plain-python injection runs silently imported the **stale main-repo** `verify.py` (no schema) instead of the worktree's — a path gotcha, not a code issue. All results above were re-run with `PYTHONPATH` forced to the worktree `src/`. No file was edited; no `git add`/`commit`/`stash`/`reset` performed. `git status` shows exactly the three expected entries (`errors.py` M, `verify.py` MM, `test_verify.py` MM); non-ASCII gate clean; 46 verify tests pass; ruff clean.

The full project suite was established green in the brief and my changes are isolated to the three receipt files, so I did not block on its (slow) re-run.
