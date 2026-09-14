# LLM retry MCP/CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Close three gaps in the existing HTTP retry loop so MCP and CI show retries and recover from free-model flakes (timeout opt-in, sampling empty/no_json).

**Architecture:** One retry owner (`_invoke_api` / `invoke_sampling`). New `retry.retry_timeouts` flag default false. Logs go through `progress.emit` so MCP stderr files flush. Exhausted attempt emits `code-forge: retry exhausted` then raises.

**Tech Stack:** Python 3.13, pytest, existing `gate.yaml retry` schema.

**Spec:** `docs/superpowers/specs/2026-09-14-llm-retry-mcp-ci.md`

## Global Constraints

- TDD: inject at the fix, FAIL then PASS, revert injection.
- No second retry loop around StateMachine.
- `probe_backend_live` stays `max_attempts=1`.
- Credentials / truncation / stub_model stay non-retryable.
- Commit messages: `<subsystem>/<case>: <why>`, Signed-off-by, no review vocabulary.
- Non-ASCII check on added lines; house style wins.

## Files

- Modify: `src/code_forge/llm_invoke.py` (`_invoke_api` loop, `llm_invoke`, `invoke_sampling`)
- Modify: `src/code_forge/gate_check.py` (`validate_retry_config`)
- Modify: `src/code_forge/gate.schema.json` (`retry.retry_timeouts`)
- Modify: `src/code_forge/cli.py` (forward flag)
- Modify: `src/code_forge/factories.py` (`build_l1_provider`, sampling)
- Modify: `src/code_forge/mcp_server.py` (`_dispatch_sampling` load retry)
- Modify: `src/code_forge/user_config.py` (merge user/project retry)
- Modify: `src/code_forge/progress.py` only if emit needs a helper; prefer calling emit
- Test: `tests/test_llm_invoke.py` (`TestRetryLoop`)
- Test: `tests/test_gate_check.py` (`TestRetryConfig`)
- Test: `tests/test_factories.py` (sampling retry kwargs)
- Test: new or existing user-config tests for retry merge

### Task 1: Retry logs flush + exhausted line

**Files:** `llm_invoke.py:1663-1689`, `tests/test_llm_invoke.py::TestRetryLoop`

- [ ] Extend `test_stderr_progress` to assert the line is visible when stderr is a non-tty StringIO (already is). Add `test_exhaustion_emits_retry_exhausted` that 429s `max_attempts` times and finds `code-forge: retry exhausted` in stderr. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Run: expect FAIL (no exhausted line; maybe no flush -- StringIO is unbuffered so flush is for MCP files). -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Implementation: replace `sys.stderr.write` with `progress.emit(...)` for both retrying and exhausted. Keep the `code-forge: retrying` / `code-forge: retry exhausted` substrings. Emit exhausted immediately before the final `raise`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Run tests PASS. Bug-inject: drop the exhausted emit, watch FAIL, restore. -- accept: satisfy this item with fresh command or artifact evidence

### Task 2: Opt-in timeout retry

**Files:** `llm_invoke.py` (`llm_invoke` + `_invoke_api` TimeoutError arm), `gate_check.py`, `gate.schema.json`, `cli.py`, `factories.py`

- [ ] Add `retry_timeouts: bool = False` through `llm_invoke` -> `_invoke_api`. When True, TimeoutError `retryable=True`. When False, keep today's one-shot. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Keep `test_timeout_not_retried_raises_immediately` green. Add `test_timeout_retried_when_flag_set` (TimeoutError twice then 200, `max_attempts=3`). -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Schema + `validate_retry_config`: `retry_timeouts` must be bool; reject `"yes"`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] CLI forwards `retry_cfg.get("retry_timeouts", False)` into `build_l1_provider` / grouped. Factories forward into `llm_invoke`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Bug-inject: leave TimeoutError `retryable=False` while flag True, watch new test FAIL. -- accept: satisfy this item with fresh command or artifact evidence

### Task 3: Sampling retry loop

**Files:** `llm_invoke.py::invoke_sampling`, `factories.py::build_sampling_l1_provider`, `mcp_server.py::_dispatch_sampling`

- [ ] `invoke_sampling(..., max_attempts=5, initial_delay_s=2.0, retry_timeouts=False)`. Retry `kind in {"empty","no_json"}` and retryable exceptions. Never retry truncated / stub_model / credentials. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Tests: empty then JSON succeeds; truncated raises on first call; exhausted empty emits `retry exhausted`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Sampling L1 provider and `_dispatch_sampling` pass gate.yaml retry the same way CLI `_run` does. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Bug-inject: skip the loop (`max_attempts` ignored), empty-then-JSON test FAIL. -- accept: satisfy this item with fresh command or artifact evidence

### Task 4: User/project retry merge

**Files:** `user_config.py`, tests

- [ ] `load_user_retry() -> dict`. `merge_retry(project, user)`: project keys win, missing keys fill from user, then defaults `{max_attempts:5, initial_delay_s:2.0, retry_timeouts:False}`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] CLI `_run` uses merge so a user-level `retry_timeouts: true` applies when the project gate has no retry block. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Validate after merge. -- accept: satisfy this item with fresh command or artifact evidence

### Task 5: Static + injection closeout

- [ ] `ruff` + `py_compile` on touched files. Non-ASCII check on `git diff`. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Re-run TestRetryLoop + TestRetryConfig + sampling tests. -- accept: satisfy this item with fresh command or artifact evidence
- [ ] Commit on `feat/llm-retry-mcp-ci`. Do not push main. Report SHA + test counts. -- accept: satisfy this item with fresh command or artifact evidence
