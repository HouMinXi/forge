# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the append-only outcome ledger.

Round-trip + malformed-line tolerance + directory auto-creation.
"""

from __future__ import annotations

import json
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
    bogus_state = json.dumps(_make_row(fp="fp-bogus-state").__dict__
                             ).replace('"FIXED"', '"BOGUS_STATE"')
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
    null_line = json.dumps(_make_row(fp="fp-null-line").__dict__
                           ).replace('"line": 42', '"line": null')
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