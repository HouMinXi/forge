---
phase: 09-reviewer-canary-spec
plan: 01
subsystem: design
tags: [canary, reviewer-validation, anti-shirk, llm-attention, spec]

# Dependency graph
requires:
  - phase: 05-prerequisites
    provides: Design anchors D-16, D-25, D-26 for canary constraints
  - phase: 08-hardening
    provides: Stable pipeline (machine.py, state.py, factories.py) that canary integrates with
provides:
  - "Reviewer Canary design specification (docs/design/reviewer-canary-spec.md)"
  - "Canary injection mechanism design (prompt-level, backend-agnostic)"
  - "6 defect type categories with Python examples"
  - "Disqualification criteria for LOCAL and CI modes"
  - "Integration point mapping to machine.py, state.py, factories.py"
  - "8 deferred items for v2.3+ implementation phase"
affects: [v2.3-canary-implementation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Prompt-level injection: modify diff payload before L1 provider, above backend layer"
    - "Dependency-injected canary_injector callable on StateMachine (same pattern as l1_provider)"

key-files:
  created:
    - docs/design/reviewer-canary-spec.md
  modified: []

key-decisions:
  - "Canary injection is prompt-only (no working tree mutation, no git history contamination)"
  - "Canary validates attention not strength (D-26): miss means unreliable round, not weak model"
  - "LOCAL miss discards L1 findings and skips consecutive_clean_rounds increment; CI miss forces FAIL verdict"
  - "Canary findings extracted before falsification (falsify_real.py never sees canary findings)"
  - "Defect-type misidentification counts as PASS (tests attention, not diagnostic precision)"

patterns-established:
  - "Design spec as standalone docs/design/ document with Spec Completeness audit trail"

requirements-completed: [SPEC-01]

# Metrics
duration: 8min
completed: 2026-06-03
---

# Phase 9 Plan 01: Reviewer Canary Spec Summary

**Design spec for prompt-level canary injection that validates LLM reviewer attention by planting known defects in the diff and disqualifying on miss**

## Performance

- **Duration:** 8 min
- **Started:** 2026-06-03T07:30:24Z
- **Completed:** 2026-06-03T07:39:19Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Wrote 788-line design specification covering injection mechanism, 6 defect categories, disqualification criteria, integration points, canary library design, security considerations, 8 deferred items, and 8 open questions
- All four validation checklists (SPEC-01 coverage, Roadmap SC#1, Roadmap SC#2, design anchor fidelity) pass with 15/15 items
- Spec references all required design anchors (D-16, D-25, D-26, BOTH-04) with concrete implications for implementation

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the Reviewer Canary design specification** - `51e5400` (docs)
2. **Task 2: Validate spec completeness against SPEC-01 and roadmap success criteria** - no separate commit (validation embedded in Task 1 document; all checks passed without edits)

## Files Created/Modified
- `docs/design/reviewer-canary-spec.md` - Complete Reviewer Canary design specification (788 lines, 12 sections)

## Decisions Made
- Canary injection point is between _execute_round calling _run_l1_phase (machine.py line 628) and the l1_provider() invocation (machine.py line 516) -- prompt-level, backend-agnostic per D-25
- Canary findings removed before falsification because canary file does not exist on disk (falsify_real.py would corrupt the signal)
- Defect-type misidentification counts as PASS (reviewer demonstrated attention to canary hunk, which is the canary's purpose per D-26)
- FORGE_CANARY_RATE defaults to 1.0 (deterministic injection every round) with 0.0 opt-out

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- SPEC-01 fully addressed: design spec exists with all required content
- Roadmap SC#1 and SC#2 both satisfied
- Document is self-contained and actionable for a v2.3+ implementation phase
- v2.2 milestone has no remaining planned phases after Phase 9

## Self-Check: PASSED

- [x] docs/design/reviewer-canary-spec.md exists (788 lines)
- [x] Commit 51e5400 exists in git log

---
*Phase: 09-reviewer-canary-spec*
*Completed: 2026-06-03*
