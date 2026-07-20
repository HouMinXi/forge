---
phase: 28-reviewer-canary-inline
plan: 04
subsystem: docs
tags: [documentation, canary, inline-outlet, spec-extends]
dependency_graph:
  requires: [28-02]
  provides: [SPEC-01-extends, canary-config-reference, canary-manual-section]
  affects: [docs/design/reviewer-canary-spec.md, docs/configuration.md, docs/manual.md]
tech_stack:
  added: []
  patterns: [bilingual-manual, gate-yaml-optional-block]
key_files:
  created: []
  modified:
    - docs/design/reviewer-canary-spec.md
    - docs/configuration.md
    - docs/manual.md
decisions:
  - "Section 12 appended to SPEC-01 (not a rewrite) preserving all 4 design anchors"
  - "Template fallback explicitly documented as degraded-quality path (MF2-7)"
  - "Canary config placed before Related Documentation in configuration.md"
  - "Manual section numbered 11, follows bilingual convention"
metrics:
  duration: 4m 25s
  completed: 2026-06-25T04:43:48Z
  tasks: 3/3
  files_modified: 3
---

# Phase 28 Plan 04: Documentation -- SPEC-01 Extends + Configuration + Manual

SPEC-01 gains a Phase 28 extends note documenting the inline outlet canary variant; configuration.md documents the gate.yaml canary: block with field types and ranges; manual.md adds an end-user canary section with opt-in, behavior, exit codes, and guarantees.

## Task Execution

### Task 1: Append Phase 28 extends note to SPEC-01

**Commit:** 702cb4a

Appended Section 12 ("Phase 28: Inline Outlet Canary (Extends)") to
docs/design/reviewer-canary-spec.md. The section covers:
- Relationship to SPEC-01: complementary, not a replacement
- Design anchor fidelity: D-16, D-25, D-26, BOTH-04 all honored
- Resolution of Section 10 item 7 (Outlet B enforcement mode)
- Two-tier injection: LLM provider (in-place mutation) vs template fallback
  (appended hunks, degraded quality)
- New module reference: src/code_forge/canary_gen.py

All existing SPEC-01 content (Sections 1-11) unchanged; diff shows only additions.

### Task 2: Document canary: block in configuration.md

**Commit:** 4d091d5

Added "Canary (inline outlet)" section to docs/configuration.md with:
- gate.yaml snippet with canary: block (enabled, n, threshold_ratio)
- Field table with types, ranges (n: 3..5, threshold_ratio: >0.0..1.0), defaults
- Behavior notes: graceful degradation, D-16 honored, working tree never mutated,
  Python-only scope, exit 7 UNRELIABLE
- CLI alternative: --canary flag

### Task 3: Add canary section to docs/manual.md

**Commit:** 5d48b98

Added "11. Canary on the inline outlet" section following the manual's bilingual
convention. Covers opt-in mechanism, what happens (N planted defects, fresh-context
review, catch-rate gate), exit codes (5 DELEGATED, 7 UNRELIABLE), key guarantees
(canary findings never in user output, working tree never mutated), graceful
degradation, and reference to configuration.md for field details.

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED

All 3 modified files exist. All 3 task commits verified (702cb4a, 4d091d5, 5d48b98).
