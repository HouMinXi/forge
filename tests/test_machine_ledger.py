# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the state-machine -> ledger hook at _finalize_local_terminal.

Strategy: full end-to-end runs (mocking L0 to return findings), plus
direct unit tests of `_write_ledger_rows()` so we can stage arbitrary
findings into state without L0 round-replacement wiping them.

Verifies:
- Real run on a fixture diff writes rows with real SHAs.
- DISMISSED findings produce DISPROVED rows with evidence_class from
  finding.error.
- UNCERTAIN and still-open CONFIRMED do NOT write rows.
- Non-git mode (SHAs None) writes zero rows.
- Bug-inject: stub append_row to raise -> exception propagates; no
  row persists.
- After bug-inject clear, rows persist correctly (real-path).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.ledger import TerminalState, iter_rows
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp, disp=Disposition.CONFIRMED, source="L0", file="a.py",
                  line=1, error=None):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source=source,
        disposition=disp,
        file=file,
        line_range=[line, line],
        description="synthetic %s" % fp,
        error=error,
    )


def _resolved_with_shas(base="a" * 40, head="b" * 40):
    return ResolvedReview(
        source_files=[Path("a.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
        base_sha=base,
        head_sha=head,
    )


def _resolved_no_shas():
    return ResolvedReview(
        source_files=[Path("a.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="non-git",
    )


def _build_machine(tmp_path, resolved, l0_findings=None, l0_infra=None):
    findings = l0_findings if l0_findings is not None else []
    infra = l0_infra if l0_infra is not None else []

    def mock_l0(registry, files):
        return (findings, infra)

    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="src-hash",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=mock_l0,
    )


def _prep_local_state(tmp_path):
    (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Real-path end-to-end
# ---------------------------------------------------------------------------


def test_real_run_with_fixed_finding_writes_row(tmp_path):
    """Real run: L0 returns CONFIRMED; autofix promotes to FIXED; ledger row."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-fixed-1", disp=Disposition.CONFIRMED)
    machine = _build_machine(tmp_path, _resolved_with_shas(),
                             l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.PASS

    rows = list(iter_rows(tmp_path))
    by_fp = {r.fingerprint: r for r in rows}
    assert "fp-fixed-1" in by_fp
    r = by_fp["fp-fixed-1"]
    assert r.terminal_state == TerminalState.FIXED
    assert r.evidence_class == "fix_applied"
    assert r.base_sha == "a" * 40
    assert r.head_sha == "b" * 40
    assert r.pass_provenance == "L0"


def test_real_run_with_no_user_findings_still_emits_fixval_skip_row(tmp_path):
    """When the run converges with no L0 findings, classify_fixval_candidate
    returns FixvalSkip (no source files on disk); the resulting DISMISSED
    fixval-skipped entry is itself terminal and the hook records it as a
    DISPROVED row. This documents the behavior: every converged run
    leaves at least one ledger row.
    """
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas(),
                             l0_findings=[])
    verdict = machine.run()
    assert verdict == Verdict.PASS
    rows = list(iter_rows(tmp_path))
    fps = {r.fingerprint for r in rows}
    assert "fixval-skipped" in fps
    r = next(r for r in rows if r.fingerprint == "fixval-skipped")
    assert r.terminal_state == TerminalState.DISPROVED


def test_non_git_mode_writes_zero_rows(tmp_path):
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-fixed-1", disp=Disposition.CONFIRMED)
    machine = _build_machine(tmp_path, _resolved_no_shas(),
                             l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.PASS
    assert list(iter_rows(tmp_path)) == []


# ---------------------------------------------------------------------------
# Direct unit tests of _write_ledger_rows
# ---------------------------------------------------------------------------


def test_unit_dismissed_finding_writes_disproved_with_error(tmp_path):
    """_write_ledger_rows() with a staged DISMISSED finding -> DISPROVED row."""
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(
        _make_finding("fp-d-1", disp=Disposition.DISMISSED,
                      error="out-of-scope-rule")
    )
    n = machine._write_ledger_rows()
    assert n == 1
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].terminal_state == TerminalState.DISPROVED
    assert rows[0].evidence_class == "out-of-scope-rule"
    assert rows[0].base_sha == "a" * 40


def test_unit_skips_open_confirmed_and_uncertain(tmp_path):
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(
        _make_finding("fp-open", disp=Disposition.CONFIRMED)
    )
    machine._state.findings.append(
        _make_finding("fp-unc", disp=Disposition.UNCERTAIN)
    )
    n = machine._write_ledger_rows()
    assert n == 0
    assert list(iter_rows(tmp_path)) == []


def test_unit_skips_when_no_shas(tmp_path):
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_no_shas())
    machine._state.findings.append(
        _make_finding("fp-fixed", disp=Disposition.FIXED)
    )
    n = machine._write_ledger_rows()
    assert n == 0
    assert list(iter_rows(tmp_path)) == []


# ---------------------------------------------------------------------------
# Bug-inject
# ---------------------------------------------------------------------------


def test_bug_inject_hook_failure_propagates_and_writes_nothing(tmp_path,
                                                               monkeypatch):
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-fixed-1", disp=Disposition.CONFIRMED)
    machine = _build_machine(tmp_path, _resolved_with_shas(),
                             l0_findings=[finding])

    from code_forge import machine as machine_mod

    def boom(cwd, row):
        raise RuntimeError("ledger append broken (bug-inject)")

    monkeypatch.setattr(machine_mod, "ledger_append", boom)
    with pytest.raises(RuntimeError, match="bug-inject"):
        machine.run()

    assert list(iter_rows(tmp_path)) == []


def test_after_fix_rows_write_correctly(tmp_path, monkeypatch):
    """After restoring append_row, rows persist."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-fixed-1", disp=Disposition.CONFIRMED)
    machine = _build_machine(tmp_path, _resolved_with_shas(),
                             l0_findings=[finding])

    from code_forge import machine as machine_mod

    def boom(cwd, row):
        raise RuntimeError("boom")

    monkeypatch.setattr(machine_mod, "ledger_append", boom)
    with pytest.raises(RuntimeError):
        machine.run()
    assert list(iter_rows(tmp_path)) == []

    monkeypatch.undo()
    machine2 = _build_machine(tmp_path, _resolved_with_shas(),
                              l0_findings=[finding])
    machine2.run()
    rows = list(iter_rows(tmp_path))
    fps = {r.fingerprint for r in rows}
    assert "fp-fixed-1" in fps


def test_unit_dedup_skips_already_recorded_pair(tmp_path):
    """Same (fingerprint, terminal_state) is not appended twice."""
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(
        _make_finding("fp-dup", disp=Disposition.FIXED)
    )
    assert machine._write_ledger_rows() == 1
    # Second pass: same finding, same SHAs -> 0 new rows.
    assert machine._write_ledger_rows() == 0
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].fingerprint == "fp-dup"


def test_unit_dedup_does_not_block_different_state(tmp_path):
    """A new terminal_state for the same fingerprint DOES write a new row."""
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(
        _make_finding("fp-state", disp=Disposition.FIXED)
    )
    assert machine._write_ledger_rows() == 1
    machine._state.findings[0].disposition = Disposition.DISMISSED
    machine._state.findings[0].error = "reopened-and-dismissed"
    assert machine._write_ledger_rows() == 1
    rows = list(iter_rows(tmp_path))
    assert len(rows) == 2
    states = {r.terminal_state for r in rows}
    assert states == {TerminalState.FIXED, TerminalState.DISPROVED}


def test_write_ledger_derives_claim_type_from_source(tmp_path):
    """_write_ledger_rows derives axis_claim from f.source, not hardcoded.

    Two-source coverage closes the mirror mutation: derive_claim_type("L0")
    returning "review" would pass a single-L0 test but fail the L1 assertion.
    """
    from code_forge.ledger import iter_rows as lr_iter

    _prep_local_state(tmp_path)
    l0_finding = _make_finding("fp-l0", disp=Disposition.FIXED, source="L0")
    l1_finding = _make_finding("fp-l1", disp=Disposition.FIXED, source="L1")
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(l0_finding)
    machine._state.findings.append(l1_finding)
    n = machine._write_ledger_rows()
    assert n == 2

    rows_by_fp = {r.fingerprint: r for r in lr_iter(tmp_path)}

    # L0 -> lint, not version-sensitive
    r_l0 = rows_by_fp["fp-l0"]
    assert r_l0.axis_claim == "lint"
    assert r_l0.version_sensitive is False

    # L1 -> review, version-sensitive
    r_l1 = rows_by_fp["fp-l1"]
    assert r_l1.axis_claim == "review"
    assert r_l1.version_sensitive is True