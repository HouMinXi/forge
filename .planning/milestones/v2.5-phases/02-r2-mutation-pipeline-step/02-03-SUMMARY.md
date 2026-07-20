---
phase: 02-r2-mutation-pipeline-step
plan: 03
subsystem: tests
tags: [mutation, l2-integration, unit-tests, bug-inject]
requires: [02-01, 02-02]
provides:
  - tests/test_mutation.py (15 unit tests)
  - tests/test_machine_l2.py (11 integration tests)
  - tests/test_install_hooks.py (4 liveness tests)
  - tests/test_factories.py (2 build_l2_runner tests)
  - tests/test_state_schema.py (2 consecutive_survivor_rounds tests)
affects: []
tech_stack:
  added: []
  patterns: [pytest mocking, tmp_path fixtures, StateMachine test patterns]
key_files:
  created:
    - tests/test_mutation.py
    - tests/test_machine_l2.py
  modified:
    - tests/test_install_hooks.py
    - tests/test_factories.py
    - tests/test_state_schema.py
decisions: []
metrics:
  duration_minutes: 7
  completed: "2026-05-26T00:52:49Z"
  tasks: 2
  files_created: 2
  files_modified: 3
  tests_added: 34
  total_tests: 635
---

# Phase 02 Plan 03: R2 mutation pipeline tests

**One-liner:** Comprehensive test coverage for mutation.py parser, run_mutation, L2 wiring, consecutive_survivor_rounds, CI async, and bug-inject teeth (EC-6)

## Summary

Created full test suite for Phase 2 mutation pipeline code:
- 15 unit tests for mutation.py (parser + run_mutation all scenarios)
- 11 integration tests for StateMachine L2 integration
- 4 liveness check tests for resolve_forge_path
- 2 factory tests for build_l2_runner
- 2 state schema tests for consecutive_survivor_rounds serialization

**Total test count:** 635 (601 existing + 34 new)

All tests pass. All modified files are ruff clean.

## Tasks Executed

### Task 1: Unit tests for mutation.py

**Commit:** c9186b5

Created tests/test_mutation.py with 15 test functions covering:

**Parser tests (7):**
- Empty string returns empty list
- Single survivor single ID
- Range produces multiple IDs (1-3 -> [1,2,3])
- Comma-separated produces correct IDs
- Mixed range+comma
- Multiple files produce correct attribution
- Malformed input returns empty list

**Runner tests (8):**
- Empty diff_files returns ([], [])
- Non-Python files only returns MUTATION_SKIPPED
- Flaky guard: baseline fails on run 2 of 3
- mutmut not installed returns MUTATION_SKIPPED
- mutmut timeout returns MUTATION_SKIPPED
- Successful run with survivors returns CONFIRMED findings
- Successful run with zero survivors returns empty list
- All MUTATION_SKIPPED findings have source=MUTANT + disposition=DISMISSED

All findings assertions verify source field and disposition field per plan requirements.

### Task 2: Integration tests for machine L2, liveness, factories, state

**Commit:** bf13d04

Extended 4 existing test files:

**tests/test_machine_l2.py (11 tests):**
- Test 1: l2_runner default (no-op) produces zero findings, PASS as before
- Test 2: l2_runner returns CONFIRMED MUTANT finding prevents fixpoint
- Test 3: MUTANT findings skip autofix (source="MUTANT" filter verified)
- Test 4 & 5: consecutive_survivor_rounds increments with survivors, resets to 0 when clean
- Test 6: 3 consecutive survivor rounds -> Verdict.FAIL with "demonstrably weak" in infra_errors
- Test 7: l2_runner exception does not crash state machine (graceful degradation)
- Test 8: CI mode status="done" with survivors -> EXIT_FAIL
- Test 9: CI mode status="running" with dead PID appends MUTATION_SKIPPED
- Test 10 (EC-6 bug-inject teeth test): toothless l2_runner -> 3 rounds -> FAIL
- Test 11 (EC-6 bug-inject teeth test): remove survivor -> l2_runner clean -> PASS

**tests/test_install_hooks.py (4 tests):**
- Test 11: forge binary passes --version -> uses binary path
- Test 12: forge binary fails --version (exit 1) -> falls back to sys.executable
- Test 13: forge --version times out (1s) -> falls back to sys.executable
- Test 14: forge --version stdout invalid (does not start with "forge ") -> fallback

**tests/test_factories.py (2 tests):**
- Test 15: mutmut on PATH -> build_l2_runner returns run_mutation
- Test 16: mutmut not on PATH -> build_l2_runner returns no-op with MUTATION_SKIPPED

**tests/test_state_schema.py (2 tests):**
- Test 17: save_state includes consecutive_survivor_rounds in JSON output
- Test 18: load_state reads consecutive_survivor_rounds; defaults to 0 for old state files

## Deviations from Plan

None. Plan executed exactly as written.

## Verification

```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src python3 -m pytest tests/ -q
# 635 passed, 3 warnings in 4.43s

ruff check tests/test_mutation.py tests/test_machine_l2.py tests/test_install_hooks.py tests/test_factories.py tests/test_state_schema.py
# All checks passed!
```

## Exit Criteria Met

- [x] EC-1: ruff clean on all new test files
- [x] EC-2: l2_runner wiring test passes
- [x] EC-3: consecutive_survivor_rounds test passes, CI async test passes
- [x] EC-4: MUTATION_SKIPPED tests pass (unsupported language, flaky guard)
- [x] EC-5: full suite green (635 tests)
- [x] EC-6: bug-inject test passes (toothless -> FAIL, clean -> PASS)

## Known Limitations

None.

## Follow-up Items

None.

## Commits

- c9186b5: test(02-03): add unit tests for mutation.py
- bf13d04: test(02-03): add integration tests for L2 mutation pipeline

## Self-Check: PASSED

Created files:
```bash
[ -f "tests/test_mutation.py" ] && echo "FOUND: tests/test_mutation.py"
# FOUND: tests/test_mutation.py

[ -f "tests/test_machine_l2.py" ] && echo "FOUND: tests/test_machine_l2.py"
# FOUND: tests/test_machine_l2.py
```

Commits exist:
```bash
git log --oneline --all | grep -q "c9186b5" && echo "FOUND: c9186b5"
# FOUND: c9186b5

git log --oneline --all | grep -q "bf13d04" && echo "FOUND: bf13d04"
# FOUND: bf13d04
```
