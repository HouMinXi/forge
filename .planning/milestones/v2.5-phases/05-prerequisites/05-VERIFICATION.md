---
phase: 05-prerequisites
verified: 2026-06-01T23:45:00Z
status: passed
score: 13/13 must-haves verified
overrides_applied: 0
---

# Phase 5: Prerequisites Verification Report

**Phase Goal:** The foundational infrastructure exists for both outlets -- the configured review backend is verified reachable (backend-agnostic per BACKEND-01), projects auto-detect their toolchain, and the outlet selection logic is designed
**Verified:** 2026-06-01T23:45:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | User runs `code-forge review` in a Python project with no tools.yaml and gets L0 linting from auto-detected ruff/pylint/flake8 | VERIFIED | cli.py:523-528 calls `detect_and_init(cwd, quiet=True)` on FileNotFoundError for default registry; cli.py:535-537 calls it again on empty registry `{}`; detect.py generates tools.yaml; 3-linter round-trip confirmed: ruff(sarif), pylint(pylint_json), flake8(flake8) all resolve through PARSER_DISPATCH without KeyError |
| SC2 | First-run auto-init generates a minimal tools.yaml from detected toolchain, user sees what was detected and what was missing | VERIFIED | `detect_and_init()` prints "Detected: {tools} / Missing: {tools}" to stdout (line 369-373); generates `.code-forge/tools.yaml` via `generate_tools_yaml()`; behavioral spot-check confirmed stdout output contains "Detected:" and "Missing:" |
| SC3 | Reachability probe returns OK within 20s when reachable; unreachable/missing auth returns clear error naming the missing piece | VERIFIED | `probe_backend()` dispatches: cli runs `["claude","auth","status","--json"]` (line 416-419, NOT inference); api checks `api_key_env` presence (line 385). Error discrimination: binary-not-found ("not found in PATH"), not-logged-in ("Run `claude auth login`"), timeout ("timed out after Ns"), api-key-missing ("X not set"). DEFAULT_AUTH_TIMEOUT=20, MAX_REASONABLE_AUTH_TIMEOUT=120. Behavioral spot-check: all 3 error paths produce actionable messages |
| SC4 | Outlet selection: explicit env-var override wins; with no override uses ONLY objective configured-backend-reachability signal; fail-safe default is Outlet A (CLI); does NOT auto-detect model capability | VERIFIED | `resolve_outlet()` precedence: FORGE_OUTLET env > gate.yaml outlet > backend reachability. Explicit inline short-circuits before probe (bomb-probe test confirms). Backend reachable returns "cli" (fail-safe). Backend unreachable raises CliError with "Configure a review backend or set FORGE_OUTLET=inline" (FAIL CLOSED). No model-capability detection code exists in outlet_resolver.py (D-16 LOCKED) |

**Score:** 4/4 roadmap success criteria verified

### Plan-Level Must-Have Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | detect.py exports DetectionResult, detect_toolchain, generate_tools_yaml, detect_and_init | VERIFIED | All 4 symbols confirmed in detect.py; DetectionResult is frozen dataclass (line 28-39) |
| 2 | backend.py exports BackendConfig, ProbeResult, resolve_backend, probe_backend, DEFAULT_BACKEND, load_backend_configs, resolve_auth_timeout, invalidate_probe_cache | VERIFIED | All 8 symbols confirmed in backend.py |
| 3 | outlet_resolver.py exports resolve_outlet, load_outlet_from_gate | VERIFIED | Both symbols confirmed in outlet_resolver.py |
| 4 | parsers/flake8.py and parsers/pylint.py registered in PARSER_DISPATCH | VERIFIED | parsers/__init__.py line 30-31: `"flake8": parse_flake8, "pylint_json": parse_pylint`; both in __all__ |
| 5 | _KNOWN_FORMATS includes flake8 and sarif | VERIFIED | registry.py line 28-38: frozenset includes "flake8" and "sarif"; pylint_json already present |
| 6 | D-26 NON-GOAL: resolve_backend has no diff/complexity/size params | VERIFIED | `inspect.signature(resolve_backend)` returns `(env, configs, cli_value)` -- no diff/complexity/size parameter. Mechanically enforced by test |
| 7 | D-28: probe uses `claude auth status --json`, NOT inference call | VERIFIED | _probe_cli line 416: `run_cmd(["claude", "auth", "status", "--json"], ...)`. No `-p` or inference prompt anywhere in probe path |
| 8 | D-16 LOCKED: no model-capability auto-detection in outlet_resolver | VERIFIED | grep for model-capability/auto-detect patterns in outlet_resolver.py returns only doc comments, not code logic |
| 9 | CLI-04 is PARTIAL (tools.yaml in Phase 5, gate.yaml deferred to Phase 6/7 per D-23) | VERIFIED | detect.py generates tools.yaml only; no gate.yaml generation code exists in detect.py or detect-related cli.py code; REQUIREMENTS.md traceability table confirms: "CLI-04: Phase 5 (tools.yaml), Phase 6/7 (gate.yaml) -- Partial" |

**Score:** 9/9 plan-level truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/detect.py` | Toolchain auto-detection | VERIFIED | 375 lines, exports detect_toolchain, generate_tools_yaml, detect_and_init, DetectionResult. Substantive implementation with pyproject.toml parsing, PATH fallback, flake8 config detection, idempotency |
| `src/code_forge/backend.py` | Pluggable backend abstraction | VERIFIED | 460 lines, exports BackendConfig, ProbeResult, load_backend_configs, resolve_backend, resolve_auth_timeout, probe_backend, invalidate_probe_cache, DEFAULT_BACKEND |
| `src/code_forge/outlet_resolver.py` | Outlet selection logic | VERIFIED | 172 lines, exports resolve_outlet, load_outlet_from_gate. Precedence chain, FAIL CLOSED, inline-never-probes |
| `src/code_forge/parsers/flake8.py` | flake8 text parser | VERIFIED | 75 lines, exports parse_flake8. Compiled regex, empty->[], match->Finding, no-match->[ToolError] |
| `src/code_forge/parsers/pylint.py` | pylint JSON parser | VERIFIED | 109 lines, exports parse_pylint. Single json.loads, _PYLINT_LEVEL_MAP, endLine-null fallback |
| `src/code_forge/parsers/__init__.py` | PARSER_DISPATCH registration | VERIFIED | "flake8" and "pylint_json" added to PARSER_DISPATCH dict; parse_flake8 and parse_pylint in __all__ |
| `src/code_forge/registry.py` | _KNOWN_FORMATS update | VERIFIED | "flake8" and "sarif" added to frozenset; stale "ruff_json" kept with comment |
| `src/code_forge/cli.py` | detect + resolve-outlet subcommands | VERIFIED | detect_parser (line 337), outlet_parser (line 352), known_subcommands includes both (line 389), routing in main() (lines 477-481), _run_detect (line 1081), _run_resolve_outlet (line 1102), _safe_load_registry (line 515), auto-detect in Step 2 (lines 523-537) |
| `tests/test_detect.py` | TDD tests for detection | VERIFIED | 397 lines, 24+ test functions |
| `tests/test_backend.py` | TDD tests for backend | VERIFIED | 657 lines, 37 tests (36 pass + 1 real_api skipped) |
| `tests/test_outlet_resolver.py` | TDD tests for outlet resolver | VERIFIED | 259 lines, 20 tests |
| `tests/test_cli_detect.py` | CLI tests for new subcommands | VERIFIED | 224 lines, 14 tests |
| `tests/test_cli_integration.py` | Review auto-detect integration tests | VERIFIED | 397 lines, includes TestReviewAutoDetect class with 5 tests |
| `tests/test_parsers.py` | Parser tests including flake8+pylint | VERIFIED | 543 lines, includes TestParseFlake8, TestParsePylint, updated TestParserDispatch |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| detect.py | registry.py | `load_registry()` call | WIRED | detect.py line 23: `from code_forge.registry import load_registry`; used in detect_and_init line 344 |
| parsers/__init__.py | parsers/flake8.py | PARSER_DISPATCH["flake8"] | WIRED | __init__.py line 17: `from code_forge.parsers.flake8 import parse_flake8`; line 30: `"flake8": parse_flake8` |
| parsers/__init__.py | parsers/pylint.py | PARSER_DISPATCH["pylint_json"] | WIRED | __init__.py line 18: `from code_forge.parsers.pylint import parse_pylint`; line 31: `"pylint_json": parse_pylint` |
| cli.py | detect.py | detect subcommand + review auto-detect | WIRED | cli.py line 1090: `from .detect import detect_and_init`; line 526-527: lazy import in review pipeline |
| cli.py | outlet_resolver.py | resolve-outlet subcommand | WIRED | cli.py line 1113: `from .outlet_resolver import resolve_outlet` |
| outlet_resolver.py | backend.py | resolve_backend + probe_backend in default reachability_fn | WIRED | outlet_resolver.py lines 27-32: imports resolve_backend, probe_backend, DEFAULT_BACKEND; line 149: calls them in default reachability_fn |

### Data-Flow Trace (Level 4)

Not applicable -- Phase 5 modules are infrastructure (config parsers, resolvers, probes), not rendering components. Data flows were verified via behavioral spot-checks instead.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 148 phase-specific tests pass | `python -m pytest tests/test_detect.py tests/test_backend.py tests/test_outlet_resolver.py tests/test_cli_detect.py tests/test_cli_integration.py tests/test_parsers.py -q` | 148 passed in 0.53s | PASS |
| Full suite (889 tests) passes | `python -m pytest tests/ -q` | 889 passed, 3 warnings in 625.03s | PASS |
| D-26 NON-GOAL: no diff params | `inspect.signature(resolve_backend)` -> `(env, configs, cli_value)` | No diff/complexity/size params | PASS |
| D-26 DEFAULT_BACKEND | `DEFAULT_BACKEND.type == "cli"`, `model == ""` | cli, no model pin | PASS |
| D-28 probe command | `_probe_cli` source contains `["claude","auth","status","--json"]` | Uses auth status, not inference | PASS |
| L0 dispatch-key validity | sarif, pylint_json, flake8 all in PARSER_DISPATCH and _KNOWN_FORMATS | No KeyError at parse time | PASS |
| Inline-never-probes | `resolve_outlet({'FORGE_OUTLET': 'inline'}, reachability_fn=bomb)` | Returns "inline", bomb never called | PASS |
| FAIL CLOSED (D-29) | `resolve_outlet({}, reachability_fn=unreachable)` | Raises CliError with correct message | PASS |
| 3-linter round-trip | detect ruff+pylint+flake8 -> generate tools.yaml -> load_registry -> verify dispatch keys | All 3 linters round-trip successfully | PASS |
| SC#2 detection report | `detect_and_init()` stdout contains "Detected:" and "Missing:" | Output verified via capture | PASS |
| SC#3 error discrimination | probe with missing binary, not-logged-in, api-key-missing | Each produces distinct actionable message | PASS |

### Probe Execution

No conventional probes (`scripts/*/tests/probe-*.sh`) found. Phase does not declare probe-based verification.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CLI-03 | 05-01, 05-04 | Auto-detect project toolchain | SATISFIED | detect.py detects ruff/pylint/flake8 from pyproject.toml + PATH; cli.py has `detect` subcommand |
| CLI-04 | 05-01, 05-04 | First-run auto-init (tools.yaml + gate.yaml) | PARTIAL | tools.yaml generation implemented and wired into review pipeline (D-20). gate.yaml deferred to Phase 6/7 per D-23. REQUIREMENTS.md traceability confirms this split |
| CLI-05 | 05-02, 05-04 | Fail-fast reachability probe | SATISFIED | probe_backend() in backend.py; backend-agnostic (cli -> auth status, api -> key presence); resolve-outlet subcommand in cli.py |
| BOTH-04 | 05-03, 05-04 | Outlet selection logic | SATISFIED | resolve_outlet() in outlet_resolver.py; FORGE_OUTLET > gate.yaml > reachability; FAIL CLOSED; no model-capability auto-detect |
| BACKEND-01 | 05-02, 05-03, 05-04 | Pluggable backend abstraction (Phase 5 half) | SATISFIED | backend.py locks config schema + resolution order + probe. Adapter implementations deferred to Phase 7 per D-30 |

No orphaned requirements found -- all 5 requirement IDs mapped to this phase are accounted for across plans.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | No TBD/FIXME/XXX markers found | - | - |
| (none) | - | No TODO/HACK/PLACEHOLDER markers found | - | - |
| (none) | - | No plan-ref comments (D-xx/R2-x/Kimi-/DS-/Mimo/Consensus) found | - | - |
| backend.py | 166,169 | `return []` in load_backend_configs | Info | Correct behavior for None/empty config input, not a stub |

All phase-modified files are clean of debt markers and plan-reference comments.

### Human Verification Required

No items require human verification. All truths were verified programmatically via code inspection, behavioral spot-checks, and test execution.

### Gaps Summary

No gaps found. All 4 roadmap success criteria verified. All 9 plan-level truths verified. All 14 artifacts exist, are substantive, and are wired. All 6 key links verified. 148 phase-specific tests pass. 889 full suite tests pass (zero regressions). No debt markers. No plan-ref comments. CLI-04 partial status is documented and acknowledged in REQUIREMENTS.md traceability table (gate.yaml deferred to Phase 6/7 per D-23).

---

_Verified: 2026-06-01T23:45:00Z_
_Verifier: Claude (gsd-verifier)_
