# Phase 31: CN Backend Robustness - Context

**Gathered:** 2026-06-27
**Status:** Ready for planning

<domain>
## Phase Boundary

forge's LLM invocation layer (llm_invoke.py + factories.py) gains resilience
against the error diversity of all five CN providers (DeepSeek, MiMo, Zhipu,
MiniMax, Kimi).  Currently every HTTP error raises LLMInvokeError immediately
with no retry, no provider-specific classification, and no actionable message.

This phase adds: configurable exponential-backoff retry, provider-specific
error code classification (HTTP status + JSON body), Retry-After header
support, pass-level retry in factories.py, and actionable error messages.
L1 passes stay serial (no concurrency control needed).

Closes ROBUST-01 through ROBUST-05.

</domain>

<decisions>
## Implementation Decisions

### Retry Strategy
- **D-31-01:** Two-layer retry. HTTP-level retry in llm_invoke.py (handles
  429/5xx/network errors before the caller sees them) AND pass-level retry
  in factories.py (retries the whole pass once on LLMInvokeError, then marks
  INFRA finding and moves to next pass).
- **D-31-02:** HTTP retry parameters are configurable via gate.yaml
  (`retry.max_attempts`, `retry.initial_delay_s`).  Defaults: max 5 attempts,
  2s initial delay, exponential backoff x2, random jitter 0-500ms.  When
  Retry-After header is present (DeepSeek, Kimi), use max(computed_delay,
  header_value).  Total worst-case wait ~30s (5 attempts = 1 initial + 4 retries;
  delays: 2+4+8+16 = 30s).

### Provider Error Classification
- **D-31-03:** Error code mapping hardcoded in llm_invoke.py (a dict keyed
  by provider name from BackendConfig.name).  Zhipu (string codes, not int):
  "1302" rate limit (retryable), "1305" service overloaded (retryable),
  "1308" usage limit per time unit (non-retryable), "1113" balance exhausted
  (non-retryable); rest default to retryable.  MiniMax: 1002 rate limit
  (retryable), 1008 balance (non-retryable), 1039 token limit
  (non-retryable -- retrying same-size prompt is wasted), 1041 conn limit
  (retryable), 2045 rate growth (retryable), 2056 usage limit
  (non-retryable).
  PROVENANCE: Zhipu codes corrected from discuss-phase originals (1302 was
  "balance", 1305 was "invalid key") by researcher against docs.z.ai
  (2026-06-27). Gatekeeper attempted live-docs re-verification (2026-06-27)
  but open.bigmodel.cn/dev/api/error-code redirects to intro page --
  codes UNCONFIRMED against current live docs. MiniMax codes aggregated
  from community (official page unreachable). Treat both as best-available;
  re-verify if unexpected errors arise at runtime.
- **D-31-04:** Body-based error detection: _invoke_openai/_invoke_anthropic
  check the parsed JSON for error indicator fields (Zhipu: `error.code`;
  MiniMax: `base_resp.status_code`) BEFORE attempting content extraction.
  If found, classify via the error map and raise LLMInvokeError with
  retryable flag, skipping the content parse that would KeyError.

### Retryable Classification
- **D-31-05:** Retryable HTTP statuses: 429, 500, 502, 503, 504.
  Retryable non-HTTP: TimeoutError, URLError (network).
  Non-retryable: 400, 401, 402, 403, 404, 422 + provider-specific
  non-retryable body codes (Zhipu "1113" balance, MiniMax 1008/1039/2056).
  (See D-31-03 PROVENANCE for code verification status.)

### L1 Pass Dispatch
- **D-31-06:** L1 passes stay serial (for loop in factories.py).
  No concurrency control.  ROBUST-04 satisfied by HTTP retry + backoff
  (serial dispatch naturally avoids rate-limit storms).

### Degradation and Feedback
- **D-31-07:** Retry exhaustion = fail-closed (INFRA finding + FAIL verdict).
  Commit is blocked.  Consistent with ADOPT-04 (fail-closed on no-backend).
- **D-31-08:** Error message format:
  `"code-forge: {provider} backend: {problem} ({HTTP code/body code}). {actionable suggestion}"`
  Examples:
  - `"code-forge: deepseek backend: balance exhausted (HTTP 402). Top up at platform.deepseek.com or switch backend with --backend"`
  - `"code-forge: zhipu backend: concurrency limit (code 1308). Reduce parallel usage or wait 30s"`
- **D-31-09:** Retry progress printed to stderr during retry loop:
  `"code-forge: retrying {provider} ({n}/{max}, waiting {delay}s)..."`
  Users in pre-commit hooks would otherwise see no output for up to 60s.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### forge core (error handling + invocation)
- `src/code_forge/llm_invoke.py` -- _invoke_openai() (line 487),
  _invoke_anthropic() (line 534), _invoke_api() (line 402),
  LLMInvokeError class, llm_invoke() entry point (line 229)
- `src/code_forge/factories.py` -- L1 pass dispatch loop (line 252),
  LLMInvokeError catch (line 306)
- `src/code_forge/backend.py` -- BackendConfig dataclass (line 59),
  `name` field used for provider identification

### forge config
- `.code-forge/gate.yaml` -- backend config; retry.* fields to be added
- `src/code_forge/gate_check.py` -- load_gate_config() for YAML parsing

### CN provider error references (from 2026-06-26 research + Exa scan)
- MiniMax error codes: platform.minimax.io/docs/api-reference/errorcode
  (1002 rate, 1008 balance, 1039 token, 1041 conn, 2045 rate growth,
  2056 usage limit)
- Zhipu error patterns: Chinese-language messages; sub-codes in
  `error.code` field
- DeepSeek + Kimi: standard HTTP 429 with optional Retry-After header
- Industry standard: 429/500/502/503/504 retryable, 4xx (except 429) not

### Phase 30 carry-forward
- `.planning/phases/30-switch-on-dogfood/30-CONTEXT.md` -- CN backend error
  context section (Phase 31 feed-forward)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `LLMInvokeError` class in llm_invoke.py: already has `exit_code`,
  `is_timeout`, `stderr`, `duration_s` attributes.  Needs `retryable: bool`
  attribute added.
- `BackendConfig.name` (backend.py:66): provider identifier already available
  at the call site, usable for error code map dispatch.
- `_strip_fences()` and `_extract_json_from_text()`: existing JSON cleanup
  helpers, unaffected by retry changes.

### Established Patterns
- Error handling: `urllib.error.HTTPError` caught in `_invoke_openai()` and
  `_invoke_anthropic()`, body excerpt read and passed to LLMInvokeError.
  Retry wraps this catch block.
- `_invoke_api()` wraps all format-specific dispatchers with TimeoutError
  handling.  Retry layer sits here or one level up.
- factories.py catches LLMInvokeError per pass and creates INFRA findings;
  pass-level retry wraps this existing catch.

### Integration Points
- `gate.yaml` parsed by `load_gate_config()` in gate_check.py -- retry.*
  fields parsed here and passed to llm_invoke.
- `BackendConfig` frozen dataclass -- retry config likely passed as separate
  args to llm_invoke, not added to BackendConfig (retry is per-invocation
  policy, not per-backend identity).
- Pre-commit hook timeout: existing EXIT_TIMEOUT=6 circuit breaker in
  gate_check.py caps total hook runtime.  Retry total wait must not exceed
  this outer timeout.

</code_context>

<specifics>
## Specific Ideas

- The error code map can be a module-level dict in llm_invoke.py:
  `PROVIDER_ERROR_MAP = {"zhipu": {1302: "non-retryable", ...}, ...}`
- Body-based error detection: check for `resp_data.get("error")` or
  `resp_data.get("base_resp")` before `resp_data["choices"]` parsing.
  This is a 3-line addition per _invoke_* function.
- Retry decorator or inline loop: either `@retry(max_attempts=N)` decorator
  on _invoke_api, or a while loop inside it.  Decorator is cleaner but
  harder to pass Retry-After context.  Planner decides.

</specifics>

<deferred>
## Deferred Ideas

- Provider fallback chain (switch to a different backend when retry exhausts
  on the primary) -- new capability, own phase
- Circuit breaker (fast-fail after N consecutive failures to same provider
  without hitting API) -- overkill for current usage pattern (single user,
  serial passes)
- Per-backend retry config override (mimo-pro gets 5 retries, deepseek gets
  3) -- premature; global config sufficient until proven otherwise
- Zhipu full 21 sub-code mapping -- only common 5-6 mapped; expand if
  user hits unmapped codes in practice

</deferred>

---

*Phase: 31-cn-backend-robustness*
*Context gathered: 2026-06-27*
