---
phase: 01b-trust-calibration
plan: 03
subsystem: cli
tags: [evaluation, recommendation, tricorder-4, rule-improvement, D3, D5, H3, H4]
dependency_graph:
  requires: [01b-01, 01b-02]
  provides: [evaluate_dimensions, generate_recommendation, show_recommendations]
  affects: [cli/forge_cli.py, .planning/ROADMAP.md]
tech_stack:
  added: []
  patterns: [dimension-aggregation, wilson-score-ci, terminal-table-dual-output]
key_files:
  created: []
  modified:
    - cli/forge_cli.py
    - .planning/ROADMAP.md
    - .planning/REQUIREMENTS.md
decisions:
  - "INTENTIONAL routes to improve_detection, not adjust_scope (H3 -- tool flagged intentional code means detection needs improvement)"
  - "Tricorder 4 criteria: Understandable and Actionable require manual review (cannot automate per RESEARCH.md Open Question 2)"
  - "Phase 2 success criterion updated from 50% effectiveness to 10% ToolFP rate (H4 -- aligns with D5)"
  - "show_stats confidence distribution uses backfill_confidence on a copy of findings to avoid mutation side effects"
  - "Cost-per-tier uses .get('tier', 'full') for backward compatibility with pre-Phase-1b run records"
metrics:
  duration: "7m"
  completed: "2026-05-12T10:35:56Z"
  tasks: 2/2
  files_modified: 3
---

# Phase 1b Plan 03: Evaluation and Recommendations Summary

Tricorder 4 criteria dimension evaluation with Wilson score CI, ToolFP-driven rule improvement recommendations distinguishing tool-wrong from user-won't-act, and dashboard extensions for confidence distribution and cost-per-tier breakdown.

## What Was Done

### Task 1: Add evaluate_dimensions(), generate_recommendation(), and extend show_stats()

Added 3 new functions in a new "Evaluation and Recommendation (D3, D5)" section:

1. **`evaluate_dimensions(findings, config, json_format)`** -- Groups findings by dimension, computes per-dimension ToolFP rate with Wilson score 95% CI, marks dimensions with <20 observations as provisional. Terminal table shows Obs, Prov, ToolFP%, CI, Impact%, Status (PASS/FAIL/provisional). JSON output available.

2. **`generate_recommendation(dimension, findings, config)`** -- Analyzes ToolFP data for a single dimension. Returns None if <20 observations or ToolFP <= 10%. Otherwise computes reason breakdown, identifies dominant reason, and returns recommendation dict with action (`improve_detection` or `adjust_scope`) and specific SKILL.md improvement suggestion. H3 fix: INTENTIONAL correctly routes to `improve_detection` (tool flagged intentional code = detection needs improvement, not scope narrowing).

3. **`show_recommendations(json_format)`** -- Orchestrator that calls generate_recommendation for every dimension. Displays summary table plus detailed recommendations with dominant cause, breakdown, and suggestion text.

Extended `show_stats()`:
- **Confidence distribution**: Buckets findings by confidence score into 5 ranges [0.0-0.2) through [0.8-1.0], displays text-based bar chart. Uses backfill_confidence on a copy to avoid mutating original findings.
- **Cost-per-tier**: Groups runs by tier (full/light/step0), shows count, total cost, and average cost per tier. Backward-compatible with pre-Phase-1b runs via `.get('tier', 'full')`.
- Both sections added to JSON output when `--json` flag is used.

CLI wiring:
- `--eval` flag routes to evaluate_dimensions
- `--recommend` flag routes to show_recommendations
- Both support `--json` for machine-readable output

### Task 2: Update ROADMAP.md Phase 2 success criteria per D5

Changed Phase 2 success criterion 1 from:
> "Existing dimensions with below-50% effectiveness are merged or retired before any new dimensions are added"

To:
> "Existing dimensions exceeding 10% ToolFP rate (per Phase 1b evaluation) are improved via D3 rule improvement flow, merged, or retired before any new dimensions are added"

This aligns Phase 2's entry gate with Tricorder 4 criteria (D5).

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 78e54eb | cli/eval: add dimension evaluation, rule improvement recommendations, and dashboard extensions |
| 2 | 6482e28 | docs/roadmap: update Phase 2 success criteria from 50% effectiveness to 10% ToolFP rate |

## Deviations from Plan

None -- plan executed exactly as written.

## Key Design Points

- **H3 fix:** INTENTIONAL is in TOOL_ERROR_REASONS (categories 1-4). generate_recommendation maps it to `improve_detection`, not `adjust_scope`. Rationale: if the tool flagged something the developer did intentionally, the tool's detection needs to be smarter about recognizing intentional patterns.
- **H4 fix:** ROADMAP Phase 2 criterion now uses the same 10% ToolFP threshold as evaluate_dimensions, creating consistency between Phase 1b evaluation and Phase 2 entry gate.
- **Provisional handling:** Dimensions with <20 observations show "provisional" status with "--" for all metrics. No false precision on small samples.
- **Backward compatibility:** Cost-per-tier gracefully handles pre-Phase-1b run records that lack a `tier` field by defaulting to 'full'.
- **No mutation:** show_stats confidence distribution operates on a copy of findings list to avoid mutating the original data.

## Threat Surface Scan

No new threat surfaces introduced beyond those documented in the plan's threat model (T-01b-09, T-01b-10, T-01b-11). All aggregation is O(n) single-pass. Recommendation output contains dimension names and FP rates (developer-facing diagnostic data, accepted risk). No new network endpoints, auth paths, or file access patterns.

## Known Stubs

None. All functions are fully implemented with real data flows from findings.json and run sidecars.

## Self-Check: PASSED

- cli/forge_cli.py: FOUND
- .planning/ROADMAP.md: FOUND
- 01b-03-SUMMARY.md: FOUND
- Commit 78e54eb: FOUND
- Commit 6482e28: FOUND
