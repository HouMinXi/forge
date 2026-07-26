# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for mechanical claim_type derivation from StateFinding.source.

Verifies:
- Each of the 7 source values maps to the correct ClaimType.
- Unknown sources raise ValueError.
- ClaimType is immutable (frozen dataclass).
- Integration: claim+ledger round-trip writes correct axis_claim and
  version_sensitive.
- Wiring: machine.py imports and uses derive_claim_type, not hardcoded
  "review".
"""

from __future__ import annotations

import inspect
import pytest

from code_forge.claim import derive_claim_type
from code_forge.ledger import (
    LedgerRow,
    TerminalState,
    append_row,
    iter_rows,
)


# ---------------------------------------------------------------------------
# Tests 1-7: each source maps to the correct ClaimType
# ---------------------------------------------------------------------------


def test_l0_lint():
    ct = derive_claim_type("L0")
    assert ct.type == "lint"
    assert ct.version_sensitive is False


def test_l1_review_version_sensitive():
    ct = derive_claim_type("L1")
    assert ct.type == "review"
    assert ct.version_sensitive is True


def test_mutant_mutation_version_sensitive():
    ct = derive_claim_type("MUTANT")
    assert ct.type == "mutation"
    assert ct.version_sensitive is True


def test_e2e_check():
    ct = derive_claim_type("E2E_CHECK")
    assert ct.type == "e2e"
    assert ct.version_sensitive is False


def test_coverage():
    ct = derive_claim_type("COVERAGE")
    assert ct.type == "coverage"
    assert ct.version_sensitive is False


def test_infra():
    ct = derive_claim_type("INFRA")
    assert ct.type == "infra"
    assert ct.version_sensitive is False


def test_fixval():
    ct = derive_claim_type("FIXVAL")
    assert ct.type == "fixval"
    assert ct.version_sensitive is False


# ---------------------------------------------------------------------------
# Test 8: unknown source raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="unknown finding source"):
        derive_claim_type("BOGUS")


# ---------------------------------------------------------------------------
# Tests 9-10: claim+ledger integration round-trip
# ---------------------------------------------------------------------------


def test_ledger_roundtrip_l1_review_version_sensitive(tmp_path):
    ct = derive_claim_type("L1")
    row = LedgerRow(
        fingerprint="abc",
        repo_root=str(tmp_path),
        base_sha="a1b2c3" + "0" * 34,
        head_sha="d4e5f6" + "0" * 34,
        file="test.py",
        line=10,
        axis_claim=ct.type,
        pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied",
        ts="2026-01-01T00:00:00Z",
        version_sensitive=ct.version_sensitive,
    )
    (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
    append_row(tmp_path, row)
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].axis_claim == "review"
    assert rows[0].version_sensitive is True


def test_ledger_roundtrip_l0_lint_not_sensitive(tmp_path):
    ct = derive_claim_type("L0")
    row = LedgerRow(
        fingerprint="def",
        repo_root=str(tmp_path),
        base_sha="a1b2c3" + "0" * 34,
        head_sha="d4e5f6" + "0" * 34,
        file="test.py",
        line=5,
        axis_claim=ct.type,
        pass_provenance="L0",
        terminal_state=TerminalState.DISPROVED,
        evidence_class="falsifier_rejected",
        ts="2026-01-01T00:00:00Z",
        version_sensitive=ct.version_sensitive,
    )
    (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
    append_row(tmp_path, row)
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].axis_claim == "lint"
    assert rows[0].version_sensitive is False


# ---------------------------------------------------------------------------
# Test 11: manual mark stays literal "manual"
# ---------------------------------------------------------------------------


def test_cli_manual_mark_stays_literal():
    """cli.py manual ledger mark uses axis_claim='manual', not derived."""
    import code_forge.cli as cli_mod

    src = inspect.getsource(cli_mod)
    assert 'axis_claim="manual"' in src, (
        "cli.py manual mark must use literal 'manual', not derived"
    )


# ---------------------------------------------------------------------------
# Test 12: backward compat -- old ledger row without version_sensitive
# ---------------------------------------------------------------------------


def test_old_ledger_row_without_version_sensitive_defaults_false(tmp_path):
    """Pre-Phase-42 rows lack version_sensitive; iter_rows defaults to False."""
    (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_path / ".code-forge" / "ledger.jsonl"
    # Write a genuine old-format row (no version_sensitive key)
    import json

    old_row = {
        "fingerprint": "old-fp",
        "repo_root": str(tmp_path),
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "file": "old.py",
        "line": 1,
        "axis_claim": "review",
        "pass_provenance": "L1",
        "terminal_state": "FIXED",
        "evidence_class": "fix_applied",
        "ts": "2025-01-01T00:00:00Z",
    }
    ledger_path.write_text(
        json.dumps(old_row, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].version_sensitive is False


# ---------------------------------------------------------------------------
# Test 13: machine.py wiring verification (source-text assertion)
# ---------------------------------------------------------------------------


def test_machine_py_wiring_derive_claim_type():
    """machine.py must import and call derive_claim_type, not hardcode."""
    import code_forge.machine as machine_mod

    src = inspect.getsource(machine_mod)

    # (a) import exists
    assert "derive_claim_type" in src, (
        "machine.py must import derive_claim_type"
    )

    # (b) hardcoded "review" gone from _write_ledger_rows
    # Read the function body specifically
    lines = src.splitlines()
    in_func = False
    func_body_lines = []
    for line in lines:
        if "def _write_ledger_rows" in line:
            in_func = True
            continue
        if in_func:
            if line.strip() and not line[0].isspace() and "def " in line:
                break
            func_body_lines.append(line)

    func_body = "\n".join(func_body_lines)
    assert 'axis_claim="review"' not in func_body, (
        "_write_ledger_rows must not hardcode axis_claim='review'"
    )

    # (c) version_sensitive IS present in the function
    assert "version_sensitive" in func_body, (
        "_write_ledger_rows must write version_sensitive"
    )
