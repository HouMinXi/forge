验证完毕。所有关键跳转都对了真实源码。以下为评审报告（按 output contract 用英文书写，落盘为 cp-artifact）。

---

# CP1b R1 review — kimi — Phase 54 plan 54-01

Angle: cross-boundary data flow + requirements compliance. Every hop below was re-verified against the real sources at main @ 4087b05, not the plan's citations.

## Angle 1 — Cross-boundary data flow

### 1.A kind= taxonomy: llm_invoke raise sites → consumers

Verified the full consumer set by sweep: `grep -rn "\.kind" src/code_forge/` plus `grep -rn "LLMInvokeError"` across all modules.

- **Sole structured consumer** is `mcp_server.py:958-960` (`exc.kind in ("truncated","empty","stub_model","no_json")`). Confirmed with my own eyes. All four new kinds (conn/credentials/sse_body/bad_body) fall outside it; `kind=""` already does. Additive-safe — plan's claim is true.
- All other catch sites are kind-agnostic stringifiers: `runtime.py:349-352`, `factories.py:354`, `contract_loader.py:414`, `daemon_state.py:414/:455`, `falsify_real.py:50`, `cli.py:3225` (re-wraps as CliError, drops kind — pre-existing, untouched by plan).
- Every new kind has a defined fate at the new `_classify_live_failure`: conn → connection-refused, credentials (+exit_code 401/403, which also covers the kindless vertex HTTP raise at `llm_invoke.py:1914`) → credential-rejected, sse_body → SSE-mixed, bad_body → JSON-malformed, anything else (incl. no_json from a prose probe reply, truncated from a cap-hit continuation) → fallback class with mandated non-empty suggestion. No consumer can see a kind it mishandles.
- Raise-site census matches the grep arithmetic: 6 conn (`:1606/:1613/:1746/:1753/:1918/:1925`) + 7 credentials (`:1332/:1337/:1343/:1347` + vertex `:1857/:1862/:1871`) + 2 parse arms (`:1547`) = 15 added; 16 → 31 checks out. google-auth ImportError at `:1839` correctly stays kindless (missing dependency, not bad credential).
- Zero-retry claim verified: retry loop `for attempt in range(max_attempts)` (`:1377`), re-raise at `:1497` fires at attempt 0 with max_attempts=1.

### 1.B Workspace path → trust store byte-identity

- Trust keys are `str(gate_yaml_path.resolve())` (`trust.py:132/:168/:183/:191`; contracts twin `:283/:302/:311/:325`). `resolve_workspace` returns an already-resolved ancestor (`workspace.py:45-50`), so `.resolve()` is idempotent and subdirectory vs root invocation produce byte-identical keys after the plan's change.
- `_resolve_repo_path(raw_path, cwd)` (`contract_loader.py:160-179`) resolves relative contract repo paths against its second argument. Plan passes the resolved workspace (not raw cwd) at both `resolve_contract_specs` call sites (`cli.py:1350-1352` and `:1411-1414`) — required, and correct: without it a subdirectory trust would hash different abs_path entries than a root trust.
- Symlinked cwd handled: warn compares against `cwd.resolve()` (plan Task 2), matching `resolve_workspace`'s internal `start = cwd.resolve()`.

### 1.C FORGE_PROJECT_DIR flow

Priority-1 env read (`workspace.py:39-41`) is the documented semantic, shared with doctor (`doctor.py:79`) and MCP (`mcp_server.py:160-162`). Trust inheriting it is intended, and the warn condition (`resolved != cwd.resolve()`) keeps an env redirect visible (54-RESEARCH.md:193-199 says exactly this). Test-side, the autouse `delenv` fixture in both trust test files closes the only leak path; existing `_run_trust(SimpleNamespace(...), tmp_path)` calls (`tests/test_contract_wiring.py:244-285`) survive because walk-up from tmp_path self-resolves when gate.yaml is present.

### 1.D replace() copy → llm_invoke machinery

- timeout: `effective_invoke_timeout_s` priority 1 is `backend.timeout_s` (`llm_invoke.py:580-581`) — the copy's `timeout_s=60` is mandatory and planned. FORGE_LLM_TIMEOUT_S (priority 3) cannot deform the probe.
- cap: `cap = max_completion_tokens or max_tokens`, then `output_ceiling > 0` overrides (`:269-271`). Copy zeroes both → cap = 32. `outcap_key` survives but only renames the wire key (`:272-280`) — value stays 32.
- params passthrough cannot override the cap: `PROTECTED_PARAM_KEYS` (`backend.py:45-55`) covers max_tokens/max_completion_tokens/output_ceiling/thinking/reasoning_effort/stream/output_config; `check_params` raises before assignment (`llm_invoke.py:339-345`).
- thinking_type/thinking_budget/reasoning_effort zeroed → omitted at `:283`/`:290`. Surviving fields (base_url, api_key_env/file, headers, stream, temperature, model) deform the probe only "as configured", which is the probe's stated purpose.
- Circular-import avoidance verified: `llm_invoke.py:29` imports backend; backend.py's import block (`:25-35`) has no llm_invoke; function-local import precedent holds in doctor.py (`:126-128`). `from dataclasses import dataclass, field` at `backend.py:30` confirmed — bare `dataclasses` is unbound; plan's `replace` addendum is the right fix.
- Truncation-before-attempt-check confirmed: `_TruncatedResponse` caught at `:1485`, `_continue_truncated` runs before the attempt check at `:1497`.

## Angle 2 — Requirements compliance (REQUIREMENTS.md:77-88)

- **ROUTER-02** (docs-only schema text): Task 1 does exactly this, bans the README duplicate per D-10. No narrowing.
- **ROUTER-03** ("prints the resolved gate.yaml path and warns when cwd is not a project, following ADR-0009 $HOME policy"): print-before-mutate ✔ (D-07); $HOME skip inherited from `workspace.py:47-48` ✔; the warn form (declared position A) is the research-endorsed implementable reading (54-RESEARCH.md:193-199) and actually tracks the requirement's "not a project" wording better than D-08's literal "not a git repo root" (a gate.yaml-bearing non-git dir IS a forge project; no-warn there is correct). No-ancestor cwd errors instead of warns — pre-existing, stronger signal, preserved by test (e). **Accept position A.**
- **ROUTER-04** ("probes a backend live... justify on debug-loop value alone"): the wrong-/v1 headline case + F1 stream regression witness is the justification; opt-in flag per D-02/D-06. **Accept positions B and C** (B's mechanism verified true at `:1485`/`:1497`, budget=2 at `:1169`; C's cli-bypass verified at `backend.py:812-813`) — with finding L-1 as B's residual.
- **ROUTER-05** (docs-only pointer): plan delivers pointer + doctor line; the doctor line exceeds "docs only" but is D-11-locked ("PLUS a doctor output line"). Extension, not narrowing.
- **Positions D/E**: user-locked; accept. **Position F**: verified — `TestFormatErrorMessage` (tests/test_llm_invoke.py:3266-3283) is substring/prefix-style only; appending `; body: <excerpt>` keeps all three green, and `_format_error_message` has exactly two callers (`:1601`, `:1741`), both in scope of Step 1.5. Accept.

## Findings

**L-1 (task 4/5, requirements wording vs mechanism).** must_have truth "bounded at 60s with zero retries" and D-05's "60s total timeout" are per-request, not total, on the truncation path. Verified: the probe passes no `continuation_breaker`, so `llm_invoke` builds a fresh `TruncationBreaker(threshold=5)` (`llm_invoke.py:1376-1382`, default `:139`) that cannot trip within a single probe; `_continue_truncated` (budget=2, `:1169`) issues up to 2 continuation requests, each computing a fresh `timeout_s` deadline inside `_invoke_openai`/`_invoke_anthropic`. A responding-but-persistently-truncating backend (32-token cap, chatty model that fills the cap with prose before the JSON) costs up to ~3x60s, contradicting the "60s total" language — position B acknowledges the multiplication but the must_have/D-05 wording was never scoped to match. Two clean fixes, plan's choice: pass `continuation_breaker=TruncationBreaker(threshold=1)` on the probe call (first truncation immediately raises kind="truncated" → classified fallback row, hard 60s bound restored), or scope the wording to "60s per request, ≤3 requests when the cap truncates". Task 6 check 2 stays accurate either way (a *hung* backend dies on the first request's socket timeout — TimeoutError is not a truncation, so no continuation).

**L-2 (task 5 acceptance text).** Injection (2) ("route the live call through probe_backend") claims "a test asserting the helper (not probe_backend) received the call FAILS", but behaviors (a)-(g) mandate no such call-assertion test. The injection is nonetheless caught: in test (c)'s natural setup (offline `probe_backend` mocked ok, helper mocked to a failure `LiveProbeResult`), the injected route renders an ok live row and exit 0, so (c) goes red. Net effect: zero execution risk, but the acceptance criterion names a guard test that does not exist in the behavior list — either add "assert the helper mock was called once per api backend" to (b), or re-point injection (2) at test (c).

**L-3 (task 6 smoke text).** Check 3 tells the human verifier to expect "a classified FAIL row (connection-refused / JSON-malformed / credential-rejected depending on the router)" for a wrong-/v1 base_url. Verified against the taxonomy: a router answering HTTP 404/400 with a JSON error body (the phase's headline case per Step 1.5) raises kind="" with exit_code=404 → none of the three named classes → the unnamed **fallback** class (detail carries the excerpt, suggestion generic). The smoke text's enumeration omits exactly the outcome the headline misconfiguration produces, inviting a false discrepancy report from the human checkpoint. Add the fallback class to the expected-outcome list.

## Declared-position adjudication summary

A — accept (54-RESEARCH.md:193-199 endorses; matches ROUTER-03 intent). B — accept mechanism, residual wording gap reported as L-1. C — accept (backend.py:812-813). D — accept (D-12). E — accept (printed path is the disambiguator). F — accept (tests/test_llm_invoke.py:3266-3283 substring-style; only two formatter callers).

SCORECARD: B=0 H=0 M=0 L=3
