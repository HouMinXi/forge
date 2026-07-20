# Phase 27: Cross-Repo Impact via register - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning

<domain>
## Phase Boundary

v1 ships R0 cross-repo direct-caller impact only (scope S2). When the primary
repo changes a symbol, the CrossRepoImpactRunner advisory axis enumerates the
sibling repos registered in the code-review-graph registry, queries each
sibling's own graph.db for DIRECT call sites (CALLS edges) of the changed
symbol, and surfaces an advisory finding naming the sibling call site
(repo-alias-qualified file + line) and the changed symbol. If the registry or a
sibling graph.db is absent, or the symbol is not found, the axis emits SKIP.

OUT of v1 (demand-gated to a later cut): the dispatch/interface resolvers
R1 (interface_method) and R1-rev (concrete_impl), which require SCIP
is_implementation; the SCIP decoder; per-repo SCIP index delivery; Rust,
kernel-C, shell, R2, R3; transitive chaining beyond depth-1.

</domain>

<decisions>
## Implementation Decisions

### D-01: Scope -- S2, R0 cross-repo only for v1
- v1 = R0 direct-caller impact across registered repos. R1/R1-rev (interface
  dispatch) and the whole SCIP machinery are demand-gated to a second cut.
- Rationale: R0 is nearly free -- it reuses the existing graph.db CALLS edges and
  the GraphTriageRunner query pattern, one sibling db at a time, with zero new
  parsing infrastructure. R1/R1-rev need is_implementation, which the tooling
  does not produce (see D-03), making them the heavy, unproven part. Shipping R0
  first validates the entire cross-repo plumbing (registry lookup, sibling-db
  open, finding construction, SKIP path, ranking) on a cheap path before any
  SCIP work, and lets real usage justify the SCIP cost.
- Evidence: a corrected multi-model consult (kimi/ds/mimo) was unanimous on S2
  after an earlier round had been given a false premise (that graph.db already
  carries is_implementation). The first round's "do full do-less now" rested on
  that false premise; with the corrected facts all three flipped to "R0 first,
  demand-gate R1/SCIP."
- Caveat (kimi): S2 satisfies the success criterion only for sibling DIRECT-call
  usage. Interface-dispatch "usage" is surfaced only once R1/R1-rev land. The
  success criterion's "symbol used by the sibling" is met for the direct-call
  case in v1; the dispatch case is the named second-cut deliverable.

### D-02: Sibling metadata delivery -- C, registry self-discovery (GRAY-01)
- The runner discovers the code-review-graph registry itself, enumerates sibling
  repos, and opens each sibling's own graph.db. It does NOT receive sibling
  paths via __init__ and does NOT read a new gate.yaml key. This keeps run()
  at the fixed (diff_text, repo_root) signature (anti-anchoring invariant,
  advisory.py:12-16) and matches the phase name "via register".
- Mechanism: read the registry via the Registry API
  (registry.py: Registry().list_repos() / find_by_alias() /
  get_data_dir_for_repo()); the registry lives at
  ~/.code-review-graph/registry.json (registry.py:19-20). Open each sibling's
  graph.db read-only, exactly as GraphTriageRunner opens the primary's
  (graph_triage.py:_detect_backend / _run_graphdb).
- Testability: Registry.__init__ accepts a path arg (registry.py:31), but there
  is no env override for the default registry location. Add a forge-side
  CRG_REGISTRY_PATH env var (mirroring GraphTriageRunner's CRG_DB_PATH),
  read inside the runner and passed as Registry(path=...), so integration tests
  point at a fixture registry without touching HOME. The env var name
  CRG_REGISTRY_PATH is LOCKED (not discretionary) -- both plans depend on it
  for fixture wiring. This closes the one real objection to C (a hard-coded
  ~/.code-review-graph path would otherwise need HOME mocking).
- Reject A (__init__ injection): makes cross_repo.py special-case the runner's
  construction (every other advisory runner is built no-arg or backend-only at
  cross_repo.py:314-320) and moves sibling-enumeration logic into the
  orchestrator. C keeps the axis self-contained, the forge norm.
- Reject B (new gate.yaml key): duplicates registry state into project config
  and drifts out of sync when repos are (un)registered.

### D-03: is_implementation source -- (a) SCIP decoder, DEMAND-GATED (GRAY-04)
- v1/R0 needs no is_implementation, so this decision does not bite in v1. It is
  recorded so the second cut does not reopen it.
- When R1/R1-rev are built: port the existing dependency-free SCIP protobuf
  decoder (spike at /tmp/phase27_scip/decode.py) and read is_implementation from
  each repo's index.scip. Do NOT add a protobuf/scip pip dependency (version
  churn for one edge type) and do NOT rely on graph.db (it has no
  is_implementation).
- Ground truth: code-review-graph v2.3.6 contains zero occurrences of
  is_implementation and zero of scip (grep, whole package). Its edge kinds are
  CALLS, CONTAINS, IMPORTS_FROM, INHERITS, IMPLEMENTS, REFERENCES, TESTED_BY.
- Native-IMPLEMENTS shortcut (option d) is DEAD for Go: building
  code-review-graph on a real Go interface+implementer fixture
  (/tmp/phase27_dir: Animal interface, Dog/Cat/Fox impls) produced ZERO
  IMPLEMENTS edges -- Go interface satisfaction is implicit (no implements
  keyword), invisible to a tree-sitter pass. This is exactly why the spec chose
  scip-go. Option d is at most a partial TS optimization (TS has a nominal
  implements keyword); since the do-less target includes Go, the second cut
  needs SCIP regardless.

### D-04: SKIP representation -- (b) infra_errors + empty findings (GRAY-02)
- SKIP is signaled by appending to a self.infra_errors: list[str] attribute on
  the runner and returning an empty findings list, matching GraphTriageRunner
  (graph_triage.py:430, 453, 487-489). An integration test asserts
  runner.infra_errors is non-empty for SKIP, and empty with findings == [] for a
  genuine no-impact result.
- Gap to close (not present in GraphTriageRunner): a corrupted/unreadable
  sibling graph.db must also append to infra_errors. GraphTriageRunner currently
  only logger.warning()s on sqlite3.Error/OSError and returns partial results
  (graph_triage.py:288-289), which would read as a silent pass. The new runner
  must treat a corrupt sibling db as SKIP-with-infra_error, per the success
  criterion "never a silent pass."
- Reject (a) sentinel AdvisoryFinding: pollutes every downstream consumer
  (aggregators/reporters must filter a magic id). Reject (c) overloaded
  attribution: collides with legitimate attribution values, fragile to parse.

### D-05: Sibling finding path -- qualified alias:relpath (GRAY-03)
- AdvisoryFinding.file for a sibling call site is "<repo-alias>:<repo-relative-
  path>" (e.g., "ovs:lib/netlink-socket.c"). The alias comes from the registry
  entry (registry.py find_by_alias / list_repos entries carry alias). Parse with
  str.split(":", 1).
- Reject absolute paths (non-reproducible across machines/CI) and bare relative
  paths (ambiguous when primary and sibling share a prefix like pkg/handler.go).

### D-06: Ranking predicate -- create a subsystem-proximity function (GRAY-05)
- The DESIGN-rescoped.md C1 analysis identified a conceptual deficiency: a
  prefix-based subsystem check self-suppresses cross-directory same-subsystem
  pairs (the flagship drivers/net vs net/core scores 0.30, below a 0.5
  threshold). The prefix constraint itself is the bug: drivers/net and net/core
  share zero common prefix at any depth. Note: no function named
  _paths_share_subsystem exists in the forge codebase -- this is new code.
- Create _subsystem_proximity using token-set similarity (e.g., Jaccard overlap
  of all path segments, optionally a subsystem-keyword match such as "net"),
  and relax the threshold for cross-repo findings (a cross-repo impact is
  inherently noteworthy). Do NOT use a prefix-segment approach (it only shifts
  the boundary).

### D-07: Depth -- depth-1 only (GRAY-06)
- v1 is depth-1 by nature (R0 direct callers). Transitive chaining beyond
  depth-1 is a named deferred limit; no budget loop or latency cap is built in
  v1.

### D-08: Integration-test topology -- hermetic fixture, no scip-go (GRAY-07)
- Two minimal registered repos with a cross-repo DIRECT CALLS edge (repo A
  exports a symbol; repo B calls it directly). Build each repo's graph.db
  in-process as real sqlite files (nodes + edges tables matching the live
  schema) plus a fixture registry.json under tmp_path at test time -- do NOT
  commit binary .db files to tests/fixtures/ (committed binaries go stale when
  the schema evolves; in-process build is always schema-current). Point
  CRG_REGISTRY_PATH at the fixture registry (D-02), and assert the runner emits
  a finding naming repo B's call site. No scip-go binary in CI, no SCIP needed
  for the S2 fixture (R0 uses CALLS edges only). Document the fixture schema
  and build helper in the test header so it is reproducible.

### D-09: Finding id -- sequential cross-repo-impact-N (DEF-04)
- AdvisoryFinding.id = "cross-repo-impact-%d" % (idx + 1), matching the
  GraphTriageRunner "graph-triage-%d" convention (graph_triage.py:334).

### D-10: line_range and file semantics for cross-repo (DEF-08 / LAND-03)
- The finding names the SIBLING CALL SITE: file = "<alias>:<sibling-relpath>"
  (D-05), line_range = [call_site_line, call_site_line] from the sibling
  graph.db nodes/edges line info, with a [0,0] fallback when the occurrence has
  no range (matching graph_triage.py:337). The changed symbol name goes in the
  description, per the success criterion.

### D-11: Runner wiring -- primary thread advisory_runners (LAND-02)
- Add CrossRepoImpactRunner() to the PRIMARY thread's advisory_runners list
  (cross_repo.py:314-320), alongside the existing advisory axes. It runs once in
  the primary context and reaches siblings itself via the registry (D-02).
  axis="CROSS-REPO-IMPACT", is_advisory=True (never blocks the verdict, never
  resets the cycle counter).

### Claude's Discretion
- Exact ranking weights and the token-set / keyword similarity formula (D-06).
- Finding description wording and the changed-symbol rendering.
- Fixture repo names and directory layout under tests/fixtures/ (D-08).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Advisory axis contract
- `src/code_forge/advisory.py` -- AxisRunner Protocol (fixed run() signature,
  anti-anchoring invariant) + AdvisoryFinding frozen dataclass (id, axis, file,
  line_range, description, attribution -- NO status/coverage_card field).

### The analog runner (the template for D-02/D-04/D-09/D-10)
- `src/code_forge/graph_triage.py` -- GraphTriageRunner: _detect_backend (db
  discovery from repo_root, CRG_DB_PATH env), _run_graphdb (read-only sqlite
  CALLS query), infra_errors SKIP pattern, id/line_range construction.

### Orchestration wiring (D-11)
- `src/code_forge/cross_repo.py` lines 279-360 -- _thread_fn; advisory_runners
  built only for the primary thread (314-320), siblings get [] (321-324).

### The registry (D-02)
- code-review-graph `registry.py` -- Registry().list_repos(), find_by_alias(),
  get_data_dir_for_repo(); registry at ~/.code-review-graph/registry.json;
  __init__ accepts a path arg (the test seam).

### Phase evidence
- `27-RESEARCH.md` (this dir) -- DEF/LAND/GAP/GRAY catalog.
- evidence worktree `phase27-dispatch-evidence` (branch) --
  evidence/phase27-dispatch/{MODEL-impact-query.md, DESIGN-rescoped.md,
  coverage-cards.md, scale-test.md, spike/, spike-direction/}.

### ROADMAP success criteria
- `.planning/ROADMAP.md` Phase 27 section.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- GraphTriageRunner._run_graphdb (graph_triage.py): the exact read-only
  CALLS-edge query pattern R0 needs, per sibling db. The cross-repo runner runs
  this query against each registered sibling's graph.db instead of the primary's.
- GraphTriageRunner.infra_errors + loud-fail (graph_triage.py:430/487): the SKIP
  pattern to reuse (D-04), with the corrupt-db gap closed.
- code-review-graph Registry API (registry.py): repo enumeration + alias lookup.

### Established Patterns
- repo_root-relative backend discovery with an env override (CRG_DB_PATH) --
  mirror it for the registry (CRG_REGISTRY_PATH).
- Advisory findings are constructed with a per-axis id prefix and an attribution
  string; never block, never dedup.

### Integration Points
- cross_repo.py _thread_fn primary branch (314-320): add the runner here.
- graph.db schema: nodes(qualified_name, file_path, line_start, line_end),
  edges(kind, source_qualified, target_qualified); kind='CALLS' for R0.

</code_context>

<specifics>
## Specific Ideas

- The v1 consumer is cross-repo direct-caller blast radius: two repos registered
  via `code-review-graph register`; changing an exported symbol in repo A
  surfaces the direct call sites in repo B that reference it, as an advisory
  (non-blocking) finding during A's review.
- R0 per sibling is the same SQL as graph_triage._run_graphdb, just iterated over
  Registry().list_repos() and opening each sibling's get_data_dir_for_repo()
  graph.db read-only.

</specifics>

<deferred>
## Deferred Ideas (the demand-gated second cut)

- R1 (interface_method) and R1-rev (concrete_impl) dispatch resolvers for Go/TS.
- SCIP decoder (D-03 option a) + per-repo index.scip delivery to read
  is_implementation.
- Option d (native tree-sitter IMPLEMENTS) as a TS-only partial -- only if a
  measured demand and a TS coverage card justify it; DEAD for Go (D-03 evidence).
- Transitive chaining beyond depth-1 (budget loop + latency cap).
- Rust glue, kernel-C R2 (ops-struct), shell R2, R3 protocol -- each FENCED
  behind its own coverage card per MODEL-impact-query.md sec 2.

</deferred>

---

*Phase: 27-cross-repo-impact-via-register*
*Context gathered: 2026-06-23*
