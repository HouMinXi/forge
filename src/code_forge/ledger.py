# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Append-only outcome ledger for reviewed findings.

Stores one JSON object per line at `<cwd>/.code-forge/ledger.jsonl`.
Survives across sessions so the self-evolution arc has a durable
record of every terminal disposition (FIXED / DISPROVED / DUPLICATE /
ESCAPED). Rows carry base/head SHAs so Phase 44 can re-extract the
reviewed diff for downstream case generation.

Atomicity: each append is one single write(2) call preceded by
open(O_APPEND). On Linux, writes <= PIPE_BUF (4096 bytes) are
guaranteed atomic at the syscall level. Ledger rows are well under
1 KB, so concurrent appends will not interleave.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Final, Iterator

_TRUNCATION_MARKER = "... [truncated]"
_MAX_EVIDENCE_LEN = 500


def _truncate_evidence(text: str) -> str:
    """Truncate evidence to <= 500 chars with an explicit marker (D-07, D-21)."""
    if len(text) <= _MAX_EVIDENCE_LEN:
        return text
    prefix_len = _MAX_EVIDENCE_LEN - len(_TRUNCATION_MARKER)
    return text[:prefix_len] + _TRUNCATION_MARKER


def resolve_ledger_root(cwd: Path) -> Path:
    """Resolve the main repo root for durable ledger storage (D-05, D-11, D-20b).

    In a git repository (including linked worktrees), returns the parent directory
    of the git common-dir (the main repo worktree root). On any failure (non-git cwd,
    git execution failure, or OS error), falls back to returning cwd unchanged.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if res.returncode == 0:
            out = res.stdout.strip()
            if out:
                common_dir = Path(out).resolve()
                return common_dir.parent
    except Exception:
        pass
    return cwd


class TerminalState(str, Enum):
    """Outcomes recorded in the ledger: four terminal + one pending-adjudication.

    Entry rules:
      - FIXED: enters via the local state machine hook on fix confirmation or via adjudication.
      - DISPROVED: enters via the local state machine hook on falsifier rejection or via adjudication.
      - DUPLICATE: enters via `code-forge ledger mark --new` or via adjudication.
      - ESCAPED: enters via `code-forge ledger mark --new` or via adjudication.
      - UNADJUDICATED: enters via CI review runs on confirmed findings pending human adjudication.
    """

    FIXED = "FIXED"
    DISPROVED = "DISPROVED"
    DUPLICATE = "DUPLICATE"
    ESCAPED = "ESCAPED"
    UNADJUDICATED = "UNADJUDICATED"


@dataclass(frozen=True)
class LedgerRow:
    """One row of the outcome ledger. Schema v1.1.

    The backend and ctx_* fields carry model attribution: which backend
    raised a finding, and which auxiliary context the pipeline fed the
    review that round. They are additive with defaults so v1 rows -- every
    row written before this schema -- keep parsing.
    """

    fingerprint: str
    repo_root: str
    base_sha: str
    head_sha: str
    file: str
    line: int
    axis_claim: str
    pass_provenance: str
    terminal_state: TerminalState
    evidence_class: str
    ts: str  # ISO-8601 UTC
    version_sensitive: bool = False
    # Backend that produced the finding; "" for human-entered mark rows.
    backend: str = ""
    # Auxiliary context this review round fed the model. Pipeline-internal
    # only: what the CALLING agent consulted is invisible to forge.
    ctx_graph_triage: bool = False
    ctx_contract: bool = False
    ctx_whole_file: bool = False
    ctx_canary: bool = False
    suppression_key: str = ""


def _ledger_path(cwd: Path) -> Path:
    return cwd / ".code-forge" / "ledger.jsonl"


def append_row(cwd: Path, row: LedgerRow) -> None:
    """Append one row to `<cwd>/.code-forge/ledger.jsonl`.

    Creates `.code-forge/` if missing. Single write per row, atomic
    under Linux PIPE_BUF for the row sizes we emit. Truncates evidence
    to <= 500 chars with an explicit marker (D-07, D-21).
    """
    path = _ledger_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(row)
    payload["terminal_state"] = row.terminal_state.value
    payload["evidence_class"] = _truncate_evidence(row.evidence_class)
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


_SUPPRESSIBLE_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "FIXED",
        "DISPROVED",
        "DUPLICATE",
    }
)
"""Terminal states whose fingerprint suppresses a re-appearing CONFIRMED finding (D-23)."""


def known_terminal_fingerprints(root: Path) -> set[str]:
    """Return fingerprints whose LATEST row is FIXED, DISPROVED, or DUPLICATE.

    Latest = last in iteration order (append-only file -> last write wins).
    UNADJUDICATED and ESCAPED do NOT suppress (D-23, D-25).
    Missing ledger or empty ledger -> empty set (no crash).
    """
    latest: dict[str, tuple[str, str]] = {}  # fp -> (terminal_state, _)
    for r in iter_rows(root):
        latest[r.fingerprint] = (r.terminal_state.value, r.ts)
    return {fp for fp, (state, _) in latest.items() if state in _SUPPRESSIBLE_TERMINAL_STATES}


def finding_suppression_key(
    *,
    base_sha: str | None,
    head_sha: str | None,
    git_diff: str | None,
    file: str,
    line: int,
    source: str,
    description: str,
) -> str:
    """Bind a ruling to the verbatim reviewed diff and exact finding claim."""
    if not base_sha or not head_sha or git_diff is None or not description:
        return ""
    payload = [base_sha, head_sha, git_diff, file, line, source, description]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def iter_rows(cwd: Path, *, strict: bool = False) -> Iterator[LedgerRow]:
    """Yield ledger rows from `<cwd>/.code-forge/ledger.jsonl`.

    Missing file -> empty iterator. Malformed lines are skipped with
    a warning to stderr (does not raise) so a corrupt tail cannot
    block downstream consumers. A strict caller gets an exception for any
    malformed or unknown row and must consume the entire iterator before
    relying on its append order.
    """
    path = _ledger_path(cwd)
    if not path.exists():
        return

    def unique_object(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate ledger field: " + key)
            data[key] = value
        return data

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                if strict:
                    raise ValueError("ledger: empty row at line %d" % lineno)
                continue
            try:
                data = json.loads(line, object_pairs_hook=unique_object) if strict else json.loads(line)
            except ValueError as exc:
                if strict:
                    raise ValueError("ledger: malformed line %d: %s" % (lineno, exc)) from exc
                print(
                    "ledger: skipping malformed line %d: %s" % (lineno, exc),
                    file=sys.stderr,
                )
                continue
            try:
                if strict:
                    if not isinstance(data, dict) or set(data) - {f.name for f in fields(LedgerRow)}:
                        raise ValueError("unknown ledger fields or non-object row")
                    for key in ("fingerprint", "repo_root", "base_sha", "head_sha", "file",
                                "axis_claim", "pass_provenance", "terminal_state", "evidence_class", "ts"):
                        if type(data[key]) is not str:
                            raise ValueError("non-string ledger field: " + key)
                    if not data["fingerprint"] or type(data["line"]) is not int or data["line"] < 0:
                        raise ValueError("invalid fingerprint or line")
                    for key in ("version_sensitive", "ctx_graph_triage", "ctx_contract", "ctx_whole_file", "ctx_canary"):
                        if key in data and type(data[key]) is not bool:
                            raise ValueError("non-boolean ledger field: " + key)
                    for key in ("backend", "suppression_key"):
                        if key in data and type(data[key]) is not str:
                            raise ValueError("non-string ledger field: " + key)
                yield LedgerRow(
                    fingerprint=data["fingerprint"],
                    repo_root=data["repo_root"],
                    base_sha=data["base_sha"],
                    head_sha=data["head_sha"],
                    file=data["file"],
                    line=int(data["line"]),
                    axis_claim=data["axis_claim"],
                    pass_provenance=data["pass_provenance"],
                    terminal_state=TerminalState(data["terminal_state"]),
                    evidence_class=data["evidence_class"],
                    ts=data["ts"],
                    version_sensitive=data.get("version_sensitive", False),
                    # .get with a default, never data[...]: v1 rows carry
                    # none of these and a KeyError here would silently skip
                    # the entire pre-enrichment ledger.
                    backend=data.get("backend", ""),
                    ctx_graph_triage=data.get("ctx_graph_triage", False),
                    ctx_contract=data.get("ctx_contract", False),
                    ctx_whole_file=data.get("ctx_whole_file", False),
                    ctx_canary=data.get("ctx_canary", False),
                    suppression_key=data.get("suppression_key", ""),
                )
            except (KeyError, ValueError, TypeError) as exc:
                if strict:
                    raise ValueError("ledger: schema-invalid line %d: %s" % (lineno, exc)) from exc
                print(
                    "ledger: skipping schema-invalid line %d: %s" % (lineno, exc),
                    file=sys.stderr,
                )
                continue
