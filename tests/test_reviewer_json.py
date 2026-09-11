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


def _rj_exc(**kw):
    base = {"file": "a.py", "start_line": 1, "end_line": 2,
            "content": "x = 1\ny = 2"}
    base.update(kw)
    return base


def _rj_data(excerpts):
    return {"findings": [], "code_excerpts": excerpts}


class TestProducerEvidenceShapeRed:
    """Task 1 RED: producer-side excerpt shape (validate_reviewer_json).

    Contract: design "Contracts / Excerpts" + plan Task 1. The model's
    JSON, file names and coordinates are untrusted, so the producer must
    enforce exact range/content parity, positive ordered integer
    coordinates (bool is not an integer), typed fields and non-blank
    content before any receipt is written. Each test below fails against
    the pinned base, which checks only presence and int-ness of the two
    coordinates.
    """

    def test_underlength_rejected(self):
        # One line short is the trailing-blank-line ambiguity, not a
        # miscount: see excerpt_line_count_matches. Two short is a miscount.
        data = _rj_data([_rj_exc(end_line=4)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_one_line_short_accepted_as_trailing_blank(self):
        data = _rj_data([_rj_exc(end_line=3)])
        assert validate_reviewer_json(data) == data

    def test_overflow_rejected(self):
        # One line over is end_line jitter (accepted, see
        # test_one_line_over_accepted_as_coordinate_jitter); two over is a
        # miscount.
        data = _rj_data([_rj_exc(end_line=1, content="x = 1\ny = 2\nz = 3")])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_bool_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line=True)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_bool_end_line_rejected(self):
        data = _rj_data([_rj_exc(end_line=True)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_zero_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line=0, end_line=0,
                                 content="x = 1")])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_negative_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line=-1, end_line=1,
                                 content="l-1\nl0\nl1")])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_content_int_rejected(self):
        data = _rj_data([_rj_exc(content=5)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_content_none_rejected(self):
        data = _rj_data([_rj_exc(content=None)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_content_mixed_list_rejected(self):
        """A list containing null/numbers/objects fails; the writer must
        not stringify it into fake evidence."""
        data = _rj_data([_rj_exc(content=["x = 1", None, 5])])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_empty_content_rejected(self):
        data = _rj_data([_rj_exc(content="")])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_whitespace_only_content_rejected(self):
        data = _rj_data([_rj_exc(content="   \n  ")])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)

    def test_non_string_file_rejected(self):
        data = _rj_data([_rj_exc(file=5)])
        with pytest.raises(ValueError):
            validate_reviewer_json(data)


class TestProducerEvidenceShapePins:
    """Shapes the producer already handles; pinned so the shared-helper
    refactor keeps accepting or rejecting them as today."""

    def test_float_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line=1.5)])
        with pytest.raises(ValueError, match="must be int"):
            validate_reviewer_json(data)

    def test_string_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line="1")])
        with pytest.raises(ValueError, match="must be int"):
            validate_reviewer_json(data)

    def test_null_start_line_rejected(self):
        data = _rj_data([_rj_exc(start_line=None)])
        with pytest.raises(ValueError, match="must be int"):
            validate_reviewer_json(data)

    def test_reversed_range_rejected(self):
        data = _rj_data([_rj_exc(start_line=3, end_line=2)])
        with pytest.raises(ValueError, match="start_line 3 > end_line 2"):
            validate_reviewer_json(data)

    def test_non_dict_excerpt_rejected(self):
        data = _rj_data(["not a dict"])
        with pytest.raises(ValueError, match="is not a dict"):
            validate_reviewer_json(data)

    def test_all_string_line_list_accepted(self):
        """The writer joins an all-string list with newlines, so the
        producer keeps accepting that shape."""
        data = _rj_data([_rj_exc(content=["x = 1", "y = 2"])])
        assert validate_reviewer_json(data) == data


class TestExcerptTrailingBlankLine:
    """An excerpt whose last quoted line is blank must still validate.

    str.splitlines() collapses a trailing separator, so a quote ending on a
    blank source line reported one line fewer than it declared and the whole
    pass was rejected as a schema violation.
    """

    def _payload(self, content, start, end):
        return {
            "findings": [],
            "code_excerpts": [
                {
                    "file": "a.py",
                    "start_line": start,
                    "end_line": end,
                    "content": content,
                }
            ],
        }

    def test_excerpt_ending_on_blank_line_accepted(self):
        # Lines 1-3 where line 3 is blank, as a list of quoted lines.
        payload = self._payload(["x = 1", "y = 2", ""], 1, 3)
        assert validate_reviewer_json(json.dumps(payload)) == payload

    def test_excerpt_ending_on_blank_line_accepted_as_string(self):
        payload = self._payload("x = 1\ny = 2\n", 1, 3)
        assert validate_reviewer_json(json.dumps(payload)) == payload

    def test_terminating_newline_is_not_an_extra_line(self):
        # "x = 1\ny = 2\n" quoting only lines 1-2: the final newline
        # terminates line 2 rather than introducing a third line.
        payload = self._payload("x = 1\ny = 2", 1, 2)
        assert validate_reviewer_json(json.dumps(payload)) == payload

    def test_genuine_line_count_mismatch_still_rejected(self):
        payload = self._payload(["x = 1", "y = 2"], 1, 5)
        with pytest.raises(ValueError, match="declares 5 lines but carries 2"):
            validate_reviewer_json(json.dumps(payload))

    def test_one_line_over_accepted_as_coordinate_jitter(self):
        # The model declared 6-9 (4 lines) but pasted 5: an off-by-one on
        # end_line, the mirror image of the trailing-blank case.  Real
        # backends do this routinely and each occurrence took the whole
        # review pass down as a schema violation.
        payload = self._payload(["a", "b"], 1, 1)
        assert validate_reviewer_json(json.dumps(payload)) == payload

    def test_two_lines_over_still_rejected(self):
        payload = self._payload(["a", "b", "c"], 1, 1)
        with pytest.raises(ValueError, match="declares 1 lines but carries 3"):
            validate_reviewer_json(json.dumps(payload))
