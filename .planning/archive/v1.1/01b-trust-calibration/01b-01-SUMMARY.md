---
phase: 01b-trust-calibration
plan: 01
subsystem: cli
tags: [confidence-scoring, wilson-score, statistics, schema-extension]
dependency_graph:
  requires: []
  provides: [wilson_score_interval, compute_confidence, backfill_confidence, tier_classification_config, evaluation_config]
  affects: [cli/forge_cli.py, cli/config.json, skills/forge/SKILL.md]
tech_stack:
  added: [math, random, re]
  patterns: [wilson-score-ci, progressive-confidence, multi-signal-schema]
key_files:
  created: []
  modified:
    - cli/forge_cli.py
    - cli/config.json
    - skills/forge/SKILL.md
decisions:
  - Wilson score uses pure-math implementation (no scipy dependency)
  - Progressive confidence uses 3 stages at <100, 100-300, 300+ per-dimension decided count
  - backfill_confidence computes pass_agreement from (file, line, dimension) grouping
  - Config keeps defaults hardcoded in Python with config.json as override
metrics:
  duration: 257s
  completed: 2026-05-12T10:15:13Z
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 1b Plan 01: Confidence Scoring Summary

Wilson score CI + 3-stage progressive confidence formula with per-dimension FP rates, multi-pass location grouping for pass_agreement, and SKILL.md schema extension with LLM calibration instructions.

## Task Completion

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add statistical utilities and confidence scoring to forge_cli.py | 347e680 | cli/forge_cli.py |
| 2 | Extend config.json with tier and evaluation defaults, extend SKILL.md finding schema | 1b8102d | cli/config.json, skills/forge/SKILL.md |

## What Was Built

### Statistical Utilities (forge_cli.py)

- `wilson_score_interval(successes, total, confidence=0.95)`: Pure-math Wilson score CI for FP rate estimation with honest uncertainty bounds. Handles edge cases (0/0 returns full uncertainty range). Z-score lookup table for 90%/95%/99% confidence levels.

- `compute_confidence(dimension_fp_rate, pass_agreement, evidence_count, llm_self_report, total_findings)`: Progressive 3-stage formula:
  - Stage 1 (<100 per-dimension decided): `1 - dimension_fp_rate` (only reliable signal)
  - Stage 2 (100-300): weighted FP rate (0.6) + pass_agreement (0.4)
  - Stage 3 (300+): full composite with all 4 signals (0.35/0.25/0.20/0.20)
  - Stage determination uses per-dimension decided count, not global (M5)

- `backfill_confidence(findings_data)`: Bridge between SKILL.md (records raw signals) and CLI (computes confidence post-run). Groups findings by (file, line, dimension) to compute pass_agreement from multi-pass consensus (M1). Respects existing confidence_signals if present.

### Config Extensions (config.json)

- `tier_classification`: critical_patterns (3 regex for auth/hooks/SKILL.md), ai_markers, audit_rate (0.10), small_diff_threshold (10)
- `evaluation`: min_observations (20), fp_rate_threshold (0.10), confidence_level (0.95)
- 35 lines total (under 40-line limit)

### Schema Extensions (SKILL.md)

- Finding heredoc extended with `confidence` (0.0) and `confidence_signals` dict
- Validation for `evidence_count` (int >= 0) and `llm_self_report` (float 0.0-1.0)
- Explicit LLM instructions with calibration scale (0.1-1.0 guidance, M4)
- Anti-default instruction: "Do NOT default to 0.8" (M4)
- Evidence count instruction: "number of distinct code locations" (M6)
- Schema documentation for all new fields

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

1. **Wilson score pure-math**: No scipy dependency. Formula is 15 lines with math.sqrt. Verified against statsmodels documentation.
2. **Per-dimension staging**: total_findings parameter in compute_confidence represents per-dimension decided count, ensuring a dimension with 50 findings uses Stage 1 even if global count is 500 (M5).
3. **Pass agreement from location grouping**: backfill_confidence groups findings by (file, line, dimension) tuple and computes pass_agreement as fraction of distinct passes over total passes in run (3), rather than hardcoding 1.0 (M1).
4. **Config under 40 lines**: Hardcoded defaults in Python, config.json provides overrides only. Avoids Pitfall 4 (config bloat).

## Verification Results

All plan verification criteria passed:
- py_compile exits 0
- config.json valid JSON with all required keys
- Wilson score, compute_confidence, backfill_confidence importable and functional
- SKILL.md contains all required fields and LLM instructions
- Non-ASCII check clean

## Self-Check: PASSED
