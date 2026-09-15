"""Tests for reviewer_json fence stripping and schema validation."""
import json

import pytest

from code_forge.reviewer_json import (
    REVIEW_JSON_CONTRACT,
    ExcerptEvidenceError,
    _hoist_nested_excerpts,
    validate_reviewer_json,
)

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
        # Range claims 1 line (start=end=1) while content carries 2: the
        # off-by-one end_line case, mirror image of a dropped trailing blank.
        payload = self._payload(["a", "b"], 1, 1)
        assert validate_reviewer_json(json.dumps(payload)) == payload

    def test_two_lines_over_still_rejected(self):
        payload = self._payload(["a", "b", "c"], 1, 1)
        with pytest.raises(ValueError, match="declares 1 lines but carries 3"):
            validate_reviewer_json(json.dumps(payload))


class TestNestedFindingExcerpts:
    """Hoist per-finding code_excerpts onto the envelope root.

    Agnes expert (and similar) returns a parseable envelope whose excerpts
    sit inside each finding rather than at the required root key. That
    used to be schema_fail + TimeoutBreaker even though the excerpts were
    well-formed. Fence-stripping already preprocesses; this is the same
    class of envelope repair, not evidence fabrication.
    """

    def _exc(self) -> dict:
        return {
            "file": "tests/test_kconfig_lock_path.py",
            "start_line": 84,
            "end_line": 84,
            "content": '    assert body.startswith(f"{HELPER_NAME}() {")',
        }

    def _finding(self) -> dict:
        return {
            "file": "tests/test_kconfig_lock_path.py",
            "line": 84,
            "severity": "P1",
            "description": "verbatim needle ignores leading tab",
            "code_excerpts": [self._exc()],
        }

    def test_nested_excerpts_hoisted_to_root(self):
        finding = self._finding()
        out = validate_reviewer_json(json.dumps({"findings": [finding]}))
        assert "code_excerpts" in out
        assert out["code_excerpts"] == [self._exc()]
        assert "code_excerpts" not in out["findings"][0]
        assert out["findings"][0]["file"] == finding["file"]
        assert out["findings"][0]["line"] == 84
        assert out["findings"][0]["severity"] == "P1"

    def test_nested_excerpts_from_several_findings_are_concatenated(self):
        other = {
            "file": "framework/package/base.sh",
            "start_line": 88,
            "end_line": 88,
            "content": "_kconfig_lock_path() {",
        }
        second = {
            "file": "framework/package/base.sh",
            "line": 88,
            "severity": "P2",
            "description": "other",
            "code_excerpts": [other],
        }
        out = validate_reviewer_json(
            {"findings": [self._finding(), second]},
        )
        assert out["code_excerpts"] == [self._exc(), other]
        assert "code_excerpts" not in out["findings"][0]
        assert "code_excerpts" not in out["findings"][1]

    def test_root_excerpts_are_not_merged_with_nested(self):
        """A well-formed envelope keeps its root list; nested copies stay put.

        Merging would double-count coverage. The root key is the contract;
        nested copies on a valid envelope are leftover model chatter.
        """
        root_exc = {
            "file": "a.py",
            "start_line": 1,
            "end_line": 2,
            "content": "x = 1\ny = 2",
        }
        finding = self._finding()
        data = {
            "findings": [finding],
            "code_excerpts": [root_exc],
        }
        out = validate_reviewer_json(data)
        assert out["code_excerpts"] == [root_exc]
        assert out["findings"][0]["code_excerpts"] == finding["code_excerpts"]

    def test_empty_root_list_does_not_hoist(self):
        """Present-but-empty root is a claimed envelope, not a missing key.

        Hoisting on [] would hide a model that named the key and then
        supplied nothing at that layer. Fail closed, same as today.
        """
        finding = self._finding()
        data = {"findings": [finding], "code_excerpts": []}
        out = validate_reviewer_json(data)
        assert out["code_excerpts"] == []
        assert out["findings"][0]["code_excerpts"] == finding["code_excerpts"]

    def test_nested_malformed_excerpt_still_rejected(self):
        bad = self._finding()
        bad["code_excerpts"] = [
            {"file": "a.py", "start_line": 1, "end_line": 5, "content": "x = 1"},
        ]
        with pytest.raises(ValueError, match="declares 5 lines but carries 1"):
            validate_reviewer_json({"findings": [bad]})

    def test_findings_without_nested_or_root_still_rejected(self):
        data = {
            "findings": [
                {
                    "file": "a.py",
                    "line": 1,
                    "severity": "P3",
                    "description": "no excerpts anywhere",
                }
            ]
        }
        with pytest.raises(ValueError, match="missing required field"):
            validate_reviewer_json(json.dumps(data))

    def test_non_dict_finding_is_kept_and_does_not_stop_hoist(self):
        """A garbage list entry must not become None, and must not drop later nested excerpts."""
        data = {"findings": ["skip-me", self._finding()]}
        _hoist_nested_excerpts(data)
        assert data["findings"][0] == "skip-me"
        assert data["code_excerpts"] == [self._exc()]
        assert data["findings"][1]["file"] == self._finding()["file"]
        with pytest.raises(ValueError, match="finding\\[0\\] is not a dict"):
            validate_reviewer_json(data)

    def test_empty_nested_list_does_not_count_as_hoist(self):
        """An empty per-finding list is not evidence. Do not invent a root key."""
        finding = {
            "file": "a.py",
            "line": 1,
            "severity": "P3",
            "description": "no excerpts",
            "code_excerpts": [],
        }
        with pytest.raises(ValueError, match="missing required field"):
            validate_reviewer_json({"findings": [finding]})

    def test_non_list_nested_does_not_hoist(self):
        """A truthy non-list must not be treated as excerpts.

        `isinstance(x, list) or x` would hoist a string by extending
        characters onto the root list. Only a non-empty list counts.
        """
        data = {
            "findings": [{
                "file": "a.py",
                "line": 1,
                "severity": "P3",
                "description": "x",
                "code_excerpts": "not-a-list",
            }],
        }
        _hoist_nested_excerpts(data)
        assert "code_excerpts" not in data

    def test_sibling_without_nested_is_kept(self):
        plain = {
            "file": "b.py",
            "line": 1,
            "severity": "P3",
            "description": "plain",
        }
        out = validate_reviewer_json(
            {"findings": [self._finding(), plain]},
        )
        assert out["findings"][1] == plain
        assert out["findings"][1] is not plain
        assert isinstance(out["findings"][1], dict)
        assert out["code_excerpts"] == [self._exc()]
        out["findings"][1]["description"] = "mutated"
        assert plain["description"] == "plain"


class TestEmptyCleanPassEnvelope:
    """A claimed-clean envelope with no excerpts is evidence failure, not a dead backend."""

    def test_empty_lists_raise_excerpt_evidence_error(self):
        with pytest.raises(ExcerptEvidenceError, match="per-hunk excerpts"):
            validate_reviewer_json({"findings": [], "code_excerpts": []})

    def test_empty_lists_are_not_a_plain_schema_error(self):
        try:
            validate_reviewer_json({"findings": [], "code_excerpts": []})
        except ExcerptEvidenceError:
            return
        except ValueError as exc:
            raise AssertionError(
                f"empty clean-pass envelope must not be a plain ValueError: {exc!r}"
            ) from exc
        raise AssertionError("empty clean-pass envelope must raise")
