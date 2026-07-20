---
phase: 24
plan: 02
subsystem: cli/init
tags: [init, schema, package-data, importlib-resources]
dependency_graph:
  requires: [24-01]
  provides: [gate.schema.json deployed by init]
  affects: [src/code_forge/cli.py, pyproject.toml]
tech_stack:
  added: [importlib.resources.files, jsonschema>=4.18 (dev)]
  patterns: [importlib.resources traversal API, package-data glob]
key_files:
  modified:
    - src/code_forge/cli.py
    - pyproject.toml
decisions:
  - schema write is silent-skip (not error) when file exists without --force, matching supplementary-file convention
  - importlib.resources.files() chosen over deprecated read_text() for Python 3.12+ compatibility
  - jsonschema added to dev deps only (validation is test-time, not runtime)
metrics:
  duration: ~25 minutes
  completed: 2026-06-15
  review_cycles: 3
  smoke_tests_run: 3
---

# Phase 24 Plan 02: Init Deploys gate.schema.json Summary

`code-forge init` now writes `gate.schema.json` alongside `gate.yaml` using `importlib.resources.files()`, with `--force` guard and offline resolution.

## What Was Built

The `init` subcommand previously created only `.code-forge/gate.yaml`. Users had no local copy of the JSON schema, so editor `$schema` directives resolved from a remote URL or failed entirely offline.

This plan adds schema deployment to the init flow:

- `pyproject.toml`: added `"gate.schema.json"` to `code_forge` package-data so the file is included in installed wheels/editable installs.
- `pyproject.toml`: added `jsonschema>=4.18` to dev extras for schema validation in tests.
- `src/code_forge/cli.py` (line 1077): imports `importlib.resources.files` as `_pkg_files` inside the `init` branch.
- `src/code_forge/cli.py` (lines 1091-1095): writes the schema to `.code-forge/gate.schema.json` after `gate.yaml`, respecting the `--force` guard. Silent skip (no error) when schema exists and `--force` is not given.
- `src/code_forge/cli.py` (line 469): updated `--force` help text to mention both `gate.yaml` and `gate.schema.json`.

## Commit

| Hash | Message |
|------|---------|
| fafb766 | feat: code-forge init deploys gate.schema.json alongside gate.yaml |

## Review Cycles

Three genuine cycles were run on the final diff.

**Cycle 1 (pre-fix):** Clean on Pass 1 and Pass 3. Pass 2 (expert) surfaced one finding:
- C2-P2-1 (LOW): `--force` help text said "overwrite existing gate.yaml" but `--force` now also controls schema overwrite. Fixed by updating the help string to "overwrite existing gate.yaml and gate.schema.json". Cycle reset to 1.

**Cycle 1 (post-fix):** All 3 passes clean (0 findings).

**Cycle 2:** All 3 passes clean (0 findings). Verified: `requires-python = ">=3.12"` makes `files()` the correct API; package-data flat glob resolves correctly to `src/code_forge/gate.schema.json`; write error handling matches the pre-existing unguarded pattern in surrounding code (pre-existing, out of scope).

**Cycle 3:** All 3 passes clean (0 findings). Final non-ASCII check: PASS.

## Smoke Test Results

Three scenarios run using Python 3.14 with PYTHONPATH pointing to worktree src (the installed CLI uses Python 3.9 shebang — a pre-existing environment mismatch unrelated to this change; `importlib.resources.files` is available in both 3.9 and 3.14).

| Scenario | Result |
|----------|--------|
| Fresh `init`: both `gate.yaml` and `gate.schema.json` created, schema has `properties.backends` | PASS |
| `init` with `gate.yaml` existing and no `--force`: non-zero rc, `gate.schema.json` sentinel preserved unchanged | PASS |
| `init --force` with both files existing: both overwritten, schema has correct structure | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Review Finding C2-P2-1 - Docs] `--force` help text incomplete**
- **Found during:** Cycle 2, Pass 2
- **Issue:** Help string read "overwrite existing gate.yaml" but `--force` now also overwrites `gate.schema.json`
- **Fix:** Updated to "overwrite existing gate.yaml and gate.schema.json"
- **Files modified:** `src/code_forge/cli.py` line 469
- **Commit:** fafb766 (included in same commit)

## Known Stubs

None. Both files are written from real package data; no placeholder content.

## Threat Flags

None. The init subcommand writes to `.code-forge/` under the user's current working directory. No new network endpoints, auth paths, or trust-boundary crossings introduced. The schema content comes from the installed package (not from user input).

## Self-Check: PASS

- [x] `src/code_forge/cli.py` modified: confirmed (fafb766, 9 insertions)
- [x] `pyproject.toml` modified: confirmed (fafb766, 2 files changed)
- [x] Commit fafb766 exists: confirmed via `git log`
- [x] No file deletions in commit: confirmed via `git diff --diff-filter=D HEAD~1 HEAD` (empty)
- [x] Smoke test PASS: all 3 scenarios passed
- [x] 3 review cycles completed: documented above
