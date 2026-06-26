# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for dead_code.py shared liveness-filtering module.

Covers:
  - Python dead-code detection (TYPE_CHECKING, False, sys.version_info)
  - C dead-code detection (#if 0)
  - Fail-safe behavior (None, unreadable, unknown extension)
  - _live_callers filtering with hand-built graph.db
  - Bug-inject proof (neutralize filter -> dead callers reappear)
  - tree-sitter-absent fail-safe
  - else-branch liveness (Python + C)
  - Honest ceiling documentation
  - Detector dispatch by extension
  - Real-path smoke (forge golden rule #3)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_forge import dead_code
from code_forge.dead_code import (
    LiveCaller,
    _is_dead_call_site,
    _live_callers,
)


# ---------------------------------------------------------------------------
# Helpers: hand-built graph.db fixtures (reuse pattern from
# test_cross_repo_impact.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fixture source files
# ---------------------------------------------------------------------------

_PY_TYPE_CHECKING = """\
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from some_module import SomeType

def live_function():
    pass
"""

_PY_IF_FALSE = """\
if False:
    dead_import()

live_code()
"""

_PY_VERSION_ALWAYS_DEAD = """\
import sys

if sys.version_info < (3, 0):
    old_py2_code()

live_code()
"""

_PY_VERSION_ALWAYS_LIVE = """\
import sys

if sys.version_info < (3, 99):
    this_is_live_on_any_realistic_py3()

live_code()
"""

_PY_ELSE_BRANCH = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dead_module import DeadType
else:
    from live_module import LiveType

def regular():
    pass
"""

_C_IF0 = """\
#include <stdio.h>

void live_fn(void) {
    printf("live");
}

#if 0
void dead_fn(void) {
    printf("dead");
}
#endif

void another_live(void) {
    printf("also live");
}
"""

_C_NESTED_IF0 = """\
#include <stdio.h>

#if 0
  #if 1
  void nested_dead(void) {}
  #endif
  void also_dead(void) {}
#endif

void live(void) {}
"""

_C_ELSE_BRANCH = """\
#if 0
void dead_code(void) {}
#else
void live_code(void) {}
#endif
"""


# ---------------------------------------------------------------------------
# TestIsDeadCallSitePython
# ---------------------------------------------------------------------------

class TestIsDeadCallSitePython:
    """Python dead-code detection via tree-sitter ancestor walk."""

    def test_type_checking_is_dead(self, tmp_path: Path) -> None:
        f = tmp_path / "tc.py"
        f.write_text(_PY_TYPE_CHECKING)
        # Line 5 is "from some_module import SomeType" inside if TYPE_CHECKING
        assert _is_dead_call_site(str(f), 5) is True

    def test_if_false_is_dead(self, tmp_path: Path) -> None:
        f = tmp_path / "iffalse.py"
        f.write_text(_PY_IF_FALSE)
        # Line 2 is "dead_import()" inside if False
        assert _is_dead_call_site(str(f), 2) is True

    def test_version_guard_always_dead(self, tmp_path: Path) -> None:
        """sys.version_info < (3, 0) is always False on Python 3.x."""
        f = tmp_path / "vdead.py"
        f.write_text(_PY_VERSION_ALWAYS_DEAD)
        # Line 4 is "old_py2_code()" inside the dead guard
        assert _is_dead_call_site(str(f), 4) is True

    def test_version_guard_always_live(self, tmp_path: Path) -> None:
        """sys.version_info < (3, 99) is True on any realistic Python 3.x.

        Regression guard: a ``<`` version guard must NOT be
        blanket-flagged dead.
        """
        f = tmp_path / "vlive.py"
        f.write_text(_PY_VERSION_ALWAYS_LIVE)
        # Line 4 is "this_is_live_on_any_realistic_py3()" -- LIVE
        assert _is_dead_call_site(str(f), 4) is False

    def test_live_code_is_live(self, tmp_path: Path) -> None:
        f = tmp_path / "tc.py"
        f.write_text(_PY_TYPE_CHECKING)
        # Line 8 is "def live_function():" -- live code
        assert _is_dead_call_site(str(f), 8) is False


# ---------------------------------------------------------------------------
# TestIsDeadCallSiteC
# ---------------------------------------------------------------------------

class TestIsDeadCallSiteC:
    """C dead-code detection via lexical #if 0 scan."""

    def test_inside_if0_is_dead(self, tmp_path: Path) -> None:
        f = tmp_path / "test.c"
        f.write_text(_C_IF0)
        # Line 9 is 'printf("dead");' inside #if 0
        assert _is_dead_call_site(str(f), 9) is True

    def test_nested_if0_outer_body(self, tmp_path: Path) -> None:
        f = tmp_path / "nested.c"
        f.write_text(_C_NESTED_IF0)
        # Line 7 is "void also_dead(void) {}" directly inside outer #if 0
        # (below the nested #if 1 ... #endif).
        assert _is_dead_call_site(str(f), 7) is True

    def test_nested_if0_inner_body_is_false_negative(
        self, tmp_path: Path,
    ) -> None:
        """Nested preprocessor inside #if 0 may produce false negatives.

        Line 5 is inside #if 1 which is itself inside #if 0, but the
        upward scan hits #if 1 at depth==0 first and returns False
        (live).  This is the documented honest-ceiling limitation
        (miss-not-noise direction).
        """
        f = tmp_path / "nested.c"
        f.write_text(_C_NESTED_IF0)
        # Line 5 is inside nested #if 1 within #if 0 -- false negative.
        assert _is_dead_call_site(str(f), 5) is False

    def test_live_c_code(self, tmp_path: Path) -> None:
        f = tmp_path / "test.c"
        f.write_text(_C_IF0)
        # Line 4 is 'printf("live");' -- live code
        assert _is_dead_call_site(str(f), 4) is False

    def test_h_extension_uses_c_detector(self, tmp_path: Path) -> None:
        f = tmp_path / "test.h"
        f.write_text(_C_IF0)
        # Line 9 inside #if 0 -- detected via .h extension
        assert _is_dead_call_site(str(f), 9) is True


# ---------------------------------------------------------------------------
# TestFailSafe
# ---------------------------------------------------------------------------

class TestFailSafe:
    """_is_dead_call_site returns False (live) on any error condition."""

    def test_none_file_path(self) -> None:
        assert _is_dead_call_site(None, 5) is False

    def test_none_line(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("pass\n")
        assert _is_dead_call_site(str(f), None) is False

    def test_unreadable_file(self) -> None:
        assert _is_dead_call_site("/nonexistent/file.py", 1) is False

    def test_unregistered_extension_go(self, tmp_path: Path) -> None:
        f = tmp_path / "x.go"
        f.write_text("package main\n")
        assert _is_dead_call_site(str(f), 1) is False

    def test_unregistered_extension_rs(self, tmp_path: Path) -> None:
        f = tmp_path / "x.rs"
        f.write_text("fn main() {}\n")
        assert _is_dead_call_site(str(f), 1) is False

    def test_unregistered_extension_java(self, tmp_path: Path) -> None:
        f = tmp_path / "x.java"
        f.write_text("class X {}\n")
        assert _is_dead_call_site(str(f), 1) is False

    def test_unregistered_extension_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / "x.xyz"
        f.write_text("something\n")
        assert _is_dead_call_site(str(f), 1) is False

    def test_detector_exception_returns_false(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A detector that raises is caught; result is False (live)."""
        def _boom(fp: str, ln: int) -> bool:
            raise RuntimeError("boom")

        monkeypatch.setitem(dead_code._DETECTORS, ".boom", _boom)
        f = tmp_path / "x.boom"
        f.write_text("data\n")
        assert _is_dead_call_site(str(f), 1) is False


# ---------------------------------------------------------------------------
# TestLiveCallers
# ---------------------------------------------------------------------------

class TestLiveCallers:
    """_live_callers returns only callers NOT inside dead code."""

    def test_filters_dead_keeps_live(self, tmp_path: Path) -> None:
        # Create fixture source files.
        # Caller 1: Python inside if TYPE_CHECKING (dead).
        py_dead_tc = tmp_path / "dead_tc.py"
        py_dead_tc.write_text(_PY_TYPE_CHECKING)

        # Caller 2: Python inside if False (dead).
        py_dead_false = tmp_path / "dead_false.py"
        py_dead_false.write_text(_PY_IF_FALSE)

        # Caller 3: C inside #if 0 (dead).
        c_dead = tmp_path / "dead.c"
        c_dead.write_text(_C_IF0)

        # Caller 4: Python live code.
        py_live = tmp_path / "live.py"
        py_live.write_text(
            "from target_mod import target_fn\n"
            "\n"
            "def caller():\n"
            "    target_fn()\n",
        )

        # Build graph.db with 4 callers + edges.
        db_path = tmp_path / "graph.db"
        _make_db(
            db_path,
            nodes=[
                # Target node
                (1, "function", "target_fn",
                 "target_mod::target_fn",
                 str(tmp_path / "target.py"), 1, 5),
                # Caller 1: TYPE_CHECKING import at line 5
                (2, "function", "dead_tc_caller",
                 "dead_tc::dead_tc_caller",
                 str(py_dead_tc), 5, 5),
                # Caller 2: if False call at line 2
                (3, "function", "dead_false_caller",
                 "dead_false::dead_false_caller",
                 str(py_dead_false), 2, 2),
                # Caller 3: C #if 0 at line 9
                (4, "function", "dead_c_caller",
                 "dead_c::dead_c_caller",
                 str(c_dead), 9, 9),
                # Caller 4: live Python at line 4
                (5, "function", "live_caller",
                 "live::live_caller",
                 str(py_live), 4, 4),
            ],
            edges=[
                # CALLS edges: each caller calls target_fn
                ("CALLS", "dead_tc::dead_tc_caller",
                 "target_fn"),
                ("CALLS", "dead_false::dead_false_caller",
                 "target_fn"),
                ("CALLS", "dead_c::dead_c_caller",
                 "target_fn"),
                ("CALLS", "live::live_caller",
                 "target_fn"),
                # IMPORTS_FROM edges for SQL disambiguation
                ("IMPORTS_FROM", "dead_tc::SomeType",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "dead_false::dead_import",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "dead_c::dead_fn",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "live::target_fn",
                 "target_mod::target_fn"),
            ],
        )

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        result = _live_callers(cursor, "target_fn", "target_mod")
        conn.close()

        assert len(result) == 1
        assert result[0].qualified == "live::live_caller"
        assert isinstance(result[0], LiveCaller)
        assert result[0].file == str(py_live)
        assert result[0].line == 4


# ---------------------------------------------------------------------------
# TestBugInject (neutralize filter -> dead callers reappear)
# ---------------------------------------------------------------------------

class TestBugInject:
    """Neutralize filter -> dead callers reappear; restore -> they vanish."""

    def test_neutralize_and_restore(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        # Same fixture as TestLiveCallers.
        py_dead_tc = tmp_path / "dead_tc.py"
        py_dead_tc.write_text(_PY_TYPE_CHECKING)
        py_dead_false = tmp_path / "dead_false.py"
        py_dead_false.write_text(_PY_IF_FALSE)
        c_dead = tmp_path / "dead.c"
        c_dead.write_text(_C_IF0)
        py_live = tmp_path / "live.py"
        py_live.write_text(
            "from target_mod import target_fn\n"
            "\n"
            "def caller():\n"
            "    target_fn()\n",
        )

        db_path = tmp_path / "graph.db"
        _make_db(
            db_path,
            nodes=[
                (1, "function", "target_fn",
                 "target_mod::target_fn",
                 str(tmp_path / "target.py"), 1, 5),
                (2, "function", "dead_tc_caller",
                 "dead_tc::dead_tc_caller",
                 str(py_dead_tc), 5, 5),
                (3, "function", "dead_false_caller",
                 "dead_false::dead_false_caller",
                 str(py_dead_false), 2, 2),
                (4, "function", "dead_c_caller",
                 "dead_c::dead_c_caller",
                 str(c_dead), 9, 9),
                (5, "function", "live_caller",
                 "live::live_caller",
                 str(py_live), 4, 4),
            ],
            edges=[
                ("CALLS", "dead_tc::dead_tc_caller", "target_fn"),
                ("CALLS", "dead_false::dead_false_caller", "target_fn"),
                ("CALLS", "dead_c::dead_c_caller", "target_fn"),
                ("CALLS", "live::live_caller", "target_fn"),
                ("IMPORTS_FROM", "dead_tc::SomeType",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "dead_false::dead_import",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "dead_c::dead_fn",
                 "target_mod::target_fn"),
                ("IMPORTS_FROM", "live::target_fn",
                 "target_mod::target_fn"),
            ],
        )

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # NEUTRALIZE: make _is_dead_call_site always return False (live).
        monkeypatch.setattr(
            dead_code, "_is_dead_call_site", lambda fp, ln: False,
        )
        result_all = _live_callers(cursor, "target_fn", "target_mod")
        # All 4 callers reappear (dead ones no longer filtered).
        assert len(result_all) == 4

        # RESTORE: undo monkeypatch.
        monkeypatch.undo()
        result_filtered = _live_callers(cursor, "target_fn", "target_mod")
        # Only 1 live caller remains.
        assert len(result_filtered) == 1
        assert result_filtered[0].qualified == "live::live_caller"

        conn.close()


# ---------------------------------------------------------------------------
# TestTreeSitterAbsent (parser-absent fail-safe)
# ---------------------------------------------------------------------------

class TestTreeSitterAbsent:
    """When tree-sitter is unavailable, .py files are treated as live."""

    def test_none_parser_returns_false(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(dead_code, "_PYTHON_PARSER", None)
        f = tmp_path / "tc.py"
        f.write_text(_PY_TYPE_CHECKING)
        # Line 5 is inside TYPE_CHECKING, but parser is None -> live.
        assert _is_dead_call_site(str(f), 5) is False


# ---------------------------------------------------------------------------
# TestElseBranchLive
# ---------------------------------------------------------------------------

class TestElseBranchLive:
    """Lines in else branches are LIVE, not dead."""

    def test_python_else_branch_is_live(self, tmp_path: Path) -> None:
        f = tmp_path / "else.py"
        f.write_text(_PY_ELSE_BRANCH)
        # Line 6 is "from live_module import LiveType" in the else branch.
        assert _is_dead_call_site(str(f), 6) is False

    def test_python_if_branch_still_dead(self, tmp_path: Path) -> None:
        f = tmp_path / "else.py"
        f.write_text(_PY_ELSE_BRANCH)
        # Line 4 is "from dead_module import DeadType" in the if branch.
        assert _is_dead_call_site(str(f), 4) is True

    def test_c_else_branch_is_live(self, tmp_path: Path) -> None:
        f = tmp_path / "else.c"
        f.write_text(_C_ELSE_BRANCH)
        # Line 4 is "void live_code(void) {}" in the #else branch.
        assert _is_dead_call_site(str(f), 4) is False

    def test_c_if0_branch_still_dead(self, tmp_path: Path) -> None:
        f = tmp_path / "else.c"
        f.write_text(_C_ELSE_BRANCH)
        # Line 2 is "void dead_code(void) {}" inside #if 0.
        assert _is_dead_call_site(str(f), 2) is True


# ---------------------------------------------------------------------------
# TestHonestCeiling (docstring documents known limitations)
# ---------------------------------------------------------------------------

class TestHonestCeiling:
    """Module docstring documents honest limitations."""

    def test_docstring_mentions_cheap(self) -> None:
        assert "cheap" in dead_code.__doc__

    def test_docstring_mentions_not_general_reachability(self) -> None:
        assert "not general reachability" in dead_code.__doc__

    def test_docstring_mentions_build_config(self) -> None:
        assert "build-config-dependent" in dead_code.__doc__

    def test_docstring_mentions_unregistered(self) -> None:
        assert "without a registered detector" in dead_code.__doc__


# ---------------------------------------------------------------------------
# TestDetectorDispatch (extension-based dispatch)
# ---------------------------------------------------------------------------

class TestDetectorDispatch:
    """_DETECTORS dict contains expected entries; unknown ext returns False."""

    def test_py_registered(self) -> None:
        # .py is registered only when tree-sitter compiled.
        if dead_code._PYTHON_PARSER is not None:
            assert ".py" in dead_code._DETECTORS

    def test_c_registered(self) -> None:
        assert ".c" in dead_code._DETECTORS

    def test_h_registered(self) -> None:
        assert ".h" in dead_code._DETECTORS

    def test_unknown_ext_returns_false(self, tmp_path: Path) -> None:
        f = tmp_path / "x.zzz"
        f.write_text("data\n")
        assert _is_dead_call_site(str(f), 1) is False


# ---------------------------------------------------------------------------
# TestRealPathSmoke (forge golden rule #3)
# ---------------------------------------------------------------------------

_MACHINE_PY = Path(__file__).parents[1] / "src/code_forge/machine.py"
_GRAPH_DB = Path(__file__).parents[1] / ".code-review-graph/graph.db"


class TestRealPathSmoke:
    """Two-level real-path test on forge's own codebase."""

    @pytest.mark.skipif(
        not _MACHINE_PY.exists(),
        reason="machine.py not found (CI portability)",
    )
    def test_detector_on_real_source(self) -> None:
        """machine.py line 32 is inside if TYPE_CHECKING -- detector says dead.

        Line 32 creates an IMPORTS_FROM edge, not a CALLS edge, so the
        advisory axes do not surface it.  This test validates the
        DETECTOR, not the full pipeline.
        """
        # Line 32 is inside if TYPE_CHECKING: block.
        assert _is_dead_call_site(str(_MACHINE_PY), 32) is True
        # Line 1 (SPDX comment) is live code.
        assert _is_dead_call_site(str(_MACHINE_PY), 1) is False

    @pytest.mark.skipif(
        not _GRAPH_DB.exists(),
        reason="graph.db not found (CI portability)",
    )
    def test_pipeline_no_crash_on_real_db(self) -> None:
        """Open real graph.db, pick a target with CALLS edges, run pipeline.

        Validates the SQL + resolution + liveness-check pipeline on real
        data without asserting specific filtering (no observed CALLS-edge
        dead-code FPs in forge's current graph.db).
        """
        conn = sqlite3.connect(
            "file:%s?mode=ro" % str(_GRAPH_DB), uri=True,
        )
        cursor = conn.cursor()

        # Find a target with at least 2 CALLS edges.
        cursor.execute(
            "SELECT target_qualified, COUNT(*) AS cnt "
            "FROM edges WHERE kind = 'CALLS' "
            "GROUP BY target_qualified HAVING cnt >= 2 "
            "LIMIT 1",
        )
        row = cursor.fetchone()
        if row is None:
            conn.close()
            pytest.skip("no suitable CALLS target in graph.db")

        target_qualified = row[0]
        # Extract name and module from qualified_name (module::name).
        parts = target_qualified.split("::")
        name = parts[-1]
        module = parts[0] if len(parts) > 1 else name

        result = _live_callers(cursor, name, module)
        conn.close()

        # Pipeline must not crash and must return a list of LiveCaller.
        # The list may be empty if the CALLS+IMPORTS_FROM SQL finds no
        # matching callers (the query requires both edge kinds).  The
        # point of this test is no-crash, not non-empty.
        assert isinstance(result, list)
        assert all(isinstance(lc, LiveCaller) for lc in result)


# ---------------------------------------------------------------------------
# TestNoSqlDuplication (SQL lives only in dead_code.py)
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).parents[1] / "src" / "code_forge"


class TestNoSqlDuplication:
    """CALLS+IMPORTS_FROM SQL lives only in dead_code.py, not in callers."""

    def test_cross_repo_impact_has_no_inline_sql(self) -> None:
        source = (_SRC_DIR / "cross_repo_impact.py").read_text(
            encoding="utf-8",
        )
        assert "c.kind = 'CALLS'" not in source

    def test_graph_triage_has_no_inline_sql(self) -> None:
        source = (_SRC_DIR / "graph_triage.py").read_text(
            encoding="utf-8",
        )
        assert "c.kind = 'CALLS'" not in source

    def test_dead_code_owns_the_sql(self) -> None:
        source = (_SRC_DIR / "dead_code.py").read_text(encoding="utf-8")
        assert "c.kind = 'CALLS'" in source
