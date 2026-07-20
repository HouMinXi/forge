# Phase 29: Dead-Code False-Positive Filter - Pattern Map

**Mapped:** 2026-06-25
**Files analyzed:** 4 (2 new, 2 modified)
**Analogs found:** 4 / 4

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/code_forge/dead_code.py` | utility | transform | `src/code_forge/graph_triage.py` (shared helpers) + `src/code_forge/canary.py` (frozen dataclass) | role-match |
| `src/code_forge/cross_repo_impact.py` | service | CRUD | self (lines 130-168 replaced by `_live_callers` call) | exact |
| `src/code_forge/graph_triage.py` | service | CRUD | self (lines 260-285 + 386-408 replaced by `_live_callers` call) | exact |
| `tests/test_dead_code.py` | test | transform | `tests/test_cross_repo_impact.py` | exact |

## Pattern Assignments

### `src/code_forge/dead_code.py` (utility, transform) -- NEW

**Analog 1:** `src/code_forge/graph_triage.py` -- shared helper pattern
**Analog 2:** `src/code_forge/canary.py` -- frozen dataclass pattern
**Analog 3:** `src/code_forge/advisory.py` -- frozen dataclass pattern

**File header pattern** (graph_triage.py lines 1-12):
```python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Module docstring: one-sentence purpose.

Multi-line elaboration of what the module does, its place in the
architecture, and key design constraints.
"""
from __future__ import annotations
```

**Frozen dataclass pattern** (canary.py lines 38-54):
```python
@dataclass(frozen=True)
class Canary:
    """A planted defect tracked in the reviewer-invisible manifest.

    file/line locate the mutation for matching against reviewer findings.
    sha256 records the hash of the injected mutation for the audit log ...
    """

    canary_id: str
    file: str
    line: int
    sha256: str
    description: str = ""
```
Apply to: `LiveCaller` dataclass. Use `@dataclass(frozen=True)` with typed fields `qualified: str`, `file: str`, `line: int | None`.

**Module-level constant pattern** (graph_triage.py lines 34-48):
```python
_SEM_TIMEOUT_S: int = 15
"""Per-entity sem impact timeout (seconds)."""

_TOP_N: int = 10
"""Fixed top-N entities to emit as findings."""

_UNNAMED_ENTITIES: frozenset[str] = frozenset({"module-level"})
"""Entity names that have no named symbol (skip)."""
```
Apply to: `_DETECTORS` dict, `_DEAD_CONDITIONS` frozenset, compiled regexes.

**Shared predicate helper pattern** (graph_triage.py lines 132-138):
```python
def _is_unnamed(entity_name: str) -> bool:
    """Return True if entity name is unnamed (skip for impact)."""
    if entity_name in _UNNAMED_ENTITIES:
        return True
    if entity_name.startswith(_UNNAMED_PREFIX):
        return True
    return False
```
Apply to: `_is_dead_call_site(file_path, line)` -- same `_`-prefixed module-internal helper with docstring, conservative return on edge cases.

**SQL query + cursor pattern** (cross_repo_impact.py lines 134-159):
```python
cursor.execute(
    "SELECT DISTINCT c.source_qualified FROM edges c "
    "WHERE c.kind = 'CALLS' AND c.target_qualified = ? "
    "AND EXISTS ("
    "  SELECT 1 FROM edges i "
    "  WHERE i.kind = 'IMPORTS_FROM' "
    "  AND i.source_qualified LIKE "
    "    substr(c.source_qualified, 1, "
    "      instr(c.source_qualified, '::') - 1) || '%%' "
    "  AND (i.target_qualified LIKE '%%' || ? || '%%' "
    "       OR i.target_qualified LIKE '%%' || ? || '%%')"
    ")",
    (name, module_name, name),
)
callers = cursor.fetchall()

for (caller_qualified,) in callers:
    cursor.execute(
        "SELECT file_path, line_start FROM nodes "
        "WHERE qualified_name = ?",
        (caller_qualified,),
    )
    row = cursor.fetchone()
    caller_file = row[0] if row else "<unknown>"
    caller_line = row[1] if row else None
```
Apply to: `_live_callers()` -- extract this exact SQL + resolution loop, add `_is_dead_call_site` filter before appending.

**Conservative error handling pattern** (cross_repo_impact.py lines 253-258):
```python
try:
    changed = resolve_changed_symbols(diff_text, str(primary_db))
except (sqlite3.Error, OSError) as exc:
    self.infra_errors.append(
        "cross-repo-impact: primary graph.db unreadable: %s" % exc
    )
    return []
```
Apply to: `_is_dead_call_site` must return `False` (not raise) on any error. Same spirit: fail-safe, not fail-loud.

**Import from sibling module pattern** (cross_repo_impact.py lines 23-24):
```python
# Reuse helpers from graph_triage -- never duplicate logic.
from .graph_triage import _is_unnamed, _parse_diff_files
```
Apply to: both `cross_repo_impact.py` and `graph_triage.py` will import from `.dead_code`. Same relative-import style.

---

### `src/code_forge/cross_repo_impact.py` (service, CRUD) -- MODIFIED

**Analog:** self (current implementation at lines 130-168)

**Current caller loop to replace** (lines 130-168):
```python
for sym in changed:
    name = sym["name"]
    module_name = sym["module"]

    # CALLS + IMPORTS_FROM disambiguation (same as graph_triage)
    cursor.execute(
        "SELECT DISTINCT c.source_qualified FROM edges c "
        ...
    )
    callers = cursor.fetchall()

    for (caller_qualified,) in callers:
        cursor.execute(
            "SELECT file_path, line_start FROM nodes "
            "WHERE qualified_name = ?",
            (caller_qualified,),
        )
        row = cursor.fetchone()
        caller_file = row[0] if row else "<unknown>"
        caller_line = row[1] if row else None

        results.append({
            "symbol": name,
            "caller_qualified": caller_qualified,
            "caller_file": caller_file,
            "caller_line": caller_line,
        })
```
Replace with: call `_live_callers(cursor, name, module_name)`, then unpack each `LiveCaller` into the result dict. The outer `for sym in changed:` loop stays; only the inner SQL + resolution is replaced.

**New import to add** (after line 24):
```python
from .dead_code import _live_callers
```

---

### `src/code_forge/graph_triage.py` (service, CRUD) -- MODIFIED

**Analog:** self (current implementation at lines 259-285 and 386-408)

**Site (B): _run_graphdb dependent count to replace** (lines 259-285):
```python
# Count dependents with IMPORTS_FROM disambiguation.
cursor.execute(
    "SELECT DISTINCT c.source_qualified FROM edges c "
    ...
)
dependents = cursor.fetchall()
dep_names = [d[0] for d in dependents[:5]]

results.append({
    "name": name,
    "file": file_path,
    "qualified_name": qualified_name,
    "dependent_count": len(dependents),
    "top_dependents": dep_names,
    ...
})
```
Replace with: call `_live_callers(cursor, name, module_name)`, then `len(live)` for `dependent_count` and `[lc.qualified for lc in live[:5]]` for `top_dependents`.

**Site (C): find_entity_dependents graphdb branch to replace** (lines 386-411):
```python
if db_path is not None:
    try:
        conn = sqlite3.connect(
            "file:%s?mode=ro" % db_path, uri=True,
        )
        cursor = conn.cursor()
        module_name = Path(file_path).stem
        cursor.execute(
            "SELECT DISTINCT c.source_qualified FROM edges c "
            ...
        )
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except (sqlite3.Error, OSError):
        pass
```
Replace with: call `_live_callers(cursor, entity_name, module_name)`, then `[lc.qualified for lc in live]`. Connection management stays the same.

**New import to add** (after line 27):
```python
from .dead_code import _live_callers
```

---

### `tests/test_dead_code.py` (test, transform) -- NEW

**Analog:** `tests/test_cross_repo_impact.py`

**File header + imports pattern** (test_cross_repo_impact.py lines 1-21):
```python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for dead_code module.

Uses tmp_path sqlite fixtures built by hand -- real sqlite files, not mocks --
so the sqlite3 query path is actually exercised. ...
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
```

**Hand-built graph.db fixture pattern** (test_cross_repo_impact.py lines 29-67):
```python
_NODES_DDL = (
    "CREATE TABLE nodes ("
    "  id INTEGER PRIMARY KEY,"
    "  kind TEXT,"
    "  name TEXT,"
    "  qualified_name TEXT,"
    "  file_path TEXT,"
    "  line_start INTEGER,"
    "  line_end INTEGER"
    ")"
)
_EDGES_DDL = (
    "CREATE TABLE edges ("
    "  kind TEXT,"
    "  source_qualified TEXT,"
    "  target_qualified TEXT"
    ")"
)

def _make_db(path: Path, nodes: list[tuple], edges: list[tuple]) -> Path:
    """Build a real sqlite graph.db at *path* with the given rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(_NODES_DDL)
    conn.execute(_EDGES_DDL)
    conn.executemany(
        "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", nodes,
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?, ?, ?)", edges,
    )
    conn.commit()
    conn.close()
    return path
```
Apply to: `test_dead_code.py` needs the same `_make_db` helper. Add fixture source files (Python with `if TYPE_CHECKING:`, C with `#if 0`) via `tmp_path` writes.

**Pytest fixture pattern** (test_cross_repo_impact.py lines 99-126):
```python
@pytest.fixture()
def primary_repo(tmp_path: Path) -> Path:
    """Create a primary repo dir with a graph.db containing named nodes."""
    repo = tmp_path / "primary"
    repo.mkdir()
    crg_dir = repo / ".code-review-graph"
    crg_dir.mkdir()
    _make_db(
        crg_dir / "graph.db",
        nodes=[...],
        edges=[...],
    )
    return repo
```
Apply to: fixture for dead-code tests should create a graph.db where some callers are at lines inside `if TYPE_CHECKING:` blocks and others are at live lines.

**Test class organization pattern** (test_cross_repo_impact.py lines 174, 205, 238, 280):
```python
class TestAdvisoryContract:
    """Verify the runner satisfies AxisRunner Protocol basics."""

    def test_is_advisory_true(self) -> None:
        ...

class TestResolveChangedSymbols:
    """Verify symbol resolution from diff + primary graph.db."""
    ...
```
Apply to: organize as `TestIsDeadCallSite`, `TestLiveCallers`, `TestBugInject`, `TestFailSafe`, `TestDetectorDispatch`.

**Sample diff helper pattern** (test_cross_repo_impact.py lines 83-92):
```python
def _sample_diff(file_path: str = "src/lib/handler.py") -> str:
    """Return a minimal unified diff touching *file_path*."""
    return (
        "diff --git a/{f} b/{f}\n"
        "--- a/{f}\n"
        "+++ b/{f}\n"
        "@@ -10,3 +10,4 @@\n"
        " existing line\n"
        "+new line\n"
    ).format(f=file_path)
```

---

## Shared Patterns

### Frozen Dataclass for Data Carriers
**Source:** `src/code_forge/canary.py` lines 38-54, `src/code_forge/advisory.py` lines 25-45
**Apply to:** `LiveCaller` in `dead_code.py`
```python
@dataclass(frozen=True)
class LiveCaller:
    """A caller confirmed as not inside dead code."""
    qualified: str
    file: str
    line: int | None
```

### Conservative Error Handling (fail-safe = live)
**Source:** `src/code_forge/cross_repo_impact.py` lines 253-258, `src/code_forge/graph_triage.py` lines 288-289
**Apply to:** `_is_dead_call_site` in `dead_code.py` -- return `False` on any exception
```python
# cross_repo_impact.py: returns [] on sqlite3 error
except (sqlite3.Error, OSError) as exc:
    self.infra_errors.append(...)
    return []

# graph_triage.py: logs warning and returns empty results
except (sqlite3.Error, OSError) as exc:
    logger.warning("graph.db read error: %s", exc)
```

### Module-Internal `_` Prefix Convention
**Source:** `src/code_forge/graph_triage.py` lines 132 (`_is_unnamed`), 60 (`_parse_diff_files`)
**Apply to:** `_is_dead_call_site`, `_live_callers`, `_is_dead_python`, `_is_dead_c`, `_DETECTORS`
All non-public helpers use `_` prefix. This is the established forge convention.

### SPDX + Copyright Header
**Source:** `src/code_forge/cross_repo_impact.py` lines 1-2, `src/code_forge/graph_triage.py` lines 1-2
**Apply to:** Both new files (`dead_code.py`, `test_dead_code.py`)
```python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
```

### Relative Import for Sibling Modules
**Source:** `src/code_forge/cross_repo_impact.py` line 24
**Apply to:** `cross_repo_impact.py` and `graph_triage.py` importing from `.dead_code`
```python
from .dead_code import _live_callers
```

### Hand-Built SQLite Fixture (tests)
**Source:** `tests/test_cross_repo_impact.py` lines 29-67
**Apply to:** `tests/test_dead_code.py` -- reuse exact `_NODES_DDL`, `_EDGES_DDL`, `_make_db` pattern. Add fixture Python/C source files via `tmp_path` writes for `_is_dead_call_site` testing.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All 4 files have strong analogs in the existing codebase |

**Partial gap:** The tree-sitter AST ancestor walk in `_is_dead_python` has no existing analog in forge. RESEARCH.md Pattern 1 (verified runtime examples) provides the reference code. The lexical C `#if 0` scanner also has no forge analog; RESEARCH.md Pattern 2 provides the reference. Both are self-contained detection functions that follow the `_is_unnamed` predicate helper shape.

## Metadata

**Analog search scope:** `src/code_forge/`, `tests/`
**Files scanned:** 6 (cross_repo_impact.py, graph_triage.py, canary.py, advisory.py, test_cross_repo_impact.py, test_graph_triage.py)
**Pattern extraction date:** 2026-06-25
