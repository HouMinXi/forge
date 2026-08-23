# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the append-only outcome ledger.

Round-trip + malformed-line tolerance + directory auto-creation.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import pytest

from code_forge.ledger import (
    LedgerRow,
    TerminalState,
    append_row,
    iter_rows,
)


def _make_row(fp="fp-test-1", state=TerminalState.FIXED, evidence="fix_applied"):
    return LedgerRow(
        fingerprint=fp,
        repo_root="/tmp/example",
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="src/example.py",
        line=42,
        axis_claim="correctness",
        pass_provenance="L1",
        terminal_state=state,
        evidence_class=evidence,
        ts="2026-07-04T12:34:56Z",
    )


def test_terminal_state_values():
    assert TerminalState.FIXED.value == "FIXED"
    assert TerminalState.DISPROVED.value == "DISPROVED"
    assert TerminalState.DUPLICATE.value == "DUPLICATE"
    assert TerminalState.ESCAPED.value == "ESCAPED"
    assert TerminalState.UNADJUDICATED.value == "UNADJUDICATED"


def test_unadjudicated_round_trip(tmp_path):
    row = _make_row(state=TerminalState.UNADJUDICATED)
    append_row(tmp_path, row)
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0] == row
    assert rows[0].terminal_state == TerminalState.UNADJUDICATED


def test_iter_skips_unadjudicated_under_old_vocabulary(tmp_path, capsys, monkeypatch):
    """Old readers with 4-state enum skip UNADJUDICATED rows (D-06)."""
    class OldTerminalState(str, Enum):
        FIXED = "FIXED"
        DISPROVED = "DISPROVED"
        DUPLICATE = "DUPLICATE"
        ESCAPED = "ESCAPED"

    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps(_make_row(fp="fp-good", state=TerminalState.FIXED).__dict__)
    unadj = json.dumps(_make_row(fp="fp-unadj", state=TerminalState.UNADJUDICATED).__dict__)
    ledger_path.write_text(valid + "\n" + unadj + "\n")

    import code_forge.ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "TerminalState", OldTerminalState)
    rows = list(iter_rows(tmp_path))
    assert [r.fingerprint for r in rows] == ["fp-good"]
    captured = capsys.readouterr()
    assert "schema-invalid" in captured.err.lower()


def test_resolve_ledger_root_non_git(tmp_path):
    """From a non-git directory, resolve_ledger_root returns cwd unchanged (D-11)."""
    from code_forge.ledger import resolve_ledger_root
    non_git = tmp_path / "not_git"
    non_git.mkdir()
    assert resolve_ledger_root(non_git) == non_git


def test_resolve_ledger_root_git_and_worktree():
    """From main repo or linked worktree, resolve_ledger_root returns main root (D-05, D-20b)."""
    from code_forge.ledger import resolve_ledger_root
    worktree_cwd = Path(__file__).resolve().parent.parent
    root = resolve_ledger_root(worktree_cwd)
    assert root.exists()
    assert (root / ".git").exists()
    # From the main repo root itself
    assert resolve_ledger_root(root) == root


def test_evidence_truncation(tmp_path):
    """Evidence >500 chars is truncated with marker; <=500 chars is not (D-07, D-21)."""
    long_evidence = "x" * 600
    row_long = _make_row(fp="fp-long", evidence=long_evidence)
    append_row(tmp_path, row_long)

    boundary_evidence = "y" * 500
    row_boundary = _make_row(fp="fp-boundary", evidence=boundary_evidence)
    append_row(tmp_path, row_boundary)

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2

    # Long evidence truncated to 500 chars with marker
    assert len(rows[0].evidence_class) == 500
    assert rows[0].evidence_class.endswith("... [truncated]")
    assert rows[0].evidence_class.startswith("x" * (500 - len("... [truncated]")))

    # 500-char evidence untouched
    assert len(rows[1].evidence_class) == 500
    assert rows[1].evidence_class == boundary_evidence
    assert not rows[1].evidence_class.endswith("... [truncated]")


def test_serialized_row_pipe_buf_margin(tmp_path):
    """A serialized row with maximal fields is well under 2048 bytes (D-07)."""
    maximal_row = LedgerRow(
        fingerprint="f" * 64,
        repo_root="/very/deeply/nested/directory/structure/that/simulates/a/long/filesystem/path" * 3,
        base_sha="1" * 40,
        head_sha="2" * 40,
        file="src/very/long/nested/package/path/to/a/source/file_with_a_long_name.py",
        line=999999,
        axis_claim="potential SQL injection through unescaped user input passed into dynamic query builder",
        pass_provenance="heuristic_analyzer_deep_inspection_pass_v2",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="e" * 600,
        ts="2026-08-22T23:59:59Z",
        version_sensitive=True,
    )
    append_row(tmp_path, maximal_row)
    ledger_file = tmp_path / ".code-forge" / "ledger.jsonl"
    line_bytes = ledger_file.read_bytes()
    assert len(line_bytes) < 2048, f"Row size {len(line_bytes)} exceeds 2048-byte limit"


def test_append_then_iter_round_trip(tmp_path):
    row = _make_row()
    append_row(tmp_path, row)
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0] == row


def test_append_multiple_rows_preserves_order(tmp_path):
    for i in range(3):
        append_row(tmp_path, _make_row(fp=f"fp-{i}"))
    rows = list(iter_rows(tmp_path))
    assert [r.fingerprint for r in rows] == ["fp-0", "fp-1", "fp-2"]


def test_iter_skips_malformed_line(tmp_path, capsys):
    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(_make_row(fp="fp-good").__dict__) + "\n"
        + "this is not valid json\n"
        + json.dumps(_make_row(fp="fp-good-2").__dict__) + "\n"
    )
    rows = list(iter_rows(tmp_path))
    fps = [r.fingerprint for r in rows]
    assert fps == ["fp-good", "fp-good-2"]
    captured = capsys.readouterr()
    assert "malformed" in captured.err.lower()


def test_iter_skips_schema_invalid_line_with_missing_field(tmp_path, capsys):
    """A valid JSON line missing required fields is skipped, not crashed."""
    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps(_make_row(fp="fp-good").__dict__)
    missing = json.dumps({"fingerprint": "fp-no-fields"})
    good_row = _make_row(fp="fp-bogus-state")
    bogus_state_row = good_row.__dict__.copy()
    bogus_state_row["terminal_state"] = "BOGUS_STATE"
    bogus_state = json.dumps(bogus_state_row)
    ledger_path.write_text(valid + "\n" + missing + "\n" + bogus_state + "\n")
    rows = list(iter_rows(tmp_path))
    assert [r.fingerprint for r in rows] == ["fp-good"]
    captured = capsys.readouterr()
    assert "schema-invalid" in captured.err.lower()


def test_iter_skips_schema_invalid_line_with_null_line(tmp_path, capsys):
    """A row whose `line` field is null does NOT abort iteration (TypeError)."""
    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps(_make_row(fp="fp-good").__dict__)
    null_line_row = _make_row(fp="fp-null-line").__dict__.copy()
    null_line_row["line"] = None
    null_line = json.dumps(null_line_row)
    ledger_path.write_text(valid + "\n" + null_line + "\n")
    rows = list(iter_rows(tmp_path))
    assert [r.fingerprint for r in rows] == ["fp-good"]
    captured = capsys.readouterr()
    assert "schema-invalid" in captured.err.lower()


def test_append_creates_directory_if_missing(tmp_path):
    assert not (tmp_path / ".code-forge").exists()
    append_row(tmp_path, _make_row())
    assert (tmp_path / ".code-forge" / "ledger.jsonl").exists()


def test_iter_empty_file_returns_empty(tmp_path):
    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")
    assert list(iter_rows(tmp_path)) == []


def test_iter_missing_file_returns_empty(tmp_path):
    assert list(iter_rows(tmp_path)) == []


def test_append_writes_valid_one_json_per_line(tmp_path):
    append_row(tmp_path, _make_row(fp="fp-a"))
    append_row(tmp_path, _make_row(fp="fp-b"))
    text = (tmp_path / ".code-forge" / "ledger.jsonl").read_text()
    lines = [ln for ln in text.split("\n") if ln]
    assert len(lines) == 2
    for ln in lines:
        parsed = json.loads(ln)
        assert "fingerprint" in parsed
        assert "terminal_state" in parsed
        assert "base_sha" in parsed
        assert "head_sha" in parsed