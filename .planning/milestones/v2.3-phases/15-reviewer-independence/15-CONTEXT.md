# Phase 15: Reviewer Independence - Context

**Gathered:** 2026-06-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Enforce reviewer-not-implementer structural independence on both outlet legs
(A and C), so that every review pass is provably context-isolated from the
implementation session. Phase 15 builds ON Phase 14's receipt/verify gate --
it does not replace or bypass it. All independence mechanisms feed the same
Phase 14 gate.

Scope extension (honest, visible): a cross-repo conventions-seed resolver
(D12 + SPEC section in the discuss-input brief) extends SHRK-02's original
charter. It fills CLAUDE.md's "What Forge Is Missing: Cross-repo impact" gap
and the empty Conventions section. The user accepted folding it in, split
across two execution waves (15a / 15b) to bound Phase 15 size.

</domain>

<decisions>
## Implementation Decisions

### Spine -- invariants Phase 15 must honor

Two legs, one gate, human on top.

- **Two legs kept (D1).** Outlet A (subprocess) and Outlet C (subagent) are
  both kept. Phase 15 does NOT pick one. Independence is one contract
  realized isomorphically on both legs.
- **One gate (D8).** Every Phase 15 mechanism feeds the same Phase 14
  receipt/verify gate. No new side-channel verify.
- **Human at the top (D6).** The gate proves coverage (every hunk was looked
  at), never judgment (it was thought about). The human is the irreducible
  final reviewer. Phase 15 shrinks what the human must check; it never
  removes them.

### Architecture -- orchestration vs judgment (D2)

Split orchestration from judgment. Orchestration belongs in deterministic
code (engine / per-pass driver), never in the reviewing model. Judgment
belongs in fresh, independent, per-pass model calls. Both observed failures
came from making one agent orchestrate AND judge: the whole-flow reviewer
shirks; the per-phase cold agent cannot orchestrate and runs amok.

### Independence definition (D3, D10)

- **D3.** Independence = the reviewer sees only the diff + criteria. It never
  receives the implementer's conversation or rationalizations.
- **D10.** Independence = context-separation (fresh llm_invoke, no shared
  session with the implementer). Model-separation (reviewer on a different
  model/backend than the implementer) is a CONFIG OPTION, not a mandate.

### Failure preference (D4)

Prefer the failure the gate can catch. A whole-flow independent reviewer that
rubber-stamps leaves receipts with missing/mismatched excerpts -> hardened
verify FAILS -> caught. A cold per-phase agent that runs amok produces
confidently wrong output the gate cannot see. Choose the catchable horn.

### Capability floor (D5)

The reviewer must be capable enough to actually judge forge passes.
Independence from an incompetent reviewer is worthless. Both independence
and capability are required.

### Outlet A already independent (D7)

Outlet A is already independent by construction: factories.build_l1_provider
loops the passes in Python, each pass a fresh llm_invoke seeing only role +
diff, no shared session. Phase 15 stands on this.

### C-leg mechanism (D9)

C-leg independence = a fresh llm_invoke per pass (diff + criteria + role, no
shared session) dispatched through Outlet C's spawn_fn hook -- the SAME
mechanism Outlet A uses via factories.build_l1_provider. A and C CONVERGE on
the independence axis. REJECTED: cold-Agent-per-pass dispatch (known 65K
truncation / hang failure -- see memories feedback_forge_review_inline_only,
feedback_subagent_hangs).

### Criteria payload (D11)

The "criteria" payload (D3 / SC2) =
  - diff
  - post-image content of the changed files (code as it now stands)
  - the pass role (qodo / expert / adversarial / ...)
  - the dimension definitions for that pass
  - a conventions-digest SLOT (see D12 + SPEC)

D3 unlock: independence forbids the implementer's RATIONALE, not codebase
FACTS. A code-derived conventions digest is D3-compatible IF derived
independently of the implementer. The slot is empty when no resolver source
is configured -- option1 still stands on its own.

### Cross-repo conventions-seed resolver (D12)

The conventions digest is produced by a PLUGGABLE conventions-seed resolver,
NOT hardcoded to CLAUDE.md. Two distinct relationship modes:

- **Mode A (DEPENDENCY):** this repo links/imports that one.
  Auto-discoverable from .gitmodules + package manifests.
- **Mode B (PEER / SIBLING):** two repos must stay consistent with NO code
  dependency. Discoverable ONLY from curated agent-context files. The OVS
  set->mod case is Mode B; dependency discovery alone MISSES it. Therefore
  the curated resolver is NOT optional.

Preferred curated source = AGENTS.md (2026 cross-tool standard; forge is
tool-agnostic).

### Human backstop deliverable (G3 -> D13)

Phase 15 produces an explicit, executable one-round human hot verify step
with a checklist. Embedded in the review flow AFTER the independent reviewer
completes. The checklist specifies what the human should examine -- focused
by the gate's coverage proof so the human reviews judgment, not coverage.
NOT implicit expectation; NOT deferred.

### Test-assertion review independence (G5 -> D14)

SC4 ("test review != impl agent") enforced via a separate gate step BEFORE
R1 test gate. This is NOT the 4th pass in the three-cycle static review --
it is a standalone spawn with its own fresh llm_invoke (diff + test files +
role = test-assertion reviewer). The reviewer is structurally guaranteed
to never be the impl/test author (same context-isolation as D9/D10).

### Pass parity exclusion (G6 -> D15)

Engine/skill pass count alignment is OUT OF SCOPE for Phase 15. Phase 15 =
independence, not pass richness. Recorded as future work.

### Execution split (O1 -> D16)

Phase 15 execution splits into two waves:

- **15a (core):** digest SLOT + independence contract + same-repo digest
  (extract current-repo naming patterns). Earned by the general convention-
  blindness of an isolated reviewer.
- **15b (build):** cross-repo resolver + sibling extraction (Stage 1-2) +
  caching. Earned by OVS set->mod -- a real cross-repo naming-convention
  class an isolated outlet reviewer cannot catch.

### Extraction recipe cut (O2 -> D17)

v1 does NOT attempt a universal extractor. The custom mapping
(.code-forge/conventions.yaml) carries per-sibling extraction recipes.
Auto-discovery (sources 2-4) handles "which repos"; extraction DEFAULTS to
a generic pass (public names + naming patterns). The user's real case (OVS)
works via custom mapping; zero-config repos get best-effort.

## SPEC -- Cross-Repo Conventions-Seed Resolver

### Stage 1 -- Source resolver (decides "which repos + what to extract")

Prioritized union (higher priority overrides lower on conflict):

1. **Custom mapping (HIGHEST).** .code-forge/conventions.yaml where the user
   declares {sibling repo path/URL, extraction target} + extraction recipe.
2. **AGENTS.md (PREFERRED CURATED).** Parse for declared related/sibling
   repos and stated conventions.
3. **Other agent-context files (FALLBACK).** CLAUDE.md, .cursor/rules/*.mdc,
   .cursorrules, .github/copilot-instructions.md, GEMINI.md, .windsurfrules.
4. **Dependency auto-discovery (SELF-FORMING, Mode A).** .gitmodules +
   package manifests (package.json, pyproject.toml, go.mod, Cargo.toml).

### Stage 2 -- Convention / vocabulary extraction

For each resolved {repo, target}:
- Deterministic where possible: public command/symbol names, naming patterns,
  exported API names.
- Optional independent AI summarization pass (fresh, no implementer session).
- Output: compact conventions digest of FACTS, not raw source.

### Stage 3 -- Inject into criteria payload

The digest fills the D11 conventions-digest slot.

### Independence preservation

- Digest derived from CODE, NOT from implementer session -> satisfies D3.
- Derivation runs INDEPENDENTLY of the implementer -> no blind-spot
  inheritance.
- Rule: build digest by reading AUTHORITATIVE sibling code, never by asking
  the implementer what the conventions are.

### Caching / freshness

Cache digest keyed on sibling repo's commit hash; refresh only when stale.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Independence mechanism (Outlet A)
- `src/code_forge/factories.py` -- build_l1_provider: per-pass fresh
  llm_invoke loop; the A-leg independence reference implementation
- `src/code_forge/machine.py` -- StateMachine + l1_provider 4-tuple
  interface; cycle counting + receipt production

### Independence mechanism (Outlet C)
- `src/code_forge/outlet_c.py` -- Phase 14 Outlet C orchestrator; accepts
  spawn_fn parameter (:38) and calls it per-pass (:54)
- `src/code_forge/cli.py:783-784` -- _subagent_spawn: the actual
  NotImplementedError ceiling (Phase 15 must replace with fresh llm_invoke)
- `src/code_forge/reviewer_json.py` -- validate_reviewer_json +
  _collect_excerpts + _json_to_state_findings (shared A/C)

### Verify gate (Phase 14)
- `src/code_forge/verify.py` -- hardened verify: STEP A/B/C + check 6/7;
  all Phase 15 mechanisms must feed this gate
- `src/code_forge/receipt.py` -- receipt write/read + _build_excerpts

### CLI dispatch
- `src/code_forge/cli.py` -- outlet routing (~line 690 subagent block,
  ~line 561 hardened verify flip)

### SKILL.md (Outlet B/C protocol)
- `src/code_forge/skills/code-forge/SKILL.md` -- Outlet C dispatch rules,
  JSON schema, severity-gated state machine protocol

### Requirements
- `.planning/REQUIREMENTS.md` -- SHRK-02 definition
- `.planning/ROADMAP.md` -- Phase 15 SC1-SC4

### Discuss-phase input brief
- `/tmp/draft_20260607_p15_discuss_input_v2.md` -- full design rationale
  for D1-D12, SPEC, scope-challenge (282 lines). Planner should read for
  WHY behind each lock.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `factories.build_l1_provider()`: A-leg per-pass loop is the reference for
  D9 convergence. C-leg spawn_fn must produce the same 4-tuple.
- `reviewer_json.validate_reviewer_json()`: shared JSON validation for both
  A and C legs. Reuse for independence contract enforcement.
- `machine.StateMachine`: cycle counting + receipt production. Both legs
  reuse this; independence is upstream of the machine.

### Established Patterns
- l1_provider 4-tuple: `(findings, excerpts, Usage, duration_s)` -- the
  standard channel from reviewer to StateMachine.
- fail-closed: malformed JSON / missing fields -> CONFIRMED finding -> dirty
  round. Phase 15 independence violations should follow this pattern.

### Integration Points
- `cli.py:783-784` (_subagent_spawn) -- NotImplementedError ceiling from
  Phase 14. Phase 15 must replace with fresh llm_invoke per pass.
  outlet_c.py:38 declares spawn_fn param; cli.py:793 passes it in.
- `cli.py` outlet dispatch -- where test-assertion review gate (D14) plugs
  in, before R1 test gate.
- `SKILL.md` -- human backstop step (D13) adds a section here.

</code_context>

<specifics>
## Specific Ideas

- OVS set->mod case is the earned failure for cross-repo conventions: kernel
  enum OVS_FLOW_CMD_SET vs userspace dpctl verb "mod". The digest must read
  the sibling's actual command struct, not the implementer's choice.
- "Judge-and-athlete" is the anti-pattern: one agent both implements and
  reviews. Phase 15 makes this structurally impossible by construction.
- Opus 4.6 faked 9-pass -- caught by hands-on inspection, not self-report.
  This is the earned failure for D13 (human backstop as explicit deliverable).

</specifics>

<deferred>
## Deferred Ideas

- **Engine/skill pass count alignment (G6/D15):** Phase 15 is independence,
  not pass richness. Future phase.
- **Cross-repo IMPACT judgment:** caller/callee breakage across repos is a
  different axis from naming/idiom. Not Phase 15.
- **Content fabrication defense:** reviewer lies about findings (writes
  findings: []). Different concern from process shirking. Future phase,
  requires adversarial verify or multi-reviewer voting.
- **Feedback learning:** forge does not learn from dismissed findings.
  Future capability.
- **Universal cross-repo extractor:** v1 uses custom-mapping recipes (O2).
  Universal extractor deferred.
- **Agentic-as-gate:** agent reviews feed the pipeline; the gate stays
  deterministic. v2.4 thesis boundary.

</deferred>

---

*Phase: 15-Reviewer-Independence*
*Context gathered: 2026-06-07*
