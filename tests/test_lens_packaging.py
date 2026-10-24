# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 7: the third-party notices ship with the package."""

from __future__ import annotations

import tomllib
from pathlib import Path

from code_forge.lenses import LENS_SOURCES

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "src" / "code_forge" / "THIRD_PARTY_NOTICES.md"
NAME = "THIRD_PARTY_NOTICES.md"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_data_lists_the_notices_for_code_forge():
    data = _pyproject()["tool"]["setuptools"]["package-data"]["code_forge"]
    assert NAME in data


def test_notices_carry_every_upstream_copyright_and_the_mit_text():
    text = NOTICES.read_text(encoding="utf-8")
    for holder in ("Copyright (c) 2025 Every", "Copyright (c) 2026 Cursor", "Copyright (c) 2026 Lauren Tan"):
        assert holder in text, holder
    # One full MIT text per upstream licence block.
    assert text.count("MIT License") == 3
    assert text.count("Permission is hereby granted, free of charge") == 3
    assert text.count('THE SOFTWARE IS PROVIDED "AS IS"') == 3


def test_notices_cover_every_lens_source_commit_and_skill():
    text = NOTICES.read_text(encoding="utf-8")
    sources = {s for group in LENS_SOURCES.values() for s in group}
    assert sources
    for skill, _repo, _path, commit in sources:
        assert commit in text, (skill, commit)
        assert skill in text, skill
    # One "Commit:" line per upstream block: Every (1), Cursor and Lauren Tan (2).
    commits = sorted({c for *_rest, c in sources})
    counts = [text.count("- Commit: `%s`" % c) for c in commits]
    assert sorted(counts) == [1, 2]


def test_notices_are_ascii_and_end_with_a_newline():
    raw = NOTICES.read_bytes()
    raw.decode("ascii")
    assert raw.endswith(b"\n")
