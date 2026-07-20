---
phase: 03-adaptive-learning-mvp
plan: 06
subsystem: cli/escalation
tags: [health-check, escalation, v2-triggers, monitoring]
dependency_graph:
  requires: [cli/forge_cli.py, cli/gap_detector.py, cli/dimension_manager.py]
  provides: [cli/escalation.py, LEARN-10 escalation monitor]
  affects: [cli/forge_cli.py]
tech_stack:
  added: []
  patterns: [lazy-import, atomic-write, rolling-window, threshold-alert]
key_files:
  created: [cli/escalation.py]
  modified: [cli/forge_cli.py]
decisions:
  - "edit_corruption_count is user-maintained (read from existing status) since SKILL.md edits happen outside forge"
  - "dimension_change_count uses added_at != None as proxy for total lifecycle changes"
  - "Escalation imports are lazy (inside try/except) to avoid circular imports and keep non-critical"
metrics:
  duration: 5m
  completed: 2026-05-14
---

# Phase 3 Plan 6: Escalation Monitor Summary

LEARN-10 escalation health check computing dedup error rate, edit corruption count, dimension change count, and feedback volume with threshold alerts for LEARN-03/04/05 v2 upgrades.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create escalation monitor module | d191980 | cli/escalation.py |
| 2 | Integrate escalation check into forge pipeline and --eval | a7f4e1c | cli/forge_cli.py |

## Implementation Details

### cli/escalation.py (new, 278 lines)
- `load_escalation_status()`: loads .forge/escalation-status.json with safe default on missing/corrupt
- `should_run_check()`: schedule logic -- never checked, 50+ runs, or 30+ days since last check
- `compute_metrics()`: computes 4 metrics with 90-day rolling window for dedup error rate
  - dedup_error_rate: false matches (different validated_dimension) / total dedup attempts
  - edit_corruption_count: user-maintained metric read from existing status
  - dimension_change_count: config dimension_states entries with added_at not None
  - feedback_volume: findings with accepted/rejected outcome
- `check_triggers()`: returns (trigger_name, value, threshold, recommendation) tuples for crossed thresholds
  - LEARN-03 at dedup_error_rate > 20%
  - LEARN-04 at edit_corruption_count >= 3
  - LEARN-05 at dimension_change_count >= 10
- `run_escalation_check(force=False)`: full health check with [forge-escalate] stderr alerts and status persistence
- `increment_run_count()`: lightweight counter increment after each pipeline run

### cli/forge_cli.py (extended)
- run_forge(): escalation increment + check after pipeline completion (try/except wrapped)
- --eval dispatch: escalation health status section with all 4 metrics and [!] alert prefix
- --learn dispatch: check_shadow_timeouts(config) call after process_learn (D7)

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All functions are fully implemented with data wiring.

## Threat Mitigations Applied

| Threat | Mitigation |
|--------|------------|
| T-03-19 (escalation-status.json tampering) | Accepted per plan -- file is user-owned; metrics verifiable via force re-check |
| T-03-20 (compute_metrics DoS on large findings) | Accepted per plan -- O(n) with 90-day window cap; typical volume < 1000 |

## Self-Check: PASSED
