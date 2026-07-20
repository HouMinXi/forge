# Phase 38: setup-mcp -- One-Command MCP Onboarding

## Goal

`code-forge setup-mcp` one command completes full MCP onboarding:
writes user-level backend config, project gate.yaml (outlet+test only),
auto-trusts, prints client registration command.

Milestone standard: "from any subdirectory, forge MCP review works."

## Design Constraints (from ruling R1-R5)

- C1: Backend defaults write to user-level (~/.config/code-forge/config.yaml)
- C2/S3: _check_backend precheck reads merged view (user + project)
- C3: Idempotent, no silent overwrite (--force required)
- C4: Auto-trust only what setup-mcp just wrote
- C5: No secrets on disk (api_key_env indirection)
- C6: Dogfood: migrate mimo-pro from project to user-level
- C7: Onboarding演練: scratch project MCP review end-to-end

## Ruling Modifications

- R1: Preset table from memory matrix, not init_template (Kimi/MiniMax stale)
- R2: S3 via cli._merge_user_into, no third implementation
- R3: No vertex preset (project-specific)
- R4: Trust backends-less gate.yaml (hash of "{}")
- R5: C7 must go through MCP server, not just CLI

## Dependencies

- Phase 37: user-level config (D1-D5) -- merged 6fb427e
- Phase 37.1: F5 backend passthrough -- merged 965c247
