# SPDX-License-Identifier: Apache-2.0
"""The falsifier's reason for a verdict has to survive the run.

falsify() asks the model for a verdict and a reasoning string, then
returns only the verdict. The reason is what a later review needs when a
real defect was dismissed, and it has to be on the finding when the state
is written and still there when that state is read back.
"""

import json
from unittest.mock import patch

from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.falsify_receipt import check_receipt
from code_forge.state import StateFinding, _finding_from_dict, _finding_to_dict


def _finding() -> StateFinding:
    return StateFinding(
        id="f1",
        fingerprint="fp1",
        source="L1",
        disposition=Disposition.UNCERTAIN,
        file="a.py",
        line_range=[1, 1],
        description="the counter is read before it is written",
    )


def test_falsify_keeps_the_reason_on_the_finding():
    """The reason comes back on the finding, not only the verdict."""

    class _Result:
        content = {
            "verdict": "DISMISSED",
            "reasoning": "the write on line 4 happens before the read on line 9",
        }

    finding = _finding()
    with patch("code_forge.falsify_real.llm_invoke", return_value=_Result()):
        got = RealFalsifier().falsify(finding)

    assert got is Disposition.DISMISSED
    assert finding.falsify_reasoning == _Result.content["reasoning"]


def test_the_reason_roundtrips_through_state():
    """A state written with a reason loads the same reason back."""
    finding = _finding()
    finding.falsify_reasoning = "checked the call order"
    restored = _finding_from_dict(json.loads(json.dumps(_finding_to_dict(finding))))
    assert restored.falsify_reasoning == "checked the call order"


def test_a_downgrade_keeps_the_receipt_reason_not_the_models():
    """When the verdict is thrown out, the saved reason says why."""

    class _Result:
        content = {
            "verdict": "DISMISSED",
            "reasoning": "numpy sorts this for you",
        }

    finding = _finding()
    finding.description = "isinstance(x, numbers.Real) is True for numpy scalars"
    with patch("code_forge.falsify_real.llm_invoke", return_value=_Result()):
        got = RealFalsifier().falsify(finding)

    assert got is Disposition.UNCERTAIN
    expected = check_receipt(finding.description, _Result.content)
    assert finding.falsify_reasoning == expected.reason
    assert finding.falsify_reasoning != _Result.content["reasoning"]


def test_a_non_string_reason_clears_the_old_one():
    """A reason that is not text must not leave the previous one in place."""

    class _Result:
        content = {"verdict": "DISMISSED", "reasoning": {"note": "nested"}}

    finding = _finding()
    finding.falsify_reasoning = "left over from the round before"
    with patch("code_forge.falsify_real.llm_invoke", return_value=_Result()):
        RealFalsifier().falsify(finding)

    assert finding.falsify_reasoning is None


def test_old_state_without_a_reason_still_loads():
    """State written before this field existed has no key for it."""
    finding = _finding()
    payload = _finding_to_dict(finding)
    payload.pop("falsify_reasoning", None)
    restored = _finding_from_dict(payload)
    assert restored.falsify_reasoning is None
