---
phase: 02-r2-mutation-pipeline-step
plan: 02
subsystem: state-machine
tags: [mutation, l2-runner, consecutive-survivor-rounds, liveness-check]
dependency_graph:
  requires: [02-01-mutation-foundation, Phase-1-state-machine]
  provides: [l2-phase-integration, consecutive-survivor-rounds, resolve-forge-path-liveness]
  affects: [machine.py-round-orchestration, state.py-schema, install_hooks.py-validation]
tech_stack:
  added: [mutation-async-ci, consecutive-rounds-guard]
  patterns: [DI-callable-injection, backward-compat-schema-loading]
key_files:
  modified:
    - src/forge/machine.py
    - src/forge/state.py
    - src/forge/install_hooks.py
decisions:
  - l2_runner wired as injectable callable with no-op default (D-01 DI pattern)
  - MUTANT findings skip autofix loop via source filter (coverage gap, not code bug)
  - consecutive_survivor_rounds LOCAL-only counter resets to 0 on clean round, 3 -> FAIL
  - CI async mutation uses wrapper THREAD + mutation-result.json state file (D-03)
  - resolve_forge_path runs --version liveness check with 1s timeout, fallback to sys.executable (D-04)
  - Import load_gate_config from gate_check (no YAML parsing duplication)
metrics:
  duration_minutes: 28
  completed_at: "2026-05-26T01:03:15Z"
  tasks_completed: 2
  files_modified: 3
  commits: 1
---

# Phase 02 Plan 02: L2 Runner Integration Summary

Wired mutation testing into state machine round orchestration and fixed forge path liveness validation.

## What Was Built

**Objective:** Wire l2_runner into the state machine and fix resolve_forge_path liveness.

**One-liner:** L2 mutation phase with consecutive-survivor guard, CI async wrapper, and forge --version liveness check.

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Wire l2_runner into machine.py state machine | 7a15e47 | machine.py, state.py |
| 2 | Add resolve_forge_path --version liveness check | 7a15e47 | install_hooks.py |

### Key Changes

**machine.py:**
- Added `l2_runner` field to StateMachine dataclass with no-op default `lambda diff_files, baseline_cmd: ([], [])`
- Added `_run_l2_phase()` method:
  - Loads baseline_cmd from gate.yaml via `load_gate_config` (no YAML parsing duplication)
  - Calls `self.l2_runner(diff_files, baseline_cmd)`
  - Returns MUTANT findings or empty list on error
- Updated `_execute_round()` to call `_run_l2_phase()` after L1
- Updated `_merge_findings()` to accept `l2_findings` parameter (backward compat default `None`)
  - Merge order: L2 first, then L1, then L0 (L0 wins on conflict per FP-04)
- Updated `_append_round_snapshot()` to record `l2_fingerprints`
- Added MUTANT autofix filter in `_apply_autofix_loop_to()`:
  - `if finding.source == "MUTANT": continue` before disposition check
  - Coverage gaps skip autofix (semantically wrong for mutation survivors)
- Added `consecutive_survivor_rounds` tracking in `_run_local()`:
  - Counts CONFIRMED MUTANT findings after each round
  - Increments counter if > 0 survivors, resets to 0 if clean
  - Hard-stops at 3 consecutive rounds with `Verdict.FAIL`
- Added CI async mutation in `_run_ci()`:
  - Checks for prior `mutation-result.json` on entry
  - If status="done" + survivors: `Verdict.FAIL`
  - If status="running" + PID alive: skip new launch
  - If status="running" + PID dead: MUTATION_SKIPPED finding
  - If status="error": `Verdict.FAIL`
  - Launches new async mutation via wrapper thread (daemon=True):
    - Writes initial mutation-result.json with status="running"
    - Runs mutmut + parses results
    - Writes final mutation-result.json with status="done" or "error"
  - Thread does not block process exit

**state.py:**
- Added `consecutive_survivor_rounds: int = 0` field to State dataclass (after `promoted_fingerprints`)
- Updated `save_state()` to serialize `consecutive_survivor_rounds`
- Updated `load_state()` to deserialize with backward-compat default (0)

**install_hooks.py:**
- Added logging import
- Updated `resolve_forge_path()` to run liveness check after `shutil.which("forge")` succeeds:
  - `subprocess.run([forge_exe, "--version"], timeout=1)`
  - Verifies returncode == 0 and stdout starts with "forge "
  - Falls back to sys.executable on timeout or validation failure
  - Logs warning on fallback

## Verification Results

**Automated checks passed:**
- ruff check: clean on all modified files
- plan verification: `consecutive_survivor_rounds` in State, `l2_runner` in StateMachine
- test suite: 601 tests passed, 0 failures

**Manual verification:**
- StateMachine has `l2_runner` field with correct signature
- `_run_l2_phase` imports `load_gate_config` from gate_check (no duplication)
- `_execute_round` calls L2 after L1 and passes l2_findings to merge
- `_merge_findings` merges L2 before L1 (L0 wins on conflict)
- `_apply_autofix_loop_to` filters MUTANT findings before disposition check
- `_run_local` increments/resets consecutive_survivor_rounds based on CONFIRMED MUTANT count
- `_run_local` returns Verdict.FAIL when consecutive_survivor_rounds >= 3
- `_run_ci` reads mutation-result.json and fails on done+survivors
- `_run_ci` launches async mutation via daemon thread
- State has `consecutive_survivor_rounds` field
- `save_state` serializes consecutive_survivor_rounds
- `load_state` deserializes with default 0
- `resolve_forge_path` runs --version with 1s timeout, falls back on failure

## Deviations from Plan

None. Plan executed exactly as written.

## Implementation Notes

**Design decisions honored:**
- **D-01 (l2_runner DI):** l2_runner follows same injectable callable pattern as l0_runner and l1_provider. No-op default returns empty findings and infra_errors.
- **D-03 (CI async):** Wrapper thread (not just Popen) waits for mutmut, parses results, writes mutation-result.json. Initial file = {status: "running"}. Alive PID = skip launch. Dead PID = MUTATION_SKIPPED.
- **D-04 (liveness):** `--version` with 1s timeout, fallback to sys.executable on failure. Logged warning informs user.
- **D-06 (diff-scoping):** `_source_files()` provides diff-scoped files for LOCAL. CI from env (delegated to future plan).
- **MUTANT autofix filter:** source="MUTANT" check before disposition check in `_apply_autofix_loop_to`. Survivors skip autofix loop entirely.
- **consecutive_survivor_rounds:** LOCAL-only counter in State, resets to 0 on clean round, 3 -> Verdict.FAIL with infra_error message.
- **Import gate_check:** load_gate_config imported from gate_check module (no YAML parsing duplication).

**CI async details:**
- Thread is daemon=True so it does not block process exit
- Initial mutation-result.json written with status="running" and PID = thread_ident
- On completion: status="done" with survivors list
- On error: status="error" with error message
- PID liveness check uses `os.kill(pid, 0)` to detect dead processes
- Stale results (PID dead + status="running") produce MUTATION_SKIPPED finding and delete result file

**State schema backward-compatibility:**
- consecutive_survivor_rounds defaults to 0 in `load_state` for pre-02-02 state.json files
- No schema_version bump (additive field per D2 convention)

## Threat Surface

No new threats introduced beyond those in PLAN.md threat model (T-02-05 through T-02-08).

Mitigations implemented:
- **T-02-05 (mutation-result.json tampering):** JSON schema validation on load (status field required)
- **T-02-06 (consecutive_survivor_rounds DoS):** Hard-stop at 3 rounds prevents infinite loop
- **T-02-07 (resolve_forge_path --version):** 1s timeout, output validation (must start with "forge "), fallback to sys.executable

## Next Steps

**For Plan 03 (tests):**
- Unit tests for consecutive_survivor_rounds counter logic
- Integration tests for CI async mutation (launch + result check)
- Liveness check tests for resolve_forge_path (timeout, validation failure)

**For Plan 04 (dogfood + checkpoint):**
- Run mutation on forge's own code changed in this phase
- Verify tests kill mutants (no survivors expected)

## Self-Check: PASSED

**Modified files contain expected changes:**
```
FOUND: src/forge/machine.py contains l2_runner field
FOUND: src/forge/machine.py contains _run_l2_phase method
FOUND: src/forge/machine.py contains if finding.source == "MUTANT": continue
FOUND: src/forge/machine.py contains consecutive_survivor_rounds tracking
FOUND: src/forge/machine.py contains CI async mutation logic
FOUND: src/forge/state.py contains consecutive_survivor_rounds: int = 0
FOUND: src/forge/install_hooks.py contains --version liveness check
```

**Commit exists:**
```
FOUND: 7a15e47
```

**Test suite:**
```
PASSED: 601 tests, 0 failures
```
