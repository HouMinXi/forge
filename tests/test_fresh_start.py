# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-09 CI fresh-start (4 cases a-d)."""

import json
import logging
from pathlib import Path

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, State, StateFinding, Verdict, save_state


def _make_finding(fp="fp-fs-1", disp=Disposition.CONFIRMED):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
        disposition=disp,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _make_resolved():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
    )


def _make_machine(tmp_path, mode=Mode.CI, l0_findings=None):
    findings = l0_findings if l0_findings is not None else []

    def mock_l0(registry, files):
        return (findings, [])

    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=mock_l0,
    )


def _write_prior_state(tmp_path):
    """Write a prior state.json with a DISMISSED finding."""
    state = State()
    finding = _make_finding(fp="fp-prior", disp=Disposition.DISMISSED)
    state.findings = [finding]
    state.dispositions = {"fp-prior": Disposition.DISMISSED}
    state.fix_attempts = {"fp-prior": 1}
    state.promoted_fingerprints = {"fp-prior"}
    state.round = 3
    state.source_hash = "oldhash"
    state.baseline_spec_repr = "old"
    state_path = tmp_path / ".code-forge" / "state.json"
    save_state(state, state_path)
    return state_path


class TestCIIgnoresState:
    """(a) CI mode + existing state.json -> NOT loaded."""

    def test_ci_starts_fresh(self, tmp_path):
        _write_prior_state(tmp_path)
        machine = _make_machine(tmp_path, mode=Mode.CI)
        machine.run()
        # Prior DISMISSED finding should NOT be in state
        assert all(
            f.fingerprint != "fp-prior"
            for f in machine._state.findings
        )


class TestCIWarnsOnExistingState:
    """(b) CI mode + existing state.json -> warning logged."""

    def test_ci_logs_warning(self, tmp_path, caplog):
        _write_prior_state(tmp_path)
        machine = _make_machine(tmp_path, mode=Mode.CI)
        with caplog.at_level(logging.WARNING, logger="code_forge"):
            machine.run()
        assert any(
            "ignoring prior state.json in CI mode" in r.message
            for r in caplog.records
        )


class TestLocalLoadsState:
    """(c) LOCAL mode + existing state.json -> loaded.

    D2-CORRECTED: header + fix_attempts + promoted_fingerprints
    preserved. UNCERTAIN sticky (DISPO-05). DISMISSED NOT sticky
    across L0 re-detect (v2.0 limitation).
    """

    def test_local_preserves_fields(self, tmp_path):
        state = State()
        unc_finding = _make_finding(fp="fp-unc", disp=Disposition.UNCERTAIN)
        state.findings = [unc_finding]
        state.dispositions = {"fp-unc": Disposition.UNCERTAIN}
        state.fix_attempts = {"fp-unc": 3}
        state.promoted_fingerprints = {"fp-unc"}
        state.round = 2
        state.source_hash = "oldhash"
        state.baseline_spec_repr = "old"
        state_path = tmp_path / ".code-forge" / "state.json"
        save_state(state, state_path)

        # LOCAL machine that re-detects the same fingerprint as CONFIRMED
        def mock_l0(registry, files):
            return ([_make_finding(fp="fp-unc", disp=Disposition.CONFIRMED)], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="newhash",
            baseline_spec_repr="new",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=3,
            max_fix_attempts=3,
        )
        verdict = machine.run()

        # fix_attempts preserved from loaded state
        assert machine._state.fix_attempts.get("fp-unc", 0) >= 3
        # promoted_fingerprints preserved
        assert "fp-unc" in machine._state.promoted_fingerprints
        # UNCERTAIN stickiness: L0 re-detect -> still UNCERTAIN
        unc = [
            f for f in machine._state.findings
            if f.fingerprint == "fp-unc"
        ]
        assert len(unc) == 1
        assert unc[0].disposition == Disposition.UNCERTAIN


class TestCIMissingStateNoOp:
    """(d) CI mode + missing state.json -> no-op, no warning."""

    def test_ci_no_file_no_warning(self, tmp_path, caplog):
        # No state.json written
        machine = _make_machine(tmp_path, mode=Mode.CI)
        with caplog.at_level(logging.WARNING, logger="code_forge"):
            machine.run()
        assert not any(
            "state.json" in r.message for r in caplog.records
        )
