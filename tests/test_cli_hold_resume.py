# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""HOLD entry + UX + re-run cycle tests."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge.llm_invoke import Usage
from code_forge.cli import MAX_HOLD_CYCLES, _run_hold_loop
from code_forge.hold import HoldAborted
from code_forge.state import (
    Disposition,
    Mode,
    State,
    StateFinding,
    Verdict,
    save_state,
)


def _make_state_with_finding(
    disposition=Disposition.UNCERTAIN,
    verdict=Verdict.PENDING,
) -> State:
    """Create a State with one UNCERTAIN finding."""
    return State(
        mode=Mode.LOCAL,
        source_hash="abc123",
        baseline_spec_repr="git:HEAD",
        findings=[
            StateFinding(
                id="f-1",
                fingerprint="fp-1",
                source="L0",
                disposition=disposition,
                file="a.py",
                line_range=[1, 5],
                description="test finding",
            ),
        ],
        verdict=verdict,
        hold_reason="UNCERTAIN findings require human input",
    )


class TestHoldAbortedExit:
    """SC-14: HoldAborted -> exit 0 + state preserved."""

    def test_hold_aborted_returns_pending(self, tmp_path):
        """HoldAborted -> Verdict.PENDING (main maps to exit 0)."""
        state_path = tmp_path / ".code-forge" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        s = _make_state_with_finding()
        save_state(s, state_path)

        call_count = [0]

        def mock_sm_run(self_sm):
            call_count[0] += 1
            return Verdict.PENDING

        with patch(
            "code_forge.cli.StateMachine.run", mock_sm_run
        ), patch(
            "code_forge.cli.run_hold_ui",
            side_effect=HoldAborted("user quit"),
        ):
            verdict = _run_hold_loop(
                mode=Mode.LOCAL,
                falsifier=MagicMock(),
                autofixer=MagicMock(),
                revert_fn=MagicMock(),
                resolved=MagicMock(),
                source_hash="abc123",
                baseline_repr="git:HEAD",
                cwd=tmp_path,
                registry={},
                max_rounds=20,
                max_fix_attempts=3,
                state_path=state_path,
                l1_provider=lambda: ([], Usage(), 0.0),
                input_fn=lambda p: "q",
                output_fn=lambda m: None,
            )

        assert verdict == Verdict.PENDING
        assert call_count[0] == 1


class TestHoldResumeTerminal:
    """HOLD -> terminal verdict on re-run."""

    def test_pending_then_pass(self, tmp_path):
        """PENDING on first run, PASS on second -> PASS."""
        state_path = tmp_path / ".code-forge" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        s = _make_state_with_finding()
        save_state(s, state_path)

        results = iter([Verdict.PENDING, Verdict.PASS])

        def mock_sm_run(self_sm):
            return next(results)

        with patch(
            "code_forge.cli.StateMachine.run", mock_sm_run
        ), patch(
            "code_forge.cli.run_hold_ui",
        ):
            verdict = _run_hold_loop(
                mode=Mode.LOCAL,
                falsifier=MagicMock(),
                autofixer=MagicMock(),
                revert_fn=MagicMock(),
                resolved=MagicMock(),
                source_hash="abc123",
                baseline_repr="git:HEAD",
                cwd=tmp_path,
                registry={},
                max_rounds=20,
                max_fix_attempts=3,
                state_path=state_path,
                l1_provider=lambda: ([], Usage(), 0.0),
                input_fn=lambda p: "d",
                output_fn=lambda m: None,
            )

        assert verdict == Verdict.PASS


class TestMaxHoldCyclesExhaustion:
    """SC-40 R3-2: MAX_HOLD_CYCLES -> ESCALATED."""

    def test_max_cycles_exhausted(self, tmp_path, monkeypatch):
        """Exhaust MAX_HOLD_CYCLES -> ESCALATED + infra_errors."""
        state_path = tmp_path / ".code-forge" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        s = _make_state_with_finding()
        save_state(s, state_path)

        # Monkeypatch MAX_HOLD_CYCLES to 2 for fast test.
        monkeypatch.setattr("code_forge.cli.MAX_HOLD_CYCLES", 2)

        def mock_sm_run(self_sm):
            return Verdict.PENDING

        with patch(
            "code_forge.cli.StateMachine.run", mock_sm_run
        ), patch(
            "code_forge.cli.run_hold_ui",
        ):
            verdict = _run_hold_loop(
                mode=Mode.LOCAL,
                falsifier=MagicMock(),
                autofixer=MagicMock(),
                revert_fn=MagicMock(),
                resolved=MagicMock(),
                source_hash="abc123",
                baseline_repr="git:HEAD",
                cwd=tmp_path,
                registry={},
                max_rounds=20,
                max_fix_attempts=3,
                state_path=state_path,
                l1_provider=lambda: ([], Usage(), 0.0),
                input_fn=lambda p: "s",
                output_fn=lambda m: None,
            )

        assert verdict == Verdict.ESCALATED

        # Verify state.json persisted with infra_errors.
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["verdict"] == "ESCALATED"
        assert any(
            "MAX_HOLD_CYCLES=2 exhausted" in e
            for e in data["infra_errors"]
        )
        assert data["converged"] is False


class TestMaxHoldNoneStateFallback:
    """SC-49 R4-L3: load_state returns None at MAX -> fresh State."""

    def test_none_state_fallback(self, tmp_path, monkeypatch):
        """State.json deleted mid-run -> construct fresh State."""
        state_path = tmp_path / ".code-forge" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        s = _make_state_with_finding()
        save_state(s, state_path)

        monkeypatch.setattr("code_forge.cli.MAX_HOLD_CYCLES", 1)

        def mock_sm_run(self_sm):
            return Verdict.PENDING

        # Patch load_state in state module to return None at the
        # MAX exhaustion path (simulates state.json deleted).
        from code_forge.state import load_state as real_load
        load_call_count = [0]

        def patched_load(path):
            load_call_count[0] += 1
            # First call (inside loop body) returns real state.
            # Second call (at MAX exhaustion) returns None.
            if load_call_count[0] == 1:
                return real_load(path)
            return None

        with patch(
            "code_forge.cli.StateMachine.run", mock_sm_run
        ), patch(
            "code_forge.cli.run_hold_ui",
        ), patch(
            "code_forge.state.load_state", side_effect=patched_load,
        ):
            verdict = _run_hold_loop(
                mode=Mode.LOCAL,
                falsifier=MagicMock(),
                autofixer=MagicMock(),
                revert_fn=MagicMock(),
                resolved=MagicMock(),
                source_hash="hash123",
                baseline_repr="git:HEAD",
                cwd=tmp_path,
                registry={},
                max_rounds=20,
                max_fix_attempts=3,
                state_path=state_path,
                l1_provider=lambda: ([], Usage(), 0.0),
                input_fn=lambda p: "s",
                output_fn=lambda m: None,
            )

        assert verdict == Verdict.ESCALATED
        # Verify fresh state was created and persisted.
        assert state_path.exists()
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["verdict"] == "ESCALATED"
        assert data["source_hash"] == "hash123"
