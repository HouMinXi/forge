---
phase: 01-r1-commit-gate-r4-docs
plan: 01
subsystem: cli
tags: [subparsers, backward-compat, gate-config]
dependency_graph:
  requires: []
  provides: [subparser-routing, gate-yaml-source-patterns]
  affects: [cli.py, gate.yaml, test_cli_parser.py]
tech_stack:
  added: [argparse.subparsers]
  patterns: [subcommand-routing, backward-compat-default]
key_files:
  created: []
  modified:
    - src/forge/cli.py (subparser restructure, 139 insertions)
    - .forge/gate.yaml (source_patterns field)
    - tests/test_cli_parser.py (subparser tests, 8 new tests)
    - tests/test_cli_phase1_compat.py (review subcommand prefix)
decisions:
  - Backward compat via argv prepending: non-subcommand first arg triggers 'review' prepend
  - --version stays on root parser for `forge --version`
  - gate-check and install-hooks are stubs returning EXIT_CLI_ERROR
  - source_patterns is ["*.py"] for forge itself (Python-only source)
metrics:
  duration: 6m 41s
  tasks_completed: 2
  files_modified: 4
  tests_added: 8
  tests_total: 529 (all pass)
  commits: 2
completed_date: 2026-05-25
---

# Phase 01 Plan 01: CLI Subparser Restructure + gate.yaml source_patterns

**One-liner:** CLI gains three subcommands (review/gate-check/install-hooks) with full backward compatibility; gate.yaml gains source_patterns field for file-type filtering.

## Tasks Completed

### Task 1: Restructure cli.py to subparser architecture (D-01)

**Commit:** 5d856eb

Restructured _build_parser() to use argparse.subparsers:

- **Three subparsers created:**
  - `review`: all existing flags (--mode, --baseline, --head, --registry, --state-dir, --max-total-rounds, --max-fix-attempts, --quiet, --staged, paths)
  - `gate-check`: minimal (--quiet only)
  - `install-hooks`: minimal (--quiet only)

- **Root parser:** --version only (so `forge --version` works)

- **Backward compatibility:**
  - Bare `forge` (no subcommand): args.subcommand=None, main() sets to 'review'
  - `forge a.py b.py`: main() prepends 'review' to argv when first non-flag arg is not a known subcommand
  - `forge --mode local a.py`: prepends 'review', routes flags correctly

- **Stub routing:**
  - gate-check: prints "not yet implemented (Plan 02)", returns EXIT_CLI_ERROR
  - install-hooks: prints "not yet implemented (Plan 03)", returns EXIT_CLI_ERROR

**Verification:** All existing review pipeline behavior preserved; argparse accepts all three subcommands; bare forge + positional args work.

### Task 2: Add source_patterns to gate.yaml + update parser tests (D-02)

**Commit:** 0980fe9

**gate.yaml changes:**
- Added `source_patterns: ["*.py"]` under test section
- Updated header comment to document the field
- yaml.safe_load verification passes

**Parser test updates (test_cli_parser.py):**
- `test_no_subcommand_defaults_review`: bare forge has subcommand=None
- `test_review_subcommand_explicit`: 'forge review' has subcommand='review'
- `test_review_subcommand_defaults`: review subcommand defaults match old forge defaults
- `test_review_flags_preserved`: review --mode local --baseline HEAD works
- `TestSubcommands` class: 4 new tests for gate-check and install-hooks routing + --quiet flag
- Updated `TestParserAllFlags.test_all_flags_set` to prepend 'review'
- Updated `TestParserInvalidChoices` tests to prepend 'review'
- Removed unused `sys` import (ruff clean)

**Phase 1 compat test updates (test_cli_phase1_compat.py):**
- `test_registry_flag_accepted`: prepend 'review' to parse_args
- `test_quiet_flag_accepted`: prepend 'review' to parse_args
- Tests using sys.argv + main() still pass (backward compat logic handles it)

**Test results:** 529 tests pass (8 new tests added).

## Deviations from Plan

None - plan executed exactly as written.

## Architecture Changes

**CLI layer:**
- Flat argparse to subparser structure
- Single entry point to three subcommands
- Backward compat via argv prepending (no parse_known_args needed)

**Config layer:**
- gate.yaml gains source_patterns field (list of glob patterns)
- Separates source file filtering from tools.yaml file_patterns (which control linter routing)

## Integration Points

**Downstream dependencies:**
- Plan 02 (gate-check implementation) will parse gate.yaml source_patterns and use fnmatch
- Plan 03 (install-hooks implementation) will write hooks calling `forge gate-check`
- Plan 04 (R4 docs) has no CLI dependency

**Upstream dependencies:**
- None (Plan 01 is Wave 1, no prior dependencies)

## Verification Evidence

**Step 0 (EC-1):**
- Ruff: All checks passed (src/forge/cli.py, tests/)
- Non-ASCII: ASCII CLEAN

**Automated verification:**
- All 529 tests pass (pytest tests/ -q)
- Subparser routing verified (test_cli_parser.py::TestSubcommands)
- Backward compat verified (test_cli_phase1_compat.py all pass)
- gate.yaml loads via yaml.safe_load, source_patterns present

**Manual verification:**
- `forge --version` works (root parser)
- `forge a.py b.py` routes to review (backward compat)
- `forge review --mode local` works (explicit subcommand)
- `forge gate-check` returns "not yet implemented" + EXIT_CLI_ERROR
- `forge install-hooks` returns "not yet implemented" + EXIT_CLI_ERROR

## Known Stubs

No stubs - all implemented code is production-ready. gate-check and install-hooks are intentional stubs (Plan 02/03 will implement them).

## Self-Check

### Created files exist: N/A (no new files created)

### Modified files exist:
```bash
[FOUND] src/forge/cli.py
[FOUND] .forge/gate.yaml
[FOUND] tests/test_cli_parser.py
[FOUND] tests/test_cli_phase1_compat.py
```

### Commits exist:
```bash
[FOUND] 5d856eb (Task 1: CLI subparser restructure)
[FOUND] 0980fe9 (Task 2: gate.yaml + tests)
```

### Test evidence:
```bash
[VERIFIED] 529 tests pass (PYTHONPATH=src python3 -m pytest tests/ -q)
[VERIFIED] gate.yaml parses: source_patterns = ['*.py']
[VERIFIED] Subparser routing: review/gate-check/install-hooks all recognized
[VERIFIED] Backward compat: bare forge + positional args work
```

## Self-Check: PASSED

All files exist, all commits present, all tests green, all verification criteria met.
