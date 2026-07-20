---
phase: 08-hardening
plan: "01"
subsystem: llm_invoke
tags:
  - subprocess-lifecycle
  - process-group-isolation
  - signal-handling
  - llm-result-type
  - cost-metadata
dependency_graph:
  requires:
    - "07-02: llm_invoke initial implementation"
  provides:
    - "LLMResult return type for cost tracking (08-02 foundation)"
    - "Subprocess orphan protection for all cli backends"
  affects:
    - src/code_forge/llm_invoke.py
    - src/code_forge/factories.py
    - src/code_forge/falsify_real.py
    - tests/test_llm_invoke.py
    - tests/test_falsify_real.py
tech_stack:
  added:
    - "subprocess.Popen with start_new_session=True for Unix process group isolation"
    - "os.killpg for kill-tree (SIGTERM -> 5s -> SIGKILL escalation)"
    - "dataclasses.dataclass(frozen=True) for Usage and LLMResult"
  patterns:
    - "lock.py chained signal handler pattern reused for _install_signal_handlers"
    - "Module-level _active_proc tracker enables signal handler cleanup"
key_files:
  created: []
  modified:
    - src/code_forge/llm_invoke.py
    - src/code_forge/factories.py
    - src/code_forge/falsify_real.py
    - tests/test_llm_invoke.py
    - tests/test_falsify_real.py
decisions:
  - "Usage default field: Usage() direct default (safe since frozen; no field(default_factory))"
  - "CLI backends report Usage(0, 0): claude -p stdout is content-only per D-07"
  - "Signal handlers installed at module load time (idempotent guard prevents re-install)"
  - "test_falsify_real mocks updated to LLMResult (Rule 1 auto-fix for breaking change)"
metrics:
  duration: "23m 31s"
  completed: "2026-06-02"
  tasks_completed: 7
  files_modified: 5
---

# Phase 8 Plan 1: Subprocess Orphan Protection and LLMResult Return Type Summary

## One-liner

Subprocess orphan protection via Popen + process group kill-tree and LLMResult frozen dataclass return type enabling token usage tracking.

## What Was Built

### CLI-07: Subprocess Orphan Protection

Replaced `subprocess.run()` with `subprocess.Popen(start_new_session=True)` in `_invoke_cli`. The new session isolates the subprocess into its own process group, enabling `os.killpg()` to kill the claude process and all its children (grandchildren included).

Added `_kill_tree(proc)` helper:
- `os.killpg(proc.pid, SIGTERM)` for graceful shutdown
- `proc.wait(timeout=5)` for grace period
- `os.killpg(proc.pid, SIGKILL)` escalation if process does not exit
- Handles `ProcessLookupError` (already dead) gracefully

Added module-level signal handlers (copying the `lock.py` chain pattern):
- `_active_proc: Optional[subprocess.Popen]` tracks the running process
- `_install_signal_handlers()` installs SIGINT/SIGTERM handlers at module load
- Handlers call `_kill_tree(_active_proc)` then chain to previous handler
- Idempotent (`_handlers_installed` guard prevents double-install)
- `_active_proc` is set in `try`, cleared in `finally` ensuring cleanup

### CLI-08 Foundation: LLMResult Return Type

Added two frozen dataclasses:

```
Usage(input_tokens: int = 0, output_tokens: int = 0)
LLMResult(content: Any, usage: Usage = Usage(), duration_s: float = 0.0)
```

Changed `llm_invoke()` return type from `Any` to `LLMResult`.

- `_invoke_cli`: returns `LLMResult(content=parsed, usage=Usage(0,0), duration_s=duration)` - CLI has no per-invocation token data per D-07
- `_invoke_openai`: now returns `(content_str, usage_dict)` tuple; caller builds `Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens)`
- `_invoke_anthropic`: returns `(content_str, usage_dict)` tuple; caller builds `Usage(input_tokens=input_tokens, output_tokens=output_tokens)`
- `_invoke_api`: builds `LLMResult(content=parsed, usage=usage, duration_s=duration)` with populated token counts from API response

### Callers Updated

- `factories.py` `build_l1_provider._provider()`: `result = llm_invoke(...); response = result.content`
- `falsify_real.py` `RealFalsifier.falsify()`: same pattern

### Tests Updated

- `tests/test_llm_invoke.py`: fully rewritten to mock `subprocess.Popen` instead of `subprocess.run`; 29 tests (18 original updated + 11 new)
- New `TestLLMResult` class: 6 tests for frozen, defaults, structure
- New `TestSubprocessCleanup` class: 3 tests for _kill_tree called on timeout, _active_proc cleared after success/error
- `tests/test_falsify_real.py`: updated 5 mocks to return `LLMResult(content=dict)` (Rule 1 auto-fix)

## Commits

| Hash | Description |
|------|-------------|
| e8f1ee3 | feat(08-01): add Usage and LLMResult frozen dataclasses |
| a6f4ba6 | feat(08-01): replace subprocess.run with Popen + process group isolation |
| 54c2ee6 | feat(08-01): add chained signal handlers for subprocess cleanup |
| dd14b2b | feat(08-01): change _invoke_openai/_invoke_anthropic to return (content, usage) tuples |
| 7c79ec0 | feat(08-01): change llm_invoke return type to LLMResult |
| f139ffe | feat(08-01): update llm_invoke callers to access .content from LLMResult |
| 7b2af30 | feat(08-01): update tests for LLMResult return type and subprocess cleanup |
| 388c23a | fix(08-01): update test_falsify_real mocks for LLMResult return type |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_falsify_real mocks returned raw dict instead of LLMResult**
- **Found during:** Task 7 full test suite run
- **Issue:** `test_falsify_real.py` mocked `llm_invoke` returning `{"verdict": "..."}` dict directly. After Task 6 changed `falsify_real.py` to access `result.content`, all falsify tests broke with `AttributeError: dict has no attribute content`.
- **Fix:** Added `_make_llm_result(content)` helper; updated all 5 mock return values to `LLMResult(content=dict)`.
- **Files modified:** `tests/test_falsify_real.py`
- **Commit:** 388c23a

**2. [Rule 1 - Bug] Existing test_llm_invoke mocked subprocess.run (dead mock after Popen change)**
- **Found during:** Task 7 analysis
- **Issue:** All existing tests mocked `subprocess.run` which no longer intercepts calls after Task 2 switched to `subprocess.Popen`. Tests passed because they hit real claude binary via default path.
- **Fix:** Rewrote all test mocks to patch `code_forge.llm_invoke.subprocess.Popen` instead.
- **Files modified:** `tests/test_llm_invoke.py`
- **Commit:** 7b2af30

### Worktree Setup

The agent's worktree branch was based on commit `3a10a89` (Phase 3 end), which predates Phase 7. Before implementing Phase 8, the worktree was fast-forwarded to `main` (`41fdac3`) via `git merge main` to incorporate Phase 7 changes. This is expected behavior for multi-wave parallel execution.

## Verification

All plan success criteria met:

- [x] Usage and LLMResult dataclasses exist and are frozen
- [x] `_invoke_cli` uses Popen + start_new_session=True
- [x] `_kill_tree` sends SIGTERM, waits 5s, escalates to SIGKILL
- [x] Signal handlers installed at module level, chain to previous handlers
- [x] `_invoke_openai`/`_invoke_anthropic` return `(content, usage_data)` tuples
- [x] `llm_invoke` returns LLMResult, api backends populate usage from response
- [x] `factories.py` and `falsify_real.py` access `.content` from LLMResult
- [x] All tests pass: 29 llm_invoke + 6 falsify_real = 35 directly modified tests
- [x] `_strip_fences()` call preserved in `_invoke_cli` before json.loads

## Known Stubs

None. All token tracking is functional (zero for CLI backends by design per D-07).

## Threat Flags

T-08-01, T-08-02, T-08-03 from plan threat model all mitigated:
- List-form cmd construction (no shell=True for non-large prompts)
- API key accessed from env at call time, never logged
- Process group cleanup via _kill_tree + signal handlers

## Self-Check: PASSED
