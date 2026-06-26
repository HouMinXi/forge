# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared liveness-filtering for advisory axes.

Both cross-repo-impact and graph-triage surface callers from graph.db.
Some of those callers sit inside statically dead code -- ``if
TYPE_CHECKING:``, ``if False:``, version guards that are False on the
running interpreter, or C ``#if 0`` blocks.  This module provides two
helpers that let the advisory axes skip those dead callers before they
become findings.

**Honest ceiling (what this filter does NOT catch):**

The filter catches common, cheap-to-detect idioms only:

* Python: ``if TYPE_CHECKING:``, ``if False:``, simple
  ``sys.version_info`` comparisons whose guard evaluates to False on the
  running interpreter.
* C: ``#if 0`` blocks (lexical nesting count).

It is not general reachability analysis -- that is undecidable per Rice's
theorem.  Known limitations:

* C ``#ifdef MACRO`` is build-config-dependent and is not evaluated.
* ``#else`` / ``#elif`` branches of ``#if 0`` are conservatively treated
  as live (miss-not-noise direction).
* Deep or unusual preprocessor nesting may produce false negatives.
* Any language without a registered detector is treated as live.

A missed dead caller is a tolerable residual false positive; a
wrongly-dropped live caller is a silent false negative -- worse.  Every
detector returns ``False`` (live) on any doubt.
"""

from __future__ import annotations

import ast
import operator
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# tree-sitter import -- guarded per D-06 fail-safe
# ---------------------------------------------------------------------------

_PYTHON_PARSER = None

try:
    from tree_sitter_language_pack import get_parser as _ts_get_parser

    _PYTHON_PARSER = _ts_get_parser("python")
except Exception:  # ImportError, OSError, or build failure
    pass


# ---------------------------------------------------------------------------
# LiveCaller frozen dataclass (D-03)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveCaller:
    """A caller confirmed as not inside dead code."""

    qualified: str
    file: str
    line: int | None


# ---------------------------------------------------------------------------
# Python dead-code detection helpers
# ---------------------------------------------------------------------------

_DEAD_CONDITIONS = frozenset({b"TYPE_CHECKING", b"False"})

_VERINFO_RE = re.compile(
    rb"sys\.version_info\s*(<=|>=|==|!=|<|>)\s*(\([^)]*\))",
)
_CMP: dict[bytes, Callable] = {
    b"<": operator.lt,
    b"<=": operator.le,
    b">": operator.gt,
    b">=": operator.ge,
    b"==": operator.eq,
    b"!=": operator.ne,
}


def _verinfo_is_dead(cond_text: bytes) -> bool:
    """True iff a SIMPLE sys.version_info guard is False on this interpreter.

    Compound guards (``and`` / ``or``) are fail-safe live per D-06 -- a
    blanket flag would wrongly drop live callers.
    """
    if b" and " in cond_text or b" or " in cond_text:
        return False
    m = _VERINFO_RE.search(cond_text)
    if not m:
        return False
    try:
        ver = ast.literal_eval(m.group(2).decode())
        if not isinstance(ver, tuple):
            return False
        guard_true = _CMP[m.group(1)](sys.version_info, ver)
    except Exception:
        return False
    return not guard_true


def _find_deepest_at_line(node, target_line: int):
    """Find the deepest tree-sitter node containing *target_line* (0-indexed)."""
    for child in node.children:
        if child.start_point[0] <= target_line <= child.end_point[0]:
            result = _find_deepest_at_line(child, target_line)
            if result is not None:
                return result
            return child
    return None


def _in_consequence(if_node, target_line: int) -> bool:
    """Return True if *target_line* is inside the consequence (if-body).

    The consequence is the ``block`` child that follows the condition.
    A line in the ``else`` branch is LIVE, not dead.
    """
    # if_statement children: "if", condition, ":", block, [else_clause]
    # Find the first block child -- that is the consequence.
    for child in if_node.children:
        if child.type == "block":
            if child.start_point[0] <= target_line <= child.end_point[0]:
                return True
            # Found the consequence block but line is not inside it.
            return False
    return False


def _is_dead_python(file_path: str, line: int) -> bool:
    """Return True if *line* is inside a dead-code guard in a Python file.

    Uses tree-sitter AST ancestor walk.  Returns False on any error
    (fail-safe = live per D-06).
    """
    if _PYTHON_PARSER is None:
        return False
    try:
        source = Path(file_path).read_bytes()
    except OSError:
        return False

    tree = _PYTHON_PARSER.parse(source)
    target_line = line - 1  # tree-sitter is 0-indexed

    node = _find_deepest_at_line(tree.root_node, target_line)
    if node is None:
        return False

    while node is not None:
        if node.type == "if_statement" and len(node.children) > 1:
            cond_text = node.children[1].text
            is_dead_cond = False
            if cond_text in _DEAD_CONDITIONS:
                is_dead_cond = True
            elif (
                b"sys.version_info" in cond_text
                and _verinfo_is_dead(cond_text)
            ):
                is_dead_cond = True

            if is_dead_cond and _in_consequence(node, target_line):
                return True
        node = node.parent
    return False


# ---------------------------------------------------------------------------
# C dead-code detection (lexical scan)
# ---------------------------------------------------------------------------

_IF0_RE = re.compile(r"^\s*#\s*if\s+0\b")
_IF_RE = re.compile(r"^\s*#\s*if\b")
_ENDIF_RE = re.compile(r"^\s*#\s*endif\b")
_ELSE_RE = re.compile(r"^\s*#\s*(?:else|elif)\b")


def _is_dead_c(file_path: str, line: int) -> bool:
    """Return True if *line* is inside ``#if 0 ... #endif`` in a C/H file.

    Lexical scan walking upward from target line.  Returns False on any
    error (fail-safe = live per D-06).

    Known limitation: ``#else`` / ``#elif`` branches of ``#if 0`` are
    conservatively treated as live -- the ``#else`` reset at depth==0
    fires before reaching ``#if 0``.  This is the miss-not-noise
    direction per D-06.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return False

    depth = 0
    for i in range(min(line - 1, len(lines)) - 1, -1, -1):
        text = lines[i]
        if _ENDIF_RE.match(text):
            depth += 1
        elif _ELSE_RE.match(text):
            if depth == 0:
                return False
        elif _IF0_RE.match(text):
            if depth == 0:
                return True
            depth -= 1
        elif _IF_RE.match(text):
            if depth == 0:
                return False
            depth -= 1
    return False


# ---------------------------------------------------------------------------
# Detector dispatch (D-11)
# ---------------------------------------------------------------------------

_DETECTORS: dict[str, Callable[[str, int], bool]] = {}

# Seed Python detector only when tree-sitter compiled successfully.
if _PYTHON_PARSER is not None:
    _DETECTORS[".py"] = _is_dead_python

_DETECTORS[".c"] = _is_dead_c
_DETECTORS[".h"] = _is_dead_c


def _is_dead_call_site(file_path: str | None, line: int | None) -> bool:
    """Return True if the call site at *file_path*:*line* is inside dead code.

    Dispatches to a language-specific detector via ``_DETECTORS`` by file
    extension.  Returns ``False`` (fail-safe = live) when:

    * *file_path* is None
    * *line* is None
    * file extension has no registered detector
    * the detector raises any exception

    **Adding a language:** register a new detector in ``_DETECTORS`` only
    with (a) an OBSERVED graph.db false positive and (b) a sound per-line
    detector for the real dead-code idiom.  The detector MUST return
    ``False`` on any doubt.
    """
    if file_path is None or line is None:
        return False
    try:
        ext = Path(file_path).suffix
        detector = _DETECTORS.get(ext)
        if detector is None:
            return False
        return detector(file_path, line)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# _live_callers: shared SQL + liveness filter (D-02, D-08)
# ---------------------------------------------------------------------------

_CALLERS_SQL = (
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
    ")"
)


def _live_callers(
    cursor: sqlite3.Cursor,
    target_name: str,
    module_name: str,
) -> list[LiveCaller]:
    """Query callers via CALLS+IMPORTS_FROM, resolve file:line, filter dead.

    Runs the shared SQL query (extracted from cross_repo_impact.py and
    graph_triage.py), resolves each caller's file_path and line_start
    from the nodes table, and drops callers inside dead code.

    Args:
        cursor: open sqlite3 cursor on a graph.db.
        target_name: the function/class name being called.
        module_name: the module stem for IMPORTS_FROM disambiguation.

    Returns:
        List of LiveCaller objects for callers NOT inside dead code.
    """
    cursor.execute(_CALLERS_SQL, (target_name, module_name, target_name))
    callers = cursor.fetchall()

    result: list[LiveCaller] = []
    for (caller_qualified,) in callers:
        cursor.execute(
            "SELECT file_path, line_start FROM nodes "
            "WHERE qualified_name = ?",
            (caller_qualified,),
        )
        row = cursor.fetchone()
        caller_file = row[0] if row else None
        caller_line = row[1] if row else None

        if _is_dead_call_site(caller_file, caller_line):
            continue

        result.append(LiveCaller(
            qualified=caller_qualified,
            file=caller_file or "<unknown>",
            line=caller_line,
        ))
    return result
