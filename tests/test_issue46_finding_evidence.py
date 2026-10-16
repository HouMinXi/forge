# SPDX-License-Identifier: Apache-2.0
"""Issue #46: a falsifier CONFIRMED without bound finding evidence must not
silently become an ordinary UNCERTAIN that lets CI PASS."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import Falsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, _json_to_state_findings
from code_forge.state import Mode, StateFinding
from code_forge.verify import finding_excerpt_binding

DIFF = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def minimum(a, b):\n"
    "-    return min(a, b)\n"
    "+    return max(a, b)\n"
)


class Confirming(Falsifier):
    def falsify(self, finding: StateFinding) -> Disposition:
        return Disposition.CONFIRMED


def _machine(tmp_path, mode=Mode.CI, diff=DIFF):
    return StateMachine(
        mode=mode,
        falsifier=Confirming(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("calc.py")],
            baseline_content=None,
            git_diff=diff,
            mode_hint="git",
        ),
        source_hash="h",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda registry, files: ([], []),
    )


ENVELOPE_ONLY = {
    "findings": [{"file": "calc.py", "line": 2, "severity": "P1",
                  "description": "minimum returns the maximum"}],
    "code_excerpts": [{"file": "calc.py", "start_line": 1, "end_line": 2,
                       "content": "def minimum(a, b):\n    return max(a, b)"}],
}


def test_contract_requests_finding_owned_excerpt():
    assert '"excerpt"' in REVIEW_JSON_CONTRACT


@pytest.mark.parametrize(
    "excerpt,expected",
    [
        ("    return max(a, b)", True),
        ("def minimum(a, b):\n    return max(a, b)", True),
        ("    return min(a, b)", False),  # pre-image / invented
        ("def minimum(a, b):", False),  # real source, does not cover line 2
        (None, False),
        ("   ", False),
    ],
)
def test_binding(excerpt, expected):
    assert finding_excerpt_binding(DIFF, "calc.py", 2, excerpt) is expected


def test_binding_wrong_file():
    assert finding_excerpt_binding(DIFF, "other.py", 2, "    return max(a, b)") is False


def test_binding_unsupported_without_diff():
    assert finding_excerpt_binding(None, "calc.py", 2, "x") is None


def test_envelope_only_confirmed_does_not_pass_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    cands = _json_to_state_findings(ENVELOPE_ONLY, "qodo")
    m.l1_provider = lambda: (cands, ENVELOPE_ONLY["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    f = result[0]
    assert f.disposition == Disposition.UNCERTAIN
    assert "returned CONFIRMED" in (f.error or "")
    assert f in m._falsify_failed_findings
    assert m._state.rounds_with_falsify_infra > 0  # CI verdict gate -> FAIL, not PASS


def test_invalid_owned_excerpt_does_not_pass_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    data = {**ENVELOPE_ONLY, "findings": [{**ENVELOPE_ONLY["findings"][0], "excerpt": "    return min(a, b)"}]}
    cands = _json_to_state_findings(data, "qodo")
    m.l1_provider = lambda: (cands, data["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.UNCERTAIN
    assert m._state.rounds_with_falsify_infra > 0


def test_bound_confirmed_stays_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_FALSIFY_WORKERS", "1")
    m = _machine(tmp_path)
    data = {**ENVELOPE_ONLY, "findings": [{**ENVELOPE_ONLY["findings"][0], "excerpt": "    return max(a, b)"}]}
    cands = _json_to_state_findings(data, "qodo")
    m.l1_provider = lambda: (cands, data["code_excerpts"], Usage(), 0.0)
    result, _ = m._run_l1_phase()
    assert result[0].disposition == Disposition.CONFIRMED
    assert not m._falsify_failed_findings
    assert m._state.rounds_with_falsify_infra == 0
