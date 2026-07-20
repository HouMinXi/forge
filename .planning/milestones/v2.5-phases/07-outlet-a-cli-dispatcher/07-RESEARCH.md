---
phase: 07
name: Outlet A CLI Dispatcher
date: 2026-06-02
confidence: HIGH
---

# Phase 7 Research: Outlet A CLI Dispatcher

## GA2: llm_invoke.py modification

**Current signature** (llm_invoke.py:47): `llm_invoke(prompt, model=None, timeout_s=120)`

**Target**: `llm_invoke(prompt, backend: BackendConfig, timeout_s=120)`

**Dispatch by backend.type:**
- `"cli"`: subprocess (existing path). Binary from `backend.command` or default `"claude"`.
  Command: `<binary> -p <prompt> --model <model> --output-format json`
- `"api"`: HTTP call via stdlib `urllib.request` (no new dependency).
  - OpenAI format: POST `<base_url>/v1/chat/completions`, `Authorization: Bearer <key>`
  - Anthropic format: POST `<base_url>/v1/messages`, `x-api-key: <key>`
  Response normalization: extract text content from provider-specific response, parse as JSON.

**Callers to update** (2 total):
- factories.py:227 (l1_provider) -- pass resolved backend
- falsify_real.py:38 (RealFalsifier) -- pass resolved backend

## BackendConfig binary field (D-06 gap)

**Current fields** (backend.py:59-72): name, type, model, format, base_url, api_key_env, default.
**NO `command` field exists** (V2 verified).

**Recommendation**: Add `command: str = ""` to BackendConfig. Empty string = default binary ("claude" for cli type). Non-empty = explicit binary (e.g., "aicc").

**_parse_backend_entry** (backend.py:90-156): needs update to read optional `command` from config dict.

## GA1: SKILL.md bridge

**Placeholder** (SKILL.md:1338): "Outlet A (CLI dispatch) not yet implemented"

**Replace with** (thin trigger per D-01/D-02):
```
code-forge review [--committed] [paths]
exit 0 -> PASS
exit non-zero -> read stderr + .code-forge/state.json, report, STOP
```

**D-03 committed mapping**: Add `--committed` flag to review subparser. cli.py translates to `--baseline HEAD~1 --head HEAD` (or merge-base equivalent). Default `code-forge review` (no flag) reviews uncommitted changes.

## --outlet flag

**Location**: review subparser in cli.py (after existing args, ~line 200).
**Precedence**: `--outlet` CLI flag > FORGE_OUTLET env > gate.yaml outlet > auth probe.
**outlet_resolver.py**: needs a `cli_value` parameter to accept the flag value. Currently only accepts env + gate_yaml_path.

## Test coverage

**Existing tests**:
- test_llm_invoke.py: ~10 tests (cli subprocess path only)
- test_backend.py: probe tests
- test_factories.py: l1_provider with stub engine
- test_falsify_real.py: falsification tests

**New tests needed**:
- llm_invoke api-type dispatch (mock urllib)
- BackendConfig.command field parsing
- --outlet flag + precedence
- --committed flag mapping
- SKILL.md bridge text verification (grep-based)

## GA4 documentation location

**Recommendation**: Document in SKILL.md itself, adjacent to the outlet branch point. The non-equivalence note should be visible where the user encounters the outlet choice, not in a separate doc they might not find.

## Dependency analysis

**No new dependencies needed**. All stdlib: urllib.request (HTTP), subprocess (CLI), json (parsing).
pyproject.toml:18-21 lists only pyyaml + unidiff as runtime deps.
