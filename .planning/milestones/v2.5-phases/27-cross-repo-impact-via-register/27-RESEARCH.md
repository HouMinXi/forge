# Phase 27: Cross-Repo Impact via register -- Research

**Status:** RESEARCH COMPLETE
**Focus:** Evidence deficiency audit, not domain discovery
**Date:** 2026-06-22
**Evidence base:** MODEL-impact-query.md rev 3, DESIGN-rescoped.md, coverage-cards.md,
REVIEW-FINDINGS.md (R1), REVIEW-FINDINGS-R2.md (R2), scale-test.md, spike/,
spike-direction/, draft_20260622_phase27_core_plan_seed.md, advisory.py,
graph_triage.py, cross_repo.py lines 270-369.

---

## Evidence Audit

### Closed Findings (do not re-raise)

The following were raised in R1 or R2 and are confirmed closed by the evidence files:

- R-role role classification (R1: "free input"). Fixed: R-role classifier specified
  in MODEL sec 5 with degradation path to plain_function + "role uncertain".
- R1-rev undefined (R2: mm + mimo). Fixed: full contract in MODEL sec 5 and
  DESIGN-rescoped.md C2.
- CHA-vs-table "siblings excluded" promise (R2: mm + mimo + gm). Fixed: dual-path
  label, table no longer promises sibling exclusion; exclusion holds only on RTA path.
- Scope math "two of three" (R2: mm). Fixed: one of three; kernel-C demand-gated.
- Rust R-role bootstrap (R2: gm). Fixed: Rust fully FENCED; glue required first.
- role-SET merge unspecified (R2: mimo). Fixed: CORE compound rule in MODEL sec 4
  + merge in sec 8.
- sec7/sec8 keep-highest vs show-worst contradiction (R2: mimo). Fixed: per-edge
  keep-highest then per-path show-worst, both scoped correctly.
- depth-1 defined for only 2 roles (R2: mimo). Fixed: per-CORE-role depth-1 in
  MODEL sec 9.
- event/callback silent gap for Go/TS (R2: mimo). Fixed: FENCED + R-role emits
  UNRESOLVED.
- R3 rows vs do-less scope (R2: mimo + mm). Fixed: R3 marked FENCED, emits nothing.
- Sibling-index staleness (R2: mm). Fixed: sibling base_commit pin in MODEL sec 9.
- R0 O(1) / "FULL for any language" overclaims (R2: mm). Fixed: O(out-degree),
  EXACT-for-parsed-surface.
- default_method mechanism gap (R2: mm). Fixed: FENCED, no mechanism claimed.
- is_implementation join circularity (R1: deepseek). Refuted and upheld by R2.
- 33% E-direction figure ungrounded (R1: deepseek + minimax). Fixed: spike-direction/
  committed with toy-scale caveat.
- E-cross-proc R3 feasibility ungrounded (R1: deepseek + mimo + minimax). Fixed:
  R3 downgraded to PROPOSED/UNMEASURED.
- 533-vs-3 mislabeled under Shell card (R1: mimo + deepseek + minimax). Fixed:
  dedicated C ops-struct card.
- Go structural-interface coverage self-contradiction (R1: gemini). Fixed: FULL
  confirmed (scip-go emits Dog/Cat->Animal without `implements` keyword).

### Deficiencies Found

---

**DEF-01 [HIGH]: R-role classifier algorithm not concretely specified**

Location: MODEL sec 5 "R-role" + DESIGN-rescoped.md C2.

The contract states "infer role from the index symbol kind + the structural
relations present -- `is_implementation`, type hierarchy, occurrences." It does not
specify:
- Which SCIP `SymbolInformation.kind` values map to which roles
  (e.g., kind=Method + is_implementation present -> interface_method vs concrete_impl?).
- The decision tree for the compound case {plain_function + concrete_impl}: when
  does a method satisfy both simultaneously, and how is that detected from the index?
- What "occurrences" field is consulted (Definition vs Reference occurrence?) and
  what pattern distinguishes an interface declaration from a concrete implementation.
- How to distinguish a TS `override_method` from a plain `concrete_impl`.

An implementer reading only the spec must make multiple undirected judgment calls
here. The spike/ code may encode decisions, but the spec does not state them as
contracts.

Missing: decision table mapping (symbol_kind, is_implementation_present,
occurrence_role) -> CORE role. No test vectors with concrete SCIP field values.

---

**DEF-02 [HIGH]: RTA "review-repo-local" trigger condition not specified**

Location: MODEL sec 5 R1-rev, sec 10 topology. DESIGN-rescoped.md C2 R1-rev.

Both documents state the dual-path label (RTA-available -> EXACT-ish;
RTA-unavailable -> CHA fallback SOUND-OVERAPPROX) but do not specify:
- How the runner determines whether RTA is "available" for the current review repo.
  Is it: (a) the language is Go/TS? (b) the toolchain is present? (c) a coverage
  card entry exists? (d) a live LSP probe succeeds?
- Whether RTA is invoked via a subprocess call (to what binary?), via the existing
  graph.db, or via a SCIP query.
- The timeout budget for the RTA step specifically (MODEL sec 9 mentions "latency
  cap" only for transitive chaining, not for RTA itself).
- What constitutes a "receiver static/instantiated type" match -- the SCIP field
  used for receiver type attribution.

The spike-direction/ shows RTA pruning CHA at toy scale but does not specify the
production invocation path. An implementer must guess how RTA is invoked.

---

**DEF-03 [HIGH]: Coverage gate precondition -- what constitutes a "probe on record"**

Location: MODEL sec 7 coverage gate.

The gate is stated: "ImpactQuery refuses to emit a non-UNMEASURED label for a
(language x resolver x repo) with no probe on record, returning UNMEASURED instead."

Not specified:
- Where probes are stored (in-memory dict? gate.yaml? a coverage_cards.db file?
  a module-level registry?).
- The key structure: is it (language: str, resolver: str, repo_root: Path) or
  something else?
- How a probe is "registered" -- at import time (module-level constants from
  coverage-cards.md), at first run (lazy probe), or by the user configuring gate.yaml?
- Whether the coverage card data from coverage-cards.md is hardcoded into the
  runner or is externally configurable.

Without this, the implementer cannot write the gate predicate. No test vector
shows what probe format is checked.

---

**DEF-04 [HIGH]: `AdvisoryFinding.id` naming convention not specified**

Location: DESIGN-rescoped.md (no mention of id format) + advisory.py (field exists).

`graph_triage.py` uses `"graph-triage-%d" % (idx + 1)`. The DESIGN doc and MODEL
do not specify what id scheme the CrossRepoImpactRunner should use. Questions:
- Should ids be per-symbol (e.g. `"cross-repo-impact-{symbol}-{idx}"`) or
  per-finding (sequential)?
- Is there a namespace requirement (e.g., `"cross-repo-impact-"` prefix)?
- Is uniqueness required within a single `run()` call, across calls, or globally?
- Does the id format affect deduplication behavior anywhere in machine.py?

Without a specified format, two implementations produce different ids for the same
finding, making regression tests brittle.

---

**DEF-05 [MEDIUM]: Sibling index import format and storage schema not specified**

Location: MODEL sec 10, DESIGN-rescoped.md C3 (staleness).

MODEL sec 10: "forge imports precomputed structural indexes (index.scip / the
sibling's graph.db)." DESIGN-rescoped.md C3 mentions staleness labeling with
`indexer_version + base_commit`. Not specified:
- Where sibling index files are expected on disk relative to the review repo or
  forge's working directory.
- Whether the sibling index is a raw `.scip` file, a pre-decoded graph.db table,
  or a separate SQLite file.
- How `indexer_version` is stored and retrieved from the index (SCIP metadata
  field? A sidecar file? graph.db pragma?).
- The concrete import step: is there a CLI call, a Python function, or a new
  graph.db ingestion path?

scale-test.md sec 3 notes the topology decision (index at build time, import
precomputed) but does not specify the import interface forge exposes.

---

**DEF-06 [MEDIUM]: Ranking predicate -- `_paths_share_subsystem` fix not specified**

Location: DESIGN-rescoped.md C1 (four fixes), MODEL sec 8.

DESIGN lists the fix: "`_paths_share_subsystem` compares only the first two path
segments" causing the flagship example to self-suppress. It says "Fix the predicate
or the example." It does not say WHICH fix is chosen, or what the corrected
predicate logic is. The planner and implementer will have to decide:
- Change the predicate to compare more path segments (how many)?
- Replace it with a different subsystem-proximity metric?
- Remove the threshold entirely for the cross-repo use case?

MODEL sec 8 says "subsystem proximity via the C1-fixed predicate" -- it assumes
the fix exists without specifying it. The DESIGN inherits the same ambiguity.

---

**DEF-07 [MEDIUM]: Transitive chaining budget -- hard limits not specified**

Location: MODEL sec 9, DESIGN-rescoped.md C3.

"On-demand chaining under a HARD budget (max depth + latency cap)" is stated but
not quantified:
- What is the default max depth (2? 3? configurable?)?
- What is the latency cap in seconds (the sem timeout is 15s; is the chain budget
  separate?)?
- Is this configurable per gate.yaml or hardcoded?
- What is the truncation note format (affects the finding description field)?

No concrete values appear in MODEL, DESIGN, or coverage-cards.

---

**DEF-08 [MEDIUM]: `line_range` field population for cross-repo call sites**

Location: advisory.py (field: `line_range: list[int]`), DESIGN + MODEL (no guidance).

graph_triage.py populates `line_range` from graph.db `line_start / line_end` of
the changed entity (the CALLEE). For cross-repo impact, the finding should name
the SIBLING call site (file + line) per the phase goal: "surfaces an ADVISORY
finding that names the sibling call site (file + line) and the changed symbol."

Not specified:
- Does `line_range` represent the changed symbol's location, or the sibling call
  site's location?
- When a sibling repo is involved, is `file` an absolute path, a repo-relative
  path, or a `<repo>/<file>` qualified path?
- How to handle a call site with no line information (e.g., the SCIP occurrence
  has no range).

The `[0, 0]` fallback in graph_triage.py is a pattern but the semantic is
undefined for cross-repo findings.

---

**DEF-09 [LOW]: Ranking "uncalibrated" label -- how surfaced to the reviewer**

Location: MODEL sec 8.

"Weights are uncalibrated; output is labeled 'ranking uncalibrated' until tuned."
Not specified whether this label appears in:
- The `description` field of each finding?
- The `attribution` field?
- A single summary finding at the top of the list?
- The SKIP finding when the axis emits no results?

An implementer will pick arbitrarily, potentially creating reviewer confusion.

---

**DEF-10 [LOW]: Open question from MODEL sec 13 -- ranking weights/subsystem calibration**

Location: MODEL sec 13 "Open questions."

MODEL explicitly lists: "Ranking weights + subsystem-proximity predicate: calibrate
on a real multi-implementer repo; ordinal ships meanwhile." This is a stated open
question in the authoritative spec. The plan must acknowledge it is intentionally
deferred and include the ordinal-only interim approach. If the planner omits this
acknowledgment, a reviewer may re-open the calibration question as a plan defect.

---

## Implementation Landmines

### LAND-01: `AxisRunner.run()` signature is `(diff_text: str, repo_root: Path)` -- no SCIP index path

advisory.py line 63: `run(self, diff_text: str, repo_root: Path) -> list[AdvisoryFinding]`.

The protocol signature is intentionally narrow (anti-anchoring invariant, advisory.py
docstring). But the CrossRepoImpactRunner needs the sibling SCIP index (or graph.db
path) to perform the cross-repo query. Options the implementer must choose between
without guidance:
- Inject the sibling index path at `__init__` (like GraphTriageRunner injects
  nothing, but discovers graph.db from repo_root at run time).
- Discover the sibling index from a `.code-forge/gate.yaml` key read inside `run()`
  using `repo_root`.
- Use the existing `CRG_DB_PATH` / gate.yaml `db_path` convention from
  graph_triage.py's `_detect_backend()`.

The MODEL and DESIGN do not state which discovery convention to follow. Using
`__init__` injection breaks the pattern from GraphTriageRunner; using gate.yaml
discovery requires a new key schema. Neither is specified.

### LAND-02: `advisory_runners` wiring in cross_repo.py is PRIMARY-only

cross_repo.py lines 292-324: advisory_runners (including GraphTriageRunner) are
instantiated only for the PRIMARY repo thread; sibling threads get an empty list
(`advisory_runners = []`). If CrossRepoImpactRunner is added to this list, it will
only run once for the primary repo. But the phase goal is to find impact on a
SIBLING repo when the PRIMARY repo changes a symbol. This means the runner's
findings must reference sibling repos, not just the primary.

Two design paths exist, neither locked:
- (A) Add CrossRepoImpactRunner to the primary thread's advisory_runners only (it
  performs the cross-repo lookup itself, querying sibling indexes from within the
  primary context).
- (B) Add a new coordination point where the primary runner can see sibling repo
  metadata.

Path (A) matches the existing advisory pattern but requires the runner to know
about sibling repos without receiving them via `run()`. Path (B) widens the
AxisRunner protocol, violating the anti-anchoring invariant.

This is the single highest-risk interface mismatch in the design.

### LAND-03: `AdvisoryFinding.file` field for sibling findings -- ambiguous semantics

advisory.py: `file: str`. For GraphTriageRunner this is a repo-relative path.
For CrossRepoImpactRunner naming a sibling call site, the field must distinguish:
- Which sibling repo the file belongs to.
- The file path within that repo.

A bare relative path like `pkg/api/client.go` is ambiguous if the primary and
sibling repos share path prefixes. No convention for qualified cross-repo paths
(e.g., `sibling-repo-name:pkg/api/client.go`) exists in advisory.py or anywhere
in the codebase. The planner must define this convention.

### LAND-04: SCIP protobuf decoding dependency

coverage-cards.md and scale-test.md describe a "dependency-free protobuf decoder"
used in spikes. The production CrossRepoImpactRunner will need to decode SCIP
`index.scip` files to read `is_implementation` edges. The spike decoder is not in
the main codebase (`spike/` directory in the evidence tree). Two options with
different dependency costs:
- Port the spike's dependency-free decoder into forge's `src/code_forge/`.
- Add `scip` (protobuf) as a pip dependency.

Neither is specified in DESIGN-rescoped.md. Adding a dependency requires the
package legitimacy check; adding a hand-rolled decoder risks maintenance burden.
scale-test.md lists the SCIP field numbers (field 3 = is_implementation), which
is the minimal decoder surface.

### LAND-05: scip-go toolchain stability is a first-class operational constraint

scale-test.md sec 1 finding 1: "scip-go's module path was renamed from
`github.com/sourcegraph/scip-go` to `github.com/scip-code/scip-go`; `@latest` at
the old path fails." The runner presumably invokes scip-go as a subprocess.
If the runner hard-codes the old path (or `go install scip-go@latest`) it silently
breaks. The DESIGN does not specify a toolchain version pin or a health-check
strategy for the scip-go binary. This must be addressed in Wave 0 or a
setup/prerequisite task.

### LAND-06: Coverage gate -- Go/TS FULL label is nominal, not universal

coverage-cards.md: TS is "FULL for NOMINAL `implements`/`extends`; BLIND to
structural (duck) conformance." If the runner emits SOUND-OVERAPPROX for a TS
interface method, it is technically correct for the nominal case, but it silently
over-promises for repos that rely on structural conformance. The coverage gate
(DEF-03) must gate on (language, conformance_mode) not just (language). This
nuance is absent from DESIGN-rescoped.md.

### LAND-07: Empty caller set is not an error -- must emit SKIP, not a zero-finding list

The success criterion states: "When the code-review-graph register/db is absent or
the symbol is not found, the axis emits SKIP -- never a crash, never a silent pass."
The DESIGN and MODEL do not specify the SKIP representation. In particular:
- Is SKIP a special `AdvisoryFinding` with `axis="CROSS-REPO-IMPACT"` and a
  description containing "SKIP"?
- Is it an empty `[]` return with a message written to stderr (matching
  GraphTriageRunner's loud-fail pattern)?
- Is it a single finding with `id="cross-repo-impact-skip"` and a specific
  description template?

The distinction matters for test assertions: a test checking for SKIP behavior
cannot assert `len(findings) == 0` if SKIP is a finding, and cannot assert
`findings[0].description contains "SKIP"` if SKIP is an empty return. Neither the
advisory.py protocol nor the DESIGN specifies the SKIP contract.

### LAND-08: Concurrent access to graph.db during review

cross_repo.py uses `threading.Thread` for per-repo execution (lines 351-360).
GraphTriageRunner opens graph.db with `sqlite3.connect("file:%s?mode=ro" ...)`.
The read-only URI mode is safe for concurrent reads. However, if the new
CrossRepoImpactRunner writes to graph.db (e.g., caching imported sibling indexes),
concurrent write from one thread and read from another will produce locking errors.
The DESIGN mentions "import precomputed indexes" but does not specify whether this
import is a write operation or a read-only query against a pre-built joint db. If
it is a write, a separate index file (not the primary graph.db) is required for
thread safety.

---

## Test Coverage Gaps

The plan seed references "24 tests (22 unit + 2 integration)" (not from the
authoritative evidence files, but from the plan seed context). The following CORE
contracts have no identifiable test coverage from the proposed plan:

### GAP-01: R-role classifier -- compound role detection
No test vector for a symbol that satisfies BOTH plain_function AND concrete_impl
simultaneously (the CORE compound case, MODEL sec 4). Without a concrete SCIP
fixture with known field values, an assertion can only test the happy path for a
single role.

### GAP-02: R1-rev -- RTA-available path vs CHA-fallback path, labeled correctly
The dual-path label is the main R1-rev contract. A test must show:
(a) When RTA is available, the finding is labeled EXACT-ish and siblings are
    absent from results.
(b) When RTA is unavailable, the finding is labeled SOUND-OVERAPPROX with the
    "siblings not filtered" note.
Both paths require different fixture conditions. If only the CHA fallback path is
tested (easier to set up without a live toolchain), the RTA path is unverified.

### GAP-03: Coverage gate precondition enforcement
A test must show that for a (language, resolver, repo) with no probe on record,
the runner returns UNMEASURED rather than a labeled result. Per the forge CLAUDE.md
bug-injection rule: inject a missing probe record, assert UNMEASURED; restore the
probe, assert the real label. Without this, the gate is untested.

### GAP-04: D2 preservation -- dispatch-unavailable note + R0 retained
Success criterion 2 says the axis emits SKIP, not a silent pass. A separate
contract (D2, MODEL sec 8) says when a missing/unbuildable index is detected, R0
results are still shown and a dispatch-unavailable note is emitted. A test must
confirm that even in the no-index case, R0 direct callers appear in the findings.

### GAP-05: Merge deduplication -- per-edge keep-highest, per-path show-worst
MODEL sec 8 specifies the merge semantics. No test in the proposed 24-test plan
addresses a scenario where the same (caller_site, callee_symbol) pair appears at
two different precision rungs from two resolvers, to verify the keep-highest
dedup fires and the per-path show-worst displays the correct compound rung.

### GAP-06: `AdvisoryFinding` contract -- advisory findings never block verdict
advisory.py documents this invariant in its module docstring. A test must confirm
that when CrossRepoImpactRunner produces findings, the StateMachine cycle counter
is not reset and the verdict is unchanged (i.e., the runner is wired as advisory,
not blocking). This is an integration-level test -- it cannot be verified by a
unit test of the runner alone.

### GAP-07: Malformed graph.db / corrupted SCIP index
graph_triage.py handles `sqlite3.Error` and `OSError` with a warning log and
empty return. The CrossRepoImpactRunner needs the same. A test injecting a
zero-byte or truncated graph.db must verify the runner returns SKIP (not a crash).
Per the forge CLAUDE.md: inject the corruption, see SKIP; restore, see real result.

### GAP-08: Staleness label on sibling index
MODEL sec 9 specifies that a sibling index "M behind HEAD" produces a staleness
label. No test scenario is described for verifying that the runner detects and
surfaces this label when the sibling index's `base_commit` does not match the
sibling's current HEAD.

### GAP-09: Tier-3 UNRESOLVED label emission
The success criterion does not explicitly name this, but MODEL and DESIGN require
it. A test must show that when R-role classifies a symbol as `dynamic_target`, the
runner emits a finding with UNRESOLVED label and the text "unresolved dynamic
dispatch at <site>", rather than silently producing an empty result or an
incorrect role routing.

### GAP-10: True-total count preserved when truncated (D1 preservation)
MODEL sec 8 D1: "the count shown is always the true total" even when max_findings
caps the display. A test with more findings than the cap must verify the
description includes "+N more" and the N is the correct remainder.

---

## Gray Areas (unresolved -- need discuss-phase)

### GRAY-01 [CRITICAL]: How does CrossRepoImpactRunner receive sibling repo metadata?

The AxisRunner protocol signature is fixed at `(diff_text, repo_root)`. The runner
needs sibling repo paths and their precomputed indexes to perform cross-repo lookup.
The evidence files do not specify how this data reaches the runner:
- Option A: `__init__` injection (sibling index paths passed at construction time
  by cross_repo.py before it adds the runner to advisory_runners).
- Option B: Discovery from gate.yaml at run time (a new `cross_repo_impact.sibling_indexes`
  key in `.code-forge/gate.yaml`).
- Option C: Discovery from the `register` mechanism (the "register" in the phase
  name -- but what the register stores and how the runner reads it is unspecified
  anywhere in the evidence).

The phase name says "via register" but the MODEL, DESIGN, and plan seed do not
define the register's data model, storage format, or API. This is the most
consequential unresolved gray area -- it affects the entire interface between
cross_repo.py and CrossRepoImpactRunner.

### GRAY-02 [HIGH]: SKIP representation -- empty return vs sentinel finding

The success criterion says "the axis emits SKIP -- never a crash, never a silent
pass." A "silent pass" in advisory.py is an empty `[]` return. But an empty
return is indistinguishable from "no impact found" (a legitimate result when the
changed symbol has no sibling callers). SKIP must be distinguishable. The evidence
does not define the SKIP representation. Options:
- A sentinel `AdvisoryFinding` with a specific id (e.g., `"cross-repo-impact-skip"`)
  and description containing "SKIP: <reason>".
- A separate `infra_errors` list attribute on the runner (matching
  GraphTriageRunner's pattern).
- A `list[AdvisoryFinding]` where a SKIP finding has a distinctive `attribution`
  field value.

Without resolving this, the integration test for success criterion 2 cannot be
written deterministically.

### GRAY-03 [HIGH]: File path convention for sibling findings

`AdvisoryFinding.file` is a string. For sibling repo findings, the path must be
interpretable by the reviewer. Options:
- Qualified path: `"<sibling-repo-name>:<repo-relative-path>"` (e.g.,
  `"rtk:src/lib.rs:42"`).
- Absolute path on disk.
- URL or remote reference if the sibling repo is not locally checked out.

The phase goal says findings name "the sibling call site (file + line)." The
`line_range` field carries the line. The `file` field format for cross-repo paths
is undecided. This affects how the finding is rendered and whether it is
machine-parseable.

### GRAY-04 [HIGH]: SCIP decoder -- hand-roll vs dependency

The spike uses a dependency-free protobuf decoder. The production runner needs
SCIP decoding. The evidence does not decide between:
- Porting the spike's minimal decoder (field numbers from scale-test.md: Index=1,
  Document=2, SymbolInformation=4, Relationship=3, is_implementation=3).
- Adding `protobuf` or `scip` as a pip dependency.
- Reusing graph.db (which may already have is_implementation edges ingested) and
  avoiding SCIP decoding entirely in the runner.

This decision drives the Wave 0 dependency list and the package legitimacy audit.

### GRAY-05 [MEDIUM]: `_paths_share_subsystem` fix -- what is the corrected predicate?

DESIGN-rescoped.md C1 says to fix the predicate that causes the flagship example
to self-suppress. It does not say how. Options:
- Compare more path segments (e.g., first 3).
- Use a keyword-matching fallback as documented in the docstring but not implemented.
- Remove the threshold for cross-repo use (cross-repo findings are always surfaced).
- Replace with a directory-tree edit-distance metric.

This must be decided before the C1 implementation task is written, because the
fix is a named deliverable of C1 and its correctness is verifiable against the
flagship example in the DESIGN.

### GRAY-06 [MEDIUM]: Transitive chaining -- is it in CORE scope for this phase?

MODEL sec 9 specifies transitive chaining under a hard budget. The plan seed's
CORE list includes "depth-1 precompute for CORE roles + staleness (MODEL sec 9)"
but MODEL sec 9 also contains the transitive chaining spec. The plan seed does not
explicitly state whether transitive chaining (beyond depth-1) is in or out of
scope for Phase 27. If it is in scope, two additional implementation items follow
(the chaining loop + the latency cap). If it is depth-1 only for Phase 27, that
must be stated as a named limit so a planner does not add the chaining loop
unnecessarily.

### GRAY-07 [LOW]: Integration test topology -- which two repos constitute the "two registered repos"?

The success criterion specifies: "With two repos registered in a cross-repo graph,
changing a symbol used by the sibling repo surfaces an ADVISORY finding." The
integration tests need two real or fixture repos. The evidence does not specify:
- Whether these are the forge repo itself + a test fixture repo.
- Whether a synthetic minimal repo (a Go module with one interface + one
  implementer) is sufficient.
- Whether the `scip-go` binary must be present in the test environment for the
  integration test to pass.

If scip-go is required, the integration tests are environment-dependent and cannot
run in CI without installing the binary. If a fixture index.scip is pre-committed,
the test is hermetic but the fixture can go stale.

---

## Recommended Gray Area Priority

Ranked by implementation-blocking impact -- resolving these in order allows plan
waves to be written without re-opening earlier decisions.

1. **GRAY-01 (CRITICAL) -- Register interface and sibling metadata delivery.** The
   entire architecture of CrossRepoImpactRunner depends on knowing how it receives
   sibling repo information. This blocks Wave 0 (interface design) and every
   subsequent wave. Resolve first.

2. **GRAY-02 (HIGH) -- SKIP representation.** Blocks writing the integration test
   for success criterion 2. Resolve before writing any test plan.

3. **GRAY-04 (HIGH) -- SCIP decoder decision.** Drives the dependency list and
   Wave 0 setup. Resolving it determines whether a new pip dependency appears in
   pyproject.toml or a new internal module is added.

4. **GRAY-03 (HIGH) -- File path convention for sibling findings.** Affects the
   `AdvisoryFinding` construction in every resolver that produces cross-repo
   results. Resolve before implementing any resolver.

5. **GRAY-05 (MEDIUM) -- `_paths_share_subsystem` fix.** Blocks C1 implementation
   task from being written with a verifiable done-condition. The fix is a named C1
   deliverable; the plan cannot say "fix the predicate" without specifying the
   corrected logic.

6. **GRAY-06 (MEDIUM) -- Transitive chaining scope.** If in scope, adds two
   implementation tasks. If out of scope, it must be a named limit in the plan.
   Resolve before drafting the wave breakdown.

7. **GRAY-07 (LOW) -- Integration test topology.** Can be resolved during Wave 0
   setup, but must be settled before writing integration test tasks. Lower urgency
   because the unit tests can proceed independently.

---

## Metadata

**Deficiencies found:** 10 (DEF-01 through DEF-10)
**Implementation landmines:** 8 (LAND-01 through LAND-08)
**Test coverage gaps:** 10 (GAP-01 through GAP-10)
**Gray areas requiring discuss-phase:** 7 (GRAY-01 through GRAY-07)
**Critical blockers (implementation cannot start without resolution):** GRAY-01, GRAY-02
