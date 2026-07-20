---
phase: 05-prerequisites
plan: 01
subsystem: detect
tags: [tomllib, pyproject.toml, flake8, pylint, ruff, parsers, yaml, shutil.which]

requires:
  - phase: none
    provides: standalone plan, no prior phase dependency
provides:
  - detect_toolchain() for Python project toolchain detection
  - generate_tools_yaml() for auto-generating .code-forge/tools.yaml
  - detect_and_init() for idempotent first-run auto-init
  - parse_flake8 text parser for flake8 default output
  - parse_pylint JSON parser for pylint --output-format=json
  - PARSER_DISPATCH registration for flake8 and pylint_json
  - _KNOWN_FORMATS includes flake8 and sarif (ruff dispatch key fix)
affects: [05-02, 05-03, 05-04, phase-06, phase-07]

tech-stack:
  added: [tomllib (stdlib 3.12+), configparser (stdlib)]
  patterns: [dependency-injection via which_fn parameter, frozen dataclass DetectionResult, TOML-aware detection with PATH fallback]

key-files:
  created:
    - src/code_forge/detect.py
    - src/code_forge/parsers/flake8.py
    - src/code_forge/parsers/pylint.py
    - tests/test_detect.py
  modified:
    - src/code_forge/parsers/__init__.py
    - src/code_forge/registry.py
    - tests/test_parsers.py

key-decisions:
  - "ruff output_format fixed from ruff_json (no dispatch key) to sarif (real _parse_sarif key)"
  - "pylint output_format uses pylint_json with new parse_pylint parser (single JSON array, not per-line)"
  - "flake8 detected via config files (.flake8, setup.cfg, tox.ini) not pyproject.toml (flake8 has no pyproject support)"
  - "stale ruff_json left in _KNOWN_FORMATS for backward compat, marked with comment"

patterns-established:
  - "Config-file-aware detection: read pyproject.toml [tool.*] + verify binary on PATH"
  - "flake8 config-file branch runs independently of [tool.*] walk"
  - "Text parser structure: compiled regex, empty->[], match->Finding, no-match->[ToolError]"
  - "JSON-array parser structure: json.loads whole blob, non-list->[ToolError], per-obj field mapping"

requirements-completed: [CLI-03, CLI-04]

duration: 16min
completed: 2026-05-31
---

# Phase 5 Plan 01: Toolchain Auto-Detection Summary

**Python toolchain auto-detection with ruff/pylint/flake8 parsers, pyproject.toml-aware detection, and round-trip validated tools.yaml generation**

## Performance

- **Duration:** 16 min
- **Started:** 2026-05-31T17:41:10Z
- **Completed:** 2026-05-31T17:57:00Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- Implemented detect.py with pyproject.toml [tool.*] detection, PATH fallback (R2-1), flake8 config-file detection, corrupted TOML recovery, and idempotent init
- Created parse_flake8 text parser and parse_pylint JSON-array parser, registered in PARSER_DISPATCH
- Fixed confirmed L0 defect: ruff/pylint/flake8 output_format values now resolve through both load_registry validation and parse_output dispatch without KeyError
- 66 tests passing (24 detect + 42 parsers) with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: TDD -- Write failing tests for detect module** - `f818778` (test)
2. **Task 2: Implement detect.py to pass all tests** - `c75f592` (feat)
3. **Task 3: flake8 + pylint parsers + registry fix** - `fa73187` (feat)

## Files Created/Modified
- `src/code_forge/detect.py` - Toolchain auto-detection: detect_toolchain, generate_tools_yaml, detect_and_init, DetectionResult
- `src/code_forge/parsers/flake8.py` - Text parser for flake8 default output (path:row:col: CODE message)
- `src/code_forge/parsers/pylint.py` - JSON parser for pylint --output-format=json (single array, level mapping)
- `src/code_forge/parsers/__init__.py` - PARSER_DISPATCH: added "flake8" and "pylint_json" keys
- `src/code_forge/registry.py` - _KNOWN_FORMATS: added "flake8" and "sarif"
- `tests/test_detect.py` - 24 tests for detection module
- `tests/test_parsers.py` - Added TestParseFlake8 (5 tests), TestParsePylint (5 tests), updated dispatch tests

## Decisions Made
- ruff output_format changed from "ruff_json" (stale, no dispatch key) to "sarif" (real _parse_sarif key) -- closes confirmed L0 KeyError defect
- pylint parser uses single json.loads (mirrors _sarif.py), not per-line parsing (differs from clippy) -- because pylint emits a single JSON array
- flake8 detection uses config-file branch independent of [tool.*] walk -- flake8 has no pyproject.toml support
- All flake8 findings mapped to level="warning" (flake8 emits no severity, "warning" is honest advisory default)
- pylint level mapping: fatal/error->"error", warning->"warning", convention/refactor/information->"note"

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Worktree PYTHONPATH: worktree does not inherit the editable install from the main tree. Resolved by setting PYTHONPATH=src for test runs. Not a code issue.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- detect.py ready for integration into cli.py (Phase 7: code-forge detect subcommand)
- detect_and_init() ready for D-20 integration (code-forge review calls it when tools.yaml missing)
- All three linters (ruff, pylint, flake8) fully wired: detection + tools.yaml entry + parser
- No blockers for 05-02, 05-03, or 05-04

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 05-prerequisites*
*Completed: 2026-05-31*
