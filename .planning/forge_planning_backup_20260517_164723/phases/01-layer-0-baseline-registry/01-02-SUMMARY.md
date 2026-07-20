---
phase: 01-layer-0-baseline-registry
plan: 02
subsystem: parsers
tags: [parsers, sarif, toolerror, tdd, finding]
dependency_graph:
  requires: [01-01]
  provides: [parser-dispatch, finding-normalization]
  affects: [01-03, 01-04]
tech_stack:
  added: []
  patterns: [per-tool-parser, shared-sarif-helper, or-pattern-null-guard]
key_files:
  created:
    - src/forge/parsers/shellcheck.py
    - src/forge/parsers/ruff.py
    - src/forge/parsers/semgrep.py
    - src/forge/parsers/clippy.py
    - src/forge/parsers/checkpatch.py
    - src/forge/parsers/non_ascii.py
    - src/forge/parsers/_sarif.py
    - tests/test_parsers.py
    - tests/fixtures/shellcheck_output.json
    - tests/fixtures/ruff_sarif.json
    - tests/fixtures/semgrep_sarif.json
    - tests/fixtures/clippy_output.json
    - tests/fixtures/checkpatch_output.txt
  modified:
    - src/forge/parsers/__init__.py
    - .gitignore
decisions:
  - Shared _parse_sarif helper (DRY) for ruff and semgrep instead of duplicated code
  - PARSER_DISPATCH uses 5 keys mapping to 6 tools (ruff+semgrep share "sarif" key)
  - non_ascii parser treats non-matching grep lines as empty result (not ToolError)
  - checkpatch parser allows summary-only output as valid (not ToolError)
metrics:
  duration: 316s
  completed: 2026-05-16T09:23:02Z
  tasks: 2/2
  tests_added: 30
  tests_total: 124
  files_created: 13
  files_modified: 2
---

# Phase 01 Plan 02: Tool Output Parsers + Dispatch Summary

Six per-tool parsers converting shellcheck JSON, ruff/semgrep SARIF, clippy cargo JSON, checkpatch emacs-mode, and grep -Pn non-ASCII output to normalized Finding objects, with shared SARIF helper (DRY) and ToolError sentinel for corrupt output (Consensus #4)

## What Was Done

### Task 1: Test fixtures and parser tests (TDD RED)

Created 5 fixture files with realistic tool output and 30 test functions covering all six parsers plus the dispatch system. Tests verify:

- Valid output produces correct Finding fields (file, line, rule_id, level, message, tool_name)
- Empty string input returns [] (clean run)
- Malformed/corrupt input returns [ToolError] (Consensus #4 -- crash != clean)
- Clippy empty-spans array is skipped (no IndexError)
- Clippy code=None uses "unknown" fallback rule_id
- exit_code is propagated from runner to ToolError
- PARSER_DISPATCH has exactly 5 keys mapping to correct functions
- parse_output dispatches correctly and raises KeyError on unknown format

Confirmed TDD red phase: all tests failed with ModuleNotFoundError.

### Task 2: Implement all parsers and dispatch (TDD GREEN)

Implemented all six parsers following the plan contract:

1. **shellcheck.py** -- JSON array parsing, SC{code} rule_id format
2. **ruff.py** -- thin wrapper over shared SARIF parser with tool_name="ruff"
3. **semgrep.py** -- thin wrapper over shared SARIF parser with tool_name="semgrep"
4. **_sarif.py** -- shared SARIF 2.1.0 parser (DRY for ruff+semgrep), file:// URI stripping, catches JSONDecodeError/KeyError/TypeError/AttributeError
5. **clippy.py** -- line-by-line cargo JSON, filters by reason+level, guards empty spans and null code
6. **checkpatch.py** -- regex parsing of emacs format, skips summary lines, ToolError on zero-match non-summary input
7. **non_ascii.py** -- regex parsing of grep -Pn output, NON_ASCII rule_id

Updated `__init__.py` with PARSER_DISPATCH dict (5 keys) and parse_output() dispatcher.

All 124 tests pass (30 new + 94 existing).

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Shared _parse_sarif in _sarif.py | DRY -- ruff and semgrep both use SARIF 2.1.0, only tool_name differs |
| 5 dispatch keys for 6 tools | ruff+semgrep share "sarif" key; tool_name parameter distinguishes them |
| non_ascii: no-match = empty list | grep output with no parseable lines means grep found nothing, not corruption |
| checkpatch: summary-only = clean | Summary line without violations is valid tool output, not corruption |
| `or` pattern for JSON null | `(item.get('endLine') or item['line'])` handles both missing keys and null values (Round 3 H-1) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added *.egg-info/ to .gitignore**
- **Found during:** Task 2 post-commit check
- **Issue:** `src/forge.egg-info/` was untracked generated build output
- **Fix:** Added `*.egg-info/` to `.gitignore`
- **Files modified:** `.gitignore`
- **Commit:** included in docs commit

## Verification Results

```
$ python3 -m pytest tests/test_parsers.py -x -v
30 passed in 0.02s

$ python3 -c "from forge.parsers import PARSER_DISPATCH; print(sorted(PARSER_DISPATCH.keys()))"
['checkpatch_emacs', 'clippy_json', 'grep_line', 'sarif', 'shellcheck_json']

$ python3 -c "from forge.parsers.base import ToolError; print('ToolError OK')"
ToolError OK

$ grep -rn "eval(" src/forge/parsers/ --include="*.py"
(no matches -- PASS)

$ grep -rn "exec(" src/forge/parsers/ --include="*.py"
(no matches -- PASS)
```

## TDD Gate Compliance

1. RED gate: `test(01-02)` commit c74a6fd -- tests written, all fail with ImportError
2. GREEN gate: `feat(01-02)` commit dbe52d1 -- parsers implemented, all 30 tests pass
3. REFACTOR gate: not needed -- code was clean from initial implementation

## Self-Check: PASSED

All 14 created files verified present. Both commits (c74a6fd, dbe52d1) verified in git log.
