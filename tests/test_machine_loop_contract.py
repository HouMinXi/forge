# SPDX-License-Identifier: Apache-2.0
"""Preserve serial evaluation and latest-snapshot precedence during loop cleanup."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import Falsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, load_state, save_state


class RecordingFalsifier(Falsifier):
    def __init__(self, verdicts: dict[str, Disposition | Exception] | None = None):
        self.verdicts = verdicts or {}
        self.calls: list[str] = []

    def falsify(self, finding: StateFinding) -> Disposition:
        self.calls.append(finding.fingerprint)
        verdict = self.verdicts.get(finding.fingerprint, Disposition.CONFIRMED)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


@pytest.fixture
def machine(tmp_path):
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=RecordingFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("sample.py")], baseline_content=None,
            git_diff="diff --git a/sample.py b/sample.py\n", mode_hint="git",
        ),
        source_hash="sample-source", baseline_spec_repr="empty", cwd=tmp_path,
        registry={}, l0_runner=lambda registry, files: ([], []),
    )


def finding(
    fingerprint: str,
    disposition: Disposition = Disposition.UNCERTAIN,
    source: Literal["L1", "INFRA", "UNTRUSTED"] = "L1",
) -> StateFinding:
    return StateFinding(
        id=fingerprint, fingerprint=fingerprint, source=source,
        disposition=disposition, file="sample.py", line_range=[1, 1],
        description=f"Finding {fingerprint}",
    )


@pytest.mark.parametrize("size", [0, 1, 3])
def test_serial_falsify_order_and_one_call_per_candidate(machine, monkeypatch, size):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    candidates = [finding(f"fp-{index}") for index in range(size)]
    excerpts = [{"file": "sample.py"}]
    machine.l1_provider = lambda: (candidates, excerpts, Usage(), 0.0)
    result, returned_excerpts = machine._run_l1_phase()
    assert machine.falsifier.calls == [item.fingerprint for item in candidates]
    assert all(actual is expected for actual, expected in zip(result, candidates, strict=True))
    assert all(item.disposition == Disposition.CONFIRMED for item in result)
    assert returned_excerpts is excerpts


def test_serial_falsify_unexpected_exception_propagation(machine, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    failure = ValueError("transport contract failure")
    machine.falsifier = RecordingFalsifier({"fp-2": failure})
    candidates = [finding(f"fp-{index}") for index in range(1, 4)]
    machine.l1_provider = lambda: (candidates, [], Usage(), 0.0)
    with pytest.raises(ValueError, match="transport contract failure") as caught:
        machine._run_l1_phase()
    assert caught.value is failure
    assert machine.falsifier.calls == ["fp-1", "fp-2"]
    assert candidates[0].disposition == Disposition.CONFIRMED
    assert candidates[2].disposition == Disposition.UNCERTAIN


@pytest.mark.parametrize("source", ["INFRA", "UNTRUSTED"])
def test_serial_falsify_preserves_non_product_candidates(machine, monkeypatch, source):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    candidates = [finding("first", source=source), finding("second")]
    machine.l1_provider = lambda: (candidates, [], Usage(), 0.0)
    result, _ = machine._run_l1_phase()
    assert machine.falsifier.calls == ["second"]
    assert result[0] is candidates[0]
    assert result[0].disposition == Disposition.UNCERTAIN
    assert result[1].disposition == Disposition.CONFIRMED


@pytest.mark.parametrize("latest", [Disposition.DISMISSED, Disposition.STYLE,
                                    Disposition.CONFIRMED, Disposition.FIXED])
def test_latest_snapshot_wins(machine, latest):
    earlier = (Disposition.CONFIRMED if latest == Disposition.DISMISSED
               else Disposition.DISMISSED)
    machine._state.round_history = [
        {"dispositions": {"a": earlier.value, "b": "STYLE"}},
        {"dispositions": {"a": latest.value}},
        {}, {"dispositions": {}},
    ]
    findings = [finding("a"), finding("b"), finding("new")]
    result = machine._apply_dismissed_stickiness(findings)
    expected = (latest if latest in {Disposition.DISMISSED, Disposition.STYLE}
                else Disposition.UNCERTAIN)
    assert result is findings
    assert [item.disposition for item in result] == [
        expected, Disposition.STYLE, Disposition.UNCERTAIN,
    ]


@pytest.mark.parametrize("history", [[], [{}], [{"dispositions": {}}]])
def test_empty_history_leaves_current_disposition_unchanged(machine, history):
    machine._state.round_history = history
    findings = [finding("a", Disposition.CONFIRMED)]
    assert machine._apply_dismissed_stickiness(findings) is findings
    assert findings[0].disposition == Disposition.CONFIRMED


def test_real_state_roundtrip_preserves_latest_disposition(machine, tmp_path):
    for index, disposition in enumerate([Disposition.CONFIRMED, Disposition.DISMISSED], 1):
        machine._state.findings = [finding("a", disposition)]
        machine._append_round_snapshot(index, [], machine._state.findings)
    state_path = tmp_path / "state.json"
    save_state(machine._state, state_path)
    restored = load_state(state_path)
    assert restored is not None
    assert restored.round_history == machine._state.round_history
    machine._state = restored
    findings = [finding("a")]
    assert machine._apply_dismissed_stickiness(findings)[0].disposition == Disposition.DISMISSED
