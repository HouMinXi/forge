# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine LOCAL mode.

STATE-01/02/04: LOCAL loop, fixpoint, MAX_TOTAL_ROUNDS exhaustion.
"""

from pathlib import Path


from forge.autofix import FixOutcome, StubAutoFixer
from forge.baseline import ResolvedReview
from forge.disposition import Disposition
from forge.falsify import StubFalsifier
from forge.machine import StateMachine
from forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-local-1", disp=Disposition.CONFIRMED):
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


class TestLocalZeroFindings:
    """(a) Zero findings -> PASS round 0."""

    def test_pass_immediately(self, tmp_path):
        def mock_l0(registry, files):
            return ([], [])

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
        assert verdict == Verdict.PASS
        assert machine._state.converged is True
        assert machine._state.round == 0


class TestLocalAutofixSuccess:
    """(b) CONFIRMED + autofix SUCCESS -> FIXED -> next round clean -> PASS."""

    def test_fix_then_pass(self, tmp_path):
        round_counter = {"n": 0}

        def mock_l0(registry, files):
            n = round_counter["n"]
            round_counter["n"] += 1
            if n == 0:
                # Round 0: finding present
                return ([_make_finding()], [])
            # Round 1+: finding gone (fix succeeded)
            return ([], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),  # default SUCCESS
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()
        assert verdict == Verdict.PASS
        assert machine._state.converged is True


class TestLocalMaxRoundsExhausted:
    """(c) MAX_TOTAL_ROUNDS exhaust -> ESCALATED + diagnosis recorded."""

    def test_escalated_on_stuck(self, tmp_path):
        """CONFIRMED that re-appears every round, autofix NO_CHANGE.

        max_fix_attempts set higher than max_total_rounds so promotion
        to UNCERTAIN never happens -- CONFIRMED persists until
        MAX_TOTAL_ROUNDS exhaustion triggers ESCALATED.
        """
        def mock_l0(registry, files):
            return ([_make_finding()], [])

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
            l0_runner=mock_l0,
            max_total_rounds=5,
            max_fix_attempts=100,
        )
        verdict = machine.run()
        assert verdict == Verdict.ESCALATED
        assert machine._state.converged is False
        # STATE-05 diagnosis recorded in infra_errors
        assert any(
            "ESCALATED category=" in e
            for e in machine._state.infra_errors
        )


class TestLocalConvergedSemantics:
    """SC-17: LOCAL PASS -> converged=True, ESCALATED -> False,
    PENDING (HOLD) -> False.
    """

    def test_pass_converged_true(self, tmp_path):
        def mock_l0(registry, files):
            return ([], [])

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
        machine.run()
        assert machine._state.converged is True

    def test_escalated_converged_false(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

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
            l0_runner=mock_l0,
            max_total_rounds=3,
            max_fix_attempts=100,
        )
        machine.run()
        assert machine._state.converged is False


class TestPostRoundHook:
    """SC-15: post_round_hook(round_index) invoked at end of each round."""

    def test_hook_called_per_round(self, tmp_path):
        calls = []

        def mock_l0(registry, files):
            return ([], [])

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
            post_round_hook=lambda r: calls.append(r),
        )
        machine.run()
        assert calls == [0]  # PASS on round 0

    def test_hook_none_is_noop(self, tmp_path):
        """Default None post_round_hook does not raise."""
        def mock_l0(registry, files):
            return ([], [])

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
        machine.run()  # should not raise
