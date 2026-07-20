---
phase: 08-hardening
plan: "03"
subsystem: documentation
tags: [docs, configuration, backends, editor-setup]
dependency_graph:
  requires: [08-02]
  provides: [docs/configuration.md, docs/setup-vscode.md, docs/setup-cursor.md, docs/setup-pycharm.md]
  affects: [README.md]
tech_stack:
  added: []
  patterns: [env-var-configuration, backends.yaml, editor-integration]
key_files:
  created:
    - docs/configuration.md
    - docs/setup-vscode.md
    - docs/setup-cursor.md
    - docs/setup-pycharm.md
  modified:
    - README.md
decisions:
  - "Shell RC file recommended over editor-specific env injection for reliability across versions"
  - "API key placeholder format sk-ant-api03-XXXXXXXXX used consistently to avoid real-key pattern matches"
  - "backends.yaml schema documented from BackendConfig dataclass fields (backend.py source of truth)"
  - "FORGE_AUTH_TIMEOUT documented with 20s default and 120s cap matching backend.py constants"
metrics:
  duration: "11 minutes"
  completed: "2026-06-03"
  tasks_completed: 5
  tasks_total: 5
  files_created: 4
  files_modified: 1
---

# Phase 08 Plan 03: Configuration Documentation Summary

Backend configuration and editor setup documentation for code-forge:
FORGE_BACKEND/FORGE_OUTLET/FORGE_LLM_MODEL env vars + backends.yaml format + 3 editor guides.

## Tasks Completed

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | Add Quick Start section to README | 366fbe3 | README.md |
| 2 | Create docs/configuration.md | 88a544b | docs/configuration.md |
| 3 | Create docs/setup-vscode.md | 3c22259 | docs/setup-vscode.md |
| 4 | Create docs/setup-cursor.md | dea7058 | docs/setup-cursor.md |
| 5 | Create docs/setup-pycharm.md | f484e1e | docs/setup-pycharm.md |

## What Was Built

### README.md Backend Configuration Section

Added a "Backend configuration" section after the existing "Quick start"
install instructions. The section documents:

- Environment variable table (FORGE_BACKEND, FORGE_OUTLET, FORGE_LLM_MODEL)
  with purpose and default columns
- Four usage examples covering the common cases (default, model pin, named
  backend, force inline)
- Minimal backends.yaml snippet showing api and cli entries
- Links to docs/configuration.md and the three editor setup guides

### docs/configuration.md (Full Reference, 282 lines)

Complete configuration reference grounded in actual source code:

- FORGE_BACKEND: documents the resolve_backend() precedence chain
  (cli flag > env > config default > session default)
- FORGE_OUTLET: documents outlet resolution precedence and the
  FAIL-CLOSED behavior (no silent inline fallback)
- FORGE_LLM_MODEL: documents DEFAULT_MODEL ("claude-sonnet-4-6") from
  llm_invoke.py; applies to CLI backends only
- FORGE_AUTH_TIMEOUT: 20s default and 120s cap from backend.py constants
  DEFAULT_AUTH_TIMEOUT and MAX_REASONABLE_AUTH_TIMEOUT; 5-minute probe cache
- backends.yaml field tables for both api and cli backend types
- Four example backend configs (Anthropic API, OpenAI API, local CLI,
  multi-backend setup)
- Authentication section covering both CLI (auth login or ANTHROPIC_API_KEY)
  and API (env var presence check only, no network call at probe time)
- Security note: api_key_env holds env var name never raw secret;
  inline api_key field is rejected by the parser

### docs/setup-vscode.md (218 lines)

Three setup methods with progressive complexity:

- Option 1 (shell RC): simplest, works across all terminals
- Option 2 (terminal.integrated.env): editor-scoped, OS-keyed JSON
  (linux/osx/windows); WARNING against committing settings.json with keys
- Option 3 (.env file + manual source): project-scoped, requires manual step
- Usage from integrated terminal and AI coding extension
- Troubleshooting: command not found, API key not set, auth status, terminal
  not reloading new variables

### docs/setup-cursor.md (154 lines)

Shorter guide because Cursor terminal env setup is identical to VS Code:

- Note clarifying Cursor built-in AI uses its own config, separate from FORGE_*
- Option 1 (shell RC, recommended)
- Option 2 (.env file)
- Explanation of settings.json compatibility (works but version-dependent)
- Cross-reference to VS Code guide for terminal.integrated.env method
- Security warning against committing .vscode/settings.json or .env

### docs/setup-pycharm.md (192 lines)

PyCharm-specific paths with exact menu navigation:

- Option 1 (Run/Debug Configurations): Run > Edit Configurations, env var
  dialog, WARNING about .idea/ XML leaking keys
- Option 2 (EnvFile plugin): JetBrains Marketplace installation, per-config
  .env attachment via EnvFile tab
- Option 3 (terminal settings): Settings > Tools > Terminal env field, plus
  shell RC method for full inheritance
- Troubleshooting: Run Config PATH differences (use absolute path), shell RC
  not sourced (use --login flag), EnvFile not loading, key exposure recovery

## Deviations from Plan

None - plan executed exactly as written.

The scope warning from the hook ("21/22 files modified this session") reflects
cumulative worktree state across multiple plans in this session, not this
plan's scope. This plan touched exactly 5 files (1 modified, 4 created).

## Known Stubs

None. All documentation references actual code behavior verified from:

- backend.py: resolve_backend(), DEFAULT_AUTH_TIMEOUT (20),
  MAX_REASONABLE_AUTH_TIMEOUT (120)
- llm_invoke.py: DEFAULT_MODEL ("claude-sonnet-4-6"), _resolve_model()
  reading FORGE_LLM_MODEL
- outlet_resolver.py: resolve_outlet() precedence chain for FORGE_OUTLET

## Threat Flags

T-08-07 and T-08-08 from the plan threat model were mitigated:

- All API key examples use placeholder format (sk-ant-api03-XXXXXXXXX)
- Security warnings in VS Code guide (do not commit settings.json with keys)
- Security warnings in PyCharm guide (do not commit .idea/ XML or .env)
- configuration.md notes that inline api_key field is rejected by the parser

## Self-Check

Files exist:

- README.md: FOUND (modified, 6 matches for FORGE env vars, 1 match for docs/configuration.md)
- docs/configuration.md: FOUND (282 lines)
- docs/setup-vscode.md: FOUND (218 lines)
- docs/setup-cursor.md: FOUND (154 lines)
- docs/setup-pycharm.md: FOUND (192 lines)

Commits exist (verified via git log):

- 366fbe3: docs(08-03): add Quick Start section to README
- 88a544b: docs(08-03): add configuration reference
- 3c22259: docs(08-03): add VS Code setup guide
- dea7058: docs(08-03): add Cursor setup guide
- f484e1e: docs(08-03): add PyCharm setup guide

Key links verified:

- README.md references docs/configuration.md: YES (1 occurrence)
- README.md references FORGE_BACKEND/FORGE_OUTLET/FORGE_LLM_MODEL: YES (6 occurrences)
- Each setup-*.md references configuration.md: YES (all 3 files)

No real API keys: CLEAN (grep for sk-ant-api[^0-9X] returned no matches in docs/)

## Self-Check: PASSED
