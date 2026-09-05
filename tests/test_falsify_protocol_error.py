# SPDX-License-Identifier: Apache-2.0
"""Phase 59-A1: a malformed falsifier answer is a protocol violation.

Before this change, RealFalsifier.falsify turned a non-dict response, a
missing verdict key, or an unknown verdict string into Disposition.UNCERTAIN
(falsify_real.py:68-80). machine.py then could not tell that apart from
the model saying "I am not sure". After: those three shapes raise
FalsifyProtocolError, a LLMInvokeError subclass, and machine.py's infra
path names the cause in f.error and state.infra_errors.

Clean-round behaviour is unchanged: the finding is still UNCERTAIN and
clause d (machine.py:1248-1251) still resets. This task is attribution.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import FalsifyProtocolError, LLMResult, Usage
from code_forge.state import StateFinding


def _finding() -> StateFinding:
    return StateFinding(
        id="f1", fingerprint="fp-1", source="L1",
        disposition=Disposition.CONFIRMED,
        file="a.py", line_range=[1, 2], description="off by one",
    )


@pytest.mark.parametrize("content", [
    "not json",
    {"reasoning": "no verdict key"},
    {"verdict": None, "reasoning": "x"},
    {"verdict": "MAYBE", "reasoning": "x"},
    {"verdict": " CONFIRMED ", "reasoning": "x"},
])
def test_malformed_response_raises_protocol_error(content):
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content=content)
        with pytest.raises(FalsifyProtocolError) as ei:
            RealFalsifier(backend=MagicMock()).falsify(_finding())
    assert ei.value.raw == content


def test_valid_verdict_still_returns_disposition():
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(
            content={"verdict": "DISMISSED", "reasoning": "x"})
        assert RealFalsifier(backend=MagicMock()).falsify(_finding()) \
            == Disposition.DISMISSED


def test_fixed_is_a_protocol_error_not_a_crash(tmp_path):
    """Review round 1 on ee45427: FIXED raised a bare ValueError, which
    falls past machine.py's LLMInvokeError/RuntimeError arms into the
    re-raising except Exception and aborts the review. It is the same
    class of violation as an unknown verdict and gets the same arm."""
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(
            content={"verdict": "FIXED", "reasoning": "x"})
        with pytest.raises(FalsifyProtocolError, match="only verify"):
            RealFalsifier(backend=MagicMock()).falsify(_finding())

    from tests.test_runtime_machine import _make_sm
    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(
            content={"verdict": "FIXED", "reasoning": "x"})
        sm._run_l1_phase()          # must not raise
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error.startswith("falsify() protocol violation:")


def test_protocol_error_attributed_in_state(tmp_path):
    """machine.py names the cause: protocol violation, not backend outage.

    Drives the real falsifier through the real state machine with only
    the backend call mocked, so a stub falsifier cannot make this green.
    """
    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.return_value = LLMResult(content="garbage")
        sm._run_l1_phase()
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error is not None
    assert f.error.startswith("falsify() protocol violation:")
    assert any("protocol violation" in e for e in sm._state.infra_errors)
    assert not any("backend unavailable" in e for e in sm._state.infra_errors)


def test_backend_outage_still_attributed_as_unavailable(tmp_path):
    """The other arm keeps its wording; the two causes stay distinguishable."""
    from code_forge.llm_invoke import LLMInvokeError
    from tests.test_runtime_machine import _make_sm

    sm = _make_sm(tmp_path)
    sm.falsifier = RealFalsifier(backend=MagicMock())
    f = _finding()
    sm.l1_provider = lambda: ([f], [], Usage(), 0.0)
    with patch("code_forge.falsify_real.llm_invoke") as inv:
        inv.side_effect = LLMInvokeError("connection refused")
        sm._run_l1_phase()
    assert f.disposition == Disposition.UNCERTAIN
    assert f.error is not None
    assert f.error.startswith("falsify() backend unavailable:")
    assert not any("protocol violation" in e for e in sm._state.infra_errors)
