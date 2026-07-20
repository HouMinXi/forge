---
phase: 31-cn-backend-robustness
plan: "01"
status: complete
started: 2026-06-27
completed: 2026-06-27
commits:
  - d819e9e
  - 9a2acdc
  - f40f3a0
  - 5805a5f
---

## Summary

Added HTTP-level retry with exponential backoff, provider-specific error code
classification, Retry-After header support, and body-based error detection to
forge's LLM invocation layer.

## What Was Built

### Task 1: LLMInvokeError attributes + provider error map + helpers
- Extended LLMInvokeError with retryable (bool, default True) and retry_after (float|None)
- Added PROVIDER_ERROR_CODES dict with Zhipu (string keys) and MiniMax mappings
- Added RETRYABLE_HTTP_STATUSES frozenset (429, 500, 502, 503, 504)
- Added helpers: _parse_retry_after, _is_body_code_retryable, _check_body_error, _format_error_message, _suggestion

### Task 2: HTTP classification + body detection + retry loop
- Modified _invoke_openai and _invoke_anthropic HTTPError catch blocks to read body once, parse Retry-After, classify retryable
- Added _check_body_error call in _invoke_openai before content extraction (Zhipu error.code + MiniMax base_resp)
- Added inline retry loop in _invoke_api wrapping format dispatch with exponential backoff + jitter
- Moved TimeoutError catch inside retry scope
- Added max_attempts and initial_delay_s kwargs to llm_invoke and _invoke_api

## Key Files

- src/code_forge/llm_invoke.py (+274/-49 lines)
- tests/test_llm_invoke.py (+510/-1 lines)

## Self-Check

129 tests pass (0 failures). All retry-specific tests pass.

## Deviations

Orchestrator committed Task 2 GREEN phase after agent disconnected 3x due to API ConnectionRefused. Agent wrote the code and tests passed; orchestrator committed the uncommitted changes.
