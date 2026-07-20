---
phase: 21-legacy-intent
plan: 03
subsystem: legacy-advisory-pipeline
tags: [legacy, advisory, wiring, integration, sarif-fix]
dependency_graph:
  requires: [21-01, 21-02]
  provides: [REVIEW-LEGACY-01, REVIEW-INTENT-01]
  affects: [cli.py, machine.py, _sarif.py, test_legacy_integration.py]
tech_stack:
  added: []
  patterns: [advisory-runner-injection, hasattr-guard-registry]
key_files:
  created:
    - tests/test_legacy_integration.py (by prior executor, fixed here)
  modified:
    - src/code_forge/cli.py
    - src/code_forge/machine.py
    - src/code_forge/parsers/_sarif.py
    - tests/test_parsers.py
decisions:
  - "SARIF parser file:/// URI stripping was a pre-existing bug (Rule 1); fixed inline"
  - "Test registry format corrected: output_format=sarif + args=--output-format=sarif"
metrics:
  completed: 2026-06-13
---

# Phase 21 Plan 03: Wire LegacyRunner into Pipeline Summary

LegacyRunner wired into forge production pipeline with registry injection and SARIF path fix.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (prior) | Integration tests (RED) | c9f7c9e | tests/test_legacy_integration.py |
| 1 (cont) | SARIF URI fix + test fix | 30b2233 | src/code_forge/parsers/_sarif.py, tests/test_parsers.py |
| 1+2 | machine.py injection + cli.py wiring + test fix | d0f39e2 | src/code_forge/machine.py, src/code_forge/cli.py, tests/test_legacy_integration.py |

## What Was Done

1. **SARIF parser bug fix (Rule 1):** `file:///tmp/foo` was stripped to `tmp/foo` instead of `/tmp/foo`. The `_parse_sarif` function stripped `file:///` (8 chars) instead of `file://` (7 chars), losing the leading `/` for absolute paths. This caused path mismatches downstream in LegacyRunner's line-intersection logic.

2. **Test registry format fix:** The E2E test used `output_format="ruff"` which is not a valid parser dispatch key (valid: `sarif`). Also used `--output-format=json` instead of `--output-format=sarif`. Both corrected.

3. **machine.py registry injection:** Added `hasattr(runner, "registry")` guard in `_run_advisory_axes()` to inject `self.registry` into LegacyRunner before dispatch. Consistent with existing `source_files` injection pattern.

4. **cli.py LegacyRunner wiring:** Added `from .legacy import LegacyRunner`, instantiation, and inclusion in `advisory_runners` list alongside TaintRunner and RuntimeRunner in `_run_hold_loop()`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SARIF parser absolute path stripping**
- **Found during:** Task 1 (E2E test debugging)
- **Issue:** `file:///tmp/foo` stripped to `tmp/foo` instead of `/tmp/foo`, causing path mismatch in LegacyRunner line-intersection (step 8/9)
- **Fix:** Changed `uri[len("file:///"):]` to `uri[len("file://"):]` in `_sarif.py`
- **Files modified:** src/code_forge/parsers/_sarif.py, tests/test_parsers.py
- **Commit:** 30b2233

**2. [Rule 1 - Bug] Test used invalid registry format**
- **Found during:** Task 1 (E2E test debugging)
- **Issue:** Test used `output_format="ruff"` (not a valid parser key) and `--output-format=json` (wrong SARIF format)
- **Fix:** Changed to `output_format="sarif"` and `args=["check", "--output-format=sarif"]`
- **Files modified:** tests/test_legacy_integration.py
- **Commit:** d0f39e2

## Verification Results

- All 6 integration tests pass (including test_real_default_l0_runner_e2e)
- Full test suite: 1645 passed, 5 skipped
- cli.py has 2 LegacyRunner references (import + instantiation)
- machine.py has 1 runner.registry injection line
- No circular imports
- No non-ASCII characters in changes

## Self-Check: PASSED
