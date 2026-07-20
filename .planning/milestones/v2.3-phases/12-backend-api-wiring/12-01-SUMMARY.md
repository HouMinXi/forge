---
phase: 12-backend-api-wiring
plan: 01
subsystem: backend
tags: [backend-config, cli-flags, dict-schema, max-tokens]
dependency_graph:
  requires: []
  provides:
    - BackendConfig.max_tokens field
    - load_backend_configs dict-based schema (D-11)
    - CLI flags --backend, --backend-url, --backend-format, --backend-key-env, --backend-model
  affects:
    - src/code_forge/backend.py
    - src/code_forge/cli.py
    - tests/test_backend.py
tech_stack:
  added: []
  patterns:
    - dict-based YAML schema with name injection from key
    - frozen dataclass field with default value (max_tokens)
    - argparse argument_group for cosmetic flag grouping
key_files:
  created: []
  modified:
    - src/code_forge/backend.py
    - src/code_forge/cli.py
    - tests/test_backend.py
decisions:
  - D-05: max_tokens int field with default 16384 (not Optional) - fits all target provider limits
  - D-11: dict-based backend schema, name injected from YAML key, backward-incompatible
  - D-03: multiple default:true backends raise CliError at parse time
  - D-02/D-10: --backend and 4 inline flags added to review subparser, mutual exclusion deferred to _run()
metrics:
  duration: "~25 minutes"
  completed: "2026-06-04T12:37:27Z"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 3
---

# Phase 12 Plan 01: BackendConfig max_tokens, dict schema, CLI flags Summary

**One-liner:** BackendConfig gains max_tokens field (16384 default), load_backend_configs migrated to dict-based schema with name injection, and five backend CLI flags added to review subparser.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| T1+T2 | max_tokens field + dict schema migration | 912277e | src/code_forge/backend.py, tests/test_backend.py |
| T3 | Backend CLI argument flags | 9339f6a | src/code_forge/cli.py |

## Changes Made

### Task 1: BackendConfig.max_tokens field (D-05)

Added `max_tokens: int = 16384` field to `BackendConfig` dataclass after the `command` field (line 74). Updated `DEFAULT_BACKEND` constructor to include `max_tokens=16384`. Updated `_parse_backend_entry` to read `max_tokens = entry.get("max_tokens", 16384)` and pass it to both the api-path and cli-path `BackendConfig()` constructors. Without this, a gate.yaml `max_tokens` override would be silently ignored.

### Task 2: load_backend_configs dict-based schema (D-11)

Replaced list iteration with dict iteration (`for name, entry in backends.items()`). Each entry receives its name from the YAML dict key via `entry["name"] = name`. Added:
- `isinstance(backends, dict)` check - raises CliError with "backends must be a dict with backend names as keys"
- `isinstance(entry, dict)` check per entry - raises CliError on non-dict values
- D-03 multiple-default validation - counts entries with `default=True`, raises CliError with names listed if count > 1

Migrated all 14 existing test call sites in `tests/test_backend.py` from list format to dict format. Updated `_api_entry()` and `_cli_entry()` helpers to omit the `name` key (now provided by dict key). Added `_as_api_backends()` and `_as_cli_backends()` convenience wrappers. Added 9 new tests covering dict-schema validation, non-dict entry rejection, multiple-defaults guard, name injection, max_tokens override, max_tokens default.

Removed pre-existing unused `import json` (ruff F401, zero behavioral impact).

### Task 3: Backend CLI argument flags (D-02, D-10)

Added to review subparser in `_build_parser()`:
- `--backend NAME` - select named backend from gate.yaml
- Argument group "inline backend flags":
  - `--backend-url URL`
  - `--backend-format {openai,anthropic}`
  - `--backend-key-env VAR_NAME`
  - `--backend-model MODEL_NAME`

All flags have `default=None`. Mutual exclusion between `--backend` and the inline flags is deferred to `_run()` in the next plan because argparse cannot express "all 4 inline flags required together AND mutually exclusive with --backend" via `add_mutually_exclusive_group`.

## Verification

- `python3 -m py_compile` passed on all modified files
- `ruff check` passed with zero new warnings
- Non-ASCII check: no non-ASCII characters in new code
- `pytest tests/` - 1002 tests passed, 0 failures (full suite)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed pre-existing unused import**
- **Found during:** Task 2, ruff lint pass
- **Issue:** `import json` in tests/test_backend.py was unused (pre-existing ruff F401)
- **Fix:** Removed the import line
- **Files modified:** tests/test_backend.py
- **Commit:** 912277e

**2. [Rule 2 - Missing functionality] Added 9 new tests for new dict-schema behavior**
- **Found during:** Task 2 implementation
- **Issue:** New dict-schema validation paths had no test coverage
- **Fix:** Added test methods to TestBackendConfigParse covering all new validation paths
- **Files modified:** tests/test_backend.py
- **Commit:** 912277e

**3. [Process note] Tasks 1 and 2 committed together**
- Both tasks modify `src/code_forge/backend.py` and could not be committed independently without an intermediate broken state. Committed as one atomic unit.

## Known Stubs

None. All fields are wired: max_tokens flows from gate.yaml entry through _parse_backend_entry to BackendConfig. CLI flags are parsed into args namespace. Wire-up to _run() resolution is the next plan (12-02).

## Threat Flags

None. No new network endpoints, auth paths, or schema changes at trust boundaries beyond what the plan's threat model covers. The max_tokens field stores an integer (no injection surface). Backend name comes from YAML dict key and is validated through existing _parse_backend_entry validation.

## Self-Check

## Self-Check: PASSED

| Item | Status |
|------|--------|
| src/code_forge/backend.py | FOUND |
| src/code_forge/cli.py | FOUND |
| tests/test_backend.py | FOUND |
| Commit 912277e (T1+T2) | FOUND |
| Commit 9339f6a (T3) | FOUND |
| max_tokens: int = 16384 field | FOUND |
| for name, entry in backends.items() | FOUND |
| --backend CLI flag | FOUND |
