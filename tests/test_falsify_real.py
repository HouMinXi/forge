from unittest.mock import patch
import pytest
from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import LLMInvokeError
from code_forge.state import StateFinding


def _make_finding(fp="fp-1"):
    return StateFinding(
        id=fp, fingerprint=fp, source="L1",
        disposition=Disposition.CONFIRMED,
        file="src/foo.py", line_range=[42, 45],
        description="potential null dereference",
    )


class TestRealFalsifier:
    def test_confirmed_on_real_finding(self):
        resp = {"verdict": "CONFIRMED", "reasoning": "path reachable"}
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.CONFIRMED

    def test_dismissed_on_false_positive(self):
        resp = {"verdict": "DISMISSED", "reasoning": "caller validates"}
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.DISMISSED

    def test_uncertain_on_ambiguous(self):
        resp = {"verdict": "UNCERTAIN", "reasoning": "depends on config"}
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_uncertain_on_invoke_error(self):
        with patch(
            "code_forge.falsify_real.llm_invoke",
            side_effect=LLMInvokeError("timeout"),
        ):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_rejects_fixed_verdict(self):
        resp = {"verdict": "FIXED", "reasoning": "n/a"}
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            with pytest.raises(ValueError, match="FIXED"):
                RealFalsifier().falsify(_make_finding())

    def test_uncertain_on_unknown_verdict(self):
        resp = {"verdict": "BOGUS", "reasoning": "n/a"}
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN
