# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""#107: hold_reason must not outlive HOLD."""

import json
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def _finding(fp, source="L0"):
    return StateFinding(
        id="%s-%s" % (fp, source),
        fingerprint=fp,
        source=source,
        disposition=Disposition.UNCERTAIN,
        file="test.py",
        line_range=[1, 1],
        description="uncertain finding",
    )


def _machine(tmp_path, findings):
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("test.py")], baseline_content=None, git_diff=None, mode_hint="git"
        ),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda registry, files: (list(findings), []),
    )


def _disk(tmp_path):
    return json.loads((tmp_path / ".code-forge" / "state.json").read_text())


def test_hold_then_pass_clears_hold_reason(tmp_path):
    held = _machine(tmp_path, [_finding("fp-a")])
    assert held.run() == Verdict.PENDING
    assert _disk(tmp_path)["hold_reason"]

    passed = _machine(tmp_path, [])
    assert passed.run() == Verdict.PASS
    assert _disk(tmp_path)["hold_reason"] is None


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.ESCALATED])
def test_terminal_verdict_persists_without_hold_reason(tmp_path, verdict):
    machine = _machine(tmp_path, [])
    machine._state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
    machine._state.verdict = verdict
    machine._persist_state()
    assert _disk(tmp_path)["hold_reason"] is None


def test_pending_verdict_keeps_hold_reason(tmp_path):
    machine = _machine(tmp_path, [])
    machine._state.hold_reason = "1 UNCERTAIN finding(s) awaiting human disposition"
    machine._state.verdict = Verdict.PENDING
    machine._persist_state()
    assert _disk(tmp_path)["hold_reason"].startswith("1 UNCERTAIN")
