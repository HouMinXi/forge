---
phase: 11-subagent-dispatch-review-outlet
plan: "01"
subsystem: llm_invoke
tags: [bug-fix, no-pin, D-26, BACKEND-02, tdd]
dependency_graph:
  requires: []
  provides: [no-pin-contract]
  affects: [llm_invoke, DEFAULT_BACKEND, cli-subprocess]
tech_stack:
  added: []
  patterns: [conditional-list-build, ternary-shell-part]
key_files:
  modified:
    - src/code_forge/llm_invoke.py
    - tests/test_llm_invoke.py
decisions:
  - "_resolve_model() returns empty string (not DEFAULT_MODEL) when FORGE_LLM_MODEL unset -- session model no-pin (D-26)"
  - "DEFAULT_MODEL constant retained for backward-compat external importers with clarifying comment"
  - "Conditional --model uses Python list extend pattern for normal path; ternary string part for large-prompt shell path"
metrics:
  duration: "~25 minutes"
  completed: "2026-06-03"
  tasks_completed: 2
  files_changed: 2
---

# Phase 11 Plan 01: llm_invoke --model-pin No-Pin Contract Fix Summary

Conditional omission of `--model` in `_invoke_cli` when `effective_model` resolves to empty string, satisfying D-26 session-default no-pin contract.

## What Was Built

Fixed `_resolve_model()` and `_invoke_cli()` in `src/code_forge/llm_invoke.py` so that
when `DEFAULT_BACKEND` (`model=""`) is used with no `FORGE_LLM_MODEL` env var set, the
`claude -p` subprocess is invoked WITHOUT `--model`, allowing the user's active session
model to run instead of pinning `claude-sonnet-4-6`.

## TDD Gate Compliance

- RED commit `17e4c5b`: 4 failing tests proving the bug (test-only changes, `# wip`)
- GREEN commit `946479c`: implementation fix making all 4 tests pass (`# post-review-c3`)
- REFACTOR: not needed (code is already clean after 3-cycle review)

## Tasks

### Task 1: Failing tests (RED)

Added 4 tests to `tests/test_llm_invoke.py::TestLLMInvoke`:

- `test_cli_omits_model_flag_when_empty` -- proves the bug: `--model` appeared in cmd
- `test_cli_passes_model_flag_when_backend_has_model` -- existing behavior preserved
- `test_cli_passes_model_flag_when_forge_env_set` -- env override preserved
- `test_large_prompt_omits_model_when_empty` -- large-prompt shell path coverage

All 4 failed on the unfixed code (RED confirmed).

### Task 2: Implementation fix (GREEN)

Two changes in `src/code_forge/llm_invoke.py`:

1. `_resolve_model()`: `os.environ.get("FORGE_LLM_MODEL", DEFAULT_MODEL)` changed to
   `os.environ.get("FORGE_LLM_MODEL", "")` -- returns empty string when unset

2. `_invoke_cli()` normal path: list built conditionally -- `--model` added only when
   `effective_model` is non-empty

3. `_invoke_cli()` large-prompt shell path: `model_part` ternary -- `" --model X"` or `""`

All 34 tests in `test_llm_invoke.py` pass. 133 tests across key test files pass.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Unused `import time` in test file**
- Found during: Step 0b ruff lint check
- Issue: pre-existing unused import in original test file
- Fix: Removed the import line
- Committed: included in GREEN phase commit `946479c`

**2. [Rule 2 - Clarity] Added comment to DEFAULT_MODEL constant**
- Found during: Cycle 1 Pass 2 review -- `DEFAULT_MODEL` became dead code after fix
- Fix: 3-line comment explaining backward-compat retention and D-26 no-pin rationale
- Committed: included in GREEN phase commit `946479c`

## Three-Cycle Review Summary

| Cycle | Pass 1 (qodo) | Pass 2 (expert) | Pass 3 (adversarial) | Findings |
|-------|---------------|-----------------|----------------------|----------|
| 1 | None | LOW: DEFAULT_MODEL dead | None | 1 LOW (auto-fixed) |
| 2 | None | None | None | 0 |
| 3 | None | None | None | 0 |

Cycle counter resets: 0. Step 4 smoke test: 34/34 PASS.

## Commits

| Hash | Message |
|------|---------|
| `17e4c5b` | llm_invoke/no-pin: add failing tests for --model omission contract (RED) |
| `946479c` | llm_invoke/no-pin: conditionally omit --model when effective_model is empty (GREEN) |
| `649b570` | Merge p11-01-exec: fix --model-pin no-pin contract in llm_invoke |

## Known Stubs

None. All behavior is fully implemented and verified by tests.

## Threat Flags

None. Changes are internal to subprocess arg construction. No new network endpoints,
auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

- `src/code_forge/llm_invoke.py` line 74: `return os.environ.get("FORGE_LLM_MODEL", "")`
- `src/code_forge/llm_invoke.py` lines 194, 202: `if effective_model` guards both paths
- `tests/test_llm_invoke.py` line 293: `test_cli_omits_model_flag_when_empty` exists
- All 3 commits confirmed in `git log --oneline`: `17e4c5b`, `946479c`, `649b570`
- 34/34 pass in `test_llm_invoke.py`; 133/133 pass in regression suite
