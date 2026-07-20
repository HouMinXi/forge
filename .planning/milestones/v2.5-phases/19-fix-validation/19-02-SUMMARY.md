---
phase: 19-fix-validation
plan: 02
subsystem: fixval-pipeline-wiring
tags: [fixval, machine, eval, integration-tests]
dependency_graph:
  requires: [19-01]
  provides: [FIXVAL gate in machine.py, FixvalAxisHook in eval runner]
  affects: [machine.py, eval/runner.py]
tech_stack:
  added: []
  patterns: [lazy-import gate, post-convergence blocking gate, eval axis hook]
key_files:
  created:
    - tests/test_fixval_integration.py
  modified:
    - src/code_forge/machine.py
    - src/code_forge/eval/runner.py
decisions:
  - "FIXVAL gate wired as last check in _finalize_local_terminal before PASS verdict (D-06)"
  - "FixvalSkip records DISMISSED finding with reason, proceeds to PASS (D-08)"
  - "block_message stored in StateFinding.error field, not infra_errors"
  - "Overfit guard runs only on PASS status (D-03 advisory only)"
  - "_get_commit_message uses git rev-parse --git-path for worktree safety"
  - "FIXVAL added to autofix skip set alongside MUTANT and E2E_CHECK"
metrics:
  duration: 12m
  completed: 2026-06-11T16:27:00Z
  tasks_completed: 3
  tasks_total: 3
  test_count: 12
  files_changed: 3
---

# Phase 19 Plan 02: Wire FIXVAL into Pipeline Summary

FIXVAL gate integrated into machine.py _finalize_local_terminal with FixvalAxisHook registered in eval runner and 12 integration tests covering all gate paths.

## Commits

| Task | Commit | Message | Files |
|------|--------|---------|-------|
| 1 | 720ba0a | feat(19-02): wire FIXVAL gate into machine.py _finalize_local_terminal | src/code_forge/machine.py |
| 2 | 87cabd2 | test(19-02): add FIXVAL integration tests for pipeline wiring | tests/test_fixval_integration.py |
| 3 | 7984d6f | feat(19-02): register FixvalAxisHook in eval runner | src/code_forge/eval/runner.py, tests/test_fixval_integration.py |

## Implementation Details

### Task 1: Wire FIXVAL into machine.py

Extended `_finalize_local_terminal` with the FIXVAL gate logic:
- Lazy imports `classify_fixval_candidate`, `run_fixval`, `run_overfit_guard` from `.fixval` (matching existing lazy-import style for taint and gate_check)
- `FixvalSkip` path: records DISMISSED `FIXVAL_SKIPPED` finding, proceeds to PASS
- `FixvalCandidate` path: reads test command from gate.yaml, commit message via `_get_commit_message`, diff text from resolved review
- BLOCK path: sets `Verdict.FAIL`, stores `block_message` in finding's `error` field
- PASS path: runs overfit guard (advisory only), then sets `Verdict.PASS`
- WAIVED/SKIPPED paths: skip overfit guard, proceed to PASS

Added `_get_commit_message` private method:
- Uses `git rev-parse --git-path COMMIT_EDITMSG` (worktree-safe)
- Falls back to `git log -1 --format=%B` for post-commit / CI
- Returns empty string on any failure with logger.warning

Updated autofix skip set: `("MUTANT", "E2E_CHECK", "FIXVAL")` -- FIXVAL findings are gate-mechanism outcomes, not code defects.

### Task 2: Integration Tests

Created `tests/test_fixval_integration.py` with 12 tests:
- `test_hollow_returns_fail`: hollow test -> Verdict.FAIL + FIXVAL_HOLLOW in findings
- `test_nonhollow_returns_pass`: non-hollow -> Verdict.PASS, no FIXVAL_HOLLOW
- `test_skip_records_finding`: FixvalSkip -> FIXVAL_SKIPPED DISMISSED finding
- `test_waiver_advisory_emitted`: WAIVED -> PASS + advisory serialized to JSON
- `test_overfit_advisory_in_advisories`: overfit advisory in machine._advisories
- `test_no_fixval_findings_on_fail`: non-converged machine has zero FIXVAL findings
- `test_reads_commit_editmsg`: COMMIT_EDITMSG path resolved and read
- `test_falls_back_to_git_log`: fallback to git log -1
- `test_returns_empty_on_failure`: empty string on exception
- `test_eval_fixval_hook_registered`: FixvalAxisHook in _AXIS_HOOKS
- `test_eval_fixval_scores_bug_p12_01`: hook processes FIXVAL-tagged entries
- `test_eval_fixval_ignores_non_fixval_entry`: hook skips non-FIXVAL entries

Mock strategy: patch at `code_forge.fixval.*` (not `code_forge.machine.*`) because machine.py uses function-local lazy imports.

### Task 3: FixvalAxisHook in eval runner

Added `FixvalAxisHook(AxisHook)` class:
- `pre_review`: no-op (FIXVAL runs inside forge's pipeline)
- `post_review`: checks `entry.axis_tags` for "FIXVAL", scores based on verdict
- Registered at module level via `register_axis_hook(FixvalAxisHook())`

DETERMINISTIC_TAGS already includes "FIXVAL" (no change needed). Corpus entries BUG-P12-01 and ttl_class will exercise the hook.

## Deviations from Plan

None -- plan executed exactly as written.

## Test Results

```
tests/test_fixval.py:         35 passed
tests/test_fixval_integration.py: 12 passed
tests/test_machine_*.py:      56 passed
Full suite (--ignore=tests/eval): 1415 passed, 5 skipped
```

No regressions.

## Known Stubs

None -- all paths are fully wired.

## Self-Check: PASSED

All 3 created/modified files exist. All 3 commit hashes verified in git log.
