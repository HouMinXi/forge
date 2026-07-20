---
status: complete
phase: 12-backend-api-wiring
source: [12-VERIFICATION.md]
started: 2026-06-04T16:32:00Z
updated: 2026-06-09T11:25:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Real mimo backend execution
expected: Run `code-forge review --backend mimo` with `MIMO_API_KEY` set against a real diff.
Review executes via mimo API endpoints; per-pass stderr shows `[mimo] N in / M out tokens`; no claude CLI subprocess invoked.
result: pass

### 2. FORGE_BACKEND=deepseek env routing
expected: Set `FORGE_BACKEND=deepseek` (no `--backend` flag), configure deepseek in gate.yaml, run `code-forge review`.
Routes to deepseek backend; zero claude tokens consumed; `[deepseek]` prefix in stderr token output.
result: pass

## Summary

total: 2
passed: 2
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

## Security Note

**`/proc/<pid>/environ` exposure (CVE-class: CWE-214)**

When testing with `export MIMO_API_KEY=<key>`, the key is visible to any same-uid process via `/proc/<pid>/environ`. Use safer alternatives during testing:

```bash
# Option 1: interactive read (no history, no export)
read -rs MIMO_API_KEY
code-forge review --backend mimo

# Option 2: inline (no persistent export)
MIMO_API_KEY=$(cat ~/.forge/mimo.key) code-forge review --backend mimo

# Option 3: pass/keyring
MIMO_API_KEY=$(pass show forge/mimo) code-forge review --backend mimo
```

**Root cause in forge:** `BackendConfig.api_key_env` requires the user to `export` a key into the env, which forge inherits at fork time -- that snapshot enters `/proc/<forge-pid>/environ`. See follow-up item below.

**Follow-up fix tracked:** `api_key_file:` support in BackendConfig (key read from chmod-600 file, no env var required). Tracked in REQUIREMENTS.md.
