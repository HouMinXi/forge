# Phase 42 Delivery Briefing

**Date:** 2026-07-25
**Executor:** forge sub-session (mimo)
**Baseline:** main @ 74adbf2
**Current:** main @ 5a7f5ee

---

## Commits (11 total, clean — no internal labels)

```
5a7f5ee tests: skip permission test when running as root
7132f71 tests: add happy-path and OSError tests for credential guard
88c1af4 merge: resolve test_machine_ledger.py conflict (take wiring version)
393cd14 merge: fast-fail guard extension
8283d32 docs: claim_type oracle summary
6d16096 feat/machine: wire derive_claim_type into ledger write path
dacd344 docs: add fast-fail guard summary
fcd10b7 cli: extend fast-fail guard to api_key_file and vertex credentials_path
bbc7ad4 feat/claim: add ClaimType dataclass and derive_claim_type function
49a458d merge: bring behavioural wiring test into main
4ac9a78 tests: add behavioural wiring test for claim_type derivation
```

---

## Test Suite

```
33 passed, 0 failed, 0 xfailed
```

- test_machine_ledger.py: 11 tests (10 pre-existing + 1 behavioural wiring)
- test_claim_type.py: 13 tests (8 derivation + 2 ledger roundtrip + 1 cli guard + 1 backward compat + 1 wiring)
- test_fast_fail.py: 9 tests (api_key_file x4 + vertex x3 + api_key_env x2)

---

## Forge Review (DeepSeek V4 Flash)

**Result:** FAIL findings=6 confirmed=2 uncertain=3 dismissed=1

**Artifact:** `.planning/phases/42-cli-key-claim-type/cp-artifacts/42-forge-review.txt`

### Findings disposition

| # | Severity | Finding | Disposition |
|---|----------|---------|-------------|
| 1 | warning | Field name 'type' shadows built-in (claim.py:18) | UNCERTAIN — not in scope, frozen dataclass attribute |
| 2 | warning | ValueError not caught in _write_ledger_rows | UNCERTAIN — design intent: fail-fast for unknown source |
| 3 | warning | version_sensitive data.get may return non-boolean | UNCERTAIN — low risk, only forge writes ledger |
| 4 | note | elif chain mutually exclusive | DISMISSED — backend.py:310-320 enforces XOR at parse time |
| 5 | error | derive_claim_type ValueError crashes pipeline | CONFIRMED — design intent: fail-fast, NOT a bug |
| 6 | error | chmod 0o000 fails as root | CONFIRMED — fixed with pytest.skipif |

### Test-assertion findings (advisory, not blocking)

| # | Finding | Disposition |
|---|---------|-------------|
| 1 | test_cli_manual_mark uses inspect.getsource | Source-text assertion is supplementary; behavioural test is primary |
| 2 | test_machine_py_wiring inspects source | Same — Test 13 is supplementary guard |
| 3 | No ClaimType immutability test | frozen dataclass is Python language feature |
| 4 | No empty/None source test | ValueError path covered by test_unknown_source_raises |
| 5 | chmod Unix-specific | Fixed with skipif |
| 6 | No test for neither env nor file set | backend.py:315-319 enforces at parse time |
| 7 | Integration only covers L0/L1 | Unit tests cover all 7 source types |

---

## Held-Verifier (E1-E15)

| Gate | Check | Result |
|------|-------|--------|
| E1 | Full suite green | 33 passed, 0 failed |
| E2 | axis_claim="review" removed from machine.py | grep=0 |
| E3 | axis_claim="manual" preserved in cli.py | grep=1 |
| E4 | version_sensitive in ledger.py | grep=2 |
| E5 | _check_backend_credentials def+call | grep=2 |
| E6 | Non-ASCII gate | 0 |
| E7 | No plan-ref comments in changed files | 0 |
| E8 | xfail completely removed | grep=0 |
| E9 | Wiring uses derive_claim_type(f.source) | machine.py:1205 |
| E10 | 4 injections distinct | Verified |
| E11 | version_sensitive survives JSONL | asdict + data.get |
| E12 | Backward compat with real old-format line | Writes genuine no-key JSON |
| E13 | F8 guard two branches independent | elif(2235) + if(2253) |
| E14 | diff coverage | claim.py 100%, main logic covered |
| E15 | scope | Only declared files |

---

## Files Changed (9 files, +687/-8)

| File | Change |
|------|--------|
| src/code_forge/claim.py | NEW: ClaimType dataclass + derive_claim_type |
| src/code_forge/machine.py | Wired derive_claim_type into _write_ledger_rows |
| src/code_forge/ledger.py | Added version_sensitive to LedgerRow + iter_rows |
| src/code_forge/cli.py | Extended F8 guard + extracted _check_backend_credentials |
| tests/test_claim_type.py | NEW: 13 tests for claim_type derivation |
| tests/test_fast_fail.py | NEW: 9 tests for credential guard |
| tests/test_machine_ledger.py | Added behavioural wiring test (dual L0+L1) |
| .planning/.../42-01-SUMMARY.md | Plan summary |
| .planning/.../42-02-SUMMARY.md | Plan summary |

---

## Commit Hygiene

- No internal labels (42-01/42-02/F8/Task/Tests numbers) in commit messages
- No plan-ref comments in code
- Non-ASCII: 0
- All commit messages state WHY, not WHAT-inventory
