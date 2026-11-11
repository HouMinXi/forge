# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Version-independent depth checks do not replace JSON syntax validation."""

import json
from unittest.mock import Mock

import pytest

from code_forge._json_depth import MAX_JSON_CONTAINER_DEPTH, check_json_container_depth
from code_forge.parsers import shellcheck
from code_forge.parsers.base import ToolError


def nested_text(depth, kind, leaf="0"):
    value = leaf
    for level in range(depth):
        value = (
            '{"child":' + value + "}"
            if kind == "dict" or (kind == "mixed" and level % 2)
            else "[" + value + "]"
        )
    return value


@pytest.mark.parametrize("kind", ["list", "dict", "mixed"])
@pytest.mark.parametrize("depth", [511, 512, 513])
@pytest.mark.parametrize(
    "leaf",
    ["0", '"brackets: [] {}, quote: \\", slash: \\\\"', r'"\u0022\u005b\u005d\u007b\u007d"'],
    ids=["number", "escaped-string", "unicode-escapes"],
)
def test_container_budget_has_an_exact_boundary(kind, depth, leaf):
    assert MAX_JSON_CONTAINER_DEPTH == 512
    text = nested_text(depth, kind, leaf)
    if depth <= 512:
        check_json_container_depth(text)
        json.loads(text)
    else:
        with pytest.raises(RecursionError, match="512-level"):
            check_json_container_depth(text)


@pytest.mark.parametrize("leaf", ["[]", "{}"])
def test_empty_containers_count_toward_the_boundary(leaf):
    check_json_container_depth(nested_text(511, "list", leaf))
    with pytest.raises(RecursionError, match="512-level"):
        check_json_container_depth(nested_text(512, "list", leaf))


@pytest.mark.parametrize("value", [None, True, 4, "[" * 600, '\\"[]{}' * 600, "\u005b\u005d"])
def test_scalars_and_escaped_strings_do_not_add_containers(value):
    text = json.dumps(value)
    check_json_container_depth(text)
    assert json.loads(text) == value


def test_siblings_do_not_accumulate_depth():
    text = "[" + ",".join("[]" for _ in range(10000)) + "]"
    check_json_container_depth(text)
    assert len(json.loads(text)) == 10000


@pytest.mark.parametrize("decoy", ["]" * 600, "}" * 600, '\\"' * 600, "\\" * 600])
def test_quoted_closers_and_escapes_cannot_hide_excessive_depth(decoy):
    text = '{"decoy":' + json.dumps(decoy) + ',"nested":' + nested_text(512, "mixed") + "}"
    with pytest.raises(RecursionError, match="512-level"):
        check_json_container_depth(text)


@pytest.mark.parametrize("text", ["[}", "]", "[", '"unterminated', '"bad\\q"', "[] {}", '{"x":}'])
def test_malformed_text_still_reaches_a_rejection(text):
    with pytest.raises(ValueError):
        check_json_container_depth(text)
        json.loads(text)
    result = shellcheck.parse_shellcheck(text)
    assert len(result) == 1 and isinstance(result[0], ToolError)
    assert "Failed to parse shellcheck JSON output" in result[0].message


def test_shellcheck_depth_rejection_precedes_json_decoding(monkeypatch):
    decoder = Mock(side_effect=AssertionError("decoder must not see excessive depth"))
    monkeypatch.setattr(shellcheck.json, "loads", decoder)
    with pytest.raises(RecursionError, match="512-level"):
        shellcheck.parse_shellcheck(nested_text(513, "list"))
    decoder.assert_not_called()
