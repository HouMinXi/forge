# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine CI mode.

STATE-02/03: CI linear -- single round, FAIL on CONFIRMED, PASS otherwise.
"""

from pathlib import Path


from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-ci-1", disp=Disposition.CONFIRMED):
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


def _make_machine(tmp_path, l0_findings=None, l0_infra=None):
    """Build a CI-mode StateMachine with injectable L0 results."""
    findings = l0_findings if l0_findings is not None else []
    infra = l0_infra if l0_infra is not None else []

    def mock_l0(registry, files):
        return (findings, infra)

    return StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash="abc123",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=mock_l0,
    )


class TestCIZeroFindings:
    """(a) Zero CONFIRMED -> PASS + converged=True."""

    def test_pass_on_clean(self, tmp_path):
        machine = _make_machine(tmp_path, l0_findings=[])
        verdict = machine.run()
        assert verdict == Verdict.PASS

    def test_converged_true_on_pass(self, tmp_path):
        machine = _make_machine(tmp_path, l0_findings=[])
        machine.run()
        assert machine._state.converged is True


class TestCIConfirmedFail:
    """(b) Any CONFIRMED -> FAIL after 1 round + converged=False (R1 H5)."""

    def test_fail_on_confirmed(self, tmp_path):
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        assert verdict == Verdict.FAIL

    def test_converged_false_on_fail(self, tmp_path):
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.converged is False

    def test_single_round_only(self, tmp_path):
        """CI executes exactly 1 round."""
        findings = [_make_finding()]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.round == 0
        assert len(machine._state.round_history) == 1


class TestCIUncertainPass:
    """(c) UNCERTAIN only -> PASS (CI non-blocking) + converged=True."""

    def test_uncertain_is_pass(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        assert verdict == Verdict.PASS

    def test_converged_true_on_uncertain_pass(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        machine.run()
        assert machine._state.converged is True


class TestCINeverHolds:
    """CI mode never enters HOLD."""

    def test_uncertain_does_not_hold(self, tmp_path):
        findings = [_make_finding(
            fp="fp-unc",
            disp=Disposition.UNCERTAIN,
        )]
        machine = _make_machine(tmp_path, l0_findings=findings)
        verdict = machine.run()
        # CI returns PASS not PENDING
        assert verdict == Verdict.PASS
