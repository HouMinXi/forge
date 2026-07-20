---
phase: 36-api-backend-onboarding
plan: 05
subsystem: error-handling
tags: [silent-failures, diagnostics, MCP, gate-check, corpus]
dependency_graph:
  requires: ["36-01", "36-04"]
  provides: ["infra_errors propagation", "stderr surfacing", "corpus validation"]
  affects: ["runner.py", "machine.py", "mcp_server.py", "runtime.py", "parsers/_sarif.py", "gate_check.py", "eval/corpus.py", "cli.py"]
tech_stack:
  added: []
  patterns: ["infra_errors list propagation", "per-item exception recovery"]
key_files:
  created: []
  modified:
    - src/code_forge/mcp_server.py
    - src/code_forge/runner.py
    - src/code_forge/machine.py
    - src/code_forge/runtime.py
    - src/code_forge/parsers/_sarif.py
    - src/code_forge/gate_check.py
    - src/code_forge/eval/corpus.py
    - src/code_forge/cli.py
    - tests/test_runner.py
decisions:
  - "MCP-35 stderr propagation done at machine.py callsite rather than threading stderr through every parser signature -- avoids architectural change to parser dispatch chain"
  - "SARIF per-item catch returns ToolError only when zero valid findings collected AND exceptions occurred -- partial results preserved"
metrics:
  duration_seconds: 776
  completed: 2026-07-01
---

# Phase 36 Plan 05: Surface Silent Failures (Pattern D) Summary

Surfaced 10 silent-failure sites across MCP, runner, runtime, SARIF, gate_check, corpus, and CLI so errors produce visible diagnostics instead of zero findings or empty output.

## Task Summary

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Surface stderr in MCP, fix runner/runtime/sarif | b2c0fd2 | MCP stderr forwarding, run_tools 4-tuple with infra_errors, receipt logging, SARIF per-item recovery |
| 2 | Fix gate_check diagnostics and corpus validation | d680e14 | Signal-kill diagnostic, test stderr printing, corpus verdict validation, smoke-run --timeout |

## Findings Addressed

| ID | Severity | Fix |
|----|----------|-----|
| MCP-16 | MEDIUM | MCP init/trust/resolve-outlet now capture and forward stderr to _make_simple_result |
| MCP-21 | MEDIUM | run_tools returns infra_errors list; timeout/OSError surfaced through verdict diagnostics |
| MCP-22 | LOW | Malformed smoke receipts log warning with file path instead of bare pass |
| MCP-24 | MEDIUM | smoke-run --timeout flag (default 300s) with TimeoutExpired catch |
| MCP-35 | LOW | ToolError stderr populated from runner tuple at machine.py callsite |
| MCP-36 | MEDIUM | SARIF per-item exception handling preserves valid findings before malformed item |
| MCP-37 | MEDIUM | Corpus loader rejects expected_verdict outside {"HOLD","PASS"} with ValueError |
| MCP-43 | LOW | Negative exit codes print signal-kill diagnostic before returning BLOCK |
| MCP-44 | MEDIUM | Test stderr printed before failure message for all non-zero exit codes |
| MCP-45 | LOW | Comment documents pytest-only limitation of baseline delta parser |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] runner.py 3-to-4-tuple signature change**
- **Found during:** Task 1
- **Issue:** Changing run_tools return from 3-tuple to 4-tuple broke existing test unpackings
- **Fix:** Updated all 7 unpacking sites in tests/test_runner.py; added infra_errors assertion to test_run_tool_none_adds_to_skipped
- **Files modified:** tests/test_runner.py
- **Commit:** b2c0fd2

**2. [Rule 2 - Missing functionality] MCP-35 stderr propagation approach**
- **Found during:** Task 1
- **Issue:** Plan suggested fixing ToolError stderr in parsers, but threading stderr through the parser dispatch chain (parse_output -> every parser) is architectural
- **Fix:** Attached tool stderr at machine.py callsite where both ToolError.stderr and runner stderr tuple are available -- same diagnostic value, zero signature changes
- **Files modified:** src/code_forge/machine.py
- **Commit:** b2c0fd2

## Deferred

- MCP-25 (mutation-check hardcoded pytest) requires --test-command flag design beyond this phase scope

## Self-Check: PASSED
