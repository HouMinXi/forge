---
phase: 15-reviewer-independence
plan: "01"
subsystem: reviewer-independence
tags:
  - outlet-c
  - reviewer-independence
  - conventions-digest
  - test-assertion-gate
  - human-backstop
dependency_graph:
  requires:
    - "14: Phase 14 receipt/verify gate (D8)"
  provides:
    - "C-leg llm_invoke-based spawn_fn (SC1-SC3)"
    - "conventions-digest slot D11 (conventions.py)"
    - "test-assertion review gate SC4 (D14)"
    - "human backstop checklist D13 (SKILL.md Step 8)"
  affects:
    - "src/code_forge/cli.py"
    - "src/code_forge/factories.py"
    - "src/code_forge/conventions.py"
    - "src/code_forge/skills/code-forge/SKILL.md"
tech_stack:
  added:
    - "conventions.py: AST naming extractor with _SKIP_DIRS pruning, Path.parents symlink guard"
  patterns:
    - "Factory pattern: _make_subagent_spawn returns spawn_fn closure"
    - "Fail-open advisory gate: _run_test_assertion_review wraps llm_invoke in try/except"
    - "Shared AST helper: _extract_python_public_names single source of truth"
key_files:
  created:
    - "src/code_forge/conventions.py"
    - "tests/test_conventions.py"
  modified:
    - "src/code_forge/cli.py"
    - "src/code_forge/factories.py"
    - "src/code_forge/skills/code-forge/SKILL.md"
    - "tests/test_outlet_c.py"
    - "tests/test_cli_integration.py"
decisions:
  - "C-leg uses llm_invoke per pass (not Agent tool) -- avoids 65K truncation and hang failures"
  - "Backend resolution moved above subagent dispatch so backend is in scope for _make_subagent_spawn"
  - "test-assertion gate is advisory-only (D8 exception): not recorded in receipts, fail-open"
  - "Post-image assembly duplicated on A and C legs (shared data, not shared code) per D9"
  - "_extract_python_public_names uses Path.parents not str.startswith for symlink safety"
  - "Worktree was 2 commits behind main (missing Phase 14 files); merged before proceeding"
metrics:
  duration: "~7 hours"
  completed: "2026-06-08"
  tasks: 2
  files_changed: 7
  insertions: 1140
  deletions: 60
---

# Phase 15 Plan 01: C-Leg Independence + Conventions Digest + Test-Assertion Gate Summary

Wired Outlet C reviewer independence (SC1-SC3) via llm_invoke-based spawn_fn replacing NotImplementedError ceiling; added conventions-digest slot (D11) with shared AST extractor; wired test-assertion review gate (SC4/D14) on both outlet paths; added human backstop checklist (D13) to SKILL.md.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 0 | Worktree setup (deviation: executed in agent worktree) | n/a | n/a |
| 1 | conventions.py + _make_subagent_spawn + post-image + factories | 24c1fd4 + d7e3b89 | conventions.py, cli.py, factories.py |
| 2 | Test-assertion gate + human backstop + SC1-SC4 tests | 7455876 | cli.py, SKILL.md, test_outlet_c.py, test_conventions.py, test_cli_integration.py |

## What Was Built

**Task 1:**

- `src/code_forge/conventions.py` (new): D11/D12 15a module with `_extract_python_public_names` shared AST helper, `get_same_repo_digest`, and `get_digest`. Implements `_SKIP_DIRS` pruning via `os.walk` (not `rglob`), `Path.parents` symlink containment guard (not `str.startswith`), 100KB file size cap, `ast.AsyncFunctionDef` support, `tree.body`-only traversal.

- `src/code_forge/cli.py`: `_make_subagent_spawn` module-level factory replaces the NotImplementedError closure. Backend resolution block moved above the subagent dispatch so `backend` is in scope. Post-image assembly with stat-pre-check (M-R4-01), 50KB cap, binary detection (M-R2-01), character-level truncation note (L-R4-06). Conventions digest wired on both A-leg and C-leg. `_run_test_assertion_review` module-level function (SC4/D14): precise test file heuristic (M-02), llm_invoke inside try block (H-R3-01), fail-open on any exception.

- `src/code_forge/factories.py`: `build_l1_provider` updated with `conventions_digest` and `post_image` optional params (D11, backward-compatible defaults). Post-image section before diff in prompt; conventions digest section before diff.

**Task 2:**

- `src/code_forge/skills/code-forge/SKILL.md`: `[Step 8] Human Backstop` added to pipeline ASCII art. New `# Step 8: Human Backstop (D13)` section with 6-item checklist and skip policy (D13, M-R2-04, L-R3-01).

- `tests/test_outlet_c.py`: `TestIndependence` (SC1: 9 llm_invoke calls for 9 passes; no role leak between consecutive prompts), `TestCriteriaPayload` (SC2: diff+role in every prompt, no session context), `TestContextIsolation` (SC3: previous pass findings absent from next pass prompt).

- `tests/test_conventions.py` (new): D11 slot tests covering all acceptance criteria -- async functions (M-04), top-level only (M-05), large file skip (M-06), `_SKIP_DIRS` pruning with dirs inside src/ (L-R2-04, M-R3-01), symlink prefix-collision rejection via `Path.parents` (H-R5-01, M-R5-01).

- `tests/test_cli_integration.py`: `TestAssertionGate` (SC4: gate runs on test files, skips non-test files, skips contest.py M-02, prompt independent, fail-open on error H-R3-01), `TestSubagentSpawnIntegration` (H-02: actual `_make_subagent_spawn`, prompt capture via `call_args[0][0]` M-R5-03), `TestBuildL1ProviderDigestAndPostImage` (M-R2-03: A-leg digest and post-image threading).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree 2 commits behind main**
- **Found during:** Task 1 verification
- **Issue:** Agent worktree was created from `f551960` and was missing Phase 14 deliverables: `reviewer_json.py`, `outlet_c.py`, updated `receipt.py`, `verify.py`, and associated tests.
- **Fix:** Committed WIP changes, then merged `main` (at `b030bd4`) into the worktree branch. Resolved merge conflicts in `cli.py` (keeping Phase 15 subagent dispatch) and `factories.py` (merging Phase 15 params with main's excerpt/reviewer_json structure).
- **Commits:** `24c1fd4` (WIP save), `d7e3b89` (merge)

**2. [Rule 1 - Bug] Test mock `fake_build_l1_provider` rejected new keyword args**
- **Found during:** Task 1 test run after merge
- **Issue:** Existing test mock had `fake_build_l1_provider(engine, resolved, backend=None)` -- rejected `conventions_digest` and `post_image` kwargs.
- **Fix:** Added `**kwargs` to the mock signature.
- **Commit:** `d7e3b89`

**3. [Rule 1 - Bug] Phase 15 test section used LLMResult/Usage without explicit alias import**
- **Found during:** Task 2 test run
- **Issue:** New test classes referenced `LLMResult`/`Usage` which were not in scope at the bottom of `test_cli_integration.py`.
- **Fix:** Added `from code_forge.llm_invoke import LLMResult as _LLMResult, Usage as _Usage` in new section; used aliased names throughout.
- **Commit:** `7455876`

**4. [Deviation - Plan Phase 0] Agent worktree used instead of .worktrees/p15**
- **Issue:** Plan says to create `.worktrees/p15` but GSD executor agents run in their own isolated worktree. Creating a nested worktree is unnecessary.
- **Fix:** Executed all file changes directly in the agent worktree (correct GSD protocol).

## Known Stubs

None. The `backend` parameter of `get_digest` is reserved for 15b (cross-repo AI-summarization) and intentionally unused in 15a; documented in the docstring.

## Threat Surface Scan

No new network endpoints, auth paths, file-write paths, or schema changes. The conventions digest reads local .py files via AST (no execution). The test-assertion gate sends diff text to `llm_invoke` -- same trust boundary as existing A/C-leg review passes. T-15-05 (symlink traversal) mitigated by `Path.parents` containment check, verified by `test_symlink_prefix_collision_rejected`.

## Self-Check

### Files created/exist:
- src/code_forge/conventions.py: EXISTS
- tests/test_conventions.py: EXISTS

### Commits exist:
- 24c1fd4: WIP save (Task 1 changes)
- d7e3b89: merge + conflict resolution
- 7455876: Task 2 tests + SKILL.md + test-assertion gate

### Verification checks (all passed):
- NotImplementedError count in cli.py: 0
- Human Backstop in SKILL.md: 2 occurrences
- Step 8 in SKILL.md: 2 occurrences
- get_digest in conventions.py: 2 occurrences
- _extract_python_public_names in conventions.py: 3 occurrences
- _SKIP_DIRS in conventions.py: 4 occurrences
- Post-Image in cli.py: 1 occurrence
- Post-Image in factories.py: 1 occurrence
- _make_subagent_spawn in cli.py: 2 occurrences
- _run_test_assertion_review in cli.py: 5 occurrences
- 50*1024 / _POST_IMAGE_FILE_CAP in cli.py: 6 occurrences
- _raw[:1024] binary detection: 1 occurrence
- test_build_l1_provider_includes: 2 occurrences
- .stat() in cli.py: 2 occurrences
- Optional[...] type annotation: 4 occurrences
- .parents in conventions.py: 3 occurrences
- prefix_collision/repo_evil in test_conventions.py: 6 occurrences
- call_args[0][0] in test_cli_integration.py: 11 occurrences
- test_outlet_c_calls_spawn_fn_per_pass: 1 occurrence

### Test results:
- 35 Phase 15 tests: ALL PASSED
- 292 tests in comprehensive run: ALL PASSED (5 skipped)

## Self-Check: PASSED
