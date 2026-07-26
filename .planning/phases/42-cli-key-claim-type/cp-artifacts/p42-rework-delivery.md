# Phase 42 REWORK-ORDER Delivery Report

**Executor:** forge sub-session (mimo)
**Date:** 2026-07-25
**Baseline:** planning-local snapshot `33356a56fd7c`
**Scope:** 42-02-PLAN.md only (42-01-PLAN.md NOT in scope per REWORK-ORDER)

---

## Item A (HIGH) — surviving mutation: DONE

**PM's defect:** Test 13 (source-text assertion) cannot catch `derive_claim_type("L1")` mutation. All 3 Test 13 assertions still pass with hardcoded argument.

**Fix:** Added `test_write_ledger_derives_claim_type_from_source` in `tests/test_machine_ledger.py:277`.

- Executes `_write_ledger_rows()` for real with source="L0"
- Asserts `rows[0].axis_claim == "lint"` and `rows[0].version_sensitive is False`
- Marked `@pytest.mark.xfail(strict=True)` (TDD RED phase; wiring done → xfail removed)
- Verified RED: `FAILED ... assert 'review' == 'lint'` at line 301
- Verified XFAIL: `1 xfailed in 0.15s` (test suite passes cleanly)
- Covers mutation: `derive_claim_type("L1")` → axis_claim="review" ≠ "lint" → FAIL

**Also fixed:**
- Removed "Test 13 is the ONLY test" claims (2 occurrences: Step 4 line 401, acceptance_criteria line 428)
- Replaced with: "primary wiring guard (behavioural); Test 13 is the supplementary guard (source-text)"
- Added derive_claim_type("L1") argument-hardcoding mutation injection to Step 4

## Item B (LOW) — duplicated injection instruction: DONE

**PM's defect:** Step 4 lines 397-402 and 410-414 are the same injection twice.

**Fix:** Removed the duplicate (lines 410-414). Step 4 now has 3 distinct injections:
1. Re-hardcode output `"review"` → test 13 + behavioural FAIL
2. Hardcode argument `derive_claim_type("L1")` → behavioural FAIL, test 13 stays green
3. Remove version_sensitive → test 13(c) FAIL

## Item C (nit) — non-ASCII: DONE

**PM's defect:** Line 428 contains em dash (only non-ASCII byte in either plan).

**Fix:** Replaced with ASCII `--`. Verified: `grep -cP '[^\x00-\x7F]'` returns 0 for both plan files and test file.

---

## Additional fixes (found during confirmation rounds)

| # | Source | Fix |
|---|--------|-----|
| 1 | KIMI confirm | verification-section duplicate bullet (lines 471/473) → replaced with 3 distinct bullets matching acceptance_criteria |
| 2 | User | xfail marking added with `strict=True` (expected failure until wiring done) |

---

## Verification evidence

### Test suite
```
10 passed, 1 xfailed in 0.18s
```
- 10 pre-existing tests: all PASS (no collateral damage)
- 1 new test: XFAIL (expected RED, strict=True)

### Forge review
```
PASS findings=0 confirmed=0 uncertain=0 dismissed=0
```

### Non-ASCII gate
```
grep -cP '[^\x00-\x7F]' 42-02-PLAN.md → 0
grep -cP '[^\x00-\x7F]' test_machine_ledger.py → 0
```

### External model convergence (3 models, final round)

| Model | Verdict |
|-------|---------|
| DeepSeek V4 | CLEAN 0/0/0/0 |
| Kimi K2.7 | CLEAN 0/0/0/0 |
| LongCat-2.0 | CLEAN 0/0/0/0 |

All 3 confirmed:
- No "ONLY test" claims
- No duplicate injections
- No non-ASCII
- behavioural test exists and is RED
- Step 4 has 3 distinct injections
- acceptance_criteria == verification consistent

---

## Files changed

| File | Change |
|------|--------|
| `.planning/phases/42-cli-key-claim-type/42-02-PLAN.md` | Items A/B/C fixes |
| `tests/test_machine_ledger.py` | +test_write_ledger_derives_claim_type_from_source (line 277, xfail) |

## Files NOT changed

| File | Reason |
|------|--------|
| `.planning/phases/42-cli-key-claim-type/42-01-PLAN.md` | Out of scope per REWORK-ORDER |

---

## Delta note (diffed against snapshot `33356a56fd7c`)

### tests/test_machine_ledger.py
- **Line 277-308**: +`test_write_ledger_derives_claim_type_from_source` with `@pytest.mark.xfail(strict=True)`. Executes `_write_ledger_rows()` with source="L0", asserts axis_claim=="lint". Uses `getattr` for version_sensitive to avoid AttributeError before field exists.
- **Line 1**: trailing newline fix (was missing at EOF)

### 42-02-PLAN.md

**Item A:**
- **Line 284**: behavior section +Test 14 (real-path behavioural wiring test, xfail noted)
- **Line 24**: must_haves +behavioural test truth
- **Line 12**: files_modified +test_machine_ledger.py
- **Lines 400-413**: Step 4 rewritten: 3 distinct injections (was 2+1 duplicate)
  - L400-405: re-hardcode output "review" → test 13 + behavioural FAIL
  - L407-413: hardcode argument derive_claim_type("L1") → behavioural FAIL, test 13 green
  - L415-419: remove version_sensitive → test 13(c) FAIL
- **Lines 433-436**: acceptance_criteria: "ONLY test" → 3 bug-injection bullets + "primary=behavioural, supplementary=Test 13"
- **Line 431**: +behavioural test pass criterion
- **Line 466**: verification +behavioural test command

**Item B:**
- **Lines 410-414**: deleted (duplicate of L400-405)

**Item C:**
- **Line 428**: em dash → ASCII "--"

**Post-REWORK (confirmation round):**
- **Lines 471-473**: verification duplicate bullet fixed → 3 distinct bullets matching acceptance_criteria
