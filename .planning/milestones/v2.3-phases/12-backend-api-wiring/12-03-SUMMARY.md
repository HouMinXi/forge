---
phase: 12-backend-api-wiring
plan: 03
subsystem: cli
tags: [cli, whole-file, multi-file, refactor, dry, F1, F2, F3]
dependency_graph:
  requires:
    - 12-02 (backend wiring, inline flags, gate.yaml loader)
  provides:
    - --whole-file nargs='+' multi-file review capability (F3, D-09)
    - _resolve_whole_file_specs shared function (F2, D-08)
    - Flattened mutual-exclusion checks in _resolve_whole_file_specs (F1, D-07)
  affects:
    - src/code_forge/cli.py
tech_stack:
  added: []
  patterns:
    - nargs='+' argparse argument for multi-value optional flags
    - shared validation function to eliminate duplicate getattr+validate pattern
    - flat if-checks replacing for-loop abstraction over heterogeneous flags
key_files:
  created: []
  modified:
    - src/code_forge/cli.py
decisions:
  - D-07: F1 for-loop flattened to 4 independent if-checks in _resolve_whole_file_specs
  - D-08: F2 whole_file logic merged into _resolve_whole_file_specs (returns 3-tuple)
  - D-09: F3 --whole-file expanded to nargs='+' multi-file
  - Ordered T1->T2->T3 per RESEARCH Pitfall 4 (expand nargs first, then merge, then flatten)
metrics:
  duration: "~25 minutes"
  completed: "2026-06-04T14:16:33Z"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 1
---

# Phase 12 Plan 03: F1/F2/F3 cli.py Cleanup Summary

**One-liner:** --whole-file expanded to nargs='+' multi-file; duplicated whole_file logic merged into _resolve_whole_file_specs; mutual-exclusion for-loop flattened to 4 independent if-checks.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| T1 | F3: --whole-file nargs='+' multi-file (D-09) | 67760d5 | src/code_forge/cli.py |
| T2 | F2: extract _resolve_whole_file_specs shared function (D-08) | 5d7dd2b | src/code_forge/cli.py |
| T3 | F1: flatten mutual-exclusion loop to 4 independent checks (D-07) | 6afd038 | src/code_forge/cli.py |

## Changes Made

### Task 1: F3 - Expand --whole-file to nargs='+' (D-09)

Added --whole-file nargs='+' to the review subparser. Updated _build_baseline_specs
to handle whole_file as a list with:
- Mutual-exclusion checks (as for-loop per ordering constraint for T3)
- Path validation: all entries must be relative and resolve under cwd
- 2-tuple return (EmptyBaseline, head_spec) unchanged for callers at cli.py:610

Updated _paths to return [Path(p) for p in whole_file] when whole_file is set.
Both functions use getattr(args, "whole_file", None) for backward compat when
the whole_file attribute is absent (non-review subcommands).

### Task 2: F2 - Extract _resolve_whole_file_specs (D-08)

Created _resolve_whole_file_specs(args, cwd) that consolidates the duplicate
getattr(args, "whole_file", None) + validate pattern from both functions:
- Returns None when whole_file is not set (callers fall through)
- Computes in_git internally via is_git_repo(cwd)
- Validates all paths at a single point
- Returns 3-tuple: (EmptyBaseline(), head_spec, [Path, ...])

_build_baseline_specs calls the shared function and unpacks (baseline, head, _)
returning the 2-tuple -- caller signature at cli.py:610 unchanged.
_paths calls the shared function and unpacks (_, _, paths_list) returning paths.

### Task 3: F1 - Flatten mutual-exclusion loop (D-07)

Replaced the for-loop over [("committed", ...), ("staged", ...), ("baseline", ...), ("head", ...)]
with 4 independent if-checks in _resolve_whole_file_specs. Pure refactor:
- Error messages identical: "--whole-file cannot be combined with --committed/--staged/--baseline/--head"
- committed/staged use getattr with False default (booleans)
- baseline/head use is-not-None test (string values)
- No behavior change verified by running test suite

## Verification

- python3 -m py_compile passed on cli.py after each task
- ruff check: zero new errors after each task
- Non-ASCII check: no non-ASCII in new code after each task
- pytest tests/: 996 passed, 0 failures (baseline 996, no regressions)
- _resolve_whole_file_specs unit test: all 5 scenarios (None return, 3-tuple, 2-tuple unpack, paths unpack, absolute path rejection)
- F1 behavioral test: all 4 conflict error messages match expected strings exactly

## Deviations from Plan

### Auto-fixed Issues

None. Plan executed exactly as written with one ordering clarification:
the for-loop (to be flattened in T3) was written in T1 and moved to
_resolve_whole_file_specs in T2, so T3 flattens it within that function.
This matches the T1->T2->T3 ordering and D-07/D-08/D-09 decision sequence.

### Discovery: Main Repo Had Single-Value --whole-file

**Found during:** Task 1 setup
**Issue:** The main repo's cli.py (HEAD a18c6d5) already contains a single-value
--whole-file default=None argument and a for-loop at lines 1105-1125. The worktree's
cli.py did not have this feature (Wave 2 executor applied backend wiring on a different
base commit). The worktree's cli.py needed --whole-file added from scratch.
**Fix:** Implemented --whole-file fresh in the worktree following the plan's
F1->F2->F3 decomposition. Functionally equivalent to the main repo version with
D-08 merge and D-07 flattening applied.
**Impact:** No behavior difference.

## Known Stubs

None. All whole_file logic is live:
- nargs='+' parser wired to review subcommand
- _resolve_whole_file_specs called from both _build_baseline_specs and _paths
- Path validation rejects absolute paths and .. escapes
- Mutual-exclusion raises CliError for each conflict case

## Threat Flags

T-12-07 (--whole-file path traversal) disposition: MITIGATED.
Validation in _resolve_whole_file_specs rejects absolute paths and paths
that escape cwd via .resolve().relative_to(cwd_resolved) check.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| src/code_forge/cli.py | FOUND |
| nargs="+" in cli.py | FOUND (line 211) |
| def _resolve_whole_file_specs | FOUND (line 1013) |
| if args.committed raise CliError | FOUND (line 1028) |
| if args.staged raise CliError | FOUND (line 1030) |
| if args.baseline is not None raise CliError | FOUND (line 1032) |
| if args.head is not None raise CliError | FOUND (line 1034) |
| _resolve_whole_file_specs called in _build_baseline_specs | FOUND (line 1062) |
| _resolve_whole_file_specs called in _paths | FOUND (line 1124) |
| for-loop absent | CONFIRMED (grep count=0) |
| Commit 67760d5 | FOUND |
| Commit 5d7dd2b | FOUND |
| Commit 6afd038 | FOUND |
| 996 tests passing | CONFIRMED |
