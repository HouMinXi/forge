# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared reviewer JSON validation and excerpt collection.

Used by both factories.py (Outlet A) and outlet_c.py (Outlet C).
"""
from __future__ import annotations

import hashlib
import json

_REQUIRED_FIELDS = {"findings", "code_excerpts"}
_FINDING_REQUIRED = {"file", "line", "severity", "description"}
_EXCERPT_REQUIRED = {"file", "start_line", "end_line", "content"}
_VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}

# The half of every L1 review prompt that describes the JSON to return. It
# lives next to the validator that enforces that JSON so the two cannot
# drift: asking for a shape validate_reviewer_json rejects fails every pass
# at once. The three L1 sites used to carry byte-identical copies of this
# text, which gave that drift three places to start from. The test-assertion
# pass in cli.py deliberately keeps its own shorter variant -- it reads only
# findings from the response and never collects excerpts, so the excerpt
# instructions here would ask it for output nobody reads.
REVIEW_JSON_CONTRACT = (
    'Return JSON: {"findings": [{"file": "...", "line": N, '
    '"severity": "P0"|"P1"|"P2"|"P3", '
    '"description": "..."}], '
    '"code_excerpts": [{"file": "...", "start_line": N, '
    '"end_line": M, "content": "..."}]}\n'
    "Each diff hunk MUST have at least one code_excerpt.\n"
    "Even if findings is empty, provide code_excerpts "
    "covering each changed hunk.\n"
    "code_excerpts content must be actual source code lines, "
    "not diff format -- no +/- prefixes, no @@ headers. "
    "The @@ line is a diff header, not a source line; do not count "
    "it when determining start_line or end_line.\n"
    "Every code_excerpt must fall inside a diff hunk. An excerpt is the "
    "evidence that you checked a changed line, and a line the diff never "
    "touched is not something that claim can be checked against. Code you "
    "read for orientation but did not verify -- a signature above the "
    "change, a caller in another file -- goes in an optional "
    '"context_quotes": [{"file": "...", "content": "..."}] instead. It '
    "carries no line numbers because it asserts nothing about them.\n"
)


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
        if exc["start_line"] > exc["end_line"]:
            raise ValueError(
                "code_excerpt[%d] start_line %d > end_line %d" % (
                    i, exc["start_line"], exc["end_line"])
            )

    if len(data["findings"]) == 0 and len(data["code_excerpts"]) == 0:
        raise ValueError(
            "findings=0 but code_excerpts empty -- reviewer must provide "
            "per-hunk excerpts even for clean passes"
        )

    return data


def _collect_excerpts(data: dict) -> list[dict]:
    """Extract code_excerpts from validated reviewer JSON."""
    return data.get("code_excerpts", [])


def _json_to_state_findings(data: dict, pass_name: str) -> list:
    """Convert validated reviewer JSON findings to StateFinding list.

    Shared by outlet_c.py (Outlet C) and factories.py (Outlet A) to
    ensure identical fingerprint computation across both code paths.
    """
    from .disposition import Disposition
    from .state import StateFinding

    findings = []
    for f_raw in data.get("findings", []):
        file_path = f_raw.get("file") or "unknown"
        try:
            line = int(f_raw.get("line") or 0)
        except (ValueError, TypeError):
            line = 0
        desc = f_raw.get("description") or ""
        fp_src = "%s:%d:%s" % (file_path, line, desc)
        fp = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
        findings.append(StateFinding(
            id="l1-%s-%s" % (pass_name, fp),
            fingerprint=fp,
            source="L1",
            disposition=Disposition.UNCERTAIN,
            file=file_path,
            line_range=[line, line],
            description="[%s] %s" % (pass_name, desc),
        ))
    return findings
