---
phase: 03-r3-e2e-coverage
plan: 01
subsystem: e2e-check-foundation
tags: [e2e-coverage, layer-1-heuristic, state-finding-source, unidiff-pin]
dependency_graph:
  requires: [Phase-1-state-machine, Phase-2-mutation-source-literal]
  provides: [e2e-check-module, layer-1-heuristic, build-e2e-checker, e2e-check-source]
  affects: [state.py-source-literal, factories.py-runner-builders, pyproject.toml-deps]
tech_stack:
  added: [unidiff-direct-patchset-parsing, signature-change-detection]
  patterns: [DI-callable-builder, dismissed-advisory-finding, deterministic-fingerprint]
key_files:
  added:
    - src/forge/e2e_check.py
  modified:
    - src/forge/state.py
    - src/forge/factories.py
    - pyproject.toml
decisions:
  - StateFinding.source Literal gains "E2E_CHECK" (4th source after L0/L1/MUTANT, D-01)
  - Layer 1 fires only on >=2 source groups AND a signature change; emits at most ONE DISMISSED finding (advisory, never blocks)
  - Fingerprint "e2e-l1:" + sha256(sorted-group-keys + "::" + sorted-sig-files, utf-8)[:16] -- whole concatenation encoded, deterministic
  - Top-level file groups under its own filename (not ""); test dirs {tests, test, spec} excluded from grouping (D-02b)
  - check_layer_2 is a stub returning [] with a stable signature plan 03-02 fills without touching callers
  - build_e2e_checker has no soft-dependency availability check (unidiff is a hard dep); returns run_e2e_check directly, symmetric with build_l2_runner
  - unidiff pinned >=0.7.5,<0.8.0 (upper bound guards against section_header API drift)
status: complete
metrics:
  completed_at: "2026-05-26"
  tasks_completed: 3
  files_modified: 4
  commits: 1
---

# Phase 03 Plan 01: E2E Check Foundation Summary

Created the e2e_check module with the Layer 1 cross-component coverage
heuristic, extended the finding source taxonomy, and pinned unidiff. This is
the foundation that plans 03-02 (Layer 2 components.yaml) and 03-03 (machine
wiring) build on.

## What Was Built

**Objective:** Stand up the e2e coverage check module (Layer 1 heuristic +
Layer 2 stub), add the E2E_CHECK finding source, and pin the unidiff dependency.

**One-liner:** Layer 1 fires a single non-blocking DISMISSED nudge when a diff
spans >=2 source groups and modifies a function signature, with a deterministic
fingerprint and no subprocess/git calls.

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Extend StateFinding.source and create e2e_check.py Layer 1 | 37772ba | state.py, e2e_check.py |
| 2 | Add build_e2e_checker to factories.py | 37772ba | factories.py |
| 3 | Pin unidiff in pyproject.toml | 37772ba | pyproject.toml |

All three tasks landed in a single code commit (37772ba); this summary follows
as a separate docs commit per the phase convention.

### Key Changes

**state.py:**
- `StateFinding.source` Literal widened from `["L0", "L1", "MUTANT"]` to
  `["L0", "L1", "MUTANT", "E2E_CHECK"]`. No other field changes.

**e2e_check.py (new, 253 lines):**
- `detect_signature_changes(diff_text) -> set[str]`: two-arm UNION (added-line
  regex for Python def / return-type / shell func, plus hunk.section_header
  match). Empty/whitespace/unparseable diff returns empty set.
- `group_source_files(files, components=None, exclude_test_dirs=True) ->
  dict[str, list[str]]`: groups by first path segment (top-level file keyed by
  its own name), excludes {tests, test, spec}; with a components dict, assigns
  to first matching glob and falls back to first-segment for unmatched files.
- `check_layer_1(diff_text, components=None) -> list[StateFinding]`: fires only
  when sig_files non-empty AND >=2 groups AND sig_files not disjoint from
  changed files; emits one finding source="E2E_CHECK", disposition=DISMISSED,
  fingerprint "e2e-l1:"+sha256(...)[:16].
- `check_layer_2(diff_text, repo_root, components=None) -> list[StateFinding]`:
  stub returning [] (plan 03-02 fills the body; signature is stable).
- `run_e2e_check(diff_text, repo_root) -> tuple[list[StateFinding], list[str]]`:
  orchestrates Layer 1 + Layer 2; try/except returns ([], [str(e)]) so a
  malformed diff never crashes the review pipeline. No subprocess, no git.

**factories.py:**
- Added `from .e2e_check import run_e2e_check`.
- Added `build_e2e_checker() -> Callable` returning `run_e2e_check` directly
  (no external-binary availability check; unidiff is a hard dep). Exists for
  symmetry with `build_l2_runner` so plan 03-03 injects it the same way.

**pyproject.toml:**
- `unidiff>=0.7.5` -> `unidiff>=0.7.5,<0.8.0` (upper bound guards against
  Hunk.section_header API drift relied on by Arm 2).

## Verification Results

**Step 0 (automated, passed):**
- ruff check: clean on e2e_check.py, state.py, factories.py.
- non-ASCII (Step 0c): clean on tracked diff and untracked e2e_check.py.
- py_compile: all four files parse.

**Review (separate Sonnet 4.6 sub-session, impl != reviewer):**
- Verdict CLEAN -- 8/8 mandate items PASS, 0 findings.
- Verified: fingerprint determinism, {tests,test,spec} exclusion, >=2-group
  guard, DISMISSED finding shape, robustness (empty/parse-error/top-level
  except), Layer 2 stub contract vs plan 03-02, no out-of-scope edits.

**Smoke / regression (main session, PYTHONPATH=src to bind worktree code):**
- Functional probe: empty diff -> ([], []); 2-group + signature diff -> exactly
  one E2E_CHECK/DISMISSED finding (fp e2e-l1:cecd62e610ee388b); same diff ->
  same fingerprint (deterministic); single-group diff -> no finding (no
  over-fire); malformed diff -> no crash (0 findings, 0 errors).
- Regression: 640 passed, 0 failures (3 pre-existing PytestUnknownMarkWarning
  on tests/test_lock_signals.py for an unregistered pytest.mark.integration --
  not touched by this plan).

**Scope boundary:**
- machine.py untouched (Layer wiring is plan 03-03). No out-of-scope files
  modified. files_modified matches the plan frontmatter exactly (4 files).

## Notes

- Implementation was found already present (uncommitted) in the worktree at
  resume; provenance was not verifiable from git history. The user chose
  keep+review over redo after Step 0 came back clean. The separate-reviewer
  CLEAN verdict plus the smoke/regression evidence above are the gate, so
  provenance does not affect what ships.
- Layer 2 (components.yaml) and the machine wiring remain stubs/absent by
  design; plans 03-02 and 03-03 complete them. The strict dependency chain
  (03-02 -> 03-03 -> 03-04 -> 03-05) means plans execute one at a time.
