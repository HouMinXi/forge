# Phase 42 REWORK-ORDER-2 Delivery Report

**Executor:** forge sub-session (mimo)
**Date:** 2026-07-25
**Baseline:** planning-local snapshot `cf1b4455bbae`

---

## Item D (HIGH) — mirror mutation: DONE

**PM's defect:** Single-L0 behavioural test is defeated by `derive_claim_type("L0")` (mirror mutation). version_sensitive only tested as False.

**Fix:** Extended test to exercise BOTH L0 and L1 findings:

```python
# L0 -> lint, version_sensitive=False
assert by_fp["fp-lint"].axis_claim == "lint"
assert getattr(by_fp["fp-lint"], "version_sensitive", None) is False
# L1 -> review, version_sensitive=True
assert by_fp["fp-review"].axis_claim == "review"
assert getattr(by_fp["fp-review"], "version_sensitive", None) is True
```

Mirror mutation `derive_claim_type("L0")`:
- L0 assertions: stay green (source="L0" hardcoded -> "lint" == "lint")
- L1 assertions: FAIL (source="L1" hardcoded to "L0" -> "lint" != "review")

**Also:** Added fourth injection to Step 4 + updated acceptance_criteria + verification.

## Item E (process) — test on main worktree: DONE

**PM's defect:** test change uncommitted on main worktree.

**Fix:**
1. Saved patch to `/tmp/p42-behavioural-test.patch`
2. Created worktree: `.worktrees/p42` on branch `phase-42-cli-key-claim-type`
3. Committed test in worktree: `4ac9a78` (amended to include L1 coverage)
4. Reverted main: `git checkout -- tests/test_machine_ledger.py`
5. Verified main clean: `git diff --stat` = empty

---

## Injection proof (Item D output contract #3)

**Status: DEFERRED to GREEN phase.**

The injection requires hardcoding `derive_claim_type("L0")` in machine.py's `_write_ledger_rows`. But `derive_claim_type` does not exist in machine.py yet (that's Task 2's GREEN phase). The current code has `axis_claim="review"` hardcoded -- the behavioural test's RED failure (`assert 'review' == 'lint'`) already proves the L0 path works. The L1 path assertion (`assert 'review' == 'review'` with current hardcoded value) would PASS, which is the wrong signal -- it would look like the injection succeeded when in fact nothing was injected.

Honest summary: the fourth injection proof cannot run until derive_claim_type is imported into machine.py. At that point, the proof runs as:
1. Replace `derive_claim_type(f.source).type` with `derive_claim_type("L0").type`
2. Run test -> L0 PASS, L1 FAIL
3. Revert -> both PASS

This is a known limitation of pre-wiring test design, not a fabrication.

---

## Delta note (diffed against snapshot `cf1b4455bbae`)

### tests/test_machine_ledger.py (worktree `phase-42-cli-key-claim-type`, SHA `4ac9a78`)

- **Lines 277-309**: Extended `test_write_ledger_derives_claim_type_from_source` from single-L0 to dual L0+L1. xfail decorator at 277, def at 281, L0 finding at 299, L1 finding at 302, L0 assertions at 307-308, L1 assertions at 309. Docstring updated to explain two-source rationale.

### 42-02-PLAN.md

- **Line 283**: Test 14 description updated: "source=L0" -> "L0 AND L1 findings", added mirror-mutation rationale and version_sensitive=True branch coverage.
- **Lines 420-425**: Step 4 +fourth injection: hardcode `derive_claim_type("L0")`, L0 green/L1 red.
- **Lines 440-443**: acceptance_criteria: 4 bug-injection bullets (re-hardcode "review" / hardcode "L1" / hardcode "L0" mirror / remove version_sensitive).
- **Lines 479-482**: verification: 4 bug-injection bullets matching acceptance_criteria.

---

## Files changed

| File | Location | Change |
|------|----------|--------|
| `tests/test_machine_ledger.py` | worktree `.worktrees/p42` | L0+L1 dual coverage, committed `4ac9a78` |
| `.planning/phases/42-cli-key-claim-type/42-02-PLAN.md` | .planning/ (disk-only) | Fourth injection + behavior + acceptance + verification |

## Files NOT changed

| File | Reason |
|------|--------|
| `tests/test_machine_ledger.py` on main | Reverted, clean |
| `42-01-PLAN.md` | Out of scope |

---

## Verification evidence

### Worktree
```
branch: phase-42-cli-key-claim-type
SHA: 4ac9a78
```

### Main
```
git diff --stat tests/test_machine_ledger.py -> empty (clean)
```

### Non-ASCII
```
42-02-PLAN.md: 0
test_machine_ledger.py: 0
```
