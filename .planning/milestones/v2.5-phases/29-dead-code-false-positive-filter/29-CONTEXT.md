# Phase 29: Dead-Code False-Positive Filter - Context

**Gathered:** 2026-06-25
**Status:** Ready for planning

<domain>
## Phase Boundary

The cross-repo and graph-triage advisory axes stop surfacing callers inside
statically-dead code (`if False:`, `if TYPE_CHECKING:`, `if sys.version_info`,
`#if 0`) as findings, eliminating the most
common class of false positives. Two shared helpers (`_is_dead_call_site` +
`_live_callers`) live in a new `dead_code.py` module; both advisory axes and
the exported `find_entity_dependents` utility call through them. The duplicate
CALLS+IMPORTS_FROM SQL query is extracted into the shared layer at the same time.

</domain>

<decisions>
## Implementation Decisions

### D-01: Detection mechanism -- per-language strategy
- **Python**: tree-sitter AST ancestor walk. Detects `if TYPE_CHECKING:` and
  `if False:` (unconditionally dead). For `if sys.version_info <op> (...)` guards
  it EVALUATES the comparison against the running interpreter's actual
  `sys.version_info` and treats the block as dead ONLY when the guard is False on
  this interpreter -- a blanket "any `<` is dead" rule would wrongly drop live
  callers inside e.g. `if sys.version_info < (3, 13):` on Python 3.11/3.12
  (violates D-06). tree-sitter is already a transitive dep (via
  code-review-graph). Compile the query ONCE at module level, not per call --
  `_is_dead_call_site` runs per caller (_TOP_N scale).
- **C/C++ (.c/.h)**: lexical scan -- upward `#if 0` / `#endif` nesting count.
  tree-sitter C grammar not assumed available.
- **Go / Rust / Java and every other language**: NO detector shipped. Reason
  (4-model consult + review): the earlier-proposed lexical patterns are unsound
  or low-value -- Rust `#[cfg(test)]` / `#[cfg(not(...))]` is LIVE not dead and
  would drop live callers (violates D-06); Go `//go:build ignore` is file-level
  and rare; Java's real idiom is a constant-folded `static final` flag that a
  lexical `if (false)` misses. None has an OBSERVED FP in forge's graph. They are
  served by the extension point (D-11), not a shipped detector.
- **Unregistered / unreadable / parse failure**: fallback = `False` (treat as
  live). See D-06.

### D-02: Shared abstraction -- two-layer helpers
- **`_is_dead_call_site(file_path, line)`** -- lowest-level primitive. Opens
  file, dispatches to language-specific detector by file extension, returns
  `bool`. Conservative: any failure returns `False`.
- **`_live_callers(cursor, target_name, module_name)`** -- owns the
  CALLS+IMPORTS_FROM SQL (so the query lives in one place per D-08), resolves
  `(qualified, file, line)` from the `nodes` table for each returned caller,
  drops dead ones via `_is_dead_call_site`, returns `list[LiveCaller]`. The
  signature takes the query parameters (target_name, module_name) rather than a
  pre-fetched caller list -- a deliberate D-02+D-08 merge so the function owns the
  extracted SQL (from cross_repo_impact.py:134-147 and graph_triage.py:260-273).

### D-03: Return type -- frozen dataclass
- `_live_callers` returns `list[LiveCaller]` where `LiveCaller` is a frozen
  dataclass: `qualified: str`, `file: str`, `line: int | None`.
- Defined in `dead_code.py`. Callers unpack fields as needed:
  (A) builds per-caller findings, (B) takes `len()`, (C) extracts qualifieds.

### D-04: Module location -- new dead_code.py
- New file: `src/code_forge/dead_code.py`.
- `graph_triage.py` and `cross_repo_impact.py` import from it.
  Import direction: both existing files already import shared helpers from
  `graph_triage.py`; the new module is one more import source at the same level.
- Keeps `graph_triage.py` from exceeding the 800-line ceiling.

### D-05: Scope -- Python + C detectors only, plus a multi-language extension point
- Only the Python and C detectors ship in Phase 29. The concrete dead-code
  idioms they target appear in forge's own code (`if TYPE_CHECKING:` at
  machine.py:32 and cli.py:24-25). IMPORTANT (verified against the real
  graph.db): those imports are IMPORTS_FROM edges, and the advisory axes surface
  CALLS-edge callers ONLY -- so they are NOT currently surfaced as findings.
  Phase 29 is therefore PREVENTIVE infrastructure (it will filter CALLS-edge dead
  callers when they occur) plus the SQL dedup win (SC#3); it is NOT fixing a
  currently-occurring CALLS-edge false positive. Reversed from the earlier
  "Python+C+Go+Rust+Java one-shot" after a 4-model consult: Go/Rust/Java had no
  observed FP and the proposed detectors were unsound (Rust) or low-value
  (Go/Java). See D-01 and D-11.
- A language is added later only when (a) an OBSERVED FP appears in graph.db AND
  (b) a sound per-line detector exists for its real dead-code idiom.
- Documented honest ceiling (Criterion #4): lexical scan has false-negatives on
  deep/unusual nesting; C `#ifdef MACRO` is build-config-dependent and undecidable
  without the real build. Any language without a registered detector is always
  treated as live.

### D-06: Error direction -- miss-not-noise (fail-safe = live)
- `_is_dead_call_site` returns `False` (treat as live) when:
  file unreadable, language unrecognized, parse fails, line is None.
- Rationale: a missed dead caller is a tolerable residual FP; a wrongly-dropped
  live caller is a silent false negative in an advisory tool -- worse.

### D-07: Forward-compat SQL -- NOT included
- The `json_extract(edges.extra, '$.reachable')` inert filter is NOT added.
  Upstream #576 is a separate track; its SQL changes are its own scope.
  The lexical/tree-sitter scan is the sole filter mechanism.

### D-08: SQL query dedup -- extracted into shared layer
- The near-identical CALLS+IMPORTS_FROM query duplicated across
  `cross_repo_impact.py:134-147` and `graph_triage.py:260-273` (and
  `find_entity_dependents:393-402`) is extracted into `_live_callers` or a
  companion helper in `dead_code.py`. Both axes call through the shared query.

### D-09: find_entity_dependents(C) -- graphdb branch wired
- The graphdb branch of `find_entity_dependents` (graph_triage.py:386-408)
  is wired through `_live_callers` to filter dead callers before returning.
- The `sem` branch (graph_triage.py:368-374) is NOT touched -- it uses a
  different data source where graph.db file:line filtering does not apply.

### D-10: Testing -- single test file
- New file: `tests/test_dead_code.py`.
- Contains: unit tests for `_is_dead_call_site` (Python + C fixture source files
  ONLY), `_live_callers` (hand-built graph.db), bug-inject cycle, and real-path
  smoke (forge's own graph.db).
- Unsupported-language fail-safe test: assert `_is_dead_call_site` returns False
  for unregistered extensions (`foo.go`, `foo.rs`, `foo.java`, `foo.xyz`) and for
  a detector that raises. This tests the DISPATCH fail-safe ONLY. Write NO test
  that asserts a dead-code SEMANTIC for an unsupported language -- a test asserting
  "`#[cfg(test)]` is dead" would freeze a wrong claim into a passing test (forge
  golden rule #2). A future language brings its own fixtures when its detector lands.
- Bug-inject is REQUIRED: neutralize `_is_dead_call_site` -> always `False`,
  watch dead caller reappear (test FAILS); restore, watch it vanish (PASSES).

### D-11: Multi-language extension point (no Go/Rust/Java detector shipped)
- The foundation IS the file-extension dispatch, not pre-built detectors
  (4-model consult: unanimous). Concrete shape -- a plain dict in `dead_code.py`:
  `_DETECTORS: dict[str, Callable[[str, int], bool]]` keyed by extension, seeded
  with `.py` / `.c` / `.h`; `_is_dead_call_site` looks up by extension and returns
  False (live) when there is no entry or the detector raises.
- No Protocol class / plugin discovery / config-driven registry (YAGNI at 2
  detectors; revisit at 5+). The one carry-over from the rejected class proposal:
  cache any compiled tree-sitter query at module level (per-caller call volume).
- "Add a language" contract (docstring on `_is_dead_call_site`): add a language
  only with (a) an OBSERVED graph.db FP and (b) a sound per-line detector for the
  real idiom; the detector MUST return False (live) on any doubt.
- The `(file_path, line)` signature is the entry point, NOT the scope boundary --
  a detector owns its read strategy and walks UP from `line` (the Rust error was
  failing to walk up). Keep the signature minimal; do not couple it to the graph
  schema.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Handoff brief (ground truth, verified file:line)
- `/tmp/draft_20260625_phase29_handoff_brief.md` -- contains verified
  file:line citations for all integration points, the asymmetry analysis
  (A)/(B)/(C), proof obligations, and process constraints. Read sections
  2 (asymmetry), 3 (design), 4 (proof obligations) before planning.

### Advisory axis source files
- `src/code_forge/cross_repo_impact.py` -- (A) enumerate-and-surface pattern.
  Lines 130-168: caller loop with file:line resolution. Lines 134-147: SQL.
- `src/code_forge/graph_triage.py` -- (B) count-based pattern.
  Lines 260-285: dependent count without file:line. Lines 349-408:
  find_entity_dependents (C).

### Test infrastructure
- `tests/test_graph_triage.py` -- existing hand-built graph.db fixtures.
  Reuse this infra for Phase 29 dead-code fixtures.
- `tests/test_cross_repo_impact.py` -- existing cross-repo test patterns.

### Forge's own graph.db (detector test target)
- `.code-review-graph/graph.db` -- contains dead-code IMPORTS_FROM edges at
  machine.py:32 (TYPE_CHECKING import of AdvisoryFinding) and cli.py:24-25
  (TYPE_CHECKING imports). These are IMPORTS_FROM edges, NOT CALLS edges, so the
  advisory axes do NOT surface them as findings -- they serve as real-source
  DETECTOR unit-test targets (does the detector flag line 32 as dead?), not as
  full-pipeline filter-removal targets (no CALLS-edge FP exists to remove).

### ROADMAP success criteria
- `.planning/ROADMAP.md` Phase 29 entry -- 4 success criteria (SC#1-#4).
  SC#1: fixture test. SC#2: bug-inject proof. SC#3: no copy-paste (shared
  helper). SC#4: honest ceiling documented.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `graph_triage._is_unnamed()` -- pattern for shared predicate helpers
- `graph_triage._parse_diff_files()` -- pattern for shared parsing helpers
- `cross_repo_impact.py:24` import of shared helpers -- establishes the import
  direction (both axis files import from the shared layer)
- tree-sitter Python: transitive dep via code-review-graph, confirmed importable
- Hand-built graph.db fixture pattern in test_graph_triage.py -- reusable for
  dead-code fixtures

### Established Patterns
- Frozen dataclasses for data carriers (canary.py: Canary, CanaryGateResult)
- DI protocol pattern for testability (canary_gen.py: CanaryProvider)
- Conservative fallback on error (cross_repo_impact returns [] on sqlite3 error)
- `_` prefix for module-internal helpers

### Integration Points
- (A) cross_repo_impact.py:150-166 -- caller loop replaced by _live_callers call
- (B) graph_triage.py:260-285 -- dependents query replaced by _live_callers call,
  then `len()` + `[:5]` on result
- (C) graph_triage.py:386-408 -- graphdb branch wrapped through _live_callers
- SQL at 3 sites (A:134, B:260, C:393) consolidated into dead_code.py

</code_context>

<specifics>
## Specific Ideas

- The asymmetry between (A) and (B) is the key design challenge: (A) already
  has file:line per caller; (B) only has bare qualified names and takes len().
  _live_callers must handle both use cases -- return LiveCaller objects that
  (A) unpacks into findings and (B) counts.
- Bug-inject proof is REQUIRED (forge golden rule #2): neutralize
  _is_dead_call_site -> always False, watch dead caller reappear (test FAILS);
  restore, watch it vanish (test PASSES). Show both outputs.
- Real-path smoke (two-level, per the ground truth above): (a) DETECTOR unit on
  real source -- _is_dead_call_site(machine.py, 32) returns True, a live line
  returns False; (b) PIPELINE no-crash -- _live_callers over a real CALLS target
  returns a non-empty list[LiveCaller] without crashing. There is no CALLS-edge
  dead-code FP in forge's own graph.db to "drop with the filter," so full-pipeline
  removal is proven on a hand-built fixture (SC#2 bug-inject), not on forge's db.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 29-Dead-Code False-Positive Filter*
*Context gathered: 2026-06-25*
