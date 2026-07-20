# Phase 40: Honest partial results + convergence (mechanical half) - Context

**Gathered:** 2026-07-15
**Status:** Ready for planning
**Source:** PM bounded recon. No discuss-phase session ran. A research
subagent was spawned first and died 3 times (OOM, then API connection
closed, then an explicit "Fable 5 limit reached" on the third death --
confirmed root cause: Fable-5 quota exhaustion, not an environment flake).
Its 3rd-resume salvage (42 lines) is folded in below where verified; the
rest of this document comes from the PM's own direct reads of the repo.

<domain>
## Phase Boundary

This phase builds the MECHANICAL half of Phase 40 only:
- F4 partial-SARIF / partial-verdict representation
- P3 per-pass timeout/failure surfacing (the adversarial-timeout pain point)
- Large-diff summary/chunking

It does NOT build the SEMANTIC half (convergence plateau, prior-round
memory). See <deferred> for why and for the locked policy decision to use
whenever that half is planned.

## Provenance Warning (read before trusting any "F4" / "7.x" reference)

"F4" and "7.1"/"7.2"/"7.3"/"7.4" are NOT a numbered ledger or spec document.
Grepped the full repo (`.planning/` and `docs/`): these labels appear in
exactly two places, near-verbatim, and nowhere else:
- `.planning/ROADMAP.md:35`
- `.planning/v2.8tail-v3-DISPATCH-SCHEDULE.md:499-500`

"F4" elsewhere in the repo (14-03-SUMMARY.md, 25-REVIEWS-R3.md,
28-REVIEWS.md, etc.) is an UNRELATED per-round review-finding ID that gets
reused independently in every review round -- it is not this F4. Do not
search for a canonical "F4 ledger"; it does not exist. The salvaged
RESEARCH.md's confidence line ("Scope provenance HIGH, verbatim quotes
located") OVERCLAIMS on this point -- verified this session, downgrading
to: F4/7.x are informal one-time labels coined in the dispatch schedule,
not references to an external definition.

"P3" IS solidly grounded: memory `feedback_forge_consumer_pain_points.md`,
one of five numbered consumer pain points from a real 2026-07-07 surflare
dogfood session. No provenance concern there.

## Split rationale (user-confirmed 2026-07-15)

The fuller source note (`.planning/v2.8tail-v3-DISPATCH-SCHEDULE.md:501-503`)
says, verbatim:

> Split option (user's call): plumbing half (partial SARIF, mechanical)
> first; semantic half (plateau / prior-round memory -- changes convergence
> SEMANTICS) moved AFTER 44 so the eval gate can measure it (see 44-6).

The user confirmed this split applies. Rationale: changing convergence
semantics (what counts as "no progress", when to stop retrying) without a
before/after eval delta violates the Fleet Evaluation Laws already binding
this project (pre-registration: no ungated semantic changes; known-answer
validation before trusting a pipeline change). Phase 44 (EVAL-ON-DUTY,
not yet built) is where that eval delta becomes possible -- its own
scope note names "44-6 Phase 40 retro-eval" explicitly.

</domain>

<decisions>
## Implementation Decisions

### Verdict presentation (locked)

- The verdict stays fail-closed always: a round with any incomplete or
  failed pass must never silently present as a full PASS. This is the
  founding principle ("a green verdict is honest or declares what it did
  not verify") applied literally.
- When a pass fails or times out mid-round, findings already produced by
  the OTHER passes in that same round must be surfaced distinctly to the
  user, not folded into one opaque FAIL with no detail.
- Ground truth (PM-verified, not taken from the salvaged research as-is):
  `src/code_forge/outlet_c.py:56-90` (`_l1_provider`) loops over
  `_PASS_NAMES = ["qodo", "expert", "adversarial"]`. When `spawn_fn`
  raises (this is where a pass timeout surfaces, via
  `LLMInvokeError`/`is_timeout` from `llm_invoke.py`), the handler appends
  ONE `StateFinding(source="INFRA", disposition=CONFIRMED, ...)` and
  `continue`s to the next pass name. It does NOT clear or discard findings
  already appended from earlier passes in the same loop. The full
  `findings` list -- real findings plus the one INFRA marker -- is
  returned intact via the 4-tuple.
- Conclusion: the original P3 complaint ("findings lost") is a
  MISDIAGNOSIS. The data survives. The gap is at the REPRESENTATION layer
  (does `receipt.py` / `sarif.py` actually show the surviving findings
  distinctly when the round is FAIL?) -- UNVERIFIED this session, and the
  planner's first investigative duty (see canonical_refs).

### Convergence plateau + prior-round memory (DECIDED, but DEFERRED -- not this phase's build scope)

Recorded here so the decision is not lost before Phase 44 exists:
2 consecutive rounds with zero NEWLY confirmed findings = stop and report.
Never silently escalate to PASS. Never loop forever waiting for a clean
round that will not come. This does NOT ship in this phase -- see
<deferred>.

### Scope split (locked, user-confirmed)

THIS phase's plans must cover only: partial-SARIF/verdict representation,
per-pass timeout/failure surfacing, large-diff summary/chunking. Do NOT
plan convergence-plateau or prior-round-memory tasks in this phase.

### Claude's Discretion (implementation-level; planner decides, present options + a clear pick, do not silently assume)

- **Chunking strategy for large diffs.** Split-by-file vs split-by-hunk vs
  summary-first; how per-chunk findings merge into one round's result; how
  a chunk-level failure interacts with the partial-declaration mechanism
  above. No existing chunking code was found in this recon pass (not
  deeply searched -- planner's first job on this sub-item is to confirm
  whether ANY chunking exists today before designing new, per CLAUDE.md
  false-green trap #2: "large diff chokes backend, no findings").
- **Per-pass timeout salvage policy.** Recommended: keep-and-mark-uncertain
  (consistent with the "findings survive" ground truth above) over
  retry-once or a fully configurable knob -- but the planner should confirm
  this is achievable given `llm_invoke.py`'s existing timeout config
  (`_default_timeout_s()`, `FORGE_LLM_TIMEOUT_S` env var, `_CLI_TIMEOUT_CAP_S
  = 300`) before locking it. Extend the existing knob, do not reinvent one.
- **Ledger representation of a partial round.** `ledger.py:27`
  (`TerminalState(str, Enum)`) currently has NO partial-shaped member
  (grep-verified this session against the Schema v1 enum at `ledger.py:41
  LedgerRow`). Planner must decide: add a new enum member (schema bump) vs
  reuse an existing member plus a payload field distinguishing "opaque
  fail" from "partial: N/3 passes completed". Either is acceptable; state
  the choice and why in the plan, do not leave it implicit.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Scope source
- `.planning/ROADMAP.md:35` -- the one-line phase scope as written in the
  roadmap (compressed, do not treat as the full spec).
- `.planning/v2.8tail-v3-DISPATCH-SCHEDULE.md:495-503` -- the fuller
  scheduling note this line was compressed from, including the
  plumbing/semantic split and the 44-6 retro-eval cross-reference. This is
  the actual source; the ROADMAP line is a summary of it.

### Partial research salvage (read with the Provenance Warning above in mind)
- `.planning/phases/40-honest-partial-convergence/40-RESEARCH.md` -- 42
  lines salvaged from a research subagent that died 3 times on Fable-5
  quota exhaustion. Contains one real, PM-verified insight (the P3
  misdiagnosis correction, folded into Decisions above) but overclaims
  confidence on F4/7.x provenance and is missing a Validation Architecture
  section and a structured Design Forks section despite its own confidence
  line implying both exist. Treat as a lead, not a source of truth.

### Code seams (PM-verified this session; file:line accurate as of main @ cfade37)
- `src/code_forge/outlet_c.py:56-90` (`_l1_provider`) -- see Decisions
  above for the full trace. This is the most load-bearing citation in this
  document; re-verify it still reads this way before building on it.
- `src/code_forge/machine.py:246` (`StateMachine.run`), `:515-529`
  (CONFIRMED-count folds to FAIL/PASS), `:572-635`
  (`diagnose_non_convergence` -> `Verdict.ESCALATED` path) -- the verdict
  is a coarse enum computed FROM the finding list; it does not consume or
  destroy the list.
- `src/code_forge/receipt.py` -- `_split_by_pass` (line 29) and
  `write_receipts` (line 58) already exist. UNVERIFIED this session
  (first investigative duty for the planner): does the written receipt
  already distinguish per-pass completion status, or does `_split_by_pass`
  do something unrelated (e.g. split by file)? Read this file's actual
  body before designing any new representation -- there may be less new
  code needed here than the phase description implies.
- `src/code_forge/ledger.py:27` (`TerminalState(str, Enum)`), `:41`
  (`LedgerRow`, docstring says "Schema v1") -- grep-verified no
  partial-shaped enum member exists today.
- `src/code_forge/sarif.py:228` (`format_summary(state, advisory_count)`)
  -- likely the human-readable "N passes, Ts" CLI summary line (matches
  the cost-line format recorded in forge memory
  `08-hardening/08-CONTEXT.md`). Candidate site for surfacing
  partial-completion state to CLI users; confirm before assuming.
- `src/code_forge/llm_invoke.py:78` (`_CLI_TIMEOUT_CAP_S = 300`), `:289`
  (`_default_timeout_s`), the `is_timeout` flag on the invoke-error path,
  `FORGE_LLM_TIMEOUT_S` env var -- existing timeout knob. Extend, do not
  reinvent.

### Project law (binding, not optional)
- `CLAUDE.md` "Verdict Trust (4 known false-green traps)" -- trap #2
  (large diff -> JSON parse error -> fail-open) is directly this phase's
  large-diff/chunking scope; trap #3 (non-git/empty-diff -> L1 never runs,
  UNCERTAIN COVERAGE) is adjacent and worth the planner's awareness.
- `CLAUDE.md` "Phase Delivery Checkpoint Protocol" -- the 5 gated
  checkpoints this phase must clear before delivery to the PM.
- Founding principle (STATE.md, REQUIREMENTS.md): "a green verdict is
  honest or declares what it did not verify" -- the test every design
  choice in this phase must pass.
- `src/code_forge/machine.py` area, AutoFixer stays stub-for-delivery
  (fix constitution, AMENDMENT 1) -- this phase DECLARES/REPRESENTS
  partial state, it does not auto-fix anything.
- Worktree Phase 0 is mandatory (global + forge CLAUDE.md): create
  `.worktrees/<name>` before any edit; never edit the main tree directly.
- Pure ASCII only; no plan-ref tokens (F4:/D-02:/etc.) in code or commit
  messages (forge CLAUDE.md Commit Message Rules).
- Nyquist validation is enabled for this project but the killed research
  subagent never produced a Validation Architecture section. The planner
  must produce one (which test layer validates each behavior change,
  which observable proves it, at least one known-answer case) as part of
  its own output -- it is not available pre-made this time.

### House style sample
- `.planning/phases/46-doctor-registry-vs-executed-tool-audit/46-PLAN.md`
  -- structural sample of a plan that survived an 11-round external
  review (frontmatter shape, read_first/acceptance_criteria per task,
  must_haves). Study structure only, do not copy content.

</canonical_refs>

<specifics>
## Specific Ideas

- P3 verbatim (from forge project memory MEMORY.md index, source file
  `feedback_forge_consumer_pain_points.md`): "adversarial 1800s timeout
  FAIL kills whole run (qodo+expert had good findings)". PM-corrected this
  session: findings are not lost (see Decisions); the real pain is
  representation, not data loss.

- Recommended macro-sequence for the phases around this one (informational
  only -- not this phase's job to implement or enforce):
  40 (mechanical, this phase) -> 44 (EVAL-ON-DUTY; its only hard
  dependency is Phase 43, already MERGED) -> 41 (Review focus; its own
  dispatch note says "Dep: 44 recommended") -> 40's semantic tail (now
  gated on 44's eval delta existing). Phase 42 (CLI key fast-fail +
  claim_type oracle) is independent -- its only dependency is Phase 43
  (MERGED) -- and can slot in at any point with no shared surface.
  STATE.md's literal "40 -> 41 -> 42" queue line predates this analysis
  and is stale relative to it. Flagging here; not correcting STATE.md now
  -- that update happens at this phase's wrap-up per forge CLAUDE.md's
  Phase Wrap-Up Protocol, not mid-planning.

</specifics>

<deferred>
## Deferred Ideas

- **7.2 convergence plateau + 7.3 prior-round memory** (the "semantic
  half"). Explicitly OUT of this phase's build scope. Locked policy
  decision for whenever this is planned (recorded here so it survives the
  wait): 2 consecutive rounds with zero NEWLY confirmed findings = stop
  and report; never silently escalate to PASS; never loop forever. Gated
  on Phase 44's eval harness existing first -- a before/after eval delta is
  required before shipping a convergence-semantics change (Fleet
  Evaluation Laws: pre-registration, known-answer validation). Likely
  shares prompt-construction surface with Phase 41 (design-intent
  injection) -- when the time comes, plan these two together rather than
  independently; do not silently assume they are unrelated.

- **Ledger schema coordination watch-item.** If the "new TerminalState
  enum member" route is chosen for partial-verdict representation (see
  Claude's Discretion above), check for collision with Phase 42's
  claim_type oracle, which "consumes the 43 ledger axis_claim field" per
  the dispatch schedule. No evidence of an actual conflict was found this
  session -- this is a watch-item for whoever plans Phase 42, not a
  confirmed problem.

</deferred>

---

*Phase: 40-honest-partial-convergence*
*Context gathered: 2026-07-15 via PM bounded recon (no discuss-phase session ran; research subagent died 3x on Fable-5 quota exhaustion, partial salvage folded in above where independently verified)*
