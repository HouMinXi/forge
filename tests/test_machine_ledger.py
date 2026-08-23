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


def test_unit_skips_style_findings(tmp_path):
    """STYLE findings in local mode are non-terminal and not written as DISPROVED or FIXED."""
    _prep_local_state(tmp_path)
    machine = _build_machine(tmp_path, _resolved_with_shas())
    machine._state.findings.append(
        _make_finding("fp-style", disp=Disposition.STYLE)
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


# ---------------------------------------------------------------------------
# CI mode ledger tests (Plan 44-01 Task 2)
# ---------------------------------------------------------------------------


def _build_ci_machine(tmp_path, resolved, l0_findings=None, l0_infra=None):
    findings = l0_findings if l0_findings is not None else []
    infra = l0_infra if l0_infra is not None else []

    def mock_l0(registry, files):
        return (findings, infra)

    return StateMachine(
        mode=Mode.CI,
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


def test_ci_confirmed_finding_appends_unadjudicated_row(tmp_path):
    """Test (a): CI run with CONFIRMED finding appends UNADJUDICATED row."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-ci-1", disp=Disposition.CONFIRMED, file="a.py", line=10)
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.FAIL

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r.fingerprint == "fp-ci-1"
    assert r.terminal_state == TerminalState.UNADJUDICATED
    assert r.base_sha == "a" * 40
    assert r.head_sha == "b" * 40
    assert r.file == "a.py"
    assert r.line == 10
    assert r.repo_root == str(tmp_path.resolve())


def test_ci_style_downgraded_finding_appends_unadjudicated_row_and_passes(tmp_path):
    """P1 / P3b / CP1 W-5: Style-downgraded finding is written to CI ledger as UNADJUDICATED and does not block PASS."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-ci-style", disp=Disposition.STYLE, file="a.py", line=15)
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.PASS

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r.fingerprint == "fp-ci-style"
    assert r.terminal_state == TerminalState.UNADJUDICATED
    assert r.base_sha == "a" * 40
    assert r.head_sha == "b" * 40
    assert r.file == "a.py"
    assert r.line == 15
    assert r.repo_root == str(tmp_path.resolve())


def test_ci_clean_pass_appends_clean_row_with_diff_scoped_fingerprint(tmp_path):
    """Test (b): CI run with zero CONFIRMED findings appends diff-scoped clean row."""
    import hashlib
    _prep_local_state(tmp_path)
    base = "1" * 40
    head = "2" * 40
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(base=base, head=head), l0_findings=[])
    verdict = machine.run()
    assert verdict == Verdict.PASS

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    expected_fp = hashlib.sha256(f"clean:{base}:{head}".encode("utf-8")).hexdigest()[:16]
    assert r.fingerprint == expected_fp
    assert r.axis_claim == "clean"
    assert r.file == ""
    assert r.line == 0
    assert r.terminal_state == TerminalState.UNADJUDICATED
    assert r.base_sha == base
    assert r.head_sha == head


def test_ci_dedup_same_diff_re_run(tmp_path):
    """Test (c): Re-running the same diff in CI does not duplicate UNADJUDICATED rows (D-08)."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-ci-dup", disp=Disposition.CONFIRMED)
    machine1 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    machine1.run()

    machine2 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    machine2.run()

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].fingerprint == "fp-ci-dup"


def test_ci_failure_isolation_oserror(tmp_path, monkeypatch):
    """Test (d): OSError during ledger write does not change verdict and logs to infra_errors (D-19)."""
    _prep_local_state(tmp_path)
    finding = _make_finding("fp-ci-fail", disp=Disposition.CONFIRMED)
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])

    from code_forge import machine as machine_mod

    def broken_append(cwd, row):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(machine_mod, "ledger_append", broken_append)
    verdict = machine.run()
    assert verdict == Verdict.FAIL
    assert any("ledger write failure" in err for err in machine._state.infra_errors)


def test_ci_env_kill_switch(tmp_path, monkeypatch):
    """Test (e): CODE_FORGE_DISABLE_LEDGER=1 suppresses CI ledger writes."""
    _prep_local_state(tmp_path)
    monkeypatch.setenv("CODE_FORGE_DISABLE_LEDGER", "1")
    finding = _make_finding("fp-ci-kill", disp=Disposition.CONFIRMED)
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.FAIL
    assert list(iter_rows(tmp_path)) == []


def test_ci_config_kill_switch(tmp_path):
    """Test (f): gate.yaml ledger.enabled=false suppresses CI ledger writes."""
    _prep_local_state(tmp_path)
    gate_file = tmp_path / "gate.yaml"
    gate_file.write_text("ledger:\n  enabled: false\n", encoding="utf-8")
    finding = _make_finding("fp-ci-kill-cfg", disp=Disposition.CONFIRMED)
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.FAIL
    assert list(iter_rows(tmp_path)) == []


def test_ci_config_kill_switch_tolerant(tmp_path):
    """Test (g): Kill-switch is tolerant to review-only gate.yaml, empty gate.yaml, malformed YAML, non-utf8."""
    _prep_local_state(tmp_path)
    gate_file = tmp_path / "gate.yaml"

    # 1. gate.yaml with no test section and ledger.enabled: false
    gate_file.write_text("mode: review\nledger:\n  enabled: false\n", encoding="utf-8")
    finding = _make_finding("fp-ci-1", disp=Disposition.CONFIRMED)
    m1 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    assert m1.run() == Verdict.FAIL
    assert list(iter_rows(tmp_path)) == []

    # 2. Empty gate.yaml (defaults to enabled, no crash)
    gate_file.write_text("", encoding="utf-8")
    m2 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    assert m2.run() == Verdict.FAIL
    assert len(list(iter_rows(tmp_path))) == 1

    # 3. Malformed YAML in gate.yaml (no crash, fail-open, records infra_error)
    (tmp_path / ".code-forge" / "ledger.jsonl").unlink()
    gate_file.write_text(":\n  - invalid yaml ::: [[]", encoding="utf-8")
    m3 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    assert m3.run() == Verdict.FAIL
    assert len(list(iter_rows(tmp_path))) == 1

    # 4. Non-UTF-8 bytes in gate.yaml (UnicodeDecodeError, no crash, fail-open)
    (tmp_path / ".code-forge" / "ledger.jsonl").unlink()
    gate_file.write_bytes(b"\xff\xfe\x00\x00invalid")
    m4 = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    assert m4.run() == Verdict.FAIL
    assert len(list(iter_rows(tmp_path))) == 1


def test_ci_mutation_survivor_terminal_writes_rows(tmp_path):
    """Test (g2): mutation-survivor FAIL terminal at :360 also writes ledger rows (D-01, kimi B-3)."""
    _prep_local_state(tmp_path)
    result_file = tmp_path / ".code-forge" / "mutation-result.json"
    result_file.write_text('{"status": "done", "survivors": [1, 2]}', encoding="utf-8")

    finding = _make_finding("fp-mutant", disp=Disposition.CONFIRMED, source="MUTANT")
    machine = _build_ci_machine(tmp_path, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.FAIL

    rows = list(iter_rows(tmp_path))
    assert len(rows) == 1
    assert rows[0].fingerprint == "fp-mutant"
    assert rows[0].terminal_state == TerminalState.UNADJUDICATED


def test_ci_worktree_persistence_and_repo_root_field(tmp_path, monkeypatch):
    """Test (h): From a linked worktree, writes land in main repo and repo_root field = main repo (D-05, D-20b)."""
    main_repo = tmp_path / "main_repo"
    worktree = tmp_path / "worktree_branch"
    main_repo.mkdir()
    worktree.mkdir()
    (main_repo / ".code-forge").mkdir()
    (worktree / ".code-forge").mkdir()

    from code_forge import machine as machine_mod

    def mock_resolve(cwd):
        if cwd == worktree:
            return main_repo
        return cwd

    monkeypatch.setattr(machine_mod, "resolve_ledger_root", mock_resolve)

    finding = _make_finding("fp-wt-1", disp=Disposition.CONFIRMED)
    machine = _build_ci_machine(worktree, _resolved_with_shas(), l0_findings=[finding])
    verdict = machine.run()
    assert verdict == Verdict.FAIL

    # Worktree local ledger is empty
    assert list(iter_rows(worktree)) == []

    # Main repo ledger received the row
    main_rows = list(iter_rows(main_repo))
    assert len(main_rows) == 1
    assert main_rows[0].fingerprint == "fp-wt-1"
    assert main_rows[0].repo_root == str(main_repo.resolve())
