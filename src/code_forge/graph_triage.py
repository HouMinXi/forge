# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""GraphTriageRunner advisory axis: system-level blast-radius ranking.

Purely deterministic (subprocess + SQLite):
no LLM call, no new pip dependencies.

Dual backend:
  - sem CLI (preferred): precise entity resolution, zero false positives.
  - graph.db (fallback): IMPORTS_FROM disambiguation, degraded quality.
  - Both absent: SKIP + loud-fail warning via infra_errors.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .advisory import AdvisoryFinding
from .dead_code import _live_callers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEM_TIMEOUT_S: int = 15
"""Per-entity sem impact timeout (seconds)."""

_SEM_DIFF_TIMEOUT_S: int = 30
"""sem diff --patch timeout (seconds)."""

_TOP_N: int = 10
"""Fixed top-N entities to emit as findings."""

_UNNAMED_ENTITIES: frozenset[str] = frozenset({"module-level"})
"""Entity names that have no named symbol (skip)."""

_UNNAMED_PREFIX: str = "lines "
"""Entity names starting with this prefix are unnamed (skip)."""

# Regex to extract file paths from unified diff "+++ b/..." lines.
_DIFF_FILE_RE: re.Pattern[str] = re.compile(
    r"^\+\+\+ b/(.+)$", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

def _parse_diff_files(diff_text: str) -> list[str]:
    """Extract file paths from unified diff '+++ b/...' lines.

    Returns unique paths without the 'b/' prefix.
    """
    matches = _DIFF_FILE_RE.findall(diff_text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


def _sem_has_index(repo_root: Path) -> bool:
    """Check whether sem has built an index for this repo.

    sem stores its index at <repo_root>/.semcode.db.  The artifact is
    a DIRECTORY of lance tables on sem 0.10.x (verified on Linux);
    other versions may use a single file, so existence -- not
    file-ness -- is the signal.  Absence means sem would hang or
    error on every impact query.  A present-but-corrupt index is
    tolerated here: _run_sem and _get_sem_impact degrade gracefully
    on non-zero exit.

    Previous implementation ran `sem diff --patch --json` with empty
    stdin and checked the exit code, but exit code tracks payload
    validity (empty stdin always returns non-zero) -- not index
    presence.  The probe was dead on every platform.
    """
    # .semcode.db was dropped in sem v0.21.0. Modern sem is fast enough
    # on misses that we do not need to prevent invocation when unindexed.
    # Older versions (0.10.x) would hang for 15s without an index.
    try:
        r = subprocess.run(
            ["sem", "--version"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
            check=False,
        )
        if r.returncode == 0:
            version_str = r.stdout.strip()
            # expecting "sem 0.21.0" or similar
            parts = version_str.split()
            if len(parts) >= 2:
                v = parts[1].lstrip('v')
                major_minor = v.split('.')[:2]
                if len(major_minor) >= 2:
                    try:
                        major, minor = int(major_minor[0]), int(major_minor[1].split('-')[0])
                        if major > 0 or minor >= 21:
                            return True
                    except ValueError:
                        pass
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("sem version check failed: %s", e)

    return (repo_root / ".semcode.db").exists()


def _detect_backend(
    repo_root: Path,
    gate_config: dict,
) -> Optional[tuple[str, str]]:
    """Detect available backend: sem preferred, graphdb fallback.

    Priority:
      1. gate_config graph_triage.enabled == False -> None (explicit disable)
      2. shutil.which("sem") -> ("sem", path)
      3. gate_config graph_triage.db_path -> ("graphdb", path) if exists
      4. repo_root/.code-review-graph/graph.db -> ("graphdb", path) if exists
      5. CRG_DB_PATH env var -> ("graphdb", path) if exists
      6. None

    Args:
        repo_root: path to repository root.
        gate_config: parsed gate.yaml dict (may be empty).

    Returns:
        ("sem", path) or ("graphdb", path) or None.
    """
    gt_section = gate_config.get("graph_triage", {})
    if isinstance(gt_section, dict) and gt_section.get("enabled") is False:
        return None

    # Prefer sem CLI -- but only if this repo is actually indexed.
    # shutil.which alone is not enough: sem in PATH with no index for
    # this repo causes every _get_sem_impact call to hang for 15s,
    # multiplied by entity count.
    sem_path = shutil.which("sem")
    if sem_path is not None and _sem_has_index(repo_root):
        return ("sem", sem_path)

    # gate.yaml db_path.
    if isinstance(gt_section, dict):
        db_path = gt_section.get("db_path")
        if db_path and os.path.isfile(str(db_path)):
            return ("graphdb", str(db_path))

    # Auto-discover at default location.
    default_db = repo_root / ".code-review-graph" / "graph.db"
    if default_db.is_file():
        return ("graphdb", str(default_db))

    # CRG_DB_PATH env var.
    env_db = os.environ.get("CRG_DB_PATH")
    if env_db and os.path.isfile(env_db):
        return ("graphdb", env_db)

    return None


# ---------------------------------------------------------------------------
# sem CLI helpers
# ---------------------------------------------------------------------------

def _is_unnamed(entity_name: str) -> bool:
    """Return True if entity name is unnamed (skip for impact)."""
    if entity_name in _UNNAMED_ENTITIES:
        return True
    if entity_name.startswith(_UNNAMED_PREFIX):
        return True
    return False


def _run_sem(diff_text: str, repo_root: Path) -> list[dict]:
    """Run sem diff --patch to get changed entities from diff text.

    Writes diff_text to a tempfile, then pipes it to sem diff via stdin.

    Args:
        diff_text: unified diff string.
        repo_root: path to repo root (cwd for sem).

    Returns:
        List of entity change dicts from sem output.
        Empty list on error.
    """
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".patch", prefix="forge-")
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as tmp_f:
            tmp_f.write(diff_text)
        tmp_fd = None  # fdopen took ownership

        with open(tmp_path, "r", encoding="utf-8") as stdin_f:
            result = subprocess.run(
                ["sem", "diff", "--patch", "--format", "json"],
                stdin=stdin_f,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                cwd=str(repo_root),
                timeout=_SEM_DIFF_TIMEOUT_S,
            )

        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        return data.get("changes", [])

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _get_sem_impact(
    entity_name: str,
    file_path: str,
    repo_root: Path,
) -> dict:
    """Run sem impact for a single entity.

    Args:
        entity_name: name of the entity.
        file_path: file containing the entity.
        repo_root: path to repo root (cwd for sem).

    Returns:
        Parsed impact dict. On error/timeout: {"impact": {"total": 0}, "dependents": []}.
    """
    fallback = {"impact": {"total": 0}, "dependents": []}
    try:
        result = subprocess.run(
            ["sem", "impact", entity_name, "--file", file_path, "--json"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(repo_root),
            timeout=_SEM_TIMEOUT_S,
        )
        if result.returncode != 0:
            return fallback
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        # Distinguishable from "indexed but zero impact": the caller
        # uses _timed_out to trip the circuit breaker.
        return {**fallback, "_timed_out": True}
    except (json.JSONDecodeError, OSError):
        return fallback


# ---------------------------------------------------------------------------
# graph.db helpers
# ---------------------------------------------------------------------------

def _run_graphdb(db_path: str, diff_files: list[str]) -> list[dict]:
    """Query graph.db for changed entities and their dependents.

    Uses IMPORTS_FROM disambiguation to reduce false positives from
    short-name edge targets.

    Args:
        db_path: path to graph.db SQLite file.
        diff_files: list of file paths from the diff.

    Returns:
        List of entity dicts with name, file, qualified_name,
        dependent_count, top_dependents.
    """
    results: list[dict] = []
    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        cursor = conn.cursor()

        for file_path in diff_files:
            # Query nodes by file_path.
            cursor.execute(
                "SELECT id, kind, name, qualified_name, file_path, "
                "line_start, line_end FROM nodes "
                "WHERE file_path LIKE ?",
                ("%%%s" % file_path,),
            )
            nodes = cursor.fetchall()

            for node in nodes:
                node_id, kind, name, qualified_name, nfile, start, end = node
                if _is_unnamed(name):
                    continue

                # Module name for IMPORTS_FROM disambiguation.
                module_name = Path(file_path).stem

                live = _live_callers(cursor, name, module_name)
                dep_names = [lc.qualified for lc in live[:5]]

                results.append({
                    "name": name,
                    "file": file_path,
                    "qualified_name": qualified_name,
                    "dependent_count": len(live),
                    "top_dependents": dep_names,
                    "start_line": start,
                    "end_line": end,
                })
    except (sqlite3.Error, OSError) as exc:
        logger.warning("graph.db read error: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    return results


# ---------------------------------------------------------------------------
# Finding construction
# ---------------------------------------------------------------------------

def _build_findings(
    ranked_entities: list[dict],
    backend_name: str,
) -> list[AdvisoryFinding]:
    """Build AdvisoryFinding list from ranked entities.

    Args:
        ranked_entities: entities sorted by impact descending, up to _TOP_N.
        backend_name: "sem" or "graphdb".

    Returns:
        List of AdvisoryFinding with axis='GRAPH-TRIAGE'.
    """
    attribution = "sem" if backend_name == "sem" else "graph.db (degraded)"
    findings: list[AdvisoryFinding] = []

    for idx, entity in enumerate(ranked_entities[:_TOP_N]):
        name = entity.get("name", "unknown")
        file_path = entity.get("file", "unknown")
        total = entity.get("total", entity.get("dependent_count", 0))
        deps = entity.get("top_dependents", [])
        start = entity.get("start_line", 0)
        end = entity.get("end_line", 0)

        dep_names = ", ".join(
            str(
                d.get("entityName", d.get("name", d))
                if isinstance(d, dict) else d
            )
            for d in deps[:3]
        )
        desc = "%s (impact: %d downstream)" % (name, total)
        if dep_names:
            desc = "%s -- top dependents: %s" % (desc, dep_names)

        findings.append(AdvisoryFinding(
            id="graph-triage-%d" % (idx + 1),
            axis="GRAPH-TRIAGE",
            file=file_path,
            line_range=[start or 0, end or 0],
            description=desc,
            attribution=attribution,
        ))

    return findings


# ---------------------------------------------------------------------------
# Public utility
# ---------------------------------------------------------------------------

def find_entity_dependents(
    entity_name: str,
    file_path: str,
    repo_root: Path,
) -> list[str]:
    """Find dependents of a named entity using available backend.

    Exported for future axes that need entity-level dependent lookup.

    Args:
        entity_name: name of the entity to look up.
        file_path: file containing the entity.
        repo_root: path to repo root.

    Returns:
        List of dependent entity IDs/qualified names.
        Empty list if neither backend available.
    """
    # Try sem first.
    sem_path = shutil.which("sem")
    if sem_path is not None:
        impact = _get_sem_impact(entity_name, file_path, repo_root)
        return [
            d.get("entityId", str(d))
            for d in impact.get("dependents", [])
        ]

    # Try graphdb.
    default_db = repo_root / ".code-review-graph" / "graph.db"
    db_path = None
    if default_db.is_file():
        db_path = str(default_db)
    else:
        env_db = os.environ.get("CRG_DB_PATH")
        if env_db and os.path.isfile(env_db):
            db_path = env_db

    if db_path is not None:
        conn = None
        try:
            conn = sqlite3.connect(
                "file:%s?mode=ro" % db_path, uri=True,
            )
            cursor = conn.cursor()
            module_name = Path(file_path).stem
            live = _live_callers(cursor, entity_name, module_name)
            return [lc.qualified for lc in live]
        except (sqlite3.Error, OSError):
            pass
        finally:
            if conn is not None:
                conn.close()

    return []


# ---------------------------------------------------------------------------
# GraphTriageRunner
# ---------------------------------------------------------------------------

class GraphTriageRunner:
    """Advisory axis: system-level blast-radius ranking.

    Satisfies the AxisRunner Protocol (is_advisory=True).
    Detects sem CLI (preferred) or graph.db (fallback) and ranks
    changed entities by downstream impact count.
    """

    def __init__(self) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self._cached_findings: Optional[list[AdvisoryFinding]] = None

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle counter."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run graph triage on the given diff.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding from blast-radius analysis.
            Empty list if no backend available or diff is empty.
        """
        self.infra_errors.clear()

        # Cache hit avoids redundant subprocess calls.
        if self._cached_findings is not None:
            return self._cached_findings

        # Guard: empty/whitespace diff.
        if not diff_text or not diff_text.strip():
            return []

        # Load gate config.  Errors must not be silently swallowed --
        # a ValueError from load_gate_config (e.g. missing required
        # section) would discard the entire config including
        # graph_triage.enabled: false, causing the backend auto-detect
        # to re-enable a subsystem the user explicitly disabled.
        gate_config: dict = {}
        gate_path = repo_root / ".code-forge" / "gate.yaml"
        try:
            from .gate_check import load_gate_config
            gate_config = load_gate_config(gate_path)
        except FileNotFoundError:
            gate_config = {}
        except (ValueError, OSError):
            gate_config = {}

        # Detect backend.
        backend = _detect_backend(repo_root, gate_config)

        if backend is None:
            gt_section = gate_config.get("graph_triage", {})
            if isinstance(gt_section, dict) and \
                    gt_section.get("enabled") is False:
                # Explicit disable: silent return.
                return []
            # Both absent: loud-fail.
            msg = (
                "GraphTriageRunner: neither sem CLI nor graph.db found; "
                "install sem (https://github.com/Ataraxy-Labs/sem) "
                "or build code-review-graph"
            )
            self.infra_errors.append(msg)
            print(msg, file=sys.stderr)
            return []

        backend_name, backend_path = backend

        if backend_name == "sem":
            return self._run_with_sem(diff_text, repo_root)

        if backend_name == "graphdb":
            return self._run_with_graphdb(
                diff_text, backend_path,
            )

        return []

    def _run_with_sem(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run analysis using sem CLI backend."""
        entities = _run_sem(diff_text, repo_root)
        if not entities:
            return []

        # Filter unnamed entities + collect impact.
        # Circuit breaker: if any entity's impact call TIMES OUT
        # (_timed_out marker from _get_sem_impact), sem has no usable
        # index for this repo.  Skip all remaining entities instead of
        # accumulating N x 15s hangs.
        ranked: list[dict] = []
        for entity in entities:
            name = entity.get("entityName", "")
            if _is_unnamed(name):
                continue

            file_path = entity.get("filePath", "")
            impact = _get_sem_impact(name, file_path, repo_root)

            if impact.get("_timed_out"):
                print(
                    "GraphTriageRunner: sem impact timed out for %r "
                    "-- disabling for this run (repo may not be indexed)"
                    % name,
                    file=sys.stderr,
                )
                self._cached_findings = []
                return []

            total = impact.get("impact", {}).get("total", 0)
            if total <= 0:
                continue

            dep_list = impact.get("dependents", [])
            ranked.append({
                "name": name,
                "file": file_path,
                "total": total,
                "top_dependents": dep_list[:5],
                "start_line": entity.get("startLine", 0),
                "end_line": entity.get("endLine", 0),
            })

        # Sort descending by impact total.
        ranked.sort(key=lambda e: e.get("total", 0), reverse=True)
        findings = _build_findings(ranked, "sem")
        self._cached_findings = findings
        return findings

    def _run_with_graphdb(
        self,
        diff_text: str,
        db_path: str,
    ) -> list[AdvisoryFinding]:
        """Run analysis using graph.db SQLite backend."""
        diff_files = _parse_diff_files(diff_text)
        if not diff_files:
            return []

        entities = _run_graphdb(db_path, diff_files)
        if not entities:
            return []

        # Sort descending by dependent count.
        entities.sort(
            key=lambda e: e.get("dependent_count", 0), reverse=True,
        )
        findings = _build_findings(entities, "graphdb")
        self._cached_findings = findings
        return findings
