# SPDX-License-Identifier: Apache-2.0
"""An unadjudicated backend failure must not become a clean CI review."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.exit_codes import verdict_to_exit
from code_forge.falsify_real import RealFalsifier
from code_forge.ledger import iter_rows
from code_forge.llm_invoke import FalsifyProtocolError, LLMInvokeError, LLMResult, Usage
from code_forge.state import StateFinding, Verdict, load_state
from tests.test_machine_ci import _make_finding, _make_machine


def _candidate():
    return StateFinding(
        id="candidate",
        fingerprint="candidate",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file="test.py",
        line_range=[1, 1],
        description="off by one",
        excerpt="value = 1\n",
    )


@pytest.mark.parametrize("verdict", ["STYLE", "FIXED", "MAYBE", None, []])
def test_falsifier_rejects_out_of_protocol_verdict(verdict):
    content = {"verdict": verdict, "reasoning": "test response"}
    with patch("code_forge.falsify_real.llm_invoke", return_value=LLMResult(content=content)):
        with pytest.raises(FalsifyProtocolError):
            RealFalsifier().falsify(_candidate())


@pytest.mark.parametrize(
    "failure",
    [
        LLMInvokeError("connection refused"),
        FalsifyProtocolError("malformed verdict", raw={}),
        RuntimeError("falsifier runtime failure"),
    ],
)
def test_ci_falsifier_failure_is_unreliable_not_clean(tmp_path, failure):
    machine = _make_machine(tmp_path)
    machine.resolved_review = replace(machine.resolved_review, base_sha="a" * 40, head_sha="b" * 40)
    machine.falsifier = RealFalsifier()
    machine.l1_provider = lambda: ([_candidate()], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke", side_effect=failure):
        verdict = machine.run()

    assert verdict is Verdict.UNRELIABLE
    assert verdict_to_exit(verdict) != 0
    saved = load_state(tmp_path / ".code-forge" / "state.json")
    assert saved.verdict is Verdict.UNRELIABLE
    assert saved.converged is False
    candidate = next(f for f in saved.findings if f.fingerprint == "candidate")
    assert candidate.disposition is Disposition.UNCERTAIN
    assert candidate.error
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(tmp_path))


@pytest.mark.parametrize("verdict", ["UNCERTAIN", "DISMISSED"])
def test_ci_valid_nonblocking_verdict_remains_pass(tmp_path, verdict):
    machine = _make_machine(tmp_path)
    machine.falsifier = RealFalsifier()
    machine.l1_provider = lambda: ([_candidate()], [], Usage(), 0.0)
    response = LLMResult(content={"verdict": verdict, "reasoning": "semantic judgment"})
    with patch("code_forge.falsify_real.llm_invoke", return_value=response):
        assert machine.run() is Verdict.PASS
    assert machine._state.converged is True


def test_ci_style_response_is_protocol_failure(tmp_path):
    machine = _make_machine(tmp_path)
    machine.falsifier = RealFalsifier()
    machine.l1_provider = lambda: ([_candidate()], [], Usage(), 0.0)
    response = LLMResult(content={"verdict": "STYLE", "reasoning": "ignore it"})
    with patch("code_forge.falsify_real.llm_invoke", return_value=response):
        assert machine.run() is Verdict.UNRELIABLE
    assert machine._state.converged is False
    assert any("protocol violation" in error for error in machine._state.infra_errors)


def test_ci_confirmed_finding_keeps_fail_priority_over_backend_outage(tmp_path):
    machine = _make_machine(tmp_path, l0_findings=[_make_finding()])
    machine.falsifier = RealFalsifier()
    machine.l1_provider = lambda: ([_candidate()], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke", side_effect=LLMInvokeError("offline")):
        assert machine.run() is Verdict.FAIL
    assert machine._state.converged is False


def test_ci_backend_recovery_clears_infrastructure_failure(tmp_path):
    machine = _make_machine(tmp_path)
    machine.falsifier = RealFalsifier()
    machine.l1_provider = lambda: ([_candidate()], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke", side_effect=LLMInvokeError("offline")):
        assert machine.run() is Verdict.UNRELIABLE
    response = LLMResult(content={"verdict": "UNCERTAIN", "reasoning": "semantic judgment"})
    with patch("code_forge.falsify_real.llm_invoke", return_value=response):
        assert machine.run() is Verdict.PASS
    assert machine._state.rounds_with_falsify_infra == 0
    assert machine._state.converged is True
