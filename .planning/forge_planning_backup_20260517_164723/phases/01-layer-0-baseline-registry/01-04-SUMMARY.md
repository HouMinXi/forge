---
phase: 01-layer-0-baseline-registry
plan: 04
subsystem: forge-cli
tags: [cli, verdict, reporter, state, integration, pipeline]
dependency_graph:
  requires: [01-02, 01-03]
  provides: [forge-cli, verdict, reporter, state-persistence, tools-yaml]
  affects: [all-downstream-phases]
tech_stack:
  added: []
  patterns: [atomic-write, safe-list-construction, monkeypatch-sysargv]
key_files:
  created:
    - src/forge/verdict.py
    - src/forge/reporter.py
    - src/forge/state.py
    - src/forge/cli.py
    - src/forge/__main__.py
    - .forge/tools.yaml
    - tests/test_verdict.py
    - tests/test_integration.py
  modified:
    - .gitignore
decisions:
  - "tools.yaml uses PARSER_DISPATCH keys (shellcheck_json, sarif, clippy_json, checkpatch_emacs, grep_line), not _KNOWN_FORMATS keys from registry.py"
  - ".gitignore uses .forge/* + !.forge/tools.yaml pattern to track config while ignoring runtime data"
metrics:
  duration_seconds: 478
  completed: 2026-05-16T09:42:45Z
  tasks_completed: 2
  tasks_total: 3
  test_count_before: 162
  test_count_after: 174
---

# Phase 01 Plan 04: CLI Entry Point and Pipeline Wiring Summary

Complete forge v2.0 Layer 0 pipeline wired through CLI entry point with verdict (ToolError-aware), reporter (all_findings + tools_failed), state persistence (atomic same-dir write), and default 6-tool registry.

## Task Completion

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Verdict, reporter, state, tools.yaml | d1f04df | verdict.py, reporter.py, state.py, .forge/tools.yaml, test_verdict.py |
| 2 | CLI entry point + integration tests | 663623a | cli.py, __main__.py, test_integration.py |
| 3 | Human verification | pending | checkpoint:human-verify |

## What Was Built

### verdict.py
- `determine_verdict(delta_findings)` returns `Verdict` (tuple[str, int])
- Uses `EXIT_PASS`/`EXIT_FAIL` from `forge.__init__` (Consensus #6)
- Any `ToolError` in findings causes FAIL (Consensus #4)
- Pure function, no I/O

### reporter.py
- `format_report(delta_findings, all_findings, tool_versions, tools_skipped, tools_failed)` -- all 5 parameters (Round 3 C-1)
- Cargo-check style plain text output (D-05)
- Pre-existing violation count from all_findings
- `tools_failed` WARNING always shown, even on PASS
- Tool versions listed for reproducibility

### state.py
- `write_state()` with atomic same-directory tempfile (Round 3 H-2)
- `_ensure_dir()` auto-creates .forge/ before write
- `read_state()` returns None on missing/corrupt file
- State includes tool_versions (Consensus #3), tools_failed, delta_findings via to_dict() (Round 3 C-3)

### cli.py
- Full pipeline: registry -> run_git_diff -> diff parse -> run_tools -> parse_output -> delta filter -> verdict -> report -> state
- Uses `run_git_diff` from `forge.git` (Consensus #1) -- no direct git subprocess calls
- Safe list construction for required/optional tool error separation (Round 3 B-1)
- stderr propagation to ToolError (R5-M3)
- --quiet suppresses skipped/versions but never tools_failed (R5-M2)
- Graceful error handling for unknown output_format (R4-K2)

### __main__.py
- Enables `python3 -m forge` (R5-M1)

### .forge/tools.yaml
- 6 tools total: 5 enabled (shellcheck, ruff, semgrep, clippy, non_ascii) + checkpatch disabled (Round 3 C-4)
- Semgrep determinism note included (DeepSeek)
- Uses PARSER_DISPATCH keys for output_format

### Integration tests
- 4 tests: FAIL (shellcheck violation), PASS (clean code), baseline (pre-existing not shown), state (tool_versions populated)
- shellcheck skipif guard (Round 3 C-5)
- pytest.raises(SystemExit) for sys.exit testing (Round 3 H-4)
- monkeypatch for sys.argv isolation
- Correct git diff usage with tracked files (Consensus #5)

## Consensus Items Addressed

| # | Item | Status |
|---|------|--------|
| 1 | git diff owned by git.py | cli.py imports run_git_diff, zero subprocess.git calls |
| 2 | Line-drift N/A | Documented in diff.py (Plan 02) |
| 3 | tool_versions in state | Populated by runner.py, written by state.py |
| 4 | ToolError prevents false PASS | verdict.py checks, any non-empty list = FAIL |
| 5 | Integration test correct git diff | Tests use tracked files + monkeypatch |
| 6 | EXIT_PASS/EXIT_FAIL constants | Defined in __init__.py, used in cli.py and verdict.py |

## Round 3 Fixes Verified

- B-1: No list mutation while iterating (safe filtered_findings construction)
- C-1: format_report has tools_failed parameter
- C-3: ToolError.to_dict() exists and used for serialization
- C-4: tools.yaml has 6 entries, checkpatch disabled
- C-5: Integration test has shellcheck skipif guard
- H-2: Atomic write uses same-directory tempfile
- H-4: Integration tests use pytest.raises(SystemExit)
- Item 11: sorted(registry.keys()) iteration in runner.py

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] .gitignore excluded .forge/tools.yaml**
- **Found during:** Task 1
- **Issue:** `.forge/` in .gitignore prevented `git add .forge/tools.yaml`
- **Fix:** Changed `.forge/` to `.forge/*` + `!.forge/tools.yaml` to allow tracking config while ignoring runtime data
- **Files modified:** .gitignore
- **Commit:** d1f04df

**2. [Rule 3 - Blocking] sys.argv leaked pytest arguments into argparse**
- **Found during:** Task 2
- **Issue:** Integration tests failed because `sys.argv` contained pytest flags (`-x -v`) which argparse rejected
- **Fix:** Used `monkeypatch.setattr(sys, "argv", ["forge"])` and `monkeypatch.chdir()` instead of manual os.chdir with try/finally
- **Files modified:** tests/test_integration.py
- **Commit:** 663623a

**3. [Rule 1 - Bug] output_format key mismatch between registry and parsers**
- **Found during:** Task 1
- **Issue:** Registry `_KNOWN_FORMATS` uses keys like `ruff_json`, `semgrep_json` but `PARSER_DISPATCH` uses `sarif`, `grep_line`, `checkpatch_emacs`. tools.yaml must use PARSER_DISPATCH keys since that is what parse_output dispatches on.
- **Fix:** tools.yaml uses PARSER_DISPATCH keys: `shellcheck_json`, `sarif`, `clippy_json`, `checkpatch_emacs`, `grep_line`
- **Files modified:** .forge/tools.yaml
- **Commit:** d1f04df

## Known Stubs

None. All modules are fully implemented with production logic.

## Self-Check: PASSED

All files verified present, all commits verified in git log.
