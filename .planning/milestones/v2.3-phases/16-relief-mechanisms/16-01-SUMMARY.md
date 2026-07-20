---
phase: 16-relief-mechanisms
plan: 01
subsystem: diff-tiering
tags: [pure-functions, tdd, diff-analysis, state-schema]
dependency_graph:
  requires: []
  provides: [count_diff_lines, tier_threshold, INFRA-source-type]
  affects: [machine.py, cli.py, factories.py, outlet_c.py]
tech_stack:
  added: []
  patterns: [pure-function-tiering, priority-chain-dispatch]
key_files:
  created: []
  modified:
    - src/code_forge/diff.py
    - src/code_forge/state.py
    - tests/test_diff.py
decisions:
  - "tier_threshold priority chain: env_override > whole_file > line_count > default"
  - "count_diff_lines returns 0 for all unmeasurable diffs (safe default -> 3 cycles)"
  - "line_count=0 maps to 3 cycles (safe default), not 2 (relief tier)"
metrics:
  duration: 18m
  completed: 2026-06-09
---

# Phase 16 Plan 01: Diff-Size Counting and Tier Threshold Summary

Two pure functions (count_diff_lines, tier_threshold) for converting diff text to a cycle threshold integer, plus INFRA source type for F3 fail-closed fix.

## What Was Built

### count_diff_lines(diff_text) -> int
Counts insertions + deletions across all hunks in a unified diff using
unidiff.PatchSet. Returns 0 for empty, None, malformed, binary,
rename-only, and mode-only diffs. Follows existing diff.py patterns
(guard empty/None, try/except UnidiffParseError).

### tier_threshold(line_count, whole_file, env_override) -> int
Maps diff line count to review cycle threshold with a priority chain:
1. env_override always wins (D-03) -- floor-clamped to max(1, value)
2. whole_file forces 3 cycles (D-04) -- prevents artificial inflation
3. line_count <= 0 returns 3 (safe default for parse errors)
4. line_count < 50 returns 2 (small diff relief per D-01)
5. line_count >= 200 returns 4 (large diff extra scrutiny per D-01)
6. else returns 3 (default)

### StateFinding.source INFRA type
Expanded the Literal type from 5 to 6 values, adding "INFRA" for
infrastructure error findings (invoke-fail, schema-fail, spawn-fail).
This enables the F3 falsifier-skip guard in Plan 02.

## Test Coverage

16 new tests in test_diff.py (+ 4 boundary parametrize cases = 20 test invocations):

### TestCountDiffLines (8 tests)
- test_count_diff_lines_empty: ("", None) -> 0
- test_count_diff_lines_added_only: 3-line addition -> 3
- test_count_diff_lines_removed_only: 2-line deletion -> 2
- test_count_diff_lines_mixed: 3 add + 2 del -> 5
- test_count_diff_lines_parse_error: malformed text -> 0
- test_count_diff_lines_binary: binary diff -> 0
- test_count_diff_lines_rename_only: rename-only -> 0
- test_count_diff_lines_mode_only: mode-only -> 0

### TestTierThreshold (8 tests + 4 parametrize)
- test_tier_threshold_env_override: env_override=5 -> 5
- test_tier_threshold_env_override_clamp: env_override=0 -> 1
- test_tier_threshold_whole_file: whole_file=True -> 3
- test_tier_threshold_small: line_count=10 -> 2
- test_tier_threshold_medium: line_count=100 -> 3
- test_tier_threshold_large: line_count=200 -> 4
- test_tier_threshold_zero: line_count=0 -> 3
- test_tier_threshold_one: line_count=1 -> 2
- test_tier_threshold_boundaries: 49->2, 50->3, 199->3, 200->4

## Verification Results

- All 32 tests in test_diff.py pass (20 existing + 12 new)
- 128 tests across 8 core test files pass with 0 failures
- grep -c "def count_diff_lines" src/code_forge/diff.py == 1
- grep -c "def tier_threshold" src/code_forge/diff.py == 1
- grep "INFRA" src/code_forge/state.py shows expanded Literal
- No non-ASCII characters in changes
- All modified files pass syntax check

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **count_diff_lines signature accepts str | None**: matches existing
   diff.py guard pattern and test requirement for None input.

2. **tier_threshold line_count=0 returns 3 (safe default)**: a parse
   failure or empty diff should not grant relief (2 cycles) since we
   cannot verify the diff is actually small. The safe default of 3
   cycles matches the pre-tiering behavior.

## Commits

| Task | Type | Hash | Description |
|------|------|------|-------------|
| 1 RED | test | 04eb7a4 | Failing tests for count_diff_lines and tier_threshold |
| 1 GREEN | feat | 32c3e5e | Implement count_diff_lines, tier_threshold, add INFRA source |

## Self-Check: PASSED
