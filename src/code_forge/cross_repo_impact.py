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

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath

from ._sqlite_uri import readonly_uri
from .advisory import AdvisoryFinding

# Reuse helpers from graph_triage -- never duplicate logic.
from .graph_triage import _is_unnamed, _parse_diff_files

from .dead_code import _live_callers, _source_opener

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AXIS = "CROSS-REPO-IMPACT"

_NO_NAMED_TARGETS = (
    "cross-repo-impact: no named changed symbols resolved; direct-caller impact not evaluated"
)

_SCOPE_UNAVAILABLE = (
    "cross-repo-impact: registry scope unavailable; direct-caller impact not evaluated. "
    "Registered query scope was not established."
)
_EMPTY_SCOPE = (
    "cross-repo-impact: no sibling registry entries; direct-caller impact not evaluated. "
    "Repositories outside this registered scope were not searched for direct callers."
)
_QUERY_SUMMARY = (
    "cross-repo-impact: direct-caller queries completed for %d of %d registered sibling entries "
    "(%d attempted; %d unavailable). Entries may repeat repositories. "
    "Repositories outside this registered scope were not searched for direct callers. "
    "Graph freshness and full impact coverage are not established."
)

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


class GraphDiscoveryError(ValueError):
    """The registry cannot supply a supported read-only discovery snapshot."""


def _read_registry(path: Path) -> list[dict]:
    """Read the provider's repos JSON without its directory-creating constructor.

    Reject nonregular opened files without waiting for a FIFO writer.
    Open directly: if the file disappears, there is nothing to recreate. A
    malformed or unreadable registry is unavailable, not permission to ignore
    its configured graph location and select another database.
    """
    try:
        with open(path, "r", encoding="utf-8", opener=_source_opener) as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as exc:
        raise GraphDiscoveryError("registry unreadable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise GraphDiscoveryError("unsupported registry structure")
    repos = data["repos"]
    for entry in repos:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not entry["path"]
            or "\0" in entry["path"]
            or any(
                key in entry and (not isinstance(entry[key], str) or "\0" in entry[key])
                for key in ("alias", "data_dir")
            )
        ):
            raise GraphDiscoveryError("unsupported registry entry")
    return repos


def _readonly_db_path(repo_root: Path, default_repos: list[dict]) -> Path:
    """Select a path using the provider's 2.3.8/2.3.9 precedence, without writes.

    Only the provider's default registry controls graph placement. Forge's
    CRG_REGISTRY_PATH override controls sibling enumeration separately. The
    first matching entry wins, even if it has no data_dir. Registered paths
    resolve literally; only the environment override expands a leading tilde.
    """
    resolved = str(repo_root.resolve())
    for entry in default_repos:
        if entry["path"] == resolved:
            if entry.get("data_dir"):
                return Path(entry["data_dir"]).resolve() / "graph.db"
            break
    override = os.environ.get("CRG_DATA_DIR", "").strip()
    data_dir = Path(override).expanduser().resolve() if override else repo_root / ".code-review-graph"
    return data_dir / "graph.db"


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


def _resolve_source_path(path: Path) -> Path:
    """Allow missing source files, but never suppress a symlink resolution loop."""
    resolved = path.resolve()
    # Newer Python does not raise for loops in non-strict resolve(). A final
    # metadata lookup retains ELOOP and other errors without requiring a
    # deleted source file to exist. No source contents are read here.
    try:
        resolved.stat()
    except FileNotFoundError:
        pass
    return resolved


def _path_identities(file_path: str, roots: tuple[Path, ...]) -> tuple[str, ...]:
    relative = _relative_graph_path(file_path)
    try:
        resolved = _resolve_source_path(roots[0] / relative)
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
        source_path = _resolve_source_path(roots[0] / relative)
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
    return [
        caller
        for _, callers in _find_cross_repo_caller_groups(sibling_db, changed, repo_root)
        for caller in callers
    ]


def _find_cross_repo_caller_groups(
    sibling_db: str,
    changed: list[dict],
    repo_root: Path | None = None,
) -> list[tuple[dict, list[dict]]]:
    """Keep each query's changed symbol paired with its matching callers.

    A name alone cannot identify a changed symbol across files. Materialize
    every group on one connection before returning, so a later query failure
    still rejects the whole sibling rather than publishing partial findings.
    """
    groups: list[tuple[dict, list[dict]]] = []
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
            callers = [
                {
                    "symbol": name,
                    "caller_qualified": lc.qualified,
                    "caller_file": lc.file,
                    "caller_line": lc.line,
                }
                for lc in live
            ]
            groups.append((sym, callers))
    finally:
        conn.close()

    return groups


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


@dataclass(frozen=True, slots=True)
class _QueryScopeSnapshot:
    """Private in-memory facts only; not a receipt, identity or freshness claim.

    Counts are registry occurrences, not distinct repositories or findings.
    Unknown scope has no counts. processing_completed describes normal return
    from native analysis, independently of fully accounted query occurrences.
    """

    scope: str = "not_reached"
    registered: int | None = None
    attempted: int | None = None
    completed: int | None = None
    unavailable: int | None = None
    with_callers: int | None = None
    # All registered occurrences have a returned helper result or handled
    # unavailability; this does not assert normal loop/analysis return.
    query_occurrences_accounted: bool = False
    processing_completed: bool = False


class CrossRepoImpactRunner:
    """Advisory axis: cross-repo direct-caller impact (R0).

    Satisfies the AdvisoryAxisRunner Protocol (is_advisory=True).
    Discovers sibling repos from code-review-graph registry snapshots,
    queries each sibling's graph.db for direct callers of
    changed symbols, and surfaces findings.
    """

    def __init__(self, *, output_fn: Callable[[str], None] | None = None, _diagnostics=None) -> None:
        self.infra_errors: list[str] = []
        self._cached: list[AdvisoryFinding] | None = None
        # Optional immediate output, not a finding or a persistent diagnostic.
        self._output_fn = output_fn
        self._diagnostics = _diagnostics
        self._installed_diagnostics = _diagnostics
        self._analysis_state = None
        self._bound_record = None
        self._bound_errors = None
        self._provider_locator = None
        self._scope_snapshot = _QueryScopeSnapshot()

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle."""
        return True

    def _emit(self, message):
        if self._output_fn is not None:
            try:
                self._output_fn(message)
            except Exception:  # noqa: BLE001 -- Best-effort output is non-gating; process control propagates.
                return False
        return True

    def _emit_scope_summary(self, immediate, diff_text, repo_root):
        """Private scope channel; never substitutes missing evidence for facts."""
        owner = self._diagnostics
        if owner is None:
            return self._emit(immediate)
        message = immediate
        try:
            scope = owner.present_scope(self._scope_snapshot, diff_text, repo_root)
            if scope is not None:
                if scope.scope == "unavailable":
                    message = _SCOPE_UNAVAILABLE
                elif scope.registered == 0:
                    message = _EMPTY_SCOPE
                else:
                    message = _QUERY_SUMMARY % (
                        scope.completed,
                        scope.registered,
                        scope.attempted,
                        scope.unavailable,
                    )
        except Exception:  # noqa: BLE001 -- Optional scope transport never erases genuine facts.
            owner.scope_delivery_status = "not_delivered"
        read = owner.scope_delivery_status == "read"
        has_sink = self._output_fn is not None
        if read and has_sink:
            owner.scope_delivery_status = "unconfirmed"
        succeeded = self._emit(message)
        if read and has_sink and succeeded:
            owner.scope_delivery_status = "delivered"
        if owner.ticket_directory is not None:
            if owner.scope_ticket_status == "exported":
                self._emit("scope-ticket: exported " + owner.scope_ticket_path.name)
            else:
                self._emit("scope-ticket: unavailable; no saved-ticket guarantee")
        return succeeded

    def _stage(self, key, operation, **kwargs):
        if self._diagnostics is None:
            return operation()
        return self._diagnostics.stage(key, operation, **kwargs)

    def _observe(self, state):
        self._analysis_state = state

    def _deliver_bound(self, record, state, *, origin="actual"):
        self._diagnostics.delivery_status = "not_delivered"
        self._diagnostics.last_delivery_origin = origin
        try:
            messages = self._diagnostics.present(record, origin=origin)
            maximum = 2 if state == "no_named_targets" else 1
            minimum = int(state == "no_named_targets")
            if (
                type(messages) is not tuple
                or not minimum <= len(messages) <= maximum
                or any(
                    type(message) is not str
                    or len(message) > 256
                    or len(message.encode("utf-8", errors="strict")) > 256
                    for message in messages
                )
            ):
                raise ValueError("invalid diagnostic presentation")
        except Exception:  # noqa: BLE001 -- A broken consumer cannot contaminate advisory State errors.
            messages = (
                (_NO_NAMED_TARGETS,) if state == "no_named_targets" else ()
            ) + self._diagnostics.warning()
        output_succeeded = True
        for message in messages:
            if not self._emit(message):
                output_succeeded = False
                for warning in self._diagnostics.warning():
                    self._emit(warning)
        if self._diagnostics.delivery_status == "read":
            self._diagnostics.delivery_status = "delivered" if output_succeeded else "unconfirmed"

    def _finish_bound(self, findings):
        state = self._analysis_state
        if state is None:
            return None
        try:
            record = self._diagnostics.finish(state, findings, self.infra_errors)
        except Exception:  # noqa: BLE001 -- A broken recorder cannot change native analysis.
            record = None
        self._deliver_bound(record, state)
        return record

    def run(self, diff_text: str, repo_root: Path) -> list[AdvisoryFinding]:
        if self._diagnostics is not self._installed_diagnostics:
            installed = self._installed_diagnostics
            if installed is not None:
                installed.refusal = "context_replaced"
                installed.delivery_status = "not_delivered"
                installed.scope_delivery_status = "not_delivered"
                installed.last_delivery_origin = None
                for message in installed.warning():
                    self._emit(message)
            return []
        if self._diagnostics is None:
            return self._run_analysis(diff_text, repo_root)
        if not self._diagnostics.admit(diff_text, repo_root):
            for message in self._diagnostics.warning():
                self._emit(message)
            return []
        if self._cached is not None:
            if self._bound_errors is not None:
                self.infra_errors[:] = self._bound_errors
            self._deliver_bound(self._bound_record, self._analysis_state, origin="cache")
            return list(self._cached)
        self._analysis_state = None
        self._bound_errors = None
        try:
            findings = self._run_analysis(diff_text, repo_root)
        except BaseException:  # noqa: BLE001 -- Preserve the native primary even when diagnostic finalization also fails.
            try:
                self._finish_bound(())
            except BaseException:  # noqa: BLE001, S110 -- The already-active native exception wins.
                pass
            raise
        self._bound_record = self._finish_bound(findings)
        # At most one active native cache error snapshot, separate from bounded
        # observation history. Unsupported large views are not claimed immutable.
        try:
            if len(self.infra_errors) <= 258 and all(
                type(e) is str and len(e) <= 4096 for e in self.infra_errors
            ):
                if sum(len(e.encode("utf-8", errors="strict")) for e in self.infra_errors) <= 65536:
                    self._bound_errors = tuple(self.infra_errors)
        except Exception:  # noqa: BLE001 -- Native errors may contain strings not representable in the diagnostic protocol.
            self._diagnostics._disable("history_unavailable")
        return list(findings)

    def _scope_progress(self, total, attempted, completed, unavailable, with_callers):
        self._scope_snapshot = _QueryScopeSnapshot(
            "established",
            total,
            attempted,
            completed,
            unavailable,
            with_callers,
            completed + unavailable == total,
        )

    def _run_analysis(self, diff_text: str, repo_root: Path) -> list[AdvisoryFinding]:
        # Cache retains the original facts and their completion state. This
        # private object must not be treated as a newly admitted run result.
        if self._cached is not None:
            return self._cached
        self._scope_snapshot = _QueryScopeSnapshot()
        result = self._run_analysis_impl(diff_text, repo_root)
        self._scope_snapshot = replace(self._scope_snapshot, processing_completed=True)
        return result

    def _run_analysis_impl(
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
            self._observe("not_requested")
            return []

        # Provider path/Registry constructors can mkdir even in read-only mode.
        # Use only its pure default-path helper and our read-only JSON snapshot.
        def import_locator():
            from code_review_graph.registry import default_registry_path

            self._provider_locator = default_registry_path

        try:
            self._stage("provider_import", import_locator)
        except ImportError:
            self.infra_errors.append(
                "cross-repo-impact: registry location unavailable; code-review-graph >=2.3.8 required"
            )
            self._observe("unavailable")
            return []
        try:
            default_path = self._stage("default_path", self._provider_locator)
            if not isinstance(default_path, Path):
                raise GraphDiscoveryError("unsupported registry location")
            default_repos = self._stage(
                "default_registry",
                lambda: _read_registry(default_path),
                kind="registry",
                error_type=GraphDiscoveryError,
            )
            primary_db = self._stage(
                "primary_database", lambda: _readonly_db_path(repo_root, default_repos)
            )
            if self._diagnostics is not None:
                self._diagnostics.require_absolute(repo_root, primary_db)
        except (OSError, RuntimeError, ValueError) as exc:
            self.infra_errors.append("cross-repo-impact: graph discovery unavailable: %s" % exc)
            self._observe("unavailable")
            return []
        if not primary_db.is_file():
            self.infra_errors.append(
                "cross-repo-impact: primary graph.db not found; build code-review-graph"
            )
            self._observe("unavailable")
            return []

        # Resolve changed symbols from the primary db.
        try:
            changed = resolve_changed_symbols(diff_text, str(primary_db), repo_root)
        except (sqlite3.Error, OSError, GraphIdentityError) as exc:
            self.infra_errors.append("cross-repo-impact: primary graph.db unreadable: %s" % exc)
            self._observe("unavailable")
            return []
        if not changed:
            self._observe("no_named_targets")
            if self._diagnostics is None:
                self._emit(_NO_NAMED_TARGETS)
            return []
        self._observe("targets_resolved")

        # Reuse the default snapshot unless enumeration has an explicit override.
        reg_path_str = self._stage("enumeration_selector", _registry_path)
        reg_path_arg = Path(reg_path_str) if reg_path_str else default_path
        try:
            repos = (
                default_repos
                if reg_path_arg == default_path
                else self._stage(
                    "enumeration_registry",
                    lambda: _read_registry(reg_path_arg),
                    kind="registry",
                    error_type=GraphDiscoveryError,
                )
            )
        except GraphDiscoveryError as exc:
            self.infra_errors.append("cross-repo-impact: registry unavailable: %s" % exc)
            self._scope_snapshot = _QueryScopeSnapshot(scope="unavailable")
            self._emit_scope_summary(_SCOPE_UNAVAILABLE, diff_text, repo_root)
            return []

        try:
            resolved_root = self._stage("primary_root", repo_root.resolve)
            siblings = []
            for index, entry in enumerate(repos):
                entry_root = Path(entry["path"])
                if self._diagnostics is not None:
                    # Even a relative entry filtered as the primary was consumed.
                    self._diagnostics.require_absolute(entry_root)
                resolved_sibling = self._stage("sibling_root.%d" % index, entry_root.resolve)
                if resolved_sibling != resolved_root:
                    siblings.append((index, entry))
        except (OSError, RuntimeError, ValueError) as exc:
            self.infra_errors.append("cross-repo-impact: registry path unavailable: %s" % exc)
            self._scope_snapshot = _QueryScopeSnapshot(scope="unavailable")
            self._emit_scope_summary(_SCOPE_UNAVAILABLE, diff_text, repo_root)
            return []

        self._scope_progress(len(siblings), 0, 0, 0, 0)
        if not siblings:
            self.infra_errors.append(
                "cross-repo-impact: no sibling repos registered; use code-review-graph register"
            )
            self._emit_scope_summary(_EMPTY_SCOPE, diff_text, repo_root)
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
        total = len(siblings)
        attempted = completed = unavailable = with_callers = 0
        for occurrence, sib in siblings:
            alias = sib.get("alias") or Path(sib["path"]).name
            try:
                sib_db = self._stage(
                    "sibling_database.%d" % occurrence,
                    lambda sib=sib: _readonly_db_path(Path(sib["path"]), default_repos),
                )
                if self._diagnostics is not None:
                    self._diagnostics.require_absolute(Path(sib["path"]), sib_db)
            except (OSError, RuntimeError, ValueError) as exc:
                self.infra_errors.append(
                    "cross-repo-impact: sibling '%s' graph discovery unavailable: %s" % (alias, exc)
                )
                unavailable += 1
                self._scope_progress(total, attempted, completed, unavailable, with_callers)
                continue

            if not sib_db.is_file():
                self.infra_errors.append("cross-repo-impact: sibling '%s' graph.db missing" % alias)
                unavailable += 1
                self._scope_progress(total, attempted, completed, unavailable, with_callers)
                continue

            try:
                attempted += 1
                self._scope_progress(total, attempted, completed, unavailable, with_callers)
                caller_groups = _find_cross_repo_caller_groups(str(sib_db), changed, Path(sib["path"]))
            except (sqlite3.Error, OSError, GraphIdentityError) as exc:
                self.infra_errors.append(
                    "cross-repo-impact: sibling '%s' graph.db unreadable: %s" % (alias, exc)
                )
                unavailable += 1
                self._scope_progress(total, attempted, completed, unavailable, with_callers)
                continue
            completed += 1
            with_callers += int(any(callers for _, callers in caller_groups))
            self._scope_progress(total, attempted, completed, unavailable, with_callers)

            sib_prefix = str(Path(sib["path"]).resolve()) + os.sep
            for sym, sym_callers in caller_groups:
                cfp = sym["file_path"]
                if cfp.startswith(primary_prefix):
                    cfp = cfp[len(primary_prefix) :]
                elif os.path.isabs(cfp):
                    resolved = str(Path(cfp).resolve())
                    if resolved.startswith(primary_prefix):
                        cfp = resolved[len(primary_prefix) :]
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

        self._emit_scope_summary(
            _QUERY_SUMMARY % (completed, total, attempted, unavailable), diff_text, repo_root
        )
        self._cached = findings
        return findings
