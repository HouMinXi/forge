# Phase 22: Graph Triage - Research

**Researched:** 2026-06-14
**Domain:** System-level blast-radius ranking via entity extraction + dependency graph
**Confidence:** HIGH (verified by smoke tests on real forge codebase)

## Summary

Phase 22 implements REVIEW-SYSTEM-01: a GraphTriageRunner advisory axis that
surfaces cross-file impact as advisory findings. The CONTEXT.md decision D-01
chose Scheme E: parallel detection of sem CLI and code-review-graph graph.db
SQLite, first available wins. Research and smoke testing revealed a critical
quality gap between the two backends that changes the integration design.

**Primary recommendation:** sem CLI is the PREFERRED backend (precise entity
resolution, zero false positives, complete blast-radius pipeline in one tool).
graph.db is a degraded FALLBACK (short-name edges cause false positives even
after IMPORTS_FROM disambiguation). The runner should try sem first, fall back
to graph.db only if sem is absent, and document the quality difference.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Scheme E -- parallel detection of sem CLI + graph.db SQLite.
  **Revised by research:** sem is PREFERRED (higher quality), graph.db is
  FALLBACK (degraded precision). Both still supported.
- **D-01a:** Full absence = SKIP + loud-fail warning, infra_errors record.
- **D-01b:** sem CLI via shutil.which + subprocess.run (list args, no shell).
- **D-01c:** graph.db via Python built-in sqlite3, zero new dependencies.
- **D-02:** graph.db detection priority: gate.yaml > auto-discover > env var.
- **D-02a:** Zero new pip runtime dependencies.
- **D-03:** Auto-detect + can disable (gate.yaml graph_triage.enabled: false).
- **D-03a:** Top 10 fixed output, sorted by downstream impact count.
- **D-05:** Advisory + review prompt injection (FUSE-01 pattern).
- **D-05a:** Impact table prepended to review prompt.

</user_constraints>

## Codebase Analysis

### Advisory Axis Infrastructure (ready to use)

The advisory axis infrastructure is mature: AdvisoryFinding dataclass,
AxisRunner Protocol, machine.py generic dispatch loop (_run_advisory_axes at
L969-996), and four working axes:

| Axis | Module | Pattern | Relevant to Phase 22 |
|------|--------|---------|---------------------|
| TaintRunner | taint.py | Tool-absent = loud-fail | Reuse for sem/graph.db absence |
| RuntimeRunner | runtime.py | LLM + constant + SKILL.md mirror + drift test | Follow for SKILL.md mirror |
| LegacyRunner | legacy.py | git-blame + advisory | Structural pattern |
| DaemonStateRunner | (Phase 23) | Two-step LLM + grep | Different approach |

GraphTriageRunner is SIMPLER than all of these: no LLM call needed. It is pure
deterministic analysis (subprocess + SQLite). This makes it the lightest
advisory axis.

### gate.yaml Extension Point

gate_check.py validates gate.yaml schema. Extension for `graph_triage`:
```yaml
graph_triage:
  enabled: false        # explicit disable (default: auto-detect)
  db_path: .code-review-graph/graph.db  # override auto-discover
```

Minimal schema: only `enabled` (bool) and `db_path` (string, optional).

## External Tool Analysis

### sem CLI (PRIMARY backend) -- VERIFIED

**Version tested:** 0.10.1
**License:** MIT OR Apache-2.0 (confirmed via Exa + crates.io)
**Source:** https://github.com/Ataraxy-Labs/sem (Ataraxy-Labs)
**Installation:** `curl -fs https://raw.githubusercontent.com/Ataraxy-Labs/sem/main/install.sh | sh`

#### sem diff --format json (VERIFIED)

Returns structured entity-level changes:
```json
{
  "summary": {"fileCount": 1, "added": 0, "modified": 1, ...},
  "changes": [
    {
      "entityId": "src/code_forge/llm_invoke.py::function::llm_invoke",
      "changeType": "modified",
      "entityName": "llm_invoke",
      "filePath": "src/code_forge/llm_invoke.py",
      "startLine": 206, "endLine": 241
    }
  ]
}
```

**Smoke test:** 19 changed entities across HEAD~3 (3 commits). Entity IDs
fully qualified. Non-code files (YAML, Markdown) also parsed.

#### sem impact <entity> --json (VERIFIED)

```json
{
  "entity": {"entityId": "...::function::llm_invoke", ...},
  "dependencies": [...],
  "dependents": [...],
  "impact": {"depth": 2, "entities": [...], "total": 72},
  "tests": [...]
}
```

**Smoke test on llm_invoke:** 5 dependencies, 53 direct dependents, 72
transitive (2-hop), entity IDs FULLY QUALIFIED.

#### Complete Pipeline Verified

```
sem diff HEAD~3 --format json -> 19 changed entities
    |
for each: sem impact <name> --file <path> --json
    |
sort by impact.total -> Top 10 ranking
```

**Result on forge HEAD~3:**

| # | Entity | File | Total Affected |
|---|--------|------|----------------|
| 1 | llm_invoke | llm_invoke.py | 72 |
| 2 | _invoke_api | llm_invoke.py | 55 |
| 3 | _extract_json_from_text | llm_invoke.py | 21 |
| 4 | _extract_with_keys | test_llm_invoke.py | 1 |
| 5-10 | (test/config entities) | various | 0 |

**Edge cases:** `module-level` and `lines N-M` entities fail sem impact
(no named entity). Safely skipped (impact = 0).

#### sem diff --patch (stdin mode) -- VERIFIED

`sem diff --patch --format json` reads a unified diff from stdin and
produces the same entity-level JSON output as the git-ref mode. This is
the correct invocation for forge's GraphTriageRunner, which receives
`diff_text` from the AxisRunner protocol (not a git ref).

Smoke test (2026-06-14):
```
git diff HEAD~3 | sem diff --patch --format json  -> 19 entities (matches git-ref mode)
sem diff --patch --format json < /tmp/test.patch  -> 1 entity (single-commit patch)
```

sem diff --help confirms: `--patch  Read unified diff from stdin (e.g. git diff | sem diff --patch)`

### code-review-graph graph.db (FALLBACK) -- VERIFIED WITH CAVEATS

**Location:** `.code-review-graph/graph.db` (24.2 MB, SQLite WAL mode)
**Verified schema:**

```sql
nodes (3280 rows):
  id, kind, name, qualified_name, file_path, line_start, line_end,
  language, parent_name, params, return_type, is_test, signature

edges (24225 rows):
  id, kind, source_qualified, target_qualified, file_path, line

Node kinds: Test (1692), Function (824), Class (556), File (208)
Edge kinds: CALLS (12604), TESTED_BY (6831), CONTAINS (3133),
            IMPORTS_FROM (1585), INHERITS (65), REFERENCES (7)
```

#### CRITICAL FINDING: Short-Name Edge Resolution

**edges.target_qualified uses SHORT names, not full qualified names.**

| Query | Expected | Actual |
|-------|----------|--------|
| `target_qualified = 'run'` | ~5 | 256 (ALL `run()` calls) |
| `target_qualified = 'llm_invoke'` | ~5 | 12 (correct, unique name) |

**Root cause:** tree-sitter call-site extraction cannot resolve which module
a called symbol belongs to. `source_qualified` IS fully qualified, but
`target_qualified` stores only the bare symbol name.

**Disambiguation via IMPORTS_FROM join:**

| Metric | Raw | After IMPORTS_FROM | Reduction |
|--------|-----|-------------------|-----------|
| `run` callers | 379 | 41 | 89% |
| Remaining false positives | -- | YES | subprocess.run, pytest.run |

41 is still wrong for RuntimeRunner.run (should be ~5). The filter cannot
distinguish `subprocess.run` from `RuntimeRunner.run` when both callers
import `runtime`.

**Conclusion:** graph.db is reliable for UNIQUE names (llm_invoke,
_extract_json_from_text) but unreliable for COMMON names (run, __init__,
setup, test). Use as degraded fallback with explicit quality caveat.

## Cross-Phase Integration (Phase 23)

Phase 23 CONTEXT.md D-04: "If Phase 22 graph available, use graph results
instead of grep."

**Research finding:** Scope this integration:
- **sem available:** Phase 23 CAN use `sem impact` for precise cross-subsystem
  dependency identification. Better than grep.
- **graph.db only:** Phase 23 should STICK with grep. Short-name edges would
  produce false matches for common daemon functions (start, stop, restart, run).

Phase 23's RESEARCH.md (750 lines, completed 2026-06-14) already designed
grep as default substrate with graph as "optional enhancement." This research
confirms grep-first was correct.

## Implementation Recommendations

### GraphTriageRunner Architecture

```
GraphTriageRunner(AxisRunner Protocol)
  |
  _detect_backend() -> "sem" | "graphdb" | None
     sem preferred, graph.db fallback, None = SKIP
  |
  run(diff_text, repo_root) -> list[AdvisoryFinding]
     |
     if sem:
       sem diff HEAD~1 --format json -> changed entities
       for each: sem impact <name> --file <path> --json
       rank by impact.total, take top 10
     |
     elif graphdb:
       parse diff for file paths -> query nodes
       BFS over edges (IMPORTS_FROM disambiguation)
       rank by dependent count, take top 10
       findings include DEGRADED quality caveat
```

### Key Design Points

1. **No LLM call.** Purely deterministic (subprocess + SQLite).
2. **sem diff uses git internally.** `sem diff HEAD~1` needs repo context,
   not stdin diff text. The runner must know the base ref.
3. **AdvisoryFinding format:** axis="graph-triage", description includes
   entity name, total impact, top dependents.
4. **Prompt injection:** FUSE-01 pattern, impact table before diff content.

### Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| sem not installed | Medium | graph.db fallback + SKIP warning |
| graph.db false positives | High (common names) | IMPORTS_FROM join + quality caveat |
| sem subprocess timeout | Low | 15s timeout per entity, skip on timeout |
| graph.db stale | Medium | Check metadata.updated_at vs HEAD |
| Both absent | Low | SKIP + infra_errors |

## Open Questions (RESOLVED)

All resolved during smoke testing:

1. **sem impact JSON format?** RESOLVED: verified, documented above.
2. **graph.db edge resolution quality?** RESOLVED: short-name targets,
   disambiguation reduces but does not eliminate false positives.
3. **sem diff input method?** RESOLVED: use `sem diff HEAD~1` (git-native).
4. **Zero-dependency integration?** RESOLVED: subprocess + sqlite3 built-in.

---

*Researched: 2026-06-14*
*Smoke tested: sem 0.10.1 on forge repo (3280 nodes, 24225 edges)*
