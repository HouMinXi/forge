## Cross-boundary data flow / requirements compliance review — R3 exit

I re-read the current plan and re-verified the key cross-boundary claims against the live source files under `src/code_forge/`.

### What I checked

1. **kind= taxonomy end-to-end**
   - `LLMInvokeError.kind` exists at `llm_invoke.py:62`; the current whitelist consumer at `mcp_server.py:958-960` remains `{truncated, empty, stub_model, no_json}`, so the planned additive kinds (`conn`, `credentials`, `sse_body`, `bad_body`) fall outside it exactly like `kind=""` already does — no fallback behavior change.
   - All planned raise sites are present and currently kind-less: `_parse_response_body` (`llm_invoke.py:1547`), credential block (`:1332/:1337/:1343/:1347`), URLError/OSError handlers (`:1606/:1613`, `:1746/:1753`, `:1918/:1925`), vertex credential raises (`:1857/:1862/:1871`).
   - The eight-class classifier mapping in Task 4 is internally consistent and handles `kind=""`, `is_timeout`, and exit-code precedence correctly.

2. **Probe truncation-continuation ordering**
   - `TruncationBreaker.record_truncation()` (`llm_invoke.py:143-147`) raises `TruncationBreakerError(kind="truncated", retryable=False)` at the threshold-crossing event.
   - `_continue_truncated()` calls `breaker.record_truncation()` first (`:1190-1191`) before any continuation request.
   - With `continuation_breaker=TruncationBreaker(threshold=1)` passed by the probe, the first truncation raises before `_continue_truncated` issues a continuation, restoring the hard 60s bound as claimed.
   - `TruncationBreakerError` inherits from `LLMInvokeError` (`:100`) and has `retryable=False`, so the `_invoke_api` retry loop (`:1497`) re-raises it immediately rather than retrying.

3. **Resolved workspace path / trust hashing**
   - `resolve_workspace()` (`workspace.py:19-51`) returns the resolved ancestor workspace root or resolved cwd fallback.
   - `record_trust()` / `revoke_trust()` key the store by `str(gate_yaml_path.resolve())` (`trust.py:168/:183`). Passing the resolved workspace-derived `gate_yaml_path` from a subdirectory produces the same absolute key as a root invocation, so trust-store entries are byte-identical.
   - `_resolve_repo_path()` (`contract_loader.py:160-179`) resolves relative repo paths against its `cwd` argument. Passing the resolved workspace there (instead of the subdirectory cwd) keeps contract hashing consistent across invocation directories.

4. **env / FORGE_PROJECT_DIR hygiene**
   - `resolve_workspace()` reads `env.get("FORGE_PROJECT_DIR")` at priority 1 (`workspace.py:39-41`). The plan mandates autouse `delenv("FORGE_PROJECT_DIR")` fixtures in both trust test files, which matches the house rule to isolate external channels.

5. **replace() config copy**
   - `BackendConfig` fields include `timeout_s`, `max_tokens`, `max_completion_tokens`, `output_ceiling`, `thinking_type`, `thinking_budget`, `reasoning_effort`, `stream`, etc. (`backend.py:142-179`).
   - `effective_invoke_timeout_s()` gives `backend.timeout_s` priority over caller `timeout_s` (`llm_invoke.py:580-581`), so the probe must override on the config copy as planned.
   - `_apply_params()` computes `cap = backend.max_completion_tokens or backend.max_tokens` and skips thinking/reasoning when those fields are falsy (`llm_invoke.py:269-296`). Zeroing `max_completion_tokens`/`output_ceiling` and the thinking fields therefore forces a clean 32-token JSON request.

6. **Requirements compliance (REQUIREMENTS.md ROUTER-02..05)**
   - ROUTER-02: covered by Task 1 schema text.
   - ROUTER-03: covered by Task 2 walk-up + path print + warn (with the accepted `workspace != cwd.resolve()` warn condition per declared position A).
   - ROUTER-04: covered by Tasks 4-5 live probe; debug-loop value is supported by the wrong-/v1 404 failure-class witness in Task 6.
   - ROUTER-05: covered by Task 3 doctor line + README pointer.

No new BLOCKER, HIGH, MEDIUM, or LOW findings. The previously adjudicated items remain fixed as described in the briefing, and the current plan text is internally consistent with the live code.

SCORECARD: B=0 H=0 M=0 L=0
