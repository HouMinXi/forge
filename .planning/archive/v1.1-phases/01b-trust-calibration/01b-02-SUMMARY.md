---
phase: 01b-trust-calibration
plan: 02
subsystem: cli
tags: [tier-classification, anti-gaming, backfill-confidence, D2, H1]
dependency_graph:
  requires: [01b-01]
  provides: [classify_change, tier-routing, audit-sampling]
  affects: [cli/forge_cli.py]
tech_stack:
  added: []
  patterns: [pure-function-classification, subprocess-git, audit-sampling]
key_files:
  created: []
  modified:
    - cli/forge_cli.py
decisions:
  - "Tier classification is deterministic Python, not LLM-delegated (D2)"
  - "LLM prompt says what to do, never mentions other tiers (M2 anti-gaming)"
  - "Override only escalates: --full accepted, --step0 rejected for critical files"
  - "backfill_confidence wired at end of run_forge with atomic_write (H1 fix)"
metrics:
  duration: "4m"
  completed: "2026-05-12T10:24:13Z"
  tasks: 1/1
  files_modified: 1
---

# Phase 1b Plan 02: Tier Classification Summary

Deterministic tier classification routing changes to full/light/step0 review depth before LLM invocation, with 10% audit sampling and backfill_confidence persistence.

## What Was Done

### Task 1: Add tier classification functions and wire backfill_confidence

Added 6 new functions to `cli/forge_cli.py` in a new "Tier Classification" section:

1. **`_get_changed_files(diff_spec)`** -- git diff --name-only with error fallback to empty list (conservative = full tier)
2. **`_count_diff_lines(diff_spec)`** -- git diff --numstat (M7: locale-independent, not --stat) with binary file skip and error fallback to 999
3. **`_detect_change_type(diff_spec, files)`** -- whitespace detection via git diff -w, comment detection via language-aware regex on diff hunks. Valid Python regex for .md files (H2: non-capturing group, not pseudo-syntax)
4. **`_has_critical_files(files, config)`** -- regex matching against critical patterns from config with hardcoded defaults
5. **`_detect_ai_generated(diff_spec, config)`** -- searches both diff added lines AND commit message (M3) for AI markers
6. **`classify_change(diff_spec, override, config)`** -- pure function composing all 5 helpers with priority-ordered logic: critical > AI > comment/whitespace > small diff > full default

Modified `run_forge()`:
- Changed signature to `run_forge(diff_spec, override_tier=None)`
- Tier classification runs before prompt construction
- 10% audit sampling silently upgrades light to full
- step0-only tier delegates to run_dry_run with run sidecar recording
- Light prompt: "Run Step 0 checks, then run one cycle of passes 1-3" (M2: says what to do, never what is skipped)
- Full prompt: "Follow the complete 5-step pipeline"
- Run sidecar extended with `tier` and `was_audited` fields
- backfill_confidence called at end with atomic_write to FINDINGS_FILE (H1)

Modified `main()`:
- Added `--full` flag (force full review)
- Added `--step0` flag (force step0, rejected for critical files)
- Override routing to run_forge(diff_spec, override_tier=override)

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 59c7c78 | cli/tier: add deterministic tier classification and wire backfill_confidence |

## Deviations from Plan

None -- plan executed exactly as written.

## Key Design Points

- **Anti-gaming (D2):** Classification is pure Python. LLM receives "execute X" instructions, never knows other tiers exist. No tier names in prompts.
- **Conservative defaults:** Empty file list = full tier. Diff count error = 999 (full tier). Unknown change type = code (full tier).
- **Override only escalates:** `--full` always accepted. `--step0` for critical files returns `light` (not step0). Cannot bypass security review.
- **Audit sampling:** `random.random() < audit_rate` per invocation, no persistent seed. `was_audited` recorded in run sidecar for tracking.
- **H1 fix:** backfill_confidence runs at end of every run_forge(), persisting computed confidence scores via atomic_write.

## Threat Surface Scan

No new threat surfaces introduced beyond those documented in the plan's threat model. All subprocess calls use timeout=10, check=False with conservative error defaults. No new network endpoints, auth paths, or file access patterns.

## Known Stubs

None. All functions are fully implemented with real git subprocess calls and config integration.

## Self-Check: PASSED

- cli/forge_cli.py: FOUND
- Commit 59c7c78: FOUND
- 6 classification functions: FOUND (lines 324, 361, 401, 487, 522, 579)
