# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for HOLD UX (a-h)."""


import pytest

from code_forge.disposition import Disposition
from code_forge.hold import HoldAborted, run_hold_ui
from code_forge.state import State, StateFinding


def _make_state(*findings):
    """Build a State with the given findings."""
    state = State()
    state.findings = list(findings)
    state.dispositions = {f.id: f.disposition for f in findings}
    return state


def _make_finding(
    fid="f1", fp="f1", disp=Disposition.UNCERTAIN, desc="test"
):
    return StateFinding(
        id=fid,
        fingerprint=fp,
        source="L0",
        disposition=disp,
        file="src/foo.py",
        line_range=[10, 10],
        description=desc,
    )


class TestConfirmInput:
    """(a) "c" input -> disposition=CONFIRMED."""

    def test_confirm(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["c"])
        output = []
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: output.append(msg),
        )
        assert finding.disposition == Disposition.CONFIRMED
        # H5: dispositions keyed by f.id
        assert state.dispositions[finding.id] == Disposition.CONFIRMED


class TestDismissInput:
    """(b) "d" input -> disposition=DISMISSED."""

    def test_dismiss(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["d"])
        output = []
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: output.append(msg),
        )
        assert finding.disposition == Disposition.DISMISSED


class TestSkipInput:
    """(c) "s" input -> disposition stays UNCERTAIN."""

    def test_skip(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["s"])
        output = []
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: output.append(msg),
        )
        assert finding.disposition == Disposition.UNCERTAIN


class TestInvalidInput:
    """(d) invalid input ("x") -> reprompt."""

    def test_reprompt(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["x", "c"])
        output = []
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: output.append(msg),
        )
        assert finding.disposition == Disposition.CONFIRMED
        assert any("invalid input" in m for m in output)


class TestEof:
    """(e) Ctrl+D (EOF) -> raise HoldAborted."""

    def test_eof_aborts(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        def eof_input(prompt):
            raise EOFError

        with pytest.raises(HoldAborted, match="HOLD UX aborted by user"):
            run_hold_ui(state, state_path, input_fn=eof_input)


class TestZeroUncertain:
    """(f) zero UNCERTAIN findings -> immediate return, no I/O."""

    def test_no_io_on_empty(self, tmp_path):
        finding = _make_finding(disp=Disposition.CONFIRMED)
        state = _make_state(finding)
        state.hold_reason = "something"
        state_path = tmp_path / ".code-forge" / "state.json"

        output = []
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: "should not be called",
            output_fn=lambda msg: output.append(msg),
        )
        assert len(output) == 0
        assert state.hold_reason is None


class TestHoldReasonCleared:
    """(g) hold_reason cleared after all findings processed."""

    def test_hold_reason_none_after(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["c"])
        run_hold_ui(
            state, state_path,
            input_fn=lambda prompt: next(inputs),
            output_fn=lambda msg: None,
        )
        assert state.hold_reason is None


class TestQuitInput:
    """(h) "q" input -> raise HoldAborted (same message as EOF)."""

    def test_quit_aborts(self, tmp_path):
        finding = _make_finding()
        state = _make_state(finding)
        state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
        state_path = tmp_path / ".code-forge" / "state.json"

        inputs = iter(["q"])
        with pytest.raises(HoldAborted, match="HOLD UX aborted by user"):
            run_hold_ui(
                state, state_path,
                input_fn=lambda prompt: next(inputs),
            )
