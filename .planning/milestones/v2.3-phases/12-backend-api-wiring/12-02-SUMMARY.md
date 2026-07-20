---
phase: 12-backend-api-wiring
plan: 02
subsystem: backend
tags: [backend-wiring, gate-yaml, max-tokens, inline-flags, llm-invoke, factories]
dependency_graph:
  requires:
    - 12-01 (BackendConfig.max_tokens, dict schema, CLI flags)
  provides:
    - gate.yaml backends block loaded into cli.py _run()
    - inline backend flags validated and transient BackendConfig constructed
    - llm_invoke.py uses backend.max_tokens for both Anthropic and OpenAI
    - LLMInvokeError caught at CLI boundary and re-wrapped as CliError
    - per-pass token cost printed to stderr after each llm_invoke call
  affects:
    - src/code_forge/cli.py
    - src/code_forge/llm_invoke.py
    - src/code_forge/factories.py
    - src/code_forge/backend.py (Wave 1 prereq reapplied)
    - tests/test_backend.py (Wave 1 prereq reapplied)
tech_stack:
  added: []
  patterns:
    - lightweight yaml.safe_load gate.yaml loader (D-16, matches outlet_resolver.py pattern)
    - transient BackendConfig construction from inline flags (D-10)
    - LLMInvokeError-to-CliError boundary wrapping (D-04, D-14)
    - sys.stderr.write for per-pass token cost (D-13)
key_files:
  created: []
  modified:
    - src/code_forge/cli.py
    - src/code_forge/llm_invoke.py
    - src/code_forge/factories.py
    - src/code_forge/backend.py
    - tests/test_backend.py
decisions:
  - D-16: lightweight yaml.safe_load for gate.yaml backends (not load_gate_config)
  - D-01: FAIL CLOSED enforced - fallback warn block removed, CliError propagates
  - D-10: all-4-inline-flags required for transient BackendConfig; partial raises CliError
  - D-06: backend.max_tokens replaces hardcoded 4096 in both API invoke paths
  - D-04/D-14: LLMInvokeError caught in _run() and re-wrapped as CliError with backend name
  - D-13: per-pass stderr cost supplements (does not replace) post-verdict summary
metrics:
  duration: "~40 minutes"
  completed: "2026-06-04T13:24:31Z"
  tasks_completed: 5
  tasks_total: 5
  files_modified: 5
---

# Phase 12 Plan 02: Backend API Wiring Summary

**One-liner:** gate.yaml backends loaded into cli.py via yaml.safe_load; inline flags construct transient BackendConfig; llm_invoke.py uses backend.max_tokens; LLMInvokeError wrapped at CLI boundary; per-pass token cost emitted to stderr.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| W1-prereq | Wave 1 prerequisites reapplied (max_tokens, dict schema, CLI flags, tests) | 587d009 | src/code_forge/backend.py, src/code_forge/cli.py, tests/test_backend.py |
| T1+T2+T4 | gate.yaml loader + inline flags validation + LLMInvokeError wrapping | 587d009 | src/code_forge/cli.py |
| T3+T5 | backend.max_tokens in API calls + per-pass stderr token cost | 5c626d9 | src/code_forge/llm_invoke.py, src/code_forge/factories.py |

## Changes Made

### Task 1: Wire gate.yaml backend loading into cli.py _run() (D-16)

Replaced the hardcoded configs=[] and the try/except/warn fallback block with a
lightweight yaml.safe_load loader following outlet_resolver.py:65-103 pattern:
- yaml.safe_load on cwd / ".code-forge" / "gate.yaml"
- FileNotFoundError sets gate_data = None (no backends, D-15 zero-behavior-change)
- yaml.YAMLError converted to CliError (T-12-04 threat mitigated)
- load_backend_configs(gate_data) when gate_data is a dict; else configs = []
- resolve_backend(env, configs=configs, cli_value=getattr(args, 'backend', None))
- D-01 FAIL CLOSED: removed fallback backend = DEFAULT_BACKEND warn block

### Task 2: Inline backend flags validation and transient BackendConfig (D-10)

Before gate.yaml loading, validates mutual exclusion and all-4-required:
- --backend NAME + any inline flag: CliError("--backend and inline flags are mutually exclusive")
- Partial inline flags: CliError("inline backend requires all 4 flags: --backend-url/format/key-env/model")
- All 4 inline flags: constructs BackendConfig(name="inline", type="api", ...) and skips resolve_backend

### Task 3: Update llm_invoke.py to use backend.max_tokens (D-06)

- _invoke_openai body: added "max_tokens": backend.max_tokens after temperature field
- _invoke_anthropic body: replaced hardcoded "max_tokens": 4096 with backend.max_tokens
- Both API paths now read from the same BackendConfig.max_tokens field (default 16384)

### Task 4: LLMInvokeError -> CliError wrapping at CLI boundary (D-04, D-14)

Wrapped the ForgeLock block in try/except. LLMInvokeError imported from llm_invoke
at Step 6 of _run(). Re-raised as CliError with backend name prefix. Exception chain preserved.

### Task 5: Per-pass token cost stderr output (D-13)

In build_l1_provider in factories.py, after each successful llm_invoke call:
- Moved import sys from inline except-block to module top
- Added sys.stderr.write("[backend_name] N in / M out tokens\n") after usage accumulation
- Skips output when both input and output token counts are zero (cli backend)
- Existing post-verdict cost summary at cli.py unchanged

## Verification

- python3 -m py_compile passed on all 4 modified source files
- ruff check: zero new errors (6 pre-existing F821/F401 in factories.py string annotations)
- Non-ASCII check: no non-ASCII characters in new code
- pytest tests/test_backend.py tests/test_cli_parser.py tests/test_llm_invoke.py tests/test_factories.py: 134 passed
- pytest tests/: 1002 tests, 0 failures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Wave 1 prerequisites not present in worktree**
- **Found during:** Task 1 setup
- **Issue:** Worktree was at a18c6d5, not 312cbbf. Sandbox gate denied git reset --hard 312cbbf. Wave 1 changes (BackendConfig.max_tokens, dict-based schema, CLI flags) were missing from worktree.
- **Fix:** Reapplied Wave 1 changes from commits 912277e and 9339f6a by editing worktree files directly. Functionally identical to already-reviewed+merged main branch content.
- **Files modified:** src/code_forge/backend.py, src/code_forge/cli.py, tests/test_backend.py
- **Commit:** 587d009

**2. [Rule 1 - Bug] Pre-existing inline import sys in factories.py**
- **Found during:** Task 5
- **Issue:** Pre-existing import sys inside the except LLMInvokeError handler would shadow the module-level import sys I added, triggering ruff F823.
- **Fix:** Removed the local import sys from the except block; module-level import covers it.
- **Files modified:** src/code_forge/factories.py
- **Commit:** 5c626d9

## Known Stubs

None. All wiring is live: gate.yaml path flows to load_backend_configs and resolve_backend; max_tokens flows from BackendConfig through both API call paths; LLMInvokeError propagates to CliError at the boundary.

## Threat Flags

None beyond what the plan's threat model covers. yaml.safe_load prevents code execution (T-12-04). api_key_env stores env var NAME only (T-12-05). max_tokens is an int with default 16384 (T-12-06).

## Self-Check: PASSED

| Item | Status |
|------|--------|
| src/code_forge/cli.py | FOUND |
| src/code_forge/llm_invoke.py | FOUND |
| src/code_forge/factories.py | FOUND |
| yaml.safe_load in cli.py | FOUND |
| load_backend_configs in cli.py | FOUND |
| inline backend requires all 4 flags | FOUND |
| --backend and inline flags mutually exclusive | FOUND |
| name="inline" transient BackendConfig | FOUND |
| backend.max_tokens in _invoke_anthropic | FOUND |
| backend.max_tokens in _invoke_openai | FOUND |
| except LLMInvokeError in cli.py | FOUND |
| sys.stderr.write token cost in factories.py | FOUND |
| Commit 587d009 | FOUND |
| Commit 5c626d9 | FOUND |
