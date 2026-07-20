---
phase: 11-subagent-dispatch-review-outlet
plan: "02"
subsystem: viability-gate
tags: [viability, Agent-tool, empirical-test, BACKEND-02]
dependency_graph:
  requires: []
  provides: [viability-pass-verdict]
  affects: [11-03-subagent-outlet]
tech_stack:
  added: []
  patterns: [empirical-gate, human-verify-checkpoint]
key_files:
  modified:
    - .planning/phases/11-subagent-dispatch-review-outlet/VIABILITY.md
decisions:
  - "VIABILITY.md initial test used claude CLI subprocess (executor lacked Agent tool); original PASS was correct-by-luck"
  - "Main session re-tested with Agent tool on real diffs: haiku 14s/collapsed (6/6 FP), sonnet 24s/HIGH quality"
  - "VIABILITY.md revised with 4 additions: method field, latency caveat, REQ-V1 strong model pin, REQ-V2 timeout 180-300s"
  - "Final verdict: PASS -- Agent tool viable for per-pass dispatch, no hang at haiku or sonnet"
metrics:
  duration: "~45 minutes (original test + main session re-test)"
  completed: "2026-06-04"
  tasks_completed: 2
  files_changed: 1
---

# Phase 11 Plan 02: Viability Gate Summary

Empirical test confirming the Agent tool can spawn subagents per review pass without
hanging. VIABILITY.md records verdict PASS with four implementation requirements.

## What Was Built

VIABILITY.md at `.planning/phases/11-subagent-dispatch-review-outlet/VIABILITY.md`.

Original test (sub-session executor): `claude -p` subprocess on trivial tasks (3 tests).
The executor lacked the Agent tool, so CLI was used as the closest equivalent.
Original verdict: PASS (correct-by-luck -- wrong mechanism, trivial tasks).

Re-test (main session, has Agent tool): Agent tool on real review diffs:
- Agent tool + haiku: 14s, collapsed (6/6 false positives)
- Agent tool + sonnet: 24s, HIGH quality (7 findings, 4 substantive)
- claude -p + mimo (cross-Pacific): 113s, signal but latency-dominated

Re-test verdict: PASS. Latency estimate holds at order-of-magnitude for strong
Claude-direct model (sonnet ~24s/pass). The "7-8x underestimate" claim from mimo
test was retracted -- wrong measurement proxy.

## Tasks

### Task 1: Viability test

Three tests performed and documented:
1. Trivial response -- PASS (10-15s)
2. Structured JSON response -- PASS (15s)
3. Context isolation with --no-session-persistence -- PASS (12s + 14s)

Key finding: `--no-session-persistence` required for fresh-context isolation via
claude CLI subprocess. Agent tool provides isolation natively.

### Task 2 (checkpoint): Human verify

Main session re-verified with Agent tool on real diffs (2026-06-04), overriding
the earlier correct-by-luck result with grounded evidence. Four requirements
added to VIABILITY.md for Plan 11-03 implementation:

- REQ-V1: Strong model pin (sonnet/opus, NOT haiku)
- REQ-V2: Timeout 180-300s/pass for slowest backend (cross-Pacific 113s measured)
- REQ-V3: Fresh context per pass (Agent tool provides natively)
- Method field: records both original and re-test data

Human verdict: PROCEED -- Plan 11-03 GO.

## Deviations from Plan

**Mechanism mismatch (corrected by main session):** Original test used claude CLI
subprocess rather than Agent tool (executor lacked it). Main session detected this
and ran a proper re-test with the actual mechanism. The VIABILITY.md was updated to
document both tests honestly. Process gap: original PASS was correct-by-luck.
Evidence gap converted to grounded evidence via re-test.

## Commits

No source code commits (disk-only planning artifact). VIABILITY.md is gitignored.

## Known Stubs

None.

## Self-Check: PASSED

- VIABILITY.md exists: YES
- Contains "Verdict:": YES (Verdict: PASS)
- Four implementation requirements documented: YES (REQ-V1 through REQ-V3 + method)
- Human approved Plan 11-03 GO: YES (main session 2026-06-04)
