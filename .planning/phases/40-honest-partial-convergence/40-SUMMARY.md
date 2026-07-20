# Phase 40: Honest partial results + convergence (mechanical half)

## What was done

Made partial review results honest and visible. A round that completed 2 of 3
passes no longer silently presents as a full PASS or opaque FAIL. The founding
principle applies: "a green verdict is honest or declares what it did not verify."

### Changes

**state.py** -- PassOutcome enum + derive_pass_outcomes()
- New `PassOutcome` string enum: COMPLETED, TIMEOUT, ERROR, SCHEMA_FAIL, INCOMPLETE, SKIPPED
- Severity ordering: TIMEOUT(0) > ERROR(1) > SCHEMA_FAIL(2) > INCOMPLETE(3) > COMPLETED(4)
- `derive_pass_outcomes()` scans INFRA findings by ID pattern to derive per-pass status
- Zero contract change to l1_provider tuple (derive-from-INFRA, not 4-tuple->5-tuple)

**sarif.py** -- per-pass status in format_summary()
- Calls `_count_pass_outcomes(state.findings)` internally
- Appends `passes=2/3` suffix when any pass is not COMPLETED
- No suffix when all passes completed (backward compat)

**receipt.py** -- pass_status field in receipt JSON
- Each pass receipt includes `pass_status` derived from INFRA findings
- Old receipts without pass_status still parse (backward compat)

**outlet_c.py** -- large-diff chunking
- File-based chunking when diff exceeds configurable threshold
- Each chunk runs independently; findings merged by fingerprint dedup
- Chunk timeout sets pass_status=TIMEOUT for that chunk

**Tests** --553 new lines across 2 files
- test_receipt_partial.py (285 lines): pass_status derivation, backward compat, bug-inject proof
- test_chunking.py (278 lines): under/over threshold, multi-file, timeout, dedup, bug-inject proof

### Key design decisions

1. **derive-from-INFRA** (not contract change): Gemini R1 BLOCKER identified that INFRA findings
   already encode pass failures. Receipt/sarif derives pass_outcomes by scanning these patterns,
   avoiding the 15+ file change of a 4-tuple->5-tuple contract modification.

2. **Option B** (derive at consumption site): pass_outcomes derived in receipt.py/sarif.py at
   emit time, not stored in State. No State.pass_outcomes field added.

3. **Verdict stays fail-closed**: partial completion is still FAIL. The ledger's TerminalState
   records the machine's final verdict, unchanged by partial completion.

## Verification

- 31 new tests pass (test_receipt_partial + test_chunking)
- Full suite passes (2798 tests, zero regressions)
- Bug-inject proofs: corrupt INFRA finding ID -> derive returns COMPLETED -> test catches it
- Backward compat: old receipts parse, no suffix when all passes OK

## Known gaps (deferred)

1. Convergence plateau + prior-round memory -> post-Phase-44 semantic half
2. Cross-file findings lost under chunking (inherent limitation of file-based chunking)
3. Bin-packing for chunk count optimization (deferred)
4. Single-file oversized fallback to hunk-based chunking (deferred)
5. Per-pass retry (not included; keep-and-mark policy)

## Commits

- 25b063e outlet/p40: honest partial results + chunking (merged to main
  2026-07-16). The SHA changed on the way in (rebase): dd6d40f above is the
  pre-rebase worktree tip and is now a dangling orphan, not reachable from
  main. Same pattern as Phase 46 (a18844a superseded f53bf84) -- anyone
  auditing history should look for 25b063e, not dd6d40f.
