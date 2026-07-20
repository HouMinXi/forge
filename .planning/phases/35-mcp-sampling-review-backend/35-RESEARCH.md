# Phase 35: MCP Sampling Review Backend - Research

**Researched:** 2026-06-30
**Domain:** MCP sampling protocol, IDE client capabilities, inference transport abstraction
**Confidence:** HIGH

## Summary

MCP sampling (`sampling/createMessage`) lets a server ask the client to run an LLM
inference and return the result. forge currently uses direct API calls (openai/anthropic/
vertex) or CLI subprocess for review inference. This phase adds sampling as a third
transport, so users with Copilot Pro or Claude Max can run forge review without a
separate API key.

The SDK surface is fully verified (mcp 1.27.0): `ServerSession.create_message()` accepts
system_prompt, messages, max_tokens, temperature, model_preferences, tools, tool_choice
-- a complete inference API matching forge's needs. The client advertises sampling support
via `ClientCapabilities.sampling` during initialization, and forge can check it at
runtime via `session.check_client_capability()`. The change is isolated: add one
`_invoke_sampling()` function in llm_invoke.py, add "sampling" to outlet_resolver.py's
allow-list, thread an optional `mcp_session` through machine.py, and pass `ctx.session`
from the two MCP tool handlers.

**Primary recommendation:** Add sampling as a new outlet type behind explicit opt-in.
The key design decision: do NOT change the `llm_invoke()` public signature. Instead,
add a standalone `async` function `invoke_sampling()` that the MCP tool handlers call
directly when outlet is "sampling", bypassing the synchronous `llm_invoke()` entirely.
This keeps the async boundary clean and the existing synchronous pipeline untouched.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Sampling is a new outlet alongside `api` and `inline`, resolved by outlet_resolver
- Explicit opt-in only: `outlet: sampling` in gate.yaml or `FORGE_OUTLET=sampling`
- No API key required for sampling outlet
- Use `ctx.session.create_message()` (ServerSession method, NOT Context.sample())
- Pass system_prompt, messages, max_tokens, temperature, model_preferences, tools, tool_choice
- Model hints via ModelPreferences(hints=[ModelHint(name="claude-sonnet")], intelligencePriority=0.8)
- Fallback: if create_message raises, return clear ToolError
- machine.py accepts optional `mcp_session: ServerSession` from MCP tool handler
- Only threaded when outlet is sampling; None for API and inline outlets
- Review pipeline (passes, convergence, state) unchanged -- only transport differs
- Tool handlers pass `ctx.session` to StateMachine when outlet is sampling
- forge_review and forge_gate_check are the two entry points
- Client doesn't support sampling: clear ToolError with guidance
- Truncation (stopReason != "endTurn"): fall back to API backend if configured
- Rate limiting: surface client error to user

### Claude's Discretion
- Internal function naming and module placement within llm_invoke.py
- Test fixture design for mocking ServerSession.create_message()
- Whether to batch multi-pass prompts or send sequentially

### Deferred Ideas (OUT OF SCOPE)
- Phase 2 auto-detect: when all target clients support sampling, outlet_resolver auto-selects sampling if no API backend configured
- Batching multi-pass prompts to reduce sampling round-trips
- Per-pass model hints (different model for L0 vs L1)
</user_constraints>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Outlet resolution (sampling) | outlet_resolver.py | -- | Pure config precedence, no network |
| Inference transport (sampling) | llm_invoke.py | mcp_server.py | llm_invoke owns all inference dispatch; mcp_server provides the session |
| Session threading | machine.py | mcp_server.py | Machine is the pipeline orchestrator; MCP handlers are the session source |
| Capability detection | mcp_server.py | -- | Tool handlers have ctx.session, detect before dispatch |
| Review pipeline logic | machine.py | -- | Unchanged -- only transport differs |

## Standard Stack

### Core (already installed, no new packages)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mcp | 1.27.0 | MCP SDK: ServerSession, SamplingMessage, ModelPreferences, CreateMessageResult | Already installed; provides create_message() [VERIFIED: pip show] |

No new packages needed. The entire implementation uses types already in the installed mcp SDK.

### SDK Types (verified against mcp 1.27.0)

| Type | Import | Purpose |
|------|--------|---------|
| `ServerSession` | `mcp.server.session` | Holds `create_message()` and `check_client_capability()` |
| `SamplingMessage` | `mcp.types` | Message envelope: role + content (TextContent/ImageContent/AudioContent) |
| `TextContent` | `mcp.types` | Text content block for SamplingMessage |
| `ModelPreferences` | `mcp.types` | hints + costPriority/speedPriority/intelligencePriority |
| `ModelHint` | `mcp.types` | name: str (substring matched by client) |
| `CreateMessageResult` | `mcp.types` | Response: role, content, model, stopReason |
| `ClientCapabilities` | `mcp.types` | For check_client_capability() -- has `sampling` field |
| `SamplingCapability` | `mcp.types` | Sub-capability: context + tools fields |

## Architecture Patterns

### System Architecture Diagram

```
  IDE Client (VS Code / JetBrains / future Claude Code)
      |
      | stdio MCP transport
      v
  mcp_server.py
      |  forge_review() / forge_gate_check()
      |  reads outlet from gate.yaml / FORGE_OUTLET
      |
      +--[ outlet == "sampling" ]---> invoke_sampling(session, prompt, ...)
      |      |                              |
      |      |  session.create_message()    | SamplingMessage + ModelPreferences
      |      |                              v
      |      |                        Client's LLM (Copilot/Claude Max)
      |      |                              |
      |      |  <--- CreateMessageResult ---+
      |      |  content.text -> parse JSON -> LLMResult
      |      v
      +--[ outlet != "sampling" ]---> existing path:
      |      _run_cli_budgeted("code-forge", "review", ...)
      |          -> cli.py -> llm_invoke() -> API / CLI subprocess
      |
      v
  machine.py (unchanged pipeline: L0 -> L1 -> L2 -> E2E -> convergence)
```

### Key Insight: Async Boundary

The existing `llm_invoke()` is synchronous (stdlib urllib). `create_message()` is async.
Rather than making `llm_invoke()` async (which would cascade changes through the entire
pipeline), the sampling path runs directly in the MCP tool handlers (which are already
async). The tool handlers call `invoke_sampling()` instead of `_run_cli_budgeted()`.

This means: when outlet is "sampling", the MCP tool handlers do NOT spawn a `code-forge`
CLI subprocess. Instead they run the review pipeline in-process (async), calling
`create_message()` for each L1 pass. This is architecturally similar to the existing
"inline" outlet but using the client's model via sampling instead of the session model.

**Two viable approaches for the in-process path:**

**Option A (simpler, recommended):** The MCP tool handlers detect `outlet == "sampling"`,
import the review pipeline modules directly, and run them in-process with
`invoke_sampling()` as the inference function. The pipeline modules already support
dependency injection (l1_provider is a callable, backend is a parameter).

**Option B (threading session through CLI):** Thread `mcp_session` through the CLI
subprocess path. Rejected because `_run_cli_budgeted` spawns a child process -- you
cannot pass a live ServerSession across process boundaries.

The CONTEXT.md says "machine.py accepts optional mcp_session." This works with Option A:
the MCP handler constructs a StateMachine directly (not via CLI subprocess), passing
the session. The l1_provider callable uses `invoke_sampling()` instead of `llm_invoke()`.

### Pattern 1: Capability Detection Before Dispatch

```python
# Source: verified against mcp SDK 1.27.0 ServerSession.check_client_capability()
from mcp.types import ClientCapabilities, SamplingCapability

def _client_supports_sampling(session: ServerSession) -> bool:
    """Check if the connected client advertises sampling capability."""
    return session.check_client_capability(
        ClientCapabilities(sampling=SamplingCapability())
    )
```

This returns False when `_client_params.capabilities.sampling is None` (client did not
advertise sampling during initialization). [VERIFIED: SDK source inspection]

### Pattern 2: Sampling Invocation

```python
# Source: verified against mcp SDK 1.27.0 ServerSession.create_message()
from mcp.types import (
    SamplingMessage, TextContent, ModelPreferences, ModelHint,
    CreateMessageResult,
)

async def invoke_sampling(
    session: ServerSession,
    prompt: str,
    *,
    system_prompt: str = "",
    max_tokens: int = 16384,
    temperature: float = 0.0,
    model_hint: str = "claude-sonnet",
) -> LLMResult:
    """Invoke LLM via MCP sampling. Returns LLMResult (same as llm_invoke)."""
    messages = [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=prompt),
        )
    ]
    model_prefs = ModelPreferences(
        hints=[ModelHint(name=model_hint)],
        intelligencePriority=0.8,
        speedPriority=0.5,
        costPriority=0.3,
    )
    result: CreateMessageResult = await session.create_message(
        messages,
        max_tokens=max_tokens,
        system_prompt=system_prompt or None,
        temperature=temperature,
        model_preferences=model_prefs,
    )
    # Extract text content
    if hasattr(result.content, "text"):
        text = result.content.text
    else:
        text = str(result.content)
    # Check for truncation
    if result.stopReason not in ("endTurn", None):
        # stopReason is "maxTokens" or "stopSequence" -- truncated
        pass  # caller handles fallback
    return LLMResult(content=_parse_sampling_response(text), usage=Usage())
```

### Pattern 3: Outlet Resolution Extension

```python
# In outlet_resolver.py -- extend VALID_OUTLET_STRINGS
VALID_OUTLET_STRINGS = {
    "subprocess": "subprocess",
    "inline": "inline",
    "subagent": "subagent",
    "sampling": "sampling",  # new
}
```

The `_parse_outlet_string()` function already handles validation via this dict.
Adding the key is the entire change. [VERIFIED: outlet_resolver.py source]

### Anti-Patterns to Avoid

- **Making llm_invoke() async:** Would cascade async/await through machine.py,
  factories.py, cli.py, and every caller. The sampling path should stay in the
  MCP handler's async context, not infect the synchronous pipeline.
- **Passing ServerSession through subprocess boundaries:** `_run_cli_budgeted`
  spawns a child process. A ServerSession is an in-process object with a live
  transport connection; it cannot be serialized or passed to a subprocess.
- **Auto-detecting sampling in Phase 1:** Claude Code doesn't support it yet.
  Auto-detect would silently change behavior for VS Code users when they
  haven't opted in. Explicit opt-in first.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Capability negotiation | Custom handshake protocol | `session.check_client_capability()` | SDK handles the init exchange and capability caching |
| Model preference encoding | Custom model selection logic | `ModelPreferences` + `ModelHint` | MCP spec defines the abstraction; clients implement the mapping |
| Response parsing (content) | Custom content type dispatch | `result.content.text` (TextContent) | SDK types are already typed unions |
| Async sampling call | `asyncio.run()` wrapper | Direct `await` in MCP handler | Tool handlers are already async |

## Client Sampling Support Matrix

| Client | Sampling Support | Capability Detection | Model Picker UX | Source |
|--------|-----------------|---------------------|-----------------|--------|
| VS Code + Copilot | YES | Advertises `sampling` capability | Per-server model pre-authorization (not per-call) | [CITED: code.visualstudio.com/blogs/2025/06/12] |
| Visual Studio (Windows) | YES | Advertises `sampling` capability | Per-call confirmation dialog | [CITED: devblogs.microsoft.com/visualstudio/mcp-prompts-resources-sampling] |
| JetBrains + Copilot plugin | YES (via Copilot plugin v1.5.57+) | Advertises `sampling` capability | Settings -> MCP -> MCP Sampling -> Allowed Models | [CITED: devblogs.microsoft.com/java/unlocking-mcp-in-jetbrains] |
| Claude Code | NO | Does not advertise `sampling` | N/A | [CITED: github.com/anthropics/claude-code/issues/1785, 121 thumbs up, assigned, OPEN] |
| Cursor | NO | Not documented | N/A | [CITED: forum.cursor.com/t/mcp-sampling-support/149604, feature request Jan 2026] |
| Codex CLI (OpenAI) | NO | Not documented, tools-only | N/A | [CITED: developers.openai.com/codex/mcp -- no sampling in supported features; github.com/openai/codex/issues/4929 feature request] |

**Key finding:** JetBrains has sampling support via the Copilot plugin (not native
JetBrains MCP). This means sampling works in IntelliJ/PyCharm/WebStorm when users
have the GitHub Copilot plugin installed, expanding the addressable market beyond
just VS Code.

**Future-proofing assessment:** Claude Code #1785 has 121 thumbs up, is labeled
`enhancement` + `area:mcp`, and an Anthropic engineer asked for use cases. The
Cursor forum request has 235 views. Codex has a GitHub issue (#4929). All three
major holdouts have active feature requests. forge's `check_client_capability()`
runtime detection means zero code changes are needed when any of these ships
sampling -- the existing outlet_resolver + capability check handles it.

## Failure Modes Unique to Sampling

| Failure Mode | Detection | Mitigation |
|-------------|-----------|------------|
| Client doesn't support sampling | `check_client_capability()` returns False | ToolError with guidance: "Client does not support MCP sampling. Set outlet: api in gate.yaml or use VS Code with Copilot." |
| User rejects sampling request | `McpError` with "User rejected sampling request" | Surface as ToolError. VS Code pre-authorizes (no per-call dialog), so this is rare there. |
| Truncation (maxTokens hit) | `result.stopReason == "maxTokens"` | Log warning. If API backend also configured, fall back to API for that pass. |
| Client rate limiting | `McpError` or timeout from client | Surface error to user. No retry -- the client controls the rate. |
| Model mismatch (Copilot routes to GPT-4o instead of Claude) | `result.model` field in CreateMessageResult | Log the actual model used. Review quality depends on client's model -- document this in user-facing docs. |
| Network timeout on sampling request | asyncio timeout / transport error | ToolError with retry guidance. |
| Empty/malformed response | `result.content` is not TextContent or text is empty | Same JSON parse fallback as API path (`_strip_fences` + `_extract_json_from_text`). |

## Common Pitfalls

### Pitfall 1: Mixing Sync and Async
**What goes wrong:** `llm_invoke()` is sync; `create_message()` is async. Wrapping
async in `asyncio.run()` inside an already-running event loop raises RuntimeError.
**Why it happens:** The MCP server runs on asyncio; nesting `asyncio.run()` is illegal.
**How to avoid:** Keep `invoke_sampling()` as a standalone async function. Call it with
`await` from the already-async MCP tool handlers. Never import it into the sync pipeline.
**Warning signs:** `RuntimeError: This event loop is already running`.

### Pitfall 2: Subprocess Path for Sampling
**What goes wrong:** `_run_cli_budgeted` spawns `code-forge review` as a subprocess.
The subprocess has no access to the MCP session -- it's a separate process.
**Why it happens:** The current MCP tool handlers delegate ALL work to a subprocess.
**How to avoid:** When outlet is "sampling", the tool handler must run the review
pipeline in-process (importing machine.py, factories.py directly) rather than spawning
a subprocess. The subprocess path remains for "subprocess" and "subagent" outlets.
**Warning signs:** "No MCP session available" errors from the subprocess.

### Pitfall 3: No Usage Tracking for Sampling
**What goes wrong:** `create_message()` returns `CreateMessageResult` which has NO
usage/token-count fields. forge's cost tracking (`cost_total_input`, `cost_total_output`)
gets zeros for sampling passes.
**Why it happens:** MCP sampling delegates inference to the client. The client pays;
token counts are not exposed back to the server.
**How to avoid:** Return `Usage(0, 0)` for sampling. Document that cost tracking is
not available for the sampling outlet (the client's billing handles it).
**Warning signs:** Cost reports showing 0 tokens for sampling-backed reviews.

### Pitfall 4: SamplingMessage Content Type
**What goes wrong:** `SamplingMessage.content` accepts TextContent | ImageContent |
AudioContent | ToolUseContent | ToolResultContent | list[...]. Passing a plain string
fails validation.
**Why it happens:** Unlike the simpler API format where messages are `{"role": "user",
"content": "text"}`, MCP requires typed content blocks.
**How to avoid:** Always wrap text in `TextContent(type="text", text=prompt)`.
**Warning signs:** Pydantic validation errors on SamplingMessage construction.

## Code Examples

### Example 1: Full Sampling Invocation (verified types)

```python
# Source: mcp SDK 1.27.0 types + ServerSession.create_message() signature
import asyncio
from mcp.server.session import ServerSession
from mcp.types import (
    ClientCapabilities,
    CreateMessageResult,
    ModelHint,
    ModelPreferences,
    SamplingCapability,
    SamplingMessage,
    TextContent,
)

async def invoke_sampling(
    session: ServerSession,
    prompt: str,
    *,
    system_prompt: str | None = None,
    max_tokens: int = 16384,
    temperature: float = 0.0,
    model_hint: str = "claude-sonnet",
) -> tuple[str, str | None]:
    """Call client's LLM via MCP sampling. Returns (text, stopReason)."""
    messages = [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=prompt),
        )
    ]
    result = await session.create_message(
        messages,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        temperature=temperature,
        model_preferences=ModelPreferences(
            hints=[ModelHint(name=model_hint)],
            intelligencePriority=0.8,
            speedPriority=0.5,
            costPriority=0.3,
        ),
    )
    text = result.content.text if hasattr(result.content, "text") else ""
    return (text, result.stopReason)
```

### Example 2: Capability Check (verified)

```python
# Source: mcp SDK 1.27.0 ServerSession.check_client_capability()
from mcp.types import ClientCapabilities, SamplingCapability

def supports_sampling(session: ServerSession) -> bool:
    return session.check_client_capability(
        ClientCapabilities(sampling=SamplingCapability())
    )
```

### Example 3: Test Mock Pattern

```python
# Pattern for testing without a real MCP client
from unittest.mock import AsyncMock, MagicMock
from mcp.types import CreateMessageResult, TextContent

mock_session = MagicMock(spec=ServerSession)
mock_session.create_message = AsyncMock(return_value=CreateMessageResult(
    role="assistant",
    content=TextContent(type="text", text='{"findings": [], "code_excerpts": []}'),
    model="claude-sonnet-4-6",
    stopReason="endTurn",
))
mock_session.check_client_capability = MagicMock(return_value=True)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Server calls external API with own key | Server requests client inference via sampling | MCP spec 2024-11 (draft), 2025-06 (VS Code ships) | Server needs no API key; uses client's subscription |
| Flat prompt string sampling | Full inference API (system_prompt, tools, model_preferences) | MCP spec 2025-06-18 revision | Servers can request structured output, specific models |
| Per-call confirmation dialog | Per-server model pre-authorization | VS Code June 2025 | Multi-call workflows (like 3-pass review) are practical |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | JetBrains sampling works via Copilot plugin, not native JetBrains MCP | Client Support Matrix | forge docs may incorrectly claim JetBrains native support; users without Copilot plugin would get a confusing error |
| A2 | VS Code model pre-authorization persists across tool calls within a session | Failure Modes | If it doesn't persist, users get 3 confirmation dialogs per review (one per pass) -- still works but friction |
| A3 | CreateMessageResult.content is always TextContent for text prompts (not ImageContent/AudioContent) | Code Examples | If client returns non-text content for a text prompt, content.text access fails -- add isinstance check |

## Open Questions (RESOLVED)

1. **In-process review pipeline for sampling outlet**
   - What we know: sampling requires in-process async execution (no subprocess).
     The existing pipeline modules (factories.py, machine.py) are sync.
   - What's unclear: exact integration point -- should the MCP handler
     construct a StateMachine directly, or should there be a thinner
     adapter that just replaces llm_invoke calls?
   - Recommendation: The thinner approach. The MCP handler builds an
     l1_provider that calls `invoke_sampling()` (wrapped in the event loop),
     and passes it to the existing StateMachine. The StateMachine runs
     synchronously in `asyncio.to_thread()`. This preserves the existing
     pipeline and isolates the async boundary.

2. **Usage/token tracking for sampling**
   - What we know: CreateMessageResult has no usage field. Token counts are
     unavailable for sampling.
   - What's unclear: Should forge's cost reporting show "N/A" or "0" for
     sampling passes?
   - Recommendation: Usage(0, 0) with a log line "sampling: token usage
     not available (billed by client)". Cost-per-pass entries still recorded
     with 0 tokens. This keeps the data model consistent.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| mcp SDK | Sampling types + ServerSession | Yes | 1.27.0 | -- |
| pytest | Test suite | Yes | (installed) | -- |
| asyncio | Async sampling calls | Yes | stdlib | -- |

**Missing dependencies:** None. All required packages are already installed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | pyproject.toml / pytest section |
| Quick run command | `pytest tests/test_llm_invoke.py tests/test_outlet_resolver.py tests/test_mcp_server.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SAMP-01 | outlet_resolver accepts "sampling" | unit | `pytest tests/test_outlet_resolver.py -x -q -k sampling` | Needs new tests |
| SAMP-02 | invoke_sampling calls create_message correctly | unit | `pytest tests/test_llm_invoke.py -x -q -k sampling` | Needs new tests |
| SAMP-03 | capability check returns False for non-sampling client | unit | `pytest tests/test_mcp_server.py -x -q -k capability` | Needs new tests |
| SAMP-04 | truncation detected via stopReason | unit | `pytest tests/test_llm_invoke.py -x -q -k truncat` | Needs new tests |
| SAMP-05 | ToolError when client lacks sampling | unit | `pytest tests/test_mcp_server.py -x -q -k no_sampling` | Needs new tests |
| SAMP-06 | JSON parse of sampling response | unit | `pytest tests/test_llm_invoke.py -x -q -k parse_sampling` | Needs new tests |
| SAMP-07 | Real-path smoke from VS Code | manual | Manual: VS Code + Copilot + `outlet: sampling` | N/A |

### Wave 0 Gaps
- [ ] Sampling test fixtures in `tests/test_llm_invoke.py` (mock ServerSession)
- [ ] Outlet resolver sampling tests in `tests/test_outlet_resolver.py`
- [ ] MCP server sampling capability tests in `tests/test_mcp_server.py`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Sampling uses client's auth (Copilot subscription) |
| V3 Session Management | no | MCP session is transport-level, managed by SDK |
| V4 Access Control | yes | gate.yaml outlet: sampling is explicit opt-in |
| V5 Input Validation | yes | Validate create_message response (stopReason, content type) |
| V6 Cryptography | no | No crypto in this phase |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via sampling response | Tampering | Same JSON parse + validation as API path (forge already handles this) |
| Client returns unrelated model output | Information Disclosure | Log result.model; user sees which model ran the review |
| Denial of service via repeated sampling | Denial of Service | Client-side rate limiting (not server's responsibility per MCP spec) |

## Sources

### Primary (HIGH confidence)
- mcp SDK 1.27.0 source: ServerSession.create_message() signature, check_client_capability(), types [VERIFIED: pip show + inspect.getsource]
- MCP spec sampling page: modelcontextprotocol.io/specification/2025-06-18/client/sampling [CITED: firecrawl scrape]
- forge source: llm_invoke.py, outlet_resolver.py, machine.py, mcp_server.py, factories.py [VERIFIED: file reads]

### Secondary (MEDIUM confidence)
- VS Code sampling blog: code.visualstudio.com/blogs/2025/06/12/full-mcp-spec-support [CITED: ADR-0007]
- JetBrains + Copilot sampling: devblogs.microsoft.com/java/unlocking-mcp-in-jetbrains [CITED: firecrawl scrape, Microsoft official blog]
- Claude Code #1785: github.com/anthropics/claude-code/issues/1785 [VERIFIED: gh issue view]

### Tertiary (LOW confidence)
- Cursor sampling: forum.cursor.com/t/mcp-sampling-support/149604 -- feature request only, no official response [CITED: firecrawl scrape]
- Codex CLI: developers.openai.com/codex/mcp -- tools-only listed, no sampling; github.com/openai/codex/issues/4929 feature request [CITED: firecrawl scrape]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new packages, SDK types verified against installed version
- Architecture: HIGH - SDK signature verified, forge call chain traced, async boundary understood
- Pitfalls: HIGH - async/sync boundary, subprocess limitation, content types all verified in SDK source
- Client support: MEDIUM - VS Code/Visual Studio/JetBrains verified via official sources; Cursor/Codex/Claude Code are negative claims verified via feature request status

**Research date:** 2026-06-30
**Valid until:** 2026-07-30 (stable: SDK types unlikely to break; client support matrix may change)
