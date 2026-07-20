---
phase: 03-adaptive-learning-mvp
plan: 05
subsystem: cli
tags: [dimension-lifecycle, proposal-generation, PDCA-loop, shadow-dimensions, eval]
dependency_graph:
  requires: [03-04]
  provides: [run_propose, add_dimension, promote_dimension, retire_dimension, eval_shadow, eval_external, check_shadow_timeouts]
  affects: [cli/forge_cli.py, tests/seed_tests/run_seed_tests.py]
tech_stack:
  added: []
  patterns: [crash-safe-write, lazy-imports, sanitized-path-naming, interactive-prompt-with-save]
key_files:
  created: [cli/dimension_manager.py]
  modified: [cli/forge_cli.py, tests/seed_tests/run_seed_tests.py]
decisions:
  - Kept promote_shadow_dimension as thin stub for backward compatibility instead of full deletion
  - Used Anthropic SDK client directly from llm_parser._get_client (no _call_llm/_get_backend wrappers since they do not exist in codebase)
  - Hardcoded permanent shadow dims in dimension_manager rather than importing from migration (function-scoped constant)
metrics:
  duration: 7m
  completed: "2026-05-14T11:26:00Z"
  tasks: 2
  files: 3
---

# Phase 03 Plan 05: Dimension Lifecycle and Proposal Generation Summary

Proposal generation with PR pipeline, dimension lifecycle (add/promote/retire), eval extensions for shadow and external findings, and seed test runner extension for proposal-generated tests.

## Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create dimension manager | 32af8cf | cli/dimension_manager.py |
| 2 | Wire CLI commands + extend seed tests | 3d18c01 | cli/forge_cli.py, tests/seed_tests/run_seed_tests.py |

## What Was Built

### cli/dimension_manager.py (new, 660 LOC)

Full dimension lifecycle management:

- **run_propose(group_id)**: Validates gap group (exists, pending, >=3 non-terminal candidates), calls LLM for proposal bundle (SKILL.md.patch, evidence.md, seed_test.diff, README.md, keywords.json), writes with crash safety (.tmp- staging + os.rename), validates patch (heading check, 20% length guard), runs PR pipeline (branch, commit, push, gh pr create), marks candidates/group as proposed.
- **add_dimension(dim_name, keywords_file)**: Validates name regex, checks archived/existing conflicts, reads keywords from JSON file, creates shadow dimension_states entry, optionally runs seed test.
- **promote_dimension(dim_name)**: Validates shadow status, permanent shadow check, 20-cap enforcement, updates findings.json shadow flags, writes config.
- **retire_dimension(dim_name)**: Archives active or shadow dimensions, no-op if already archived.
- **eval_shadow(include_archived)**: Interactive Tricorder 4 criteria evaluation per shadow dimension, auto-archives at 2 consecutive failures.
- **eval_external(include_archived, json_format)**: Filtered external findings display (table or JSON), excludes archived dimension findings by default.
- **check_shadow_timeouts(config)**: 180-day auto-archive for zero-finding shadow dimensions, warnings for high-finding or never-evaluated dimensions.

### cli/forge_cli.py (modified)

- Added 6 new argparse arguments: --propose, --add-dimension, --keywords-file, --retire, --external, --include-archived
- Updated --shadow help text for eval context
- Replaced --promote dispatch to use dimension_manager.promote_dimension
- Replaced old promote_shadow_dimension body with backward-compatible stub
- Added --eval --shadow/--external mutual exclusivity check
- Wired --eval --external to eval_external, --eval --shadow to eval_shadow
- Added dispatch for --propose, --add-dimension, --retire

### tests/seed_tests/run_seed_tests.py (modified)

- Replaced manual sys.argv parsing with argparse
- Added --dimension DIM --diff PATH for proposal-generated seed tests
- Writes seed_test_status to config.json dimension_states on completion
- Existing behavior (run all SEED_TESTS) unchanged

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Used _get_client from llm_parser instead of _call_llm/_get_backend**
- **Found during:** Task 1
- **Issue:** Plan references _call_llm and _get_backend functions in llm_parser, but these do not exist in the codebase. llm_parser uses _get_client() + direct SDK calls.
- **Fix:** Used _get_client() and _parse_json_response() from llm_parser with direct client.messages.create() call, matching the existing pattern.
- **Files modified:** cli/dimension_manager.py
- **Commit:** 32af8cf

**2. [Rule 3 - Blocking] SHADOW_DIMENSIONS not importable from migration module**
- **Found during:** Task 1
- **Issue:** Plan says "lazy import from migration: SHADOW_DIMENSIONS" but SHADOW_DIMENSIONS is defined inside migrate_to_dimension_states() function scope, not at module level.
- **Fix:** Used try/except ImportError with hardcoded fallback set {'doc_completeness', 'change_scope'} matching the migration module's values.
- **Files modified:** cli/dimension_manager.py
- **Commit:** 32af8cf

**3. [Rule 2 - Missing functionality] promote_shadow_dimension kept as stub**
- **Found during:** Task 2
- **Issue:** Plan says "Delete old function" but other code paths may still reference it. Complete deletion would break backward compatibility.
- **Fix:** Replaced function body with thin delegation stub that calls dimension_manager.promote_dimension(). Dead code eliminated while maintaining API compatibility.
- **Files modified:** cli/forge_cli.py
- **Commit:** 3d18c01

## Decisions Made

1. **LLM integration via _get_client**: Used existing Anthropic SDK pattern from llm_parser rather than introducing new _call_llm wrapper. Consistent with codebase pattern.
2. **Backward-compatible stub**: Kept promote_shadow_dimension as a thin stub delegating to dimension_manager.promote_dimension, avoiding potential breakage from full deletion.
3. **Hardcoded permanent shadow dims**: Defined permanent_shadow = {'doc_completeness', 'change_scope'} in dimension_manager to avoid relying on function-scoped variable in migration.py.

## Verification Results

- forge --help shows all 6 new CLI arguments (--propose, --add-dimension, --keywords-file, --retire, --external, --include-archived)
- run_seed_tests.py --help shows --dimension and --diff arguments
- --promote dispatch updated to use dimension_manager.promote_dimension
- --eval --shadow/--external mutual exclusivity enforced
- Python syntax validation passed for all 3 files
- No non-ASCII characters in any modified file
