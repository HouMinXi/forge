# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Detect JSON replies that stopped before the document closed."""
from __future__ import annotations

import json

_JSON_TEXT_STRICT = False


def json_cut_inside_string(text: str) -> bool:
    """True when text still sits inside a JSON string at EOF.

    A gateway can label the stream finish_reason=stop while the last
    string never closed. That is a cut, not a finished invalid object.

    A backslash swallows exactly one following character, which is all
    the open/closed question needs: \\uXXXX leaves four hex digits
    behind, and hex digits are ordinary string bytes that cannot close
    a string. Decoding escapes properly would change no answer here.
    """
    in_string = False
    chars = iter(text)
    for char in chars:
        if in_string:
            if char == "\\":
                next(chars, None)
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
    return in_string


def _decode_error_pos(exc: object) -> int:
    """Take a non-negative integer pos from JSONDecodeError; else -1."""
    pos = getattr(exc, "pos", None)
    if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0:
        return pos
    return -1


def _loads_review_json(text: str) -> object:
    """Parse review JSON with the non-strict decoder."""
    return json.loads(text, strict=_JSON_TEXT_STRICT)


def json_cut_at_eof(text: str) -> bool:
    """True when the reply stopped mid-document rather than finishing.

    Two shapes count as a cut. The obvious one is an unterminated
    string. The subtler one is a closed last string inside a container
    that never closed: finish_reason=stop can still land there, and the
    parser then reports its error with pos at len(text).

    Extra data after a complete object is not a cut: that error sits on
    the extra byte, which is still inside the buffer.
    """
    if not text.strip():
        return False
    if json_cut_inside_string(text):
        return True
    try:
        _loads_review_json(text)
    except json.JSONDecodeError as exc:
        # pos == len(text): the decoder ran out of input and asked for
        # more, which is what a cut looks like. pos == len(text) - 1:
        # the error sits ON the last byte, so the byte was present and
        # wrong -- a finished invalid document, not a truncated one.
        # Extra data ('{"a":1}x') is exactly that case, which is why
        # this boundary must not be loosened to len(text) - 1.
        return _decode_error_pos(exc) >= len(text)
    return False
