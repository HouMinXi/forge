# Phase 33: MCP Server - Discussion Log

**Date:** 2026-06-29
**Participants:** User + Claude

## Areas Discussed

### 1. CLI Decoupling Strategy
- **Options presented:** A (subprocess), B (fake args), C (extract core logic)
- **User initially selected:** C (extract core logic)
- **User reconsidered:** "MCP能把cmdline整合进来么？" -- questioned whether refactoring was necessary
- **Final selection:** A (subprocess) -- CLI is a stable interface, zero risk
- **Rationale:** All 3 SCs satisfied without touching cli.py. Ponytail ladder: shortest path.

### 2. Tool Surface
- **Options presented:** review+gate-check only / +init / full exposure
- **User selected:** Full exposure (all 5 subcommands)
- **User added:** "MCP是否可以让用户通过UI界面选择可配置后端使用的模型？"
- **Resolution:** backend parameter as enum from gate.yaml -> IDE renders dropdown

### 3. Self-Review Prevention
- **Options presented:** CLI fail-closed only / MCP layer defense / forced --backend
- **User selected:** MCP layer defense-in-depth (resolve-outlet pre-flight)

### 4. Return Format + Timeout
- **Options presented:** text / structured JSON / SARIF
- **User requested:** Exa research on IDE MCP format best practices
- **Research finding:** 2026 best practice = dual-layer (content[] + structuredContent)
- **User selected:** Dual-layer

- **Timeout options presented:** sync / budgeted start / job mode
- **User selected:** Job mode initially
- **User requested:** Further Exa research on A/B/C best practices
- **Research finding:** Python SDK removed Tasks 5/29, SEP-2663 not yet in SDK, Claude Code 60s hard timeout with no Tasks support
- **User final selection:** A (Budgeted Start) with C (Tasks) migration path preserved
- **Quote:** "A，但是需要给C留下基座"

## Claude's Discretion Items
- Subprocess over import (cli.py too coupled)
- 20s budget duration (fits Claude Code 60s window)
- stdio transport only (MCP best practice for local tools)

## Deferred Ideas
- HTTP/SSE transport (team use)
- SEP-2663 Tasks native migration (~2026-07-28)
- Hot-reload gate.yaml
- cli.py review_pipeline() refactor
- Progress streaming from CLI stderr
