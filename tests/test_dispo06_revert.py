# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for DISPO-06 auto-fix revert paths.

(a) PARSE_FAIL -> revert_fn(finding) called + fix_attempts++
(b) Non-git mode PARSE_FAIL -> revert_fn called (mode_hint differs)
(c) Revert preserves fix_attempts increment toward MAX budget
(d) NO_CHANGE -> fix_attempts++ + stays CONFIRMED, revert_fn NOT called
(e) EXCEPTION -> fix_attempts++ + infra_errors + stays CONFIRMED
"""

from pathlib import Path


from forge.autofix import FixOutcome, StubAutoFixer
from forge.baseline import ResolvedReview
from forge.disposition import Disposition
from forge.falsify import StubFalsifier
from forge.machine import StateMachine
from forge.state import Mode, StateFinding


def _make_finding(fp="fp-rev-1", disp=Disposition.CONFIRMED):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source="L0",
        disposition=disp,
        file="test.py",
        line_range=[1, 1],
        description="finding for revert test",
    )


def _make_resolved(mode_hint="git"):
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint=mode_hint,
    )


class TestParseFail:
    """(a) PARSE_FAIL -> revert_fn(finding) + fix_attempts++ + CONFIRMED."""

    def test_revert_fn_called_on_parse_fail(self, tmp_path):
        reverted = []

        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class PFAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=PFAutoFixer(),
            revert_fn=lambda f: reverted.append(f),
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=2,
            max_fix_attempts=2,
        )
        machine.run()

        # revert_fn was called at least once
        assert len(reverted) >= 1
        # Called with a StateFinding
        assert reverted[0].fingerprint == "fp-rev-1"

    def test_fix_attempts_incremented(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class PFAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=PFAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=2,
            max_fix_attempts=5,
        )
        machine.run()
        assert machine._state.fix_attempts.get("fp-rev-1", 0) >= 1


class TestNonGitParseFail:
    """(b) Non-git mode PARSE_FAIL -> revert_fn called."""

    def test_non_git_revert(self, tmp_path):
        reverted = []

        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class PFAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=PFAutoFixer(),
            revert_fn=lambda f: reverted.append(f),
            resolved_review=_make_resolved(mode_hint="non-git"),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=2,
            max_fix_attempts=2,
        )
        machine.run()
        assert len(reverted) >= 1


class TestRevertPreservesBudget:
    """(c) Revert preserves fix_attempts toward MAX budget."""

    def test_budget_consumed_by_revert(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class PFAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.PARSE_FAIL

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=PFAutoFixer(),
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
        machine.run()

        # After 3 PARSE_FAILs, promotion to UNCERTAIN should trigger
        assert machine._state.fix_attempts.get("fp-rev-1", 0) >= 3


class TestNoChangeOutcome:
    """(d) NO_CHANGE -> fix_attempts++ + stays CONFIRMED, no revert."""

    def test_no_change_no_revert(self, tmp_path):
        reverted = []

        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class NCAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                return FixOutcome.NO_CHANGE

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=NCAutoFixer(),
            revert_fn=lambda f: reverted.append(f),
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=2,
            max_fix_attempts=5,
        )
        machine.run()

        # revert_fn should NOT be called for NO_CHANGE
        assert len(reverted) == 0
        # But fix_attempts should be incremented
        assert machine._state.fix_attempts.get("fp-rev-1", 0) >= 1


class TestExceptionOutcome:
    """(e) EXCEPTION -> fix_attempts++ + infra_errors + stays CONFIRMED."""

    def test_exception_records_infra(self, tmp_path):
        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class ExcAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                raise RuntimeError("AI model timeout")

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=ExcAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            max_total_rounds=2,
            max_fix_attempts=5,
        )
        machine.run()

        # infra_errors should record the exception
        assert any(
            "autofixer exception" in e
            for e in machine._state.infra_errors
        )
        # fix_attempts incremented
        assert machine._state.fix_attempts.get("fp-rev-1", 0) >= 1
