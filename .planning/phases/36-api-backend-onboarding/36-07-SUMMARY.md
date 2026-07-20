---
phase: 36-api-backend-onboarding
plan: 07
subsystem: mcp-server, trust, cli
tags: [mcp, workspace-resolution, trust-model, worktree-guard]
dependency_graph:
  requires: [36-06]
  provides: [FORGE_PROJECT_DIR workspace resolution, dangerous-fields-only trust hash, --allow-main worktree bypass]
  affects: [mcp_server.py, trust.py, cli.py]
tech_stack:
  patterns: [per-request workspace resolution, trust hash migration fallback]
key_files:
  created:
    - docs/setup-mcp.md
  modified:
    - src/code_forge/mcp_server.py
    - src/code_forge/trust.py
    - src/code_forge/cli.py
    - tests/test_mcp_server.py
    - tests/test_trust.py
decisions:
  - "FORGE_PROJECT_DIR is primary workspace source; cwd is fallback, not walkup"
  - "Trust hash covers only DANGEROUS_FIELDS frozenset; legacy hash used as migration fallback"
  - "--allow-main and FORGE_ALLOW_MAIN are ergonomic aliases for FORGE_SKIP_WORKTREE_CHECK"
metrics:
  duration: 609s
  completed: 2026-07-01
  tasks_completed: 2
  tasks_total: 3
---

# Phase 36 Plan 07: MCP Workspace Resolution, Trust Model, Worktree Guard Summary

MCP workspace resolution via FORGE_PROJECT_DIR env var with dangerous-fields-only trust hash and --allow-main worktree bypass.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Fix MCP workspace resolution and trust re-read | 969d601 | mcp_server.py, docs/setup-mcp.md, tests/test_mcp_server.py |
| 2 | Lighten trust model and add --allow-main | 044b2a7 | trust.py, cli.py, tests/test_trust.py |

## Task 3: Checkpoint (pending)

Task 3 is a human-verify checkpoint requiring manual validation of:
- FORGE_PROJECT_DIR-based workspace resolution
- Trust surviving benign gate.yaml edits (model name change)
- --allow-main flag acceptance

## Changes Made

### Task 1: MCP workspace resolution (MCP-01, MCP-02, MCP-03, MCP-04, MCP-06)

- Added `_resolve_project_dir()` helper that checks FORGE_PROJECT_DIR env var first, validates gate.yaml exists, falls back to cwd
- Updated all tool handlers (forge_review, forge_gate_check, forge_init, forge_trust, forge_resolve_outlet) to use resolved project dir as subprocess cwd
- `_check_backend()` now returns the resolved Path so callers can pass it to CLI runners
- Added API key provenance diagnostic to forge_resolve_outlet output (MCP-06)
- Created docs/setup-mcp.md with FORGE_PROJECT_DIR configuration and zombie process workaround (MCP-04)
- Updated 33 existing MCP server tests for new resolution behavior

### Task 2: Trust model and worktree guard (MCP-05, MCP-07, MCP-08)

- `hash_backends_block()` now hashes only fields in DANGEROUS_FIELDS frozenset (base_url, api_key_env, api_key_file, credentials_path, shell, command, hook)
- Legacy all-fields hash kept as `_hash_all_backends()` for migration
- `is_trusted()` tries new hash first, falls back to legacy hash for silent one-time migration
- Trust invalidation prints a clear message to stderr naming the field category
- Added `--allow-main` flag to review parser
- Worktree guard respects `--allow-main`, `FORGE_ALLOW_MAIN=1`, and existing `FORGE_SKIP_WORKTREE_CHECK=1`
- Guard error message now documents both escape mechanisms
- Updated trust tests: hash sensitivity tests use dangerous fields, added test for benign field ignorance

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed --no-color from MCP CLI args**
- **Found during:** Task 1
- **Issue:** mcp_server.py passed `--no-color` to CLI but cli.py has no such flag
- **Fix:** Removed the invalid flag
- **Files modified:** src/code_forge/mcp_server.py, tests/test_mcp_server.py

**2. [Rule 3 - Blocking] Plan referenced non-existent _WORKSPACE variable**
- **Found during:** Task 1
- **Issue:** Plan described _WORKSPACE at line 57 referenced at 11 sites, but actual code uses Path.cwd() directly (file is 403 lines not 632)
- **Fix:** Implemented the same intent (per-request workspace resolution) against the actual code structure
- **Files modified:** src/code_forge/mcp_server.py

## Verification

- 33/33 MCP server tests pass
- 23/23 trust tests pass
- --allow-main flag accepted by review parser (verified via PYTHONPATH import from worktree)
- Syntax check passes for all modified files
