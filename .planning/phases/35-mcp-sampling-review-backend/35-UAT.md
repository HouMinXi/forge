---
status: testing
phase: 35-mcp-sampling-review-backend
source: 35-01-SUMMARY.md
started: 2026-07-01T00:15:00Z
updated: 2026-07-01T00:15:00Z
---

## Current Test

number: 1
name: CLI accepts --outlet sampling
expected: |
  Running `code-forge review --outlet sampling` in a non-MCP context
  is accepted by argparse (no "invalid choice" error) and reaches the
  CLI guard, which rejects it with "only available within the MCP server
  context".
awaiting: user response

## Tests

### 1. CLI accepts --outlet sampling
expected: `code-forge review --outlet sampling` reaches the CLI guard (not rejected by argparse). Guard produces "only available within the MCP server context" error.
result: [pending]

### 2. CLI guard via FORGE_OUTLET env var
expected: Setting `FORGE_OUTLET=sampling` and running `code-forge review` hits the same guard with the same error message.
result: [pending]

### 3. invoke_sampling truncation detection
expected: When the MCP sampling response has `stopReason == "maxTokens"`, `invoke_sampling` raises `LLMInvokeError` with "truncated" in the message instead of returning a result.
result: [pending]

### 4. invoke_sampling JSON extraction
expected: A valid sampling response with JSON content is parsed and returned as `LLMResult.content` (dict), with `Usage(0, 0)`.
result: [pending]

### 5. Sampling provider truncation fallback
expected: When `invoke_sampling` raises truncation error, `build_sampling_l1_provider` falls back to the CLI backend (subprocess) for retry, preventing infinite loop.
result: [pending]

### 6. Sampling provider timeout cancels future
expected: When the 5-minute hard ceiling is exceeded, the provider catches the timeout, calls `future.cancel()`, and returns a DELEGATED disposition instead of hanging.
result: [pending]

### 7. Full regression suite green
expected: `pytest` on all test files passes with the same count as pre-Phase-35 baseline (2372 passed, 7 skipped). No new failures, no RuntimeWarning.
result: [pending]

## Summary

total: 7
passed: 0
issues: 0
pending: 7
skipped: 0
blocked: 0

## Gaps

[none yet]
