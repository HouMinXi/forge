---
phase: 12-backend-api-wiring
verified: 2026-06-04T16:30:00Z
status: passed
score: 13/15 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run code-forge review --backend mimo on a real diff with MIMO_API_KEY set"
    expected: "Review completes using mimo API; no claude CLI calls; backend name appears in per-pass stderr token output as [mimo] N in / M out tokens"
    why_human: "Requires real MIMO_API_KEY env var; behavioral end-to-end through real HTTP. Cannot verify programmatically without live key."
  - test: "Set FORGE_BACKEND=deepseek in env (no --backend flag) and run code-forge review on a diff"
    expected: "Review routes to deepseek; zero claude tokens consumed; FORGE_BACKEND env var respected through resolve_backend"
    why_human: "SC2 from ROADMAP requires observing zero claude tokens in actual execution with a real deepseek backend. Env var routing is architecturally wired (verified) but behavioral proof needs real execution."
---

# Phase 12: Backend API Wiring Verification Report

**Phase Goal**: Wire custom backends (mimo/deepseek/kimi) to cli.py so forge review runs on cheap models; expose max_tokens configuration; clean up F1/F2/F3 cli.py tech debt.
**Verified**: 2026-06-04T16:30:00Z
**Status**: passed (SC1/SC2 human-confirmed 2026-06-09 via 12-HUMAN-UAT.md)
**Re-verification**: No -- initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria + PLAN must_haves)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | BackendConfig.max_tokens field with default 16384 | VERIFIED | backend.py:74 `max_tokens: int = 16384`; DEFAULT_BACKEND line 87 includes max_tokens=16384 |
| 2 | load_backend_configs accepts dict-based backends schema | VERIFIED | backend.py:192 `for name, entry in backends.items()` -- dict iteration with name injection |
| 3 | D-03: multiple default=True backends raises CliError | VERIFIED | backend.py:200-204 count defaults; `raise CliError("multiple default backends: %s")` when >1 |
| 4 | CLI flags --backend, --backend-url, --backend-format, --backend-key-env, --backend-model defined | VERIFIED | cli.py:214-238 all 5 flags with correct metavars and default=None |
| 5 | gate.yaml backends block loaded via yaml.safe_load in _run() | VERIFIED | cli.py:698,737-750 yaml imported; FileNotFoundError->None; YAMLError->CliError |
| 6 | resolve_backend called with cli_value=args.backend | VERIFIED | cli.py:753-757 `resolve_backend(env, configs=configs, cli_value=getattr(args,'backend',None))` |
| 7 | D-01 FAIL CLOSED: fallback warn block removed | VERIFIED | No hardcoded configs=[] fallback; no warn-then-DEFAULT_BACKEND; CliError propagates |
| 8 | Inline flags 4-way validation + transient BackendConfig | VERIFIED | cli.py:706-735 mutual exclusion CliError; all-4-required CliError; BackendConfig(name="inline") |
| 9 | backend.max_tokens used in _invoke_anthropic | VERIFIED | llm_invoke.py:407 `"max_tokens": backend.max_tokens`; hardcoded 4096 absent |
| 10 | backend.max_tokens used in _invoke_openai | VERIFIED | llm_invoke.py:361 `"max_tokens": backend.max_tokens` added to OpenAI body dict |
| 11 | LLMInvokeError caught at CLI boundary re-raised as CliError | VERIFIED | cli.py:704 import; cli.py:807-811 `except LLMInvokeError as exc: raise CliError("backend %s: %s")` |
| 12 | Per-pass token cost emitted to stderr in factories.py | VERIFIED | factories.py:248-259 `sys.stderr.write("[%s] %d in / %d out tokens\n")` after llm_invoke |
| 13 | _resolve_whole_file_specs exists and called from both callers (F2) | VERIFIED | cli.py:1100 function def; called at line 1151 (_build_baseline_specs) and 1213 (_paths) |
| 14 | --whole-file with nargs='+' (F3) | VERIFIED | cli.py:242 `"--whole-file", nargs="+"` confirmed by file read |
| 15 | F1: 4 independent checks replace for-loop | VERIFIED | cli.py:1115-1124 four independent if-checks; no `for flag in` loop (grep count=0) |
| SC1 | code-forge review --backend mimo executes on mimo API | UNCERTAIN | Wiring verified; real HTTP execution requires MIMO_API_KEY |
| SC2 | FORGE_BACKEND=deepseek routes all review calls without --backend flag | UNCERTAIN | backend.py:272 env.get("FORGE_BACKEND") present; behavioral proof requires real key |

**Score**: 13/15 programmatically verified; 2 SC items require human execution with real API keys

### Deferred Items

BACK-04 (dogfood verify with zero claude tokens) is assigned to Phase 13 per REQUIREMENTS.md traceability. Behavioral execution proof for SC1/SC2 is the Phase 13 goal.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/backend.py` | BackendConfig.max_tokens + dict schema + D-03 | VERIFIED | Lines 74, 192, 200-204 |
| `src/code_forge/cli.py` | Backend flags + gate.yaml loader + inline validation + LLMInvokeError + F1/F2/F3 | VERIFIED | Lines 214-238, 697-759, 807-811, 1100-1213 |
| `src/code_forge/llm_invoke.py` | backend.max_tokens in both API paths | VERIFIED | Lines 361, 407; 4096 absent |
| `src/code_forge/factories.py` | Per-pass token cost to stderr | VERIFIED | Lines 248-259 |
| `tests/test_backend.py` | Dict schema tests + D-03 validation tests | VERIFIED | 8 target tests pass |
| `tests/test_cli_integration.py` | Inline flag tests + error wrapping + real API smoke | VERIFIED | 7 unit pass; 1 real API skips without key |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| cli.py _run() | backend.py load_backend_configs | yaml.safe_load(gate.yaml) -> load_backend_configs(gate_data) | WIRED | cli.py:748 |
| cli.py _run() | backend.py resolve_backend | resolve_backend(env, configs=configs, cli_value=getattr(args,'backend',None)) | WIRED | cli.py:753-757 |
| llm_invoke._invoke_anthropic | BackendConfig.max_tokens | body["max_tokens"] = backend.max_tokens | WIRED | llm_invoke.py:407 |
| llm_invoke._invoke_openai | BackendConfig.max_tokens | body["max_tokens"] = backend.max_tokens | WIRED | llm_invoke.py:361 |
| cli.py | llm_invoke.LLMInvokeError | except LLMInvokeError -> raise CliError(...) | WIRED | cli.py:704,807-811 |
| factories.py build_l1_provider | stderr token output | sys.stderr.write("[%s] %d in / %d out tokens") | WIRED | factories.py:248-259 |
| cli.py | _resolve_whole_file_specs | called from _build_baseline_specs and _paths | WIRED | cli.py:1151, 1213 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| _invoke_anthropic | max_tokens | backend.max_tokens (BackendConfig field) | Yes -- from config not hardcoded | FLOWING |
| _invoke_openai | max_tokens | backend.max_tokens (BackendConfig field) | Yes -- field added to body dict | FLOWING |
| cli.py _run() | backend | load_backend_configs(gate_data) -> resolve_backend() | Yes -- reads gate.yaml | FLOWING |
| factories.py | token counts | result.usage.input_tokens/output_tokens from llm_invoke() | Yes -- from API response | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Dict schema tests | pytest test_backend.py -k "dict_schema or multiple_defaults or ..." | 8 passed | PASS |
| Inline flags tests | pytest test_cli_integration.py -k "inline_flags or llm_invoke_error or ..." | 7 passed | PASS |
| Real API smoke | pytest TestRealMimoApiSmoke | 1 skipped (MIMO_API_KEY not set) | SKIP |
| Parser tests | pytest tests/test_cli_parser.py | 35 passed | PASS |
| Full suite | pytest tests/ | 1024 passed, 1 skipped | PASS |

### Probe Execution

SKIPPED -- no probe scripts found under scripts/*/tests/probe-*.sh.

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| BACK-01 | 12-01, 12-02, 12-04 | gate.yaml backends wired to cli.py with --backend flag and FORGE_BACKEND env | VERIFIED (wiring) / UNCERTAIN (behavioral) | cli.py wiring complete; end-to-end with real API deferred to Phase 13 |
| BACK-02 | 12-01, 12-02, 12-04 | max_tokens fix: replace 4096 in _invoke_anthropic; add to _invoke_openai | VERIFIED | llm_invoke.py:361,407 use backend.max_tokens; 4096 absent |
| BACK-03 | 12-03, 12-04 | F1/F2/F3 cli.py cleanup | VERIFIED | F1: 4 checks at 1115-1124; F2: _resolve_whole_file_specs at 1100; F3: nargs='+' at 242 |
| BACK-04 | not in Phase 12 | Dogfood verify -- zero claude tokens | DEFERRED to Phase 13 | REQUIREMENTS.md traceability: BACK-04 -> Phase 13 |

### Anti-Patterns Found

Scanned: backend.py, cli.py, llm_invoke.py, factories.py, test_backend.py, test_cli_integration.py.

No TBD/FIXME/XXX/TODO/PLACEHOLDER debt markers found in any phase-modified file.

Pre-existing: tests/test_lock_signals.py uses @pytest.mark.integration which is unregistered in pyproject.toml. Predates Phase 12. Not a phase gap.

### Human Verification Required

#### 1. Real mimo Backend End-to-End Execution (SC1)

**Test**: With MIMO_API_KEY set, configure gate.yaml with mimo backend (type: api, format: anthropic, model: MiMo-V2.5-Pro), run `code-forge review --backend mimo` on a real diff.
**Expected**: Review completes via mimo API; per-pass stderr shows `[mimo] N in / M out tokens`; no claude CLI subprocess; findings produced.
**Why human**: Requires live MIMO_API_KEY. HTTP call path is wired and unit-tested with mocks; actual network round-trip and token routing confirmed only with a real key.

#### 2. FORGE_BACKEND=deepseek Env-Variable Routing (SC2)

**Test**: Set `FORGE_BACKEND=deepseek` in env (no --backend flag). Configure deepseek backend in gate.yaml. Run `code-forge review` on a diff.
**Expected**: Routes to deepseek; zero claude CLI calls; resolve_backend reads FORGE_BACKEND from env; per-pass stderr shows `[deepseek]` prefix.
**Why human**: Behavioral proof of env-variable routing. Architecture verified (backend.py:272 reads env.get("FORGE_BACKEND")); zero-claude-tokens confirmation observable only in real execution.

### Gaps Summary

No programmatic gaps. All 15 must-have truths from PLAN frontmatter VERIFIED in codebase. The 2 UNCERTAIN items (SC1, SC2) are behavioral execution proofs requiring real API keys -- code wiring is fully in place and unit-tested with mocks. Status is human_needed because ROADMAP success criteria include observable behavioral outcomes (routing to third-party APIs, zero claude tokens consumed) that cannot be verified without live credentials.

---

_Verified: 2026-06-04T16:30:00Z_
_Verifier: Claude (gsd-verifier)_
