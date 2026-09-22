# SPDX-License-Identifier: Apache-2.0
"""Unattended HOLD must not block on stdin.

A praise comment parked as UNCERTAIN used to enter run_hold_ui, which
calls input(). A non-interactive review then dies on EOF. The skip
switch records the finding and returns.
"""
from code_forge.disposition import Disposition
from code_forge.hold import run_hold_ui
from code_forge.state import Mode, State, StateFinding, Verdict, save_state


def _state(tmp_path):
    path = tmp_path / "state.json"
    state = State(
        mode=Mode.LOCAL,
        source_hash="abc",
        baseline_spec_repr="git:HEAD",
        findings=[
            StateFinding(
                id="f-1",
                fingerprint="fp-1",
                source="L1",
                disposition=Disposition.UNCERTAIN,
                file="a.py",
                line_range=[1, 1],
                description="The source correctly implements this.",
            )
        ],
        verdict=Verdict.PENDING,
        hold_reason="UNCERTAIN findings require human input",
    )
    save_state(state, path)
    return state, path


def test_noninteractive_hold_does_not_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_HOLD_NONINTERACTIVE", "1")
    state, path = _state(tmp_path)

    def boom(_prompt):
        raise AssertionError("input was called")

    run_hold_ui(state, path, input_fn=boom)
    assert state.findings[0].disposition == Disposition.UNCERTAIN
    assert state.hold_reason is None


def test_interactive_hold_still_prompts(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)
    state, path = _state(tmp_path)
    seen = []

    def answer(_prompt):
        seen.append(_prompt)
        return "s"

    run_hold_ui(state, path, input_fn=answer)
    assert seen
    assert state.findings[0].disposition == Disposition.UNCERTAIN
