# Phase 22: Graph Triage - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-14
**Phase:** 22-graph-triage
**Areas discussed:** Integration approach, Dependency philosophy, Opt-in mechanism, Scope honesty

---

## Integration Approach

| Option | Description | Selected |
|--------|-------------|----------|
| A: sem CLI | Shell out to sem binary. Full featured but requires cargo install. | |
| B: code-review-graph MCP | Already running, zero deps. But MCP is Claude Code exclusive. | |
| D: Drop Phase 22 | Don't ship REVIEW-SYSTEM-01. Shrink v2.4 scope. | |
| E: sem CLI optional + MCP fallback | Parallel detect both; whoever available first wins. | ✓ |

**User's choice:** E: sem CLI optional + MCP fallback

### Follow-up: Detection order

| Option | Description | Selected |
|--------|-------------|----------|
| sem first | which sem -> have it use sem, else try MCP | |
| MCP first | Check MCP connection -> have it use MCP, else try sem | |
| Parallel detection | Check both simultaneously, first available wins | ✓ |

**User's choice:** Parallel detection

### Follow-up: Both absent

| Option | Description | Selected |
|--------|-------------|----------|
| SKIP + warning | Loud-fail like taint missing semgrep. infra_errors records. | ✓ |
| FAIL global | Since user opted in, tool absent = hard failure. | |
| Degrade to grep | Fall back to crude import/include grep analysis. | |

**User's choice:** SKIP + warning

---

## Dependency Philosophy

### MCP detection method

| Option | Description | Selected |
|--------|-------------|----------|
| Env var probe | Check CRG_DB_PATH env var | |
| Direct read graph.db | Read .code-review-graph/graph.db via built-in sqlite3 | |
| gate.yaml path | graph_triage: { db_path: ... } explicit config | |
| sem only, no MCP | Simplify to single backend | |

**User's choice:** All three methods work and are layered (user response: "the first three methods all work, and forge can also post-check MCP at runtime"). Locked as D-02: priority order gate.yaml > auto-discover > env var. Direct SQLite read, zero new pip deps.

---

## Opt-in Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| gate.yaml explicit | graph_triage: { enabled: true } required. Most conservative. | |
| Auto-detect + can disable | Discover sem/graph.db -> auto-enable. User can disable. | ✓ |
| CLI parameter | code-forge review --graph-triage. Per-invocation, not persistent. | |
| gate.yaml + CLI override | gate.yaml persistent + CLI --graph-triage / --no-graph-triage. | |

**User's choice:** Auto-detect + can disable

### Follow-up: Output count

| Option | Description | Selected |
|--------|-------------|----------|
| Top 5 fixed | Only 5 highest-impact entities | |
| Top 10 fixed | 10 highest-impact entities | ✓ |
| Dynamic Top-N | Configurable via gate.yaml, default 5 | |
| Threshold filter | Only entities above impact count N | |

**User's choice:** Top 10 fixed

---

## Scope Honesty

| Option | Description | Selected |
|--------|-------------|----------|
| Has confirmed consumer | Can name at least one project/scenario that directly benefits | |
| Speculative but low cost | No confirmed consumer, but zero-dep scheme E makes it cheap | |
| Speculative, should defer | No consumer, defer until demand signal appears | |
| forge itself is consumer | forge review itself uses graph triage to enhance review quality | ✓ |

**User's choice:** forge itself is consumer (can dogfood)

### Follow-up: Review flow integration

| Option | Description | Selected |
|--------|-------------|----------|
| Pure advisory, standalone display | Standalone "Graph Triage" section in verdict output | |
| Advisory + inject into review prompt | Top-N impact table injected into LLM prompt + standalone display | ✓ |
| Appendix only | Written to findings.json only, not displayed | |

**User's choice:** Advisory + inject into review prompt

---

## Claude's Discretion

None -- user made explicit choices on all areas.

## Deferred Ideas

- graph.db auto-build (too invasive for v2.4)
- sem MCP server mode (complexity vs CLI)
- Cross-repo graph analysis (needs code-review-graph cross-repo registry)
- PyO3 bindings to sem-core (major engineering effort)
