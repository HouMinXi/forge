# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared reviewer JSON validation and excerpt collection.

Used by both factories.py (Outlet A) and outlet_c.py (Outlet C).
"""
from __future__ import annotations

import json

_REQUIRED_FIELDS = {"findings", "code_excerpts"}
_FINDING_REQUIRED = {"file", "line", "severity", "description"}
_EXCERPT_REQUIRED = {"file", "start_line", "end_line", "content"}
_VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}


def validate_reviewer_json(raw: str | dict) -> dict:
    """Validate reviewer output against the receipt schema.

    Accepts either a JSON string or an already-parsed dict.
    Raises ValueError on any schema violation (fail-closed).
    """
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError("not valid JSON: %s" % e) from e

    if not isinstance(data, dict):
        raise ValueError("not a JSON object")

    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError("missing required field: %s" % field)

    if not isinstance(data["findings"], list):
        raise ValueError("findings must be a list")
    if not isinstance(data["code_excerpts"], list):
        raise ValueError("code_excerpts must be a list")

    for i, f in enumerate(data["findings"]):
        if not isinstance(f, dict):
            raise ValueError("finding[%d] is not a dict" % i)
        for key in _FINDING_REQUIRED:
            if key not in f:
                raise ValueError("finding[%d] missing: %s" % (i, key))
        if f.get("severity") not in _VALID_SEVERITIES:
            raise ValueError("finding[%d] invalid severity: %s" % (i, f.get("severity")))

    for i, exc in enumerate(data["code_excerpts"]):
        if not isinstance(exc, dict):
            raise ValueError("code_excerpt[%d] is not a dict" % i)
        for key in _EXCERPT_REQUIRED:
            if key not in exc:
                raise ValueError("code_excerpt[%d] missing: %s" % (i, key))
        if not isinstance(exc.get("start_line"), int):
            raise ValueError("code_excerpt[%d] start_line must be int" % i)
        if not isinstance(exc.get("end_line"), int):
            raise ValueError("code_excerpt[%d] end_line must be int" % i)

    if len(data["findings"]) == 0 and len(data["code_excerpts"]) == 0:
        raise ValueError(
            "findings=0 but code_excerpts empty -- reviewer must provide "
            "per-hunk excerpts even for clean passes"
        )

    return data


def _collect_excerpts(data: dict) -> list[dict]:
    """Extract code_excerpts from validated reviewer JSON."""
    return data.get("code_excerpts", [])
