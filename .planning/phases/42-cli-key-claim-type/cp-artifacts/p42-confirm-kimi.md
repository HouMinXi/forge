验证完成。新测试以完全正确的原因失败（`'review' != 'lint'`)，证明它非空心且能捕获两种变异。

## [kimi] Confirmation — Phase 42 REWORK-ORDER

### Item A verification
- [x] `test_write_ledger_derives_claim_type_from_source` exists in test_machine_ledger.py (line 277)
- [x] Test exercises `_write_ledger_rows` with source="L0", asserts `axis_claim=="lint"` (lines 292-301) — **real-run proof**: executed it just now, it FAILS with `assert 'review' == 'lint'` at line 301. The current hardcoded `machine.py:1211 axis_claim="review"` IS the mutation, so this RED state empirically proves the test catches both the re-hardcode mutation and the `derive_claim_type("L1")` argument mutation (both yield "review" for source="L0"). Not hollow.
- [x] "ONLY test" claims removed — `grep -n "ONLY" 42-02-PLAN.md` → zero hits; line 436 now reads "primary wiring guard (behavioural); Test 13 is the supplementary guard (source-text)"
- [x] `derive_claim_type("L1")` mutation injection present in Step 4 (plan lines 407-413), including the explicit note that Test 13 stays green under this mutation

### Item B verification
- [x] Duplicate injection instruction removed — Step 4 now holds exactly three distinct injections (400-405 re-hardcode output; 407-413 hardcode argument; 415-419 remove version_sensitive line), one statement each

### Item C verification
- [x] No non-ASCII bytes in 42-02-PLAN.md — `grep -nP '[^\x00-\x7F]'` → zero hits

### Source spot-checks (pre-execution state, consistent)
- `machine.py:1211` still `axis_claim="review"` — unchanged, as expected pre-execution; the plan's grep-returns-0 criterion is a post-execution gate
- `ledger.py:40-54` LedgerRow has no `version_sensitive` — unchanged, consistent
- `src/code_forge/claim.py` and `tests/test_claim_type.py` do not exist yet — consistent with plan-not-yet-executed

### New findings
| # | Severity | Finding |
|---|----------|---------|
| 1 | LOW | `<verification>` section repeats the Item-B defect class: plan lines 471 and 473 state the same injection twice ("re-hardcoding 'review' → test 13 FAIL", differing only in parenthetical), and the new argument-hardcoding injection (`derive_claim_type("L1")` → behavioural test FAIL) is absent from `<verification>` though acceptance_criteria line 434 covers it. Replace line 473 with the argument-hardcoding bullet. Optional: Task 1's NOTE (line 259) names only Test 13 for wiring — could add "supplementary; primary is the behavioural test in test_machine_ledger.py" for consistency with line 436. |

### Verdict: 0B/0H/0M/1L

All three REWORK-ORDER items are fixed and empirically verified (Item A proven by real test execution, not static reading). The single LOW is a checklist-consistency residue in `<verification>`, same copy-paste class as Item B; acceptance_criteria is already complete, so executor impact is nil.
