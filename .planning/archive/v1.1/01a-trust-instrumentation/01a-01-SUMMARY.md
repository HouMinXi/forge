---
phase: 01a-trust-instrumentation
plan: 01
subsystem: forge-skill
tags: [state-machine, finding-persistence, severity-gating, auto-continue, feedback]
dependency_graph:
  requires: []
  provides: [severity-gated-state-machine, finding-persistence, auto-continue, feedback-collection]
  affects: [skills/forge/SKILL.md, .forge/findings.json]
tech_stack:
  added: []
  patterns: [severity-normalization, density-based-escalation, atomic-json-write, subprocess-sha]
key_files:
  modified:
    - skills/forge/SKILL.md
decisions:
  - "P3 density-based escalation uses three thresholds: per-file >5, per-diff >10, density >0.15/line"
  - "Feedback collection happens ONCE at commit gate, not during individual passes"
  - "commit_sha obtained via subprocess.check_output inside Python heredoc, not shell substitution"
  - "Finding validation checks severity, dimension, and file path before storage"
metrics:
  duration: 304s
  completed: "2026-05-12T04:40:04Z"
  tasks_completed: 1
  tasks_total: 1
  files_modified: 1
  lines_added: 292
  lines_removed: 18
---

# Phase 1a Plan 01: SKILL.md State Machine Transformation Summary

Severity-gated state machine with P3 density-based escalation, finding persistence to .forge/findings.json with validation, auto-continue on clean passes, and feedback collection at commit gate.

## Tasks Completed

| # | Task | Commit | Key Changes |
|---|------|--------|-------------|
| 1 | Add severity normalization, finding persistence with validation, P3 threshold, auto-continue, and feedback collection | 14ccdc4 | skills/forge/SKILL.md (+292/-18 lines) |

## Changes Made

### Addition 1: Severity Normalization Table
- New section mapping qodo-review (Red/Yellow/Green), code-review-expert (P0-P3), and adversarial-qe (Critical/High/Medium/Low) to normalized P0/P1/P2/P3
- Fallback classification guide for findings without explicit severity

### Addition 2: Finding Persistence (TRUST-01) with Validation
- Python heredoc template for appending findings to .forge/findings.json
- VALID_SEVERITIES and VALID_DIMENSIONS sets for data validation before storage
- subprocess.check_output for commit_sha (not shell $() which does not expand in quoted heredocs)
- Warnings printed to stderr for invalid severity/dimension/file path
- Atomic write via tempfile.mkstemp + os.replace
- Full D1 schema with 13 fields documented

### Addition 3: Auto-Continue Protocol (TRUST-06)
- Zero-finding passes proceed immediately without user prompt
- Only pauses when findings exist and require user decision
- Eliminates "type continue after every LGTM pass" UX friction

### Addition 4: Feedback Collection (LEARN-07-LITE)
- Binary accept/reject feedback collected ONCE at pipeline completion (commit gate)
- 6-category reject_reason taxonomy: HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE, STYLE_PREFERENCE, ACCEPTABLE_RISK
- Findings fixed during pipeline auto-accepted; pending findings deferred to forge --classify
- Python heredoc template for updating finding outcomes

### Modification 1: State Machine (Severity-Gated Cycle Reset)
- P0/P1: full reset (cycle_counter = 0, restart from Cycle 1 Pass 1)
- P2: restart current cycle only (do not reset counter)
- P3: accumulate with density-based escalation (deduplicate by rule type, then check per-file >5, per-diff >10, density >0.15/line)
- P3 escalation triggers P2-equivalent restart, not P0/P1 full reset
- Old unconditional "any finding resets everything" behavior fully removed

### Modification 2: Pipeline Overview ASCII Diagram
- Updated to show P0/P1, P2, P3, and clean pass paths

### Modification 3: Execution Protocol
- Step 5 updated to reference severity-gated state machine and finding persistence
- Step 8.5 added for feedback collection at commit gate

### Modification 4: Adaptive Mechanisms List
- Item 1 replaced with Severity-Gated Cycle Reset (TRUST-07)
- Item 11 added: Auto-Continue on Clean Pass (TRUST-06)
- Item 12 added: Finding Persistence (TRUST-01)
- Item 13 added: Feedback Collection (LEARN-07-LITE)

### Modification 5: Handling Findings Section
- Updated to reference severity-dependent behavior
- P3 density-based escalation with deduplication documented

### Additional: Steps 1-3 Gate
- Updated gate criteria to reference P0/P1, P2, and P3 severity handling

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **P3 density thresholds**: per-file >5, per-diff >10, density >0.15/line (from P3-THRESHOLD-RESEARCH.md)
2. **Feedback timing**: ONCE at commit gate, not during individual passes (resolves review issues #4 and #10)
3. **commit_sha method**: subprocess.check_output inside Python heredoc (resolves review issue #6)
4. **Validation scope**: severity, dimension, and file path checked before storage (resolves review issue #5)

## Requirements Addressed

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| TRUST-01 | Complete | Finding persistence section with validated JSON schema |
| TRUST-06 | Complete | Auto-continue protocol section |
| TRUST-07 | Complete | Severity-gated state machine with P3 density escalation |
| LEARN-07-LITE | Complete | Feedback collection section at commit gate |

## Review Issues Addressed

| Issue | Severity | Resolution |
|-------|----------|------------|
| #4 | HIGH | Feedback collection happens ONCE at pipeline completion, not during passes |
| #5 | HIGH | Finding data extraction validation: severity/dimension/filepath checked |
| #6 | MEDIUM | commit_sha uses subprocess.check_output inside Python, not shell substitution |
| #10 | MEDIUM | Feedback vs auto-continue conflict resolved: feedback is post-pipeline only |
| #17 | USER | P3 density-based escalation with deduplication |

## Self-Check: PASSED

- skills/forge/SKILL.md: FOUND
- Commit 14ccdc4: FOUND
- 01a-01-SUMMARY.md: FOUND
