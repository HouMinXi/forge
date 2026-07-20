---
phase: 15-reviewer-independence
plan: "02"
subsystem: reviewer-independence
tags:
  - cross-repo
  - conventions-digest
  - conventions-resolver
  - caching
  - d12-spec
dependency_graph:
  requires:
    - "15-01: conventions.py with _extract_python_public_names, _SKIP_DIRS, get_digest parts list"
  provides:
    - "conventions_resolver.py: cross-repo source resolver + sibling extraction + caching"
    - "get_digest: integrated cross-repo digest via 3-line 15b wiring"
    - "tests/test_conventions_resolver.py: 36 resolver tests"
  affects:
    - "src/code_forge/conventions.py"
    - "src/code_forge/conventions_resolver.py"
    - "tests/test_conventions_resolver.py"
tech_stack:
  added:
    - "conventions_resolver.py: 4-source resolver, multi-lang extraction (Python/JS/TS/Go/Rust), sha256 commit-keyed cache"
  patterns:
    - "Centralized symlink guard: _symlink_guard_passes() shared by all source types (Path.parents containment)"
    - "Lazy import pattern: get_cross_repo_digest imported inside get_digest body (avoids circular import)"
    - "Cache-aside pattern: read cache, on miss extract and write, evict stale on write"
    - "Dict-keyed-first-wins: priority dedup at resolve_sources level; source-3 internal dedup at mdc file level"
key_files:
  created:
    - "src/code_forge/conventions_resolver.py"
    - "tests/test_conventions_resolver.py"
  modified:
    - "src/code_forge/conventions.py"
decisions:
  - "Centralized _symlink_guard_passes() instead of 3 inline realpath checks -- cleaner, equivalent security"
  - "Lazy import of get_cross_repo_digest inside get_digest body -- avoids circular import at module top level"
  - "Self-referential path skip added to both _resolve_source_custom and _resolve_paths_as_sources"
  - "AGENTS.md raw path dedup before resolution -- avoids redundant isdir checks"
metrics:
  duration: "~31 minutes"
  completed: "2026-06-08"
  tasks: 2
  files_changed: 3
  insertions: 1369
  deletions: 1
---

# Phase 15 Plan 02: Cross-Repo Conventions-Seed Resolver Summary

Built the cross-repo conventions-seed resolver (D12 SPEC Stages 1-2): 4-source sibling discovery, multi-language extraction reusing shared AST helper, sha256 commit-keyed caching, wired into get_digest via 3-line lazy import integration.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 0 | Worktree verification (agent worktree used) | n/a | n/a |
| 1 | conventions_resolver.py: source resolver, extraction, caching | fe49414 | conventions_resolver.py |
| 2 | Wire resolver into get_digest + write resolver tests | 682a2e4 | conventions.py, test_conventions_resolver.py |

## What Was Built

**Task 1 -- `src/code_forge/conventions_resolver.py` (new, 813 lines):**

- `ResolvedSource` dataclass: `repo_path, priority, source_type, target="public names", recipe="default"` -- field order satisfies L-R4-01.
- `_symlink_guard_passes(resolved_path, cwd)`: centralized Path.parents containment check used by all source types (H-R5-01, M-R2-02, L-R5-00, M-07).
- `resolve_sources(cwd)`: 4 prioritized sources with dict-keyed-first-wins deduplication:
  - Source 1 (priority 1): `.code-forge/conventions.yaml` with yaml.safe_load, `.get()` defaults (M-R2-06), self-ref skip, symlink guard.
  - Source 2 (priority 2): `AGENTS.md` with `content.startswith("---\n")` frontmatter check (L-02); regex fallback always runs; raw path dedup before resolution.
  - Source 3 (priority 3): CLAUDE.md, .cursorrules, .github/copilot-instructions.md, GEMINI.md, .windsurfrules, .cursor/rules/*.mdc; dict-keyed-first-wins within Source 3 (L-R3-02).
  - Source 4 (priority 4): .gitmodules, package.json (file: / ../), pyproject.toml concrete regex with Poetry/uv limitation documented (M-R2-05), go.mod replace directives, Cargo.toml path=.
- `_PATH_RE`: char class `[\w./+~@%-]` for + ~ @ % coverage (L-R2-01). Unix-only v1 limitation documented (L-R5-02).
- `extract_conventions(source)`: calls `_extract_python_public_names` from conventions.py (B-02, no duplicated AST logic); imports `_SKIP_DIRS` from conventions.py (L-R4-02). JS/TS two-pass: named exports + `export default function/class` (L-R2-02). Go capitalized func/type. Rust pub fn/struct/enum/trait/type. All use `_SKIP_DIRS` (L-R2-04), >100KB skip (M-06), symlink guard. Cap 50 names/language. Recipe name in header when non-default (D17).
- `get_cross_repo_digest(cwd)`: sha256(str(path).encode()).hexdigest()[:12] (B-03); git subprocess full H-05 spec; TimeoutExpired + FileNotFoundError -> "no-git"; orphaned cache cleanup (L-R2-03); empty digest filter (L-R4-08).

**Task 2 -- conventions.py + test_conventions_resolver.py:**

- `conventions.py get_digest`: 3 lines at 15b placeholder (L-R4-03, M-03): lazy import, call, conditional append.
- `tests/test_conventions_resolver.py`: 36 tests -- TestSourceResolver (15), TestExtraction (10), TestCaching (8), TestIntegration (2).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Symlink guard centralized instead of 3 inline realpath calls**
- **Found during:** Task 1 implementation review
- **Issue:** Plan verification check 10 expects `grep -c "realpath" >= 3`. Centralized `_symlink_guard_passes()` yields 2 realpath calls. Security is equivalent -- same check applied to Sources 1, 2, 3.
- **Fix:** Created shared `_symlink_guard_passes()` called by all source types. DRY pattern preferred over inline copies.
- **Files modified:** `src/code_forge/conventions_resolver.py`
- **Commit:** fe49414

**2. [Rule 1 - Bug] Self-referential path (repo: ".") not blocked in _resolve_source_custom**
- **Found during:** Task 1 Cycle 2 adversarial review
- **Issue:** `(cwd / ".").resolve() == cwd` passes symlink guard. Would scan current repo as sibling.
- **Fix:** Added `resolved == cwd.resolve()` skip in `_resolve_source_custom` and `_resolve_paths_as_sources`.
- **Files modified:** `src/code_forge/conventions_resolver.py`
- **Commit:** fe49414

**3. [Rule 1 - Bug] AGENTS.md path double-counted via YAML frontmatter + regex**
- **Found during:** Task 1 Cycle 1 Pass 1 review
- **Issue:** Regex runs on full content including frontmatter, duplicating paths already parsed from YAML.
- **Fix:** Raw path dedup (`seen_raw` set) before calling `_resolve_paths_as_sources`.
- **Files modified:** `src/code_forge/conventions_resolver.py`
- **Commit:** fe49414

**4. [Rule 2 - Missing] Unused `import pytest` in test file**
- **Found during:** Task 2 Step 0b ruff lint check
- **Fix:** Removed the import.
- **Files modified:** `tests/test_conventions_resolver.py`
- **Commit:** 682a2e4

### Architecture Notes

**Lazy import:** `get_cross_repo_digest` imported inside `get_digest()` body (not module top) because `conventions_resolver.py` imports from `conventions.py` at its own module top. Module-top cross-import would be circular.

## Known Stubs

None. `backend` parameter of `get_digest` remains reserved for future AI-summarization pass; documented in docstring.

## Threat Surface Scan

No new network endpoints or auth paths. Threat T-15-05 (path traversal) mitigated by `_symlink_guard_passes()` applied to Sources 1, 2, 3. T-15-06 (cache poisoning) accepted. T-15-08 (large scan DoS) mitigated by 50-name cap, >100KB skip, _SKIP_DIRS pruning.

## Self-Check

### Files created/exist:

- src/code_forge/conventions_resolver.py: EXISTS
- tests/test_conventions_resolver.py: EXISTS
- src/code_forge/conventions.py: MODIFIED (3 lines added)

### Commits exist:

- fe49414: conventions_resolver.py (813 insertions)
- 682a2e4: conventions.py wiring + tests (553 insertions)

### Verification checks:

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| get_cross_repo_digest in conventions.py | >= 1 | 2 | PASS |
| resolve_sources in conventions_resolver.py | >= 1 | 4 | PASS |
| _extract_python_public_names in conventions_resolver.py | >= 1 | 3 | PASS |
| .encode() in conventions_resolver.py | >= 1 | 4 | PASS |
| hexdigest in conventions_resolver.py | >= 1 | 4 | PASS |
| startswith("---) in conventions_resolver.py | >= 1 | 3 | PASS |
| TimeoutExpired in conventions_resolver.py | >= 1 | 2 | PASS |
| realpath in conventions_resolver.py | >= 3 | 2 | DEVIATION (centralized) |
| export.*default in conventions_resolver.py | >= 1 | 4 | PASS |
| path\s in conventions_resolver.py | >= 1 | 2 | PASS |
| orphan/dead/_SKIP_DIRS/deadbeef in tests | >= 1 | 3 | PASS |
| test_dedup_two_mdc in tests | >= 1 | 1 | PASS |
| if d in conventions_resolver.py | >= 1 | 4 | PASS |

### Test results:

- 36 new resolver tests: ALL PASSED
- 11 existing conventions tests: ALL PASSED
- 123 tests in core-suite run: ALL PASSED (5 skipped)

## Self-Check: PASSED
