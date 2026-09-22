from __future__ import annotations

from pathlib import Path

from code_forge.llm_invoke import (
    _extract_json_from_text,
    _model_json_decoder,
    _salvage_truncated_object,
)

_FALSIFY_KEYS = frozenset({"verdict", "reasoning"})


def _salvage(text: str, start: int = 0):
    return _salvage_truncated_object(text, start, _model_json_decoder())


class TestSalvagePrefixStart:
    def test_nonzero_start_ignores_quotes_brackets_commas_in_prefix(self):
        prefix = 'see "quotes" [arr], {obj}, then '
        payload = '{"verdict": "CONFIRMED", "reasoning": "cut'
        text = prefix + payload
        start = len(prefix)
        assert text[start] == "{"
        assert '"' in text[:start]
        assert "[" in text[:start]
        assert "," in text[:start]
        assert _salvage(text, start) == {"verdict": "CONFIRMED"}

    def test_extract_whitelist_recovers_object_after_noisy_prefix(self):
        text = 'see "quotes" [arr], {obj}, then {"verdict": "CONFIRMED", "reasoning": "cut'
        got = _extract_json_from_text(text, expected_keys=_FALSIFY_KEYS)
        assert got == {"verdict": "CONFIRMED"}


class TestSalvageStringEscapes:
    def test_escaped_quote_and_backslash_do_not_split_a_complete_field(self):
        text = (
            '{"path": "C:\\\\tmp\\\\a.py", '
            '"note": "say \\"hi\\"", '
            '"verdict": "DISMISSED", '
            '"reasoning": "cut'
        )
        assert _salvage(text) == {
            "path": "C:\\tmp\\a.py",
            "note": 'say "hi"',
            "verdict": "DISMISSED",
        }

    def test_escaped_quote_before_a_prose_comma_does_not_end_the_string(self):
        text = '{"verdict": "CONFIRMED", "reasoning": "he said \\"hi, there\\" and then cut'
        assert _salvage(text) == {"verdict": "CONFIRMED"}


class TestSalvageNestedValues:
    def test_complete_nested_array_and_object_are_kept_as_is(self):
        text = (
            '{"verdict": "CONFIRMED", '
            '"items": [1, 2, {"k": "v"}], '
            '"meta": {"file": "a.py", "n": [1, 2]}, '
            '"reasoning": "cut'
        )
        assert _salvage(text) == {
            "verdict": "CONFIRMED",
            "items": [1, 2, {"k": "v"}],
            "meta": {"file": "a.py", "n": [1, 2]},
        }

    def test_cut_inside_nested_array_or_object_drops_that_field_whole(self):
        cut_array = '{"verdict": "CONFIRMED", "items": [1, 2, 3'
        cut_object = '{"verdict": "CONFIRMED", "meta": {"file": "a.py", "line":'
        assert _salvage(cut_array) == {"verdict": "CONFIRMED"}
        assert _salvage(cut_object) == {"verdict": "CONFIRMED"}


class TestSalvageEmptyKey:
    def test_complete_empty_key_is_kept_and_truncated_empty_key_is_dropped(self):
        complete = '{"": 1, "verdict": "DISMISSED", "reasoning": "cut'
        truncated = '{"verdict": "CONFIRMED", "": "cut'
        assert _salvage(complete) == {"": 1, "verdict": "DISMISSED"}
        assert _salvage(truncated) == {"verdict": "CONFIRMED"}


class TestSalvageClosedMalformed:
    def test_closed_but_syntactically_bad_object_is_not_recovered(self):
        closed_undefined = '{"verdict": "CONFIRMED", "reasoning": undefined_token}'
        closed_trailing_comma = '{"verdict": "CONFIRMED",}'
        closed_missing_colon = '{"verdict" "CONFIRMED"}'
        closed_extra_comma = '{"verdict": "CONFIRMED",, "reasoning": "x"}'
        assert _salvage(closed_undefined) is None
        assert _salvage(closed_trailing_comma) is None
        assert _salvage(closed_missing_colon) is None
        assert _salvage(closed_extra_comma) is None


class TestSalvageIncompleteLeadingFields:
    def test_no_complete_field_is_not_recovered(self):
        assert _salvage("{") is None
        assert _salvage('{"verdict": "CONFI') is None
        assert _salvage('{"verdict": "CONFIRMED"') is None

    def test_truncated_field_is_dropped_complete_fields_kept_as_is(self):
        text = '{"verdict": "CONFIRMED", "file": "a.py", "reasoning": "long cut'
        assert _salvage(text) == {"verdict": "CONFIRMED", "file": "a.py"}


class TestSalvageExtractorWhitelistAndFile:
    def test_extract_salvages_falsify_envelope_and_not_review_envelope(self):
        truncated_falsify = '{"verdict": "CONFIRMED", "reasoning": "cut'
        truncated_review = (
            '{"findings": [], "code_excerpts": {"a.py": "x"}, "note": "cut'
        )
        assert _extract_json_from_text(
            truncated_falsify, expected_keys=_FALSIFY_KEYS
        ) == {"verdict": "CONFIRMED"}
        assert _extract_json_from_text(truncated_review) is None

    def test_salvage_from_real_temporary_file(self, tmp_path: Path):
        path = tmp_path / "truncated_falsify.json"
        content = (
            'prefix "quotes" [arr], {obj}, then '
            '{"verdict": "UNCERTAIN", '
            '"meta": {"file": "mod.py", "n": [1, 2]}, '
            '"reasoning": "budget cut mid'
        )
        path.write_text(content, encoding="utf-8")
        text = path.read_text(encoding="utf-8")
        start = text.index("{", text.index("{") + 1)
        assert _salvage(text, start) == {
            "verdict": "UNCERTAIN",
            "meta": {"file": "mod.py", "n": [1, 2]},
        }
        assert _extract_json_from_text(text, expected_keys=_FALSIFY_KEYS) == {
            "verdict": "UNCERTAIN",
            "meta": {"file": "mod.py", "n": [1, 2]},
        }
