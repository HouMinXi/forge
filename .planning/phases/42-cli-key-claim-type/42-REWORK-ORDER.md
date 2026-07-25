# Phase 42 plan rework order

**Issued:** 2026-07-25 by the PM session, after independent verification of the
CP1b review set (archived at `cp-artifacts/`, index at `cp-artifacts/INDEX.md`).
**Target:** `42-02-PLAN.md` (all three items). `42-01-PLAN.md` is NOT in scope.
**Baseline:** planning-local snapshot `33356a56fd7c`. Your edits are diffed
against that.

## What the review set already settled -- do NOT redo it

Three rounds plus a final round ran. ds, longcat and gemini all returned
CLEAN on the versions they saw; kimi's four final findings and gemini's
`read_text` OSError finding were all verified applied by the PM against the
current files. The plans are in good shape. This is not a re-plan and not a
re-review. Three defects only, listed below.

Do not re-derive the review history, do not re-verify file:line anchors, do
not restructure the plans. Anything outside the three items is out of scope.

## Ground truth, already measured -- use these, do not re-derive

Verified by the PM against the working tree at main @ 74adbf2:

- `tests/test_machine_ledger.py` and `tests/test_realpath_ledger.py` already
  call `_write_ledger_rows` (grep-confirmed). Ten-plus test files already
  construct `StateMachine(`. A real-path test of the wiring is therefore
  cheap and already patterned in this repo.
- Neither of those two tests asserts on `axis_claim` (grep returned nothing).
  Adding an assertion there is additive; it breaks no existing test.
- `_make_finding` in `test_machine_ledger.py` defaults to `source="L0"`;
  `test_realpath_ledger.py:67` also uses `source="L0"`.
- Every `source=` literal in `tests/` and `src/` is one of the seven values in
  `_SOURCE_TO_CLAIM` (L0 40x, MUTANT 26x, L1 18x, INFRA 18x, E2E_CHECK 17x,
  FIXVAL 7x, COVERAGE 2x). The planned `ValueError` on unknown source has no
  existing trigger.

## Item A (HIGH) -- the wiring has a surviving mutation

`42-02-PLAN.md` asserts in TWO places -- line 401 ("Test 13 is the ONLY test
that catches this wiring regression") and line 428 ("Test 13 (wiring
verification) is the ONLY test that catches machine.py wiring regressions")
-- a claim that is false, and the design it justifies leaves a live hole.
Both occurrences need correcting; fixing one and leaving the other is a
partial fix that still misdirects the executor.

Test 13 is a source-text assertion with three parts: (a) `derive_claim_type`
is imported, (b) no literal `axis_claim="review"` remains, (c)
`version_sensitive` appears in the LedgerRow construction.

Now consider this mutation of the wiring:

    axis_claim=derive_claim_type(f.source).type
    ->
    axis_claim=derive_claim_type("L1").type

All three Test 13 assertions still pass: the import is present, no literal
`axis_claim="review"` exists, `version_sensitive` is still written. Tests 9
and 10 construct `LedgerRow` directly and never touch `machine.py`, so they
stay green too. Yet every finding in the ledger is now classified `review`
with `version_sensitive=True` regardless of its real source -- precisely the
defect this phase exists to remove.

Note for context: kimi's final review states "Test 13 source-assertion
correctly covers the machine.py wiring that runtime tests 9-10 bypass". That
assessment is correct for the mutation the plan names (re-hardcoding the
output string) and does not cover the mutation above (hardcoding the
argument). Treat it as partially valid, not as a clearance.

Required: add real-path behavioural coverage of the wiring, and correct the
line 428 claim. The shape is yours to choose, but it must execute
`_write_ledger_rows` for real and assert on what actually lands in the
ledger. Test 13 stays -- it becomes a supplementary guard, not the only one.

## Item B (LOW) -- duplicated injection instruction

Task 2 Step 4 states the same injection twice: lines 396-402 ("Inject:
replace `derive_claim_type(f.source).type` back to the hardcoded string
`"review"`. Run test 13 -- must FAIL") and lines 410-414 ("Inject at wiring
site: re-add `axis_claim="review"` as a hardcoded string ... Run test 13 --
must FAIL"). The `<verification>` block repeats it as well at lines 462 and
464. This is leftover from the round-3 fix that was applied by appending a
corrected version without removing the superseded one. An executor reading
two near-identical injections cannot tell whether they are one step or two.

Required: one statement per distinct injection.

## Item C (nit) -- non-ASCII

`42-02-PLAN.md` line 428 contains an em dash (the only non-ASCII byte in
either plan). It sits on the same line as Item A, so fix it in that edit.
Gate: `grep -cP '[^\x00-\x7F]'` must return 0 for both plan files.

## Output contract

1. Edited `42-02-PLAN.md`.
2. A short delta note listing, per item, what changed and at which lines.
   State what you did, not what you intended.
3. One confirmation round against ds, longcat and kimi on the edited file.
   gemini already returned CLEAN on the current version and does not need
   re-running unless your edit is large.

The confirmation round prompt must follow the non-convergence protocol: state
what was fixed, what stays open, and what ground truth disproved. Include the
note about kimi's "Test 13 correctly covers" being partially valid, otherwise
kimi will re-confirm its own prior position unchanged. Never re-send a bare
plan.

## Honest failure is pre-authorised

If you judge that Item A cannot be done cheaply -- for example if constructing
a `StateMachine` for a real-path test turns out to need fixtures far heavier
than the two existing ledger tests suggest -- say so, show the fixture cost you
measured, and propose the alternative. Do not silently keep the source-grep
guard and report the item done.

If you judge Item A is wrong -- that the mutation I describe is not actually
reachable, or that some existing test does catch it -- say so with the
file:line evidence and do not make the edit. A disproof with evidence is a
better outcome than a compliant edit. It is not a failure to return "A is
wrong, here is why".

What is not acceptable is reporting an item complete without doing it. The
edit is diffed against snapshot `33356a56fd7c`, and the delta note is checked
against that diff.
