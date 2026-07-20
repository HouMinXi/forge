# ADR-0007: MCP sampling as a review backend

**Status**: proposed
**Date**: 2026-06-30
**Supersedes**: none
**Related**: ADR-0003 (customer-supplied backend), ADR-0006 (workspace resolution)

## Context

forge's MCP server receives `forge_review` from a client (VS Code Copilot,
Claude Code, etc.), then independently calls an LLM API (DeepSeek, Vertex,
etc.) with its own API key. The client's model access is invisible to forge
-- MCP is one-directional (client calls server tools).

This creates friction: users who already pay for Copilot Pro or Claude Max
must configure and pay for a separate API key just for forge review. Three
users have asked "why can't forge use the model I already have?"

MCP spec defines `sampling` -- the server sends `sampling/createMessage` to
the client, requesting the client run an LLM inference and return the result.
This inverts the flow: forge asks the client's model to do the review,
using the client's existing subscription.

### Client support (verified 2026-06-30)

| Client | Sampling | Source |
|--------|----------|--------|
| VS Code + Copilot | YES | code.visualstudio.com/blogs/2025/06/12/full-mcp-spec-support |
| Visual Studio (Windows) | YES | devblogs.microsoft.com/visualstudio/mcp-prompts-resources-sampling/ |
| Claude Code | NO (feature request) | github.com/anthropics/claude-code/issues/1785 (121 thumbs up, assigned, no ETA) |
| JetBrains / PyCharm | NOT CONFIRMED | MCP in preview, only tools primitive documented |
| Cursor | NOT CONFIRMED | no public docs on sampling |

### SDK availability

FastMCP `Context.sample()` exists in the installed SDK. The server calls
`await ctx.sample(prompt)` and receives `SamplingResult.text`. The client
shows a confirmation dialog (VS Code) before running the inference.

## Decision

Add MCP sampling as a new outlet type alongside the existing API backend.
The outlet resolver gains a third option:

    Priority  Outlet         Mechanism                        Key needed
    --------  -------------  -------------------------------  ----------
    1         gate.yaml API  Direct API call (existing)       YES
    2         sampling       ctx.sample() via MCP client      NO
    3         inline         Same model reviews itself        NO (but not independent)

### Phase 1: sampling outlet behind explicit opt-in

Do NOT auto-detect. The user enables it in gate.yaml:

    review:
      outlet: sampling

Or via env var: `FORGE_OUTLET=sampling`.

When enabled, `llm_invoke.py` constructs the review prompt (same as the
API path), calls `ctx.sample(prompt)`, and parses the response. The review
pipeline (machine.py passes, convergence, state) is unchanged -- only the
inference transport differs.

### Phase 2: auto-detect (only after Claude Code ships sampling)

When all three target clients support sampling, the outlet resolver can
auto-detect: if no gate.yaml API backend is configured AND the client
advertises sampling capability, use sampling. This is the zero-config path.

## Alternatives Considered

### Alternative 1: Do nothing -- users provide their own API key
- **Pros**: already works, no code changes
- **Cons**: friction for Copilot/Max subscribers, extra cost, extra config
- **Why not**: the core ask ("use the model I already have") is legitimate
  and MCP sampling exists precisely for this

### Alternative 2: Auto-detect sampling as default
- **Pros**: zero-config for VS Code users
- **Cons**: Claude Code (the primary user base) doesn't support it yet;
  auto-detect silently changes behavior; sampling adds a user confirmation
  dialog per inference (VS Code), which may be disruptive in a 3-pass review
- **Why not**: premature until client coverage is broader; explicit opt-in
  first

### Alternative 3: Proxy the client's API key to forge's own call
- **Pros**: no protocol change
- **Cons**: API keys are not transferable between clients; Copilot's model
  access is not exposed as a standard API endpoint
- **Why not**: architecturally impossible -- Copilot's models are behind
  its own infrastructure

## Consequences

### Positive
- VS Code + Copilot users: forge review with zero API key, zero extra cost
- Visual Studio users: same benefit
- Removes the biggest onboarding friction ("why do I need another API key?")
- Uses MCP as designed -- sampling is the protocol answer to this exact problem

### Negative
- VS Code sampling shows a confirmation dialog per `ctx.sample()` call.
  A 3-pass review = 3 dialogs minimum. May need batching or user guidance
  to reduce friction.
- Sampling delegates model choice to the client. forge cannot control which
  model runs the review (Copilot may route to GPT-4o, not Claude). Review
  quality depends on the client's model.
- Claude Code users get no benefit until Anthropic ships #1785.

### Risks
- Client-side rate limiting or token caps may truncate large diffs.
  Mitigation: fall back to API backend on sampling error.
- Sampling is a newer MCP feature; client implementations may have bugs.
  Mitigation: explicit opt-in (Phase 1), not auto-detect.

## Scope challenge

- Does this need to exist? Yes. Three users asked. VS Code sampling is
  shipped. The friction is real and the protocol solution exists.
- Three real consumers: VS Code + Copilot users of forge, Visual Studio
  users of forge, future Claude Code users (when #1785 ships).
- Do-nothing cost: every forge MCP user must configure and pay for a
  separate API key, even when they already have a model subscription.

## Implementation sketch (Phase 1)

Architecture change is isolated to the inference transport:

1. `llm_invoke.py`: add `_invoke_sampling(ctx, prompt)` alongside
   `_invoke_openai`, `_invoke_anthropic`, `_invoke_vertex`.
2. `outlet_resolver.py`: recognize `outlet: sampling` in gate.yaml.
3. `machine.py`: thread `ctx: Context` from the MCP tool handler through
   the state machine to `llm_invoke`. Currently the machine receives `cwd`;
   it would also receive an optional `mcp_context`.
4. Tool handlers (`forge_review`, `forge_gate_check`): pass `ctx` when
   outlet is sampling.
5. Tests: mock `ctx.sample()`, verify prompt construction and response
   parsing. Real-path smoke: call `forge_review` from VS Code with
   `outlet: sampling`, confirm the Copilot confirmation dialog appears.

Estimated diff: ~100 lines across 4 files. The review pipeline, pass logic,
convergence, and state machine are untouched -- only the transport changes.

## Resolved questions (verified 2026-06-30 against MCP spec + SDK 1.27.0)

**Q1: VS Code confirmation dialog frequency?**

VS Code shows a model picker (pre-authorization per server, not per-call).
Users select which models a server may use for sampling. After that, calls
proceed without per-call dialogs. Visual Studio (Windows) does show a
per-call confirmation dialog. The MCP spec says "Implementations are free
to expose sampling through any interface pattern" -- it does not mandate
per-call approval. Source: VS Code blog 2025-06-12 screenshot shows a
model picker, not a per-call dialog.

**Verdict**: not a blocker. VS Code UX is acceptable for multi-pass review.

**Q2: System prompt and structured output support?**

YES. `ServerSession.create_message()` accepts (verified in SDK):

    system_prompt: str | None          -- forge's review system prompt
    messages: list[SamplingMessage]    -- user messages (diff + instructions)
    temperature: float | None          -- forge sets 0.0 for determinism
    max_tokens: int                    -- forge's output cap
    tools: list[Tool] | None           -- for structured output via tool_use
    tool_choice: ToolChoice | None     -- force tool call for JSON parsing
    model_preferences: ModelPreferences | None  -- hints + priorities
    include_context: "none"|"thisServer"|"allServers" | None

This is a FULL inference API, not a flat prompt string. forge's existing
review prompt (system + user + structured output) maps directly.

**Verdict**: no architectural gap. forge's prompts work as-is.

**Q3: Token limits?**

The server specifies `max_tokens` in the request. The actual ceiling
depends on the client's model (Copilot's Claude Sonnet: 8192 output tokens
typical). The client may silently cap. Large diffs are already chunked by
forge's diff splitting logic before they reach `llm_invoke`.

**Verdict**: existing diff chunking handles this. Add a fallback to API
backend if sampling returns a truncation signal (`stopReason != "endTurn"`).

**Q4: Model control?**

YES, via `ModelPreferences`:

    model_preferences=ModelPreferences(
        hints=[ModelHint(name="claude-sonnet")],
        intelligencePriority=0.8,
        speedPriority=0.5,
        costPriority=0.3,
    )

Hints are substring-matched and advisory. The client makes final selection
but SHOULD respect hints. forge can hint "claude-sonnet" for review quality
and the client maps to its best available match.

**Verdict**: forge can express model preference. Not a guarantee, but
sufficient for review quality. Document that review quality depends on
the client's available models.

## Revised implementation (all questions resolved)

No blockers remain. The SDK API (`ServerSession.create_message`) maps
directly to forge's inference needs. The implementation path:

1. `llm_invoke.py`: add `_invoke_sampling(session, system_prompt, messages,
   max_tokens, model_preferences)` that calls `session.create_message()`.
2. `outlet_resolver.py`: recognize `outlet: sampling`. No API key required.
3. `machine.py`: accept optional `mcp_session: ServerSession` from tool
   handler. Pass to `llm_invoke` when outlet is sampling.
4. `mcp_server.py`: tool handlers pass `ctx.session` to the machine when
   outlet is sampling.
5. Fallback: if `create_message` raises (client doesn't support sampling),
   return a clear ToolError: "Client does not support MCP sampling. Set an
   API backend in gate.yaml or use a client that supports sampling."

FastMCP `Context` does NOT have a `sample()` convenience method. Use
`ctx.session.create_message()` directly (verified in SDK).

Estimated diff: ~120 lines across 4 files. Review pipeline unchanged.
