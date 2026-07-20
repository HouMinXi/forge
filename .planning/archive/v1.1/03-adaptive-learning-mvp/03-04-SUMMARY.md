---
phase: 03-adaptive-learning-mvp
plan: 04
subsystem: cli/gap-management
tags: [interactive, gap-review, reclassification, staleness]
dependency_graph:
  requires: [cli/gap_detector.py, cli/llm_parser.py, cli/migration.py]
  provides: [cli/gap_manager.py, --gaps CLI, --reclassify CLI, --approve-expansion CLI]
  affects: [cli/forge_cli.py]
tech_stack:
  added: []
  patterns: [interactive-review-loop, lazy-import, atomic-write, LLM-grouping]
key_files:
  created: [cli/gap_manager.py]
  modified: [cli/forge_cli.py]
decisions:
  - "Gap groups fully regenerated on each --gaps run (no persistent group IDs across sessions)"
  - "LLM grouping uses claude-haiku-3.5 matching llm_parser pattern"
  - "Reclassify audit entries dedup on (proposed_dimension, text_hash) to prevent duplicates"
metrics:
  duration: 4m
  completed: 2026-05-14
---

# Phase 3 Plan 4: Interactive Gap Management Summary

Interactive gap management with staleness sweeps, keyword expansion review, LLM-powered gap grouping, reclassification with all 5 D4 side effects, and 3 new CLI commands (--gaps, --reclassify, --approve-expansion).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create gap manager with staleness sweeps, expansion review, and grouping | 21145e3 | cli/gap_manager.py |
| 2 | Wire --gaps, --reclassify, --approve-expansion into forge_cli.py | 24e777b | cli/forge_cli.py |

## Implementation Details

### cli/gap_manager.py (new, 579 lines)
- `_sweep_stale_expansions()`: auto-dismisses keyword expansions pending > 90 days, updates external findings
- `_sweep_stale_candidates()`: auto-dismisses gap candidates pending/grouped > 180 days
- `_review_expansions()`: interactive approve/reject/skip/quit loop for keyword expansions, creates gap candidates on reject
- `_group_candidates()`: LLM-powered grouping of pending gap candidates, validates output structure, sanitizes proposed dimension names
- `_process_groups()`: interactive propose/reclassify/dismiss/skip/quit for proposal-ready groups (>= 3 candidates)
- `run_gaps()`: orchestrates D5 steps 1-5 in order
- `approve_expansion_noninteractive()`: non-interactive single-expansion approval
- `run_reclassify()`: all 5 D4 side effects (finding update, derived entries, keywords, dimension_states, audit)

### cli/forge_cli.py (extended)
- Added `--gaps` (store_true), `--approve-expansion` (metavar ID), `--reclassify` (nargs=2)
- `--gaps` and `--approve-expansion` combinable (approval ID passed to run_gaps)
- `--approve-expansion` standalone dispatches directly to approve_expansion_noninteractive
- All imports are lazy (inside elif dispatch blocks)

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All functions are fully implemented with data wiring.

## Threat Mitigations Applied

| Threat | Mitigation |
|--------|------------|
| T-03-12 (LLM grouping output tampering) | proposed_dimension sanitized to [a-z0-9_] via regex; candidate_ids validated against existing candidates; non-array/non-dict LLM responses rejected |
| T-03-14 (reclassify target dimension tampering) | target_dim validated against keyword_dictionaries AND checked for archived status before any mutations |
