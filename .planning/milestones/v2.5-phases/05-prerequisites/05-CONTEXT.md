# Phase 5: Prerequisites - Context

**Gathered:** 2026-05-30
**Status:** Ready for planning
**Cross-AI Review:** 2026-05-30 (DeepSeek + Mimo + Kimi, 3-model consensus)

<domain>
## Phase Boundary

Phase 5 delivers the foundational infrastructure for both outlets: toolchain auto-detection with zero-config first-run, backend reachability verification (backend-agnostic per BACKEND-01), and outlet selection logic. No review execution happens in this phase -- that is Phase 6 (Outlet B) and Phase 7 (Outlet A).

**Requirements in scope:** CLI-03, CLI-04, CLI-05, BOTH-04

</domain>

<decisions>
## Implementation Decisions

### Auto-detect strategy (CLI-03/04)

- **D-01:** Detection uses configuration-file-aware probing: read `pyproject.toml` to infer Python and declared tools (e.g. `[tool.ruff]`), then verify tool availability via `shutil.which()`. Prioritize project-level config over blind PATH scanning. **Fallback for projects without pyproject.toml:** scan for `*.py` files (or `setup.py` / `requirements.txt`) to infer Python, then check PATH for known Python tools (ruff, pylint, flake8, pytest). Phase 5 implements Python detection only. The detection framework accepts language plugins (Cargo.toml, package.json) but Rust/JS/Go heuristics are deferred to v2.3+.
- **D-02:** Empty detection = error stop, not empty template. `load_registry()` returns `{}` for empty `tools.yaml` without error (registry.py:64-69), which would create a silent-PASS where L0 runs zero tools but shows green. Detection finding no tools MUST report error: "No toolchain detected. L0 has no static analysis tools. Install tools or manually configure `.code-forge/tools.yaml`." Detection error handling is universal (not outlet-specific) -- detect is shared infrastructure consumed by both outlets.
- **D-03:** Detection is idempotent. If `.code-forge/tools.yaml` already exists **and contains a non-empty `tools` key**, skip generation. `--force` flag to overwrite. Empty or zero-byte files are treated as missing (prevents silent-PASS bypass via `touch .code-forge/tools.yaml`). Same logic for `gate.yaml`. Per-file independence: generate whichever file is missing without requiring both. **Emptiness for idempotence is defined by `load_registry()` returning `{}`**: covers zero-byte files, a missing `tools:` key, AND a present-but-empty value (`tools: []`, `tools:` null, `tools: {}`). All count as missing -> detect regenerates; if regeneration still finds nothing, D-02 fail-loud applies via the D-20 review path. This closes the `tools: []` bypass where the key is present but the value is empty (registry.py:64-69 returns `{}`). [Added post-review: Kimi R2 BLOCKER B1]
- **D-04:** Detection outputs a human-readable report: "Detected: ruff, pytest / Missing: mypy, pylint" (ROADMAP Phase 5 SC#2: "user sees what was detected and what was missing"). Semantics: "Detected" = declared in project config AND available on PATH; "Missing" = known tool for this language but not found on PATH. Only Detected tools are written to tools.yaml. Report goes to stdout, generated YAML goes to `.code-forge/`.
- **D-05:** `tomllib` (stdlib 3.11+) for TOML parsing. `requires-python = ">=3.12"` confirmed in pyproject.toml -- no `tomli` fallback needed.

### Auto-init integration (CLI-03/04 + SC#1)

- **D-20:** `code-forge review` calls `detect_and_init()` internally when `.code-forge/tools.yaml` does not exist (or is empty/zero-byte per D-03). This satisfies SC#1 ("User runs `code-forge review` ... and gets L0 linting without manual config"). The standalone `code-forge detect` subcommand is for pre-flight inspection or re-detection (`--force`) without running review. [Added post-review: 3-model consensus finding C1]

### Auth probe design (CLI-05)

- **D-06:** Model-agnostic probe. Call `claude -p "ack"` (minimal prompt, neutral response; claude CLI has no --max-tokens flag). Do NOT hardcode a specific model -- uses the user's default model. [Probe prompt refined post-review: avoid guardrail-triggering all-caps strings, minimize cost] **[SUPERSEDED by D-28 (2026-05-31 backend re-discuss): the reachability probe is `claude auth status --json` for the default claude backend -- NOT an inference call -- and is backend-agnostic per BACKEND-01.]**
- **D-07:** Timeout configurable via `FORGE_AUTH_TIMEOUT` env var, default 20 seconds. Empirical: cold start measured at ~12s on this machine; 15s was too tight (3s margin). 20s default gives reasonable buffer for slower connections. [Default raised from 15 to 20 post-review] **[AMENDED by D-28 (2026-05-31 backend re-discuss): the cold-start ~12s rationale was tuned for the OLD inference probe (`claude -p "ack"`). The backend-agnostic probe (`claude auth status` for cli/claude, `api_key_env` presence for api) is near-instant, so 20s is now a generous upper bound rather than a cold-start-fitted threshold. The FORGE_AUTH_TIMEOUT env var + 20s default still stand.]**
- **D-08:** Auth probe caching: **file-based with short TTL (5 minutes), stored in user-level directory** (`~/.cache/code-forge/auth_cache.json`), not project directory (auth is per-user, not per-project; project-dir risks git commits + redundant cold starts across repos). Success cached with 5-min TTL -- short because cached "auth OK" can go stale (token expires) and staleness only surfaces when subprocess fails. **Failure is NOT cached** -- failed auth should retry immediately on next review. **Write-through invalidation:** if a `claude -p` subprocess fails with auth error during review, immediately invalidate the cache regardless of TTL. Session-level (in-memory) caching is explicitly rejected: CLI-06 (Phase 7) runs each pass as a fresh subprocess with no shared memory.
- **D-09:** Outlet B (inline) path NEVER runs the auth probe. Explicit `FORGE_OUTLET=inline` or `gate.yaml outlet: inline` short-circuits before probe. Auth probe only runs when outlet resolves to `cli` (Outlet A needs `claude -p`).
- **D-10:** No auth + no explicit Outlet B = error stop. Per CLI-02 FAIL CLOSED spirit: "Configure `claude -p` auth or set `FORGE_OUTLET=inline`." Never silently degrade to inline self-drive. This error is raised by the CALLER of outlet_resolver (SKILL.md or CLI dispatcher), not by outlet_resolver itself -- the resolver is a pure function that returns the resolved outlet or raises on invalid input.
- **D-11:** Add opt-in real `claude -p` integration test (pytest mark: `@pytest.mark.real_api`, skipped by default). Mock tests cover 3 paths (success/timeout/failure) but real-API smoke catches mock blind spots (memory: feedback_real_api_smoke_catches_mock_blindspot.md).
- **D-21:** Auth probe error discrimination (required by SC#3: "clear error naming the missing piece"). The probe MUST distinguish and report: (a) `claude` binary not found in PATH, (b) binary found but auth not configured (no API key / no OAuth), (c) auth configured but expired/invalid (401-class), (d) network timeout (no response within FORGE_AUTH_TIMEOUT). Each case produces a distinct error message naming the missing piece and suggesting a fix action.

### Outlet selection logic (BOTH-04)

- **D-12:** Override form: `FORGE_OUTLET` env var + `gate.yaml` `outlet:` field. Values: `cli` / `inline`. Invalid values raise `ValueError` with source attribution (same pattern as `mode_resolver.py`). **CLI flag:** BOTH-04 text mentions "env var / flag" but Phase 5 implements env var + gate.yaml only. The `--outlet` CLI flag is deferred to Phase 7 when `code-forge review` subcommand is wired as the Outlet A entry point. [Reconciled with BOTH-04 post-review: flag acknowledged, implementation phased]
- **D-13:** Precedence chain: `FORGE_OUTLET` env var > `gate.yaml outlet:` > auth probe. When explicit override exists, return that value directly. When no override: run auth probe -- if auth succeeds, return `cli` (fail-safe Outlet A); if auth fails, **raise error** (not return `inline`). Auth probe failure is NOT a routable outlet value -- it is an error condition. [Clarified post-review: prevents misrouting to Outlet B on auth failure]
- **D-14:** Edge case handling: env var empty string falls through to gate.yaml; whitespace-only raises `ValueError`; case-insensitive matching applies to both env var AND gate.yaml values. gate.yaml with no `outlet:` key = falls through to auth probe (normal case for existing projects).
- **D-15:** Result exposed via Python subcommand: `code-forge resolve-outlet` outputs `cli` or `inline` to stdout and exits 0. **Error contract:** if auth is required but unavailable (no override + auth probe failure), prints diagnostic to stderr and exits 1. SKILL.md calls this subcommand and reads stdout for the outlet value; non-zero exit = report error and STOP. [Error contract added post-review]
- **D-16:** NEVER auto-detect "model capability." A model cannot reliably self-assess trustworthiness -- that unreliability is the reason Reviewer Canary exists. Outlet selection uses ONLY objective signals (explicit override, auth availability). This is a LOCKED design constraint from BOTH-04 correction.

### Integration architecture

- **D-17:** Three new modules: `src/code_forge/detect.py`, `src/code_forge/auth.py`, `src/code_forge/outlet_resolver.py`. Each follows existing patterns: pure functions, injected dependencies, no global state. [Module naming: planner may refine to `toolchain_detect.py` / `auth_probe.py` for consistency with project naming density]
- **D-18:** New CLI subcommands in `cli.py`: `code-forge detect` (CLI-03/04), `code-forge resolve-outlet` (BOTH-04). Auth probe is internal (called by outlet resolver and by the review entry point), not a standalone subcommand. `code-forge review` integrates detection via D-20.
- **D-19:** Test files: `tests/test_detect.py`, `tests/test_auth.py`, `tests/test_outlet_resolver.py`, plus additions to `tests/test_cli.py` for new subcommands. Target density: 10-15 tests per new module, mirroring existing patterns.
- **D-22:** `gate.yaml` schema extension: add optional `outlet:` field at top level (backwards-compatible). `load_gate_config()` currently requires a `test:` section -- if outlet-only configs without `test:` are needed, either (a) relax the validation, or (b) read `outlet` via a separate lightweight YAML read. Planner decides which approach during implementation. [Promoted from Claude's Discretion post-review: this is a hard integration requirement, not optional]
- **D-23:** Auto-init'd `gate.yaml` content (when detect generates it per D-03 same-logic) MUST round-trip through `load_gate_config()`. Minimal valid template includes a `test:` section with a runnable default (e.g. the detected test runner, such as `pytest`). If D-22 resolution relaxes the `test:`-required validation for outlet-only configs, the template may omit `test:`; otherwise it MUST contain a valid `test:` block or first-run `load_gate_config()` raises. Exact default command + thresholds = planner discretion. Open scope question for planner: whether Phase 5 auto-generates `gate.yaml` at all, or only `tools.yaml` (gate.yaml deferred to Phase 6/7 when the test gate + outlet field are consumed). [Added post-review: Kimi R2 BLOCKER B2 + DeepSeek concurrence]
- **D-24:** Existing-`tools.yaml` idempotency distinguishes EMPTY from MALFORMED (resolves the deferred Kimi-HIGH on retry-path ValueError propagation). Grounded in a ground-truth smoke test of the real `load_registry()` (registry.py:58-102): the five empty forms (empty file, no `tools` key, `tools:` null / `[]` / `{}`) all RETURN `{}`, while present-but-malformed forms (non-empty non-dict `tools`, entry missing a required field, entry not a mapping, invalid YAML) all RAISE `ValueError` with an actionable message. Therefore `detect_and_init` MUST branch: `load_registry() == {}` -> regenerate (D-03 helpful path); `load_registry()` RAISES `ValueError` -> FAIL LOUD via `CliError` surfacing the underlying message plus remediation (fix it, delete it to regenerate, or rerun with force=True) -- do NOT silently overwrite a hand-edited file (that swallows the diagnostic, violating the project fail-loud rule). `force=True` is the escape hatch that regenerates over a malformed file. Converts the sub-session deferred executor-will-broaden-the-except into a decided, test-pinned behavior. [Added post-review: main-session gatekeeping R4 + ground-truth smoke test]

### Backend abstraction (BACKEND-01) [added 2026-05-31 Phase 5 backend re-discuss]

These decisions SUPERSEDE the `claude -p`-bound framing in D-06, D-09, D-10, D-13, D-21: the review backend is now pluggable, and the claude path is one (default) backend. v2.2 requirements CLI-05/06/07/08 + BOTH-04 were amended backend-agnostic; BACKEND-01 was added.

- **D-25:** Pluggable backend, three types. (a) `api` + `format: openai` -> `POST {base_url}/v1/chat/completions`, `Authorization: Bearer`; (b) `api` + `format: anthropic` -> `POST {base_url}/v1/messages`, `x-api-key`; (c) `cli` -> wrap an existing review CLI (`claude -p` for a Claude subscription, `aicc --model X` to reuse a multi-provider setup). OpenAI-format + Anthropic-format covers nearly the whole provider landscape (OpenAI/DeepSeek/Kimi/GLM/Mimo/Gemini-compat/Groq/local -> openai; Claude/Bedrock/Vertex -> anthropic). The anti-fake property (Python owns each pass + counts cycles + canary) holds for both `api` (HTTP) and `cli` (subprocess) -- it never depended on claude.

- **D-26:** DEFAULT backend = the main session model, single model, NO splitting. Concretely a plain `claude -p` invocation with NO `--model` pin, which uses the user's default Claude model (consistent with D-06's original "uses user's default model"). forge does NOT analyze the diff (code complexity, change size, or any added dimension) to auto-select a model -- the USER configures the model; forge follows the session model by default. This is a hard NON-GOAL, not a deferred feature. **Trust vs depth (resolves the tension with Outlet A, which exists for untrusted/cheap editor models):** the default-backend-follows-session-model choice governs review DEPTH (which model answers), never TRUST. Forge's integrity guarantee comes from Python owning every pass + the cycle counter + the canary (D-25 anti-fake), which holds for ANY backend model -- a weak default backend reviews less deeply but CANNOT fake a CLEAN verdict, and Outlet A still routes through a real fresh backend invocation, never the editor's in-context model self-certifying. Model strength and orchestration trust are orthogonal. [User decision 2026-05-31: no diff-driven model routing -- forge must not split model choice by complexity or change size]

- **D-27:** Backend config is a list of entries `{name, type, format?, base_url?, api_key_env?, model}`. API keys are referenced by env-var NAME (`api_key_env`), NEVER stored inline (secret hygiene). Active/default backend resolves: `FORGE_BACKEND` env override > config default > the D-26 session-model default. Config file location (new `.code-forge/backends.yaml` vs a `backends:` block in an existing config) = planner discretion in Phase 7; Phase 5 locks only the schema shape + resolution order.

- **D-28:** Backend-agnostic reachability probe (SUPERSEDES D-06's `claude -p "ack"` probe; generalizes D-21). Per backend type: `api` -> check the configured `api_key_env` is present (cheap, no network; optional liveness call deferred/off by default to stay bounded); `cli` -> run that CLI's own auth check -- for the claude default that is `claude auth status --json` (verified this session: `-p` is real, `--max-tokens` is NOT a claude flag, and `auth status --json` returns `loggedIn`/`authMethod`/`subscriptionType` with zero inference cost). The probe NEVER does an inference call merely to check auth. D-07's 20s timeout becomes a generous cap (these checks are near-instant).

- **D-29:** Outlet resolution generalized (SUPERSEDES the `claude -p` references in D-09/D-10/D-13). Objective signal = "the configured default backend is reachable" (was "claude -p auth ok"). Precedence unchanged: `FORGE_OUTLET` > `gate.yaml outlet:` > backend-reachability signal. Outlet B (inline) still NEVER probes. Reachability failure with no explicit Outlet B = error stop ("Configure a review backend or set FORGE_OUTLET=inline"), never a silent inline degrade. D-16 (no model-capability auto-detect) still holds.

- **D-30:** Phase 5 scope for BACKEND-01 = lock the abstraction + config schema + resolution order + backend-agnostic probe ONLY. The actual adapter implementations (HTTP clients for the two API formats, the cli wrappers) land in Phase 7 (Outlet A dispatcher) per ROADMAP. detect.py (CLI-03/04) + D-24 are UNAFFECTED by this re-discuss.

### Claude's Discretion

- Exact detection heuristics for languages beyond Python (Rust, JS, Go -- framework extensibility only in Phase 5)
- Mixed-language repo priority (v2.2: Python wins; planner may add a simple heuristic)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing patterns (MUST replicate)
- `src/code_forge/mode_resolver.py` -- Precedence pattern (flag > env > default), edge case handling (empty string, whitespace), ValueError with source attribution. Outlet resolver MUST replicate this structure.
- `src/code_forge/env_resolver.py` -- FORGE_* env var resolution pattern (cli > env > default with validation). Auth timeout resolver follows this.
- `src/code_forge/registry.py` -- ToolConfig dataclass, tools.yaml schema, load_registry(). Auto-detect output MUST produce YAML loadable by this module. Note: returns {} for empty tools (line 64-69) -- detect must prevent this.
- `src/code_forge/gate_check.py` -- gate.yaml schema, load_gate_config(). Currently requires `test:` section (raises ValueError without it). Outlet field extension needs to handle this constraint.

### Test patterns
- `tests/test_mode_resolver.py` -- 13 tests covering precedence, edge cases, error paths. Outlet resolver tests should mirror this structure.
- `tests/test_registry.py` -- 15 tests for tools.yaml loading. Detect tests must verify generated YAML round-trips through load_registry.
- `tests/test_gate_check.py` -- 51 tests including missing/invalid config paths.

### Project constraints
- `CLAUDE.md` -- Three-cycle review required before commit; worktree required
- `.planning/REQUIREMENTS.md` -- CLI-03, CLI-04, CLI-05, BOTH-04 full text
- `.planning/ROADMAP.md` -- Phase 5 success criteria SC#1-4

### Memory-captured lessons
- `feedback_real_api_smoke_catches_mock_blindspot.md` -- Why D-11 (real API test) exists

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mode_resolver.py`: 60 lines, complete precedence resolver with edge cases. Outlet resolver is structurally identical -- copy the pattern, change the values.
- `env_resolver.py`: FORGE_* env var parsing with sanity caps and error messages. Auth timeout resolution follows this exactly.
- `registry.py:load_registry()`: Already validates tools.yaml schema. Auto-detect generates YAML; this function validates it. No need to reinvent validation.
- `gate_check.py:load_gate_config()`: Already validates gate.yaml schema. Extension for `outlet:` field is additive but `test:` section is currently required (see D-22).

### Established Patterns
- Dependency injection: all modules accept `env: Mapping`, `fs_open`, `run_cmd` for testability (no mocking of globals)
- Error types: `CliError` for user-facing errors, `ValueError` for config errors
- Exit codes: `exit_codes.py` defines `EXIT_PASS=0`, `EXIT_FAIL=1`, `EXIT_CLI_ERROR=2`, `EXIT_BUSY=3`, `EXIT_ESCALATED=4` (verified 2026-05-31). `CliError` maps to `EXIT_CLI_ERROR=2` (established cli.py pattern, used by 05-01 detect). [Corrected 2026-05-31: an earlier note here wrongly claimed only 0/1 exist and 'never exit 2'; that contradicted exit_codes.py and every Phase 5 plan.]

### Integration Points
- `cli.py`: New subcommands `detect` and `resolve-outlet` register via argparse subparsers (existing pattern at line ~170)
- `machine.py:266`: Already reads `.code-forge/gate.yaml` -- outlet field will be read alongside existing config
- `skills/code-forge/SKILL.md:319/330/340`: The Invoke calls that Outlet B (Phase 6) replaces and Outlet A (Phase 7) dispatches around
- `code-forge review` entry point: needs D-20 detect integration (call detect_and_init when tools.yaml missing)

</code_context>

<specifics>
## Specific Ideas

- Auth probe empirical data: `claude -p` cold start ~12s, warm ~3s. Timeout default 20s (D-07).
- `claude -p` with invalid model returns exit 1 with clear error message -- usable for error discrimination (D-21).
- `claude -p` without `--model` uses user's configured default -- exactly the model-agnostic behavior CLI-05 requires.
- Auth cache staleness: cached "success" is more dangerous than cached "failure" because staleness only surfaces at subprocess execution time. Short TTL + write-through invalidation addresses this (D-08).

</specifics>

<deferred>
## Deferred Ideas

- Reviewer Canary implementation (v2.3+, Phase 9 is spec-only)
- Outlet enforcement (Phase 7 CLI-02 FAIL CLOSED responsibility -- outlet_resolver gives routing result, not execution guarantee)
- `--outlet` CLI flag (Phase 7 when `code-forge review` subcommand is wired)
- Multi-language detect beyond Python (Rust/JS/Go detection heuristics -- Phase 5 implements the framework, Python is primary)
- `FORGE_LLM_MODEL` documentation (Phase 8 BOTH-02)
- Outlet B L0 behavior (Phase 6 scope -- whether inline path runs L0 tools or relies solely on LLM passes)

</deferred>

---

*Phase: 05-prerequisites*
*Context gathered: 2026-05-30*
*Cross-AI reviewed: 2026-05-30 (DeepSeek, Mimo, Kimi -- 3-model consensus)*
