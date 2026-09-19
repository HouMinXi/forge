# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Decide whether a model reply is a cut-off prefix of valid JSON.

A gateway can label a stream finished while the reply is still mid-object.
Judging that from the decoder error message only catches an unterminated
string: a cut landing on a comma, a colon, a bare literal or a number reads
as a structural error instead, and those are most cut positions in a
findings array.

Scan once and judge by the state the scan ends in. Anything still open --
a string, a container, a partial literal, a growable number -- can be
completed by more input. Text that closes cleanly is either a finished
document or trailing junk, and neither is worth a continuation request.
"""

import json


_HEX = "0123456789abcdefABCDEF"
_ESCAPES = '"\\/bfnrtu'
_LITERALS = ("true", "false", "null")
_NUMBER_CHARS = "0123456789+-.eE"


def _is_number_prefix(text):
    """True when more digits could still make text a JSON number."""
    if text == "-":
        return True
    if text[-1] in "+-.eE":
        return True
    try:
        float(text)
    except ValueError:
        return False
    return True


def is_truncated(text):
    """True when text is a cut-off prefix of some valid JSON document.

    A cut can always be completed by appending bytes; structural damage
    cannot, and spending a continuation on it buys nothing.

    Known gap: a second colon in one object pair ({"a":"b":) reads as a
    cut even though no suffix rescues it. Tracking key-versus-value
    position rejects that sample but also rejects the ordinary
    {"a":"b","c":1}, so the cheap check stays and the rare malformed
    shape costs one wasted continuation.
    """
    s = text.strip()
    if not s:
        return False

    stack = []
    literal = ""
    number = ""
    expect_value = True
    chars = iter(s)

    for ch in chars:
        if ch == '"':
            # Consume the string here so no escape flag has to survive
            # between iterations; a flag creates a state well-formed
            # input never reaches.
            if not expect_value:
                return False
            closed = False
            for sch in chars:
                if sch == '\\':
                    nxt = next(chars, None)
                    if nxt is None:
                        return True
                    if nxt not in _ESCAPES:
                        return False
                    if nxt == "u":
                        hexes = [next(chars, None) for _ in range(4)]
                        # a bad digit is a broken escape even when the
                        # run also ended early: check it before the cut
                        if any(h is not None and h not in _HEX
                               for h in hexes):
                            return False
                        if any(h is None for h in hexes):
                            return True
                    continue
                if sch == '"':
                    closed = True
                    break
            if not closed:
                return True
            expect_value = False
            continue

        if literal:
            literal += ch
            if not any(lit.startswith(literal) for lit in _LITERALS):
                return False
            if literal in _LITERALS:
                literal = ""
            continue

        if number:
            if ch in _NUMBER_CHARS:
                number += ch
                continue
            if not _is_number_prefix(number):
                return False
            number = ""

        if ch in ' \t\n\r':
            continue
        if ch in "{[":
            if not expect_value:
                return False
            stack.append(ch)
            expect_value = True
        elif ch in "}]":
            if not stack or (ch == "}") != (stack[-1] == "{"):
                return False
            stack.pop()
            expect_value = False
        elif ch == ",":
            if expect_value or not stack:
                return False
            expect_value = True
        elif ch == ":":
            if expect_value or not stack or stack[-1] != "{":
                return False
            expect_value = True
        elif ch in "tfn":
            if not expect_value:
                return False
            literal = ch
            expect_value = False
        elif ch in "-0123456789":
            if not expect_value:
                return False
            number = ch
            expect_value = False
        else:
            return False

    if number:
        if not _is_number_prefix(number):
            return False
        # more digits may still arrive inside a container; at top level a
        # parseable number means the document already finished
        return bool(stack) or number[-1] in "+-.eE"
    return bool(literal or stack)


def json_cut_at_eof(text):
    """Name kept from the earlier fix; the scanner answers the question."""
    return is_truncated(text)


def json_cut_inside_string(text):
    """True when text still sits inside an unclosed JSON string at EOF.

    Narrower than is_truncated: a cut between two array elements leaves
    no string open. The invoke path keeps both names because the two
    conditions drive different diagnostics.
    """
    chars = iter(text)
    for char in chars:
        if char != '"':
            continue
        for inner in chars:
            if inner == "\\":
                next(chars, None)
                continue
            if inner == '"':
                break
        else:
            return True
    return False


# Kept so the decoder-position tests from the earlier fix still run. The
# scanner never consults the decoder, but those tests pin real behaviour
# of the review JSON loader and stay worth running.
_JSON_TEXT_STRICT = False


def _decode_error_pos(exc):
    """Take a non-negative integer pos from JSONDecodeError; else -1."""
    pos = getattr(exc, "pos", None)
    if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0:
        return pos
    return -1


def _loads_review_json(text):
    """Parse review JSON with the non-strict decoder."""
    return json.loads(text, strict=_JSON_TEXT_STRICT)
