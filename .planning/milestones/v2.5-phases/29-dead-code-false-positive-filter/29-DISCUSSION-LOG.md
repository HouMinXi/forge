# Phase 29: Dead-Code False-Positive Filter - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-25
**Phase:** 29-dead-code-false-positive-filter
**Areas discussed:** Detection mechanism, Shared abstraction level, Scope split, Forward-compat SQL, find_entity_dependents, Error direction, SQL dedup, Module location, Testing strategy, Return type, Multi-language support

---

## Detection mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Pure lexical scan | Indentation scan for if False:/TYPE_CHECKING:. Simplest, Python+C unified path. | |
| Python tree-sitter + C lexical | Python AST ancestor walk (accurate), C #if/#endif nesting (lexical). Mixed. | ✓ |
| You decide | Claude decides based on complexity and test coverage. | |

**User's choice:** Python tree-sitter + C lexical
**Notes:** tree-sitter already importable as transitive dep.

---

## Pattern scope (follow-up to Detection)

| Option | Description | Selected |
|--------|-------------|----------|
| TYPE_CHECKING + False only | Minimal set covering observed live FPs. | |
| Add version guard | Also detect sys.version_info conditional imports. | ✓ |
| You decide | Claude decides based on actual FP data. | |

**User's choice:** Add version guard (sys.version_info patterns)

---

## Shared abstraction level

| Option | Description | Selected |
|--------|-------------|----------|
| Two-layer helper | _is_dead_call_site + _live_callers. Both axes call _live_callers. | ✓ |
| Only _is_dead_call_site | Bottom-level only, resolve+filter loop stays in each axis. | |
| You decide | Claude decides based on SC#3 and code structure. | |

**User's choice:** Two-layer helper

---

## Scope split

| Option | Description | Selected |
|--------|-------------|----------|
| All at once | Python + C in single phase. | ✓ |
| Python-only first | C #if 0 as fast-follow. | |

**User's choice:** All at once (Python + C in a single phase). A later expansion to Go/Rust/Java was considered then REVERSED -- see Multi-language support below.

---

## Forward-compat SQL

| Option | Description | Selected |
|--------|-------------|----------|
| Add inert filter | json_extract on edges.extra at 3 query sites. Zero cost now. | |
| Don't add | Lexical/tree-sitter sufficient. #576 is its own scope. | ✓ |
| You decide | Claude decides based on cost/benefit. | |

**User's choice:** Don't add

---

## find_entity_dependents

| Option | Description | Selected |
|--------|-------------|----------|
| Wire through _live_callers | graphdb branch goes through shared filter. sem branch untouched. | ✓ |
| Defer (YAGNI) | No production caller today. Wait for future axis. | |

**User's choice:** Wire through _live_callers
**Notes:** Confirmed no production callers (only test + docstring "exported for future axes"). User chose to wire it anyway since we're changing the shared layer.

---

## Error direction

| Option | Description | Selected |
|--------|-------------|----------|
| Default live (miss-not-noise) | Failure returns False (treat as live). Residual FP tolerable. | ✓ |
| Default dead (noise-not-miss) | Aggressive filtering, risk of dropping live callers. | |

**User's choice:** Default live (miss-not-noise)

---

## SQL query dedup

| Option | Description | Selected |
|--------|-------------|----------|
| Extract to shared layer | Consolidate duplicate CALLS+IMPORTS_FROM SQL into dead_code.py. | ✓ |
| Don't touch, scope external | Pre-existing duplication, leave for separate refactor. | |
| You decide | Claude decides based on actual change scope. | |

**User's choice:** Extract to shared layer

---

## Module location

| Option | Description | Selected |
|--------|-------------|----------|
| New dead_code.py | Separate module, SRP, keeps graph_triage.py under 800 lines. | ✓ |
| In graph_triage.py | Alongside existing shared helpers. File would exceed 700 lines. | |
| You decide | Claude decides based on code volume and coupling. | |

**User's choice:** New dead_code.py

---

## Testing strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Single test_dead_code.py | Unit + bug-inject + real-path smoke all in one file. | ✓ |
| Split into two files | Unit tests separate from smoke tests (gated pattern). | |
| You decide | Claude decides based on test scale. | |

**User's choice:** Single test_dead_code.py

---

## Return type

| Option | Description | Selected |
|--------|-------------|----------|
| list[tuple[str,str,int/None]] | Lightweight, consistent with fetchall() style. | |
| Frozen dataclass | LiveCaller(qualified, file, line). More readable. Matches forge patterns. | ✓ |
| You decide | Claude decides based on codebase consistency. | |

**User's choice:** Frozen dataclass

---

## Multi-language support

| Option | Description | Selected |
|--------|-------------|----------|
| Python + C only | Cover observed FPs. Other languages via fallback=live. | |
| Add Go/Rust/Java | Forward-looking lexical pattern match. No new parser deps. | ✓ |

**User's choice:** Add Go/Rust/Java (lexical only)
**Notes:** Go: //go:build ignore. Rust: #[cfg(not(...))], #[cfg(test)]. Java: if (false). All via file-extension dispatch in _is_dead_call_site.
**REVERSED 2026-06-25** (4-model consult gm/kn/ds/mimo + review): Go/Rust/Java detectors NOT shipped. The proposed lexical patterns were unsound (Rust #[cfg(test)]/#[cfg(not(...))] is LIVE, would drop live callers) or low-value (Go file-level/rare; Java's real idiom is a constant-folded static-final flag that `if (false)` misses); none had an observed FP. Superseded by Python+C detectors plus a multi-language extension point. Authoritative: 29-CONTEXT.md D-01/D-05/D-11.

---

## Claude's Discretion

None -- user made explicit choices for all areas.

## Deferred Ideas

None -- discussion stayed within phase scope.
