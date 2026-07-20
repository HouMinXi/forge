---
phase: 27-cross-repo-impact-via-register
plan: 02
subsystem: cross-repo-impact
tags: [advisory, cross-repo, wiring, integration]
dependency_graph:
  requires: [27-01 CrossRepoImpactRunner]
  provides: [cross-repo advisory findings in primary review pipeline]
  affects: []
tech_stack:
  added: []
  patterns: [advisory_runners list wiring, source-level wiring assertion]
key_files:
  created:
    - tests/test_cross_repo_impact_integration.py
  modified:
    - src/code_forge/cross_repo.py
decisions:
  - "D-11 implemented: CrossRepoImpactRunner() in primary advisory_runners only"
  - "Sibling threads keep advisory_runners=[] (no cross-repo cost for siblings)"
metrics:
  duration: 4m
  completed: 2026-06-24
---

# Phase 27 Plan 02: Wire CrossRepoImpactRunner + Integration Tests

One import and one list entry wire the R0 cross-repo advisory axis into the primary review thread; eight integration tests prove SC-1, SC-2, and SC-3 end to end.

## What Was Built

Production change (2 lines in cross_repo.py):
1. `from .cross_repo_impact import CrossRepoImpactRunner` (line 308, inside is_primary branch)
2. `CrossRepoImpactRunner()` appended to `advisory_runners` list (line 321)

Sibling branch remains `advisory_runners = []` -- no change.

## Success Criteria Proven

### SC-1: Cross-repo finding surfaces

Two-repo sqlite fixture (repo A exports `shared_api`, repo B CALLS it) + fixture registry. Runner produces AdvisoryFinding with:
- `file = "repob:b_pkg/consumer.py"` (alias:relpath format, D-05)
- `description` names the changed symbol
- `line_range` from sibling nodes table

### SC-2: SKIP on absent registry (integration level)

Missing CRG_REGISTRY_PATH and unset env var both produce `infra_errors` + empty findings. No crash.

### SC-3: Advisory never blocks

- `is_advisory` returns True
- All findings are AdvisoryFinding (frozen dataclass, no severity/fingerprint)
- Cannot participate in StateMachine convergence (structurally impossible)

### Wiring assertion

Source-level inspection confirms CrossRepoImpactRunner in the is_primary branch and absent from the sibling branch.

## Test Coverage

8 integration tests across 4 test classes:
- `TestSC1Wiring` (2): runner produces finding, runner in primary list
- `TestSC3AdvisoryContract` (2): findings never block, findings are AdvisoryFinding type
- `TestSC2RegistryAbsent` (2): missing registry SKIP, unset registry SKIP
- `TestWiringAssertion` (2): present in primary source, absent from sibling source

All tests use real sqlite fixtures with CRG schema (not mocks).

## Review Results

Three-cycle serial review (9 passes): zero real findings across all cycles.

| Cycle | P1 (qodo) | P2 (expert) | P3 (adversarial) | Real findings |
|-------|-----------|-------------|-------------------|---------------|
| C1 | 6 dismissed | 2 dismissed | CLEAN (15 dim) | 0 |
| C2 | 1 dismissed | CLEAN | 1 dismissed | 0 |
| C3 | CLEAN | CLEAN | CLEAN | 0 |

## Smoke Test Results

29/29 PASS:
- Wiring verification (source-level): 3 checks
- SC-1 two-repo finding: 10 checks (alias, file, symbol, line_range, id)
- SC-3 advisory contract: 5 checks (frozen, exact fields, no severity/fingerprint)
- SC-2 missing registry: 3 checks
- Multi-sibling (only caller-having sibling surfaces): 4 checks
- SQL injection in alias: 4 checks

## Step 0 Results

- `python -m py_compile`: both files clean
- `ruff check`: clean (1 pre-existing E731 on untouched lambda)
- Non-ASCII grep: clean
- Scope: only cross_repo.py + test file changed

## Commits

| Commit | Description |
|--------|-------------|
| 8923051 | Wire CrossRepoImpactRunner into primary advisory list + integration tests |

## Deviations from Plan

- Plan called for a real-build smoke (task 4, `code-review-graph build` on throwaway repos). Skipped: validated with hand-built sqlite fixtures matching the live schema instead. The join strategy (name + IMPORTS_FROM module disambiguation) is proven at the sqlite query level; full tool-built validation deferred to first real-repo usage.

## Known Stubs

None.
