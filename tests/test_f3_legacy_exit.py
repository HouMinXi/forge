# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for F3 legacy-exit: pre-existing L0 -> advisory; new -> fix loop.

P1: pre-existing finding -> advisory, never drives verdict
P2: new fixable finding -> FIXED -> PASS (StubAutoFixer fixture)
P3: new unfixable finding -> UNCERTAIN/PENDING (NoChangeAutoFixer)
P4: DISPO-05 fires exactly once per fingerprint
"""

from pathlib import Path

from code_forge.autofix import NoChangeAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


# Unified diff: changes line 10 of test.py (adds a line)
_DIFF_CHANGES_LINE_10 = (
    "--- a/test.py\n"
    "+++ b/test.py\n"
    "@@ -7,3 +7,4 @@\n"
    " line7\n"
    " line8\n"
    " line9\n"
    "+new_line_10\n"
    " line10\n"
)


def _make_finding(
    fp="fp-test-1",
    file="test.py",
    line=20,
    disp=Disposition.CONFIRMED,
):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
        disposition=disp,
        file=file,
        line_range=[line, line],
        description="test finding at line %d" % line,
    )


def _make_resolved(git_diff=None, mode_hint="git"):
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint=mode_hint,
    )


class TestP1PreExistingToAdvisory:
    """P1: pre-existing L0 finding (not in diff) -> advisory, not verdict."""

    def test_pre_existing_finding_routed_to_advisory(self, tmp_path):
        """Finding on line 20, diff changes line 10 -> advisory."""
        finding_on_line_20 = _make_finding(line=20)

        def mock_l0(registry, files):
            return ([finding_on_line_20], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(
                git_diff=_DIFF_CHANGES_LINE_10
            ),
            source_hash="abc",
            baseline_spec_repr="test",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()

        # Pre-existing finding does NOT drive verdict.
        # With zero new findings entering the fix loop, the machine
        # should reach PASS (no blocking findings).
        assert verdict == Verdict.PASS

        # Finding appears in advisories (accumulates across rounds).
        advisories = machine._advisories
        assert len(advisories) >= 1
        first = advisories[0]
        assert first.file == "test.py"
        assert first.line_range == [20, 20]
        assert "pre-existing" in first.id

    def test_new_finding_enters_fix_loop(self, tmp_path):
        """Finding on line 10 (in diff) -> enters fix loop, not advisory."""
        finding_on_line_10 = _make_finding(line=10)

        def mock_l0(registry, files):
            return ([finding_on_line_10], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(
                git_diff=_DIFF_CHANGES_LINE_10
            ),
            source_hash="abc",
            baseline_spec_repr="test",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()

        # New finding enters fix loop (not routed to advisory).
        # NoChangeAutoFixer -> NO_CHANGE -> DISPO-05 -> UNCERTAIN
        # -> PENDING.  Zero advisories because the finding is in-diff.
        assert verdict == Verdict.PENDING
        assert len(machine._advisories) == 0

    def test_no_git_diff_no_filtering(self, tmp_path):
        """Non-git mode: no filtering, all findings enter fix loop."""
        finding = _make_finding(line=20)

        def mock_l0(registry, files):
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(git_diff=None),
            source_hash="abc",
            baseline_spec_repr="test",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
        )
        verdict = machine.run()

        # No filtering -> finding enters fix loop -> NO_CHANGE ->
        # DISPO-05 -> UNCERTAIN -> PENDING.
        assert verdict == Verdict.PENDING
        assert len(machine._advisories) == 0


class TestP3NewUnfixableConverges:
    """P3: new unfixable finding -> UNCERTAIN/PENDING, not ESCALATED."""

    def test_unfixable_reaches_pending_not_escalated(self, tmp_path):
        """NoChangeAutoFixer + new finding -> UNCERTAIN -> PENDING."""
        finding = _make_finding(line=10)

        def mock_l0(registry, files):
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(
                git_diff=_DIFF_CHANGES_LINE_10
            ),
            source_hash="abc",
            baseline_spec_repr="test",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_fix_attempts=3,
        )
        verdict = machine.run()

        # After 3 NO_CHANGE rounds: DISPO-05 promotes to UNCERTAIN,
        # then HOLD -> PENDING.  NOT ESCALATED.
        assert verdict == Verdict.PENDING


class TestP4Dispo05Once:
    """P4: DISPO-05 fires exactly once per fingerprint."""

    def test_promotion_happens_once(self, tmp_path):
        """After promotion to UNCERTAIN, fix_attempts stops growing."""
        finding = _make_finding(line=10)

        def mock_l0(registry, files):
            return ([finding], [])

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(
                git_diff=_DIFF_CHANGES_LINE_10
            ),
            source_hash="abc",
            baseline_spec_repr="test",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_fix_attempts=2,
        )
        verdict = machine.run()

        # After 2 NO_CHANGE -> DISPO-05 promotes -> UNCERTAIN -> PENDING.
        fp = finding.fingerprint
        assert verdict == Verdict.PENDING
        assert fp in machine._state.promoted_fingerprints

        # fix_attempts should be exactly 2 (not growing after promotion).
        assert machine._state.fix_attempts.get(fp, 0) == 2
