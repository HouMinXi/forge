#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Adapter base types -- CanonicalFinding, ExtractedFinding, BaseAdapter.

Defines the data contracts for source adapters and LLM parser output.
CanonicalFinding is the pre-LLM schema (adapter-set fields).
ExtractedFinding is the post-LLM schema (LLM-extracted fields).
BaseAdapter is the abstract base class for all source adapters.
"""

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

# --- Module-level constants (M1, M5) ---
MAX_RAW_SOURCE = 5000
MAX_CONTEXT_EXCERPT = 2000
TIMEOUT_NETWORK = 60
TIMEOUT_GIT_FAST = 10
TIMEOUT_GIT_SLOW = 120


@dataclass
class CanonicalFinding:
    """Pre-LLM canonical schema from adapter (D1).

    Fields set by the adapter before LLM processing.

    Attributes:
        source: Data source type ("github_pr", "git_log", "ci_log").
        source_tool: Tool attribution ("human", "qodo", "coderabbit",
            "copilot", "github-actions", "unknown", "git", "ci").
        source_id: Unique ID within source (GitHub comment ID as str,
            commit SHA, or sha256(content[:1024])-index).
        timestamp: ISO-8601 original event time.
        raw_source: Original unprocessed text.
        context: Dict with required and optional per-source fields.
            Required fields:
                diff_hunk (str or None): code diff context.
            Source-specific optional fields:
                github_pr: pr_url (str), path (str or None).
                git_log: commit_sha (str), branch (str).
                ci_log: ci_output_excerpt (str).
    """

    source: str
    source_tool: str
    source_id: str
    timestamp: str
    raw_source: str
    context: dict

    def __post_init__(self) -> None:
        """Validate required fields are non-empty strings."""
        for field_name in ('source', 'source_id', 'timestamp'):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"CanonicalFinding.{field_name} must be a "
                    f"non-empty string, got {value!r}"
                )


@dataclass
class ExtractedFinding:
    """Post-LLM structured finding (D2).

    Fields extracted by the LLM parser from raw comment text.

    Attributes:
        dimension_raw: Free-text concern description (NOT a canonical
            dimension name).
        confidence: LLM self-reported extraction faithfulness (0-1).
        suggested_keywords: 2-5 keywords characterizing the concern.
        text: One-sentence structured finding description.
        file: Code file path (None if general comment).
        line: Line number (None if general comment).
    """

    dimension_raw: str
    confidence: float
    suggested_keywords: List[str]
    text: str
    file: Optional[str]
    line: Optional[int]


class BaseAdapter(ABC):
    """Abstract base class for source adapters.

    Each adapter normalizes raw input from a specific source into
    a list of CanonicalFinding objects. The fetch method is the
    single public entry point -- source_ref is the CLI argument
    value (PR ref, branch name, or file path).

    Subclasses override _do_fetch() (not fetch()). The base fetch()
    wrapper guarantees the return value is always a list (never None).
    """

    def fetch(self, source_ref: str) -> List[CanonicalFinding]:
        """Fetch and normalize raw input from the source.

        Calls _do_fetch() and enforces the List return contract.
        Subclasses must not override this method -- override
        _do_fetch() instead.

        Args:
            source_ref: CLI argument value. Format depends on
                adapter type (e.g., "owner/repo#N" for github_pr,
                branch name for git_log, file path for ci_log).

        Returns:
            List of CanonicalFinding objects. Empty list on error
            (never None -- adapter contract returns List).
        """
        result = self._do_fetch(source_ref)
        if result is None:
            print(
                "forge: warning: adapter returned None, "
                "coercing to empty list",
                file=sys.stderr,
            )
            return []
        return result

    @abstractmethod
    def _do_fetch(self, source_ref: str) -> List[CanonicalFinding]:
        """Fetch implementation for subclasses to override.

        Args:
            source_ref: CLI argument value (same as fetch()).

        Returns:
            List of CanonicalFinding objects. Should return []
            on error, but the base fetch() guards against None.
        """
