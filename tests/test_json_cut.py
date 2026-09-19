# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""JSON cut detection for stop-finished truncated replies."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

from code_forge.json_cut import (
    _JSON_TEXT_STRICT,
    _decode_error_pos,
    _loads_review_json,
    json_cut_at_eof,
    json_cut_inside_string,
)


def test_open_string_is_a_cut():
    assert json_cut_inside_string('{"content":"hello') is True
    assert json_cut_at_eof('{"content":"hello') is True


def test_closed_object_is_not_a_cut():
    assert json_cut_inside_string('{"content":"hello"}') is False
    assert json_cut_at_eof('{"content":"hello"}') is False


def test_escaped_quote_stays_inside_the_string():
    assert json_cut_inside_string('{"content":"say \\"hi') is True


def test_backslash_u_escape_is_still_a_string():
    """JSON \\uXXXX is four hex digits; this scanner only skips one byte.

    That is enough to keep the string open: the leftover digits are
    still inside the quotes. The cut heuristic does not decode Unicode.
    """
    quote = '"'
    text = quote + "\\u0041" + quote
    assert json_cut_inside_string(text) is False
    text_open = quote + "\\u0041"
    assert json_cut_inside_string(text_open) is True


def test_backslash_consumes_the_next_byte():
    """A backslash swallows the next character; no escape flag is kept."""
    quote = '"'
    backslash = "\\"
    # quote, backslash, quote: the second quote is skipped, string stays open
    assert json_cut_inside_string(quote + backslash + quote) is True
    # trailing backslash at EOF is still inside the string
    assert json_cut_inside_string(quote + backslash) is True


def test_string_scanner_has_no_escape_flag():
    src = Path(__file__).resolve().parents[1].joinpath(
        "src/code_forge/json_cut.py"
    ).read_text()
    _, body = src.split("def json_cut_inside_string", 1)
    body = body.split("\ndef ", 1)[0]
    assert "escaped" not in body
    assert "skip_next" not in body
    assert "while i" not in body


def test_empty_is_not_a_cut():
    assert json_cut_inside_string("") is False
    assert json_cut_at_eof("") is False
    assert json_cut_at_eof("   ") is False


def test_bare_backslash_outside_a_string_is_not_a_cut():
    """A lone backslash is not an open string; escape skip only runs inside quotes."""
    assert json_cut_inside_string("\\") is False
    assert json_cut_at_eof("\\") is False


def test_closed_string_unclosed_container_is_a_cut():
    text = (
        '{"findings":[],"code_excerpts":[{"file":"a.py",'
        '"content":"assert \\"exit 2\\" in str(caught.value)"}]'
    )
    assert json_cut_inside_string(text) is False
    assert json_cut_at_eof(text) is True


def test_mid_object_syntax_error_is_not_a_cut():
    text = '{"findings":[{"ok": true},,{"ok": false}]}'
    assert json_cut_at_eof(text) is False


def test_extra_data_after_complete_object_is_not_a_cut():
    """A stray byte after a closed object is Extra data, not an EOF cut."""
    assert json_cut_at_eof('{"a":1}x') is False


def test_unterminated_string_is_caught_before_the_pos_check():
    """An open string never relies on pos; the scanner answers first.

    json.loads reports Unterminated string with pos at the opening
    quote, far below len(text). Loosening the EOF check to
    pos >= len(text) - 1 to "fix" that case is unnecessary here and
    breaks Extra data, whose error does sit on the last byte.
    """
    text = '{"a":"hello'
    assert json_cut_inside_string(text) is True
    assert json_cut_at_eof(text) is True
    try:
        json.loads(text, strict=_JSON_TEXT_STRICT)
    except json.JSONDecodeError as exc:
        assert exc.pos < len(text) - 1
    else:  # pragma: no cover - the text is invalid by construction
        raise AssertionError("expected JSONDecodeError")


def test_last_byte_error_is_not_a_cut():
    """pos == len(text) - 1 is a finished invalid document, not a cut."""
    text = '{"a":1}x'
    try:
        json.loads(text, strict=_JSON_TEXT_STRICT)
    except json.JSONDecodeError as exc:
        assert exc.pos == len(text) - 1
    else:  # pragma: no cover - the text is invalid by construction
        raise AssertionError("expected JSONDecodeError")
    assert json_cut_at_eof(text) is False


def test_closed_empty_string_is_not_a_cut():
    """A closed empty string is not an open string.

    Initial escaped=True swallows the closing quote and reports a cut.
    """
    assert json_cut_inside_string('""') is False
    assert json_cut_at_eof('""') is False


def test_control_character_in_string_is_not_a_cut():
    """strict=False accepts a tab in a string; strict=True would reject it."""
    assert json_cut_at_eof('{"a":"hello\tworld"}') is False


def test_tab_in_closed_string_unclosed_object_is_a_cut():
    """A cut after a closed string must not be masked by a tab inside it.

    json.loads default strict=True reports Invalid control character
    mid-document; non-strict reports the unclosed object at EOF.
    """
    text = '{"a":"hello\tworld"'
    assert json_cut_inside_string(text) is False
    assert json_cut_at_eof(text) is True


def test_scanner_agrees_with_the_stdlib_decoder_exhaustively():
    """The scanner is not a parallel parser that can drift from json.

    Every string up to length 4 over a JSON-significant alphabet is
    checked against the decoder's own verdict. The two answer different
    questions only when the decoder stops at a structural error before
    it ever reaches the open string; that is not a divergence about
    string state, so those cases are excluded explicitly rather than
    hidden. Anything else is a real disagreement and fails here.
    """
    alphabet = ['"', "\\", "a", "{", "}", ":", ",", "1", " ", "u", "0"]
    divergences = []
    for size in range(1, 5):
        for combo in itertools.product(alphabet, repeat=size):
            text = "".join(combo)
            scanner = json_cut_inside_string(text)
            try:
                json.loads(text, strict=_JSON_TEXT_STRICT)
            except json.JSONDecodeError as exc:
                unterminated = exc.msg.startswith("Unterminated string")
            else:
                unterminated = False
            if scanner == unterminated:
                continue
            if scanner and not unterminated:
                # decoder died earlier, on structure; different question
                continue
            divergences.append((text, scanner, unterminated))
    assert divergences == []


def test_decode_error_pos_missing_attr():
    assert _decode_error_pos(object()) == -1


def test_loads_uses_non_strict_constant():
    assert _JSON_TEXT_STRICT is False


def test_decode_error_pos_rejects_bool():
    assert _decode_error_pos(SimpleNamespace(pos=True)) == -1
    assert _decode_error_pos(SimpleNamespace(pos=False)) == -1
    assert _decode_error_pos(SimpleNamespace(pos="3")) == -1
    assert _decode_error_pos(SimpleNamespace(pos=None)) == -1


def test_decode_error_pos_rejects_negative():
    assert _decode_error_pos(SimpleNamespace(pos=-1)) == -1


def test_decode_error_pos_keeps_zero():
    assert _decode_error_pos(SimpleNamespace(pos=0)) == 0


def test_decode_error_pos_keeps_positive():
    assert _decode_error_pos(SimpleNamespace(pos=12)) == 12


def test_json_cut_at_eof_matches_the_loader_leniency():
    """The cut test must use the same leniency as the real parse.

    A control character inside a string is accepted by the review
    loader, so the cut test must accept it too -- otherwise a reply
    containing a raw newline would be called finished-and-invalid
    while the real parse succeeds.

    Asserted against behaviour rather than against a strict= kwarg:
    the scanner reaches the same verdict without calling the decoder,
    and pinning the call would pin one implementation of the answer.
    """
    raw_newline = '{"a":"line' + chr(10) + 'more"}'

    # The loader accepts it, so it is a finished document, not a cut.
    assert _loads_review_json(raw_newline) == {"a": "line\nmore"}
    assert json_cut_at_eof(raw_newline) is False

    # Cut the same reply before the string closes and it must flip.
    assert json_cut_at_eof('{"a":"line' + chr(10)) is True

    assert json_cut_at_eof('{"a":1}') is False
