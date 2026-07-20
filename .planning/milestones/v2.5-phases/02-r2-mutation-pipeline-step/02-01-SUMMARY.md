---
phase: 02-r2-mutation-pipeline-step
plan: 01
subsystem: mutation-testing
tags: [mutation, state-machine, factories, l2-runner]
dependency_graph:
  requires: [Phase-1-state-machine, Phase-1-factories]
  provides: [StateFinding-MUTANT-source, mutation-module, build-l2-runner]
  affects: [state.py-schema, factories.py-exports]
tech_stack:
  added: [mutmut-subprocess-integration]
  patterns: [soft-dependency-check, flaky-guard, subprocess-list-args]
key_files:
  created:
    - src/forge/mutation.py
  modified:
    - src/forge/state.py
    - src/forge/factories.py
decisions:
  - Extended StateFinding.source Literal to include MUTANT (additive, no schema version bump per D2)
  - Python-only MVP via mutmut subprocess (D-02, swappable design)
  - mutmut is soft dependency (D-05, availability check returns MUTATION_SKIPPED)
  - Flaky guard runs baseline 3x before mutation (D-02 flaky guard requirement)
  - All subprocess calls use list args, never shell=True (T-02-01 mitigation)
metrics:
  duration_minutes: 35
  completed_at: "2026-05-26T00:35:30Z"
  tasks_completed: 2
  files_modified: 3
  commits: 2
---

# Phase 02 Plan 01: Mutation Foundation Summary

Extended StateFinding.source to accept MUTANT literal and created mutation module with mutmut integration.

## What Was Built

**Objective:** Establish mutation testing foundation with pure-function mutation runner, type system extension, and DI factory.

**One-liner:** mutmut subprocess integration with 3x flaky guard and soft-dependency fallback.

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Extend StateFinding.source and create mutation.py | d12e717 | state.py, mutation.py |
| 2 | Add build_l2_runner to factories.py | a843c24 | factories.py |

### Key Changes

**state.py:**
- Extended `StateFinding.source` from `Literal["L0", "L1"]` to `Literal["L0", "L1", "MUTANT"]`
- Additive change, no schema version bump (per D2 convention)

**mutation.py (new module):**
- `Survivor` dataclass: file and mutant_id
- `parse_mutmut_results()`: parses mutmut 3.x output, handles ranges (1-3) and comma-separated IDs
- `run_mutation()`: runs mutmut on diff-scoped .py files with:
  - 3x baseline flaky guard (aborts if any baseline run fails)
  - mutmut availability check (returns MUTATION_SKIPPED if missing)
  - Timeout handling (600s default for mutmut run, 10s for results)
  - Converts survivors to StateFinding with source="MUTANT", disposition=CONFIRMED
- All subprocess calls use list args (never shell=True per T-02-01)

**factories.py:**
- `build_l2_runner()` factory function
- Checks mutmut availability via `shutil.which()`
- Returns no-op callable with MUTATION_SKIPPED if mutmut not found (soft dependency per D-05)
- Delegates to `run_mutation` when mutmut is available
- Signature: `() -> Callable[(diff_files, baseline_cmd) -> (findings, infra_errors)]`

## Verification Results

**Automated checks passed:**
- ruff check: clean on all modified files
- imports: all exports verified
- test suite: 601 tests passed, 0 failures
- plan verification: StateFinding with source="MUTANT" instantiates successfully

**Manual verification:**
- StateFinding.source Literal includes "MUTANT" (line 48 in state.py)
- mutation.py exports run_mutation and parse_mutmut_results
- build_l2_runner returns callable with correct signature
- parse_mutmut_results handles ranges: "1-3" produces [1, 2, 3]
- No shell=True in any subprocess call

## Deviations from Plan

None. Plan executed exactly as written.

## Implementation Notes

**Design decisions honored:**
- **D-02 (mutmut invocation):** Direct subprocess.run of `mutmut run --paths-to-mutate` + `mutmut results`. Subprocess calls isolated in mutation.py for future language runner swaps.
- **D-05 (soft dependency):** mutmut absence produces MUTATION_SKIPPED finding (disposition=DISMISSED). Not a hard dependency in pyproject.toml.
- **Flaky guard:** 3x baseline run before mutation. Any failure aborts with MUTATION_SKIPPED.
- **Python-only MVP:** Filters diff_files to .py files only. Non-Python files produce MUTATION_SKIPPED.

**Subprocess safety (T-02-01 mitigation):**
- All subprocess.run calls use list args
- No shell=True anywhere
- Timeout guards on all subprocess calls (120s baseline, 600s mutmut run, 10s results)

**MUTATION_SKIPPED conditions:**
- Unsupported language (no .py files in diff)
- mutmut not installed
- Flaky baseline (any of 3 runs fails)
- Timeout (baseline, mutmut run, or mutmut results)

**Fingerprint scheme:**
- Survivors: `mutant:<file>:<id>` (stable, unique per mutant)
- MUTATION_SKIPPED: `mutation-no-python`, `mutation-unavailable`, `mutation-flaky`, `mutation-timeout`, `mutation-baseline-timeout`, `mutation-results-timeout`

## Threat Surface

No new threats introduced beyond those in PLAN.md threat model (T-02-01 through T-02-04, T-02-SC).

## Next Steps

**For Plan 02 (machine.py integration):**
- Wire build_l2_runner into StateMachine constructor
- Add l2_runner phase after L1 in _execute_round
- Implement consecutive_survivor_rounds counter
- Filter source="MUTANT" findings before autofix loop

**For Plan 03 (tests):**
- Unit tests for parse_mutmut_results (ranges, comma-separated, edge cases)
- Integration tests for run_mutation (flaky guard, timeout, MUTATION_SKIPPED conditions)
- Factory tests for build_l2_runner (mutmut available vs missing)

**For Plan 04 (dogfood + checkpoint):**
- Run mutation on forge's own code changed in this phase
- Verify tests kill mutants (no survivors expected)

## Self-Check: PASSED

**Created files exist:**
```
FOUND: src/forge/mutation.py
```

**Modified files contain expected changes:**
```
FOUND: src/forge/state.py contains Literal["L0", "L1", "MUTANT"]
FOUND: src/forge/factories.py contains build_l2_runner
```

**Commits exist:**
```
FOUND: d12e717 (Task 1)
FOUND: a843c24 (Task 2)
```

**Test suite:**
```
PASSED: 601 tests, 0 failures
```
