from unittest.mock import patch
import pytest
from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.llm_invoke import LLMInvokeError, LLMResult, Usage
from code_forge.state import StateFinding


def _make_finding(fp="fp-1"):
    return StateFinding(
        id=fp, fingerprint=fp, source="L1",
        disposition=Disposition.CONFIRMED,
        file="src/foo.py", line_range=[42, 45],
        description="potential null dereference",
    )


def _make_llm_result(content):
    """Build a LLMResult wrapping the given content dict."""
    return LLMResult(content=content, usage=Usage(), duration_s=0.1)


class TestRealFalsifier:
    def test_confirmed_on_real_finding(self):
        resp = _make_llm_result({"verdict": "CONFIRMED", "reasoning": "path reachable"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.CONFIRMED

    def test_dismissed_on_false_positive(self):
        resp = _make_llm_result({"verdict": "DISMISSED", "reasoning": "caller validates"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.DISMISSED

    def test_uncertain_on_ambiguous(self):
        resp = _make_llm_result({"verdict": "UNCERTAIN", "reasoning": "depends on config"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN

    def test_invoke_error_propagates(self):
        """Backend failure is not a verdict.

        This assertion is INVERTED from its original form (241ae61,
        "Subprocess failure returns UNCERTAIN"). That design made an
        unreachable backend indistinguishable from a finding the
        verifier genuinely could not decide, and the fixpoint check
        resets the clean-round counter on any UNCERTAIN -- so a dead
        backend reset the counter every round until max_total_rounds,
        at 30-180s per call. Measured 2026-08-27: three hours of a
        review that could not converge.

        The caller (machine.py falsify loop) now catches this, records
        it to infra_errors, and stops the run after three consecutive
        rounds of it. llm_invoke already owns the retry budget;
        exhausting it is an infrastructure outcome, not a judgement.
        """
        with patch(
            "code_forge.falsify_real.llm_invoke",
            side_effect=LLMInvokeError("timeout"),
        ):
            with pytest.raises(LLMInvokeError):
                RealFalsifier().falsify(_make_finding())

    def test_rejects_fixed_verdict(self):
        resp = _make_llm_result({"verdict": "FIXED", "reasoning": "n/a"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            with pytest.raises(ValueError, match="FIXED"):
                RealFalsifier().falsify(_make_finding())

    def test_uncertain_on_unknown_verdict(self):
        resp = _make_llm_result({"verdict": "BOGUS", "reasoning": "n/a"})
        with patch("code_forge.falsify_real.llm_invoke", return_value=resp):
            assert RealFalsifier().falsify(_make_finding()) == Disposition.UNCERTAIN
