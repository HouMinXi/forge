# Phase 13: Backend Dogfood Verification - Context

**Gathered:** 2026-06-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Prove that all five third-party backends (mimo, deepseek, kimi, glm, minimax) produce
real review output via forge, consuming zero claude tokens. Phase 12 wired the backend
machinery; this phase validates it end-to-end with real API calls and automated tests.

</domain>

<decisions>
## Implementation Decisions

### E2E Test Scope
- **D-01:** All 5 backends get real_api E2E tests with @pytest.mark.real_api and
  skip-if-no-key. No mock-only backends. Each test calls code-forge review against
  a sample diff, asserts [backend] token output in stderr, asserts zero claude
  subprocess invocations.
- **D-02:** Backend configs for gate.yaml (all anthropic format except deepseek=openai):

  | backend  | format    | base_url                                       | model            | key source           |
  |----------|-----------|------------------------------------------------|------------------|----------------------|
  | mimo     | anthropic | https://token-plan-cn.xiaomimimo.com/anthropic | mimo-v2.5-pro    | pass show api/mimo   |
  | deepseek | openai    | https://api.deepseek.com/v1                    | deepseek-v4-pro  | pass show api/deepseek |
  | kimi     | anthropic | https://api.moonshot.cn/anthropic               | kimi-k2.6        | single key (no proxy)|
  | glm      | anthropic | (direct endpoint, no proxy)                     | glm-4.6v or glm-4.5-air | single key    |
  | minimax  | anthropic | https://api.minimaxi.com/anthropic              | MiniMax-M3       | pass show api/minimax|

  glm: current latest model (glm-5.1) is unavailable; use glm-4.6v or glm-4.5-air
  for testing. kimi/glm: direct connection (no rotation proxy); use single key from
  pass or env var.
- **D-03:** kimi and glm connect directly to their API endpoints, NOT through
  the local rotation proxy. The proxy handles 429/key rotation for Claude Code
  sessions but forge only makes 3-9 API calls per review, which does not trigger
  rate limits.

### UAT Evidence Reuse
- **D-04:** Phase 12 UAT evidence (12-HUMAN-UAT.md) counts as SC1/SC2/SC3
  verification for mimo and deepseek. Phase 13 does NOT re-run manual UAT for
  those two; it adds automated E2E tests + manual UAT for the 3 new backends
  (kimi, glm, minimax).

### Token Cost in SARIF Output
- **D-05:** Add token cost to SARIF output in runs[0].properties. Structure:
  ```json
  "properties": {
    "tokenCost": {
      "inputTokens": 9498,
      "outputTokens": 7190,
      "totalTokens": 16688,
      "backend": "mimo",
      "model": "mimo-v2.5-pro",
      "passes": 3,
      "durationSeconds": 148.9
    }
  }
  ```
  stderr per-pass lines ([backend] N in / M out) remain unchanged.

### Claude's Discretion
- glm model selection: try glm-4.6v first; if unavailable, fall back to glm-4.5-air
- E2E test fixture: use the existing test_cli_integration.py pattern for consistency

</decisions>

<canonical_refs>
## Canonical References

### Backend Wiring (from Phase 12)
- `.planning/phases/12-backend-api-wiring/12-CONTEXT.md` -- D-01 through D-16 decisions
- `.planning/phases/12-backend-api-wiring/12-HUMAN-UAT.md` -- UAT evidence for mimo+deepseek
- `src/code_forge/backend.py` -- BackendConfig, load_backend_configs, resolve_backend
- `src/code_forge/llm_invoke.py` -- _invoke_api, _invoke_openai, _invoke_anthropic

### Existing Test Patterns
- `tests/test_cli_integration.py:735-790` -- real_api mimo smoke test pattern
- `tests/test_backend.py:870` -- @pytest.mark.real_api probe test
- `tests/test_llm_invoke.py:174-230` -- mock API dispatch tests

### SARIF Output
- `src/code_forge/sarif.py` -- build_sarif_log, current properties structure
- `src/code_forge/cli.py:59-87` -- _emit_ci_output (where SARIF is emitted)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `@pytest.mark.real_api` marker + conftest skip logic: already wired for mimo
- `BackendConfig` dataclass: accepts all 5 backends with no code changes needed
- `_invoke_openai` / `_invoke_anthropic`: both paths tested in Phase 12
- `build_sarif_log()` in sarif.py: already produces runs[0] with properties dict

### Established Patterns
- test_cli_integration.py uses `FakeResponse` class for mock tests, real urllib
  for @real_api tests
- SARIF properties are a freeform dict; adding tokenCost is additive, no schema break
- gate.yaml loading via _load_gate_backends() helper (cli.py:90)

### Integration Points
- cli.py _emit_ci_output: where tokenCost properties need to be injected
- StateMachine or _run(): where token usage accumulates across passes
- conftest.py: where @real_api marker and skip conditions are defined

</code_context>

<specifics>
## Specific Ideas

- glm endpoint may need discovery: check zhipu API docs for the direct (non-proxy)
  anthropic-compatible endpoint URL
- kimi direct endpoint: https://api.moonshot.cn/anthropic (confirmed from bashrc)
- minimax direct endpoint: https://api.minimaxi.com/anthropic (confirmed from bashrc)

</specifics>

<deferred>
## Deferred Ideas

- SEC-01: untrusted gate.yaml credential flow (tracked separately for v2.4)
- REVIEW-TRUST-01 through REVIEW-SYSTEM-01 (v2.4 backlog)
- Key rotation proxy integration for forge (not needed at 3-9 calls/review)

</deferred>

---

*Phase: 13-Backend Dogfood Verification*
*Context gathered: 2026-06-05*
