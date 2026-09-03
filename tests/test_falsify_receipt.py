# SPDX-License-Identifier: Apache-2.0
"""Tests for execution-receipt gating of falsifier verdicts.

Fixture case is the real one from ashare-lab R21 (2026-09-02): a finding
claiming numpy registers np.bool_ in the numbers ABCs was stamped
CONFIRMED after 4287 seconds. One line refutes it, on two numpy versions:

    >>> isinstance(numpy.bool_(True), numbers.Real)
    False

The gate does not decide whether the claim is true -- it cannot. It
decides whether anyone checked, and routes unchecked library claims to a
human instead of letting them carry the pipeline's highest confidence.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.falsify_receipt import (
    asserts_library_behaviour,
    check_receipt,
    has_execution_receipt,
)
from code_forge.state import StateFinding

# The verbatim claim from R21.
NUMPY_CLAIM = (
    "numpy>=1.20 registers np.bool_ in the numbers ABCs, so the existing "
    "isinstance check against numbers.Real will silently accept boolean "
    "arrays and the version guard will not catch it"
)

DIFF_CLAIM = (
    "the early return at line 812 leaves the lock held when the payload "
    "is empty, so a later caller on this path blocks forever"
)


def _finding(description: str) -> StateFinding:
    f = StateFinding.__new__(StateFinding)
    f.file = "analyze_matrix.py"
    f.line_range = [280, 280]
    f.description = description
    f.fingerprint = "9e33a4c4a23e0d7b"
    return f


class TestClaimClassification:
    def test_library_behaviour_claim_is_recognised(self):
        assert asserts_library_behaviour(NUMPY_CLAIM) is not None

    def test_claim_about_the_diff_is_not(self):
        """The common case must stay cheap to pass.

        Demanding receipts for logic claims would downgrade nearly every
        finding and make the gate useless.
        """
        assert asserts_library_behaviour(DIFF_CLAIM) is None

    def test_naming_a_library_is_not_a_behavioural_claim(self):
        """Mentioning numpy is not the same as asserting how it behaves."""
        assert asserts_library_behaviour(
            "we call numpy.asarray here without checking the dtype first"
        ) is None

    @pytest.mark.parametrize("desc", [
        "isinstance(x, numbers.Real) is True for numpy scalars",
        "since python 3.11 asyncio.timeout replaces wait_for",
        "pandas returns a copy rather than a view in this path",
        "the requests API guarantees the connection is released",
    ])
    def test_other_behavioural_shapes(self, desc):
        assert asserts_library_behaviour(desc) is not None

    @pytest.mark.parametrize("desc", [
        "aiohttp deprecates the sync client",
        "httpx raises ReadTimeout rather than ConnectTimeout",
        "boto3 deprecates the resource interface in v2",
        "requests.Session returns a new connection pool each call",
        "numpy.bool_ subclasses int rather than bool",
        "pandas coerces the dtype to object here",
    ])
    def test_libraries_outside_any_allowlist(self, desc):
        """Round 9 caught the first version relying on 17 hardcoded names.

        An allowlist cannot cover libraries written after it. The subject
        now has to LOOK like an external module reference instead.
        """
        assert asserts_library_behaviour(desc) is not None

    @pytest.mark.parametrize("desc", [
        "the early return at line 812 leaves the lock held",
        "this function returns None on the error path",
        "_record raises TypeError when result is None",
        "the loop returns early when the list is empty",
        "_severity_tier returns P1 for unprefixed findings",
    ])
    def test_claims_about_the_diff_stay_out(self, desc):
        """The widened pattern must not swallow ordinary logic claims.

        These use the same verbs. If they matched, most findings would
        need receipts, everything would land UNCERTAIN, and the gate
        would be worse than not having it.
        """
        assert asserts_library_behaviour(desc) is None

    def test_case_is_the_signal_for_a_class_name(self):
        """Capitalisation distinguishes a class name from a plain word.

        "redis returns Cached" names a type; "redis returns cached" is
        prose. Compiling these patterns with IGNORECASE collapses the
        two, and then any bare word after the verb reads as a class.
        """
        assert asserts_library_behaviour("redis returns Cached") is not None
        assert asserts_library_behaviour("redis returns cached") is None

    def test_empty_description(self):
        assert asserts_library_behaviour("") is None


class TestReceiptPresence:
    def test_both_halves_required(self):
        assert has_execution_receipt(
            {"receipt": {"command": "python -c '...'", "output": "False"}}
        )

    def test_command_without_output_is_not_a_receipt(self):
        """Knowing how to phrase a command is not evidence of running it."""
        assert not has_execution_receipt(
            {"receipt": {"command": "python -c '...'", "output": ""}}
        )

    def test_output_without_command_cannot_be_reproduced(self):
        assert not has_execution_receipt(
            {"receipt": {"command": "", "output": "False"}}
        )

    def test_missing_and_malformed(self):
        assert not has_execution_receipt({})
        assert not has_execution_receipt({"receipt": "ran it, was false"})
        assert not has_execution_receipt(None)


class TestDowngrade:
    def test_library_claim_without_receipt_downgrades(self):
        check = check_receipt(NUMPY_CLAIM, {"verdict": "CONFIRMED"})
        assert check.should_downgrade

    def test_library_claim_with_receipt_stands(self):
        check = check_receipt(NUMPY_CLAIM, {
            "verdict": "CONFIRMED",
            "receipt": {
                "command": "python -c 'import numpy,numbers; "
                           "print(isinstance(numpy.bool_(True), numbers.Real))'",
                "output": "False",
            },
        })
        assert not check.should_downgrade

    def test_diff_claim_never_needs_one(self):
        check = check_receipt(DIFF_CLAIM, {"verdict": "CONFIRMED"})
        assert not check.should_downgrade
        assert not check.needs_receipt


class TestFalsifierIntegration:
    """Drive RealFalsifier itself -- the gate must be in the product path.

    A test that only exercises check_receipt proves the classifier works
    while nothing calls it.
    """

    def _run(self, response: dict, description: str) -> Disposition:
        class _Result:
            content = response

        with patch("code_forge.falsify_real.llm_invoke", return_value=_Result()):
            return RealFalsifier().falsify(_finding(description))

    def test_the_r21_case_no_longer_reaches_confirmed(self):
        """The measured failure: CONFIRMED on an unverified numpy claim."""
        got = self._run({"verdict": "CONFIRMED", "reasoning": "checked"},
                        NUMPY_CLAIM)
        assert got == Disposition.UNCERTAIN

    def test_the_r21_case_with_a_receipt_is_allowed_through(self):
        got = self._run({
            "verdict": "CONFIRMED",
            "reasoning": "verified",
            "receipt": {"command": "python -c '...'", "output": "False"},
        }, NUMPY_CLAIM)
        assert got == Disposition.CONFIRMED

    def test_unverified_dismissal_is_downgraded_too(self):
        """An unverified DISMISSED buries a real defect.

        That is the worse direction to be wrong in, so the gate is not
        limited to CONFIRMED.
        """
        got = self._run({"verdict": "DISMISSED", "reasoning": "not real"},
                        NUMPY_CLAIM)
        assert got == Disposition.UNCERTAIN

    def test_ordinary_findings_are_untouched(self):
        got = self._run({"verdict": "CONFIRMED", "reasoning": "real"},
                        DIFF_CLAIM)
        assert got == Disposition.CONFIRMED

    def test_uncertain_stays_uncertain(self):
        got = self._run({"verdict": "UNCERTAIN", "reasoning": "unclear"},
                        NUMPY_CLAIM)
        assert got == Disposition.UNCERTAIN
