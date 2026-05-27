# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for GATE-01b HOLD entry conditions.

HOLD = non-terminal LOCAL-mode pause when UNCERTAIN > 0 AND
unfixed CONFIRMED == 0. Returns Verdict.PENDING.
"""

from pathlib import Path


from code_forge.autofix import FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-h-1", disp=Disposition.CONFIRMED):
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


class TestHoldEntry:
    """(a) HOLD entered: UNCERTAIN > 0 AND unfixed CONFIRMED == 0."""

    def test_uncertain_only_enters_hold(self, tmp_path):
        """L1 finding dispositioned as UNCERTAIN, no CONFIRMED."""
        unc = _make_finding(fp="fp-unc", disp=Disposition.UNCERTAIN)

        def mock_l0(registry, files):
            return ([unc], [])

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
            l0_runner=mock_l0,
        )
        verdict = machine.run()
        assert verdict == Verdict.PENDING


class TestHoldNotEnteredWithConfirmed:
    """(b) HOLD NOT entered while unfixed CONFIRMED remain."""

    def test_confirmed_prevents_hold(self, tmp_path):
        """Both UNCERTAIN and CONFIRMED present -> loop continues."""
        unc = _make_finding(fp="fp-unc", disp=Disposition.UNCERTAIN)
        conf = _make_finding(fp="fp-conf", disp=Disposition.CONFIRMED)

        round_counter = {"n": 0}

        def mock_l0(registry, files):
            n = round_counter["n"]
            round_counter["n"] += 1
            if n < 3:
                return ([unc, conf], [])
            return ([unc, conf], [])

        class NeverFixAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NeverFixAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=5,
            max_fix_attempts=10,
        )
        verdict = machine.run()
        # Should exhaust rounds, not HOLD (CONFIRMED still present)
        assert verdict == Verdict.ESCALATED


class TestCINeverHolds:
    """(c) CI mode never HOLDs."""

    def test_ci_uncertain_is_pass_not_hold(self, tmp_path):
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
        assert verdict == Verdict.PASS  # not PENDING
