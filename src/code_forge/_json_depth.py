# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Explicit pre-decoding depth budget for the two qualified JSON consumers.

This is not a JSON decoder. Syntax and values remain the standard decoder's
responsibility; only nesting is bounded independently of its recursion limit.
"""

MAX_JSON_CONTAINER_DEPTH = 512


def check_json_container_depth(text: str) -> None:
    """Count the root container as level one, ignoring delimiters in strings.

    The stack never exceeds the fixed budget. A depth failure deliberately
    remains a RecursionError so each caller keeps its existing failure route.
    Other JSON readers do not acquire this contract by importing this module.
    """
    stack = []
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            if len(stack) == MAX_JSON_CONTAINER_DEPTH:
                raise RecursionError("JSON container recursion exceeds the 512-level limit")
            stack.append("]" if char == "[" else "}")
        elif char in "]}":
            if not stack or stack.pop() != char:
                raise ValueError("Mismatched JSON container delimiter")
