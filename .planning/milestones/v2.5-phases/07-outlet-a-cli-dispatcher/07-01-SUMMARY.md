---
plan: 07-01
phase: 07-outlet-a-cli-dispatcher
status: complete
started: 2026-06-02
completed: 2026-06-02
---

# Plan 07-01: BackendConfig.command + llm_invoke generalization

## One-liner

Added command field to BackendConfig and generalized llm_invoke() to dispatch by backend type (cli subprocess vs api HTTP).

## What was built

- BackendConfig.command: str = "" field for specifying CLI binary
- llm_invoke(prompt, backend=BackendConfig, timeout_s) dual dispatch
- _strip_fences() shared markdown-fence stripping helper
- Backward compatible via DEFAULT_BACKEND fallback

## Key files modified

- src/code_forge/backend.py -- command field
- src/code_forge/llm_invoke.py -- dual dispatch
- tests/test_backend.py -- 4 new tests
- tests/test_llm_invoke.py -- 10 new tests

## Test results

63 passed, 0 failed.

## Deviations

None.

## Self-Check: PASSED
