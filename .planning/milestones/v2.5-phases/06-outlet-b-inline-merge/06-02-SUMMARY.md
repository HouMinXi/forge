---
phase: 06-outlet-b-inline-merge
plan: 02
subsystem: skill-files
tags: [inline-merge, invoke-elimination, outlet-branch, anti-ai-gate]
dependency_graph:
  requires:
    - 06-01
  provides:
    - code-forge-inline-pipeline
  affects:
    - ~/.claude/skills/code-forge/SKILL.md
tech_stack:
  added: []
  patterns:
    - load-directive-pattern
    - outlet-branching
    - inline-content-merge
key_files:
  created: []
  modified:
    - ~/.claude/skills/code-forge/SKILL.md
decisions:
  - All 5 Invoke calls eliminated per D-04
  - 3 pass Invoke calls replaced with Load directives per D-05
  - fp-verify and smoke-test content inlined directly
  - anti-ai-audit gate positioned per D-13 between 3x3 and Step 3.5
  - Anti-AI finding does NOT reset 3x3 counter per D-14
  - Outlet branch point added after Step 0 per D-09
  - Phase 7 placeholder added for CLI dispatch
  - step N pipeline semantics preserved per D-07
metrics:
  start_time: "2026-06-01T21:00:00Z"
  end_time: "2026-06-01T21:15:00Z"
  duration_minutes: 15
  tasks_completed: 2
  files_modified: 1
---

# Phase 06 Plan 02: Inline Merge Execution Summary

Modified ~/.claude/skills/code-forge/SKILL.md to eliminate all Invoke calls, add outlet branching, and inline fp-verify + smoke-test + anti-ai-audit content.

## Objective

Replace 5 sub-skill Invoke calls with inline content and Load directives to enable trusted strong models (terminal Opus) to run the full review pipeline without sub-skill session hangs.

## One-liner

Eliminated all 5 Invoke calls from code-forge SKILL.md by replacing pass Invokes with Load directives and inlining fp-verify, smoke-test, and anti-ai-audit content; added outlet branching with Phase 7 CLI dispatch placeholder.

## Tasks Completed

### Task 1: Replace 3 pass Invoke calls with Load directives and add outlet branch ✓

Modified 7 sections of SKILL.md to replace pass Invoke calls with Load directives and add outlet resolution branching.

### Task 2: Inline fp-verify, smoke-test, and anti-ai-audit content; replace remaining 2 Invoke calls ✓

Inlined Step 3a anti-ai-audit gate, expanded Step 3.5 fp-verify to full 10-step protocol, expanded Step 4 smoke-test with assembly rules and pitfalls.

## Deviations from Plan

**Plan estimated final size under 1000 lines; actual is 1076 lines (7.6% over estimate).**
- Reason: Source content richer than estimated. fp-verify protocol expanded from summary to full subsections. Smoke-test added ~100 lines of assembly rules + pitfalls.
- Impact: None. Size manageable, content complete per requirements.

## Known Stubs

None.

## Threat Flags

None.

## Self-Check: PASSED

All success criteria met:
- Zero Invoke calls (verified: 0)
- 3 Load passes/ directives (verified: 3)
- Outlet branch point present
- Phase 7 placeholder present
- Step 3a anti-ai-audit gate present between 3x3 and Step 3.5
- Anti-ai finding does NOT reset cycle_counter (D-14 text verified)
- fp-verify 10-step protocol fully inlined
- Smoke-test assembly rules + pitfalls inlined
- step N semantics preserved (D-07)
- Pipeline overview diagram updated
- Progress tracking includes Step 3a
