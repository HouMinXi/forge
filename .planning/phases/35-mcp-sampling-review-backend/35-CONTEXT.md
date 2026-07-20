# Phase 35: MCP Sampling Review Backend - Context

**Gathered:** 2026-06-30
**Status:** Ready for planning
**Source:** ADR Ingest Express Path (docs/adr/0007-mcp-sampling-review-backend.md)

<domain>
## Phase Boundary

Add MCP sampling as a new outlet type so the forge review pipeline can use the
client's model (Copilot Pro, Claude Max subscription) instead of requiring a
separate API key. Phase 1 only: explicit opt-in via `outlet: sampling` in
gate.yaml or `FORGE_OUTLET=sampling` env var. Auto-detect deferred to Phase 2
(after Claude Code ships sampling support).

</domain>

<decisions>
## Implementation Decisions

### Outlet Type
- Sampling is a new outlet alongside `api` and `inline`, resolved by outlet_resolver
- Explicit opt-in only: `outlet: sampling` in gate.yaml or `FORGE_OUTLET=sampling`
- No API key required for sampling outlet

### Inference Transport
- Use `ctx.session.create_message()` (ServerSession method, NOT Context.sample())
- Pass system_prompt, messages, max_tokens, temperature, model_preferences, tools, tool_choice
- Model hints via ModelPreferences(hints=[ModelHint(name="claude-sonnet")], intelligencePriority=0.8)
- Fallback: if create_message raises, return clear ToolError

### Machine Threading
- machine.py accepts optional `mcp_session: ServerSession` from MCP tool handler
- Only threaded when outlet is sampling; None for API and inline outlets
- Review pipeline (passes, convergence, state) unchanged -- only transport differs

### MCP Server Integration
- Tool handlers pass `ctx.session` to StateMachine when outlet is sampling
- forge_review and forge_gate_check are the two entry points

### Error Handling
- Client doesn't support sampling: clear ToolError with guidance
- Truncation (stopReason != "endTurn"): fall back to API backend if configured
- Rate limiting: surface client error to user

### Claude's Discretion
- Internal function naming and module placement within llm_invoke.py
- Test fixture design for mocking ServerSession.create_message()
- Whether to batch multi-pass prompts or send sequentially

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture Decisions
- `docs/adr/0007-mcp-sampling-review-backend.md` -- full ADR with resolved questions, SDK verification, implementation sketch
- `docs/adr/0006-mcp-workspace-resolution.md` -- workspace resolution (prerequisite, already shipped)
- `docs/adr/0003-customer-supplied-backend.md` -- existing backend architecture

### Source Files (read before modifying)
- `src/code_forge/llm_invoke.py` -- add _invoke_sampling alongside _invoke_openai/_anthropic/_vertex
- `src/code_forge/outlet_resolver.py` -- add sampling outlet type recognition
- `src/code_forge/machine.py` -- thread mcp_session through StateMachine
- `src/code_forge/mcp_server.py` -- pass ctx.session from tool handlers

### SDK Types (verified in mcp 1.27.0)
- `mcp.server.session.ServerSession.create_message()` -- the actual sampling call
- `mcp.types.SamplingMessage` -- role + content
- `mcp.types.ModelPreferences` -- hints + priority values
- `mcp.types.ModelHint` -- name substring
- `mcp.types.CreateMessageResult` -- response with role, content, model, stopReason

</canonical_refs>

<specifics>
## Specific Ideas

- ServerSession.create_message signature (verified):
  `(self, messages, *, max_tokens, system_prompt=None, temperature=None, model_preferences=None, tools=None, tool_choice=None, include_context=None, ...)`
- VS Code pre-authorizes models per-server (model picker), not per-call dialog
- forge's existing review prompts (system + user + structured output) map directly to create_message params
- Estimated diff: ~120 lines across 4 files

</specifics>

<deferred>
## Deferred Ideas

- Phase 2 auto-detect: when all target clients support sampling, outlet_resolver auto-selects sampling if no API backend configured
- Batching multi-pass prompts to reduce sampling round-trips
- Per-pass model hints (different model for L0 vs L1)

</deferred>

<scope_fence>
## Scope Fence (from ADR)

IN SCOPE:
- _invoke_sampling in llm_invoke.py
- outlet_resolver sampling type
- machine.py mcp_session threading
- mcp_server.py ctx passthrough
- Tests with mocked create_message
- Real-path smoke from VS Code

OUT OF SCOPE:
- Auto-detect (Phase 2)
- CLI path (sampling only works through MCP)
- Changes to review pipeline logic (passes, convergence, state)

</scope_fence>

---

*Phase: 35-mcp-sampling-review-backend*
*Context gathered: 2026-06-30 via ADR Ingest Express Path*
