# Phase 38.1: MCP Server Process Lifecycle Hardening

## Goal

Server processes leaked indefinitely: the inherited CLI signal handler
cannot terminate the async server, abandoned stdio pipes never deliver
EOF, and the job TTL watchdog kills legitimately-running long reviews.
Fix the signal path, remove the premature kill, add job progress
visibility, and close three observed operational gaps.

## Root Cause (proven empirically, not hypothesized)

`_install_signal_handlers()` (llm_invoke.py:475) installs a SIGTERM
handler at module load time. The MCP server imports llm_invoke
transitively, inheriting the handler. On SIGTERM the handler calls
`raise KeyboardInterrupt` (llm_invoke.py:500), which the asyncio
runner swallows -- the process survives. Reproduced: an idle server
with stdin held open ignores SIGTERM (state S, wchan futex_do_wait)
yet exits promptly on stdin EOF. The handler itself is correct for
the CLI context; the server must install its own disposition.

Six live orphan servers accumulated over multiple days in a single
session. All ignored SIGTERM; SIGKILL was required. Parent was alive
the whole time, so parent-death detection would have caught none.

## Scope

**In scope (7 tasks + 2 companions, separate commits):**

1. Server SIGTERM/SIGINT handler via `loop.add_signal_handler()` in
   lifespan startup. Run `cleanup_all()`, then exit for real.
2. Probe what Claude Code does on /mcp reconnect (SIGTERM? stdin
   close? neither?). Decides if signal fix alone stops accumulation.
3. Remove `_evict_stale()` running-branch kill (mcp_jobs.py:167-171).
   Running procs keep one forced-death owner: `cleanup_all()` at
   shutdown. Terminal entries past TTL still evicted.
4. Job progress visibility: redirect child stderr to tempfile,
   `forge_job_status` returns tail + real `duration_s` while running.
5. Contract compliance diagnosis (factories.py injection sites).
6. Wall-clock attribution (CLI-direct profiling, decision gate).
7. `forge_job_status` error message enrichment for post-restart
   job_id queries.
8. Manual recovery recipe documentation.
9. Optional parent-death guard (only if small; secondary layer).

**Out of scope:**
- Changes to llm_invoke's own handler (correct for CLI)
- Review pipeline semantics, gate.yaml schema, trust model
- Job-state persistence layer (zero observed demand)

## Design Constraints

- C1: `loop.add_signal_handler(SIGTERM, ...)` replaces llm_invoke
  handler for server only; CLI behavior untouched (last install wins)
- C2: Do NOT close/dup2 fd 0 from handler (self-close is a no-op on
  Linux when read() is blocked in another thread)
- C3: Do NOT chain to llm_invoke handler (its raise is the broken path)
- C4: Shutdown task must unlink both contract tempfiles and stderr
  log paths (integration with progress visibility)
- C5: _evict_stale keeps terminal-entry eviction; only the
  running-kill branch is removed
- C6: Contract compliance is prompt-side best-effort, not a
  guarantee -- state honestly in delivery report

## Dependencies

- Phase 38: setup-mcp (merged 07d0381) -- prerequisite met
- No blocking dependency on other phases

## Anchor Verification

| Claim | File:Line | Verified |
|-------|-----------|----------|
| llm_invoke handler installs at import | llm_invoke.py:510-511 | yes |
| Handler raises KeyboardInterrupt | llm_invoke.py:500 | yes |
| lifespan calls cleanup_all on teardown | mcp_server.py:110 | yes |
| cleanup_all terminate+kill escalation | mcp_jobs.py:106-117 | yes |
| _evict_stale kills running procs | mcp_jobs.py:167-171 | yes |
| duration_s=0.0 hardcoded | mcp_server.py:744 | yes |
| Unknown job_id message | mcp_server.py:729 | yes |
| main() runs mcp.run(stdio) | mcp_server.py:774-776 | yes |
| _run_cli_budgeted call sites | mcp_server.py:488,571,635 | yes |
| entry["created_at"] exists | mcp_jobs.py:90 | yes |
