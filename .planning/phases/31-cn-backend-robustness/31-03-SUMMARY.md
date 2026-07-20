---
phase: 31-cn-backend-robustness
plan: 03
subsystem: review-pipeline
tags: [retry, l1-provider, gate-config]
dependency_graph:
  requires: [31-01, 31-02]
  provides: [pass-level-retry, retry-config-wiring]
  affects: [factories.py, cli.py]
tech_stack:
  added: []
  patterns: [pass-level-retry-loop, config-threading]
key_files:
  created: []
  modified:
    - src/code_forge/factories.py
    - src/code_forge/cli.py
    - tests/test_factories.py
decisions:
  - "Pass-level retry uses range(2) loop around llm_invoke, prompt reused on retry"
  - "Retry config defaults (5, 2.0) match D-31-02 for backward compatibility"
metrics:
  duration: 8m46s
  completed: 2026-06-27
---

# Phase 31 Plan 03: Pass-Level Retry + Config Wiring Summary

Pass-level retry in factories.py _provider() with gate.yaml retry config threaded through cli.py to build_l1_provider to llm_invoke.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Pass-level retry + retry config threading | b76983e | factories.py, test_factories.py |
| 2 | Wire retry config from gate.yaml through cli.py | 8383a9f | cli.py |
| 3 | Full-suite regression | (verification only) | 274 tests passed |

## Implementation Details

**Task 1:** Wrapped the llm_invoke call in _provider() with `for pass_attempt in range(2)`. On first attempt, retryable LLMInvokeError prints a retry message and continues the inner loop. Non-retryable errors or second-attempt failures create INFRA findings immediately. Added max_attempts and initial_delay_s kwargs to build_l1_provider, forwarded to llm_invoke. 6 new tests in TestPassLevelRetry class.

**Task 2:** After `_load_gate_backends` in cli.py, extract `gate_data.get("retry", {})` and validate via `validate_retry_config`. Pass `max_attempts` and `initial_delay_s` to `build_l1_provider` with defaults matching D-31-02.

**Task 3:** Full regression: 274 tests passed across test_llm_invoke.py, test_factories.py, test_gate_check.py.

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED
