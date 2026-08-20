# CP1b R1 briefing — Phase 54 router-onboarding-compat (shared context for all panel models)

You are reviewing a PLAN, not writing code. The plan file is
`.planning/phases/54-router-onboarding-compat/54-01-PLAN.md` in the repo at
/home/houminxi/code/forge (branch main @ 4087b05, tree clean). Read it first,
then read any source files it cites — every anchor you check should be against
the real files, not the plan's claims about them.

## What this phase is

Four router-onboarding fixes for the forge review tool, one plan, six tasks:
- T1 (F2/ROUTER-02): base_url /v1 semantics text in gate.schema.json only.
- T2 (F3/ROUTER-03): `code-forge trust` gains walk-up resolution (reusing
  workspace.resolve_workspace), prints the resolved absolute gate.yaml path
  before mutating trust ops, warns-but-proceeds when resolved != cwd, and
  passes the resolved workspace as the contracts-base to both
  resolve_contract_specs calls.
- T3 (F5/ROUTER-05): doctor prints the user-level config path (or would-be
  location + hint); README pointer; tests patch user_config_path, never
  load_user_backends (conftest autouse trap).
- T4 (F4 core/ROUTER-04): additive kind= taxonomy on LLMInvokeError
  (conn/credentials/sse_body/bad_body) at existing raise sites in
  llm_invoke.py + new probe_backend_live helper in backend.py riding
  unmodified llm_invoke with max_attempts=1 and a frozen-config replace()
  copy (timeout_s=60, max_tokens=32, thinking/effort fields zeroed).
- T5 (F4 wiring/ROUTER-04): doctor --live flag threading; direct helper call
  bypassing the 5-min probe cache; failures flow through the existing
  has_fail -> exit 1 pipeline; cli-type backends get an informational skip.
- T6: human real-path smoke checkpoint.

Locked user decisions (D-01..D-12) are in 54-CONTEXT.md — the plan must be
judged AGAINST them, not against your own redesign of them.

## Prior review history (do NOT re-report these; if you find one independently, mark it CONFIRMING, not new)

Internal round 1 (two independent reviewers) found and the plan fixed:
1. Whitelist-negative test homelessness -> now rides
   test_dispatch_sampling_unknown_kind_never_falls_back (tests/test_mcp_server.py:919),
   file added to Task 4 files + frontmatter.
2. Task 2 was silent on the contracts-base argument of resolve_contract_specs
   -> now passes the resolved workspace at both call sites.
3. FORGE_PROJECT_DIR env hijack of trust tests -> autouse delenv fixture
   mandated in BOTH existing trust test files.
4. `dataclasses` module name unbound in backend.py -> plan adds `replace` to
   the existing from-import (backend.py:30).
5. Thinking-burn residual -> probe copy zeroes thinking_type/thinking_budget/
   reasoning_effort.
6. Task 5 patch target ambiguity -> code_forge.backend.probe_backend_live.
7. grep-count criterion ambiguous -> explicit 16 -> 28 with the two-raise-arm
   shape clause.
8. VALIDATION.md frontmatter/sign-off stale -> flipped; quick-run now
   includes tests/test_mcp_server.py.

Internal round 2 residuals (same reviewers) also fixed: the five above items'
fix-scope gaps (existing-test exposure, verify-command omission, ternary
shape hole, positive-harness miscitation, reasoning_effort residual).

Internal round 3 (exit round) residuals also fixed: autouse delenv fixture
widened to BOTH existing trust test files; Task 4 verify + VALIDATION
quick-run gained tests/test_mcp_server.py; grep-criterion shape clause;
whitelist-negative re-pointed to the :919 negative test; reasoning_effort=""
added to the probe copy; Task 4 llm_invoke patch target named as the source
module; file manifests synced (tests/test_contract_wiring.py +
tests/test_mcp_server.py in Task 2/4 files and frontmatter).

## CP1b R1 outcomes and what changed since (R2 candidates read this)

**deepseek R1: B=0 H=0 M=1 L=3 — all four confirmed against ground truth and fixed:**
- M-1: the dispatch line `live=args.live` had no executing test -> Task 5
  behavior (g) now mandates a main()-level dispatch test (pattern
  tests/test_cli_integration.py:181; patch code_forge.doctor.run_doctor)
  plus injection (4): delete live=args.live -> (g) FAILS.
- L-1: build/lib guard was vacuous (gitignored) -> mtime check instead.
- L-2: cli-skip row text claimed "already executed by the offline probe"
  (false: bypass entirely, backend.py:792-793/:812-813) -> reworded to
  "trusted as configured; no live probe applies".
- L-3: test_backend count 153 -> 162 with re-collect caveat.

**kimi k2.7 R1: B=0 H=1 M=0 L=2 — all three confirmed against ground truth and fixed:**
- F-1 HIGH: _format_error_message (llm_invoke.py:691-715) took body_excerpt
  but never used it; openai/anthropic HTTP errors dropped the body, and the
  wrong-/v1 misconfiguration (the phase's headline debug case) surfaces as
  exactly such an HTTP error. Fixed as new Task 4 Step 1.5: the shared
  formatter now appends the excerpt for openai+anthropic (vertex already
  embedded inline), with an excerpt-presence test and injection (5).
- F-2: detail=str(exc) could carry newlines into the one-line doctor row ->
  detail is whitespace-normalized at the helper boundary (precedent
  llm_invoke.py:1518), with a no-newline test.
- F-3: vertex credential raises (:1857/:1862/:1871) lacked kind= -> added
  kind="credentials" (the google-auth ImportError stays kindless by design);
  grep-count target updated 28 -> 31.

Both internal reviewers re-verified the ds fixes at 0/0/0/0 BEFORE the kimi
fixes landed; the kimi fixes are under internal verification now.

**deepseek R2 (on the R1-fixed plan): B=0 H=0 M=0 L=2 — both confirmed and fixed:**
- L-1: the Task 2 warn was placed at the shared post-resolution prefix,
  which would leak it into --status (contradicting test (f) and the locked
  --status-stays-as-is decision). Fixed: warn scoped to the two mutating
  paths only; test (f) now asserts no warn from --status after walk-up.
- L-2: the fallback taxonomy label was unpinned ("Claude's Discretion")
  while the headline wrong-/v1 404 case (exit_code set, kind="") lands
  exactly there. Fixed: two more pinned labels — "http-error" (exit_code
  >= 400, no kind) and "unclassified" (true unknowns) — propagated to the
  objective line, Task 5 test (d) (now seven classes), and the T6 smoke
  expectation (wrong-path now expects an http-error row WITH the body
  excerpt as the Step 1.5 witness).

**kimi R2 (on the kimi-R1-fixed plan): B=0 H=0 M=0 L=3 — one already fixed, two confirmed and fixed:**
- L-1: "60s total" was per-REQUEST, not total — the probe passed no
  continuation_breaker, so a fresh threshold-5 breaker (:1370) plus
  _continue_truncated's budget=2 (:1161) allowed ~3x60s on a
  persistently-truncating backend. Fixed: the probe call now passes
  `continuation_breaker=TruncationBreaker(threshold=1)` — the first
  truncation's record_truncation raises kind="truncated" BEFORE any
  continuation request; hard 60s bound restored; new pinned label
  "truncated-output" (eight classes total, all propagated).
- L-2: Task 5 injection (2) cited a helper-call-assertion guard test that
  no behavior mandated. Fixed: behavior (b) now asserts the helper was
  called exactly once per api backend and never via probe_backend.
- L-3: T6 smoke text omitted the fallback class for the headline 404 —
  ALREADY FIXED by the ds-R2 L-2 edit (T6 check 3 now expects the
  http-error row with excerpt). Confirming, not new.

## Declared positions (adjudicate these EXPLICITLY — say accept or refute with evidence, do not silently rediscover them)

A. D-08 interpretation: warn condition is `resolved_workspace != cwd.resolve()`,
   not a literal "not a git repo root" probe. A non-git directory that IS the
   workspace root gets no warning. Research-endorsed as the implementable form.
B. Probe cap is max_tokens=32, not a literal 1-token: the truncation
   continuation (_TruncatedResponse, llm_invoke.py:1485) runs BEFORE the
   attempt check (:1497), so a truncating cap multiplies requests even at
   max_attempts=1. Boundary of the "snapshot of a real review" framing:
   thinking_type/thinking_budget/reasoning_effort are zeroed on the probe
   copy, so the probe does NOT exercise a backend's configured thinking
   settings (deliberate -- protects the 32-token cap); `stream` stays
   configured and remains the F1 regression witness.
C. Live probe covers type=api backends only; cli-type backends get an
   informational skip row (the offline probe already executes them).
D. Six tasks in one plan exceeds the generic 2-3 target; locked by user
   decision D-12.
E. The plan adds no .git probe to trust; the always-printed resolved path is
   the disambiguator.
F. (new, from kimi R1 fix) Task 4 Step 1.5 deliberately changes the
   REVIEW-PATH HTTP error message shape (excerpt appended for
   openai/anthropic) — an owned decision, not a side effect. Existing
   formatter tests are substring-style and verified unaffected.

## Output contract

- Findings with severity BLOCKER/HIGH/MEDIUM/LOW, each with file:line evidence
  you personally verified in the repo (not the plan's citations).
- If a finding's asserted-wrong value and proposed-correct value are identical
  strings, discard it before reporting (degenerate-output detector).
- End with exactly: `SCORECARD: B=<n> H=<n> M=<n> L=<n>`
- A CLEAN verdict (0/0/0/0) is a valued outcome — report it if that is what
  you find. A manufactured finding costs more than a missed nit.
