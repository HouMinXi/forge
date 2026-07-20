---
phase: 02-dimension-gap-closure
plan: 05
subsystem: seed-tests
tags: [seed-tests, adversarial-qe, zero-data-dimensions, D1, SKILL.md]

# Dependency graph
requires:
  - phase: 02-dimension-gap-closure
    plan: 02
    provides: "VALID_DIMENSIONS with error_handling and edge_cases, 14 attack dimensions"
provides:
  - "7 synthetic diff files targeting zero-data dimensions"
  - "Seed test runner with dry-run and full LLM modes"
  - "D1 seed test framework for detecting SKILL.md prompt gaps"
affects: [02-03-PLAN, 02-04-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Before/after file reconstruction from embedded before-state in diff files (R2 fix)"
    - "sys.executable + forge_cli.py path for CLI invocation (R8 fix)"
    - "Dry-run validation mode for zero-cost diff format checking"

key-files:
  created:
    - "tests/seed_tests/run_seed_tests.py"
    - "tests/seed_tests/seed_diffs/performance_unbounded_loop.diff"
    - "tests/seed_tests/seed_diffs/concurrency_unsynchronized.diff"
    - "tests/seed_tests/seed_diffs/error_handling_missing.diff"
    - "tests/seed_tests/seed_diffs/api_contract_break.diff"
    - "tests/seed_tests/seed_diffs/graceful_degradation_crash.diff"
    - "tests/seed_tests/seed_diffs/test_quality_mock_only.diff"
    - "tests/seed_tests/seed_diffs/ai_code_smell_drift.diff"
  modified: []

key-decisions:
  - "Each diff embeds before-state via ---BEGIN BEFORE--- markers for deterministic reconstruction"
  - "Dimension names match VALID_DIMENSIONS exactly (error_handling not error_handling_completeness)"
  - "CLI invocation uses sys.executable + forge_cli.py path, not bare forge command"

patterns-established:
  - "Seed diff format: ---BEGIN BEFORE--- block + standard unified diff"
  - "Seed test mapping: filename stem -> target_dimension in SEED_TESTS dict"
  - "Dry-run mode validates structure without LLM cost"

requirements-completed: [DIM-01, DIM-06]

# Metrics
duration: 4min
completed: 2026-05-12
---

# Phase 02 Plan 05: Seed Tests for Zero-Data Dimensions Summary

**7 synthetic diffs targeting zero-data dimensions with before/after reconstruction runner for D1 seed testing of SKILL.md prompt detection capability**

## Performance

- **Duration:** 4 min
- **Started:** 2026-05-12T16:44:14Z
- **Completed:** 2026-05-12T16:48:19Z
- **Tasks completed:** 2 of 3 (Task 3 is human-verify checkpoint)
- **Files created:** 8

## Accomplishments

- Created 7 synthetic diff files, each targeting exactly one zero-data dimension with plausible code patterns (not obviously broken, per Pitfall 6)
- Built seed test runner with two modes: --dry-run (zero-cost format validation) and full (LLM invocation via forge_cli.py)
- R2 fix: diffs embed before-state content for deterministic git diff reconstruction (replaces broken git-apply-on-placeholder approach)
- R5 fix: all target dimension names match VALID_DIMENSIONS set in SKILL.md
- R8 fix: runner uses sys.executable + absolute path to forge_cli.py instead of bare forge command
- Dry-run validates all 7 diffs successfully with exit code 0

## Task Commits

Each task was committed atomically:

1. **Task 1: Create synthetic diffs for zero-data dimensions** - `29044a7` (feat)
2. **Task 2: Create seed test runner script** - `3bb78b2` (feat)
3. **Task 3: Verify seed test diffs are realistic** - PENDING (human-verify checkpoint)

## Files Created

- `tests/seed_tests/seed_diffs/performance_unbounded_loop.diff` - N+1 query, removed LIMIT, unbounded memory (dim 10)
- `tests/seed_tests/seed_diffs/concurrency_unsynchronized.diff` - Dict mutation from background thread without lock (dim 5)
- `tests/seed_tests/seed_diffs/error_handling_missing.diff` - File I/O without try/except, no resource cleanup (dim 3)
- `tests/seed_tests/seed_diffs/api_contract_break.diff` - Renamed response fields without API versioning (dim 6)
- `tests/seed_tests/seed_diffs/graceful_degradation_crash.diff` - Hard dependency on optional library, no ImportError (dim 8)
- `tests/seed_tests/seed_diffs/test_quality_mock_only.diff` - Tests assert only on mock return values (dim 11)
- `tests/seed_tests/seed_diffs/ai_code_smell_drift.diff` - Repeated identical validation pattern drift (dim 12)
- `tests/seed_tests/run_seed_tests.py` - Seed test runner with dry-run and full LLM modes

## Seed Test Dimension Mapping

| Diff File | Target Dimension | Pattern |
|-----------|-----------------|---------|
| performance_unbounded_loop | performance | N+1 query, removed LIMIT |
| concurrency_unsynchronized | concurrency | Dict mutation without lock |
| error_handling_missing | error_handling | No try/except on file I/O |
| api_contract_break | api_contract | Renamed fields without versioning |
| graceful_degradation_crash | graceful_degradation | Hard import dependency |
| test_quality_mock_only | test_quality | Mock-only assertions |
| ai_code_smell_drift | ai_code_smell | Repeated pattern drift |

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED

- All 8 created files exist on disk
- Both task commits (29044a7, 3bb78b2) verified in git log
- Dry-run exits 0 with all 7 diffs validated
- No non-ASCII characters in any created file
- No file deletions in either commit
