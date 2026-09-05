# SPDX-License-Identifier: Apache-2.0
"""Context sources: external facts that go into the L1 prompt.

Phase 59-B1. A ContextSource wraps one fact provider (today: graph
triage; later: an MCP server) behind a fixed contract:

  - every fact row carries where it came from (source name) and, when
    the provider indexes a snapshot of the tree, which commit that
    snapshot was taken at;
  - a snapshot taken at a commit other than the one under review is
    refused unless the operator opts in, because facts about an older
    tree are worse than no facts (they look authoritative);
  - a provider that raises is recorded as an error, not swallowed into
    an empty table;
  - the rendered text is byte-for-byte what cli.py built before this
    module existed, so the shared prompt prefix stays cacheable.

The blast-radius block at cli.py:3722-3761 is the prototype this
generalises. Its output format is kept verbatim in render_blast_radius.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    from .advisory import AdvisoryFinding


@dataclass(frozen=True)
class FactRow:
    """One row of context from one source."""

    entity: str
    file: str
    downstream: str
    dependents: str
    source: str
    origin_line: Optional[int] = None


@dataclass
class GatherResult:
    rows: list[FactRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    snapshot_shas: dict[str, Optional[str]] = field(default_factory=dict)


class ContextSource(Protocol):
    name: str

    def snapshot_sha(self) -> Optional[str]:
        """Commit the source's index was built at, or None if it has no
        snapshot (computes facts from the working tree on demand)."""
        ...

    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]:
        ...


@dataclass
class GraphTriageSource:
    """Adapter over GraphTriageRunner.run.

    Snapshot sha comes from graph.db metadata.git_head_sha when the
    backend is graphdb; sem computes on demand, so it reports None.
    """

    repo_root: Path
    name: str = "graph_triage"

    def snapshot_sha(self) -> Optional[str]:
        from .graph_triage import _detect_backend
        backend = _detect_backend(self.repo_root, _gate_cfg(self.repo_root))
        if backend is None or backend[0] != "graphdb":
            return None
        return _graphdb_head_sha(Path(backend[1]))

    def facts(self, changed_files: list[str], diff_text: str) -> list[FactRow]:
        from .graph_triage import GraphTriageRunner
        runner = GraphTriageRunner()
        findings = runner.run(diff_text, self.repo_root)
        if runner.infra_errors:
            raise RuntimeError("; ".join(runner.infra_errors))
        return [_adapt_advisory(f, self.name) for f in findings]


def _gate_cfg(repo_root: Path) -> dict:
    """gate.yaml as a dict; absent file is {}.

    A malformed or unreadable file is NOT {}: that would let
    _detect_backend pick a backend the operator disabled. It propagates
    so gather() records it under this source's name.
    """
    from .gate_check import load_gate_config
    try:
        return load_gate_config(repo_root / ".code-forge" / "gate.yaml")
    except FileNotFoundError:
        return {}


def _graphdb_head_sha(db: Path) -> Optional[str]:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            row = con.execute(
                "select value from metadata where key='git_head_sha'"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None


def _adapt_advisory(f: "AdvisoryFinding", source: str) -> FactRow:
    """Parse the AdvisoryFinding description exactly as cli.py did.

    Description shape: "name (impact: N downstream) -- top dependents: a, b".
    """
    desc = f.description
    parts = desc.split(" (impact: ", 1)
    ename = parts[0] if parts else "unknown"
    downstream = "0"
    deps = ""
    if len(parts) > 1:
        rest = parts[1]
        dp = rest.split(" downstream)", 1)
        downstream = dp[0] if dp else "0"
        if len(dp) > 1 and "-- top dependents: " in dp[1]:
            deps = dp[1].split("-- top dependents: ", 1)[1].strip()
    origin = None
    lr = getattr(f, "line_range", None)
    if lr and len(lr) >= 1 and isinstance(lr[0], int) and lr[0] > 0:
        origin = lr[0]
    return FactRow(
        entity=ename, file=f.file, downstream=downstream,
        dependents=deps, source=source, origin_line=origin,
    )


def gather(
    sources: list[ContextSource],
    changed_files: list[str],
    diff_text: str,
    head_sha: Optional[str],
    allow_unsnapshotted: bool = False,
    on_error: Optional[Callable[[str, str], None]] = None,
) -> GatherResult:
    """Collect facts from every source, applying the snapshot gate.

    Each source is wrapped in its own try/except so one failing source
    never hides another's facts, and the failure is recorded rather
    than becoming an empty table.

    Snapshot gate: a source whose snapshot_sha() is a commit other than
    head_sha is skipped (recorded in result.skipped) unless
    allow_unsnapshotted is True. A source with no snapshot (None)
    computes on demand and always runs. If head_sha itself is unknown
    the gate cannot be applied and snapshotted sources are skipped too,
    for the same reason: stale facts read as authoritative.
    """
    result = GatherResult()
    for src in sources:
        name = getattr(src, "name", type(src).__name__)
        try:
            snap = src.snapshot_sha()
            result.snapshot_shas[name] = snap
            if snap is not None and not allow_unsnapshotted:
                if head_sha is None or snap != head_sha:
                    result.skipped.append(
                        "%s: index at %s, review head %s"
                        % (name, (snap or "?")[:12], (head_sha or "unknown")[:12])
                    )
                    continue
            rows = src.facts(changed_files, diff_text)
            result.rows.extend(rows)
        except Exception as exc:  # noqa: BLE001 - attribute, do not swallow
            msg = "%s: %s: %s" % (name, type(exc).__name__, exc)
            result.errors.append(msg)
            if on_error is not None:
                on_error(name, msg)
    return result


def render_blast_radius(rows: list[FactRow]) -> str:
    """Render rows as the exact table cli.py:3756-3759 built.

    Empty input renders "" (not a header-only table), matching the
    pre-change behaviour where no findings meant no context section.
    """
    if not rows:
        return ""
    body = "\n".join(
        "| %s | %s | %s | %s |" % (r.entity, r.file, r.downstream, r.dependents)
        for r in rows
    )
    return (
        "| Entity | File | Downstream | Top Dependents |\n"
        "|--------|------|------------|----------------|\n"
        + body
    )


def render_context_sources(result: GatherResult) -> str:
    """Render the non-blast-radius facts as a ## Context Sources body.

    Today every FactRow comes from graph_triage and goes into the
    blast-radius table, so this returns "" and the L1 prompt is
    unchanged. B2 wires it into build_l1_provider; MCP sources (B3)
    are the first producers of text here.
    """
    extra = [r for r in result.rows if r.source != "graph_triage"]
    if not extra:
        return ""
    lines = [
        "| Source | Entity | File | Line | Note |",
        "|--------|--------|------|------|------|",
    ]
    for r in extra:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (r.source, r.entity, r.file,
               r.origin_line if r.origin_line is not None else "",
               r.dependents)
        )
    return "\n".join(lines)
