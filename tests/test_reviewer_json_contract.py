"""Pin the ValueError contract of validate_reviewer_json.

Both production call sites (factories.py outlets) catch ValueError to route
malformed reviewer output into the salvage path. A static lint that prefers
TypeError for type violations would break that routing: TypeError escapes
the catch net and crashes the review instead of salvaging. These tests pin
ValueError for every type-violation branch so the contract survives future
"cleanup".
"""
import json

import pytest

from code_forge.reviewer_json import validate_reviewer_json

VALID = {
    "findings": [
        {
            "file": "a.py",
            "line": 1,
            "severity": "P3",
            "description": "boundary case",
        }
    ],
    "code_excerpts": [
        {
            "file": "a.py",
            "start_line": 1,
            "end_line": 2,
            "content": "x = 1\ny = 2",
        }
    ],
}


def _payload(**overrides):
    data = json.loads(json.dumps(VALID))
    data.update(overrides)
    return data


class TestValueErrorContract:
    """Type violations must raise ValueError, never TypeError."""

    def test_non_object_payload_raises_value_error(self):
        with pytest.raises(ValueError, match="not a JSON object"):
            validate_reviewer_json(json.dumps([1, 2, 3]))

    def test_findings_not_a_list_raises_value_error(self):
        with pytest.raises(ValueError, match="findings must be a list"):
            validate_reviewer_json(_payload(findings={"file": "a.py"}))

    def test_code_excerpts_not_a_list_raises_value_error(self):
        with pytest.raises(ValueError, match="code_excerpts must be a list"):
            validate_reviewer_json(_payload(code_excerpts={"file": "a.py"}))

    def test_finding_not_a_dict_raises_value_error(self):
        with pytest.raises(ValueError, match=r"finding\[0\] is not a dict"):
            validate_reviewer_json(_payload(findings=["not a dict"]))

    def test_code_excerpt_not_a_dict_raises_value_error(self):
        with pytest.raises(ValueError, match=r"code_excerpt\[0\] is not a dict"):
            validate_reviewer_json(_payload(code_excerpts=[42]))

    def test_excerpt_file_wrong_type_raises_value_error(self):
        bad = _payload(
            code_excerpts=[
                {"file": None, "start_line": 1, "end_line": 1, "content": "x"}
            ]
        )
        with pytest.raises(ValueError, match="file must be a non-empty string"):
            validate_reviewer_json(bad)

    def test_excerpt_content_wrong_type_raises_value_error(self):
        bad = _payload(
            code_excerpts=[
                {"file": "a.py", "start_line": 1, "end_line": 1, "content": 3.5}
            ]
        )
        with pytest.raises(ValueError, match="content must be str"):
            validate_reviewer_json(bad)

    def test_type_error_is_never_raised_for_type_violations(self):
        # TypeError must not leak: the salvage net catches ValueError only.
        # Each payload must reach a distinct type-violation branch; a payload
        # that short-circuits on an earlier check (e.g. missing required
        # field) does not protect the branch it was meant to exercise.
        bad_inputs = [
            # findings not a list
            json.dumps(_payload(findings=1)),
            # finding entry not a dict
            json.dumps(_payload(findings=[None])),
            # code_excerpts not a list
            json.dumps(_payload(code_excerpts=2)),
            # excerpt entry not a dict
            json.dumps(_payload(code_excerpts=[None])),
            # excerpt file wrong type (all required keys present)
            json.dumps(_payload(code_excerpts=[
                {"file": 1, "start_line": 1, "end_line": 1, "content": "x"}
            ])),
            # excerpt content wrong type
            json.dumps(_payload(code_excerpts=[
                {"file": "a.py", "start_line": 1, "end_line": 1, "content": 42}
            ])),
        ]
        for raw in bad_inputs:
            try:
                validate_reviewer_json(raw)
            except TypeError:
                pytest.fail(
                    "TypeError escaped validate_reviewer_json; the salvage "
                    "path in factories.py catches ValueError only"
                )
            except ValueError:
                pass
