# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for ESCALATED-frozen predicate (5 cases a-e)."""

from pathlib import Path

from forge.autofix import FixOutcome, StubAutoFixer
from forge.baseline import ResolvedReview
from forge.disposition import Disposition, MAX_FIX_ATTEMPTS_PER_FINGERPRINT
from forge.falsify import StubFalsifier
from forge.hold import check_escalated_frozen, run_hold_ui
from forge.machine import StateMachine
from forge.state import Mode, State, StateFinding, Verdict, save_state


def _make_finding(fp="fp-ef-1", disp=Disposition.CONFIRMED):
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


class TestEmptyPromotedFingerprints:
    """(a) promoted_fingerprints empty -> check returns False."""

    def test_empty_returns_false(self):
        state = State()
        state.findings = [_make_finding()]
        state.fix_attempts = {"fp-ef-1": MAX_FIX_ATTEMPTS_PER_FINGERPRINT}
        assert check_escalated_frozen(state) is False


class TestReConfirmedPromoted:
    """(b) fp in promoted + CONFIRMED + fix_attempts=MAX -> True."""

    def test_re_confirmed_is_true(self):
        state = State()
        state.findings = [_make_finding()]
        state.fix_attempts = {
            "fp-ef-1": MAX_FIX_ATTEMPTS_PER_FINGERPRINT
        }
        state.promoted_fingerprints = {"fp-ef-1"}
        state.hold_reason = None
        assert check_escalated_frozen(state) is True

    def test_hold_reason_not_none_blocks(self):
        """H4 guard: hold_reason not None -> False even if all else matches."""
        state = State()
        state.findings = [_make_finding()]
        state.fix_attempts = {
            "fp-ef-1": MAX_FIX_ATTEMPTS_PER_FINGERPRINT
        }
        state.promoted_fingerprints = {"fp-ef-1"}
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        assert check_escalated_frozen(state) is False


class TestUncertainNotReConfirmed:
    """(c) fp in promoted + UNCERTAIN -> False."""

    def test_uncertain_returns_false(self):
        state = State()
        state.findings = [
            _make_finding(disp=Disposition.UNCERTAIN)
        ]
        state.fix_attempts = {
            "fp-ef-1": MAX_FIX_ATTEMPTS_PER_FINGERPRINT
        }
        state.promoted_fingerprints = {"fp-ef-1"}
        assert check_escalated_frozen(state) is False


class TestFirstTimeAtMax:
    """(d) fp NOT in promoted + CONFIRMED + fix_attempts=MAX -> False."""

    def test_first_time_max_returns_false(self):
        state = State()
        state.findings = [_make_finding()]
        state.fix_attempts = {
            "fp-ef-1": MAX_FIX_ATTEMPTS_PER_FINGERPRINT
        }
        state.promoted_fingerprints = set()
        assert check_escalated_frozen(state) is False


class TestIntegrationHoldResumeCycle:
    """(e) Full HOLD-resume cycle with re-CONFIRM -> ESCALATED."""

    def test_full_cycle(self, tmp_path):
        """Promote finding -> HOLD -> human re-CONFIRM -> ESCALATED."""
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
            max_total_rounds=20,
            max_fix_attempts=MAX_FIX_ATTEMPTS_PER_FINGERPRINT,
        )

        # Phase 1: run until HOLD (promotion -> UNCERTAIN -> HOLD)
        verdict = machine.run()
        assert verdict == Verdict.PENDING
        # R3-8: hold_reason set at HOLD entry
        assert machine._state.hold_reason is not None
        assert "UNCERTAIN" in machine._state.hold_reason
        assert machine._state.verdict == Verdict.PENDING

        # Phase 2: human re-CONFIRMs via HOLD UI
        state_path = tmp_path / ".forge" / "state.json"
        inputs = iter(["c"])
        run_hold_ui(
            machine._state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: None,
        )
        # After HOLD UI: hold_reason cleared
        assert machine._state.hold_reason is None

        # Phase 3: re-run -> ESCALATED frozen (re-CONFIRM of promoted)
        # Reset round count for re-run
        verdict2 = machine.run()
        assert verdict2 == Verdict.ESCALATED
        assert any(
            "ESCALATED frozen (DISPO-05)" in e
            for e in machine._state.infra_errors
        )
