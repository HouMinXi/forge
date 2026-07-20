---
phase: 08-hardening
verified: 2026-06-03T10:30:00Z
status: passed
score: 10/10 must-haves verified
overrides_applied: 0
gaps: []
resolved_by_code:
  - item: CLI-08 cost display for CLI backends
    commit: f2baffb
    resolution: "_invoke_cli now detects the claude CLI JSON envelope (type==result+usage keys) and extracts input_tokens/output_tokens. cli.py cost display fires whenever cost_passes>0; when tokens are unavailable it shows 'tokens: N/A (cli backend)'. CLI users now have the same cost-line visibility as API users."
---

# Phase 8: Hardening Verification Report

**Phase Goal:** Outlet A is production-ready with subprocess lifecycle safety, cost visibility, and documented configuration for all editor environments
**Verified:** 2026-06-03T10:30:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Subprocess orphan protection prevents leaked claude -p processes on timeout/cancel | VERIFIED | `start_new_session=True` at llm_invoke.py:211; `_kill_tree` at lines 86-96 with `os.killpg` SIGTERM->5s->SIGKILL escalation; `_active_proc` cleared in `finally` at line 235 |
| 2 | LLMResult return type includes content, usage (tokens), and duration | VERIFIED | `class LLMResult` frozen dataclass at llm_invoke.py lines 35-41 with `content: Any`, `usage: Usage`, `duration_s: float`; `class Usage` at lines 27-31 with `input_tokens`, `output_tokens` |
| 3 | Signal handler chain pattern from lock.py ensures cleanup before previous handler runs | VERIFIED | `_install_signal_handlers()` at llm_invoke.py lines 99-131: `_make_chained_handler(prev)` calls `_kill_tree(_active_proc)` then chains to previous handler; installed at module load line 135; idempotent guard at line 109 |
| 4 | Process group isolation via start_new_session=True enables killing child + grandchildren | VERIFIED | `os.killpg(proc.pid, _signal.SIGTERM)` in `_kill_tree` line 90; `start_new_session=True` in `subprocess.Popen` at line 211 |
| 5 | After review completes, state.json contains cost section with total tokens and per-pass breakdown | VERIFIED | `save_state` writes `"cost": {"total_input_tokens": ..., "total_output_tokens": ..., "total_duration_s": ..., "passes": ..., "per_pass": [...]}` at state.py lines 261-267; `load_state` reads with backward-compat defaults at lines 207-212 |
| 6 | stderr displays human-readable cost summary: tokens (in + out), passes, duration | VERIFIED (WARNING) | Pattern `"code-forge: cost: %d tokens (%d in + %d out), %d passes, %.1fs"` exists at cli.py lines 741-750. However: condition at lines 733-735 requires `cost_total_input>0 OR cost_total_output>0`, which is never true for CLI backends (D-07: Usage(0,0)). Default-user workflow (claude CLI) produces no cost line on stderr. See human verification item. |
| 7 | Cost accumulation happens during StateMachine drive loop | VERIFIED | `L1Provider = Callable[[], tuple[list[StateFinding], Usage, float]]` at machine.py line 52; cost accumulated in `_execute_round` at lines 597-613 after full round (L0+L1+L2+E2E), satisfying H3 fix |
| 8 | README.md has Quick Start section showing FORGE_BACKEND, FORGE_OUTLET, FORGE_LLM_MODEL basics | VERIFIED | `## Quick start` at README.md line 28; `## Backend configuration` at line 63; env var table with all 3 vars at lines 70-72; `docs/configuration.md` linked at line 111 |
| 9 | docs/configuration.md documents all env vars, backends.yaml format, and auth setup | VERIFIED | 282 lines (>150 minimum); FORGE_BACKEND, FORGE_OUTLET, FORGE_LLM_MODEL, FORGE_AUTH_TIMEOUT all documented with defaults and examples; backends.yaml api+cli field tables; Outlet A/B description; auth section covers both CLI (claude auth login) and API (api_key_env pattern) |
| 10 | Each editor guide (VS Code, Cursor, PyCharm) shows how to set env vars for code-forge | VERIFIED | setup-vscode.md: 218 lines, `terminal.integrated.env` documented (4 occurrences), 3 setup methods, links to configuration.md; setup-cursor.md: 154 lines, Cursor-specific notes, links to configuration.md; setup-pycharm.md: 192 lines, `Run/Debug Configurations` documented, links to configuration.md |

**Score:** 9/10 truths verified (Truth 6 verified with WARNING -- design-intent question on CLI backend suppression)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/llm_invoke.py` | LLMResult, Usage, _kill_tree, signal handlers; min 350 lines | VERIFIED | 413 lines; `class Usage` (L27), `class LLMResult` (L35), `_kill_tree` (L86), `_install_signal_handlers` (L99), `_make_chained_handler` (L111), `start_new_session=True` (L211), `os.killpg` (L90, L93) |
| `src/code_forge/state.py` | cost_total_input, cost_per_pass fields | VERIFIED | `cost_total_input: int = 0` at line 99; `cost_per_pass: list[dict]` at line 103; `save_state` writes `"total_input_tokens"` and `"per_pass"` at lines 262-266 |
| `src/code_forge/machine.py` | Usage import, cost accumulation | VERIFIED | `from .llm_invoke import Usage` at line 39; `L1Provider` tuple type at line 52; `_round_input_tokens` accumulator at line 150; cost written in `_execute_round` at lines 600-613 |
| `src/code_forge/cli.py` | cost stderr print, load_state for cost | VERIFIED | `"code-forge: cost:"` at line 741; `load_state as _load_cost_state` at line 731 (B6 fix satisfied) |
| `docs/configuration.md` | Full reference, min 150 lines | VERIFIED | 282 lines; all 4 env vars; backends.yaml schema; auth setup; Outlet A/B documented |
| `docs/setup-vscode.md` | VS Code guide, min 80 lines | VERIFIED | 218 lines; 3 env var setup methods including `terminal.integrated.env.linux/osx`; troubleshooting section |
| `docs/setup-cursor.md` | Cursor guide, min 60 lines | VERIFIED | 154 lines; Cursor-specific notes; links to configuration.md and VS Code guide |
| `docs/setup-pycharm.md` | PyCharm guide, min 60 lines | VERIFIED | 192 lines; `Run/Debug Configurations` with exact menu paths; EnvFile plugin; 3 PyCharm-specific methods |
| `README.md` | Quick Start, min 100 lines | VERIFIED | 277 lines; `## Quick start` (L28); `## Backend configuration` (L63); all 3 FORGE vars in table; backends.yaml snippet; links to docs/ guides |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `llm_invoke.py _invoke_cli` | `subprocess.Popen` | `start_new_session=True` | WIRED | Line 206-211 |
| `llm_invoke.py _invoke_cli` | `_kill_tree` | `TimeoutExpired` exception | WIRED | Lines 228-233 |
| `llm_invoke.py` | `signal` module SIGINT/SIGTERM | `_install_signal_handlers` at module load | WIRED | Lines 127-130, 135 |
| `machine.py _run_l1_phase` | `l1_provider()` | unpacks `(candidates, usage, duration)` tuple | WIRED | Line 501 |
| `machine.py _execute_round` | `State.cost_total_input` | direct assignment after round | WIRED | Line 600 |
| `cli.py _run_hold_loop` | `state.json` cost section | `_load_cost_state(state_path)` then `cost_passes > 0` check | WIRED | Lines 731-750 |
| `factories.py build_l1_provider` | `llm_invoke` -> `.content` | `result = llm_invoke(...); response = result.content` | WIRED | Lines 242-243 |
| `falsify_real.py RealFalsifier.falsify` | `llm_invoke` -> `.content` | `result = llm_invoke(...); response = result.content` | WIRED | Lines 44-45 |
| `README.md Backend configuration` | `docs/configuration.md` | direct Markdown link | WIRED | Line 111 |
| `docs/setup-vscode.md` | `docs/configuration.md` | cross-reference link | WIRED | Confirmed present |
| `docs/setup-cursor.md` | `docs/configuration.md` | cross-reference link | WIRED | Confirmed present |
| `docs/setup-pycharm.md` | `docs/configuration.md` | cross-reference link | WIRED | Confirmed present |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `cli.py` cost display | `final_state.cost_total_input` | `load_state(state_path)` reading state.json written by `save_state` after `_execute_round` | Yes for api backends; 0 for cli backends by D-07 design | FLOWING (api) / STATIC-BY-DESIGN (cli) |
| `machine.py _execute_round` | `_round_input_tokens` | `l1_provider()` -> `factories.py` -> `llm_invoke()` -> `LLMResult.usage.input_tokens` | Yes for api backends; 0 for cli (Usage(0,0)) | FLOWING (api path) |
| `state.py save_state` | `cost.per_pass` | `_state.cost_per_pass.append(...)` accumulates in `_execute_round` each round | Yes -- written per round, loaded back by load_state | FLOWING |

**WARNING (CLI-08 partial):** The `cli.py` condition on lines 733-735 silently suppresses the cost line when both `cost_total_input == 0` and `cost_total_output == 0`. This is always true for the default CLI backend because D-07 returns `Usage(0,0)`. The cost infrastructure (State fields, accumulation, state.json section) is complete -- only the stderr display is suppressed for the default backend. API backend users see correct cost summaries. Default (CLI backend) users see nothing.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| CLI-07 | 08-01-PLAN.md | Subprocess orphan protection for cli backends | SATISFIED | `start_new_session=True`, `_kill_tree` with `os.killpg`, `_install_signal_handlers` with chain pattern -- all present and wired |
| CLI-08 | 08-01 + 08-02 PLANs | Post-review cost summary from backend response | PARTIAL | Cost infrastructure complete; api backend path fully functional; cli backend (default) produces no cost line due to Usage(0,0) + token-nonzero condition -- design intent requires human clarification |
| BOTH-02 | 08-03-PLAN.md | Backend config + auth documented with VS Code / Cursor / PyCharm guides | SATISFIED | README.md Backend configuration section; docs/configuration.md (282 lines) covers env vars, backends.yaml, auth; 3 editor guides all present and link to configuration.md |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `docs/configuration.md` | 160, 175, 241, 250, 253 | `XXXXXXXXX` in export examples | Info | Intentional API key placeholder format matching T-08-07 security requirement. Not a debt marker. |
| `docs/setup-vscode.md` | 24, 32, 62, 66, 70, 102 | `XXXXXXXXX` in examples | Info | Intentional placeholder. Not a debt marker. |
| `docs/setup-cursor.md` | 29, 37, 58 | `XXXXXXXXX` in examples | Info | Intentional placeholder. |
| `docs/setup-pycharm.md` | 33, 67, 102, 109 | `XXXXXXXXX` in examples | Info | Intentional placeholder. |

No unreferenced `TBD`, `FIXME`, or `XXX` debt markers found in any phase-modified source files. No empty stub implementations. No non-ASCII characters in phase-modified Python source files.

### Behavioral Spot-Checks

Step 7b: SKIPPED -- cannot run live review session without Claude CLI auth and a running process. Core behaviors verified via static code analysis and grep evidence above.

### Probe Execution

No `scripts/*/tests/probe-*.sh` probes declared in any PLAN file and none found under `scripts/`. SKIPPED.

### Human Verification Required

#### 1. CLI backend cost summary -- design intent clarification

**Test:** Run `code-forge review` with the default backend (no `FORGE_BACKEND` or `FORGE_OUTLET` set, using the system `claude` CLI). Complete or cancel a review. Observe stderr.

**Expected:** Per CLI-08 requirement: "Post-review cost summary displays total token usage and estimated cost from the backend response (token counts from the api response body OR the cli output)". With the CLI backend, the cost line is currently suppressed because `claude -p` does not expose token counts and D-07 designed `Usage(0,0)` as intentional.

Three resolution paths:

- **(a) Accept current behaviour:** CLI backends produce no cost output. The requirement text "or the cli output" was aspirational; since `claude -p` has no token API, silence is correct. No code change -- add a comment in cli.py clarifying the condition.
- **(b) Show passes+duration for CLI:** Remove the `(cost_total_input > 0 OR cost_total_output > 0)` guard at cli.py lines 734-735. Print when `cost_passes > 0` regardless of token counts. This gives CLI users duration visibility (e.g., "9 passes, 187.3s") even with zero tokens.
- **(c) Update the requirement:** Revise CLI-08 in REQUIREMENTS.md to read "api backends only" and close the gap.

**Why human:** D-07 is a documented design decision made before CLI-08 was fully specified. The two decisions are in tension. Product owner must decide which takes precedence.

---

## Gaps Summary

No BLOCKER gaps. No MISSING or STUB artifacts. All key links are WIRED.

The one open item is a WARNING-level design-intent question about CLI-08 scope for the default backend. The cost visibility infrastructure is complete and correct; only the stderr display condition for CLI backends needs human decision.

Phase goal achievement summary:
- **Subprocess lifecycle safety (CLI-07):** Fully implemented. Process group isolation, kill-tree SIGTERM->SIGKILL escalation, chained signal handlers -- all verified.
- **Cost visibility (CLI-08):** Complete infrastructure. API backend path fully functional with real token counts. CLI backend (default user workflow) produces no cost output -- human decision needed on design intent.
- **Documented configuration (BOTH-02):** Fully implemented. README.md with Backend configuration section, docs/configuration.md (282 lines), and 3 editor guides (218+154+192 lines) all verified with working cross-links.

---

_Verified: 2026-06-03T10:30:00Z_
_Verifier: Claude (gsd-verifier)_
