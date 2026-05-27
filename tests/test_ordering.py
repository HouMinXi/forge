# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-08 intra-round ordering (3 cases a-c)."""

from pathlib import Path

from code_forge.autofix import FixOutcome, StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _make_finding(fp="fp-ord-1", disp=Disposition.CONFIRMED):
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


class TestLocalOrdering:
    """(a) LOCAL: L0 detect + autofix completes before L1 detect runs."""

    def test_l0_autofix_before_l1(self, tmp_path):
        """L1 provider records call order vs autofix."""
        events = []

        def mock_l0(registry, files):
            events.append("l0_detect")
            return ([_make_finding()], [])

        class RecordingAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                events.append("autofix")
                return FixOutcome.SUCCESS

        def l1_provider():
            events.append("l1_detect")
            return []

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=RecordingAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l1_provider=l1_provider,
        )
        machine.run()
        # L0 detect -> autofix -> L1 detect
        assert events[:3] == ["l0_detect", "autofix", "l1_detect"]


class TestL1SeesPostFixCode:
    """(b) L1 receives post-fix file content."""

    def test_l1_sees_fixed_state(self, tmp_path):
        """Autofixer mutates state; L1 provider verifies mutation."""
        fix_happened = {"value": False}

        def mock_l0(registry, files):
            return ([_make_finding()], [])

        class MutatingAutoFixer(StubAutoFixer):
            def fix(self, finding, mode_hint):
                fix_happened["value"] = True
                return FixOutcome.SUCCESS

        l1_saw_fix = {"value": False}

        def l1_provider():
            # L1 checks whether autofix ran before it
            l1_saw_fix["value"] = fix_happened["value"]
            return []

        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=MutatingAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=_make_resolved(),
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            l1_provider=l1_provider,
        )
        machine.run()
        assert l1_saw_fix["value"] is True


class TestCICallsL1:
    """(c) CI mode DOES call L1 (per LAYER0-07 SARIF mandate)."""

    def test_ci_invokes_l1(self, tmp_path):
        """Instrumented L1 provider records calls; CI count == 1."""
        l1_calls = {"count": 0}

        def mock_l0(registry, files):
            return ([], [])

        def l1_provider():
            l1_calls["count"] += 1
            return []

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
            l0_runner=mock_l0,
            l1_provider=l1_provider,
        )
        machine.run()
        assert l1_calls["count"] == 1
