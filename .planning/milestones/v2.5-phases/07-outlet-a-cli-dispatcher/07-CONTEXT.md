---
phase: 07
name: Outlet A CLI Dispatcher
slug: outlet-a-cli-dispatcher
date: 2026-06-02
decisions: 14
---

# Phase 7 Context: Outlet A CLI Dispatcher

<domain>
When outlet resolves to "cli", SKILL.md dispatches the review to `code-forge review` -- a mechanical enforcer where Python code (StateMachine) counts consecutive clean rounds and fake CLEAN is structurally impossible. Phase 7 is plumbing: bridge the SKILL.md placeholder to the existing CLI pipeline, generalize the LLM invocation to the configured backend, and add the `--outlet` flag.
</domain>

<code_context>
## Existing infrastructure (DO NOT rebuild)

| Component | File | What it does |
|---|---|---|
| StateMachine | machine.py (819 lines) | consecutive_clean_rounds counter, fixpoint detection, HOLD/ESCALATED, max_total_rounds |
| 3-pass l1_provider | factories.py:206-254 | qodo/expert/adversarial pass configs, llm_invoke per pass, dedup by fingerprint |
| RealFalsifier | falsify_real.py | 10-step anti-hallucination per finding via llm_invoke |
| llm_invoke | llm_invoke.py (134 lines) | `claude -p --model M --output-format json` subprocess wrapper |
| review subcommand | cli.py:410-633 | _run() wires registry + baseline + StateMachine.run() |
| resolve-outlet | cli.py:1102, outlet_resolver.py (171 lines) | FORGE_OUTLET > gate.yaml > auth probe |
| backend config | backend.py (467 lines) | BackendConfig (type/model/format/base_url/api_key_env), probe_backend (probe-only) |

## Key integration points

- cli.py:611 `build_l1_provider(engine_choice, resolved)` -- entry for pass execution
- llm_invoke.py:47 `llm_invoke(prompt, model, timeout_s)` -- the function to generalize
- llm_invoke.py:41 `shutil.which("claude")` -- hardcoded binary (GA2 target)
- SKILL.md:1338 Phase 7 placeholder -- the bridge target (GA1)
- exit_codes.py: PASS=0, FAIL=1, CLI_ERROR=2, BUSY=3, ESCALATED=4
</code_context>

<decisions>

## GA1: Skill -> CLI bridge -- THIN TRIGGER

- **D-01:** SKILL.md's "cli" branch issues ONE `code-forge review` Bash call, waits for exit, reads result. No re-orchestration of cycles (that is machine.py's job; re-orchestrating re-opens the fake-CLEAN hole).
- **D-02:** Exit code contract: 0 = PASS (proceed); non-zero = read stderr + `.code-forge/state.json`, report error, STOP.
- **D-03:** OPEN for planner: map SKILL.md's "committed" review intent to explicit `--baseline`/`--head` flags. Default `code-forge review` is uncommitted (cli.py:551). Pick one mapping and lock it.

## GA2: Generalize llm_invoke to configured backend -- YES

- **D-04:** Add `backend: BackendConfig` parameter to `llm_invoke()`. The 2 callers (factories.py:227, falsify_real.py:38) pass the resolved backend from `resolve_backend()`.
- **D-05:** Dispatch by `backend.type`: `"cli"` = subprocess (existing path), `"api"` = HTTP call using format (openai|anthropic), base_url, api_key_env.
- **D-06:** BackendConfig needs a new field for the CLI binary (currently NO `command` field -- V2 correction). Either add a `command` field or default cli-type to `"claude"` when unset. This is NEW work, not a read of existing config.
- **D-07:** Default remains plain `claude -p` via the session-default cli backend (BACKEND-01 default). Complexity/size routing is a NON-GOAL.
- **D-08:** backend.py stays probe-only. Do NOT add execute there. Execution lives in llm_invoke.py.

## GA3: Residual Phase 7 scope -- LOCKED

- **D-09:** Phase 7 = GA1 (bridge) + GA2 (pluggable invoke) + `--outlet` flag on review subparser. Reuse machine.py, factories, falsify_real AS-IS.

## GA4: Counter divergence -- DOCUMENT, DO NOT UNIFY

- **D-10:** Phase 7 ships Outlet A reusing machine.py's existing binary reset, AS-IS.
- **D-11:** Phase 7 MUST document the non-equivalence: Outlet A = binary (any CONFIRMED resets consecutive_clean_rounds); Outlet B = TRUST-07 severity-gated (P0/P1 = full reset, P2 = cycle restart without counter reset, P3 = density-gated). Ship Outlet A as "available", NOT as "behaviorally identical to B".
- **D-12:** Counter unification is its own gray area, candidate for Phase 8+. Lean direction: bring machine.py UP to TRUST-07 semantics (requires threading severity into _fixpoint_reached, which today keys on Disposition only). Decision deferred.

## GA5: Progress/reflow -- BLACK-BOX WAIT

- **D-13:** Phase 7 MVP: call -> wait exit code -> read state.json. No streaming progress.
- **D-14:** stderr progress lines deferred to Phase 8 alongside orphan protection + cost transparency. Progress is UX, not correctness.

</decisions>

<non_goals>
- Do NOT rebuild the convergence counter (it exists as consecutive_clean_rounds in machine.py)
- Do NOT re-author the qodo/expert/adversarial passes or the falsifier (they exist)
- Do NOT add execute_review() to backend.py (probe-only by design)
- Do NOT unify Outlet A/B convergence semantics (GA4 deferred)
- Do NOT add complexity/size routing between backends (BACKEND-01 NON-GOAL)
- Do NOT add streaming progress (Phase 8)
</non_goals>

<deferred>
- Counter unification (GA4: bring machine.py up to TRUST-07 severity-gated semantics) -- candidate Phase 8+
- stderr progress lines during CLI review run -- Phase 8
- Subprocess orphan protection -- Phase 8
- Cost transparency (token counting) -- Phase 8
</deferred>

<canonical_refs>
- src/code_forge/machine.py -- convergence state machine (DO NOT modify in Phase 7)
- src/code_forge/llm_invoke.py -- LLM subprocess shim (GA2 modification target)
- src/code_forge/factories.py -- l1_provider + falsifier factories (callers of llm_invoke)
- src/code_forge/falsify_real.py -- 10-step falsification (caller of llm_invoke)
- src/code_forge/backend.py -- BackendConfig definition + probe (D-06: add binary field)
- src/code_forge/cli.py -- review subcommand + resolve-outlet (D-03: --outlet flag, baseline mapping)
- src/code_forge/outlet_resolver.py -- outlet resolution logic
- src/code_forge/exit_codes.py -- exit code constants
- src/code_forge/skills/code-forge/SKILL.md:1338 -- Phase 7 placeholder (GA1 bridge target)
- .planning/phases/05-prerequisites/05-CONTEXT.md -- D-06 through D-16, D-20-D-24 (backend + outlet decisions)
- .planning/phases/06-outlet-b-inline-merge/06-CONTEXT.md -- D-01 through D-15 (inline merge decisions)
</canonical_refs>
