---
phase: 05-prerequisites
plan: 03
subsystem: outlet-resolver
tags: [yaml, precedence-resolver, fail-closed, backend-reachability, dependency-injection]

requires:
  - phase: 05-02
    provides: ProbeResult, resolve_backend, probe_backend, DEFAULT_BACKEND from backend.py
provides:
  - resolve_outlet() for outlet selection (FORGE_OUTLET > gate.yaml > backend reachability)
  - load_outlet_from_gate() lightweight gate.yaml outlet reader (D-22 option b)
  - _parse_outlet_string() with source-attributed ValueError on invalid values
affects: [05-04, phase-06, phase-07]

tech-stack:
  added: []
  patterns: [precedence resolver with fail-closed default (no silent inline degrade), lightweight YAML field reader separate from full config loader, bomb-probe injection to mechanically prove short-circuit behavior]

key-files:
  created:
    - src/code_forge/outlet_resolver.py
    - tests/test_outlet_resolver.py
  modified: []

key-decisions:
  - "Precedence chain unchanged from D-13: FORGE_OUTLET env > gate.yaml outlet > backend reachability probe (D-29)"
  - "Backend unreachable with no explicit override raises CliError (FAIL CLOSED D-29) -- never returns inline"
  - "Outlet B (inline) NEVER calls reachability_fn -- short-circuits before probe (D-29, mechanically tested via bomb_probe)"
  - "load_outlet_from_gate is a separate lightweight reader that does NOT call load_gate_config (D-22 option b)"
  - "Corrupted YAML -> ValueError with 'gate.yaml read failed' (C3); PermissionError -> ValueError with 'permission denied' (R2-3)"
  - "No model-capability auto-detection anywhere in the module (D-16 LOCKED)"

patterns-established:
  - "Bomb-probe injection: reachability_fn that raises AssertionError if called proves short-circuit behavior"
  - "Lightweight single-field YAML reader: reads one key without loading full config schema"
  - "Fail-closed reachability: unreachable backend raises CliError, never silently degrades"

requirements-completed: [BOTH-04, BACKEND-01]

duration: 9min
completed: 2026-05-31
---

# Phase 5 Plan 03: Outlet Resolver Summary

**Outlet selection with FORGE_OUTLET > gate.yaml > backend-reachability precedence, fail-closed on unreachable backend, inline-never-probes**

## Performance

- **Duration:** 9 min
- **Started:** 2026-05-31T18:24:36Z
- **Completed:** 2026-05-31T18:34:12Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Implemented outlet_resolver.py with BOTH-04 precedence chain over the backend-reachability signal (D-29)
- 20 TDD tests covering env override, gate.yaml outlet, backend reachability (reachable->cli, unreachable->FAIL CLOSED), inline-never-probes, edge cases, standalone gate reader, corrupted YAML, and permission denied
- Bomb-probe pattern mechanically proves inline short-circuits before any reachability check

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD -- Write failing tests (RED)** - `8b47e65` (test)
2. **Task 2: Implement outlet_resolver.py (GREEN)** - `bb69beb` (feat)

**Merge commit:** `311d32a` (merge: Phase 5 plan 03 outlet resolver)

## Files Created/Modified
- `src/code_forge/outlet_resolver.py` - Outlet selection: resolve_outlet, load_outlet_from_gate, _parse_outlet_string, VALID_OUTLET_STRINGS
- `tests/test_outlet_resolver.py` - 20 tests across 5 test classes (TestEnvOverride, TestGateYamlOutlet, TestBackendReachabilityDefault, TestEdgeCases, TestLoadOutletFromGate)

## Decisions Made
- Precedence chain follows D-13/D-29: FORGE_OUTLET env > gate.yaml outlet > backend reachability probe
- Backend unreachable raises CliError with message containing "Configure a review backend or set FORGE_OUTLET=inline" (FAIL CLOSED)
- load_outlet_from_gate is a separate function from load_gate_config -- reads only the outlet key, does not require a test section (D-22 option b)
- Lazy default reachability_fn: when None, resolves DEFAULT_BACKEND and probes it; this keeps the resolver a pure function in tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed corrupted YAML test fixture**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Plan specified `:::bad\nyaml` as invalid YAML, but PyYAML parses it as a valid scalar string
- **Fix:** Changed fixture to `{unclosed: [bracket` which correctly triggers yaml.YAMLError
- **Files modified:** tests/test_outlet_resolver.py
- **Verification:** test_corrupted_yaml now passes (raises ValueError with "gate.yaml read failed")
- **Committed in:** bb69beb (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test fixture fix only; no scope creep. The implementation matches the plan exactly.

## Issues Encountered
None -- implementation matched the plan specification.

## User Setup Required
None -- no external service configuration required.

## Next Phase Readiness
- outlet_resolver.py ready for cli.py integration (Phase 7: code-forge resolve-outlet subcommand, D-15)
- resolve_outlet() ready for SKILL.md consumption (Phase 6 Outlet B, Phase 7 Outlet A)
- load_outlet_from_gate() available for any module needing the outlet field from gate.yaml
- No blockers for 05-04

## TDD Gate Compliance

Verified in git log:
1. `8b47e65` - test(05-03) commit exists (RED gate)
2. `bb69beb` - feat(05-03) commit exists after it (GREEN gate)
3. No refactor commit needed (code is clean from initial implementation)

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 05-prerequisites*
*Completed: 2026-05-31*
