# Phase 42 Plan Confirmation Round (post-REWORK-ORDER)

## What was fixed (3 items from PM's REWORK-ORDER)

**Item A (HIGH)**: The PM identified a surviving mutation that Test 13 cannot catch:
```python
# Original
axis_claim=derive_claim_type(f.source).type
# Mutation (Test 13 stays green)
axis_claim=derive_claim_type("L1").type
```
Test 13 is source-text only: checks import, no literal "review", version_sensitive present. All three pass even with hardcoded argument.

**Fix**: Added `test_write_ledger_derives_claim_type_from_source` in test_machine_ledger.py. This is a real-path behavioural test: executes _write_ledger_rows with source="L0", asserts axis_claim="lint" in the ledger. Catches the argument-hardcoding mutation.

Also fixed: removed "Test 13 is the ONLY test" claims (2 occurrences), replaced with "primary=behavioural, supplementary=Test 13".

**Item B (LOW)**: Removed duplicate injection instruction in Step 4 (lines 410-414 were a copy of 397-402).

**Item C (nit)**: Em dash on line 428 replaced with ASCII "--".

## What stays open

Nothing. All 3 items addressed.

## Ground truth that disproved kimi's prior assessment

Kimi's final review stated: "Test 13 source-assertion correctly covers the machine.py wiring that runtime tests 9-10 bypass."

This is **partially valid**: Test 13 covers the mutation where someone re-hardcodes the OUTPUT string (`axis_claim="review"`). It does NOT cover the mutation where someone hardcodes the ARGUMENT (`derive_claim_type("L1")`). The new behavioural test covers both.

## Files to review

- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (edited)
- tests/test_machine_ledger.py (new test added at line 277)

Source code verification (unchanged, spot-check only):
- src/code_forge/machine.py (1200-1219)
- src/code_forge/ledger.py (40-54)

## Output format

```
## [Model] Confirmation — Phase 42 REWORK-ORDER

### Item A verification
- [ ] test_write_ledger_derives_claim_type_from_source exists in test_machine_ledger.py
- [ ] Test exercises _write_ledger_rows with source="L0", asserts axis_claim="lint"
- [ ] "ONLY test" claims removed from plan
- [ ] derive_claim_type("L1") mutation injection present in Step 4

### Item B verification
- [ ] Duplicate injection instruction removed (one statement per distinct injection)

### Item C verification
- [ ] No non-ASCII bytes in 42-02-PLAN.md

### New findings
| # | Severity | Finding |
|---|----------|---------|

### Verdict: CLEAN — 0B/0H/0M/0L
```

Read plans AND source code. Verify every claim.
