---
phase: 21-legacy-intent
plan: 01
subsystem: git-blame
tags: [git, blame, porcelain, parser, advisory]
dependency_graph:
  requires: []
  provides: [git_blame]
  affects: [legacy.py, machine.py]
tech_stack:
  added: []
  patterns: [sha-cache-dedup, tab-prefix-first-parsing, advisory-graceful-degradation]
key_files:
  created: []
  modified:
    - src/code_forge/git.py
    - tests/test_git.py
decisions:
  - "D-06 enforced: git_blame() in git.py, single owner of git subprocess calls"
  - "Advisory axis graceful degradation: return {} instead of raising on missing git"
  - "Tab-prefix check before SHA header detection prevents false match on source lines"
  - "Hex-only validation (not isalnum) for SHA detection -- rejects G-Z"
metrics:
  duration: 13m 37s
  completed: 2026-06-13T08:45:16Z
  tasks: 2/2
  files_modified: 2
---

# Phase 21 Plan 01: git_blame Porcelain Parser Summary

git blame --porcelain parser in git.py with SHA dedup cache, tab-first parse order, and advisory-axis graceful degradation

## Tasks Completed

| Task | Name | Type | Commit | Key Changes |
|------|------|------|--------|-------------|
| 1 | git_blame -- RED (failing tests) | test | c0998e0 | 6 test functions in tests/test_git.py covering simple parse, SHA dedup, staged lines, non-zero exit, missing file, callable check |
| 2 | git_blame -- GREEN (implementation) | feat | f3c9216 | git_blame() added to git.py; module docstring updated to "diff, blame"; test fixture SHA corrected from 41 to 40 chars |

## Implementation Details

### git_blame(file_path, repo_root) -> dict[int, dict]

- Returns `{line_number: {"sha": str, "author": str, "subject": str}}` (1-indexed)
- Parses `git blame --porcelain` output with SHA deduplication via sha_cache
- Parse order: (1) tab-prefix content line, (2) empty-line guard, (3) SHA header detection
- SHA validation uses hex-only character set (not isalnum -- rejects G-Z)
- Uses `--` separator before file_path to prevent flag injection (T-21-01)
- Returns `{}` on: non-zero exit, timeout (60s), OSError, missing git binary, empty stdout
- Advisory axis divergence: returns `{}` instead of raising RuntimeError (unlike other git.py functions)

### Test Coverage (6 tests)

- `test_git_blame_parses_simple`: single commit block with full metadata
- `test_git_blame_dedup_sha`: two lines same SHA, second block has no author/summary
- `test_git_blame_staged_line`: SHA 0*40, author "Not Committed Yet", subject defaults to ""
- `test_git_blame_returns_empty_on_nonzero`: returncode=128 -> {}
- `test_git_blame_returns_empty_for_missing_file`: missing path -> {}
- `test_git_blame_exists`: callable import check (D-06 enforcement)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test fixture SHA was 41 chars, not 40**
- **Found during:** Task 2 (GREEN)
- **Issue:** The test fixture SHA `abc1234567890abcdef1234567890abcdef123456` was 41 characters, causing the SHA header regex to fail (len check requires exactly 40). All tests failed in GREEN because the parser never matched the SHA header.
- **Fix:** Replaced with `aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee` (exactly 40 hex chars)
- **Files modified:** tests/test_git.py
- **Commit:** f3c9216 (included in Task 2 commit)

## TDD Gate Compliance

- RED gate (c0998e0): `test(21-01)` commit -- all 6 tests fail with ImportError (git_blame not yet defined)
- GREEN gate (f3c9216): `feat(21-01)` commit -- all 6 tests pass after implementation
- REFACTOR gate: not needed -- implementation is clean, no refactoring required

## Verification Results

- `pytest tests/test_git.py -k "git_blame" -x -q`: 6 passed
- `pytest tests/test_git.py -x -q`: 37 passed (no regressions in existing tests)
- `pytest -x -q` (full suite): 1565 passed, 1 failed (pre-existing test_semgrep_validate failure, unrelated)
- `grep -n "def git_blame" src/code_forge/git.py`: line 358
- `grep -n "git subprocess calls" src/code_forge/git.py`: line 5 ("diff, blame")
- Non-ASCII check: clean (both files)

## Known Stubs

None -- git_blame() is fully implemented with no placeholder logic.

## Self-Check: PASSED

- [x] src/code_forge/git.py exists
- [x] tests/test_git.py exists
- [x] Commit c0998e0 exists (RED)
- [x] Commit f3c9216 exists (GREEN)
- [x] git_blame function defined in git.py
- [x] Module docstring updated to include "blame"
