---
phase: 36-api-backend-onboarding
plan: 01
subsystem: core
tags: [bugfix, edge-cases, error-handling]
dependency_graph:
  requires: []
  provides: [clean-error-paths, verdict-consistency, patch-preservation]
  affects: [llm_invoke, machine, hold, fixval, diagnose, cli, outlet_resolver]
tech_stack:
  added: []
  patterns: [guard-before-index, conditional-cleanup, error-type-promotion]
key_files:
  created: []
  modified:
    - src/code_forge/machine.py
    - src/code_forge/hold.py
    - src/code_forge/fixval.py
    - src/code_forge/diagnose.py
    - src/code_forge/cli.py
    - src/code_forge/outlet_resolver.py
decisions:
  - "MCP-19 already fixed (no CliError in llm_invoke.py) -- skipped, no code change needed"
  - "MCP-39 Option A chosen: _parse_outlet_string raises CliError (CLI-facing function, correct error type)"
metrics:
  duration: 191s
  completed: 2026-07-01
---

# Phase 36 Plan 01: Edge-Path Crash Fixes Summary

Surgical fixes for 7 crash/annotation sites (MCP-19/20/23/39/40/46/47/48) that produced tracebacks instead of clean errors.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix crash sites in machine/hold/fixval/diagnose | 818074a | machine.py, hold.py, fixval.py, diagnose.py |
| 2 | Fix crash sites in cli.py and outlet_resolver.py | 7bf63b0 | cli.py, outlet_resolver.py |

## Changes

### MCP-19 (BLOCKER): CliError in llm_invoke.py
Already fixed -- no CliError references remain in llm_invoke.py. No code change needed.

### MCP-20 (HIGH): _run_ci bare return
Changed bare `return` to `return Verdict.PENDING` when mutation PID is still alive, preventing None propagation to callers expecting a Verdict enum.

### MCP-46 (MEDIUM): _run_l1_phase annotation
Corrected return type from `-> list[StateFinding]` to `-> tuple[list[StateFinding], list[dict]]` matching the actual 2-tuple return.

### MCP-47 (MEDIUM): hold.py line_range IndexError
Guarded `finding.line_range[0]` and `[1]` access with length checks, defaulting to 0 for short/empty lists.

### MCP-48 (MEDIUM): diagnose_non_convergence context
Kept the "D" return (infra-dominant semantics correct) but now appends convergence detail (A/B/C secondary signals) to `infra_errors` so the caller's ESCALATED message carries context.

### MCP-40 (HIGH): fixval patch preservation
Added `restore_ok` flag; outer finally only unlinks patch file when restore succeeded. Failed restores and unhandled exceptions preserve the patch for manual recovery.

### MCP-23 (MEDIUM): verify subcommand missing git
Wrapped `subprocess.run(["git", ...])` in try/except FileNotFoundError, following the existing pattern from mutation-check and e2e-check.

### MCP-39 (MEDIUM): invalid outlet string traceback
Changed `_parse_outlet_string` to raise CliError instead of ValueError. The review path's existing `except CliError` chain catches it cleanly. The diagnostic path's separate `except ValueError` still catches internal parsing errors from `load_outlet_from_gate`.

## Deviations from Plan

### Auto-observed

**1. [Deviation] MCP-19 already fixed**
- **Found during:** Task 1 read_first
- **Issue:** Plan expected CliError raises at llm_invoke.py lines 889 and 978; grep found zero CliError references
- **Action:** Skipped -- no code change needed, all raises already use LLMInvokeError
- **Impact:** None -- one fewer fix, acceptance criteria still met (no CliError in llm_invoke.py)

## Self-Check: PASSED
