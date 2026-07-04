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

import json
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator


class TerminalState(str, Enum):
    """Terminal outcomes recorded in the ledger.

    FIXED and DISPROVED enter via the state machine hook; DUPLICATE
    and ESCAPED enter only via `code-forge ledger mark --new`.
    """

    FIXED = "FIXED"
    DISPROVED = "DISPROVED"
    DUPLICATE = "DUPLICATE"
    ESCAPED = "ESCAPED"


@dataclass(frozen=True)
class LedgerRow:
    """One row of the outcome ledger. Schema v1."""

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


def _ledger_path(cwd: Path) -> Path:
    return cwd / ".code-forge" / "ledger.jsonl"


def append_row(cwd: Path, row: LedgerRow) -> None:
    """Append one row to `<cwd>/.code-forge/ledger.jsonl`.

    Creates `.code-forge/` if missing. Single write per row, atomic
    under Linux PIPE_BUF for the row sizes we emit.
    """
    path = _ledger_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(row)
    payload["terminal_state"] = row.terminal_state.value
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def iter_rows(cwd: Path) -> Iterator[LedgerRow]:
    """Yield ledger rows from `<cwd>/.code-forge/ledger.jsonl`.

    Missing file -> empty iterator. Malformed lines are skipped with
    a warning to stderr (does not raise) so a corrupt tail cannot
    block downstream consumers.
    """
    path = _ledger_path(cwd)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    "ledger: skipping malformed line %d: %s"
                    % (lineno, exc),
                    file=sys.stderr,
                )
                continue
            try:
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
                )
            except (KeyError, ValueError, TypeError) as exc:
                print(
                    "ledger: skipping schema-invalid line %d: %s"
                    % (lineno, exc),
                    file=sys.stderr,
                )
                continue