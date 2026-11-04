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
from pathlib import Path, PurePosixPath, PureWindowsPath

from ._sqlite_uri import readonly_uri
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


class GraphIdentityError(ValueError):
    """The graph cannot establish a path in the requested repository."""


def _graph_roots(cursor: sqlite3.Cursor, repo_root: Path | None) -> tuple[Path, ...]:
    """Bind graph metadata to the caller's root, never the database directory.

    Graphs may live in an external data directory. Older graphs without root
    metadata are unavailable until rebuilt; a filename suffix is not identity.
    """
    if repo_root is None:
        raise GraphIdentityError("explicit repository root required")
    try:
        rows = cursor.execute("SELECT value FROM metadata WHERE key = 'repo_root'").fetchall()
    except sqlite3.Error as exc:
        raise GraphIdentityError("graph root metadata unavailable; rebuild the graph") from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise GraphIdentityError("graph root metadata missing or ambiguous; rebuild the graph")
    recorded = Path(rows[0][0])
    if not recorded.is_absolute() or "\0" in rows[0][0] or ".." in recorded.parts:
        raise GraphIdentityError("invalid graph root metadata; rebuild the graph")
    root = Path(repo_root).absolute()
    try:
        resolved = root.resolve(strict=True)
        recorded_resolved = recorded.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise GraphIdentityError("graph repository root unavailable") from exc
    if not resolved.is_dir() or recorded_resolved != resolved:
        raise GraphIdentityError("graph belongs to a different repository root; rebuild the graph")
    return root, resolved, recorded


def _relative_graph_path(file_path: str) -> PurePosixPath:
    """Accept a literal repository-relative POSIX path, not a glob or escape."""
    if (
        not isinstance(file_path, str)
        or not file_path
        or "\0" in file_path
        or "\\" in file_path
        or PurePosixPath(file_path).is_absolute()
        or PureWindowsPath(file_path).drive
        or any(part in {"", ".", ".."} for part in file_path.split("/"))
    ):
        raise GraphIdentityError("invalid repository-relative graph path")
    return PurePosixPath(file_path)


def _path_identities(file_path: str, roots: tuple[Path, ...]) -> tuple[str, ...]:
    relative = _relative_graph_path(file_path)
    try:
        resolved = (roots[0] / relative).resolve()
        if not resolved.is_relative_to(roots[1]):
            raise GraphIdentityError("graph path leaves the repository root")
    except (OSError, RuntimeError, ValueError) as exc:
        raise GraphIdentityError("graph path cannot be bound to the repository root") from exc
    return relative.as_posix(), *((r / relative).as_posix() for r in roots)


def _caller_source_path(file_path: str | None, roots: tuple[Path, ...], cursor: sqlite3.Cursor) -> str:
    if not isinstance(file_path, str) or file_path == "<unknown>":
        raise GraphIdentityError("caller graph path unavailable")
    path = Path(file_path)
    relative = file_path
    if path.is_absolute():
        # The stored spelling must belong to one of the bound roots. Merely
        # resolving a foreign checkout's symlink into this root is not enough.
        for root in roots:
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                break
        else:
            raise GraphIdentityError("caller graph path outside its repository root")
    identities = _path_identities(relative, roots)
    if path.is_absolute() and file_path not in identities:
        raise GraphIdentityError("caller graph path outside its repository root")
    spellings = cursor.execute(
        "SELECT DISTINCT file_path COLLATE BINARY FROM nodes "
        "WHERE file_path COLLATE BINARY IN (?, ?, ?, ?)",
        identities,
    ).fetchall()
    if len(spellings) > 1:
        raise GraphIdentityError("ambiguous caller graph file identity; rebuild the graph")
    # Do not let the shared liveness reader interpret a relative path against
    # the process CWD. The graph identity returned to callers stays unchanged.
    try:
        source_path = (roots[0] / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise GraphIdentityError("caller source path unavailable") from exc
    if not source_path.is_relative_to(roots[1]):
        raise GraphIdentityError("caller source path leaves its repository root")
    return str(source_path)


def resolve_changed_symbols(
    diff_text: str,
    primary_db: str,
    repo_root: Path | None = None,
) -> list[dict]:
    """Resolve changed symbols from the diff against the primary graph.db.

    Parses '+++ b/<file>' paths from the diff, then queries the primary
    graph.db for named nodes in those files.

    Args:
        diff_text: unified diff string.
        primary_db: path to the primary repo's graph.db.
        repo_root: explicit reviewed root; required for any nonempty diff.

    Returns:
        List of dicts with keys: name, qualified_name, file_path, module.
        Unnamed nodes (module-level, lines ...) are excluded.
    """
    diff_files = _parse_diff_files(diff_text)
    if not diff_files:
        return []

    results: list[dict] = []
    conn = sqlite3.connect(readonly_uri(Path(primary_db).absolute()), uri=True)
    try:
        cursor = conn.cursor()
        roots = _graph_roots(cursor, repo_root)
        for file_path in diff_files:
            identities = _path_identities(file_path, roots)
            cursor.execute(
                "SELECT name, qualified_name, file_path FROM nodes "
                "WHERE file_path COLLATE BINARY IN (?, ?, ?, ?)",
                identities,
            )
            nodes = cursor.fetchall()
            if len({node[2] for node in nodes}) > 1:
                raise GraphIdentityError("ambiguous graph file identity; rebuild the graph")
            for name, qualified_name, node_file in nodes:
                if _is_unnamed(name):
                    continue
                results.append(
                    {
                        "name": name,
                        "qualified_name": qualified_name,
                        "file_path": node_file,
                        "module": Path(file_path).stem,
                    }
                )
    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# Cross-repo caller discovery
# ---------------------------------------------------------------------------


def find_cross_repo_callers(
    sibling_db: str,
    changed: list[dict],
    repo_root: Path | None = None,
) -> list[dict]:
    """Find direct callers of changed symbols in a sibling's graph.db.

    Runs the same CALLS + IMPORTS_FROM disambiguation query that
    graph_triage._run_graphdb uses, against the sibling's database.

    Args:
        sibling_db: path to the sibling repo's graph.db.
        changed: list of changed symbol dicts from resolve_changed_symbols.
        repo_root: explicit sibling root, independent of its database location.

    Returns:
        List of caller dicts with keys: symbol, caller_qualified,
        caller_file, caller_line.

    Raises:
        sqlite3.Error: if the database is corrupt or unreadable.
        OSError: if the file cannot be opened.
    """
    results: list[dict] = []
    conn = sqlite3.connect(readonly_uri(Path(sibling_db).absolute()), uri=True)
    try:
        cursor = conn.cursor()
        roots = _graph_roots(cursor, repo_root) if changed else ()
        for sym in changed:
            name = sym["name"]
            module_name = sym["module"]

            live = _live_callers(
                cursor,
                name,
                module_name,
                resolve_file=lambda path: _caller_source_path(path, roots, conn.cursor()),
            )
            results.extend(
                (
                    {
                        "symbol": name,
                        "caller_qualified": lc.qualified,
                        "caller_file": lc.file,
                        "caller_line": lc.line,
                    }
                )
                for lc in live
            )
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

    Satisfies the AdvisoryAxisRunner Protocol (is_advisory=True).
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
                "cross-repo-impact: primary graph.db not found; build code-review-graph"
            )
            return []

        # Resolve changed symbols from the primary db.
        try:
            changed = resolve_changed_symbols(diff_text, str(primary_db), repo_root)
        except (sqlite3.Error, OSError, GraphIdentityError) as exc:
            self.infra_errors.append("cross-repo-impact: primary graph.db unreadable: %s" % exc)
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
        siblings = [r for r in repos if Path(r["path"]).resolve() != resolved_root]

        if not siblings:
            self.infra_errors.append(
                "cross-repo-impact: no sibling repos registered; use code-review-graph register"
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
                self.infra_errors.append("cross-repo-impact: sibling '%s' graph.db missing" % alias)
                continue

            try:
                callers = find_cross_repo_callers(str(sib_db), changed, Path(sib["path"]))
            except (sqlite3.Error, OSError, GraphIdentityError) as exc:
                self.infra_errors.append(
                    "cross-repo-impact: sibling '%s' graph.db unreadable: %s" % (alias, exc)
                )
                continue

            sib_prefix = str(Path(sib["path"]).resolve()) + os.sep
            for sym in changed:
                cfp = sym["file_path"]
                if cfp.startswith(primary_prefix):
                    cfp = cfp[len(primary_prefix) :]
                elif os.path.isabs(cfp):
                    resolved = str(Path(cfp).resolve())
                    if resolved.startswith(primary_prefix):
                        cfp = resolved[len(primary_prefix) :]
                sym_callers = [c for c in callers if c["symbol"] == sym["name"]]
                for c in sym_callers:
                    cf = c["caller_file"]
                    if cf.startswith(sib_prefix):
                        cf = cf[len(sib_prefix) :]
                    elif os.path.isabs(cf):
                        resolved = str(Path(cf).resolve())
                        if resolved.startswith(sib_prefix):
                            cf = resolved[len(sib_prefix) :]
                    hits.append(
                        {
                            **c,
                            "caller_file": cf,
                            "alias": alias,
                            "changed_file_path": cfp,
                        }
                    )

        # Rank by subsystem proximity (closer = higher rank).
        hits.sort(
            key=lambda h: _subsystem_proximity(
                h["caller_file"],
                h["changed_file_path"],
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

            findings.append(
                AdvisoryFinding(
                    id="cross-repo-impact-%d" % (i + 1),
                    axis=_AXIS,
                    file="%s:%s" % (alias, caller_file),
                    line_range=(caller_line, caller_line),
                    description="%s used by %s at %s:%d"
                    % (
                        symbol,
                        alias,
                        caller_file,
                        caller_line,
                    ),
                    attribution="cross-repo graph.db",
                )
            )

        self._cached = findings
        return findings
