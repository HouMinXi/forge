# Phase 8: Hardening - Context

**Gathered:** 2026-06-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Outlet A production hardening: subprocess lifecycle safety (orphan protection on cancel/timeout), cost transparency (token usage + estimated cost after review), and documented configuration for editor environments. No new review capabilities -- this phase makes existing Outlet A robust and user-friendly.

</domain>

<decisions>
## Implementation Decisions

### CLI-07: Subprocess Orphan Protection
- **D-01:** Unix-first implementation using `preexec_fn=os.setsid` + `os.killpg`. Windows support is a deferred roadmap item, not Phase 8 scope. See ROADMAP.md Deferred section "Windows IDE support".
- **D-02:** Replace `subprocess.run()` with `subprocess.Popen(start_new_session=True)` + manual `communicate(timeout=)`. On timeout or signal, `_kill_tree(proc)` sends SIGTERM, waits 5s, escalates to SIGKILL.
- **D-03:** Signal handler lives in `llm_invoke.py` internal (module-level `_active_proc`). Each `_invoke_cli` call sets `_active_proc = proc`, clears in `finally`. Signal handler installed once at module level, chains to previous handler (lock.py pattern). Self-contained, no cross-module coupling.
- **D-04:** `_kill_tree(proc)` helper: `os.killpg(proc.pid, signal.SIGTERM)` -> `proc.wait(timeout=5)` -> `os.killpg(proc.pid, signal.SIGKILL)`. Kills child + grandchildren (e.g. `claude -p` may spawn sub-processes).
- **D-05:** api path (`urllib.request.urlopen`) has no orphan subprocess issue -- OS handles TCP cleanup when parent dies. No changes needed for api backends in CLI-07.

### CLI-08: Post-Review Cost Summary
- **D-06:** `llm_invoke` return type changes from `Any` to `LLMResult(content: Any, usage: Usage, duration_s: float)`. Breaking change -- all callers (factories.py, falsify_real.py) must access `result.content` instead of `result` directly.
- **D-07:** `Usage` dataclass: `input_tokens: int = 0, output_tokens: int = 0`. Populated from api response (`resp_data["usage"]` for both openai and anthropic formats). For cli backends, set to 0 (cli stdout is content-only; token data not available from `claude -p`).
- **D-08:** Cost data written to `state.json` after review completes. New `cost` key: `{total_input_tokens, total_output_tokens, total_duration_s, passes, per_pass: [{pass, cycle, input, output, duration_s}]}`. Accumulation happens in StateMachine or cli.py during drive loop.
- **D-09:** stderr summary line printed after review: `code-forge: cost: N tokens (X in + Y out), P passes, T.Ts`. Both structured (state.json) and human-readable (stderr).

### BOTH-02: Configuration Documentation
- **D-10:** Two-layer structure: README.md gets a Quick Start section (most common config), detailed guides go in new `docs/` directory.
- **D-11:** docs/ files: `docs/configuration.md` (env vars, backends.yaml format, auth), per-editor setup guides (`docs/setup-vscode.md`, `docs/setup-cursor.md`, `docs/setup-pycharm.md`).
- **D-12:** Covers: `FORGE_BACKEND`, `FORGE_OUTLET`, `FORGE_LLM_MODEL` env vars, backend yaml format, auth setup per editor.

### Claude's Discretion
- StateMachine vs cli.py for cost accumulation (D-08 implementation detail)
- Exact README Quick Start wording and structure
- Whether to include a `docs/setup-terminal.md` alongside editor guides

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Subprocess & Signal Handling
- `src/code_forge/llm_invoke.py` -- current subprocess.run call (line 123), to be replaced with Popen + process group
- `src/code_forge/lock.py` -- signal handler chain pattern (lines 73-98), reuse for _on_signal

### Cost Data Flow
- `src/code_forge/machine.py` -- StateMachine drive loop, where per-pass results accumulate
- `src/code_forge/factories.py` -- l1_provider calls llm_invoke (line 227), must adapt to LLMResult
- `src/code_forge/falsify_real.py` -- RealFalsifier calls llm_invoke (line 44), must adapt to LLMResult
- `src/code_forge/cli.py` -- state.json write path (line 698), add cost section

### Requirements
- `.planning/REQUIREMENTS.md` -- CLI-07, CLI-08, BOTH-02 definitions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `lock.py` signal handler chain (`_make_chained_handler`): same pattern for subprocess cleanup handler
- `LLMInvokeError` dataclass already has `duration_s` field: extend to LLMResult for success path
- `state.json` write path in `cli.py`: natural place to add cost section

### Established Patterns
- `BackendConfig` frozen dataclass: LLMResult and Usage should follow same pattern
- `_strip_fences` helper extraction (Phase 7): same approach for `_kill_tree` helper
- Module-level state in llm_invoke.py: `DEFAULT_TIMEOUT_S`, `DEFAULT_MODEL` -- `_active_proc` fits this pattern

### Integration Points
- `llm_invoke() -> LLMResult` return type change touches: factories.py:227, falsify_real.py:44, all tests
- `state.json` cost section consumed by: SKILL.md (reads state after CLI exits), CI output

</code_context>

<specifics>
## Specific Ideas

- `_kill_tree` escalation: SIGTERM first (grace period for cleanup), SIGKILL after 5s (hard kill)
- stderr cost line format: `code-forge: cost: 58030 tokens (45230 in + 12800 out), 9 passes, 187.3s`
- state.json cost structure should include per-pass breakdown for debugging slow passes

</specifics>

<deferred>
## Deferred Ideas

- **Windows IDE support** -- tracked in ROADMAP.md Deferred section. Phase 8 ships Unix-first; Windows port gated on user demand.
- **backends.yaml loading** -- configuration file loading deferred from Phase 7, could land in Phase 8 docs scope but implementation is separate.
- **Cost estimation** ($ dollar amounts) -- requires per-model pricing tables. Phase 8 reports raw tokens; dollar estimation is a future enhancement.

</deferred>

---

*Phase: 08-hardening*
*Context gathered: 2026-06-02*
