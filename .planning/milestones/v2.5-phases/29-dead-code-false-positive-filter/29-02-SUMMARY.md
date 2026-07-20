---
phase: 29-dead-code-false-positive-filter
plan: 02
subsystem: advisory
tags: [dead-code, wiring, sql-dedup, cross-repo, graph-triage]
dependency_graph:
  requires: [_live_callers, LiveCaller, _is_dead_call_site]
  provides: [cross-repo-liveness-filter, graph-triage-liveness-filter, SC3-no-duplication-test]
  affects: [cross_repo_impact.py, graph_triage.py, test_dead_code.py]
tech_stack:
  added: []
  patterns: [shared-helper-extraction, sql-deduplication]
key_files:
  created: []
  modified:
    - src/code_forge/cross_repo_impact.py
    - src/code_forge/graph_triage.py
    - tests/test_dead_code.py
decisions:
  - "cross_repo_impact builds result dicts from LiveCaller fields, preserving the existing dict shape"
  - "graph_triage site B uses len(live) for dependent_count and slices live[:5] for top_dependents"
  - "graph_triage site C returns [lc.qualified for lc in live], preserving the existing return type"
  - "sem branch in find_entity_dependents untouched per D-09"
metrics:
  duration: 9m
  completed: 2026-06-26
---

# Phase 29 Plan 02: Wire Advisory Axes Through _live_callers Summary

Replaced triplicated inline CALLS+IMPORTS_FROM SQL in cross_repo_impact.py (1 site) and graph_triage.py (2 sites) with shared _live_callers from dead_code.py, eliminating SQL duplication (SC#3) and enabling dead-code filtering (SC#1) across both advisory axes.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wire cross_repo_impact.py through _live_callers | dd28f32 | src/code_forge/cross_repo_impact.py |
| 2 | Wire graph_triage.py at sites B and C | ca2ffbb | src/code_forge/graph_triage.py |
| 3 | Add TestNoSqlDuplication (SC#3) | 3bce3c7 | tests/test_dead_code.py |

## Key Changes

### src/code_forge/cross_repo_impact.py (-31/+7 lines)
- Added `from .dead_code import _live_callers`
- Replaced 31-line inline SQL + per-caller resolution loop in `find_cross_repo_callers` with `_live_callers(cursor, name, module_name)` call
- Result dict construction now reads from LiveCaller fields (qualified, file, line)

### src/code_forge/graph_triage.py (-34/+6 lines)
- Added `from .dead_code import _live_callers`
- Site B (`_run_graphdb`): replaced 15-line inline SQL + dependent counting with `_live_callers` call; `dependent_count` uses `len(live)`, `top_dependents` uses `[lc.qualified for lc in live[:5]]`
- Site C (`find_entity_dependents` graphdb branch): replaced 14-line inline SQL with `_live_callers` call; returns `[lc.qualified for lc in live]`
- sem branch untouched per D-09

### tests/test_dead_code.py (+27 lines, 3 new tests)
- TestNoSqlDuplication: 3 tests verifying `c.kind = 'CALLS'` SQL pattern absent from cross_repo_impact.py and graph_triage.py, present in dead_code.py

## Verification Results

- cross_repo_impact.py: 0 inline CALLS SQL, 0 inline IMPORTS_FROM SQL
- graph_triage.py: 0 inline CALLS SQL, 0 inline IMPORTS_FROM SQL
- `python3 -m pytest tests/test_cross_repo_impact.py`: 19 passed
- `python3 -m pytest tests/test_graph_triage.py`: 25 passed
- `python3 -m pytest tests/test_dead_code.py`: 38 passed (35 original + 3 new)
- Full suite: 2101 passed, 7 skipped

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all wiring is complete.

## Self-Check: PASSED
