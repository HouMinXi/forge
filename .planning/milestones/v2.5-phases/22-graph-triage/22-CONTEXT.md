# Phase 22: Graph Triage - Context

**Gathered:** 2026-06-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 22 delivers REVIEW-SYSTEM-01: an opt-in system-level blast-radius ranking
that surfaces cross-file impact as advisory findings. It integrates with
existing entity extraction tools (sem CLI and/or code-review-graph SQLite DB)
to build a dependency graph of changed entities and rank them by downstream
impact count. Top-10 high-impact entities are injected into the LLM review
prompt and displayed as advisory findings in the verdict output.

In scope: GraphTriageRunner advisory axis; dual-backend entity extraction
(sem CLI + graph.db SQLite); blast-radius ranking algorithm; gate.yaml
`graph_triage` schema + validation; review prompt injection of impact context;
advisory output display; E-corpus entry.

Note: SKILL.md mirror + D-10 drift test are NOT applicable to Phase 22.
Those patterns exist for LLM-based axes that hardcode a question constant
(e.g., RUNTIME_LIFECYCLE_QUESTION in runtime.py). GraphTriageRunner is purely
deterministic (subprocess + SQLite) with no LLM question constant to mirror
or drift-test. This is consistent with TaintRunner (Phase 18), which also has
no SKILL.md mirror because it is deterministic.

Out of scope: blocking behavior of any kind; adding new pip dependencies;
building a custom tree-sitter parser; cross-repo analysis; inspect-core (FSL
license); Phase 23 daemon state (separate axis); modifying existing review
passes or cycle counter logic.

Scope note: REVIEW-SYSTEM-01 delivers the dependents direction (blast radius).
The callees-of-changed direction that Phase 21 deferred here is not covered by
impact ranking and remains future work.

## Scope Honesty (per brief convention)

forge review itself is the consumer -- graph triage provides cross-file impact
context that makes review findings more informed. Unlike Phase 23 (only surflare
as external consumer), forge can dogfood this axis on its own codebase. The
axis is still opt-in by default (auto-detect + can disable) because not all
repos have sem or graph.db available.

</domain>

<decisions>
## Implementation Decisions

### Area 1: Integration Approach

- **D-01:** Scheme E -- parallel detection of sem CLI + code-review-graph
  graph.db SQLite. Whichever is available first gets used. Both produce the
  same intermediate representation: a list of changed entities with their
  downstream dependents.

- **D-01a:** Full absence = SKIP + loud-fail warning (same pattern as taint
  axis missing semgrep). `infra_errors` records the degradation. Does not
  block other review axes.

- **D-01b:** sem CLI detection: `shutil.which("sem")`. Invoked via
  `subprocess.run(["sem", "impact", ...])` with list args (no shell=True).

- **D-01c:** graph.db detection: Python built-in `sqlite3` module reads the
  SQLite database directly. No MCP protocol needed, zero new dependencies.

### Area 2: Dependency Philosophy

- **D-02:** graph.db access via direct SQLite read (Python built-in sqlite3).
  Detection priority:
  1. gate.yaml `graph_triage.db_path` explicit config
  2. `repo_root / .code-review-graph / graph.db` auto-discovery
  3. `CRG_DB_PATH` environment variable

- **D-02a:** pyproject.toml zero new runtime dependencies. sem = external
  binary (`which sem`), graph.db = Python built-in sqlite3. Forge stays at
  2 runtime deps (pyyaml + unidiff).

### Area 3: Opt-in Mechanism

- **D-03:** Auto-detect + can disable. If sem CLI or graph.db is discovered,
  graph triage activates automatically. User can explicitly disable via
  `graph_triage: { enabled: false }` in gate.yaml. Lowest adoption barrier --
  install the tool, automatically benefit.

- **D-03a:** Top 10 fixed output. Display the 10 entities with highest
  downstream impact count, sorted descending.

### Area 4: Integration with Review Flow

- **D-05:** Advisory + review prompt injection. Top-10 entity impact info is
  injected into the LLM review prompt (so the reviewer knows which changes
  have large blast radius). Also displayed independently in the "Graph Triage"
  section of the verdict output. Does not affect cycle counter.

- **D-05a:** Prompt injection format: a structured table prepended to the
  review prompt (same FUSE-01 pattern as Step 0 context fusion). Table
  columns: entity name, file, downstream count, top dependents.

### Advisory Contract (pre-answered from Phase 20/21)

Advisory only -- never blocks verdict, never resets cycle counter, never gates
commit. Same contract as RUNTIME (Phase 20 D-04/SC4) and LEGACY (Phase 21).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Advisory axis infrastructure
- `src/code_forge/advisory.py` -- AdvisoryFinding dataclass + AxisRunner Protocol
- `src/code_forge/machine.py` L969-996 -- `_run_advisory_axes()` dispatch loop
- `src/code_forge/cli.py` L1516 -- advisory runner registration order

### Existing advisory axes (pattern to follow)
- `src/code_forge/runtime.py` -- RuntimeRunner (RUNTIME axis, same advisory contract)
- `src/code_forge/taint.py` -- TaintRunner (tool-absent = loud-fail pattern for D-01a)
- `src/code_forge/legacy.py` -- LegacyRunner (latest advisory axis)

### LLM invocation (F1/F2 envelope contract)
- `src/code_forge/llm_invoke.py` L62-74 -- `_REVIEW_ENVELOPE_KEYS` and caller map
- `src/code_forge/llm_invoke.py` L107-151 -- `_extract_json_from_text` with
  `expected_keys` parameter

### gate.yaml schema validation
- `src/code_forge/gate_check.py` -- existing schema validation (extend for graph_triage)

### FUSE-01 prompt injection pattern
- SKILL.md Step 0 Context Fusion section -- pattern for prepending deterministic
  context to LLM review prompts

### sem-core (external tool)
- https://github.com/Ataraxy-Labs/sem -- sem CLI + sem-core Rust library
- `sem impact` subcommand -- cross-file dependency graph
- License: MIT OR Apache-2.0

### code-review-graph (external tool, already deployed)
- `.code-review-graph/graph.db` -- SQLite database with entity + edge tables
- Schema: nodes table (id, name, kind, file, line_start, line_end),
  edges table (source_id, target_id, kind)

### Eval corpus
- `tests/eval/corpus/corpus.yaml` -- manifest (add E-corpus entry)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `AdvisoryFinding` dataclass: id, axis, file, line_range, description,
  attribution -- sufficient for all graph triage findings (no extension needed)
- `AxisRunner` Protocol: is_advisory, run(diff_text, repo_root), infra_errors --
  GraphTriageRunner implements this directly
- `TaintRunner._check_semgrep_available()` pattern: tool-absent detection +
  infra_errors recording (reuse for sem/graph.db detection)

### Established Patterns
- Advisory runner registration: cli.py L1516 `advisory_runners=[..., runner]` --
  GraphTriageRunner appends to the list
- machine.py `_run_advisory_axes()` generic loop: no changes needed (handles
  new runners automatically)
- FUSE-01 context injection: Step 0 findings prepended to LLM prompt. Same
  pattern for graph triage entity impact table.

### Integration Points
- `gate_check.py`: extend validate_gate_yaml to parse `graph_triage` section
  (enabled, db_path, top_n fields)
- `cli.py L1516`: add GraphTriageRunner to advisory_runners list
- `machine.py`: inject graph triage context into review prompt (new code path)
- Review prompt template: prepend impact table before diff content

</code_context>

<specifics>
## Specific Ideas

- The blast-radius ranking is the core value: "function X changed, and it has
  47 downstream dependents across 12 files" tells the reviewer where to focus.
- For graph.db backend: query nodes table for changed entities (match by file
  path from diff), then BFS/edge-walk to count reachable downstream nodes.
- For sem CLI backend: `sem impact --format json` (if available) or parse
  `sem impact` text output.
- Impact table format in review prompt:
  ```
  | Entity | File | Downstream | Top Dependents |
  |--------|------|------------|----------------|
  | parse_config() | config.py | 23 | cli.py, machine.py, runner.py |
  ```

</specifics>

<deferred>
## Deferred Ideas

- **graph.db auto-build:** If graph.db is stale or missing, forge could trigger
  `code-review-graph build` automatically. Too invasive for v2.4 -- user should
  build their own graph.
- **sem MCP server:** sem has an MCP server mode. Could use that instead of CLI.
  Adds complexity for marginal benefit over direct CLI invocation.
- **Cross-repo graph analysis:** When changes span repos (e.g., OVS kernel +
  userspace), the blast radius should include cross-repo dependents. Requires
  code-review-graph's cross-repo registry. Future feature.
- **PyO3 bindings to sem-core:** Native Python bindings to the Rust crate.
  Major engineering effort for uncertain benefit vs CLI subprocess.

</deferred>

---

*Phase: 22-graph-triage*
*Context gathered: 2026-06-14*
