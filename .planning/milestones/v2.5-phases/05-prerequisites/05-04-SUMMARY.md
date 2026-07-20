---
phase: 05-prerequisites
plan: 04
subsystem: cli-integration
tags: [argparse, subcommands, detect, resolve-outlet, auto-detect, lazy-import, review-pipeline]

requires:
  - phase: 05-01
    provides: detect_and_init, detect_toolchain, DetectionResult from detect.py
  - phase: 05-02
    provides: BackendConfig, ProbeResult, resolve_backend, probe_backend from backend.py
  - phase: 05-03
    provides: resolve_outlet, load_outlet_from_gate from outlet_resolver.py
provides:
  - code-forge detect subcommand (toolchain detection + tools.yaml generation)
  - code-forge resolve-outlet subcommand (outlet selection with backend probe)
  - Review pipeline auto-detect integration (D-20)
  - _safe_load_registry helper for ValueError->CliError translation
affects: [phase-06, phase-07]

tech-stack:
  added: []
  patterns: [lazy import for subcommand handlers, _safe_load_registry ValueError->CliError wrapper, is_default_registry exact string match for argparse default]

key-files:
  created:
    - tests/test_cli_detect.py
  modified:
    - src/code_forge/cli.py
    - tests/test_cli_integration.py

key-decisions:
  - "resolve-outlet passes NO reachability_fn -- uses resolve_outlet default backend probe (D-29)"
  - "resolve-outlet CliError exits 1 (EXIT_FAIL, runtime), ValueError exits 2 (EXIT_CLI_ERROR, config)"
  - "is_default_registry uses exact string match against argparse default literal (intentional)"
  - "_safe_load_registry wraps all load_registry calls to handle Python exception scoping"
  - "detect_and_init called with quiet=True in review path to suppress stdout pollution"

patterns-established:
  - "Lazy import in subcommand handlers avoids circular imports and startup cost"
  - "_safe_load_registry helper ensures ValueError->CliError for all registry load paths"
  - "Auto-detect triggers on both FileNotFoundError (missing) and {} return (empty)"

requirements-completed: [CLI-03, CLI-04, CLI-05, BOTH-04, BACKEND-01]

duration: 24min
completed: 2026-05-31
---

# Phase 5 Plan 04: CLI Integration Summary

**Wire detect, backend, and outlet_resolver modules into CLI with detect and resolve-outlet subcommands plus review auto-detect integration (D-20)**

## Performance

- **Duration:** 24 min
- **Started:** 2026-05-31T23:31:16Z
- **Completed:** 2026-05-31T23:56:11Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Added detect and resolve-outlet subcommands to _build_parser and main() routing
- resolve-outlet routes through the backend-agnostic reachability probe via resolve_outlet default (D-29, no claude-specific import)
- Integrated detect_and_init into review pipeline Step 2 with quiet=True (D-20)
- _safe_load_registry helper translates ValueError to CliError for all 3 registry load paths (R2-4 preserved)
- Empty tools.yaml (D-03 + D-02) triggers auto-detect; if no tools found, CliError propagates to EXIT_CLI_ERROR
- 19 new tests: 14 in test_cli_detect.py + 5 in test_cli_integration.py
- Full SC#1 integration: detect (01) + backend (02) + outlet (03) wired through CLI

## Task Commits

Each task was committed atomically:

1. **Task 1: detect and resolve-outlet subcommands (TDD RED+GREEN)** - RED: `5c3d846`, GREEN: `68ceeed` (feat)
2. **Task 2: review auto-detect integration (D-20)** - `3612de5` (feat)

## Files Created/Modified
- `src/code_forge/cli.py` - Two new subcommands (detect, resolve-outlet), _run_detect, _run_resolve_outlet, _safe_load_registry, auto-detect in Step 2
- `tests/test_cli_detect.py` - 14 tests across TestDetectSubcommand (7) and TestResolveOutletSubcommand (7)
- `tests/test_cli_integration.py` - 5 tests in TestReviewAutoDetect (missing triggers detect, existing skips, custom registry no detect, empty-tools fail, corrupted-tools fail)

## Decisions Made
- resolve-outlet does not import any claude-specific auth function; it relies on resolve_outlet default reachability_fn from outlet_resolver.py which calls probe_backend (D-29 backend-agnostic)
- resolve-outlet CliError exits EXIT_FAIL (1) because backend-unreachable is a runtime condition; ValueError exits EXIT_CLI_ERROR (2) because invalid outlet string is a config error (D-15)
- is_default_registry = (args.registry == ".code-forge/tools.yaml") uses exact string match against the argparse default literal at line 172; explicit --registry with any different path (including ./prefix) intentionally skips auto-detect
- _safe_load_registry extracted as a helper because Python exception scoping means a ValueError from code inside an except block propagates uncaught past a sibling except ValueError clause
- detect_and_init called with quiet=True in review path to suppress stdout that would pollute review output (Kimi-B4-1)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed mock patch targets for lazy imports**
- **Found during:** Task 1 (GREEN phase)
- **Issue:** Tests used `patch("code_forge.cli.detect_and_init", ...)` but `detect_and_init` is lazy-imported inside `_run_detect`, so the name does not exist on the cli module at patch time
- **Fix:** Changed patch targets to `code_forge.detect.detect_and_init` and `code_forge.outlet_resolver.resolve_outlet` (the source modules)
- **Files modified:** tests/test_cli_detect.py
- **Committed in:** 68ceeed (part of Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test mock target fix only; no scope creep. The implementation matches the plan exactly.

## Issues Encountered
None -- implementation matched the plan specification.

## User Setup Required
None -- no external service configuration required.

## Next Phase Readiness
- Phase 5 complete: all 4 plans executed
- All three new modules (detect, backend, outlet_resolver) fully wired through CLI
- SC#1 integration verified: user can run code-forge review in a Python project with no tools.yaml and get L0 linting
- Ready for Phase 6 (Outlet B inline merge) and Phase 7 (Outlet A CLI dispatcher)

## TDD Gate Compliance

Verified in git log:
1. `5c3d846` - test(05-04) commit exists (RED gate)
2. `68ceeed` - feat(05-04) commit exists after it (GREEN gate)
3. No refactor commit needed (code is clean from initial implementation)

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 05-prerequisites*
*Completed: 2026-05-31*
