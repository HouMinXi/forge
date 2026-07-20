---
phase: 01-layer-0-baseline-registry
plan: 01
subsystem: forge-core
tags: [scaffold, dataclass, registry, git-wrapper, diff-parser]
dependency_graph:
  requires: []
  provides: [Finding, ToolError, EXIT_PASS, EXIT_FAIL, load_registry, ToolConfig, match_tools, extract_changed_lines, get_changed_files, run_git_diff, validate_diff_spec]
  affects: [parsers, runner, delta, verdict, state]
tech_stack:
  added: [pyyaml, unidiff, pytest]
  patterns: [frozen-dataclass, allowlist-validation, tdd-red-green]
key_files:
  created:
    - pyproject.toml
    - src/forge/__init__.py
    - src/forge/parsers/__init__.py
    - src/forge/parsers/base.py
    - src/forge/registry.py
    - src/forge/git.py
    - src/forge/diff.py
    - tests/__init__.py
    - tests/test_registry.py
    - tests/test_git.py
    - tests/test_diff.py
  modified:
    - .gitignore
decisions:
  - "yaml.safe_load exclusively -- yaml.load with unsafe loader never used (T-01-01)"
  - "shell=True never used in subprocess calls (T-01-04)"
  - "validate_diff_spec uses allowlist regex, not blocklist (stronger security model)"
  - "ToolError is NOT a Finding -- isinstance check distinguishes crash from clean (Consensus #4)"
  - "git diff exit 1 is normal (has diff), not error (Mimo F-03)"
metrics:
  duration: "8m 27s"
  completed: "2026-05-16T08:19:51Z"
  tasks: 3
  files: 12
---

# Phase 01 Plan 01: Project Scaffold and Foundation Modules Summary

YAML-driven tool registry with frozen Finding/ToolError types, allowlist-validated git wrapper, and unidiff-based diff parser -- all with TDD test coverage (52 tests).

## Tasks Completed

| Task | Name | Commit(s) | Key Files |
|------|------|-----------|-----------|
| 1 | Project scaffold, Finding, ToolError, exit codes | 7740431 | pyproject.toml, src/forge/__init__.py, src/forge/parsers/base.py |
| 2 | Registry loader + git wrapper (TDD) | e89b75e (RED), 7a9d2fe (GREEN) | src/forge/registry.py, src/forge/git.py, tests/test_registry.py, tests/test_git.py |
| 3 | Diff parser (TDD) | 1d382a3 (RED), 1ff76bf (GREEN) | src/forge/diff.py, tests/test_diff.py |

## TDD Gate Compliance

Task 2: test(01-01) at e89b75e (RED) -> feat(01-01) at 7a9d2fe (GREEN). Compliant.
Task 3: test(01-01) at 1d382a3 (RED) -> feat(01-01) at 1ff76bf (GREEN). Compliant.

## Verification Results

- 52 tests pass (pytest): 10 registry, 18 git, 12 diff, 12 base type checks
- No `yaml.load` (unsafe) in src/forge/ -- only yaml.safe_load
- No `shell=True` in src/forge/ -- args as list exclusively
- R7-L5 escapes confirmed: regex contains literal `\^` and `\-`
- All imports succeed: Finding, ToolError, EXIT_PASS, EXIT_FAIL, load_registry, ToolConfig, match_tools, extract_changed_lines, get_changed_files, run_git_diff, validate_diff_spec
- Finding frozen (immutable), 9 fields, to_dict() returns all keys
- ToolError frozen, 4 fields, to_dict() returns plain dict (Round 3 C-3)
- ToolConfig has enabled field (default True), load_registry filters disabled (Round 3 C-4)
- validate_diff_spec rejects --evil-flag, accepts HEAD, --staged, HEAD^, abc-def; rejects HEAD@{u}

## Decisions Made

1. **yaml.safe_load exclusively** -- yaml.load with unsafe loader never used. Prevents arbitrary Python object instantiation from user-editable .forge/tools.yaml (T-01-01).
2. **Allowlist regex for diff-spec validation** -- stronger than blocklist. Only permits known-safe characters. Explicitly escaped `\^` and `\-` per R7-L5 to prevent silent breakage on character class reordering.
3. **shell=True never used** -- subprocess args always passed as list. Prevents shell injection (T-01-04).
4. **ToolError is NOT a Finding** -- downstream code uses isinstance to distinguish tool crashes from clean runs. Prevents false PASS on tool failure (Consensus #4).
5. **git diff exit 1 is normal** -- exit 1 means "differences found", not error. Only exit 128+ is fatal. Prevents false RuntimeError on normal diffs (Mimo F-03).
6. **Line-number drift documented as N/A** -- both tools and diff reference working tree file state. No drift possible (Consensus #2).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added .venv/ to .gitignore**
- **Found during:** Task 1
- **Issue:** pip install required venv creation (system pip blocked by permission); .venv/ directory would be untracked
- **Fix:** Added `.venv/` to .gitignore before any commits
- **Files modified:** .gitignore
- **Commit:** 7740431

No other deviations. Plan executed as written.

## Known Stubs

None. All modules are fully functional with no placeholder data.

## Self-Check: PASSED

All 11 created files verified on disk. All 5 commits verified in git log.
