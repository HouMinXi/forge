---
phase: 28-reviewer-canary-inline
plan: 03
subsystem: cli
tags: [canary, inline-outlet, cli-wiring, opt-in]
dependency_graph:
  requires: [28-01, 28-02]
  provides: [canary-cli-wiring, canary-opt-in-flag]
  affects: [cli.py, test_canary_cli.py]
tech_stack:
  added: []
  patterns: [closure-based-providers, tuple-return-for-metadata, path-containment-guard]
key_files:
  created: []
  modified:
    - src/code_forge/cli.py
    - tests/test_canary_cli.py
decisions:
  - "_load_gate_backends returns tuple[list, dict] to carry gate_data without re-reading"
  - "_load_canary_config uses gate_data dict argument, never re-reads disk"
  - "Canary provider and review provider wired as closures capturing resolved backend"
  - "Path traversal defense via os.path.realpath + startswith containment"
metrics:
  duration_seconds: 377
  completed: 2026-06-25
---

# Phase 28 Plan 03: CLI Canary Wiring Summary

Wire canary opt-in into cli.py inline outlet branch with tuple-returning _load_gate_backends, --canary flag, provider closures, and graceful degradation

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Modify _load_gate_backends + canary flag + epilogs + inline branch wiring | 1084301 | src/code_forge/cli.py |
| 2 | Integration tests for CLI canary wiring | f55e8e3 | tests/test_canary_cli.py |

## What Was Built

**Task 1 -- CLI wiring (151 insertions, 12 deletions):**
- `_load_gate_backends` return type changed from `list` to `tuple[list, dict]` so callers receive both backend configs and the full gate_data dict. All 3 call sites (line 860 in _run_eval, line 1332 in _run, line 2420 in _run_resolve_outlet) updated to unpack the tuple.
- `_load_canary_config(args, gate_data)` helper extracts canary opt-in from either `--canary` CLI flag (returns CLI defaults: n=5, threshold_ratio=0.6) or `gate_data["canary"]` section (returns gate values with defaults applied). Never re-reads gate.yaml from disk.
- `--canary` flag added to review_parser (store_true).
- Both root and review parser epilogs updated with all 8 exit codes (0 PASS through 7 UNRELIABLE).
- Inline outlet branch augmented: when canary_config is not None, dispatches `run_inline_canary` with wired `_canary_provider` (prompt includes "original" field, logs to stderr on error), `_review_provider`, and `_source_lookup` (path traversal defense via realpath + startswith containment). Diff uses `git diff HEAD` matching the main review path. Any dispatch exception falls through to DELEGATED.
- Default path (no canary opt-in) is byte-for-byte unchanged: same D4 honesty floor comments, same stderr message, same `return Verdict.DELEGATED`.

**Task 2 -- Integration tests (293 insertions, 51 total tests):**
- 34 new tests added to `tests/test_canary_cli.py` (17 Plan 02 tests preserved).
- Coverage: tuple return, canary config extraction (flag/gate/disabled/no-reread/override), default path regression, exit codes, epilog completeness, prompt schema, provider logging, diff command, path traversal, dispatch fallthrough, parser flag.
- Zero real network calls; all tests use stubs/mocks.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all data paths are wired to real implementations or graceful fallbacks.

## Self-Check: PASSED

- [x] src/code_forge/cli.py exists
- [x] tests/test_canary_cli.py exists
- [x] 28-03-SUMMARY.md exists
- [x] Commit 1084301 found (Task 1)
- [x] Commit f55e8e3 found (Task 2)
- [x] _load_gate_backends( count = 4 (1 def + 3 calls)
- [x] 51 tests collected, all passing
