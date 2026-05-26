# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""source_hash computation per STATE-07.

Whitespace normalization: trailing-ws strip + LF line endings.
H1/H3 fixes applied: binary files hashed as raw bytes (preserves invalidation
correctness); path serialization uses as_posix() for cross-platform determinism.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


def normalize_text(text: str) -> str:
    """Strip trailing whitespace per line; force LF endings.

    No trailing blank line stripping.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines)


def compute_source_hash(
    *,
    git_diff: Optional[str] = None,
    files: Optional[list[Path]] = None,
) -> str:
    """STATE-07 source_hash.

    Git mode: caller passes git_diff (unified diff output).
    Non-git mode: caller passes files. Files are sorted by posix path
    string for cross-platform deterministic ordering (H3). Binary files
    (UnicodeDecodeError on utf-8 read) are hashed as raw bytes with a
    binary marker (H1) -- this preserves invalidation correctness for
    binary edits and keeps source_hash stable.

    Exactly one of git_diff / files must be provided. Returns lowercase
    hex SHA256.
    """
    if (git_diff is None) == (files is None):
        raise ValueError(
            "compute_source_hash: pass exactly one of git_diff or files"
        )

    h = hashlib.sha256()
    if git_diff is not None:
        h.update(b"mode=git\n")
        h.update(normalize_text(git_diff).encode("utf-8"))
        return h.hexdigest()

    h.update(b"mode=non-git\n")
    for f in sorted(files, key=lambda p: p.as_posix()):
        try:
            content = f.read_text(encoding="utf-8")
            h.update(("--- %s text\n" % f.as_posix()).encode("utf-8"))
            h.update(normalize_text(content).encode("utf-8"))
        except UnicodeDecodeError:
            # H1: binary file -- hash raw bytes
            h.update(("--- %s binary\n" % f.as_posix()).encode("utf-8"))
            h.update(f.read_bytes())
        h.update(b"\n")
    return h.hexdigest()
