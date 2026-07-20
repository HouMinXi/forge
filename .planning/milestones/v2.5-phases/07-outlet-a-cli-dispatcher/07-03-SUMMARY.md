---
phase: 07-outlet-a-cli-dispatcher
plan: 03
subsystem: skill-layer
tags: [outlet-a, skill-md, cli-dispatch, ga4-docs]
dependency_graph:
  requires: [07-01, 07-02]
  provides: [outlet-a-bridge, ga4-documentation]
  affects: [SKILL.md]
tech_stack:
  added: []
  patterns: [thin-wrapper, fail-closed, exit-code-contract]
key_files:
  created: []
  modified:
    - src/code_forge/skills/code-forge/SKILL.md
decisions:
  - "SKILL.md cli branch calls code-forge review with optional --committed flag"
  - "Bash tool timeout=600000 (10 min) to handle full 3-cycle pipeline"
  - "Exit code contract: 0=PASS, 1=FAIL, 2=CLI_ERROR, 3=BUSY, 4=ESCALATED"
  - "Read .code-forge/state.json for results, not stderr parsing"
  - "FAIL CLOSED: any non-zero exit stops, never falls back to inline"
  - "GA4 documents Outlet A (binary reset) vs Outlet B (severity-gated) non-equivalence"
metrics:
  duration_minutes: 18
  completed_date: 2026-06-02
---

# Phase 7 Plan 3: Outlet A CLI Dispatch Bridge

**One-liner:** Replaced SKILL.md Phase 7 placeholder with working Outlet A bridge that dispatches to code-forge review CLI and documented GA4 counter non-equivalence.

## Tasks Completed

### Task 1: Replace SKILL.md placeholder with Outlet A bridge + GA4 docs
- Step 3.5 cli branch: builds `code-forge review [--committed] [files...]` command
- Bash tool invocation with explicit timeout=600000 (10 minutes in milliseconds)
- Exit code interpretation for all 5 codes (0-4) with distinct error messages
- Reads .code-forge/state.json after CLI exits for findings display
- FAIL CLOSED contract: non-zero exit stops, never falls back to inline
- No re-orchestration of cycles (machine.py owns convergence)
- New section "Outlet Behavior: A vs B" documents counter divergence
- Outlet A uses binary reset (any finding resets counter)
- Outlet B uses severity-gated reset (P0/P1=full, P2=cycle, P3=density)
- States explicitly: "not behaviorally identical", unification is future work

## Verification

```bash
grep -c "code-forge review" SKILL.md
# Output: 2

grep -c "not yet implemented" SKILL.md
# Output: 0

grep -c "Outlet Behavior" SKILL.md
# Output: 1
```

All 3 verification criteria passed. Placeholder removed, bridge functional, GA4 documentation present.

## Deviations

None. Plan executed exactly as specified in 07-03-PLAN.md.

## Known Issues

None. SKILL.md is a documentation file (no runtime tests). The cli branch instructions are mechanically complete per GA1 requirements.

## Self-Check: PASSED

Modified file exists and contains:
- Working cli branch with code-forge review dispatch
- Exit code handling for 0/1/2/3/4
- .code-forge/state.json read instructions
- FAIL CLOSED contract documentation
- GA4 Outlet A vs B non-equivalence section
- No "not yet implemented" or "wait for Phase 7" text

Commit: 08fccd2 (feat(07-03): add Outlet A CLI dispatch bridge to SKILL.md)
