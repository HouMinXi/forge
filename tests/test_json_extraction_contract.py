from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_forge.llm_invoke import (
    _REVIEW_ENVELOPE_KEYS,
    _extract_json_from_text,
    _model_json_decoder,
    _strip_fences,
)


class TestStripFencesContract:
    def test_strip_fences_requires_fence_at_start_after_whitespace(self):
        text_with_whitespace = "\n  \t```json\n{\"findings\": []}\n```\n  \t"
        assert _strip_fences(text_with_whitespace) == '{"findings": []}'

        text_with_leading_prose = (
            "Here is the envelope:\n```json\n{\"findings\": []}\n```"
        )
        assert _strip_fences(text_with_leading_prose) == (
            "Here is the envelope:\n```json\n{\"findings\": []}\n```"
        )

        plain_text = "   {\"findings\": []}   "
        assert _strip_fences(plain_text) == '{"findings": []}'

    def test_strip_fences_stops_at_first_closing_fence_ignoring_subsequent_text(self):
        text_with_trailing = (
            "```json\n"
            '{"findings": []}\n'
            "```\n"
            "This explanatory prose should be ignored.\n"
            "```python\n"
            "extra_code = 123\n"
            "```"
        )
        assert _strip_fences(text_with_trailing) == '{"findings": []}'

    def test_strip_fences_unclosed_fence_preserves_content(self):
        unclosed = '```json\n{"findings": [{"id": 1}]}\nunclosed trailing text'
        assert _strip_fences(unclosed) == '{"findings": [{"id": 1}]}\nunclosed trailing text'

        lone_fence = "```json"
        assert _strip_fences(lone_fence) == ""


class TestExtractJsonFromTextContract:
    def test_extract_json_returns_first_matching_dict(self):
        text = (
            'preamble {"unrelated": 1} '
            '{"findings": [{"id": "first"}]} '
            '{"findings": [{"id": "second"}]}'
        )
        got = _extract_json_from_text(text)
        assert got == {"findings": [{"id": "first"}]}

        explicit_keys = frozenset({"verdict", "reasoning"})
        falsify_text = (
            'prose {"findings": []} '
            '{"verdict": "CONFIRMED", "reasoning": "first"} '
            '{"verdict": "DISMISSED", "reasoning": "second"}'
        )
        got_falsify = _extract_json_from_text(falsify_text, expected_keys=explicit_keys)
        assert got_falsify == {"verdict": "CONFIRMED", "reasoning": "first"}

    def test_extract_json_unspecified_keys_uses_defaults(self):
        assert _extract_json_from_text('{"findings": []}') == {"findings": []}
        assert _extract_json_from_text('{"code_excerpts": {}}') == {"code_excerpts": {}}
        assert _extract_json_from_text('{"surfaces": []}') == {"surfaces": []}
        assert _extract_json_from_text('{"other_key": 1}') is None

    def test_extract_json_explicit_empty_keys_rejects_all_dicts(self):
        empty_keys: frozenset[str] = frozenset()
        assert _extract_json_from_text('{"findings": []}', expected_keys=empty_keys) is None
        assert _extract_json_from_text('{"verdict": "CONFIRMED"}', expected_keys=empty_keys) is None
        assert _extract_json_from_text("{}", expected_keys=empty_keys) is None
        assert _extract_json_from_text('{"code_excerpts": {}}', expected_keys=empty_keys) is None

    def test_extract_json_skips_irrelevant_keys_and_invalid_braces(self):
        text = (
            "leading prose { invalid json } "
            "{ 12345 } "
            "{ 'single_quotes': true } "
            '{"non_envelope_key": [1, 2, 3]} '
            '{"findings": [{"id": "valid_envelope"}]}'
        )
        got = _extract_json_from_text(text)
        assert got == {"findings": [{"id": "valid_envelope"}]}

    def test_extract_json_salvage_none_continues_to_valid_falsify_envelope(self):
        text = (
            "Prefix broken { malformed: json } followed by valid "
            '{"verdict": "CONFIRMED", "reasoning": "valid reasoning"}'
        )
        keys = frozenset({"verdict", "reasoning"})
        got = _extract_json_from_text(text, expected_keys=keys)
        assert got == {"verdict": "CONFIRMED", "reasoning": "valid reasoning"}

    def test_extract_json_arrays_rejected_but_nested_objects_scanned(self):
        assert _extract_json_from_text("[1, 2, 3]") is None
        assert _extract_json_from_text('["findings", "code_excerpts"]') is None

        array_with_matching_object = '[{"findings": ["item1"]}]'
        assert _extract_json_from_text(array_with_matching_object) == {"findings": ["item1"]}

        array_with_irrelevant_then_matching = (
            '[{"irrelevant": 1}, {"findings": ["item2"]}]'
        )
        assert _extract_json_from_text(array_with_irrelevant_then_matching) == {
            "findings": ["item2"]
        }

    def test_extract_json_review_envelope_does_not_salvage_truncation(self):
        truncated_review = (
            '{"findings": [], "code_excerpts": {"file.py": "pass"}, "tail": "cut'
        )
        assert _extract_json_from_text(truncated_review) is None
        assert (
            _extract_json_from_text(
                truncated_review,
                expected_keys=_REVIEW_ENVELOPE_KEYS,
            )
            is None
        )

        truncated_falsify = '{"verdict": "CONFIRMED", "reasoning": "cut prose'
        salvaged = _extract_json_from_text(
            truncated_falsify,
            expected_keys=frozenset({"verdict", "reasoning"}),
        )
        assert salvaged == {"verdict": "CONFIRMED"}

    def test_extract_json_lenient_decoder_allows_control_characters(self):
        text_with_raw_control = '{"findings": ["row1\nrow2\tcol2\rret"]}'
        got = _extract_json_from_text(text_with_raw_control)
        assert got == {"findings": ["row1\nrow2\tcol2\rret"]}

        strict_decoder = json.JSONDecoder(strict=True)
        with pytest.raises(json.JSONDecodeError):
            strict_decoder.raw_decode(text_with_raw_control)

        lenient_decoder = _model_json_decoder()
        decoded_obj, _ = lenient_decoder.raw_decode(text_with_raw_control)
        assert decoded_obj == {"findings": ["row1\nrow2\tcol2\rret"]}

    def test_extract_json_from_real_temporary_file(self, tmp_path: Path):
        file_path = tmp_path / "llm_output.txt"
        content = (
            "```json\n"
            '{"findings": [{"file": "mod.py", "line": 42}], "code_excerpts": {}}\n'
            "```\n"
            "Trailing model explanation.\n"
        )
        file_path.write_text(content, encoding="utf-8")

        read_content = file_path.read_text(encoding="utf-8")
        stripped = _strip_fences(read_content)
        extracted = _extract_json_from_text(stripped)
        assert extracted == {
            "findings": [{"file": "mod.py", "line": 42}],
            "code_excerpts": {},
        }
