# SPDX-License-Identifier: Apache-2.0
"""A model quoting source code drops raw quotes into JSON strings.

json.loads then stops at the first quote and reports a delimiter error,
so the whole pass is discarded. The repair escapes a quote that cannot
legally end the string and leaves one that can.
"""

import json

import pytest

from code_forge.llm_invoke import _loads_model_json


def test_unescaped_quote_inside_a_string_is_repaired():
    raw = '{"description": "split("SYNC_NEED = ", 1) returns the tail"}'
    parsed = _loads_model_json(raw)
    assert parsed["description"] == 'split("SYNC_NEED = ", 1) returns the tail'


def test_repair_keeps_a_later_numeric_field():
    raw = '{"note": "split("X", 1) tail", "count": 42}'
    parsed = _loads_model_json(raw)
    assert parsed == {"note": 'split("X", 1) tail', "count": 42}


def test_escaped_backslash_does_not_hide_a_raw_quote():
    raw = '{"note": "path\\\\"C:\\\\""}'
    parsed = _loads_model_json(raw)
    assert parsed == {"note": 'path\\"C:\\"'}


def test_a_quote_that_ends_the_string_stays():
    raw = '{"description": "plain text", "count": 2}'
    assert _loads_model_json(raw) == {"description": "plain text", "count": 2}


def test_already_escaped_quote_is_left_alone():
    raw = '{"description": "split(\\"SYNC\\", 1)"}'
    assert _loads_model_json(raw)["description"] == 'split("SYNC", 1)'


def test_malformed_structure_still_raises():
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json('{"description": plain}')


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"count": 2, "ok": true}', {"count": 2, "ok": True}),
        ('{"items": [1, 2], "nested": {"a": 1}}', {"items": [1, 2], "nested": {"a": 1}}),
        ('{"name": "a, b", "flag": false}', {"name": "a, b", "flag": False}),
        ('{"text": "say \\"hi\\""}', {"text": 'say "hi"'}),
    ],
)
def test_ordinary_json_parses_unchanged(raw, expected):
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict":"DISMISSED" "reasoning":"missing comma"}',
        '{"findings":[{"ok": true},,{"ok": false}]}',
        '{"second":1,,"marker":2}',
        '{"findings": [{"file": "a.py", "description": "x"}], UNQUOTED: true}',
        r'{"description":"calls.\q end"}',
        '{"a": 1}]',
        '{"a": [1]}}',
        '{"a": {"b": "x"}',  # truncated: the error sits at the end of the text
    ],
)
def test_structural_errors_are_raised_unchanged(raw):
    """The repair only escapes a raw content quote. A missing or doubled
    delimiter, a bare key or a bad escape is a real syntax error: escaping
    some earlier quote would corrupt valid structure (8c886b6 walked back
    across ``}``/``]``/``:`` and past non-delimiter errors)."""
    with pytest.raises(json.JSONDecodeError) as caught:
        _loads_model_json(raw)
    assert caught.value.doc == raw


@pytest.mark.parametrize(
    ("raw", "field", "expected"),
    [
        (
            '{"verdict":"CONFIRMED","description":"returns "}" when the list is empty"}',
            "description",
            'returns "}" when the list is empty',
        ),
        ('{"description":"closes with "]" here","line":3}', "description", 'closes with "]" here'),
    ],
)
def test_quoted_closer_token_inside_a_value_is_repaired(raw, field, expected):
    """#234: a description quoting ``"}"`` was repaired on main (8c886b6) and
    must stay repaired under the bounded walk-back."""
    assert _loads_model_json(raw)[field] == expected


@pytest.mark.parametrize(
    "raw",
    ['{"a":"x"}"junk"', '{"a":"x"}"', '{"a":"x"} "b"'],
)
def test_trailing_data_after_a_complete_object_still_raises(raw):
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            '{"findings":[{"description":"returns "}" here","line":1}],"code_excerpts":[]}',
            {"findings": [{"description": 'returns "}" here', "line": 1}], "code_excerpts": []},
        ),
        (
            '{"findings":[{"line":1,"description":"returns "}" here"}],"code_excerpts":[]}',
            {"findings": [{"line": 1, "description": 'returns "}" here'}], "code_excerpts": []},
        ),
        (
            '{"notes":{"items":["closes "]" here","b"]}}',
            {"notes": {"items": ['closes "]" here', "b"]}},
        ),
    ],
)
def test_quoted_closer_token_nested_in_findings_is_repaired(raw, expected):
    """#216 falsification P1: the real reply shape nests the description in
    the findings array, where json reports "Expecting ',' delimiter" instead
    of "Extra data". Base e412ade repaired it; the fix must too."""
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"d":"the args "foo" "bar" are swapped"}', 'the args "foo" "bar" are swapped'),
        ('{"d":"pass "a" "b"","line":2}', 'pass "a" "b"'),
        ('{"d":"x "a"\t"b" y"}', 'x "a"\t"b" y'),
        ('{"d":"use "}"  "]", then stop"}', 'use "}"  "]", then stop'),
    ],
)
def test_adjacent_quoted_words_inside_content_are_repaired(raw, expected):
    """#216 falsification P2: string-whitespace-string is a missing comma only
    when the second string is a member or element (``:``, ``,`` or a closer
    follows it). Prose quoting two tokens side by side is content."""
    assert _loads_model_json(raw)["d"] == expected


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict":"DISMISSED" "reasoning":"x"}',
        '{"items":["a" "b"]}',
        '{"items":["a" "b","c"]}',
        '{"a":"x" "b": 1}',
        '{"items":["a" "b", 2]}',
        '{"items":["a" "b", true]}',
        '{"a":"x" "b',  # truncated reply: nothing follows the second string
    ],
)
def test_member_position_missing_comma_still_raises(raw):
    with pytest.raises(json.JSONDecodeError) as caught:
        _loads_model_json(raw)
    assert caught.value.doc == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"d": "x "}]" y"}', {"d": 'x "}]" y'}),
        ('{"d": "x "}," y"}', {"d": 'x "}," y'}),
        ('{"d": "x "]}" y"}', {"d": 'x "]}" y'}),
        (
            '{"findings": [{"description": "x "}]" y", "line": 1}]}',
            {"findings": [{"description": 'x "}]" y', "line": 1}]},
        ),
        ('{"reasoning": "ok "},"", "f": 1}', {"reasoning": 'ok "},"', "f": 1}),
    ],
)
def test_multi_character_closer_token_inside_a_value_is_repaired(raw, expected):
    """#216 review 6261541244: tokens such as ``"}]"`` and ``"},"`` were
    repaired on base; the closer-token repair walks back over the whole run."""
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":"x"}}',
        '{"a":"x"}]',
        '{"a":"x"},',
        '{"a":["x"]]}',
        '[{"a":"x"}}]',
        # Nested "}," followed by a string element is unrecoverable without
        # guessing (base raises too): the key separator turns up far later.
        '{"findings": [{"description": "x "}," y", "line": 1}]}',
    ],
)
def test_extra_closers_after_a_complete_value_still_raise(raw):
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw)


@pytest.mark.parametrize(
    "raw",
    [
        # Content ", " read as a member separator: the "repair" invents keys
        # such as '], None", "line' and drops the finding's real fields.
        '{"reasoning": "foo, value, "", "ok", empty, value", "findings": []}',
        '{"findings": [{"description": "returns, ",", ok, empty", "line": 42}]}',
    ],
)
def test_repair_that_yields_a_key_with_a_raw_quote_fails_closed(raw):
    """#216 review 6261799749: a repaired parse whose object keys contain a
    raw quote came from escaping structure, not content. Raise the original
    error instead of returning it."""
    with pytest.raises(json.JSONDecodeError) as caught:
        _loads_model_json(raw)
    assert caught.value.doc == raw


def test_quoted_json_in_a_value_is_still_repaired():
    raw = '{"excerpt": "cfg = {"a": 1, "b": [2]}", "line": 3}'
    assert _loads_model_json(raw) == {"excerpt": 'cfg = {"a": 1, "b": [2]}', "line": 3}


def test_valid_json_with_a_quote_in_a_key_is_returned_unchanged():
    assert _loads_model_json('{"a\\"b": 1}') == {'a"b': 1}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # json stops at a letter after "}": still a closer token (base
        # repairs it; the old quote/closer-only stop check regressed it)
        ('{"d": "x "}y z"}', {"d": 'x "}y z'}),
    ],
)
def test_closer_run_edges(raw, expected):
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    "excerpt",
    [
        'parts = line.split(" ")',
        'print(a, " ", b)',
        'indent = " " * 4',
        'cfg = {"k": " "}',
        'sep = ": "',
    ],
)
def test_everyday_code_excerpts_with_quoted_strings_are_repaired(excerpt):
    """#216 review 6262018713: the missing-comma guard must not reject
    ordinary code that quotes short strings; main parses these."""
    raw = '{"excerpt": "%s", "line": 7}' % excerpt
    assert _loads_model_json(raw) == {"excerpt": excerpt, "line": 7}
    prose = '{"description": "uses %s here", "line": 7}' % excerpt
    if not excerpt.endswith("}"):  # '"} here' is a known fail-closed shape
        assert _loads_model_json(prose)["description"] == "uses %s here" % excerpt


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": "x",, "b": "y"}',
        '{"items": ["x",, "y"]}',
    ],
)
def test_doubled_separator_after_a_string_still_raises(raw):
    with pytest.raises(json.JSONDecodeError) as caught:
        _loads_model_json(raw)
    assert caught.value.doc == raw


def test_a_top_level_string_with_the_most_repairs_parses():
    """Every quote but the two outer ones needs escaping: the loop bound is
    exactly enough (count - 1 attempts)."""
    assert _loads_model_json('"a"b"c"') == 'a"b"c'


@pytest.mark.parametrize(("raw", "expected"), [("[1, 2]", [1, 2]), ('{"a": 1}', {"a": 1}), ("7", 7)])
def test_replies_with_few_quotes_still_parse(raw, expected):
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # a closed array before the value: the value still sits in the object
        ('{"x": [1], "d": "f(a, " ", 2)"}', {"x": [1], "d": 'f(a, " ", 2)'}),
        # an escaped quote and a bracket inside an earlier string are content
        ('{"x": "a\\"[", "d": "f(a, " ", 2)"}', {"x": 'a"[', "d": 'f(a, " ", 2)'}),
    ],
)
def test_the_enclosing_container_skips_strings_and_closed_brackets(raw, expected):
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"d": "end "x","}', {"d": 'end "x",'}),
        ('{"excerpt": "  "name": "forge","}', {"excerpt": '  "name": "forge",'}),
        (
            '{"findings": [{"excerpt": "    "a": 1, "b": "c","}]}',
            {"findings": [{"excerpt": '    "a": 1, "b": "c",'}]},
        ),
    ],
)
def test_content_ending_in_a_quoted_value_and_comma_is_repaired(raw, expected):
    """#216 forge LOCAL P2: json reports "Unterminated string" at the value's
    closing quote; base repairs these (an excerpt line like '"name": "forge",')."""
    assert _loads_model_json(raw) == expected


@pytest.mark.parametrize("raw", ['{"a": "x", "', '{"a": "x", "b', '["x", "'])
def test_truncated_replies_still_raise(raw):
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw)


def test_trailing_comma_repair_in_a_later_object_ignores_an_earlier_objects_repair():
    raw = '[{"a": "x "y" z"}, {"d": "end "x","}]'
    assert _loads_model_json(raw) == [{"a": 'x "y" z'}, {"d": 'end "x",'}]


@pytest.mark.parametrize(
    "raw",
    [
        # An earlier repair in the same container already split the content
        # into members; the trailing '"..",' may belong to that earlier value.
        '{"k0": ""x","name": "forge","}',
        '["plain"name": "forge",  "a": 1, "b": "c","]',
    ],
)
def test_trailing_comma_repair_after_an_earlier_split_raises(raw):
    with pytest.raises(json.JSONDecodeError):
        _loads_model_json(raw)
