---
phase: 02-dimension-gap-closure
plan: 01
subsystem: cli
tags: [complexity, step-0b, radon, deterministic, DIM-03]
dependency_graph:
  requires: []
  provides: [step-0b-complexity-check, python-cc-analysis, shell-function-length]
  affects: [cli/forge_cli.py, cli/config.json]
tech_stack:
  added: [radon (optional, graceful degradation)]
  patterns: [chained-config-access, 4-tuple-finding-format, heredoc-aware-parsing]
key_files:
  created: []
  modified: [cli/forge_cli.py, cli/config.json]
decisions:
  - "Radon imported inside function body (not module level) for graceful ImportError skip"
  - "Shell function detection uses two regex patterns to cover both bash syntaxes"
  - "Heredoc content skipped during brace-depth counting to prevent false positives"
  - "Config loaded once before loop to avoid redundant file reads"
metrics:
  duration: 3m
  completed: "2026-05-12T16:37:36Z"
  tasks_completed: 1
  tasks_total: 1
  files_modified: 2
---

# Phase 02 Plan 01: Step 0b Complexity Checks Summary

Deterministic cyclomatic complexity and function length checks integrated into forge dry-run pipeline, using radon CC for Python and brace-depth line counting for shell scripts.

## What Was Done

### Task 1: Add Step 0b complexity functions and integrate into run_dry_run()

**Commit:** `42631aa`

Added two new functions before `run_dry_run()` in `cli/forge_cli.py`:

1. `_check_python_complexity(filepath, threshold, findings_list)` -- Uses radon `cc_visit` to compute cyclomatic complexity per function. Reports findings for CC >= threshold (default 15). Gracefully skips if radon is not installed (ImportError caught inside function body).

2. `_check_shell_function_length(filepath, threshold, findings_list)` -- Counts lines per shell function using brace-depth tracking. Reports findings for functions exceeding threshold (default 80 lines). Handles both `name() {` and `function name {` syntaxes (R4 fix). Skips heredoc content during brace counting (R13 fix).

Integrated Step 0b as a separate `if/elif` block in `run_dry_run()`, placed between Step 0a (syntax) and Step 0c (non-ASCII), at the same indentation level (R9 fix). Config loaded once before the per-file loop (R10 fix).

Added `"complexity"` section to `cli/config.json` with `python_cc_threshold: 15` and `shell_line_threshold: 80`.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Radon imported inside function body | Avoids module-level ImportError; matches project pattern for optional tools (shellcheck, ruff) |
| Two regex patterns for shell functions | Bash supports both `name() {` and `function name {` -- must detect both to avoid false negatives |
| Heredoc-aware line skipping | Heredoc bodies can contain `{`/`}` characters that would corrupt brace-depth tracking |
| Config hoisted outside loop | Avoids redundant JSON file reads on each iteration |

## Verification Results

| Check | Result |
|-------|--------|
| `python3 -m py_compile cli/forge_cli.py` | PASS |
| `python3 -c "import json; ..."` config validation | PASS |
| Step 0b function defs (2) + calls (2) + comments (2) | 6 matches |
| R4 fix: `func_paren` + `func_keyword` patterns | 4 matches |
| R13 fix: `heredoc_delim` usage | 15 matches |
| Step ordering: 0a < 0b < 0c by line number | PASS (1223, 1290, 1306) |
| Non-ASCII check on new code | PASS |
| No accidental file deletions | PASS |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functions are fully implemented with real logic.
