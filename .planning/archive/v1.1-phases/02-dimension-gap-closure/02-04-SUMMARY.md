---
phase: 02-dimension-gap-closure
plan: 04
subsystem: cli
tags: [colocation, shadow-mode, dimension-merging, DIM-06]
dependency_graph:
  requires: [02-03]
  provides: [compute_colocation_matrix, show_colocation, shadow_filter, promote_shadow_dimension]
  affects: [cli/forge_cli.py, cli/config.json]
tech_stack:
  added: []
  patterns: [itertools.combinations for pair generation, chained .get() config access]
key_files:
  created: []
  modified: [cli/forge_cli.py, cli/config.json]
decisions:
  - "R12: both directional rates must exceed threshold for merge candidate (not just min)"
  - "Shadow filter defaults to exclude; explicit --shadow flag to include"
  - "promote_shadow_dimension writes both findings.json and config.json promoted_dimensions"
metrics:
  duration: 3m
  completed: 2026-05-13
  tasks: 1
  files: 2
---

# Phase 02 Plan 04: Co-location Analysis and Shadow Mode Summary

Co-location matrix for data-driven dimension merging (DIM-06), shadow mode filtering in evaluation/stats/recommendations, and --promote command for shadow-to-active promotion.

## One-liner

Co-location matrix with bidirectional rate analysis (R12), shadow filter on eval/stats/recommend (R7), and --promote for shadow dimension promotion (R6/N4).

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 83c093a | cli/forge_cli: add co-location analysis, shadow filter, --promote command |

## What Was Built

### Co-location Analysis (DIM-06)

- `compute_colocation_matrix(findings)`: Groups findings by (file, line), counts dimension pairs sharing same location using `itertools.combinations`. Excludes shadow findings and findings without valid coordinates.
- `_dimension_finding_counts(findings)`: Helper to count total non-shadow findings per dimension.
- `show_colocation(json_format=False)`: Displays co-location matrix with merge recommendations. Reads `min_colocation_findings` (default 20) and `merge_threshold` (default 0.30) from config.json colocation section.

### Shadow Mode Filter

- `evaluate_dimensions()` (R3 fix): Signature changed to `(findings, config=None, json_format=False, include_shadow=False)`. The `config` parameter is preserved. Shadow filter applied at function entry before any processing.
- `show_stats()`: Signature changed to `(json_format=False, include_shadow=False)`. Shadow filter applied after loading findings.
- `show_recommendations()` (R7 fix): Shadow filter applied after loading findings, before grouping by dimension.

### Promote Command (R6/N4)

- `promote_shadow_dimension(dimension_name)`: Two-action promotion: (a) updates existing findings `shadow=True` to `shadow=False`, (b) adds dimension to `config.json` `promoted_dimensions` list so future findings are created as active.

### CLI Arguments

- `--colocation`: Show dimension co-location analysis
- `--shadow`: Include shadow dimensions in `--eval` and `--stats` output
- `--promote DIM`: Promote shadow dimension to active

### Config

- `cli/config.json`: Added `colocation` section with `min_colocation_findings: 20` and `merge_threshold: 0.30`.

## R-fix Compliance

| Fix | Description | Status |
|-----|-------------|--------|
| R3 | evaluate_dimensions preserves config=None parameter | PASS |
| R6 | --promote command implemented via promote_shadow_dimension() | PASS |
| R7 | show_recommendations filters shadow findings after load | PASS |
| R12 | Both directional rates (d1->d2, d2->d1) computed and both must exceed threshold | PASS |
| N4 | promote_shadow_dimension writes config.json promoted_dimensions | PASS |

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **R12 bidirectional threshold**: Both `rate_d1` and `rate_d2` must independently exceed the merge threshold. A pair where only one direction exceeds 30% is labeled "below threshold", not "MERGE CANDIDATE". This prevents asymmetric merges where a small dimension is subsumed by a large one just because its findings happen to co-locate.

2. **Shadow filter default behavior**: All three functions (evaluate_dimensions, show_stats, show_recommendations) default to excluding shadow findings. Only explicit `--shadow` flag includes them. This prevents Pitfall 3 (shadow findings skewing FP statistics).

3. **Promote persistence**: promote_shadow_dimension writes to both findings.json (retroactive) and config.json promoted_dimensions (prospective), ensuring both existing and future findings for the promoted dimension are active.

## Self-Check: PASSED
