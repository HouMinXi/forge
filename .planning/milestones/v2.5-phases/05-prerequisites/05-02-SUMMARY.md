---
phase: 05-prerequisites
plan: 02
subsystem: backend
tags: [subprocess, shutil.which, dataclass, json-cache, cli-probe, api-probe, env-resolution]

requires:
  - phase: none
    provides: standalone plan, no prior phase dependency
provides:
  - BackendConfig frozen dataclass for backend entry parsing (D-27)
  - ProbeResult frozen dataclass for reachability results (D-28)
  - load_backend_configs() for parsing config entries
  - resolve_backend() with FORGE_BACKEND > config > session-default precedence
  - resolve_auth_timeout() for FORGE_AUTH_TIMEOUT resolution
  - probe_backend() backend-agnostic reachability (cli auth status + api key presence)
  - invalidate_probe_cache() for cache management
  - DEFAULT_BACKEND constant (session-model cli, no model pin)
affects: [05-03, 05-04, phase-06, phase-07]

tech-stack:
  added: []
  patterns: [frozen dataclass config schema, env-var-name-only secret reference, file-based probe cache with TTL, broad except for convenience-only cache, D-26 no-diff-routing guard via inspect.signature test]

key-files:
  created:
    - src/code_forge/backend.py
    - tests/test_backend.py
  modified: []

key-decisions:
  - "DEFAULT_BACKEND is cli type with model='' (session model, no --model pin, D-26)"
  - "api_key_env stores env-var NAME only, inline api_key rejected with CliError (D-27 secret hygiene)"
  - "cli probe uses claude auth status --json (zero inference cost, D-28), NOT claude -p"
  - "api probe checks api_key_env presence only, no subprocess or network call (D-28)"
  - "_read_cache uses except Exception: return None -- broad catch correct because cache is convenience-only (R5 reviewed)"
  - "Cache timing: _write_cache stores timestamp, _read_cache checks TTL on read; file-missing on first probe triggers exception path (no time_fn call)"

patterns-established:
  - "Backend config schema: {name, type, format?, base_url?, api_key_env?, model} with allow-list validation"
  - "Active-backend resolution: explicit override > config default > session-model default"
  - "D-26 NON-GOAL guard: inspect.signature test mechanically pins no-diff-routing prohibition"
  - "File-based probe cache: success cached with 5-min TTL; failures NOT cached; corrupted/partial treated as miss"
  - "Backend-agnostic probe dispatch: cli -> subprocess auth check, api -> env var presence"

requirements-completed: [CLI-05, BACKEND-01]

duration: 14min
completed: 2026-05-31
---

# Phase 5 Plan 02: Backend Abstraction Summary

**Pluggable review-backend abstraction with config schema, FORGE_BACKEND resolution, and backend-agnostic reachability probe (cli auth status + api key presence) with TTL caching**

## Performance

- **Duration:** 14 min
- **Started:** 2026-05-31T18:03:36Z
- **Completed:** 2026-05-31T18:17:53Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Implemented BackendConfig frozen dataclass with D-27 secret hygiene (api_key_env holds env-var NAME only; inline api_key rejected with CliError)
- resolve_backend follows FORGE_BACKEND > config default > session-default precedence with D-26 no-diff-routing guard (mechanically enforced via inspect.signature test)
- probe_backend dispatches: cli runs claude auth status --json (NOT inference, D-28); api checks api_key_env presence (no subprocess/network)
- File-based probe cache with 5-min TTL; failures not cached; corrupted cache treated as miss (D-08)
- 37 tests written, 36 pass (1 real_api skipped), zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD -- Write failing tests** - `fae36ec` (test)
2. **Task 2: Implement backend.py** - `d234341` (feat)

## Files Created/Modified
- `src/code_forge/backend.py` - Pluggable review-backend abstraction: BackendConfig, ProbeResult, load_backend_configs, resolve_backend, resolve_auth_timeout, probe_backend, invalidate_probe_cache, DEFAULT_BACKEND
- `tests/test_backend.py` - 37 tests covering config parse, resolution, probe, caching, and real-API opt-in

## Decisions Made
- DEFAULT_BACKEND is a cli backend with model="" -- the D-26 session model (plain `claude -p`, no --model pin)
- Secret hygiene: api_key_env holds the env-var NAME only; CliError if raw api_key field is present
- Cache timing: _write_cache records timestamp at write time; _read_cache checks staleness against its own time_fn call; first probe (file-missing) takes exception path without calling time_fn in _read_cache
- Broad except in _read_cache is intentional (cache is convenience-only; re-probe is always safe)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed cache test iterator exhaustion**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Cache timing tests provided incorrect number of iterator values for time_fn; _write_cache and _read_cache call time_fn independently, and file-missing on first probe skips _read_cache's time_fn call, so the required call count differs from the naive expectation
- **Fix:** Updated iterator values in test_probe_cache_hit_within_ttl (2 values) and test_probe_cache_miss_after_ttl (3 values) to match actual call sequence
- **Files modified:** tests/test_backend.py
- **Verification:** All 36 non-real_api tests pass
- **Committed in:** d234341 (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test fix only; no scope creep. The implementation matches the plan exactly.

## Issues Encountered
None -- implementation matched the plan specification.

## User Setup Required
None -- no external service configuration required.

## Next Phase Readiness
- backend.py ready for outlet_resolver.py (05-03/04) integration
- probe_backend ready to be called by outlet resolution logic
- resolve_backend ready to be called by cli.py when Phase 7 wires the review subcommand
- DEFAULT_BACKEND provides the session-model fallback for both outlets
- No blockers for 05-03 or 05-04

## TDD Gate Compliance

Verified in git log:
1. `fae36ec` - test(05-02) commit exists (RED gate)
2. `d234341` - feat(05-02) commit exists after it (GREEN gate)
3. No refactor commit needed (code is clean from initial implementation)

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 05-prerequisites*
*Completed: 2026-05-31*
