# Phase 33: MCP Server - Context

**Gathered:** 2026-06-29
**Status:** Ready for planning

<domain>
## Phase Boundary

`code-forge-mcp` stdio server exposes forge's review, gate-check, init,
trust, and resolve-outlet as MCP tools callable from any IDE (Claude Code,
VS Code, Cursor). The server subprocess-calls the existing CLI -- zero
changes to cli.py. Long-running review calls use a budgeted-start pattern
(inline if <20s, poll fallback otherwise), with architecture that can
migrate to SEP-2663 Tasks when the Python SDK ships support (~2026-07-28).

Closes MCP-01, MCP-02.

</domain>

<decisions>
## Implementation Decisions

### CLI Decoupling
- **D-33-01:** MCP handlers call the CLI via
  `asyncio.create_subprocess_exec("code-forge", ...)` and parse stdout.
  Async subprocess (not sync `subprocess.run()`) because FastMCP is
  async-first -- sync calls block the event loop. Zero modification to
  `cli.py`. The CLI is the stable interface; `_run()` (2605 lines, 15+
  argparse attributes) is too tightly bound to argparse to import
  directly. Refactoring `_run()` into an importable `review_pipeline()`
  is out of Phase 33 scope.

### Tool Surface
- **D-33-02:** Six MCP tools exposed:
  1. `forge_review` -- core review pipeline (SC1)
  2. `forge_gate_check` -- pre-commit gate (SC1)
  3. `forge_init` -- initialize .code-forge/ directory
  4. `forge_trust` -- manage backend trust
  5. `forge_resolve_outlet` -- diagnose backend routing
  6. `forge_job_status` -- poll long-running job results (D-33-06)

  Each tool maps to a CLI subcommand. Tool descriptions follow MCP best
  practice: lead with when-to-use, specify return shape, include
  constraints. Tool names use `snake_case` per `{verb}_{noun}` convention.

### Self-Review Prevention
- **D-33-03:** *(Superseded by D-33-23.)* MCP layer adds a pre-flight
  config-existence check before calling CLI for review/gate-check. If no
  trusted backend: return MCP error, never invoke review. This prevents
  the DELEGATED loop. The CLI's own fail-closed behavior (Phase 30) is
  the primary guard; MCP's pre-flight is defense-in-depth.
  Implementation: D-33-23 (emptiness check, no resolve_outlet call).

### Parameter Mapping
- **D-33-04:** CLI flag to MCP parameter mapping:

  | CLI flag | MCP parameter | Notes |
  |----------|---------------|-------|
  | `--contract FILE` | `contract: str` (content) | Handler writes to temp file, passes path to CLI. MCP cannot assume file paths are shared. |
  | `--backend NAME` | `backend: str` (enum) | Enum populated from gate.yaml backends at server startup. Enables IDE model-selection dropdown. |
  | `--committed` | `committed: bool` | Optional, default false |
  | `--whole-file` | `whole_file: bool` | Optional, default false |
  | `--canary` | `canary: bool` | Optional, default false |
  | `--sarif` | N/A | MCP controls its own output format (D-33-05) |
  | `--baseline` | `baseline: str` | For gate-check only |
  | `--force` | `force: bool` | For init only |

  Parameters not listed (--quiet, --no-color, etc.) are set by the MCP
  handler internally -- not user-facing.

### Return Format
- **D-33-05:** Dual-layer return per 2026 MCP best practice:
  - `content[]` (text): CLI stdout verbatim -- human-readable findings
  - `structuredContent` (JSON): `{verdict: str, exit_code: int,
    findings_count: int | None, duration_s: float}`
  *(findings_count refined by D-33-20: None means "not counted".)*

  The agent reads text for reasoning; the IDE app reads JSON for UI
  rendering. Error cases use `isError: true` with actionable message
  (MCP spec: tool errors vs protocol errors).

### Timeout / Long-Running
- **D-33-06:** Budgeted Start pattern with SEP-2663 migration path:
  - `forge_review` and `forge_gate_check` synchronously await CLI for
    up to 20 seconds (budget). If CLI completes within budget: return
    result inline (most common case -- small diffs, fast backends).
  - If budget expires: CLI continues in background. Handler returns
    `{job_id, status: "running", poll_after_seconds: 10}`. The
    `forge_job_status` tool accepts `job_id` and returns current state
    (`running`/`completed`/`failed`).
  - Claude Code / Cursor already recognize `poll_after_seconds` and
    auto-poll.
  - Job management lives in a separate module (`mcp_jobs.py`) with
    interface `start_job() -> job_id` / `get_job(job_id) -> result`.
    When Python MCP SDK implements SEP-2663 (tracked: issue #2806,
    target 2026-07-28), replace job module with
    `TaskConfig(mode="optional")` -- handler logic unchanged.
  - Claude Code hard timeout is 60s (issue #52137). Within that window
    the budgeted start (20s) + 2-3 polls (10s each) fits comfortably.

### Model Selection UI
- **D-33-07:** *(Enum claim superseded by D-33-17: str + runtime validation.)*
  The `backend` parameter on `forge_review` is typed `str | None`. Backend
  names loaded from gate.yaml at startup for runtime validation. IDE
  dropdown is a future enhancement. Server restart picks up new backends
  (no hot-reload needed for Phase 33).

### Transport
- **D-33-08:** stdio transport only (local server). The MCP server
  runs as a subprocess of the IDE. No HTTP, no auth, no CORS. Entry
  point: `code-forge-mcp = "code_forge.mcp_server:main"` in
  pyproject.toml `[project.scripts]`. Registration:
  `.mcp.json` (Claude Code) / `.cursor/mcp.json` (Cursor).

### Server Framework (from research)
- **D-33-09:** Use `FastMCP` (bundled in mcp 1.27.0) with `@mcp.tool()`
  decorators, `ToolAnnotations`, and `ToolError`. Do NOT use
  `structured_output=True` -- it auto-serializes Pydantic to JSON text
  in content[], violating D-33-05 (see D-33-13). Instead, manually
  construct `CallToolResult` for dual-layer return. Do NOT use the
  lowlevel `mcp.server.Server` class. FastMCP handles JSON-RPC, tool
  registration, and transport -- 90% less boilerplate. Use FastMCP
  `lifespan` context manager for gate.yaml loading via
  `_load_gate_backends` at startup (D-33-07, D-33-23). Use
  `mcp.run(transport="stdio")` for D-33-08.

### Anti-Patterns (from research)
- **D-33-10:** Never `print()` to stdout in tool handlers -- stdio
  transport uses stdout for JSON-RPC messages. Any extra output corrupts
  the protocol. Use stderr or `ctx.info()`/`ctx.warning()` for logging.
  Subprocess CLI output goes to PIPE, not inherited stdout.

### Budgeted Start: asyncio.shield (cross-AI review C-B1)
- **D-33-11:** `asyncio.wait_for()` CANCELS the inner coroutine on timeout.
  A second `proc.communicate()` call gets empty data because the first
  reader consumed and discarded pipe buffers. Fix: wrap communicate in
  `asyncio.shield()` so cancellation does not tear down readers, then pass
  the shielded task (not just the Process) to `start_job()`. `_wait_for_job`
  awaits the SAME task, not a fresh communicate() call.

### Pre-flight: _load_gate_backends Required (cross-AI review C-B2)
- **D-33-12:** *(Superseded by D-33-23.)* `_check_backend` must call
  `_load_gate_backends(gate_yaml_path)` to get backend configs. The trust
  guard (cli.py:130 `if not is_trusted(...)`) returns `([], {})` for
  untrusted repos. Use `_load_gate_backends` for BOTH the lifespan backend
  name enumeration AND the pre-flight emptiness check (D-33-23).
  Do NOT pass configs to `resolve_outlet` -- D-33-23 removes that call.

### Dual-Layer Return: Manual CallToolResult (cross-AI review C-B3)
- **D-33-13:** Do NOT use `structured_output=True` on FastMCP tools.
  FastMCP auto-serializes Pydantic models to JSON text in `content[]`,
  violating D-33-05 ("CLI stdout verbatim"). Instead: manually construct
  `CallToolResult` with `content=[TextContent(type="text", text=cli_stdout)]`
  + `structuredContent={verdict, exit_code, ...}`. This gives agents raw
  stdout for reasoning and IDEs structured JSON for rendering. Update D-33-09
  accordingly: FastMCP for tool registration + annotations, NOT for
  structured_output auto-generation.

### Process Cleanup on Shutdown (cross-AI review C-H1)
- **D-33-14:** FastMCP `lifespan` teardown (after `yield`) must iterate
  `_jobs`, send `proc.terminate()` to running subprocesses, `await
  proc.wait()` with 5s grace, then `proc.kill()`. Prevents orphan CLI
  processes when IDE closes or MCP server exits.

### Job Memory Lifecycle (cross-AI review C-H2)
- **D-33-15:** `_jobs` dict entries evicted after retrieval or after 1-hour
  TTL (whichever comes first). `get_job()` pops the entry on completed/failed
  status. A periodic cleanup in `_wait_for_job` removes stale entries.
  Prevents unbounded memory growth in long IDE sessions.

### Job Status Pydantic Model (cross-AI review C-H3)
- **D-33-16:** `forge_job_status` returns `ForgeJobRef(BaseModel)` with
  `job_id: str, status: str, poll_after_seconds: int | None,
  result: ForgeResult | None`. Not a raw dict. Consistent with D-33-05.

### Backend Parameter: str + Validation (cross-AI review C-H4)
- **D-33-17:** `backend` parameter typed as `str | None` (NOT Literal/Enum).
  FastMCP evaluates types at decoration time; gate.yaml backends are loaded
  at runtime (lifespan). Handler validates against loaded backend names and
  raises `ToolError` if invalid. Update D-33-07: the dropdown is a future
  IDE enhancement, not a Phase 33 deliverable.

### Tempfile Lifecycle on Timeout (cross-AI review C-H5)
- **D-33-18:** Contract tempfile is NOT deleted in the handler's `finally`
  block. For the timeout path, the CLI subprocess is still running with
  `--contract tmpfile.path`. Deletion happens in `_wait_for_job` callback
  after the CLI completes. For the inline path (no timeout), `finally`
  deletion is safe.

### Gate.yaml Path Discovery (cross-AI review C-H6)
- **D-33-19:** MCP server discovers gate.yaml via `Path.cwd()` (stdio
  transport: IDE spawns the server with cwd = workspace root). Document
  this assumption in the MCP server's `instructions` string and in the
  `.mcp.json` example. If cwd is wrong, the pre-flight raises ToolError
  with "gate.yaml not found at {path}" -- actionable, not silent.

### findings_count Optional (cross-AI review C-H7)
- **D-33-20:** `ForgeResult.findings_count` is `int | None = None`.
  `None` means "not counted" (honest). Populate only when a reliable
  source exists (future: CLI structured output). Never return 0 as a
  surrogate for "unknown".

### Pre-flight Exception Scope (cross-AI review C-H8)
- **D-33-21:** `_check_backend` catches `(CliError, ValueError, OSError)`
  and converts to `ToolError`. `ValueError` covers invalid outlet strings
  in gate.yaml. `OSError` covers file access failures.

### Shield Placement Fix (cross-AI review round 2, C2-B1)
- **D-33-22:** The plan's `ensure_future(shield(coro))` pattern is WRONG.
  `wait_for` cancels the outer Task; awaiting it later raises CancelledError.
  Correct pattern: `inner_task = asyncio.create_task(proc.communicate())`,
  then `await asyncio.wait_for(asyncio.shield(inner_task), timeout=budget)`.
  On timeout, pass `inner_task` (not the shield wrapper) to `start_job`.
  `_wait_for_job` awaits `inner_task` which is still alive. Verified by
  Python code. Updates D-33-11.

### Pre-flight: Config Check Only (cross-AI review round 2, C2-B2)
- **D-33-23:** `_check_backend` must NOT call `resolve_outlet()`.
  `resolve_outlet` calls `probe_backend()` (HTTP probe, +2-7s, burns quota).
  Instead: call `_load_gate_backends(gate_yaml_path)`, check
  `if not backend_configs: raise ToolError(...)`. Trust guard in
  `_load_gate_backends` returns `([], {})` for untrusted repos. The CLI's
  own fail-closed handles reachability. Updates D-33-03/D-33-12. Remove
  `resolve_outlet` import from mcp_server.py.

### Test Patch Targets (cross-AI review round 2, C2-H1/H2)
- **D-33-24:** Patch `code_forge.cli._load_gate_backends` (source module),
  NOT `code_forge.mcp_server._load_gate_backends`. `from X import Y`
  creates a local binding that module-level patch misses. Or: change import
  to `from code_forge import cli` and call `cli._load_gate_backends(...)`.
  For `_backend_names`, patch `code_forge.mcp_server._backend_names`
  directly (module variable), not lifespan context dict.

### _wait_for_job Exception Safety (cross-AI review round 2, C2-H3)
- **D-33-25:** Wrap `await comm_task` in `try/except BaseException`.
  On exception: set status "failed", store error, delete tempfile.

### Shield Semantics Test (cross-AI review round 2, C2-H4)
- **D-33-26:** Test must assert `proc.communicate.call_count == 0` after
  `_wait_for_job`. Core D-33-11 assertion.

### Cleanup Kill Path Test (cross-AI review round 2, C2-H5)
- **D-33-27:** Test where `proc.wait()` raises `asyncio.TimeoutError`.
  Assert `proc.kill()` called. Per D-33-14.

### Tempfile Control Flow (cross-AI review round 2, C2-H6)
- **D-33-28:** No `finally` for tempfile. Explicit branching: inline path
  deletes immediately; timeout path sets `tmp_path = None` (ownership
  transferred to start_job). Updates D-33-18.

### CancelledError Cleanup (cross-AI review round 3, GM-B1)
- **D-33-29:** `_run_cli_budgeted` must catch `asyncio.CancelledError`
  (IDE client abort) in addition to `TimeoutError`. On CancelledError:
  kill proc, cancel inner_task, re-raise. Without this, both become
  orphans invisible to cleanup_all (never entered _jobs).

### Gate.yaml Missing vs Untrusted (cross-AI review round 3, GM-H1)
- **D-33-30:** `_check_backend` must check `gate_yaml_path.exists()`
  BEFORE calling `_load_gate_backends`. If missing: raise ToolError
  with "gate.yaml not found at {path}. Run 'code-forge init'." This
  distinguishes "not initialized" from "not trusted" (D-33-23).

### Tempfile Flush Before Subprocess (cross-AI review round 3, GM-M1)
- **D-33-31:** After writing contract content to NamedTemporaryFile,
  call `tmp.close()` before passing path to CLI subprocess. Without
  close/flush, buffered data may not be on disk when CLI reads it.

### Evict vs Wait Race (cross-AI review round 3, GM-M2)
- **D-33-32:** `_evict_stale` must NOT remove running entries from
  `_jobs`. It calls `proc.kill()` but leaves the entry for
  `_wait_for_job` to process. `_wait_for_job` handles the kill,
  updates status to "failed", and a subsequent evict pass garbage-
  collects the terminal entry. Prevents KeyError race.

### Claude's Discretion
- D-33-01 (subprocess vs import): Claude chose subprocess based on
  cli.py's tight argparse coupling making import impractical.
- D-33-06 (budget duration): Claude chose 20s based on Claude Code's
  60s hard timeout -- leaves room for 2-3 poll cycles.
- D-33-08 (stdio only): Claude chose stdio per MCP best practice for
  local tools -- zero auth surface.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### MCP SDK and Protocol
- MCP Python SDK 1.27.0 -- `mcp.server` module for stdio server
  implementation. Note: Tasks (SEP-2663) NOT yet implemented in SDK;
  issue #2806 tracks for 2026-07-28 release.
- SEP-2663 (Tasks Extension) -- Final status, merged 2026-04-27.
  The migration target for D-33-06 job pattern.
  URL: https://modelcontextprotocol.io/seps/2663-tasks-extension

### Forge CLI (the subprocess target)
- `src/code_forge/cli.py` -- `main()` entry point (line 1029),
  `_build_parser()` (lines 162-563), `_run()` (line 1395)
- `src/code_forge/exit_codes.py` -- EXIT_PASS=0, EXIT_FAIL=1,
  EXIT_CLI_ERROR=2, EXIT_DELEGATED=5 (detect self-review)
- `src/code_forge/errors.py` -- CliError class
- `src/code_forge/outlet_resolver.py` -- `resolve_outlet()` signature
  reference (NOT called by MCP pre-flight per D-33-23)

### Entry Point and Packaging
- `pyproject.toml` line 48 area -- where `code-forge-mcp` script
  entry point will be added
- `src/code_forge/init_template.py` -- CONTRACT_TEMPLATE_MD (Phase 32)

### Client Integration
- Claude Code issue #52137 -- 60s hard timeout, no Tasks support yet
- VS Code MCP docs -- tool annotations (`readOnlyHint`,
  `destructiveHint`), dynamic tool discovery
- Azure budgeted-start reference: github.com/Azure-Samples/
  mcp-functions-long-running-tools

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- MCP Python SDK 1.27.0: `mcp.server.Server`, stdio transport, tool
  registration via decorators
- `subprocess.run()`: stdlib, zero dependencies for CLI invocation
- `_load_gate_backends()` in cli.py: trust-guarded config loader,
  used for pre-flight emptiness check (D-33-23) and backend name
  enumeration. Imported via `from code_forge import cli` (D-33-24).

### Established Patterns
- CLI subcommands (review, gate-check, init, trust, resolve-outlet)
  map 1:1 to MCP tools
- Exit codes are well-defined (exit_codes.py) -- structuredContent
  can map them directly
- `--sarif` output exists but is not needed for MCP (D-33-05 defines
  its own return format)

### Integration Points
- `pyproject.toml [project.scripts]`: add `code-forge-mcp` entry
- `.mcp.json` / `.cursor/mcp.json`: client registration
- `src/code_forge/mcp_server.py`: new file -- the MCP server
- `src/code_forge/mcp_jobs.py`: new file -- job state management

</code_context>

<specifics>
## Specific Ideas

- Tool annotations: `forge_resolve_outlet` and `forge_trust` get
  `readOnlyHint: true` (VS Code skips confirmation for read-only tools)
- `forge_init` gets `destructiveHint: false`, `idempotentHint: true`
- `forge_review` description: "Run the forge review pipeline on the
  current git diff. Long-running: returns inline if <20s, otherwise
  returns job_id for polling via forge_job_status."
- Backend enum refresh: read gate.yaml once at server startup. Restart
  server to pick up new backends (no hot-reload complexity for Phase 33).

</specifics>

<deferred>
## Deferred Ideas

- **HTTP/SSE transport**: remote MCP server for team use. Needs auth,
  CORS, deployment -- separate phase if demand exists.
- **SEP-2663 Tasks native**: replace job module with SDK Tasks when
  Python SDK ships support (tracked issue #2806, ~2026-07-28).
- **Hot-reload gate.yaml**: watch for changes and update backend enum
  without server restart. YAGNI for Phase 33.
- **cli.py refactor**: extract `review_pipeline()` as importable
  function. Large scope (~300+ LOC), deferred until subprocess proves
  insufficient.
- **Progress streaming**: pipe CLI stderr for real-time progress in
  MCP. Requires subprocess stdout/stderr parsing -- complex, deferred.

</deferred>

---

*Phase: 33-MCP Server*
*Context gathered: 2026-06-29*
