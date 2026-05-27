# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for GATE-03 PASS determinism.

Given same disposition ledger, repeated run yields same Verdict.
"""

from pathlib import Path


from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-d-1", disp=Disposition.CONFIRMED):
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


class TestDeterministicVerdict:
    """(a) Same disposition ledger -> same Verdict on rerun."""

    def test_clean_pass_deterministic(self, tmp_path):
        results = []
        for _ in range(3):
            machine = StateMachine(
                mode=Mode.LOCAL,
                falsifier=StubFalsifier(),
                autofixer=StubAutoFixer(),
                revert_fn=lambda f: None,
                resolved_review=_make_resolved(),
                source_hash="abc",
                baseline_spec_repr="empty",
                cwd=tmp_path,
                registry={},
                l0_runner=lambda r, f: ([], []),
            )
            results.append(machine.run())
        assert all(v == Verdict.PASS for v in results)

    def test_ci_fail_deterministic(self, tmp_path):
        results = []
        for _ in range(3):
            machine = StateMachine(
                mode=Mode.CI,
                falsifier=StubFalsifier(),
                autofixer=StubAutoFixer(),
                revert_fn=lambda f: None,
                resolved_review=_make_resolved(),
                source_hash="abc",
                baseline_spec_repr="empty",
                cwd=tmp_path,
                registry={},
                l0_runner=lambda r, f: (
                    [_make_finding()], []
                ),
            )
            results.append(machine.run())
        assert all(v == Verdict.FAIL for v in results)


class TestPassRequiresTerminalConfirmed:
    """(b) PASS requires every CONFIRMED reached terminal FIXED|DISMISSED."""

    def test_active_confirmed_prevents_pass(self, tmp_path):
        """If CONFIRMED persists, fixpoint never reached -> no PASS."""
        from code_forge.autofix import FixOutcome

        class NoChangeAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda r, f: ([_make_finding()], []),
            max_total_rounds=3,
            max_fix_attempts=100,
        )
        verdict = machine.run()
        assert verdict != Verdict.PASS


class TestLocalPassRequiresUncertainDispositioned:
    """(c) LOCAL PASS requires UNCERTAIN dispositioned;
    CI PASS treats UNCERTAIN as warning (non-blocking).
    """

    def test_local_uncertain_prevents_pass(self, tmp_path):
        """LOCAL with UNCERTAIN -> HOLD (PENDING), not PASS."""
        unc = _make_finding(fp="fp-unc", disp=Disposition.UNCERTAIN)
        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda r, f: ([unc], []),
        )
        verdict = machine.run()
        assert verdict == Verdict.PENDING  # HOLD, not PASS

    def test_ci_uncertain_is_pass(self, tmp_path):
        """CI with UNCERTAIN -> PASS (non-blocking)."""
        unc = _make_finding(fp="fp-unc", disp=Disposition.UNCERTAIN)
        machine = StateMachine(
            mode=Mode.CI,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda r, f: ([unc], []),
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
