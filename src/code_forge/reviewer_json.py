# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared reviewer JSON validation and excerpt collection.

Used by both factories.py (Outlet A) and outlet_c.py (Outlet C).
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

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
    "Reply with the JSON object only -- no markdown code fences, no "
    "surrounding text. Raw JSON on the first line.\n"
    "Each diff hunk MUST have at least one code_excerpt.\n"
    "Even if findings is empty, provide code_excerpts "
    "covering each changed hunk.\n"
    "code_excerpts content must be actual source code lines, "
    "not diff format -- no +/- prefixes, no @@ headers.\n"
    "Every code_excerpt must fall inside a diff hunk. An excerpt is the "
    "evidence that you checked a changed line, and a line the diff never "
    "touched is not something that claim can be checked against. Code you "
    "read for orientation but did not verify -- a signature above the "
    "change, a caller in another file -- goes in an optional "
    '"context_quotes": [{"file": "...", "content": "..."}] instead. It '
    "carries no line numbers because it asserts nothing about them.\n"
    "start_line and end_line are post-image line numbers of the new file; "
    "the @@ header's old-side start is not a source line.\n"
)


def _strip_fence(raw: str) -> str:
    """Strip a complete markdown fence envelope if one wraps the reply.

    Models routed through some gateways wrap their JSON in ```json
    fences. Only a full envelope is stripped -- an opening fence line
    and a closing fence line with nothing outside -- because that is
    the only shape that is unambiguously formatting rather than
    content. Anything else is returned unchanged so validation still
    fails closed on it.
    """
    if not isinstance(raw, str):
        # Non-string input reaches json.loads as before, whose
        # TypeError the caller converts to ValueError -- this helper
        # must not raise an AttributeError ahead of that contract.
        return raw
    text = raw.strip()
    if not text.startswith("```"):
        return text
    first_nl = text.find("\n")
    if first_nl == -1:
        # A lone fence line is not an envelope.
        return text
    opener = text[:first_nl].strip().lower()
    if opener not in ("```", "```json"):
        return text
    if not text.endswith("```"):
        return text
    tail = text[:-3]
    last_nl = tail.rfind("\n")
    if last_nl == -1 or tail[last_nl + 1:].strip():
        # The closing fence must sit on its own line; only indentation
        # may precede it. A ``` that terminates a JSON string is
        # content, not a fence -- cutting it would amputate the string
        # and the parse fails anyway, but "the fence line" is only
        # ever a fence when it is one.
        return text
    return text[first_nl + 1:-3].strip()


def validate_reviewer_json(raw: str | dict) -> dict:
    """Validate reviewer output against the receipt schema.

    Accepts either a JSON string or an already-parsed dict.
    A markdown fence envelope around a JSON string is stripped before
    parsing. Raises ValueError on any schema violation (fail-closed).
    """
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(_strip_fence(raw))
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


_LINE_BUCKET_SIZE: int = 10
"""Quantisation step for location-stable fingerprints.

Lines rounded to the nearest multiple of this value are treated as the
same location.  The value 10 absorbs the +/-3 line jitter measured on
real LLM outputs (e.g. 979/981/982 all round to 980) while keeping
genuinely distant lines distinct.
"""


def _location_fingerprint(
    file_path: str, line: int, pass_name: str,
) -> str:
    """Compute a location-stable fingerprint for an L1 finding.

    The fingerprint is determined by (file, line_bucket, pass_name) only,
    NOT by description text.  This means a model that restates the same
    issue in different words across rounds produces the same fingerprint,
    so the convergence state machine treats it as the same finding rather
    than a new one that resets the clean-round counter.

    Two genuinely different findings at the same file+line_bucket from the
    same pass collapse into one fingerprint.  That is acceptable: a single
    pass rarely reports two semantically distinct issues at the exact same
    line, and when it does the dedup layer keeps whichever it encounters
    first (insertion order, not severity).  This is an explicit trade-off:
    location stability for convergence is worth losing a rare same-bucket
    duplicate.
    """
    bucket = round(line / _LINE_BUCKET_SIZE) * _LINE_BUCKET_SIZE
    fp_src = "%s:%d:%s" % (file_path, bucket, pass_name)
    return hashlib.sha256(fp_src.encode()).hexdigest()[:16]


def _dedup_by_fingerprint(
    findings: list, seen=None,
) -> list:
    """Fold findings to one per fingerprint, first-in-wins.

    A finding whose fingerprint was already seen is dropped regardless
    of severity; the first occurrence in insertion order survives.  The
    `seen` set may carry state from earlier folds (the provider loops
    pass the same set across passes) so the dedup holds across an entire
    fold, not just within one batch.

    Shared by every L1 fold site -- build_l1_provider,
    build_grouped_l1_provider and build_sampling_l1_provider in
    factories.py, plus the L1 chunk fold in outlet_c.py -- so the
    "first-in-wins" claim is true on every path, not just one.
    """
    if seen is None:
        seen = set()
    kept = []
    for f in findings:
        if f.fingerprint in seen:
            continue
        seen.add(f.fingerprint)
        kept.append(f)
    return kept


def _json_to_state_findings(
    data: dict, pass_name: str, backend: Optional[str] = None,
) -> list:
    """Convert validated reviewer JSON findings to StateFinding list.

    Shared by outlet_c.py (Outlet C) and factories.py (Outlet A) to
    ensure identical fingerprint computation across both code paths.

    backend names the model that produced these findings, so the ledger
    can attribute them later. It stays optional: a caller with nothing
    truthful to name leaves it None rather than guessing.
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
        fp = _location_fingerprint(file_path, line, pass_name)
        findings.append(StateFinding(
            id="l1-%s-%s" % (pass_name, fp),
            fingerprint=fp,
            source="L1",
            disposition=Disposition.UNCERTAIN,
            file=file_path,
            line_range=[line, line],
            description="[%s] %s" % (pass_name, desc),
            backend=backend,
        ))
    return findings
