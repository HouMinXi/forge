---
phase: 33-mcp-server
plan: 01
subsystem: mcp
tags: [mcp, fastmcp, stdio, tools, async]
dependency_graph:
  requires: [cli.py, exit_codes.py, errors.py]
  provides: [mcp_server.py, mcp_jobs.py, code-forge-mcp entry point]
  affects: [pyproject.toml]
tech_stack:
  added: [mcp>=1.27 (FastMCP stdio server)]
  patterns: [budgeted-start with asyncio.shield, pop-on-retrieval job state, dual-layer CallToolResult]
key_files:
  created:
    - src/code_forge/mcp_server.py
    - src/code_forge/mcp_jobs.py
  modified:
    - pyproject.toml
decisions:
  - "Manual CallToolResult over structured_output=True for dual-layer return (D-33-13)"
  - "str | None backend param with runtime validation over Literal/Enum (D-33-17)"
  - "_load_gate_backends emptiness check over resolve_outlet HTTP probe (D-33-23)"
metrics:
  duration: 256s
  completed: 2026-06-29T06:57:49Z
---

# Phase 33 Plan 01: MCP Server + Job State Summary

FastMCP stdio server exposing 6 forge tools (review, gate-check, init, trust, resolve-outlet, job-status) with budgeted-start async subprocess pattern and dual-layer CallToolResult responses.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | mcp_jobs.py -- budgeted-start job state | 9188ff6 | src/code_forge/mcp_jobs.py |
| 2 | mcp_server.py + pyproject.toml -- FastMCP server | 5a5fe83 | src/code_forge/mcp_server.py, pyproject.toml |

## Verification Results

1. mcp_server.py imports without error -- PASS
2. mcp_jobs.py exports (start_job, get_job, ForgeResult, ForgeJobRef) -- PASS
3. Zero print() calls in both modules -- PASS (grep returns 0/0)
4. code-forge-mcp entry point in pyproject.toml -- PASS
5. mcp optional dependency group present -- PASS
6. 6 tools registered in FastMCP instance -- PASS
7. ForgeResult.findings_count defaults to None -- PASS
8. exit_to_verdict maps all 8 codes (0-7) -- PASS

## Key Implementation Details

- **Budgeted-start pattern**: asyncio.create_task wraps proc.communicate(), asyncio.shield protects it from wait_for cancellation. On timeout, the live inner_task (not the cancelled shield wrapper) passes to start_job.
- **Pre-flight _check_backend**: checks gate.yaml existence (D-33-30) then calls _load_gate_backends for trust/emptiness (D-33-23). No resolve_outlet call avoids 2-7s HTTP probe latency.
- **Job lifecycle**: pop-on-retrieval for terminal states (D-33-15). TTL eviction kills stale running procs but leaves entries for _wait_for_job (D-33-32). Tempfile deleted in _wait_for_job, not handler finally (D-33-18/D-33-28).
- **CancelledError handling**: _run_cli_budgeted catches CancelledError (IDE abort), kills proc and inner_task before re-raising (D-33-29).

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all tools are fully wired to CLI subprocess invocation.
