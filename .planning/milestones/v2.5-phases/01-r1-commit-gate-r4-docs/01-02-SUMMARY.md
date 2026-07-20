---
phase: 01-r1-commit-gate-r4-docs
plan: 02
subsystem: testing
tags: [gate-check, commit-gate, pytest, baseline, ci-detection]

# Dependency graph
requires:
  - phase: 01-r1-commit-gate-r4-docs-01
    provides: [subparser infrastructure (expected but not present in worktree)]
provides:
  - gate-check subcommand core logic (parse gate.yaml, run tests, translate exit codes)
  - FAIL-OPEN guard (config errors -> BLOCK, never allow)
  - CI mode detection (FORGE_MODE=ci + 5 platform vars)
  - Test baseline delta computation (NEW failures only)
  - Source file pattern matching (skip tests when no source changes)
  - CLI dispatch for gate-check subcommand
affects: [01-03-install-hooks, 02-mutation-gate, pre-commit-hook]

# Tech tracking
tech-stack:
  added: [yaml (PyYAML)]
  patterns:
    - Pure functions with injected dependencies (testability)
    - FAIL-OPEN security guard (config errors isolated from test exit codes)
    - Exit code translation layer (test codes -> hook codes)
    - Baseline bootstrap pattern (None baseline -> allow + warn)

key-files:
  created:
    - src/forge/gate_check.py
    - tests/test_gate_check.py
  modified:
    - src/forge/cli.py

key-decisions:
  - "FAIL-OPEN guard: run_gate_check returns only 0 or 1, NEVER 2 (EXIT_CLI_ERROR) - config/parse errors return EXIT_FAIL (1) to prevent mistranslation"
  - "NF-1 fix: exit 4/5/timeout/>5 BLOCK immediately WITHOUT baseline delta check (pipeline problems, not test failures)"
  - "Empty source_patterns list defaults to [] -> always run tests (no filter)"
  - "Baseline delta applies ONLY to test exit 1 (real failures)"
  - "CI mode detection: FORGE_MODE + 5 platform vars (CI, GITHUB_ACTIONS, GITLAB_CI, JENKINS_URL, BUILD_URL)"

patterns-established:
  - "Pure function testing: inject fs_open callable for file I/O tests"
  - "Exit code translation: test subprocess codes -> pre-commit hook codes (SPEC v3.2 table)"
  - "Baseline bootstrap: None baseline -> allow + warn, not BLOCK"
  - "Source pattern ordering: check empty staged_files BEFORE empty patterns (avoid vacuous True)"

requirements-completed: [EC-1, EC-2, EC-4, EC-5, EC-6]

# Metrics
duration: 15min
completed: 2026-05-25
---

# Phase 01-r1-commit-gate-r4-docs Plan 02: gate-check Core Logic

**Implemented test-based commit gate: parses gate.yaml, runs tests, translates exit codes, enforces FAIL-OPEN guard, detects CI mode, computes baseline delta, and filters on source patterns**

## Performance

- **Duration:** ~15 minutes
- **Started:** 2026-05-25T14:11:00Z (approximate)
- **Completed:** 2026-05-25T14:26:00Z (approximate)
- **Tasks:** 2 tasks completed
- **Files modified:** 3 files (1 created module, 1 created test file, 1 modified CLI)

## Accomplishments

- **gate_check.py module with 8 core functions:** load_gate_config, validate_command_safety, is_ci_mode, match_source_patterns, load_test_baseline, compute_baseline_delta, translate_exit_code, run_gate_check
- **FAIL-OPEN guard enforced:** config/parse errors return EXIT_FAIL (1), never EXIT_CLI_ERROR (2), preventing mistranslation to allow+warn
- **40 comprehensive unit tests:** 18 for EC-2 (parse + translate + FAIL-OPEN), 9 for EC-4 (CI detection), 5 for EC-5 (baseline delta), 4 for EC-6 (source patterns), 4 integration tests
- **CLI dispatch wired:** `forge gate-check` routed to run_gate_check() via main() early-exit pattern
- **Full suite green:** 561 tests pass (521 original + 40 new)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create gate_check.py module with all core logic** - `514e683` (feat)
   - load_gate_config: parse gate.yaml with validation
   - validate_command_safety: known_runners allowlist + metachar rejection
   - is_ci_mode: FORGE_MODE=ci + 5 platform vars
   - match_source_patterns: fnmatch filter with empty-list default
   - load_test_baseline: JSON load with bootstrap (None)
   - compute_baseline_delta: NEW failure detection vs baseline
   - translate_exit_code: 0->0, 1->1, 2/3->0, 4/5->1, >5->1
   - run_gate_check: main entrypoint, FAIL-OPEN guard (return 1, never 2)

2. **Task 2: Wire gate-check into CLI + write comprehensive tests** - `0b2b0d1` (feat)
   - cli.py: Add gate-check subcommand dispatch in main()
   - gate_check.py: Fix match_source_patterns empty-files-first check
   - tests/test_gate_check.py: 40 unit tests covering EC-2, EC-4, EC-5, EC-6

## Files Created/Modified

- `src/forge/gate_check.py` - gate-check subcommand core logic: config parsing, command safety validation, CI detection, baseline delta computation, exit code translation, FAIL-OPEN guard
- `tests/test_gate_check.py` - 40 unit tests covering all requirements (EC-2, EC-4, EC-5, EC-6) plus integration tests
- `src/forge/cli.py` - gate-check subcommand dispatch added to main() (early-exit pattern before existing review pipeline)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan 01 outputs not present in worktree**
- **Found during:** Task 2 setup
- **Issue:** Plan depends_on [01-01] but worktree lacks subparser infrastructure from Plan 01. cli.py has single-command structure, not subparsers.
- **Fix:** Implemented minimal gate-check dispatch via early-exit in main() (check sys.argv[1] == "gate-check" before argparse). Preserves backward compatibility while adding new subcommand. Full subparser refactor deferred to Plan 03 or later.
- **Files modified:** src/forge/cli.py (added 10 lines to main())
- **Commit:** 0b2b0d1 (combined with Task 2)

**2. [Rule 1 - Bug] match_source_patterns empty-list check order**
- **Found during:** test_gate_check.py execution
- **Issue:** `match_source_patterns([], [])` returned True (should be False). Empty patterns check ran before empty staged_files check, causing vacuous True.
- **Fix:** Reordered checks: empty staged_files first (return False), then empty patterns (return True). Matches test expectations and logical flow.
- **Files modified:** src/forge/gate_check.py
- **Commit:** 0b2b0d1 (combined with Task 2)

## Test Coverage

| Requirement | Tests | Coverage |
|-------------|-------|----------|
| EC-1 (syntax/format) | Step 0 automated | ruff clean, no non-ASCII, syntax verified |
| EC-2 (parse + translate + FAIL-OPEN) | 18 tests | load_gate_config (4), validate_command_safety (3), translate_exit_code (7), FAIL-OPEN guard (4) |
| EC-4 (CI detection) | 9 tests | is_ci_mode (8), skip-tests-ignored-in-ci (1) |
| EC-5 (baseline delta) | 5 tests | no-baseline, known-failure, new-failure, new-test-passes, regression |
| EC-6 (source patterns) | 4 tests | py-match, md-no-match, empty-patterns, no-staged-files |
| Integration | 4 tests | skip-in-local, test-pass, test-fail-new-failure, never-returns-exit-2 |

**Total:** 40 gate-check tests + 521 existing tests = 561 tests, all passing.

## Verification

All acceptance criteria met:

- [x] gate_check.py exists with all 8 functions listed in plan
- [x] translate_exit_code: 0->0, 1->1, 2->0, 3->0, 4->1, 5->1, 99->1
- [x] validate_command_safety rejects shell metacharacters and unknown runners
- [x] is_ci_mode detects FORGE_MODE=ci, CI, GITHUB_ACTIONS, GITLAB_CI, JENKINS_URL, BUILD_URL
- [x] run_gate_check returns only 0 or 1, NEVER 2
- [x] FAIL-OPEN: missing gate.yaml -> return 1 (BLOCK)
- [x] FAIL-OPEN: invalid YAML -> return 1 (BLOCK)
- [x] No baseline -> allow + warn (bootstrap)
- [x] NEW failure -> BLOCK
- [x] match_source_patterns with empty patterns list -> True (always run tests)
- [x] gate.yaml without source_patterns field -> defaults to [] -> always run
- [x] Exit code 2/3 warning prints "forge: warning: tests exited with code N (reason)" to stderr
- [x] ruff clean, no non-ASCII
- [x] cli.py dispatches gate-check to run_gate_check (not stub)
- [x] tests/test_gate_check.py has 40 test cases (25+ required)
- [x] All gate-check tests pass
- [x] Full suite (561 tests) green
- [x] NF-1 fix: exit 4/5/timeout/>5 BLOCK immediately, no baseline delta

## Self-Check: PASSED

All claimed artifacts verified:

```bash
# Files exist
[ -f src/forge/gate_check.py ] && echo "FOUND: src/forge/gate_check.py" || echo "MISSING"
[ -f tests/test_gate_check.py ] && echo "FOUND: tests/test_gate_check.py" || echo "MISSING"

# Commits exist
git log --oneline --all | grep -q "514e683" && echo "FOUND: 514e683" || echo "MISSING"
git log --oneline --all | grep -q "0b2b0d1" && echo "FOUND: 0b2b0d1" || echo "MISSING"

# Tests pass
PYTHONPATH=src python3 -m pytest tests/test_gate_check.py -q  # 40 passed
PYTHONPATH=src python3 -m pytest tests/ -q  # 561 passed
```

All checks passed.
