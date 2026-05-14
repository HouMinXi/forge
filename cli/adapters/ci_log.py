#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CI log source adapter -- reads local CI failure log files (D1)."""

import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import List

from cli.adapters.base import (
    BaseAdapter,
    CanonicalFinding,
    MAX_CONTEXT_EXCERPT,
    MAX_RAW_SOURCE,
)

HASH_PREFIX_LEN = 1024

# H4: encoding fallback chain
_ENCODING_CHAIN = ('utf-8', 'latin-1')


def _read_file_with_fallback(path: str) -> str:
    """Read a file trying multiple encodings.

    Tries utf-8 first, then latin-1. If both fail, reads as
    binary with errors='replace'.

    Args:
        path: File path to read.

    Returns:
        File content as string.

    Raises:
        IOError: If the file cannot be opened at all.
    """
    for encoding in _ENCODING_CHAIN:
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # Last resort: binary with replacement characters
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


class CILogAdapter(BaseAdapter):
    """CI log file adapter.

    Reads a local CI failure log file and creates a single
    CanonicalFinding. The LLM parser may later split into
    multiple extracted findings with index suffixes.
    """

    def _do_fetch(self, source_ref: str) -> List[CanonicalFinding]:
        """Read a local CI log file.

        Args:
            source_ref: Local file path (from --ci-file <path>).

        Returns:
            List of CanonicalFinding objects. Empty list on error.
        """
        if not os.path.isfile(source_ref):
            print(
                f"forge: error: CI log file not found: "
                f"{source_ref}",
                file=sys.stderr,
            )
            return []

        try:
            content = _read_file_with_fallback(source_ref)
        except IOError as exc:
            print(
                f"forge: error: failed to read CI log file "
                f"'{source_ref}': {exc}",
                file=sys.stderr,
            )
            return []

        content_hash = hashlib.sha256(
            content[:HASH_PREFIX_LEN].encode('utf-8'),
        ).hexdigest()

        return [CanonicalFinding(
            source='ci_log',
            source_tool='ci',
            source_id=f"{content_hash}-0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_source=content[:MAX_RAW_SOURCE],
            context={
                'diff_hunk': None,
                'ci_output_excerpt': content[:MAX_CONTEXT_EXCERPT],
            },
        )]
