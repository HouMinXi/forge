# Phase 42: CLI key fast-fail (F8) + claim_type oracle (7.1) - Context

**Gathered:** 2026-07-25
**Status:** Ready for planning. This document is the ENTRANCE -- carry it to
mimo to generate the PLAN, then static + multi-model adversarial review.
**Source:** PM direct recon against main @ 74adbf2. Every file:line below was
read by the PM this session, not inherited from a report. Code graph refreshed
2026-07-25 (full build, 286 files / 5436 nodes / 42597 edges).
**Verified-by:** forge PM. Ground truth is the code at the cited lines, not the
roadmap one-liner.

**CORRECTION 2026-07-25 (post-mimo verification; disclosed per Fleet Law S1).**
mimo's independent ground-review (42-CONTEXT-VERIFICATION.md) caught a real
defect in the PM's first-draft F8 seams: the PM asserted "the review command
does not validate key resolvability before dispatching the pipeline," which is
FALSE. An early fast-fail guard for api_key_env already exists (commit 92ca717,
cli.py:2396-2400). Root cause of the PM error: the PM's Read window ended at
cli.py:2390, several lines short of the guard at 2396, and asserted a negative from
a truncated read. The F8 Phase-Boundary line, seams, and Q3 are CORRECTED below;
F8 is now scoped as an EXTENSION (api_key_file + vertex), not a greenfield build.
Every other anchor passed mimo's verification unchanged, and the claim_type half
is fully correct.

<domain>

## Phase Boundary

This phase builds EXACTLY two things, both logic-bearing:

1. **F8 CLI key fast-fail (EXTENSION, not greenfield -- see CORRECTION above).**
   An early fast-fail guard already exists for api_key_env (commit 92ca717,
   cli.py:2396-2400). This phase EXTENDS it to the credential types it currently
   skips (api_key_file, vertex), OR declares the existing coverage sufficient and
   closes F8. Scope decided by the plan; see corrected F8 seams + Q3.

2. **claim_type oracle (7.1).** Mechanically derive "WHAT is claimed" for each
   finding (a claim_type), aligned to the existing Phase 43 ledger `axis_claim`
   field, carrying a `version_sensitive` attribute that later phases consume.

It does NOT build, and the plan must NOT pull in:
- Phase 44 EVAL-ON-DUTY (case generation / eval gate). Phase 42's only hard
  dep is Phase 43, which is MERGED. See Dependencies.
- Phase 51 BASIS-DISCLOSE (ON WHAT AUTHORITY). claim_type composes WITH basis
  but is a separate, orthogonal column; 42 ships claim_type only.
- Phase 52 ENV-MANIFEST. 42 only DEFINES the `version_sensitive` attribute that
  52 will read; 42 does not build the manifest or any version-checking.
- The Router onboarding compat batch (F1 SSE / F3 trust / F4 live-probe / F2 /
  F5 docs) -- scheduled separately AFTER Phase 42
  (reports/router-friction-triage-20260725.md). In particular F8's key check is
  a NO-NETWORK resolvability check; the LIVE network probe is the Router batch's
  F4, not this phase.

## Provenance Warning (read before trusting "F8" or "7.1")

The F-numbers and 7.x numbers are context-local labels, NOT a global spec
ledger. The same "F8" means an unrelated thing in Phase 35
(`35-01-PLAN.md:89`, an isinstance check on sampling content); Phase 41's "F1"
was a plan-ref leak; Phase 31 has its own F2/F3. 40-CONTEXT.md already
established this for the 7.x series. Do not cross-reference F-numbers between
phases.

- **F8 "CLI key fast-fail" has ONLY a name.** Grepped `.planning/` + `docs/`:
  it appears as a bare label in ROADMAP.md:37, STATE.md, 40-CONTEXT.md:227, and
  v2.8tail-v3-DISPATCH-SCHEDULE.md:524 -- and NOWHERE with a behavior spec.
  There is no surviving design doc. **The plan DEFINES F8's exact behavior**
  from the code gap below; it does not inherit a spec. This is a real, honest
  underdetermination, not a gap in this recon.

- **7.1 "claim_type oracle" has a design sketch** (not a full spec) in
  v2.9-V3-GROUNDTRUTH-SCHEDULE.md:68-69, 438-452, 466-471. Captured under
  "Design intent" below. The one-way schema constraint (43-4) is real and
  binding.

## PM-verified code seams (main @ 74adbf2)

### F8 -- how an API key is handled TODAY (the gap fast-fail closes)

- **Config parse already fast-fails on "neither key configured":**
  `backend.py:310-320` -- an api backend must set exactly one of `api_key_env`
  (env var NAME) or `api_key_file` (path); "both" and "neither" both raise at
  config load. So F8 is NOT about missing config -- that case is covered.
- **The key VALUE is resolved DEEP, at invoke time:** `llm_invoke.py:840-862`
  inside `_invoke_api`:
  - `:842` read api_key_file (OSError -> LLMInvokeError "cannot read api_key_file")
  - `:848` empty file -> LLMInvokeError "api_key_file is empty"
  - `:853` `os.environ.get(api_key_env, "")`
  - `:854-857` unset env var -> LLMInvokeError "API key env var %r is not set"
- **...and per-pass, inside a retry loop:** `_invoke_api` runs with
  `max_attempts=5, initial_delay_s=2.0` (`llm_invoke.py:834-835`), loop at
  `:879`. A configured-but-unresolvable key is only discovered AFTER the review
  pipeline has spun up and dispatched a pass, and may burn retry/backoff before
  surfacing.
- **An early fast-fail guard ALREADY EXISTS for api_key_env** (commit 92ca717,
  `cli.py:2396-2400`), AFTER outlet/backend resolution but BEFORE the review
  state machine:
  `if backend.format != "vertex" and backend.api_key_env: if not
  os.environ.get(backend.api_key_env): raise CliError("API key env var %r is not
  set")`. So a missing ENV-VAR key on a non-vertex backend ALREADY fails fast
  with one clear message (not 3 deep INFRA findings, one per pass).
- **A resolvability probe also exists** (`_probe_api`, `backend.py:600-628+`,
  presence-only, no network) and resolves BOTH api_key_env presence and vertex
  creds. On the review path it is invoked via `_reachability`
  (`cli.py:2336-2343`) into `resolve_outlet(reachability_fn=...)`
  (`cli.py:2345-2351`) -- but that call's job is OUTLET auto-detection, not the
  key gate. The key gate is the explicit 92ca717 guard above.

Net grounded gap (CORRECTED -- SMALLER than the first draft claimed): the
92ca717 guard fires ONLY when `backend.api_key_env` is set AND the backend is
non-vertex. It therefore does NOT cover:
- **api_key_file backends** -- the guard's `and backend.api_key_env` is falsy
  (a backend sets api_key_env XOR api_key_file, verified backend.py:310-320), so
  the guard is skipped and a missing/empty key FILE still fails deep at
  `_invoke_api` (`llm_invoke.py:840-851`), per-pass, in the retry loop.
- **vertex backends** -- explicitly excluded by `format != "vertex"`;
  unresolvable ADC / credentials_path still fails deep at invoke.

So F8 is NOT "build fast-fail from scratch" -- it is "EXTEND the existing 92ca717
guard to the two credential types it skips (api_key_file, vertex), reusing
`_probe_api` which already resolves both," OR judge that api_key_file/vertex
fast-fail is not worth it and close F8 as already-covered. The plan decides and
states which.

### claim_type -- the Phase 43 ledger field it aligns to

- **The ledger field EXISTS:** `ledger.py:41-53` -- `LedgerRow` (frozen
  dataclass, "Schema v1") has `axis_claim: str`; deserialized at `ledger.py:108`.
- **It is currently HARDCODED at both write sites:**
  - `machine.py:1211` -- `axis_claim="review"` (batch write at terminal-state
    finalize).
  - `cli.py:1321` -- `axis_claim="manual"` (the `ledger mark` command).
- **No claim_type enum/table exists yet** on the findings side (`findings.py`
  has no claim_type; its only "claim" reference at `:18` is an unrelated
  line-claim). The oracle is greenfield on the findings side, CONSUMING the
  existing `axis_claim` string field.

## Design intent (claim_type oracle -- what the source docs commit to)

From v2.9-V3-GROUNDTRUTH-SCHEDULE.md (binding where it constrains schema,
sketch where it describes behavior):
- **claim_type = WHAT is claimed**, orthogonal to **basis = ON WHAT AUTHORITY**
  (Phase 51). Two separate columns on the same finding (`:444-445`).
- **Derived MECHANICALLY, never model-self-reported** (`:466-468`) -- "a model
  asked for its own authority confabulates." This is a hard design law: the
  oracle computes claim_type from pipeline signals, it does not ask the LLM to
  self-classify.
- **43-4 one-way constraint (binding):** the oracle must consume / align to the
  existing `axis_claim` schema; the alignment cost is "borne by 42" -- Phase 43
  does not change for Phase 42 (`:68-69`).
- **Carries a `version_sensitive` attribute** -- a column on the claim-type
  table that Phase 52 reads to decide version-sensitivity mechanically
  (`:466-471`). 42 DEFINES the column; 52 CONSUMES it.
- Two mechanical sub-fields are named as already-computed by the pipeline and
  may inform the design: `falsification_survived` (bool), `convergence_rounds`
  (int) (`:446-448`). No new judgment required to populate them.

## Open design questions (the PLAN must resolve; honest-failure PRE-AUTHORIZED)

These are genuinely open. The plan should present options with a clear pick, not
silently assume. **Honest-failure is pre-authorized on each:** if grounding
during planning shows the item is smaller/larger/already-handled, REPORT that as
a finding -- do NOT fabricate scope to match the roadmap line.

F8:
1. Scope of "CLI": the forge review CLI entry (fast-fail before pipeline), the
   `cli`-type backend (claude auth-status), or both? Grounded read favors "the
   review command, API backends" -- but confirm and state it.
2. Placement: a pre-dispatch gate reusing `_probe_api`, vs. hoisting the
   `llm_invoke.py:853` check earlier. Which outlets does it cover (subprocess /
   inline / subagent)?
3. **Already-covered: PARTIALLY RESOLVED (see CORRECTION + corrected seams).**
   The api_key_env case is ALREADY fast-failed by the 92ca717 guard
   (`cli.py:2396-2400`) -- do NOT rebuild it. The open part is only whether to
   EXTEND the guard to api_key_file + vertex (reusing `_probe_api`, which already
   resolves both), or declare existing coverage sufficient and close F8. State
   which, with the one-line reason. (This is the F5-of-the-router-RCA lesson,
   now realized on THIS phase: the first-draft "no validation" claim was wrong;
   verify before building.)
4. Exit code + message shape for the fast-fail.

claim_type:
5. The claim_type value set (the enum): what are the categories, and how are
   they DERIVED mechanically from `pass_provenance` / finding source / axis /
   the two named sub-fields? The docs give the philosophy, not the enum.
6. Where claim_type surfaces: findings dataclass + SARIF property + verdict
   header + how it aligns to the ledger `axis_claim` write at `machine.py:1211`
   (does the oracle REPLACE the hardcoded "review", and does `manual` at
   cli.py:1321 stay literal?).
7. `version_sensitive`: derivation rule (which claim_types are version-sensitive)
   and its representation as a forward-compatible column for Phase 52.

## Output contract (what mimo's PLAN must deliver)

- A GSD-style phase plan with dependency-ordered waves, each task naming the
  file(s) it touches and a done-condition provable by a command or re-read.
- Full function signatures for every new/changed function (repeat the signature;
  "from <source>" attribution for every referenced value; empty/None behavior;
  FACT vs ASSUMPTION tags). Pure ASCII inside every fenced block (the git
  non-ASCII gate never sees a gitignored plan, so the plan itself must be clean).
- Each open question above answered with options + a pick, or explicitly
  deferred with a reason.
- A threat_model / inversion note per part (how it fails; the check that catches
  it).
- Bug-injection points named UP FRONT for each part (inject AT the fix site, not
  near it): F8 -- the NEW api_key_file/vertex extension guard (NOT the pre-existing
  92ca717 api_key_env guard -- injecting there is "near, not at" and stays green
  when the extension is removed); claim_type -- the derivation seam AND the
  ledger-alignment write.

## Acceptance gates (freeze the plan toward these)

From the source schedule + project law:
- **Logic-bearing -> full 3-cycle static review + smoke test.** Both parts.
- **Bug-injection proof for BOTH parts** (Golden Rule 2): inject the bug the
  test targets -> watch FAIL -> revert -> watch PASS, at the exact fix site.
  - F8: if extending -- remove the NEW api_key_file/vertex extension -> a test
    with a missing/empty key FILE (or unresolvable vertex creds) asserting early
    clear failure goes RED. Do NOT inject at the pre-existing 92ca717 api_key_env
    guard (that stays green when the extension is removed = false green). If F8
    closes as already-covered, there is no new guard to inject -- the deliverable
    is the trace proving api_key_file/vertex fast-fail is not needed. (Editable-
    install trap: force PYTHONPATH to the worktree src pre-merge.)
  - claim_type: strip the derivation -> a test asserting the derived claim_type /
    ledger alignment goes RED.
- **claim_type is MECHANICAL, not a prompt change** -> it does NOT require the
  Phase 44 eval-delta gate (that gate is for review-PROMPT/semantic changes like
  Phase 41). This is a scope-fencing fact: do not let the plan attach an
  eval-delta requirement to a mechanical derivation.
- Every new/modified logic line executed by >=1 test (diff-cover / pytest --cov
  against the diff).
- Real-path smoke once (Golden Rule 3): exercise the real CLI / real ledger
  write, not only mocks.

## Downstream consumers (schema stability matters)

Phase 42 is the ROOT of a downstream chain: Phase 51 (basis) composes with
claim_type; Phase 52 (env-manifest) reads the `version_sensitive` column;
`version_sensitive` is described as "a new column ... borne by 42 like the 43-4
constraint." Design the claim_type schema for forward-compat: adding a column
later should not require rewriting 42's oracle. The plan should state the schema
explicitly so 51/52 can pin against it.

## Dependencies

- **Phase 43 (LEDGER) -- MERGED (8f7cdd6).** `axis_claim` field present. This is
  Phase 42's only hard dependency, and it is met. Phase 42 can proceed now.
- No dependency on Phase 44 / 51 / 52 (those consume 42, not the reverse).

## Project law (binding on the plan and the implementation)

- impl != reviewer; PM does not implement source. The plan is generated by mimo;
  static + multi-model adversarial review is a SEPARATE step; the main session
  ground-truth-verifies and is the only one that commits. No auto-merge.
- Commit messages: human-voiced, WHY-not-WHAT, no plan-refs / F8: / D-xx /
  severity labels / task IDs in code or messages. Author Minxi Hou. Self-check:
  `grep -rnE '#.*(F[0-9]+:|[Dd]-[0-9])' src/ tests/`.
- No non-ASCII in code (em dash / smart quotes / arrows). Gate before commit.
- Worktree discipline: all work in a linked worktree, never the main tree.
- `.planning/` is gitignored, disk-only, never staged/pushed.

</domain>
