# Phase 33: MCP Server - Research

**Researched:** 2026-06-29
**Domain:** MCP Python SDK / stdio server / subprocess-based tool delegation
**Confidence:** HIGH

## Summary

The MCP Python SDK 1.27.0 (installed, verified) ships a high-level `FastMCP`
class that handles tool/resource/prompt registration, JSON-RPC stdio transport,
progress reporting, logging, and structured content -- all via decorators. The
SDK is mature (70+ releases, 1.28.1 latest on PyPI) and FastMCP is the
canonical way to build MCP servers in Python in 2026.

SEP-2663 Tasks support exists as experimental code in SDK 1.27.0 (`mcp.server.experimental.*`)
with in-memory store/queue, task context, and status notifications. It is
usable but marked experimental and API may change. Claude Code does NOT support
Tasks yet (hardcoded 60s tool timeout, no `poll_after_seconds` handling found
in search). The CONTEXT.md budgeted-start pattern (20s inline + background poll)
is the correct approach for Phase 33.

**Primary recommendation:** Use `FastMCP` (already bundled inside the `mcp` package)
with `@mcp.tool()` decorators, `ToolAnnotations`, and `structuredContent` for the
dual-layer return format. Keep the budgeted-start polling pattern in application
code (mcp_jobs.py). Skip Resources, Prompts, and experimental Tasks for Phase 33
-- they add complexity without clear value for forge's use case.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-33-01:** MCP handlers call CLI via `subprocess.run(["code-forge", ...])`.
  Zero modification to cli.py.
- **D-33-02:** Six MCP tools: forge_review, forge_gate_check, forge_init,
  forge_trust, forge_resolve_outlet, forge_job_status.
- **D-33-03:** Pre-flight check via resolve_outlet before review/gate-check.
- **D-33-04:** CLI flag to MCP parameter mapping (contract as content,
  backend as enum, committed/whole_file/canary as bool, etc.).
- **D-33-05:** Dual-layer return: content[] text + structuredContent JSON.
- **D-33-06:** Budgeted Start pattern (20s inline, poll fallback).
- **D-33-07:** Backend enum from gate.yaml at server startup.
- **D-33-08:** stdio transport only, entry point `code-forge-mcp`.

### Claude's Discretion
- D-33-01: subprocess vs import -- subprocess chosen (cli.py too coupled).
- D-33-06: budget duration -- 20s chosen (fits 2-3 polls in 60s window).
- D-33-08: stdio only -- per MCP best practice for local tools.

### Deferred Ideas (OUT OF SCOPE)
- HTTP/SSE transport (needs auth, CORS, deployment).
- SEP-2663 Tasks native (wait for SDK support ~2026-07-28).
- Hot-reload gate.yaml (YAGNI for Phase 33).
- cli.py refactor to importable review_pipeline().
- Progress streaming from CLI stderr.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MCP-01 | `code-forge-mcp` stdio server starts and exposes review + gate-check tools callable from any MCP client | FastMCP `@mcp.tool()` + `mcp.run(transport="stdio")` -- verified in SDK source |
| MCP-02 | MCP review tool routes to the resolved trusted backend (proven: finding returns via CN API, not DELEGATED self-review) | D-33-03 pre-flight via importable `resolve_outlet()` + `_load_gate_backends()` pattern |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| MCP JSON-RPC transport | MCP Server (stdio) | -- | SDK handles wire protocol |
| Tool parameter validation | MCP Server | -- | Pydantic via FastMCP auto-schema |
| CLI invocation | MCP Server (subprocess) | -- | D-33-01: subprocess.run() |
| Backend resolution / pre-flight | MCP Server (import) | -- | resolve_outlet() is cleanly importable |
| Gate.yaml parsing (backends) | MCP Server (import) | -- | _load_gate_backends() pattern reusable |
| Job state management | MCP Server (in-process) | -- | mcp_jobs.py dict-based store |
| Result formatting | MCP Server | -- | structuredContent + text content |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mcp | 1.27.0 (installed) | MCP server SDK -- FastMCP, stdio, types | Official Anthropic SDK, ships FastMCP built-in [VERIFIED: pip show + SDK source read] |
| asyncio | stdlib | Async event loop for MCP server | FastMCP is async-first, subprocess calls use asyncio.create_subprocess_exec [VERIFIED: SDK source] |
| subprocess | stdlib | CLI invocation | D-33-01 locked decision [VERIFIED: stdlib] |
| tempfile | stdlib | Write contract content to temp file | D-33-04: MCP sends content, handler writes to file for CLI [VERIFIED: stdlib] |
| uuid | stdlib | Generate job_id for polling | Standard UUID4 for job tracking [VERIFIED: stdlib] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| yaml (PyYAML) | already dep | Read gate.yaml for backend enum | Server startup: populate backend parameter enum [VERIFIED: already in pyproject.toml deps] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| FastMCP (high-level) | lowlevel Server | More boilerplate, manual handler registration -- FastMCP does it with decorators |
| subprocess.run (sync) | asyncio.create_subprocess_exec | Async subprocess avoids blocking the event loop during CLI calls -- RECOMMENDED for the budgeted-start pattern |
| In-process job dict | Redis/SQLite | Overkill for single-process stdio server with <10 concurrent jobs |

**Installation:**
```bash
pip install "mcp>=1.27,<2"
```

**Version verification:**
```
$ pip show mcp -> Version: 1.27.0
$ pip index versions mcp -> Latest: 1.28.1
```
Pin `<2` per SDK team recommendation: v2 is in alpha (2.0.0a1, June 2026),
stable v2 targeted 2026-07-27, breaking changes expected. [CITED: pypi.org/project/mcp]

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| mcp | PyPI | 1+ yr | high (official Anthropic SDK) | github.com/modelcontextprotocol/python-sdk | [OK] | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

Note: `mcp` is already installed in the environment (used by code-review-graph).
Adding `mcp>=1.27,<2` to pyproject.toml `[project.optional-dependencies]` as an
`mcp` extra is the cleanest approach -- the MCP server is optional, not required
for CLI-only users.

## Architecture Patterns

### System Architecture Diagram

```
IDE (Claude Code / Cursor / VS Code)
  |
  | stdio (JSON-RPC over stdin/stdout)
  |
  v
+---------------------------+
| code-forge-mcp            |
| (FastMCP stdio server)    |
|                           |
| @tool forge_review        |---> pre-flight: resolve_outlet() [import]
| @tool forge_gate_check    |---> pre-flight: resolve_outlet() [import]
| @tool forge_init          |
| @tool forge_trust         |
| @tool forge_resolve_outlet|
| @tool forge_job_status    |---> jobs dict lookup
|                           |
| mcp_jobs.py               |
| {job_id: {proc, result}}  |
+-----|---------------------+
      |
      | subprocess (async)
      |
      v
+---------------------------+
| code-forge CLI            |
| (existing, unmodified)    |
| review / gate-check /     |
| init / trust / resolve-   |
| outlet                    |
+---------------------------+
      |
      | HTTP (CN API backend)
      v
+---------------------------+
| LLM Backend               |
| (mimo-pro / deepseek /    |
|  zhipu / kimi / minimax)  |
+---------------------------+
```

### Recommended Project Structure

```
src/code_forge/
  mcp_server.py    # FastMCP server, tool definitions, main()
  mcp_jobs.py      # Job state: start_job(), get_job(), _jobs dict
```

Two new files only. No changes to existing files except pyproject.toml
(entry point + optional dependency).

### Pattern 1: FastMCP Tool with Annotations and Structured Content

**What:** Register tools via `@mcp.tool()` decorator with `ToolAnnotations`
and return structured content alongside text.

**When to use:** Every tool definition.

**Example:**
```python
# Source: SDK source /mcp/server/fastmcp/server.py (read directly)
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("code-forge-mcp")

@mcp.tool(
    name="forge_resolve_outlet",
    description="Diagnose which review backend and outlet forge will use. "
                "Read-only: does not modify any state.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def forge_resolve_outlet() -> dict:
    """Returns {outlet, backend, trusted} from resolve_outlet()."""
    proc = await asyncio.create_subprocess_exec(
        "code-forge", "resolve-outlet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "content": stdout.decode(),
        "exit_code": proc.returncode,
    }
```
[VERIFIED: SDK source -- FastMCP.tool() accepts `annotations=ToolAnnotations(...)`,
ToolAnnotations has readOnlyHint/destructiveHint/idempotentHint/openWorldHint fields]

### Pattern 2: Budgeted Start (20s inline, poll fallback)

**What:** Run subprocess with a 20s timeout. If it completes, return inline.
If it times out, let it continue in background and return a job_id for polling.

**When to use:** forge_review and forge_gate_check (potentially long-running).

**Example:**
```python
import asyncio
import uuid

# mcp_jobs.py
_jobs: dict[str, dict] = {}

def start_job(proc: asyncio.subprocess.Process) -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"proc": proc, "status": "running", "result": None}
    # Schedule background waiter
    asyncio.create_task(_wait_for_job(job_id, proc))
    return job_id

async def _wait_for_job(job_id: str, proc: asyncio.subprocess.Process) -> None:
    stdout, stderr = await proc.communicate()
    _jobs[job_id] = {
        "status": "completed" if proc.returncode is not None else "failed",
        "result": {
            "stdout": stdout.decode(),
            "exit_code": proc.returncode,
        },
    }

def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


# In mcp_server.py -- forge_review handler
@mcp.tool(...)
async def forge_review(backend: str | None = None, ...) -> dict:
    # D-33-03: pre-flight
    _check_backend_available()

    proc = await asyncio.create_subprocess_exec(
        "code-forge", "review", ...
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=20.0
        )
        # Completed within budget -- return inline
        return _format_result(stdout, proc.returncode)
    except asyncio.TimeoutError:
        # Budget exceeded -- background + poll
        job_id = start_job(proc)
        return {
            "job_id": job_id,
            "status": "running",
            "poll_after_seconds": 10,
            "message": "Review still running. Poll via forge_job_status.",
        }
```

### Pattern 3: structuredContent Dual-Layer Return (D-33-05)

**What:** FastMCP auto-generates structuredContent when the tool returns a dict
(with an outputSchema). The text content[] is CLI stdout verbatim; the
structuredContent carries machine-parseable verdict/exit_code/findings_count.

**When to use:** All tool results.

**Example:**
```python
from pydantic import BaseModel

class ForgeResult(BaseModel):
    verdict: str
    exit_code: int
    findings_count: int
    duration_s: float

@mcp.tool(
    name="forge_review",
    description="Run the forge review pipeline on the current git diff.",
    structured_output=True,  # enables outputSchema + structuredContent
)
async def forge_review(...) -> ForgeResult:
    ...
    return ForgeResult(
        verdict="FAIL",
        exit_code=1,
        findings_count=3,
        duration_s=12.5,
    )
```
[VERIFIED: SDK source -- FastMCP auto-detects Pydantic BaseModel return type
and generates outputSchema; lowlevel server validates structuredContent against
outputSchema via jsonschema.validate()]

**Important SDK behavior:** When a tool function returns a dict or Pydantic model,
FastMCP puts the JSON in `structuredContent` AND a serialized text version in
`content[]` for backward compatibility. This is exactly what D-33-05 wants:
agents read text, IDEs read JSON. No custom content assembly needed.

### Pattern 4: Error Handling (MCP tool error vs protocol error)

**What:** MCP distinguishes tool errors (isError=true in CallToolResult) from
protocol errors (JSON-RPC error response). Tool errors = the tool ran but
found a problem. Protocol errors = the tool could not run.

**When to use:** Pre-flight failures (no backend) are tool errors, not crashes.

**Example:**
```python
# Return isError=true for pre-flight failures
# FastMCP: raise ToolError("message") -> isError=true in response
from mcp.server.fastmcp.exceptions import ToolError

@mcp.tool(...)
async def forge_review(...):
    if not _has_backend():
        raise ToolError(
            "No trusted review backend configured. "
            "Run 'code-forge trust' first."
        )
    ...
```
[VERIFIED: SDK source -- ToolError in fastmcp/tools/base.py line 117 catches
Exception and re-raises as ToolError; lowlevel server sets isError=True]

### Anti-Patterns to Avoid

- **Printing to stdout in tool handlers:** stdio transport uses stdout for
  JSON-RPC messages. Any print() to stdout corrupts the protocol. Use
  `ctx.info()` / `ctx.warning()` for logging, or print to stderr.
  [CITED: multiple MCP guides + SDK stdio.py source]

- **Blocking the event loop with subprocess.run():** FastMCP is async. Using
  sync subprocess.run() blocks the entire server during CLI execution. Use
  asyncio.create_subprocess_exec() instead.

- **Returning raw CLI output without exit code:** The agent needs the exit
  code to know pass/fail. Always include it in structuredContent.

- **Importing cli._run() directly:** D-33-01 locks this out. The function
  is 2605 lines with 15+ argparse attributes; importing it requires
  constructing a fake argparse.Namespace. subprocess is correct.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON-RPC stdio transport | Custom stdin/stdout parser | `FastMCP.run(transport="stdio")` | Handles framing, encoding, error responses, task groups |
| Tool parameter validation | Manual JSON schema + checking | FastMCP auto-schema from type hints | Pydantic generates schema, validates input, handles errors |
| Structured content assembly | Manual CallToolResult construction | FastMCP auto-structured from return type | Return a Pydantic model or dict, SDK handles both content[] and structuredContent |
| Tool discovery protocol | Manual list_tools handler | FastMCP auto-registers from decorators | @tool() handles name, description, schema, annotations |
| Progress notifications | Custom notification protocol | `ctx.report_progress(current, total)` | SDK handles progress tokens, client negotiation |

**Key insight:** FastMCP handles 90% of the MCP boilerplate. The forge MCP
server is primarily about the 6 tool handler functions + mcp_jobs.py. The
protocol layer is free.

## MCP SDK Full Capability Assessment

### Features to USE in Phase 33

| Feature | What It Does | Forge Value | Confidence |
|---------|-------------|-------------|------------|
| `@mcp.tool()` decorator | Registers tools with auto-schema | Core -- all 6 tools | HIGH |
| `ToolAnnotations` | readOnlyHint, destructiveHint, idempotentHint | VS Code skips confirmation for read-only tools | HIGH |
| `structured_output=True` | Pydantic return type -> outputSchema + structuredContent | D-33-05 dual-layer return | HIGH |
| `mcp.run(transport="stdio")` | Runs stdio JSON-RPC server | D-33-08 transport | HIGH |
| `ToolError` exception | Sets isError=true in response | Pre-flight failures (D-33-03) | HIGH |
| Context `ctx.info()` / `ctx.warning()` | Sends log messages to client | Debug output during review | MEDIUM |
| Context `ctx.report_progress()` | Progress notifications | Future use with progress streaming | LOW (deferred) |
| `instructions` parameter | Server-level instructions for the agent | Tells agent when to use forge tools | MEDIUM |
| Lifespan context manager | Run setup/teardown code | Load gate.yaml backends at startup (D-33-07) | HIGH |

### Features to SKIP in Phase 33

| Feature | What It Does | Why Skip | Revisit When |
|---------|-------------|----------|--------------|
| Resources (`@mcp.resource()`) | Expose read-only data URIs | gate.yaml content is not useful as a resource -- the agent does not need to read it separately from tool results | If users request config inspection |
| Prompts (`@mcp.prompt()`) | Reusable prompt templates | The agent composes its own prompts when calling tools. A "review with contract" prompt template does not save meaningful work -- the agent already passes contract content as a parameter | Never, unless user demand |
| Elicitation (`ctx.elicit()`) | Request structured input from user | Phase 33 tools are fire-and-forget or poll. No interactive input needed during a review | If interactive review approval is needed |
| Experimental Tasks | SEP-2663 async task handles | Claude Code does not support Tasks yet (60s hardcoded timeout, no poll_after_seconds). Budgeted-start pattern works today | When SDK v2 stable + Claude Code supports Tasks (~2026-08+) |
| SSE/StreamableHTTP transport | HTTP-based remote server | D-33-08: stdio only | If team/remote use case arises |
| OAuth/Auth | Token-based authentication | stdio = local subprocess = no auth needed | If HTTP transport added |
| Completion handler | Autocomplete for prompt/resource args | No prompts or resource templates | Never for Phase 33 |
| Custom routes | Extra HTTP endpoints | stdio transport = no HTTP | If HTTP transport added |
| `title` on tools | Human-readable tool title (separate from name) | Available in 1.27.0 but optional. Tool name + description sufficient | Minor polish later |

### Features Worth Noting for Migration Path

| Feature | Status in 1.27.0 | Migration Impact |
|---------|-------------------|------------------|
| `server.experimental.enable_tasks()` | Exists, experimental | When Claude Code supports Tasks, replace mcp_jobs.py with `enable_tasks()` + `ServerTaskContext`. The TaskSupport class already provides InMemoryTaskStore and handles get/list/cancel. |
| `TasksCallCapability` | Backported from v2 | Server declares task support in capabilities; client must also declare support |
| SDK v2 (2.0.0a) | Alpha, beta 2026-06-30 | Pin `mcp<2` now. v2 may change FastMCP API. Upgrade when stable (2026-07-28) |

## Common Pitfalls

### Pitfall 1: Claude Code 60s Hard Timeout
**What goes wrong:** MCP tool call exceeds 60s, Claude Code silently drops the
result. Server-side timeout (even if set to 30min) does not matter.
**Why it happens:** `DEFAULT_REQUEST_TIMEOUT_MSEC = 60000` is hardcoded in the
MCP TypeScript SDK that Claude Code bundles. MCP_TIMEOUT config is ignored.
**How to avoid:** Budgeted-start pattern: 20s inline attempt + background with
10s poll intervals. 20s + 3x10s = 50s, well within 60s window.
**Warning signs:** Tool calls that "hang" or return "No result received."
[CITED: github.com/anthropics/claude-code/issues/16837, /issues/52137]

### Pitfall 2: stdout Corruption in stdio Mode
**What goes wrong:** A print() statement or library logging to stdout corrupts
the JSON-RPC framing. Server appears to hang or return garbage.
**Why it happens:** stdio transport uses stdout exclusively for JSON-RPC
messages. Any extra output breaks the line-delimited JSON protocol.
**How to avoid:** Never print to stdout. Use stderr for debug output.
Subprocess CLI output goes to PIPE, not inherited stdout.
**Warning signs:** Server connects but tools never appear; garbled responses.
[VERIFIED: SDK source stdio.py -- stdout wrapped as JSON-RPC writer]

### Pitfall 3: Blocking Event Loop with subprocess.run()
**What goes wrong:** sync subprocess.run() blocks the entire async event loop.
No other MCP requests can be processed during a 20s+ review.
**Why it happens:** FastMCP runs on anyio (async). A sync blocking call freezes
all concurrent handlers.
**How to avoid:** Use `asyncio.create_subprocess_exec()` for all CLI calls.
**Warning signs:** Server unresponsive during long tool calls.

### Pitfall 4: Pre-Flight Self-Review Loop (D-33-03)
**What goes wrong:** Claude Code calls forge_review MCP tool -> subprocess calls
CLI -> CLI finds no explicit backend -> falls back to `claude -p` -> that
Claude instance calls forge_review again -> infinite loop.
**Why it happens:** Without pre-flight, the CLI's outlet resolver may fall
through to the implicit claude binary on PATH.
**How to avoid:** Import `resolve_outlet()` directly and call it before
subprocess. If no trusted backend: raise ToolError immediately.
**Warning signs:** Recursive process spawning, high CPU, eventual timeout.
[VERIFIED: outlet_resolver.py source -- resolve_outlet() is cleanly importable,
raises CliError when no backend configured]

### Pitfall 5: mcp Package Name Collision
**What goes wrong:** npm has a different `mcp` package (v1.4.2). If someone
confuses the ecosystem, they install the wrong package.
**Why it happens:** npm `mcp` is unrelated to the MCP protocol SDK.
**How to avoid:** Always `pip install mcp` (PyPI). Never `npm install mcp`.
[VERIFIED: npm view mcp -> 1.4.2 (different package)]

## Code Examples

### Complete Minimal MCP Server (verified pattern)

```python
# Source: SDK source read + MCP types inspection
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP(
    "code-forge-mcp",
    instructions=(
        "Forge code review tools. Use forge_review to review git diffs, "
        "forge_gate_check for pre-commit gating, forge_resolve_outlet to "
        "diagnose backend configuration."
    ),
)

@mcp.tool(
    description="Check which review backend forge will use.",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def forge_resolve_outlet() -> dict:
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "code-forge", "resolve-outlet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return {"output": stdout.decode().strip(), "exit_code": proc.returncode}

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```
[VERIFIED: FastMCP.run() calls anyio.run(self.run_stdio_async) which uses
stdio_server() context manager -- SDK source lines 294-296, 753-760]

### Async Subprocess with Timeout (budgeted start)

```python
import asyncio

async def _run_cli_budgeted(
    args: list[str],
    budget_seconds: float = 20.0,
) -> tuple[str, int] | None:
    """Run CLI with budget. Returns (stdout, exit_code) or None if timed out."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=budget_seconds
        )
        return (stdout.decode(), proc.returncode or 0)
    except asyncio.TimeoutError:
        return None  # caller should start_job(proc)
```

### Client Registration (.mcp.json for Claude Code)

```json
{
  "mcpServers": {
    "code-forge": {
      "command": "code-forge-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

### Client Registration (.cursor/mcp.json for Cursor)

```json
{
  "mcpServers": {
    "code-forge": {
      "command": "code-forge-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Lowlevel Server + manual handlers | FastMCP decorators | SDK 1.0+ (2025) | 80% less boilerplate |
| Text-only tool results | structuredContent + text dual return | MCP spec 2025-06-18 | Agents read text, IDEs parse JSON |
| No tool metadata | ToolAnnotations (readOnlyHint etc.) | MCP spec 2025-03-26 | VS Code skips confirmation for safe tools |
| Custom polling for long tasks | SEP-2663 Tasks extension | Spec 2026-04-27 (merged) | Not yet in clients; budgeted-start bridges the gap |
| Raw SDK only | FastMCP bundled in mcp package | SDK 1.x | No separate install needed |
| SSE transport for remote | Streamable HTTP | MCP spec 2025-11 | SSE deprecated for new servers |

**Deprecated/outdated:**
- `mcp.server.Server` (lowlevel): still works but FastMCP is preferred
- SSE transport: superseded by Streamable HTTP for remote servers
- `PrefectHQ/fastmcp`: was a separate package, now merged into `mcp` SDK

## Subprocess vs asyncio.create_subprocess_exec

D-33-01 locks subprocess as the invocation method. Within that constraint:

| Method | Blocking? | Timeout? | Budget Pattern? | Recommendation |
|--------|-----------|----------|-----------------|----------------|
| `subprocess.run()` | YES (sync) | timeout param | No (blocks event loop) | AVOID in async server |
| `asyncio.create_subprocess_exec()` | NO (async) | via `asyncio.wait_for()` | YES (clean) | USE THIS |
| `Popen` + poll | Partially | Manual poll loop | Awkward | AVOID |

**Recommendation:** Use `asyncio.create_subprocess_exec()` exclusively. It is
still subprocess invocation (D-33-01 compliant) but does not block the async
event loop. The `asyncio.wait_for(proc.communicate(), timeout=20.0)` pattern
gives clean budgeted-start behavior.

## SEP-2663 Tasks: Current State in SDK 1.27.0

The Tasks extension exists as experimental code at:
- `mcp.server.experimental.task_support` -- TaskSupport class with InMemoryTaskStore
- `mcp.server.experimental.task_context` -- ServerTaskContext with update_status/complete/fail
- `mcp.server.lowlevel.experimental` -- ExperimentalHandlers with enable_tasks()

**What works today:**
- `server.experimental.enable_tasks()` auto-registers get/list/cancel/result handlers
- `ServerTaskContext.update_status("message")` sends status notifications
- `ServerTaskContext.complete(result)` / `.fail(error)` for terminal states
- InMemoryTaskStore and InMemoryTaskMessageQueue for single-process servers
- Task group for spawning background work via anyio

**What does NOT work for forge Phase 33:**
- Claude Code does NOT support the Tasks protocol (hardcoded 60s timeout,
  no tasks/get polling, no TasksCallCapability negotiation)
  [CITED: github.com/anthropics/claude-code/issues/52137]
- The API is experimental ("may change without notice")
- SDK v2 (targeting 2026-07-28) will likely change the Tasks API

**Migration path (D-33-06):** mcp_jobs.py implements the same interface
(start_job -> job_id, get_job -> result) that TaskSupport provides. When
Claude Code gains Tasks support:
1. Replace `_jobs` dict with `enable_tasks()` InMemoryTaskStore
2. Replace `forge_job_status` tool with native tasks/get protocol
3. Handler logic stays the same -- only the plumbing changes

## MCP Resources: Assessment for Forge

**Could expose:**
- `forge://config/gate.yaml` -- current gate.yaml content
- `forge://config/trust` -- trust status
- `forge://status/backends` -- available backends

**Should we?** No for Phase 33. Reasons:
1. The agent never needs to read gate.yaml separately -- the review tool
   already uses the correct backend. The agent calls tools, not reads config.
2. Resources add a capability declaration, handler code, and test surface
   for zero user-facing benefit.
3. If a user wants to see config, `forge_resolve_outlet` already returns it.
4. YAGNI: no deferred idea in CONTEXT.md asks for resources.

## MCP Prompts: Assessment for Forge

**Could expose:**
- `review_with_contract` -- pre-fills contract parameter
- `quick_review` -- review with defaults

**Should we?** No for Phase 33. Reasons:
1. Claude Code and Cursor do not use MCP prompts for tool invocation -- they
   compose tool calls directly from the tool schema.
2. A prompt template that says "call forge_review with these parameters"
   adds indirection without value. The agent reads the tool description.
3. Zero user demand signal.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Claude Code hardcoded 60s timeout still applies in current version | Pitfall 1 | If fixed, budgeted-start is still correct (just less necessary) |
| A2 | FastMCP auto-structured output works with Pydantic BaseModel returns | Pattern 3 | May need manual CallToolResult construction -- fallback is simple |
| A3 | `asyncio.create_subprocess_exec` works inside FastMCP's anyio event loop | Pattern 2 | anyio supports asyncio backend; if trio is used, need trio subprocess -- but FastMCP defaults to asyncio via anyio.run() |

## Open Questions (RESOLVED)

1. **asyncio vs trio in FastMCP** (RESOLVED)
   - What we know: FastMCP uses anyio. `anyio.run()` defaults to asyncio backend.
   - What's unclear: Can we safely use `asyncio.create_subprocess_exec()` or
     must we use `anyio.open_process()`?
   - Recommendation: Use `anyio.open_process()` for portability, OR verify
     FastMCP always uses asyncio backend. (FastMCP.run() calls
     `anyio.run(self.run_stdio_async)` which defaults to asyncio.)
   - RESOLVED: asyncio.create_subprocess_exec is safe -- FastMCP.run() calls
     anyio.run() which defaults to asyncio backend. Plan uses asyncio directly
     per D-33-01.

2. **Backend enum population timing** (RESOLVED)
   - What we know: D-33-07 says read gate.yaml at server startup.
   - What's unclear: FastMCP lifespan context manager vs module-level code.
   - Recommendation: Use FastMCP `lifespan` parameter for clean startup/shutdown.
   - RESOLVED: Use FastMCP lifespan context manager per D-33-07/D-33-09.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| mcp (Python) | MCP server | YES | 1.27.0 | -- |
| code-forge CLI | subprocess calls | YES | installed (editable) | -- |
| PyYAML | gate.yaml parsing | YES | already dep | -- |
| asyncio | async subprocess | YES | stdlib (3.14) | -- |

**Missing dependencies with no fallback:** none
**Missing dependencies with fallback:** none

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/test_mcp_server.py -x` |
| Full suite command | `pytest --ignore=.worktrees --ignore=.claude/worktrees -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MCP-01 | Server starts, tools appear in list | unit | `pytest tests/test_mcp_server.py::test_tool_list -x` | Wave 0 |
| MCP-01 | Each tool callable with valid params | unit | `pytest tests/test_mcp_server.py::test_tool_call -x` | Wave 0 |
| MCP-02 | Pre-flight blocks when no backend | unit | `pytest tests/test_mcp_server.py::test_preflight_no_backend -x` | Wave 0 |
| MCP-02 | Review routes to real backend (not DELEGATED) | integration | Manual: `code-forge-mcp` + real backend call | Manual |

### Sampling Rate
- **Per task commit:** `pytest tests/test_mcp_server.py -x`
- **Per wave merge:** Full suite
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_mcp_server.py` -- covers MCP-01, MCP-02
- [ ] `tests/test_mcp_jobs.py` -- covers D-33-06 job state

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | stdio = local subprocess, no auth needed |
| V3 Session Management | no | stateless tool calls |
| V4 Access Control | yes | Pre-flight trust check (D-33-03) via resolve_outlet() |
| V5 Input Validation | yes | Pydantic via FastMCP auto-schema; tempfile for contract content |
| V6 Cryptography | no | no crypto operations |

### Known Threat Patterns for MCP stdio server

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Self-review delegation loop | Elevation of Privilege | D-33-03 pre-flight resolve_outlet() check |
| stdout corruption (JSON-RPC injection) | Tampering | subprocess PIPE isolation; never print to stdout |
| Untrusted gate.yaml backends | Spoofing | Existing trust guard in _load_gate_backends() |
| Contract content path traversal | Tampering | tempfile.NamedTemporaryFile (no user-controlled paths) |
| Job ID enumeration | Information Disclosure | UUID4 (128-bit random); jobs dict is process-local |

## Sources

### Primary (HIGH confidence)
- MCP Python SDK 1.27.0 source code (read directly from /usr/local/lib/python3.14/site-packages/mcp/) -- FastMCP, stdio, types, experimental tasks, tool annotations, structured content
- forge source code (read directly) -- exit_codes.py, outlet_resolver.py, cli.py, gate_check.py, backend.py

### Secondary (MEDIUM confidence)
- [PyPI mcp package](https://pypi.org/project/mcp/) -- version history, v2 timeline
- [GitHub Claude Code issues #16837, #52137](https://github.com/anthropics/claude-code/issues/16837) -- 60s timeout confirmation
- [MCP Dev Summit 2026 summary](https://dev.to/peytongreen_dev/mcp-dev-summit-2026-what-actually-changed-for-python-developers-16ep) -- v1.27.0 TasksCallCapability backport
- [MCP 2026-07-28 Release Candidate blog](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/) -- SEP-2663 Tasks spec status
- [GitHub python-sdk #1546](https://github.com/modelcontextprotocol/python-sdk/issues/1546) -- Tasks implementation tracking

### Tertiary (LOW confidence)
- [MCP.directory comparison](https://mcp.directory/blog/fastmcp-vs-fastapi-mcp-vs-python-sdk-2026) -- FastMCP ecosystem claims (not independently verified)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- SDK source read directly, all APIs verified
- Architecture: HIGH -- patterns derived from SDK source + CONTEXT.md decisions
- Pitfalls: HIGH -- 60s timeout confirmed via multiple GitHub issues; stdout corruption verified from SDK source
- SEP-2663 status: MEDIUM -- experimental code read directly, but client support status from web search

**Research date:** 2026-06-29
**Valid until:** 2026-07-15 (SDK v2 beta may change APIs)
