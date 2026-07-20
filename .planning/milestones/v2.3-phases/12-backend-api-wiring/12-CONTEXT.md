# Phase 12: Backend API Wiring - Context

**Gathered:** 2026-06-04
**Status:** Ready for planning (rev 3 after round 2 cross-model review)

<domain>
## Phase Boundary

Wire gate.yaml backends block into cli.py so code-forge review can route
L1 review and falsification LLM calls to third-party models (mimo, deepseek,
kimi) via api (openai/anthropic format). Fix max_tokens truncation. Clean up
F1/F2/F3 cli.py debt. Extend --whole-file to multi-file.

This phase does NOT add new review capabilities. It routes existing engine
calls through configurable backends.

</domain>

<decisions>
## Implementation Decisions

### Backend Resolution (fallback behavior)
- **D-01:** FORGE_BACKEND=X with X not in gate.yaml backends -> CliError (FAIL CLOSED). Consistent with outlet_resolver.py:16-17 (backend unreachable raises CliError). **Caveat (D-15 reconciliation):** this is a behavior change for any existing user who has FORGE_BACKEND set without a gate.yaml. D-15 is narrowed: zero behavior change when NO backend config is present (no gate.yaml, no FORGE_BACKEND, no --backend flags). When backend config IS present, FAIL CLOSED applies.
- **D-02:** `--backend NAME` selects a named backend from gate.yaml (passed as cli_value to resolve_backend). This is SEPARATE from inline flags (D-10). `--backend NAME` requires gate.yaml with that name defined; without gate.yaml -> CliError.
- **D-03:** Multiple backends in gate.yaml -> user marks one as `default: true`. Multiple defaults -> CliError. No default marked -> first backend in list (configs[0], matching current resolve_backend behavior at backend.py:266). No configs at all -> DEFAULT_BACKEND (cli type, session model, backend.py:269).
- **D-04:** api_key_env points to unset env var -> CliError at the CLI boundary. Specifically: llm_invoke.py continues to raise LLMInvokeError internally; cli.py catches LLMInvokeError and re-wraps as CliError with message "backend X needs env var Y but it is not set". This preserves module boundaries (llm_invoke does not import CliError).

### max_tokens Strategy
- **D-05:** BackendConfig gains a max_tokens field (integer, optional, default 16384). Rationale: current hardcoded 4096 in _invoke_anthropic truncates review JSON when diff content is included (review responses regularly exceed 4096 tokens). 16384 provides 4x headroom within all target provider limits (mimo, deepseek, kimi all support >= 16K output).
- **D-06:** _invoke_anthropic: replace hardcoded 4096 (llm_invoke.py:406) with backend.max_tokens. _invoke_openai: ADD max_tokens to the request body (currently absent at llm_invoke.py:357-361 -- only model/messages/temperature are set). Both read from the same backend.max_tokens field. No provider-default reliance.

### F1/F2/F3 Cleanup
- **D-07:** F1 (cli.py:1022-1036 for-loop over flag conflicts): pure refactor. Flatten the for-loop body into 4 independent if checks. The loop pretends 4 flags are uniform but each branch is unique. No behavior change. (Note: lines 1019-1021 are the whole_file guard, lines 1037-1042 are paths check + return -- neither is part of the loop.)
- **D-08:** F2 (whole_file logic): the pattern `whole_file = getattr(args, "whole_file", None); if whole_file is not None:` appears at BOTH cli.py:1020-1021 (_build_baseline_specs) AND cli.py:1101-1103 (_paths). Merge into one function that returns (baseline, head, paths) tuple. Order: D-09 (nargs expansion) FIRST, then D-08 (merge) -- so the merged function handles list from the start.
- **D-09:** F3 (--whole-file): expand to multi-file (nargs='+'). Keep single-repo scope. Validation: all paths must resolve under cwd (reject absolute paths or paths outside the repo root).

### Inline Backend Flags
- **D-10:** 4 independent flags for no-gate.yaml quick trial: --backend-url URL, --backend-format openai|anthropic, --backend-key-env VAR_NAME, --backend-model MODEL_NAME. All 4 required because api calls pass backend.model to the provider (llm_invoke.py:358 openai, :405 anthropic). All 4 present -> construct transient BackendConfig. Missing any -> CliError. **Mutual exclusion with D-02:** --backend NAME and inline flags (--backend-url/format/key-env/model) are mutually exclusive. Providing both -> CliError. --backend selects from gate.yaml; inline flags bypass gate.yaml entirely.

### gate.yaml Schema and Parser Update
- **D-11:** backends block uses dict-based schema (backend name as YAML key):
  ```yaml
  backends:
    mimo:
      type: api
      format: anthropic
      model: MiMo-V2.5-Pro
      base_url: https://api.mimo.ai
      api_key_env: MIMO_API_KEY
      max_tokens: 16384
      default: true
  ```
  **Required code change:** load_backend_configs (backend.py:163-175) currently iterates backends as a list. Must be updated to accept a dict: iterate .items(), inject key as "name" into each entry dict before passing to _parse_backend_entry. This is a backward-incompatible parser change (list format no longer supported -- no existing consumers use list format since configs=[] is hardcoded everywhere).

### Gate.yaml Loading for Backend Resolution
- **D-16:** cli.py _run() loads gate.yaml for backends using a lightweight yaml.safe_load (same approach as load_outlet_from_gate in outlet_resolver.py:65-103), NOT via gate_check.py's load_gate_config (which requires a "test:" section). The loaded dict is passed to both load_backend_configs and load_outlet_from_gate. If gate.yaml does not exist, configs=[] (no backends).

### Test Strategy
- **D-12:** Unit tests with mock for resolve_backend, load_backend_configs, inline flag parsing. Plus 1 real API smoke test (@pytest.mark.integration, skip-if-no-key) against mimo confirming valid JSON response.

### Cost Output
- **D-13:** After each llm_invoke returns (inside l1_provider per-pass execution), print one-liner to stderr: [backend_name] N in / M out tokens. This is IN ADDITION TO the existing post-verdict cost summary at cli.py:758-778.

### Error Messages
- **D-14:** API HTTP errors (401/429/500): llm_invoke.py raises LLMInvokeError internally (preserving module boundary). cli.py catches LLMInvokeError and re-wraps as CliError with backend name + HTTP status + hint. Example: CliError('backend mimo: 401 Unauthorized (check MIMO_API_KEY env var)'). Same wrapping pattern as D-04.

### Backward Compatibility
- **D-15:** Zero behavior change when NO backend configuration is present: no gate.yaml + no FORGE_BACKEND + no --backend flags -> DEFAULT_BACKEND (cli type, session model). New functionality activates only on explicit configuration. **Note:** users who currently have FORGE_BACKEND set without a gate.yaml will see a behavior change (CliError instead of warn+fallback) -- this is intentional per D-01 FAIL CLOSED, and documented as a known migration.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Backend Infrastructure
- `src/code_forge/backend.py` -- BackendConfig (:59-73), load_backend_configs (:163, currently list-based, D-11 changes to dict), resolve_backend (:225, no-default returns configs[0] at :266)
- `src/code_forge/llm_invoke.py` -- _invoke_api (:291), _invoke_anthropic (max_tokens hardcoded 4096 at :406), _invoke_openai (:345, no max_tokens set)
- `src/code_forge/outlet_resolver.py` -- load_outlet_from_gate (:65-103, lightweight yaml.safe_load pattern for D-16)

### CLI Wiring Point
- `src/code_forge/cli.py` -- _run backend resolution (:666-678, configs=[] hardcoded), _build_baseline_specs (:1013, whole_file at :1020), _paths (:1099, whole_file at :1101), post-verdict cost summary (:758-778)

### Gate Config
- `src/code_forge/gate_check.py` -- load_gate_config (:38-41, requires test: section -- NOT used for backends per D-16)

### Research
- `/tmp/draft_20260604_forge_v23_research.txt` -- Part A sections A1-A6

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- load_backend_configs(data): parses backends -> list of BackendConfig (needs dict update per D-11)
- resolve_backend(env, configs, cli_value): handles FORGE_BACKEND + cli_value precedence
- _invoke_openai / _invoke_anthropic: fully implemented api dispatch
- load_outlet_from_gate: lightweight yaml.safe_load pattern (reuse for D-16)

### Established Patterns
- CliError at CLI boundary, LLMInvokeError at module boundary (D-04/D-14 wrapping pattern)
- Outlet precedence: env > gate.yaml > default (D-02/D-10 follow same layering)

### Integration Points
- cli.py:666-678 single wiring point: load gate.yaml via D-16 pattern, pass to load_backend_configs
- cli.py arg parser: add --backend, --backend-url, --backend-format, --backend-key-env, --backend-model (mutually exclusive groups: --backend vs inline 4-flag set)
- BackendConfig: add max_tokens field
- load_backend_configs: change from list to dict iteration

</code_context>

<specifics>
## Specific Ideas

- F1 pure refactor: flatten loop at :1022-1036, no behavior change
- F2 order: D-09 nargs expansion FIRST, then D-08 merge
- --whole-file nargs='+' multi-file, paths must resolve under cwd
- Token cost on stderr per-pass (supplements, does not replace post-verdict summary)
- gate.yaml schema is dict-based (D-11), parser update required
- Inline flags: 4 required (url + format + key-env + model); model needed for api calls

</specifics>

<deferred>
## Deferred Ideas

- **Cross-repo joint scanning** (MULTI-01): --whole-file across sibling repos. Requires multi-repo diff model. Deferred to v2.4+ as new capability.

</deferred>

---

*Phase: 12-Backend API Wiring*
*Context gathered: 2026-06-04 (rev 3)*
