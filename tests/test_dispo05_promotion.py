# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for DISPO-05 promotion stickiness.

(a) CONFIRMED -> UNCERTAIN promotion after MAX_FIX_ATTEMPTS exhausted
(b) L0 re-detect post-promotion stays UNCERTAIN (FP-04 exception)
(c) DEFERRED to 02-04 (R2-3): ESCALATED-frozen exit
"""

from pathlib import Path


from code_forge.autofix import FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-stuck", disp=Disposition.CONFIRMED):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
        disposition=disp,
        file="test.py",
        line_range=[1, 1],
        description="stuck finding",
    )


def _make_resolved():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
    )


class TestPromotionOnBudgetExhausted:
    """(a) CONFIRMED -> UNCERTAIN promotion exactly once per fingerprint."""

    def test_promotion_after_max_attempts(self, tmp_path):
        """After MAX_FIX_ATTEMPTS PARSE_FAILs, CONFIRMED -> UNCERTAIN."""
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class ParseFailAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=ParseFailAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=10,
            max_fix_attempts=3,
        )
        verdict = machine.run()

        # After budget exhausted, finding promoted to UNCERTAIN
        # HOLD triggers -> Verdict.PENDING
        assert verdict == Verdict.PENDING

        # fix_attempts should be >= max_fix_attempts
        assert machine._state.fix_attempts.get("fp-stuck", 0) >= 3


class TestPostPromotionStickiness:
    """(b) Post-promotion: L0 re-detect stays UNCERTAIN (FP-04 exception)."""

    def test_l0_redetect_stays_uncertain(self, tmp_path):
        """After promotion, L0 re-detecting same fp must NOT re-CONFIRM."""
        call_count = {"n": 0}

        def mock_l0(registry, files):
            call_count["n"] += 1
            # Always re-detect the same finding
            return ([_make_finding()], [])

        class ParseFailAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=ParseFailAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=10,
            max_fix_attempts=3,
        )
        verdict = machine.run()

        # Should end in PENDING (HOLD) because UNCERTAIN sticks
        assert verdict == Verdict.PENDING

        # The finding should be UNCERTAIN in final state, not re-CONFIRMED
        stuck = [
            f for f in machine._state.findings
            if f.fingerprint == "fp-stuck"
        ]
        assert len(stuck) == 1
        assert stuck[0].disposition == Disposition.UNCERTAIN


class TestDeferredEscalatedFrozen:
    """(c) DEFERRED to 02-04: human re-CONFIRM -> ESCALATED frozen.

    This test documents the deferral; it does NOT implement the behavior.
    02-04 plan MUST add the predicate and test.
    """

    def test_deferred_marker(self):
        """02-04 scope: not implemented in 02-02."""
        # This test exists only to document the deferral per R2-3.
        # When 02-04 implements ESCALATED-frozen exit, this test
        # should be replaced with a real behavioral test.
        pass
