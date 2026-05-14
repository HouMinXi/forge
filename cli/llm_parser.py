#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""LLM parser -- extracts structured findings from raw comments via Anthropic SDK (D2)."""

import hashlib
import json
import re
import sys
from typing import List, Optional

from cli.adapters.base import CanonicalFinding, ExtractedFinding

DEFAULT_MODEL = "claude-haiku-3.5"

# M4: limit regex search to prevent ReDoS on large responses
_MAX_REGEX_SEARCH_LEN = 10000


def _get_client():
    """Get an Anthropic API client.

    The SDK reads ANTHROPIC_API_KEY from the environment automatically.
    Returns None with a warning if the SDK is not available or the API
    key is missing.

    Returns:
        anthropic.Anthropic instance, or None on error.
    """
    try:
        import anthropic
        return anthropic.Anthropic()
    except ImportError:
        print(
            "forge: warning: anthropic SDK not available, "
            "LLM parsing disabled",
            file=sys.stderr,
        )
        return None
    except anthropic.AuthenticationError as exc:
        print(
            f"forge: error: ANTHROPIC_API_KEY not set or "
            f"invalid. Set it or use 'claude -p' fallback. "
            f"({exc})",
            file=sys.stderr,
        )
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Parse JSON from LLM response text.

    Tries direct parse first. If that fails, attempts to extract
    JSON from markdown code blocks (```json ... ``` or ``` ... ```).
    Regex search is limited to first _MAX_REGEX_SEARCH_LEN chars
    to prevent ReDoS on large responses.

    Args:
        text: Raw response text from LLM.

    Returns:
        Parsed JSON object, or None on failure.
    """
    # Try direct JSON parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # M4: limit regex search range
    search_text = text[:_MAX_REGEX_SEARCH_LEN]

    # Try to extract from markdown code blocks
    patterns = [
        r'```json\s*\n(.*?)\n\s*```',
        r'```\s*\n(.*?)\n\s*```',
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

    return None


def _validate_extracted(data: dict) -> Optional[ExtractedFinding]:
    """Validate and normalize extracted finding data.

    Ensures correct types: confidence clamped to 0-1, keywords is
    a list, file can be str or None, line can be int or None.

    Args:
        data: Dict from parsed JSON.

    Returns:
        ExtractedFinding instance, or None if data is invalid.
    """
    if not isinstance(data, dict):
        return None

    dimension_raw = str(data.get('dimension_raw', ''))
    if not dimension_raw:
        return None

    # Confidence: float, clamped to 0-1
    try:
        confidence = float(data.get('confidence', 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    # Keywords: must be a list of strings
    keywords = data.get('suggested_keywords', [])
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k) for k in keywords if k]

    text = str(data.get('text', ''))

    # File: str or None
    file_path = data.get('file', None)
    if file_path is not None:
        file_path = str(file_path)
        if not file_path or file_path.lower() == 'null':
            file_path = None

    # Line: int or None
    line = data.get('line', None)
    if line is not None:
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = None

    return ExtractedFinding(
        dimension_raw=dimension_raw,
        confidence=confidence,
        suggested_keywords=keywords,
        text=text,
        file=file_path,
        line=line,
    )


def extract_finding_from_comment(
    raw_text: str,
    diff_hunk: Optional[str] = None,
    file_path: Optional[str] = None,
    model: str = DEFAULT_MODEL,
) -> Optional[ExtractedFinding]:
    """Extract a structured finding from a raw comment via LLM.

    Sends the comment text with diff context to an LLM and parses
    the structured JSON response.

    Args:
        raw_text: Original comment text.
        diff_hunk: Diff context (None if not available).
        file_path: Code file path (None if not available).
        model: Anthropic model ID (default: claude-haiku-3.5).

    Returns:
        ExtractedFinding instance, or None on error.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = (
        "Extract a structured code review finding from this comment.\n"
        "\n"
        f"Comment: {raw_text}\n"
        "\n"
        "Diff context:\n"
        f"{diff_hunk or 'No diff context available'}\n"
        "\n"
        f"File: {file_path or 'Unknown'}\n"
        "\n"
        "Return ONLY valid JSON with these fields:\n"
        "- dimension_raw: free-text description of the concern "
        "category (do NOT use a canonical dimension name)\n"
        "- confidence: 0-1 how faithfully this extraction "
        "represents the original comment\n"
        "- suggested_keywords: array of 2-5 keywords "
        "characterizing the concern\n"
        "- text: one-sentence structured finding description\n"
        "- file: code file path (null if general comment)\n"
        "- line: line number as integer (null if general comment)"
    )

    try:
        import anthropic
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        print(
            f"forge: warning: LLM API call failed: {exc}",
            file=sys.stderr,
        )
        return None

    # H1: check for empty response content before accessing
    if not response.content:
        print(
            "forge: warning: LLM returned empty response",
            file=sys.stderr,
        )
        return None

    try:
        response_text = response.content[0].text
    except (IndexError, AttributeError) as exc:
        print(
            f"forge: warning: malformed LLM response: {exc}",
            file=sys.stderr,
        )
        return None

    data = _parse_json_response(response_text)
    if data is None:
        print(
            "forge: warning: failed to parse LLM response as JSON",
            file=sys.stderr,
        )
        return None

    result = _validate_extracted(data)
    if result is None:
        print(
            "forge: warning: LLM response missing required fields",
            file=sys.stderr,
        )
        return None

    return result


def extract_findings(
    canonical_findings: List[CanonicalFinding],
    model: str = DEFAULT_MODEL,
) -> List[tuple]:
    """Extract structured findings from a list of canonical findings.

    Calls extract_finding_from_comment for each canonical finding
    and pairs successful extractions with their source.

    Args:
        canonical_findings: List of CanonicalFinding objects.
        model: Anthropic model ID (default: claude-haiku-3.5).

    Returns:
        List of (CanonicalFinding, ExtractedFinding) tuples.
        Only includes findings where extraction succeeded.
    """
    client = _get_client()
    if client is None:
        return []

    results: List[tuple] = []
    for canonical in canonical_findings:
        diff_hunk = canonical.context.get('diff_hunk')
        file_info = (
            canonical.context.get('path')
            or canonical.context.get('file')
        )

        extracted = extract_finding_from_comment(
            raw_text=canonical.raw_source,
            diff_hunk=diff_hunk,
            file_path=file_info,
            model=model,
        )

        if extracted is not None:
            results.append((canonical, extracted))
        else:
            print(
                f"forge: warning: extraction failed for "
                f"source_id={canonical.source_id}",
                file=sys.stderr,
            )

    return results


def compute_text_hash(text: str) -> str:
    """Compute SHA-256 hash of text for dedup.

    Used by the dedup pipeline in Plan 03 and stored in
    external_findings.json.

    Args:
        text: Text to hash.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
