"""Tests for reviewer_json fence stripping and schema validation."""
import json

import pytest

from code_forge.reviewer_json import REVIEW_JSON_CONTRACT, validate_reviewer_json

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


class TestFenceStripping:
    def test_plain_json_accepted(self):
        assert validate_reviewer_json(json.dumps(VALID)) == VALID

    def test_fenced_json_accepted(self):
        fenced = "```json\n" + json.dumps(VALID) + "\n```"
        assert validate_reviewer_json(fenced) == VALID

    def test_fence_without_language_tag_accepted(self):
        fenced = "```\n" + json.dumps(VALID) + "\n```"
        assert validate_reviewer_json(fenced) == VALID

    def test_fence_with_surrounding_whitespace_accepted(self):
        fenced = "\n```json\n" + json.dumps(VALID) + "\n```\n\n"
        assert validate_reviewer_json(fenced) == VALID

    def test_unclosed_fence_rejected(self):
        fenced = "```json\n" + json.dumps(VALID)
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(fenced)

    def test_orphan_closing_fence_rejected(self):
        fenced = json.dumps(VALID) + "\n```"
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(fenced)

    def test_lone_fence_line_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json("```")

    def test_foreign_language_tag_rejected(self):
        fenced = "```python\n" + json.dumps(VALID) + "\n```"
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(fenced)

    def test_preamble_before_fence_rejected(self):
        fenced = "here is my review:\n```json\n" + json.dumps(VALID) + "\n```"
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(fenced)


class TestSchemaFailClosed:
    def test_dict_input_accepted(self):
        assert validate_reviewer_json(dict(VALID)) == VALID

    def test_non_json_string_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json("no json here")

    @pytest.mark.parametrize("value", [5, [1, 2], None])
    def test_non_string_input_raises_value_error_not_attribute_error(
        self, value
    ):
        """The fence helper runs before json.loads; a non-string must
        still fail as ValueError, not crash on .strip()."""
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(value)

    def test_backticks_ending_a_json_string_are_not_a_fence(self):
        """A ``` that terminates a JSON string sits mid-line; only a
        fence on its own line closes the envelope."""
        raw = '```json\n{"a": "b```"}'
        with pytest.raises(ValueError, match="not valid JSON"):
            validate_reviewer_json(raw)

    def test_crlf_fenced_json_accepted(self):
        fenced = "```json\r\n" + json.dumps(VALID) + "\r\n```"
        assert validate_reviewer_json(fenced) == VALID

    def test_indented_closing_fence_accepted(self):
        fenced = "```json\n" + json.dumps(VALID) + "\n  ```"
        assert validate_reviewer_json(fenced) == VALID

    def test_indented_fence_with_crlf_accepted(self):
        fenced = "```json\r\n" + json.dumps(VALID) + "\r\n\t```"
        assert validate_reviewer_json(fenced) == VALID

    def test_missing_excerpts_rejected(self):
        data = {k: v for k, v in VALID.items() if k != "code_excerpts"}
        with pytest.raises(ValueError, match="missing required field"):
            validate_reviewer_json(json.dumps(data))


class TestReviewJsonContract:
    """Tests for REVIEW_JSON_CONTRACT content requirements."""

    def test_contains_post_image_line_numbers_note(self):
        """REVIEW_JSON_CONTRACT must specify that start_line/end_line are
        post-image line numbers and @@ header old-side start is not a source line.
        """
        assert "start_line and end_line are post-image line numbers" in REVIEW_JSON_CONTRACT
        assert "the @@ header's old-side start is not a source line" in REVIEW_JSON_CONTRACT
