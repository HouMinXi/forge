---
status: complete
phase: 38-setup-mcp
source: PLAN.md, CONTEXT.md (no SUMMARY.md -- non-GSD delivery)
started: 2026-07-03T17:10:00Z
updated: 2026-07-03T17:25:00Z
---

## Current Test

[testing complete]

## Tests

### 1. setup-mcp --dry-run shows planned writes
expected: `code-forge setup-mcp --dry-run` prints planned config content without writing files
result: pass
evidence: `code-forge setup-mcp --backend deepseek --backend mimo-pro --dry-run` printed user config YAML and gate.yaml content to stderr, exit 0, no files written

### 2. setup-mcp writes user-level config
expected: `code-forge setup-mcp` creates ~/.config/code-forge/config.yaml with backend definitions (api_key_env indirection, no plaintext secrets)
result: pass
evidence: ~/.config/code-forge/config.yaml exists with api_key_env references (MIMO_PRO_API_KEY, DEEPSEEK_API_KEY), zero plaintext secrets. T7 dogfood verified.

### 3. setup-mcp writes project gate.yaml
expected: Running in a project directory creates .code-forge/gate.yaml with outlet+test config only (no backend definitions -- those live in user config)
result: pass
evidence: dry-run output shows generated gate.yaml with only `outlet: subprocess` and `test:` section. Live gate.yaml retains vertex-claude (project-specific, deliberate manual keep during T7 migration).

### 4. Idempotent without --force
expected: Running `code-forge setup-mcp` a second time exits cleanly without overwriting existing config. No silent data loss.
result: pass
evidence: `code-forge setup-mcp --backend deepseek` with existing config prints "User config exists ... (use --force to overwrite)" and "No files written", exit 0. test_idempotent_no_overwrite PASSED.

### 5. --force overwrites existing config
expected: `code-forge setup-mcp --force` overwrites existing user config and gate.yaml even if they already exist
result: pass
evidence: Ran with FORGE_CONFIG_DIR=/tmp/forge-uat-cfg in temp dir. --force wrote new config overwriting previous. test_force_overwrites PASSED.

### 6. --backend selects specific presets
expected: `code-forge setup-mcp --backend deepseek --backend mimo-pro --dry-run` writes only the named presets, not all 7
result: pass
evidence: dry-run with --backend deepseek --backend mimo-pro showed only those 2; dry-run with --backend deepseek alone showed 0 hits for mimo-pro/kimi/minimax/glm.

### 7. Registration command printed
expected: When user config is written, output prints the exact MCP client registration command the user should run (e.g., for claude.json mcpServers entry)
result: pass
evidence: Output includes "claude mcp add forge -e DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY -- code-forge-mcp" when user config written. Not printed when config already exists (idempotent path).

### 8. MCP precheck passes with merged view
expected: After setup-mcp, `code-forge-mcp-pass` server starts successfully. The precheck reads merged user+project config (not just project gate.yaml)
result: pass
evidence: TestCheckBackendMergedView 3/3 PASSED (zero_project+user passes, zero+zero raises, both visible). test_lifespan_merges_project_first_user_appends PASSED.

### 9. Partial keys warn but do not block
expected: If some backends have valid API keys and others don't, server logs a warning listing missing key env vars but does NOT refuse to start
result: pass
evidence: test_preflight_partial_keys_warns_but_passes PASSED.

### 10. Preset table correctness
expected: Presets use verified vendor endpoints -- Kimi: api.kimi.com/coding/v1 model K2.7-Code; MiniMax: api.minimaxi.com/anthropic model MiniMax-M3; GLM: model glm-5.2
result: pass
evidence: PRESETS dict verified: kimi base_url=https://api.kimi.com/coding/v1 model=K2.7-Code, minimax base_url=https://api.minimaxi.com/anthropic model=MiniMax-M3, glm model=glm-5.2. All match vendor docs.

## Summary

total: 10
passed: 10
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]

## Notes

- 2 pre-existing test failures in TestResolveWorkspace (not Phase 38 scope)
- `python -m code_forge.cli` invocation returns stale code; use `code-forge` entry point
- T7/T8 manual dogfood completed in prior session (mimo-pro migration + scratch project MCP review)
- Full regression: 2443 passed, 7 skipped at merge time (07d0381)
