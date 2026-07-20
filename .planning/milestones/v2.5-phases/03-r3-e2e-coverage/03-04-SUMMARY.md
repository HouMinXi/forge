---
phase: 03-r3-e2e-coverage
plan: 04
subsystem: e2e-test-suite
tags: [e2e-coverage, unit-tests, machine-integration, bug-inject-teeth]
dependency_graph:
  requires: [03-01-e2e-check-foundation, 03-02-layer2-cooccurrence, 03-03-machine-wiring]
  provides: [test-e2e-check, test-machine-e2e, test-factories-build-e2e-checker]
  affects: [tests/test_e2e_check.py, tests/test_machine_e2e.py, tests/test_factories.py]
tech_stack:
  added: []
  patterns: [tmp_path-fixture, counting-stub-for-autofix, real-fixture-over-mock, three-state-cycle-teeth]
key_files:
  added:
    - tests/test_e2e_check.py
    - tests/test_machine_e2e.py
  modified:
    - tests/test_factories.py
decisions:
  - T1 uses explicit e2e_patterns ["*/integration/**"]; default patterns would not match bonding/integration/test.sh, making the inverse assertion pass by luck rather than correctness
  - Case 8 drives Verdict.PENDING via machine.run() -> _run_local() -> _should_enter_hold(); also asserts the e2e fingerprint appears in state.findings
  - F.hub-only asserts result==[] AND a loop confirms no dep1/dep2 in descriptions (vacuously true when empty; belt-and-suspenders for future behavior changes)
  - F.same-pair-dedup touches both A and B in the diff; asserts len(result)==1 to prove sorted_pair_hash collapses depends_on and data_paths expressions
  - Counting stubs used only for autofix-skip and falsifier-bypass (where side-effect absence is the behavior under test); real tmp_path fixtures used elsewhere
status: complete
metrics:
  completed_at: "2026-05-27"
  tasks_completed: 3
  files_modified: 3
  commits: 1
  tests_added: 58
  total_suite: 698
---

# Phase 03 Plan 04: E2E Test Suite Summary

Added 58 tests across three files covering every function in e2e_check.py, the
machine-level e2e_runner integration, and four bug-inject teeth that prove both
sides of the invariants.

## What Was Built

**Objective:** Give every line of 03-01..03-03 an executable test; provide
teeth that catch regressions in both the fire and clear directions so a future
change cannot silently break coverage detection.

**One-liner:** 48 unit tests for e2e_check.py (Groups A-G), 9 machine
integration tests (Cases 1-8 + factory Case 9), and 4 bug-inject teeth
(T1-T4), all with real tmp_path fixtures and counting stubs only where side-
effect absence is the specific assertion.

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | tests/test_e2e_check.py -- Groups A-G unit tests | e0f06fa | test_e2e_check.py |
| 2 | tests/test_machine_e2e.py -- Cases 1-8 + factories | e0f06fa | test_machine_e2e.py, test_factories.py |
| 3 | Bug-inject teeth T1-T4 (in test_e2e_check.py) | e0f06fa | test_e2e_check.py |

### Test Coverage

**tests/test_e2e_check.py (52 tests -- 48 Groups A-G + 4 teeth T1-T4):**

- **Group A (9)** -- `detect_signature_changes`: empty diff, garbage/ParseError,
  Python def, no-signature, section_header arm for multiline def, removed file
  excluded, shell function, flat-shell no-sig, section_header attribute canary
  (fails fast if a unidiff upgrade silently drops the attribute).
- **Group B (6)** -- `group_source_files`: two-directory split, nested same
  directory, tests-excluded by default, all three exclusion variants (test,
  tests, spec) checked individually, component map assignment, exclude_test_dirs=False.
- **Group C (9)** -- `load_components_yaml`: absent file returns None, valid
  config with defaulted e2e_patterns, version != 1, undefined depends_on,
  self-reference, cycle, unknown e2e_absent_ok component, unknown data_paths
  component, malformed YAML. Each error case checks ComponentsConfigError and
  a keyword in the message (undefined/self/cycle/version).
- **Group D (2)** -- `find_e2e_artifacts`: recursive ** glob, middle-segment
  wildcard.
- **Group E (5)** -- `check_layer_1`: empty, single-group+sig, multi-group
  no-sig, multi-group+sig (one DISMISSED finding, fingerprint starts "e2e-l1"),
  fingerprint stability (same inputs -> same fingerprint).
- **Group F (9)** -- `check_layer_2`: components=None opt-out, hub+dep+no-
  artifact P2, hub-only with two declared dependents (asserts result==[] AND
  loop confirms no dep name in descriptions), dependent has artifact suppresses,
  e2e_absent_ok suppresses both arms, peer one-side no-fire, peer both-sides
  fires, same-pair in depends_on+data_paths deduplicates to exactly 1 finding,
  transitive A->B->C diff touching A+C only fires nothing.
- **Group G (4)** -- `run_e2e_check`: empty+no-config returns 2-tuple of empty
  lists, invalid YAML emits one e2e-config-error UNCERTAIN finding with Layer 1
  still running, Layer 2 fires and suppresses Layer 1 (dedup), Layer 1 alone
  passes through.

**Teeth T1-T4:**
- **T1** -- Layer 2 P2 three-state cycle: State 1 (no artifact) fires one
  finding, State 2 (artifact created at bonding/integration/test.sh) clears to
  zero, State 3 (artifact deleted) refires. e2e_patterns set explicitly to
  ["*/integration/**"] -- the default patterns do not match this path and would
  silently pass the inverse assertion for the wrong reason.
- **T2** -- Layer 1 fires on multi-group+signature diff and clears on the same
  multi-group diff without a signature.
- **T3** -- Layer 1 does not fire on a single-component diff even with a
  signature change.
- **T4** -- A depends_on typo (undefined component name) produces exactly one
  e2e-config-error UNCERTAIN finding whose description names the undefined
  reference.

**tests/test_machine_e2e.py (9 tests):**
- Case 1: e2e_runner field is present with the no-op default.
- Case 2: _run_e2e_phase invoked exactly once per round with (diff_text, cwd).
- Case 3: e2e findings from the runner reach self._state.findings after a round.
- Case 4: _merge_findings priority -- L0 wins on collision; all four sources
  (L0/L1/L2/E2E) appear when fingerprints are disjoint.
- Case 5: _append_round_snapshot records "e2e_fingerprints" key.
- Case 6: autofix skip -- counting stub confirms zero invocations on MUTANT and
  E2E_CHECK source findings.
- Case 7: E2E_CHECK bypasses the falsifier -- zero falsifier calls on an e2e-
  only round (verified via counting stub replacing the L1 provider).
- Case 8: End-to-end -- an UNCERTAIN E2E_CHECK finding drives machine.run()
  to Verdict.PENDING; hold_reason is non-empty; the e2e fingerprint appears in
  state.findings.
- Case 9 (test_factories.py extension): build_e2e_checker() returns a callable;
  calling it returns a 2-tuple of (list, list).

**Entry point for Case 8:** `machine.run()` dispatches through `_run_local()`
which calls `_execute_round()` then `_should_enter_hold()`.

## Verification Results

The checks below were run by the dispatch sub-session that received the
implementation report. Main session (user) independent verification is a
separate step before merge and is not recorded here.

**Step 0 (dispatch sub-session):**
- ruff check (3 test files): clean.
- Non-ASCII grep on committed diff: no output (PASS).
- python3 -m py_compile: all three files parse.

**Contracts confirmed by reading the committed code (dispatch sub-session):**
- T1 has `e2e_patterns: ['*/integration/**']` explicitly set in YAML fixture;
  docstring explains the default-pattern false-negative risk.
- F.hub-only asserts `result == []` then loops to check no dep name in
  descriptions (belt-and-suspenders; loop body is vacuously safe on empty result).
- F.same-pair-dedup touches both A and B in the diff; asserts `len(result) == 1`.
- Case 8 asserts `verdict == Verdict.PENDING`, hold_reason non-empty, and the
  e2e fingerprint present in state.findings.
- AI traces in comments: none found (grep on D-0x, Task N, Plan 03, EC-5,
  CONTEXT.md, LOCKED produced zero hits).

**Regression (dispatch sub-session):** PYTHONPATH=src python3 -m pytest
tests/ -q -> 698 passed (640 baseline + 58 new, 0 failures).

## Notes
- No source files modified; all 58 additions are in the three test files.
- The three-state cycle (T1) is the primary integrity guard: it proves the
  artifact scanner actually reads the filesystem on each call, not a cached
  result. A one-shot fire/clear would pass by luck if the scanner memoized.
- Case 8 is the user-visible correctness proof: if UNCERTAIN E2E_CHECK findings
  did not reach Verdict.PENDING, the entire 03-01..03-03 wiring would be
  operationally inert.
