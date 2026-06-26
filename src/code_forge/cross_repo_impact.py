# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CrossRepoImpactRunner advisory axis: cross-repo direct-caller impact.

When the primary repo changes a symbol, this runner enumerates sibling
repos registered in the code-review-graph registry, queries each
sibling's graph.db for DIRECT call sites (CALLS edges) of the changed
symbol, and surfaces advisory findings naming the sibling call site.

Purely deterministic (SQLite only): no LLM call, no new pip dependency.
Mirrors graph_triage.py patterns for diff parsing, unnamed filtering,
CALLS+IMPORTS_FROM disambiguation, and infra_errors SKIP signaling.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from .advisory import AdvisoryFinding

# Reuse helpers from graph_triage -- never duplicate logic.
from .graph_triage import _is_unnamed, _parse_diff_files

from .dead_code import _live_callers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AXIS = "CROSS-REPO-IMPACT"

_TOP_N: int = 10
"""Max findings emitted (depth-1, capped)."""


# ---------------------------------------------------------------------------
# Registry path seam
# ---------------------------------------------------------------------------

def _registry_path() -> str | None:
    """Return CRG_REGISTRY_PATH env var if set, else None (default).

    Forge-side test seam, mirrors graph_triage CRG_DB_PATH.
    """
    return os.environ.get("CRG_REGISTRY_PATH") or None


# ---------------------------------------------------------------------------
# Symbol resolution from primary graph.db
# ---------------------------------------------------------------------------

def resolve_changed_symbols(
    diff_text: str,
    primary_db: str,
) -> list[dict]:
    """Resolve changed symbols from the diff against the primary graph.db.

    Parses '+++ b/<file>' paths from the diff, then queries the primary
    graph.db for named nodes in those files.

    Args:
        diff_text: unified diff string.
        primary_db: path to the primary repo's graph.db.

    Returns:
        List of dicts with keys: name, qualified_name, file_path, module.
        Unnamed nodes (module-level, lines ...) are excluded.
    """
    diff_files = _parse_diff_files(diff_text)
    if not diff_files:
        return []

    results: list[dict] = []
    conn = sqlite3.connect("file:%s?mode=ro" % primary_db, uri=True)
    try:
        cursor = conn.cursor()
        for file_path in diff_files:
            cursor.execute(
                "SELECT name, qualified_name, file_path "
                "FROM nodes WHERE file_path LIKE ?",
                ("%%%s" % file_path,),
            )
            for name, qualified_name, node_file in cursor.fetchall():
                if _is_unnamed(name):
                    continue
                results.append({
                    "name": name,
                    "qualified_name": qualified_name,
                    "file_path": node_file,
                    "module": Path(file_path).stem,
                })
    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# Cross-repo caller discovery
# ---------------------------------------------------------------------------

def find_cross_repo_callers(
    sibling_db: str,
    changed: list[dict],
) -> list[dict]:
    """Find direct callers of changed symbols in a sibling's graph.db.

    Runs the same CALLS + IMPORTS_FROM disambiguation query that
    graph_triage._run_graphdb uses, against the sibling's database.

    Args:
        sibling_db: path to the sibling repo's graph.db.
        changed: list of changed symbol dicts from resolve_changed_symbols.

    Returns:
        List of caller dicts with keys: symbol, caller_qualified,
        caller_file, caller_line.

    Raises:
        sqlite3.Error: if the database is corrupt or unreadable.
        OSError: if the file cannot be opened.
    """
    results: list[dict] = []
    conn = sqlite3.connect("file:%s?mode=ro" % sibling_db, uri=True)
    try:
        cursor = conn.cursor()
        for sym in changed:
            name = sym["name"]
            module_name = sym["module"]

            live = _live_callers(cursor, name, module_name)
            for lc in live:
                results.append({
                    "symbol": name,
                    "caller_qualified": lc.qualified,
                    "caller_file": lc.file,
                    "caller_line": lc.line,
                })
    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# Subsystem proximity
# ---------------------------------------------------------------------------

def _subsystem_proximity(caller_file: str, changed_file: str) -> float:
    """Compute token-set overlap between two file paths.

    Uses Jaccard similarity over all directory path segments (excluding
    the filename). Token-set overlap, NOT first-two-segments prefix.

    drivers/net/foo.c vs net/core/bar.c share "net" -> score > 0.
    """
    caller_parts = set(Path(caller_file).parts[:-1])  # dirs only
    changed_parts = set(Path(changed_file).parts[:-1])

    if not caller_parts and not changed_parts:
        return 0.0

    union = caller_parts | changed_parts
    intersection = caller_parts & changed_parts
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# CrossRepoImpactRunner
# ---------------------------------------------------------------------------

class CrossRepoImpactRunner:
    """Advisory axis: cross-repo direct-caller impact (R0).

    Satisfies the AxisRunner Protocol (is_advisory=True).
    Discovers sibling repos via code-review-graph Registry,
    queries each sibling's graph.db for direct callers of
    changed symbols, and surfaces findings.
    """

    def __init__(self) -> None:
        self.infra_errors: list[str] = []
        self._cached: list[AdvisoryFinding] | None = None

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run cross-repo impact analysis on the given diff.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding from cross-repo caller analysis.
            Empty list on SKIP (with infra_errors populated).
        """
        if self._cached is not None:
            return self._cached

        self.infra_errors.clear()

        if not diff_text or not diff_text.strip():
            return []

        # Locate primary graph.db via canonical get_db_path.
        from code_review_graph.incremental import get_db_path
        primary_db = get_db_path(repo_root)
        if not primary_db.is_file():
            self.infra_errors.append(
                "cross-repo-impact: primary graph.db not found; "
                "build code-review-graph"
            )
            return []

        # Resolve changed symbols from the primary db.
        try:
            changed = resolve_changed_symbols(diff_text, str(primary_db))
        except (sqlite3.Error, OSError) as exc:
            self.infra_errors.append(
                "cross-repo-impact: primary graph.db unreadable: %s" % exc
            )
            return []
        if not changed:
            return []

        # Enumerate sibling repos via Registry.
        from code_review_graph.registry import Registry
        reg_path_str = _registry_path()
        reg_path_arg = Path(reg_path_str) if reg_path_str else None
        registry = Registry(path=reg_path_arg)
        repos = registry.list_repos()

        resolved_root = repo_root.resolve()
        siblings = [
            r for r in repos
            if Path(r["path"]).resolve() != resolved_root
        ]

        if not siblings:
            self.infra_errors.append(
                "cross-repo-impact: no sibling repos registered; "
                "use code-review-graph register"
            )
            return []

        # Query each sibling for callers of changed symbols.
        # Prefix for stripping absolute paths to repo-relative.
        # Tool-built graph.db stores absolute file_path; hand-built
        # fixtures use relative -- the startswith guard handles both.
        # When a repo is registered via symlink but graph.db stores
        # the realpath (or vice versa), the first startswith fails;
        # the fallback resolves the file path and retries.
        primary_prefix = str(resolved_root) + os.sep
        hits: list[dict] = []
        for sib in siblings:
            alias = sib.get("alias") or Path(sib["path"]).name
            sib_db = get_db_path(Path(sib["path"]))

            if not sib_db.is_file():
                self.infra_errors.append(
                    "cross-repo-impact: sibling '%s' graph.db missing"
                    % alias
                )
                continue

            try:
                callers = find_cross_repo_callers(str(sib_db), changed)
            except (sqlite3.Error, OSError) as exc:
                self.infra_errors.append(
                    "cross-repo-impact: sibling '%s' graph.db "
                    "unreadable: %s" % (alias, exc)
                )
                continue

            sib_prefix = str(Path(sib["path"]).resolve()) + os.sep
            for sym in changed:
                cfp = sym["file_path"]
                if cfp.startswith(primary_prefix):
                    cfp = cfp[len(primary_prefix):]
                elif os.path.isabs(cfp):
                    resolved = str(Path(cfp).resolve())
                    if resolved.startswith(primary_prefix):
                        cfp = resolved[len(primary_prefix):]
                sym_callers = [
                    c for c in callers if c["symbol"] == sym["name"]
                ]
                for c in sym_callers:
                    cf = c["caller_file"]
                    if cf.startswith(sib_prefix):
                        cf = cf[len(sib_prefix):]
                    elif os.path.isabs(cf):
                        resolved = str(Path(cf).resolve())
                        if resolved.startswith(sib_prefix):
                            cf = resolved[len(sib_prefix):]
                    hits.append({
                        **c,
                        "caller_file": cf,
                        "alias": alias,
                        "changed_file_path": cfp,
                    })

        # Rank by subsystem proximity (closer = higher rank).
        hits.sort(
            key=lambda h: _subsystem_proximity(
                h["caller_file"], h["changed_file_path"],
            ),
            reverse=True,
        )

        # Build AdvisoryFinding per hit, capped at _TOP_N.
        findings: list[AdvisoryFinding] = []
        for i, hit in enumerate(hits[:_TOP_N]):
            alias = hit["alias"]
            caller_file = hit["caller_file"]
            caller_line = hit.get("caller_line") or 0
            symbol = hit["symbol"]

            findings.append(AdvisoryFinding(
                id="cross-repo-impact-%d" % (i + 1),
                axis=_AXIS,
                file="%s:%s" % (alias, caller_file),
                line_range=[caller_line, caller_line],
                description="%s used by %s at %s:%d" % (
                    symbol, alias, caller_file, caller_line,
                ),
                attribution="cross-repo graph.db",
            ))

        self._cached = findings
        return findings
