# SPDX-License-Identifier: Apache-2.0

"""Canonical output utilities for the Stage 1 planner."""

import hashlib
import json
import re

SERIALIZATION_VERSION = "plan-json-v1"
SCHEMA_VERSION = 1
PASS_LENSES = {"qodo": ("L1",), "expert": ("L2", "L5"), "adversarial": ("L3", "L4")}
_SHORT_CONTROLS = dict(zip("bfnrt", (8, 12, 10, 13, 9), strict=True))
_ESCAPE = re.compile(r"\\(?:[bfnrt]|\\)")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canon(value) -> bytes:
    """Serialize an already validated JSON value under plan-json-v1."""
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    )

    def expand(match):
        escape = match[0]
        if escape[1] in _SHORT_CONTROLS:
            return f"\\u{_SHORT_CONTROLS[escape[1]]:04x}"
        return escape

    return _ESCAPE.sub(expand, encoded).encode("utf-8")
