---
phase: 27-cross-repo-impact-via-register
plan: 01
subsystem: cross-repo-impact
tags: [advisory, cross-repo, graph-triage, registry]
dependency_graph:
  requires: [code-review-graph registry API, graph_triage.py helpers]
  provides: [CrossRepoImpactRunner, resolve_changed_symbols, find_cross_repo_callers]
  affects: [cross_repo.py wiring (plan 27-02)]
tech_stack:
  added: []
  patterns: [AxisRunner Protocol, infra_errors SKIP, token-set Jaccard proximity]
key_files:
  created:
    - src/code_forge/cross_repo_impact.py
    - tests/test_cross_repo_impact.py
  modified: []
decisions:
  - "D-06 implemented: token-set Jaccard proximity over path segments, not prefix-based"
  - "Reused _parse_diff_files and _is_unnamed from graph_triage.py (no duplicate logic)"
  - "Used get_db_path from code-review-graph.incremental (canonical W-1)"
metrics:
  duration: 5m
  completed: 2026-06-23
---

# Phase 27 Plan 01: CrossRepoImpactRunner Advisory Axis Summary

R0 cross-repo direct-caller impact runner using code-review-graph Registry and per-sibling graph.db CALLS+IMPORTS_FROM queries, with four SKIP causes routed to infra_errors and token-set Jaccard ranking.

## What Was Built

CrossRepoImpactRunner advisory axis that:
1. Parses changed files from the diff via reused `_parse_diff_files`
2. Resolves changed symbols from the primary repo's graph.db
3. Enumerates sibling repos via code-review-graph Registry (CRG_REGISTRY_PATH seam)
4. Queries each sibling's graph.db for CALLS edges targeting changed symbols
5. Uses IMPORTS_FROM disambiguation (same query as GraphTriageRunner)
6. Opens all databases read-only (`?mode=ro`)
7. Ranks findings by token-set Jaccard subsystem proximity (D-06)
8. Caps output at TOP_N (10) findings
9. Caches results after first run

## SKIP Behavior (SC-2)

Four causes append to `infra_errors` and return `[]` without crashing:
- Primary graph.db not found
- No sibling repos registered
- Sibling graph.db missing
- Sibling graph.db corrupt/unreadable (sqlite3.Error/OSError)

Genuine no-callers (sibling present, no CALLS match) returns `[]` with EMPTY `infra_errors` -- the D-04 distinguisher.

## Test Coverage

19 tests across 8 test classes:
- `TestAdvisoryContract` (3): is_advisory, empty diff, whitespace diff
- `TestResolveChangedSymbols` (2): named nodes resolved, unnamed skipped
- `TestFindCrossRepoCallers` (2): matching caller found, unrelated yields none
- `TestSkipStates` (4): primary db missing, no siblings, sibling db missing, sibling db corrupt
- `TestGenuineNoCallers` (1): [] with empty infra_errors
- `TestFindingShape` (1): id/axis/file/line_range/description/attribution
- `TestSubsystemProximity` (4): shared tokens, same path, disjoint, ordering
- `TestTopNCap` (1): output capped at _TOP_N
- `TestCaching` (1): second run returns cached result

All tests use real sqlite files on disk (not mocks).

## Bug Injection Verification

Three bug injections per the plan's verification section:

1. **Missing registry**: CRG_REGISTRY_PATH at nonexistent path -> `[]` + infra_errors non-empty; restore -> findings appear. PASSED.
2. **Truncated sibling db**: 0-byte graph.db -> infra_error mentioning sibling alias, no crash; restore -> caller found. PASSED.
3. **Deleted CALLS edge**: remove CALLS from sibling (keep symbol) -> `[]` with EMPTY infra_errors (genuine no-impact); restore -> finding returns. PASSED.

## Step 0 Results

- `python -m py_compile`: both files clean
- `ruff check`: clean (one unused `os` import fixed in refactor commit)
- Non-ASCII grep: clean

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| 1ce7d96 | test | Failing tests for CrossRepoImpactRunner (RED) |
| 4e5719f | feat | Implement CrossRepoImpactRunner advisory axis (GREEN) |
| 4a080b1 | refactor | Remove unused os import from test (Step 0 fix) |

## Deviations from Plan

None -- plan executed exactly as written.

## TDD Gate Compliance

1. `test(...)` commit exists: 1ce7d96 (RED gate)
2. `feat(...)` commit exists after it: 4e5719f (GREEN gate)
3. `refactor(...)` commit exists after GREEN: 4a080b1 (REFACTOR gate)

All gates satisfied.

## Known Stubs

None -- all functions are fully implemented with real logic.

## Self-Check: PASSED

- src/code_forge/cross_repo_impact.py: FOUND
- tests/test_cross_repo_impact.py: FOUND
- 27-01-SUMMARY.md: FOUND
- Commit 1ce7d96 (RED): FOUND
- Commit 4e5719f (GREEN): FOUND
- Commit 4a080b1 (REFACTOR): FOUND
