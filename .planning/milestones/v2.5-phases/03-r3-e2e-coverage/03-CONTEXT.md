# Phase 3: R3 (e2e coverage) - Context

**Gathered:** 2026-05-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver cross-component e2e/integration coverage detection as the third and
final leg of forge's verification grounding thesis (R1 commit gate + R2
mutation + R3 e2e). Two layers:

- **Layer 1 (heuristic, no config):** diff touches >=2 source groups AND
  modifies a function signature/return type -> non-blocking checklist finding.
- **Layer 2 (explicit, opt-in):** `.forge/components.yaml` defines components,
  shared hubs, data paths, and e2e artifact patterns. Co-occurrence trigger:
  diff hits hub + dependent (or two peer components on a data path) and no
  satisfying e2e artifact exists -> P2 finding.

NOT auto data-flow analysis. SPEC: "spans >=2 components on a data path is
not reliably auto-detectable (no call graph; shell/C have none)."

Honest: Layer 1 is best-effort. Layer 2 is enforceable only on opt-in. R3
checks artifact PRESENCE, not proof the artifact exercises the changed code.

</domain>

<decisions>
## Implementation Decisions

### Pipeline Integration
- **D-01:** R3 integrates as an independent module (`e2e_check.py`), following
  the MUTANT (L2) precedent in machine.py. Code evidence:
  - `state.py:48` source Literal extends to include `"E2E_CHECK"` (same pattern
    as adding `"MUTANT"` in Phase 2).
  - `_run_l2_phase()` (machine.py:483) directly appends findings without going
    through autofix or falsify -- R3 follows the same shape.
  - E2E_CHECK findings MUST NOT enter `_apply_autofix_loop_to()` (L0-only) or
    falsifier (L1-only). A coverage-gap finding is not a code defect; autofix
    is semantically wrong for it.
  - Reuse `self._source_files()` and the diff module for diff-scoped files.
    Do not re-shell `git diff`.
  - Layer 1 findings = non-blocking advisory, MUST NOT flip verdict.
  - Layer 2 findings disposition mapping (LOCKED): SPEC's "P2 finding" maps
    to disposition=UNCERTAIN in forge's model. Rationale: CONFIRMED would
    block convergence permanently (a missing e2e test cannot be resolved
    within a review cycle -- no autofix, no falsifier). DISMISSED would make
    Layer 2 a paper tiger. UNCERTAIN enters HOLD for human triage, which is
    the correct UX: "you have a cross-component change with no e2e test;
    decide whether to add one or mark intentionally absent."
  - Layer 1 findings disposition: DISMISSED (advisory, never blocks).
  - The e2e_check runner itself assigns disposition before returning findings
    (Layer 1 -> DISMISSED, Layer 2 -> UNCERTAIN). Findings skip the falsifier
    entirely -- same contract as MUTANT findings which arrive pre-dispositioned.
  - Bug-inject teeth required: both sides (fires when expected, does NOT fire
    when not expected).

### Heuristic Detection (Layer 1)
- **D-02a:** Detect function signature/return-type changes via:
  **(added-lines regex) UNION (section_header matching a def-pattern)**.
  - Added-lines regex: scan hunk added lines for language-specific function
    signature patterns. Python/Go use `def`/`func` + `->` return-type. C-style
    places the return type BEFORE the function name (e.g., `static int foo(void)`),
    so the regex shape differs: roughly `(static\s+)?(int|void|char|...)\s+\w+\s*\(`,
    no `->`. Covers new functions and single-line signature modifications.
    Ship Python+shell first; add other languages with their own regex shape
    when real consumers request (per pre-mortem #5).
  - Section_header: `unidiff.Hunk.section_header` contains the enclosing
    function context (verified: git emits it even under `-U0`). Catches
    multi-line signature interior edits where the only added line is a
    parameter (e.g., `+   b: str,` inside a multi-line `def foo(...)`)
    that matches neither `def` nor `->`.
  - Reliability note: section_header is git's enclosing-function detection.
    It works for forms git recognizes (Python `def`, C/Java braces, shell
    `name() {`). For flat shell scripts without function wrappers, or
    non-standard function syntax, `section_header` may be empty -- the
    added-lines regex arm is the fallback (catches new function definitions
    even when no enclosing context exists).
  - Both arms use the `unidiff` library (a forge dependency via diff.py, but
    e2e_check.py must import unidiff directly -- diff.py's API does not
    expose Hunk.section_header). No AST parser needed.
  - Pin `unidiff` in pyproject.toml; planner must include a unit test
    asserting `section_header` is present on a parsed hunk -- it is an
    undocumented instance attribute and a version bump could silently break
    the heuristic. (Chaos Response has the raw `@@` parsing fallback.)

- **D-02b:** "Source directory" grouping for Layer 1:
  - **Configurable**, default = first path segment.
  - When `components.yaml` exists, Layer 1 MUST derive grouping from the
    Layer 2 `component -> paths` map. Single source of truth for "what is a
    component." Do not maintain a divergent fixed rule alongside the explicit
    map.
  - Fixed N-level rules fail: "first two" over-splits hub-and-spoke projects
    (bonding + bonding/common = 2 groups = false positive in
    code/kernel/networking). "First one" under-groups src-layout projects
    (src/auth + src/api both collapse to src).
  - Non-blocking checklist that fires on intra-component changes trains users
    to ignore the tool (plan-forge F3 per-mention FP lesson).
  - Test directories (tests/, test/, spec/) are excluded from source grouping
    by default. A change touching src/foo.py + tests/test_foo.py is NOT
    cross-component. When components.yaml exists, "what is a source path" is
    whatever the `component -> paths` map declares -- test dirs are simply not
    listed as component paths, so the exclusion is implicit (no separate
    exclude field). The default-mode exclusion list is a fixed builtin, not a
    Phase 3 configurable knob.

### components.yaml Schema (Layer 2)
- **D-03:** Schema design:

  ```yaml
  # .forge/components.yaml
  version: 1

  components:
    bonding:
      paths: ["bonding/**"]
      depends_on: [common]    # explicit hub-spoke edge (manual-authoring path)
    common:
      paths: ["common/**"]
      shared: true            # optional hint (depends_on is source of truth)

  # Peer data paths (api<->db style projects)
  data_paths:
    - [api, db]

  # What counts as satisfying e2e/integration artifact
  e2e_patterns:
    - "*/integration/**"
    - "tests/e2e/**"
    - "test_*integration*"
  # Intentionally absent e2e (escape hatch for Layer 2)
  e2e_absent_ok:
    - component: leaf_types
      reason: "data-only module, no integration path"
  ```

  **Trigger semantics (co-occurrence, NOT blast-radius):**
  - diff hits hub + dependent N -> N missing e2e -> P2 finding (specific pair).
    "N missing e2e" is PER-PAIR: an artifact satisfies the (hub, N) pair only
    if it matches e2e_patterns AND lives within N's component paths. If hub +
    N1 + N2 are all touched and only N1 has a matching artifact, P2 fires for
    (hub, N2) only -- never one blanket P2 for the hub. Honest ceiling
    (unchanged): N's integration artifact PRESENCE is checked, not that it
    exercises the hub change.
  - diff hits only hub (hub-only change) -> Layer 1 non-blocking nudge
    ("common is a hub for M subsystems, blast radius large, verify affected
    ones"). NOT a P2 requiring all dependents to have e2e. Separating
    blast-radius advisory from P2 enforcement is critical to avoid noise.
  - diff hits two peer components on a data_path -> missing e2e -> P2 finding.
    data_paths pairs are SYMMETRIC: P2 fires only when BOTH endpoints are in
    the touched set; touching only one endpoint does NOT fire (per-pair e2e
    scoping above applies to either endpoint's component paths).
  - Dependents determined by: (1) explicit `depends_on` in components.yaml
    -- the Phase 3 manual-authoring path, deterministic and grep-free; OR
    (2) source-graph crawler -- the deferred auto-detection enhancement
    (Adjustment 2). Phase 3 ships path (1); path (2) is a future tool that
    can auto-populate the same `depends_on` field. NOT "all other components"
    -- "all" wastes the source-graph and generates ~74-subsystem false
    positives on every common/ change.

  **Hub-and-spoke support (Adjustment 1 from R3-CONSUMER-INPUT.md):**
  - The original SPEC's `data_paths -> component pairs` (symmetric) cannot
    express one-to-many shared dependency. `depends_on` edges + computed hub
    detection handle the hub-and-spoke shape.
  - `shared: true` is an OPTIONAL readability marker. Hub status is COMPUTED
    by reverse-scanning `depends_on`: a component that appears in another
    component's `depends_on` list is a hub. `depends_on` is the single source
    of truth; if `shared` and the reverse-scan disagree, the reverse-scan
    wins. This avoids the stale-flag liability (someone sets `shared: false`
    but leaves the `depends_on` references intact).

  **Schema validation (load-time, before any detection):**
  - Every `depends_on` target MUST exist in `components` keys. An undefined
    reference (typo, e.g. `[cmmon]`) is a load-time error emitting an
    E2E_CHECK finding "components.yaml: depends_on references undefined
    component X" -- NOT a silent skip. A silent skip means the hub-spoke edge
    the author intended is never checked and no P2 ever fires.
  - Reject self-reference (`A.depends_on: [A]`) and cycles (`A->B->A`).
  - Phase 3 follows ONE-LEVEL edges only: transitive chains (`bonding ->
    common -> utils`) are NOT traversed; only directly-declared pairs trigger.
    State this limit so authors do not expect transitive coverage.
  - `e2e_absent_ok[].component` gets the same existence check.
  - `depends_on` and `data_paths` are distinct relations; if the same pair is
    expressed in both, dedup so only one P2 fires for that pair.

  **E2e artifact pattern configurable (Adjustment 3):**
  - Default: `tests/e2e/**` + `test_*integration*`. The recursive `**` is
    intentional (matches nested integration tests) and slightly broader than
    SPEC.md L184's literal `tests/e2e/*` (non-recursive) -- noted as an
    intentional deviation, not a bug. Matching uses pathlib.glob (NOT
    registry.py's fnmatch, which does not support `**`).
  - Projects declare their convention (e.g., `*/integration/**` for
    code/kernel/networking).

  **Auto-detection (Adjustment 2, revised):**
  - Source-dependency graph IS extractable for shell projects (`. include.sh`
    and `source` directives are greppable, deterministic edges).
  - Honest: three source-line forms exist --
    (a) relative (`../../common/include.sh`, depth varies),
    (b) absolute deployment path (`/mnt/tests/.../include.sh`, not repo path),
    (c) variable interpolation (`${CASE_PATH}/../include.sh`, statically
    unresolvable). Grep handles (a) and (b); (c) is best-effort.
  - Correct framing: "mostly deterministic + variable-interpolation best-effort
    tail." Auto-detect draft + human ratification. Do NOT claim fully
    deterministic.

### R4 Docs Update
- **D-04:** Phase 3 includes R4 doc maintenance:
  - R2 PLANNED->LIVE: early (Phase 2 already merged to main, this is
    outstanding debt from Phase 1 D-03).
  - R3 PLANNED->LIVE: after Phase 3 code is merged.
  - Honest assessment (CLAUDE.md:307): "until R2 and R3 land" -> update
    once both land.
  - Scope: CLAUDE.md sections "What Forge Covers" / "What Forge Is Missing" /
    "Honest assessment" only. Separate docs commit.

### Layer 1/Layer 2 Deduplication
- When Layer 2 fires on a co-occurrence (hub+dependent or peer data_path),
  suppress the Layer 1 checklist finding for the same file pair. Layer 2 is
  strictly stronger (opt-in, enforceable) -- Layer 1 adds no signal when
  Layer 2 already covers the same change.

### Claude's Discretion
- Internal module layout for e2e_check.py
- E2E_CHECK finding fingerprint scheme (constraint: prefix with `e2e-` to
  avoid collision with L0/L1 fingerprints in `_merge_findings` at
  machine.py:661, which silently overwrites on fingerprint equality)
- Error message wording for Layer 1 checklist / Layer 2 P2
- Test file organization for e2e_check tests
- Whether Layer 1 and Layer 2 logic live in one module or two
- Exact regex patterns per language (Python/shell/C/Go function definitions)
- E2e pattern matching strategy (fnmatch vs pathlib.glob -- registry.py uses
  fnmatch which does not support `**`; e2e_patterns like `*/integration/**`
  need recursive glob semantics)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Spec and Design
- `.planning/milestones/v2.1-dynamic-gate/SPEC.md` -- R3 section (lines
  175-186): two-layer design, heuristic + opt-in, honest limitation
- `.planning/milestones/v2.1-dynamic-gate/ROADMAP.md` -- Phase 3 exit
  criteria (6 items): Step 0 clean, heuristic, components.yaml, suite green,
  bug-inject teeth, three-cycle review
- `.planning/milestones/v2.1-dynamic-gate/R3-CONSUMER-INPUT.md` -- real
  Layer 2 consumer analysis (code/kernel/networking): hub-and-spoke shape,
  source-graph auto-detection, e2e artifact patterns, co-occurrence trigger
  semantics, Layer 1 grouping FP evidence. MUST read before designing
  components.yaml schema or trigger logic.

### State Machine (must read before modifying)
- `src/forge/machine.py` -- StateMachine class, _run_l2_phase() pattern
  (Phase 2 precedent for R3 integration), _execute_round() ordering,
  _apply_autofix_loop_to() exclusion pattern, _source_files()
- `src/forge/state.py` -- StateFinding.source Literal["L0", "L1", "MUTANT"]
  (line 48, extend to "E2E_CHECK")
- `src/forge/disposition.py` -- Disposition model, P2 severity semantics

### Diff Infrastructure (reuse, do not rewrite)
- `src/forge/diff.py` -- extract_changed_lines(), get_changed_files() using
  unidiff; Hunk.section_header for function context
- `src/forge/git.py` -- single owner of git diff calls; working_tree_diff(),
  cached_diff(), git_diff(), validate_diff_spec()

### Phase 2 Code (L2 runner precedent)
- `src/forge/mutation.py` -- L2 runner implementation pattern
- `src/forge/factories.py` -- build_l2_runner factory pattern (reference for
  building e2e_check runner)

### Phase 1 Code (gate patterns)
- `src/forge/gate_check.py` -- match_source_patterns() (reference for file
  pattern matching logic)

### Real Consumer (Layer 2 validation corpus)
- `~/code/kernel/networking/` -- ~106 top-level subsystem directories, common/
  hub sourced by ~74 subsystems, 19 subsystem-level common/ hubs,
  5 subsystems with integration/ directories (counts approximate, consistent
  with R3-CONSUMER-INPUT.md). First real components.yaml target.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `diff.py`: unidiff PatchSet parsing (e2e_check.py reuses this idiom). Note:
  Hunk.section_header is NOT exposed through diff.py's API -- e2e_check.py
  imports unidiff directly to access it (a new use of the unidiff library,
  not a reuse of diff.py)
- `git.py`: single git diff owner -- R3 uses _source_files() pipeline, does
  NOT call git directly
- `machine.py _run_l2_phase()`: L2 runner integration pattern -- R3 follows
  same shape (independent phase, direct append, skip autofix/falsify)
- `gate_check.py match_source_patterns()`: glob pattern matching logic --
  reference for e2e_patterns matching
- `registry.py file_patterns`: existing glob pattern infrastructure

### Established Patterns
- Module-per-concern: mutation.py, gate_check.py -- e2e_check.py follows
- DI injection: l0_runner, l1_provider, l2_runner as injectable callables --
  e2e_check runner follows same factory pattern
- StateFinding source field: Literal type extended per phase (L0/L1 -> MUTANT
  -> E2E_CHECK)
- MUTANT autofix skip: `if f.source == "MUTANT": continue` before autofix --
  E2E_CHECK follows same exclusion pattern

### Integration Points
- state.py StateFinding.source: extend Literal to include "E2E_CHECK"
- machine.py: add e2e_check phase (after L2 or parallel, planner decides)
- factories.py: build_e2e_checker factory
- CLAUDE.md: R2 PLANNED->LIVE + R3 PLANNED->LIVE (D-04)
- .forge/components.yaml: new config file (Layer 2 opt-in)

</code_context>

<specifics>
## Specific Ideas

- Use code/kernel/networking as the Layer 2 validation corpus and first real
  components.yaml (R3-CONSUMER-INPUT.md)
- Bug-inject teeth (Phase 2 lesson): cross-component change + no e2e -> fires;
  add e2e artifact -> clears. Both sides dogfooded, not just one
- Plan output goes to aicc for deepseek/mimo/kimi cross-model review before
  execution (user requirement)
- Phase 2 runner drift follow-up: if mutation.py is touched during Phase 3,
  add runner=baseline_cmd to setup.cfg (tracked in memory
  project_forge_p2_runner_drift.md)

</specifics>

<deferred>
## Deferred Ideas

- R5 test layering (threshold-triggered real-dependency regression) -- deferred
  to forge-code phase per ROADMAP
- Function-level diff-scoping for mutation (AST-located changed nodes) --
  Phase 2 deferred, still deferred
- Multi-language mutation runners (cargo-mutants, go-mutesting) -- Phase 2
  deferred, still deferred
- Cross-repo impact detection -- forge stated gap, not R3 scope
- Source-graph auto-detection tooling (AI-assisted draft + human ratify) --
  valuable but out of Phase 3 code scope; Phase 3 delivers the schema and
  manual authoring; auto-detection is a future enhancement

</deferred>

## Reference Class

Three comparable projects that added cross-component coverage checking,
with plan-vs-actual ratios:

- **Chromium CQ Dry Run / Mega CQ tiering (2018-present):** diff-based CI
  tier selection (which builders to run based on affected files). Schema
  equivalent: OWNERS files + directory-based component mapping. Integration:
  ~1.5x planned (estimated, no public number; the directory-to-builder mapping was straightforward; the
  long tail was edge cases in shared-header impact radius). Lesson: shared
  dependencies (headers included by many targets) are the hard case, not
  leaf components.

- **Forge Phase 2 (internal reference):** L2 mutation runner integration.
  Four plans, three implementation attempts before dogfood passed. Ratio
  2.0x planned. Main overrun: mock blind spot (3 false passes), not
  implementation difficulty. Lesson: real-backend dogfood is non-negotiable;
  mock-only testing structurally cannot catch integration contract mismatches.

- **SonarQube Quality Gate baseline model (2015-present):** marks current
  state, tracks only new problems. Closest to R3 Layer 2's "presence check."
  Implementation: mostly schema + config; detection logic is simpler than
  mutation. Ratio ~1.2x planned (estimated, no public number) when schema is well-defined. Lesson: schema
  design is the hard part; detection is mechanical once the schema is right.

## Risks

### Known Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Layer 1 heuristic too noisy (fires on intra-component changes) | Medium | High | Configurable grouping; components.yaml overrides; plan-forge F3 FP lesson documented |
| components.yaml schema does not fit real projects | Medium | High | Validated against code/kernel/networking (~106 subsystems, hub-and-spoke); R3-CONSUMER-INPUT.md documents adjustments |
| Hub-only changes generate blast-radius P2 noise | High (if not designed correctly) | High | Co-occurrence trigger: hub+dependent=P2, hub-only=L1 nudge. Explicitly separated in D-03 |
| section_header regex misses exotic function signatures | Low | Low | Best-effort heuristic (Layer 1 is non-blocking); regex table extensible per language |
| components.yaml drifts as project structure changes | Medium | Medium | Components reference nonexistent paths -> glob returns empty -> Layer 2 silently misses co-occurrence. Add a lint/validate subcommand to detect stale component definitions |

### Gray Rhinos

| Risk | Denial Reason | Counter |
|------|--------------|---------|
| Layer 1 best-effort heuristic adds no real value (users ignore non-blocking checklists) | "It is non-blocking, so false positives are acceptable" | plan-forge F3 per-mention FP lesson: users trained to ignore = no value. Configurable grouping + components.yaml reuse reduces FPs. If Layer 1 still generates >30% FP, add a project-level disable switch |
| Layer 2 schema design takes longer than implementation | "Schema is just YAML, how hard can it be" | Phase 2 overran on the subprocess contract (3 attempts), not on code complexity. Schema design for hub-and-spoke + peer-pair + e2e-patterns is the Phase 3 equivalent of that contract. Budget schema design as the critical-path item |

### Black Swans

| Risk | Survival Plan |
|------|--------------|
| unidiff library drops section_header attribute | section_header is a git diff convention, not unidiff-specific; fall back to raw @@ parsing |
| Projects with no clear component boundaries | Layer 2 is opt-in; Layer 1 best-effort; R3 degrades gracefully to no-op |

## Pre-mortem

1. **Layer 1 fires on every commit in a single-package project.** Early warning:
   forge's own repo (single src/forge/ package) triggers checklist on every
   cross-file change. Counter: single-component projects naturally do not hit
   the ">=2 source groups" threshold unless tests/ is counted as a component.
   Exclude test directories from source grouping.

2. **components.yaml too painful to author for large repos.** Early warning:
   code/kernel/networking has ~106 subsystems; manual authoring takes hours.
   Counter: document auto-detection workflow (grep source chains + human
   ratify). Phase 3 ships manual authoring; auto-detection is deferred but
   the schema supports it.

3. **Co-occurrence trigger misses hub-only changes that break dependents.**
   Early warning: common/include.sh change breaks bonding tests, but no P2
   fires because diff only touched common. Counter: by design -- hub-only =
   Layer 1 advisory nudge, not P2. The nudge says "blast radius large, verify
   affected." This is the honest ceiling: R3 checks artifact presence, not
   coverage proof.

4. **E2E_CHECK findings mapped to CONFIRMED block convergence permanently.**
   Early warning: review loops never reach fixpoint because a missing e2e
   test is not something autofix or the developer can resolve within the
   review cycle. Counter: Layer 1 = DISMISSED (advisory, never blocks);
   Layer 2 = UNCERTAIN (LOCKED in D-01, NOT CONFIRMED -- enters HOLD for
   human triage, never blocks convergence permanently). The escape hatch for
   an intentionally-absent test is e2e_absent_ok in components.yaml.

5. **Regex table maintenance burden grows with language count.** Early warning:
   adding Go/Rust/C patterns to the regex table requires per-language
   verification; a wrong pattern silently misses signature changes. Counter:
   ship with Python + shell (the two languages forge and kernel/networking
   use). Add other languages only when a real consumer requests them, with
   test cases per language.

## Chaos Response

Stressor scenarios for the e2e coverage detection, classified by outcome:

| Stressor | Response | Classification |
|---------|----------|----------------|
| git diff fails or returns empty | _source_files() returns empty; zero files to analyze; R3 emits no findings; review continues | survive |
| components.yaml has YAML syntax error | yaml.safe_load raises; emit E2E_CHECK finding "components.yaml parse error"; do not crash | survive |
| components.yaml references nonexistent paths | Glob match returns empty; component has zero files; no co-occurrence possible; pass through silently | survive |
| unidiff cannot parse diff (binary files, encoding) | diff.py already returns empty dict for unparseable diff; R3 gets no hunks; no signature detection; Layer 1 does not fire | survive |
| Hub component has 100+ dependents | Co-occurrence only triggers on touched dependents; hub-only = Layer 1 nudge; no O(N) P2 explosion | survive |
| All changed files are test files | Source grouping excludes test directories; zero source groups; Layer 1 does not fire | benefit |
| components.yaml missing but Layer 2 finding expected | Layer 2 is opt-in; no components.yaml = no Layer 2 findings; Layer 1 still operates | survive |
| depends_on references an undefined component (typo) | load-time validation rejects; emit E2E_CHECK "depends_on references undefined component"; do NOT silently skip (silent skip = intended hub-spoke edge never checked) | survive |
| depends_on cycle or self-reference | load-time validation rejects; one-level edges only, cycles never traversed | survive |

## Scope Challenge

**Q1: Does this need to exist?**
Yes. forge's own CLAUDE.md:307 says "until R2 and R3 land, forge is partially
implementing its own thesis." R2 landed (Phase 2). R3 is the last leg. Without
it, forge ships "verification grounding > prompt-only self-claim" while its
own review pipeline has no cross-component coverage signal.

**Q2: Three real consumers**
- forge itself: cross-module changes (machine.py + state.py + mutation.py) --
  though forge is a single-package project, Layer 2 would catch changes
  spanning multiple modules with no integration test.
- code/kernel/networking: ~106-subsystem hub-and-spoke structure with common/
  shared library. First real Layer 2 consumer (R3-CONSUMER-INPUT.md).
- Any project running forge review with a components.yaml: gets cross-component
  coverage checking on their diffs.

**Q3: Do-nothing cost**
forge continues to ship the verification grounding thesis with only 2 of 3
legs. CLAUDE.md "honest assessment" remains "partially implementing its own
thesis." The self-contradiction is explicitly documented and visible to every
user who reads the docs.

**Q4: Barbell vs middle ground**
The barbell options are: (A) full automatic data-flow analysis across all
languages (years of work, call-graph extraction for shell/C is unsolved), (B)
no cross-component coverage signal (status quo, documented gap). R3 is NOT
the middle ground -- it IS the minimal viable design: Layer 1 best-effort
heuristic (cheap, non-blocking) + Layer 2 opt-in explicit mapping (enforceable
only when the user declares components). Function-level data-flow analysis
and cross-repo impact are incremental enhancements, not compromises.

## External Voices

Primary sources on cross-component coverage and integration testing:

- Humble & Farley (2010). Continuous Delivery. Addison-Wesley. Foundational
  reference on delivery pipeline design. The principle that integration testing
  must span real component boundaries -- not just unit tests in isolation --
  directly motivates R3's two-layer design (heuristic checklist + opt-in
  components.yaml enforcement).

- Forge Phase 2 internal experience (2026-05-26): 639 mock tests + 9-pass
  static review missed 3 integration bugs caught only by dynamic verification
  (real-API smoke test). This is forge's own empirical evidence that unit-test
  coverage alone is insufficient for cross-component integration paths. The
  historical failure is internal, not academic -- but it is the specific
  incident that motivated R2 and R3. See memory
  feedback_real_api_smoke_catches_mock_blindspot.md.

- Chromium CQ system (2018-present). The Chromium project's Commit Queue
  uses OWNERS files + directory-based component mapping to trigger tiered CI
  builders (CQ Dry Run, CQ Submit, Mega CQ). Lesson: explicit component
  mapping scales; automatic inference does not for large projects. The
  OWNERS model is the real-world precedent for components.yaml.

- A legitimate objection exists: R3 checks artifact PRESENCE, not coverage
  PROOF. An integration test file can exist but not exercise the changed code
  path. This ceiling is acknowledged in the Residual Limit section of
  R3-CONSUMER-INPUT.md and is unchanged by the design. Do not oversell Layer
  2 enforcement as coverage proof.

## References

Cross-plan reference audit:

- **Phase 1** (referenced in D-04, Specific Ideas, code_context):
  `.planning/phases/01-r1-commit-gate-r4-docs/` -- Phase 1 delivered
  gate_check.py, install_hooks.py, R4 gate-philosophy docs. Phase 3 references
  Phase 1's D-03 (R4 LIVE/PLANNED distinction) for the docs update plan.
  Phase 3 does not modify Phase 1 code.

- **Phase 2** (referenced in Reference Class, Specific Ideas, D-01):
  `.planning/phases/02-r2-mutation-pipeline-step/` -- Phase 2 delivered
  mutation.py, l2_runner wiring, MUTANT source type. Phase 3's D-01 follows
  Phase 2's L2 integration pattern as the direct precedent. Runner drift
  follow-up tracked in memory.

- **R3-CONSUMER-INPUT.md** (referenced in D-02b, D-03, canonical_refs):
  `.planning/milestones/v2.1-dynamic-gate/R3-CONSUMER-INPUT.md` -- consumer
  analysis for code/kernel/networking. 4 adjustments (hub-and-spoke shape,
  auto-detection feasibility, e2e artifact configurability, Layer 1 grouping
  FP evidence) plus heuristic-detection note (D-02a section_header fix).

---

*Phase: 03-r3-e2e-coverage*
*Context gathered: 2026-05-26*
