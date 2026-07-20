---
phase: 19-fix-validation
plan: 01
subsystem: fixval
tags: [fixval, tdd, revert-red, overfit, waiver]
dependency_graph:
  requires: [advisory.py, state.py, disposition.py, mutation.py]
  provides: [fixval.py]
  affects: [state.py]
tech_stack:
  added: [unidiff]
  patterns: [ast-transform, git-apply-revert, dual-channel-waiver]
key_files:
  created:
    - src/code_forge/fixval.py
    - tests/test_fixval.py
  modified:
    - src/code_forge/state.py
decisions:
  - "BLOCK finding uses DISMISSED disposition (block via Verdict.FAIL, not CONFIRMED)"
  - "Dual-channel waiver: FIXVAL_WAIVER env var + Fixval-Waiver trailer, env takes precedence"
  - "Revert patch derived from diff_text via unidiff.PatchSet (same source as classify)"
  - "Overfit guard saves original bytes before ast.unparse, restores verbatim in finally"
  - "AST NodeTransformer for variable rename (not regex -- avoids f-string/decorator breakage)"
metrics:
  duration: 5m 0s
  completed: 2026-06-11T15:53:16Z
  tasks: 1/1
  test_count: 35
  files_created: 2
  files_modified: 1
---

# Phase 19 Plan 01: FIXVAL Core Module Summary

FIXVAL core with TDD: structural trigger classifies code+test pairing, revert-RED/restore-GREEN via unidiff PatchSet + git apply -R proves tests are not hollow, dual-channel waiver (env var + trailer) with advisory record, AST-based overfit guard emits advisory on variable-rename break.

## Task Completion

| Task | Name | Type | Commit | Key Files |
|------|------|------|--------|-----------|
| 1 (RED) | FIXVAL core tests | test | ba778b9 | tests/test_fixval.py |
| 1 (GREEN) | FIXVAL core impl | feat | 311641c | src/code_forge/fixval.py, src/code_forge/state.py |

## What Was Built

### classify_fixval_candidate (D-01)
Structural trigger: classifies a file list as FixvalCandidate (both test and non-test files) or FixvalSkip (only one kind). Multi-language test patterns: Python (tests/test_*.py, *_test.py, test_*.py), TypeScript (*.test.ts, *.spec.ts), Go (*_test.go).

### parse_fixval_waiver (D-04/D-05)
Dual-channel waiver parsing. Channel 1: FIXVAL_WAIVER env var (primary at pre-commit time). Channel 2: Fixval-Waiver trailer in commit message (case-insensitive). Env takes precedence. Empty reason returns None (reason required).

### run_fixval (D-02)
Revert-RED/restore-GREEN engine:
1. Guard: diff_text=None -> SKIPPED
2. Waiver check via parse_fixval_waiver(commit_message, env=os.environ)
3. Baseline guard: reuses mutation._run_baseline_guard with strip-retry
4. Revert: parse diff_text via unidiff.PatchSet, filter to non-test files, git apply -R
5. Test: scoped_cmd = test_cmd + candidate.test_files; FAIL -> PASS, PASS -> BLOCK
6. Restore: git apply (forward re-apply) in finally block, never git checkout --

### run_overfit_guard (D-03)
AST-based variable-rename transform using ast.NodeTransformer. Saves original bytes before ast.unparse, restores verbatim in finally (never re-unparse). Advisory only: if test breaks after rename -> "test may be overfitting" advisory. Non-.py files skipped.

### FixvalResult
Frozen dataclass with status, findings, advisories, block_message. BLOCK -> DISMISSED StateFinding (block via Verdict.FAIL, not CONFIRMED -- CONFIRMED blocks reconvergence). PASS -> empty findings. SKIPPED/WAIVED -> DISMISSED with reason.

### StateFinding.source extension
Added "FIXVAL" to the source Literal in state.py. All FIXVAL findings use source="FIXVAL" to distinguish from R2 mutation findings.

## Decisions Made

1. **DISMISSED for BLOCK finding** -- CONFIRMED would prevent reconvergence in _fixpoint_reached; the actual block is via Verdict.FAIL set by machine.py
2. **Env var takes precedence** -- at pre-commit time the commit message is not finalized; env var is the channel that works there
3. **unidiff.PatchSet for revert patch** -- guarantees source agreement with classify (same diff_text), avoids staged-vs-unstaged mismatch
4. **Byte-save/restore for overfit** -- ast.unparse is lossy (strips comments, formatting); original bytes restored verbatim
5. **AST NodeTransformer** -- regex rename would break f-strings, decorators, kwargs; AST transform is semantically safe

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- 35 tests passing (>= 21 required)
- All 8 exports verified: classify_fixval_candidate, run_fixval, run_overfit_guard, parse_fixval_waiver, FixvalResult, FixvalSkip, FixvalCandidate, FixvalStatus
- All acceptance criteria grep checks satisfied
- Non-ASCII check: PASS
- State schema tests: 12 passed (no regression)

## Self-Check: PASSED

- [x] src/code_forge/fixval.py exists
- [x] tests/test_fixval.py exists
- [x] src/code_forge/state.py modified (FIXVAL in Literal)
- [x] Commit ba778b9 exists (RED)
- [x] Commit 311641c exists (GREEN)
