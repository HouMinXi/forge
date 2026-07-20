---
phase: 01a-trust-instrumentation
plan: 03
subsystem: forge-skill, forge-hook
tags: [fuse-01, context-fusion, severity-detection, all-passes, sidecar]
dependency_graph:
  requires: [01a-01]
  provides: [step0-context-fusion, severity-aware-hook, session-sidecar]
  affects: [skills/forge/SKILL.md, hooks/check_review_tracker.sh, .forge/current_session.json]
tech_stack:
  added: []
  patterns: [fuse-01-context-injection, severity-hierarchy-parsing, sidecar-atomic-write]
key_files:
  modified:
    - skills/forge/SKILL.md
    - hooks/check_review_tracker.sh
decisions:
  - "FUSE-01 context table capped at 20 rows with overflow to .forge/step0_findings.txt"
  - "Session State section reads .forge/current_session.json for cross-check severity enforcement"
  - "Chinese markers in _is_review_pass() and _max_severity() use literal characters matching existing _has_findings() style"
  - "P3-only rounds excluded from hard stop counter (TRUST-07)"
metrics:
  duration: 377s
  completed: "2026-05-12T05:04:13Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 2
  lines_added: 237
  lines_removed: 10
---

# Phase 1a Plan 03: FUSE-01 Context Fusion and Hook Severity Upgrade Summary

Step 0 context fusion (FUSE-01) with 20-row cap injected into SKILL.md LLM pass prompts, and check_review_tracker.sh upgraded to detect all 3 review passes with severity-aware finding classification writing to current_session.json sidecar.

## Tasks Completed

| # | Task | Commit | Key Changes |
|---|------|--------|-------------|
| 1 | Add Step 0 Context Fusion (FUSE-01) with size cap to SKILL.md | d780a93 | skills/forge/SKILL.md (+80/-1 lines) |
| 2 | Upgrade check_review_tracker.sh to severity-aware detection for all 3 passes | 3d86a13 | hooks/check_review_tracker.sh (+157/-9 lines) |

## Changes Made

### Task 1: SKILL.md FUSE-01 Protocol

**Addition 1: Step 0 Context Fusion (FUSE-01) section**
- New section between "Step 0 Gate" and "Steps 1-3: Three-Cycle Static Review"
- 3-step protocol: collect findings, serialize as markdown table, inject into LLM passes
- Table template with columns: #, File, Line, Tool, Issue
- Size cap at 20 rows with truncation note and overflow to .forge/step0_findings.txt
- Zero-findings variant for clean Step 0 output
- 4 rules for LLM passes receiving Step 0 context (do not re-flag, do flag new instances, etc.)

**Addition 2: Session State (Hook Integration) section**
- New section after "Finding Persistence (TRUST-01)"
- Documents .forge/current_session.json sidecar schema
- Cross-check instruction: use higher severity if hook and SKILL.md disagree

**Modification 1: Execution Protocol step 3**
- Updated to reference FUSE-01 context block serialization with 20-row cap

**Modification 2: Adaptive Mechanisms item 14**
- Added "Step 0 Context Fusion (FUSE-01)" describing the deterministic+LLM fusion pattern

### Task 2: Hook Severity-Aware Upgrade

**Addition 1: _is_review_pass(cmd, output) function**
- Detects all 3 review passes: qodo-review (via existing _is_real_qodo), code-review-expert, adversarial-qe
- Returns pass name string or None
- English markers: regex patterns for each pass's characteristic output
- Chinese markers: literal characters matching existing _has_findings() style

**Addition 2: _max_severity(output) function**
- Returns highest severity: P0 > P1 > P2 > P3 > none
- P0: regex for critical security/crash/data-loss
- P1: regex + Chinese literals for must-fix/high-risk/serious-problem
- P2: regex + Chinese literals for should-fix/suggest-fix
- P3: falls back to _has_findings() for any-finding-detected
- none: no findings at all

**Modification 1: State defaults**
- Added 'last_max_severity': 'none' and 'last_review_pass': '' to _load_state()

**Modification 2: PostToolUse Bash handler**
- Dispatch uses _is_review_pass() instead of _is_real_qodo() only
- Calls _max_severity() and stores severity in state
- rounds_with_findings only increments for P0/P1/P2 (P3 excluded per TRUST-07)
- Reporting includes pass name and max severity

**Modification 3: Sidecar write**
- Writes .forge/current_session.json with last_max_severity, last_review_pass, qodo_runs, rounds_with_findings
- Uses atomic write (tempfile.mkstemp + os.replace)
- Logs WARNING to stderr on write failure (not silent)

**Modification 4: Header comment**
- Documents last_max_severity and last_review_pass state fields
- Documents P0/P1/P2 hard stop policy and all-3-passes detection

## Deviations from Plan

None -- plan executed exactly as written.

## Review Issues Addressed

| Issue | Severity | Resolution |
|-------|----------|------------|
| #7 | MEDIUM | Hook detection extended to code-review-expert and adversarial-qe |
| #9 | MEDIUM | FUSE-01 context table capped at 20 rows |
| #12 | MEDIUM | SKILL.md modifications use content anchors (grep headings), not line numbers |
| #15 | MEDIUM | Sidecar write failures log WARNING to stderr |
| #16 | MEDIUM | Chinese strings use literal characters matching _has_findings() style |
| DeepSeek HIGH #1 | HIGH | Session State section with current_session.json integration |

## Requirements Addressed

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| FUSE-01 | Complete | Step 0 Context Fusion section in SKILL.md with 20-row cap |
| TRUST-07 | Complete | Hook only counts P0/P1/P2 toward hard stop; P3 excluded |

## Decisions Made

1. **FUSE-01 size cap**: 20 rows shown inline, overflow written to .forge/step0_findings.txt
2. **Session State cross-check**: conservative approach -- use the higher severity when hook and SKILL.md disagree
3. **Chinese encoding**: literal characters matching existing _has_findings() patterns (grandfathered non-ASCII)
4. **P3 hard stop exclusion**: P3-only rounds do not increment rounds_with_findings counter

## Self-Check: PASSED

- skills/forge/SKILL.md: FOUND
- hooks/check_review_tracker.sh: FOUND
- Commit d780a93: FOUND
- Commit 3d86a13: FOUND
- 01a-03-SUMMARY.md: FOUND
