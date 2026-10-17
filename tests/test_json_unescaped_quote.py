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
