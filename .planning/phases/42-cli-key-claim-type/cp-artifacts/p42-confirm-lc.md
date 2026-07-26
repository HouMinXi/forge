## LongCat-2.0 Confirmation — Phase 42 REWORK-ORDER

### Item A verification
- [x] `test_write_ledger_derives_claim_type_from_source` exists in `tests/test_machine_ledger.py` (line 277)
- [x] Test exercises `_write_ledger_rows` with `source="L0"`, asserts `axis_claim="lint"` (lines 294-301) — plus `version_sensitive is False` (line 302)
- [x] "ONLY test" claims removed from plan — replaced by "primary wiring guard (behavioural); Test 13 is the supplementary guard (source-text)" (line 436) and "supplementary source-text guard" (line 282)
- [x] `derive_claim_type("L1")` mutation injection present in Step 4 (lines 407-413), with explicit note that Test 13 stays green

**Grounded proof**: Ran the test against current code → RED (`assert 'review' == 'lint'` at line 301). Injected the mutation logic in isolation → correct wiring yields `'lint'` (PASS), hardcoded `derive_claim_type("L1")` yields `'review'` (FAIL). Mutation caught as claimed.

### Item B verification
- [x] Step 4 duplicate removed — the old copy (410-414) is gone; Step 4 now has 3 distinct injections (re-hardcode "review" / hardcode "L1" arg / remove version_sensitive), each with its own revert
- [ ] **Remaining duplicate found** — see New Findings #1

### Item C verification
- [x] No non-ASCII bytes in `42-02-PLAN.md` (grep -P `[^\x00-\x7F]` clean)
- [x] No non-ASCII bytes in `tests/test_machine_ledger.py` (clean)
- [x] Test file compiles (`py_compile` OK) and is complete (ends at line 302)

### New findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | LOW | `42-02-PLAN.md:471` and `:473` are duplicate bullets — both assert "re-hardcoding `review` in machine.py causes test 13 FAIL", differing only in parenthetical note ("tests 9-10 bypass machine.py, stay green" vs "wiring regression caught by source assertion"). Item B fixed the Step 4 duplicate but missed this verification-section duplicate. Delete one. |

### Verdict: 0B/0H/0M/1L

The 1 LOW is a leftover duplicate in `<verification>` that the REWORK-ORDER's Item B did not reach. It's purely cosmetic (a checklist item, not a test-instruction block) and does not affect execution correctness. Ship as-is or delete one of lines 471/473 — your call.
