# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Patch-corpus JSON parsing and validation.

Specification contract (spec lines 373-396):
  - Schema version 1
  - Entries with id, source, source_digest, old, new, operator, test_selector
  - Exact old-byte occurrence count must be one
  - Source digest is the reviewed post-change unmutated file
  - Stale digest produces hold, not silent acceptance
  - Identifier and selector lengths are bounded
  - Duplicate or unknown fields are errors
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .schemas import valid_identifier


# Bounds for corpus fields.
# [INFERRED] The spec says "bounded by the plan's schema" but gives
# no explicit numbers.  These are generous practical limits.
MAX_CORPUS_ENTRIES = 4096
MAX_IDENTIFIER_LEN = 64
MAX_SOURCE_PATH_LEN = 1024
MAX_SELECTOR_LEN = 2048
MAX_OPERATOR_LEN = 128
MAX_OLD_NEW_BYTES = 1024 * 1024  # 1 MiB per old/new field
MAX_DIGEST_LEN = 128

# All required keys per entry, in order
_ENTRY_REQUIRED_KEYS = frozenset({
    "id", "source", "source_digest", "old", "new",
    "operator", "test_selector",
})


class CorpusError(Exception):
    """Raised when a corpus file is invalid."""


class StaleDigestError(CorpusError):
    """Raised when a corpus entry's source_digest does not match the file."""


@dataclass(frozen=True)
class CorpusEntry:
    """A single corpus entry from the corpus JSON file."""
    id: str
    source: str
    source_digest: str
    old: str
    new: str
    operator: str
    test_selector: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source": self.source,
            "source_digest": self.source_digest,
            "old": self.old,
            "new": self.new,
            "operator": self.operator,
            "test_selector": self.test_selector,
        }


@dataclass(frozen=True)
class Corpus:
    """A parsed and validated corpus file."""
    schema_version: int
    target_id: str
    entries: tuple[CorpusEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_id": self.target_id,
            "entries": [e.to_dict() for e in self.entries],
        }


def _validate_entry(idx: int, raw: dict[str, Any]) -> CorpusEntry:
    """Validate and construct a single CorpusEntry from a raw dict."""
    if not isinstance(raw, dict):
        raise CorpusError(
            "entry[%d]: must be a mapping, got %s" % (idx, type(raw).__name__)
        )

    # Check for missing / unknown keys
    missing = _ENTRY_REQUIRED_KEYS - raw.keys()
    if missing:
        raise CorpusError(
            "entry[%d]: missing required keys: %s"
            % (idx, ", ".join(sorted(missing)))
        )
    extra = set(raw.keys()) - _ENTRY_REQUIRED_KEYS
    if extra:
        raise CorpusError(
            "entry[%d]: unknown keys: %s" % (idx, ", ".join(sorted(extra)))
        )

    # Type and length checks
    eid = raw["id"]
    if not isinstance(eid, str):
        raise CorpusError(
            "entry[%d]: id must be a string, got %s"
            % (idx, type(eid).__name__)
        )
    if not valid_identifier(eid):
        raise CorpusError(
            "entry[%d]: id must match [a-z][a-z0-9_-]{0,63}, got %r"
            % (idx, eid)
        )

    source = raw["source"]
    if not isinstance(source, str) or not source:
        raise CorpusError("entry[%d]: source must be a nonempty string" % idx)
    if len(source) > MAX_SOURCE_PATH_LEN:
        raise CorpusError(
            "entry[%d]: source path exceeds %d chars" % (idx, MAX_SOURCE_PATH_LEN)
        )
    # Validate as a relative path (no absolute, no traversal)
    if source.startswith("/"):
        raise CorpusError("entry[%d]: source must be relative, got %r" % (idx, source))
    if ".." in source.split("/"):
        raise CorpusError(
            "entry[%d]: source must not contain traversal (..), got %r"
            % (idx, source)
        )

    source_digest = raw["source_digest"]
    if not isinstance(source_digest, str) or not source_digest:
        raise CorpusError(
            "entry[%d]: source_digest must be a nonempty string" % idx
        )
    if len(source_digest) > MAX_DIGEST_LEN:
        raise CorpusError(
            "entry[%d]: source_digest exceeds %d chars" % (idx, MAX_DIGEST_LEN)
        )

    old = raw["old"]
    if not isinstance(old, str):
        raise CorpusError(
            "entry[%d]: old must be a string, got %s" % (idx, type(old).__name__)
        )
    if not old:
        raise CorpusError("entry[%d]: old must be nonempty" % idx)
    if len(old.encode("utf-8")) > MAX_OLD_NEW_BYTES:
        raise CorpusError(
            "entry[%d]: old exceeds %d bytes" % (idx, MAX_OLD_NEW_BYTES)
        )

    new = raw["new"]
    if not isinstance(new, str):
        raise CorpusError(
            "entry[%d]: new must be a string, got %s" % (idx, type(new).__name__)
        )
    if len(new.encode("utf-8")) > MAX_OLD_NEW_BYTES:
        raise CorpusError(
            "entry[%d]: new exceeds %d bytes" % (idx, MAX_OLD_NEW_BYTES)
        )

    operator = raw["operator"]
    if not isinstance(operator, str) or not operator:
        raise CorpusError(
            "entry[%d]: operator must be a nonempty string" % idx
        )
    if len(operator) > MAX_OPERATOR_LEN:
        raise CorpusError(
            "entry[%d]: operator exceeds %d chars" % (idx, MAX_OPERATOR_LEN)
        )

    test_selector = raw["test_selector"]
    if not isinstance(test_selector, str) or not test_selector:
        raise CorpusError(
            "entry[%d]: test_selector must be a nonempty string" % idx
        )
    if len(test_selector) > MAX_SELECTOR_LEN:
        raise CorpusError(
            "entry[%d]: test_selector exceeds %d chars" % (idx, MAX_SELECTOR_LEN)
        )

    return CorpusEntry(
        id=eid,
        source=source,
        source_digest=source_digest,
        old=old,
        new=new,
        operator=operator,
        test_selector=test_selector,
    )


def load_corpus(data: str | bytes) -> Corpus:
    """Parse and validate a corpus JSON file.

    *data* is the raw file content (str or bytes).
    Returns a validated ``Corpus``.
    Raises ``CorpusError`` on any validation failure.
    """
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CorpusError("corpus file is not valid UTF-8: %s" % exc) from exc
    else:
        text = data

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusError("corpus file is not valid JSON: %s" % exc) from exc

    if not isinstance(obj, dict):
        raise CorpusError(
            "corpus root must be a mapping, got %s" % type(obj).__name__
        )

    # Validate top-level keys
    allowed_top = {"schema_version", "target_id", "entries"}
    missing = allowed_top - obj.keys()
    if missing:
        raise CorpusError(
            "corpus missing required keys: %s" % ", ".join(sorted(missing))
        )
    extra = set(obj.keys()) - allowed_top
    if extra:
        raise CorpusError(
            "corpus has unknown keys: %s" % ", ".join(sorted(extra))
        )

    sv = obj["schema_version"]
    if sv != 1:
        raise CorpusError("corpus schema_version must be 1, got %r" % sv)

    target_id = obj["target_id"]
    if not isinstance(target_id, str):
        raise CorpusError(
            "corpus target_id must be a string, got %s"
            % type(target_id).__name__
        )
    if not valid_identifier(target_id):
        raise CorpusError(
            "corpus target_id must match [a-z][a-z0-9_-]{0,63}, got %r"
            % target_id
        )

    raw_entries = obj["entries"]
    if not isinstance(raw_entries, list):
        raise CorpusError(
            "corpus entries must be a list, got %s"
            % type(raw_entries).__name__
        )
    if len(raw_entries) > MAX_CORPUS_ENTRIES:
        raise CorpusError(
            "corpus has %d entries, exceeding maximum %d"
            % (len(raw_entries), MAX_CORPUS_ENTRIES)
        )

    entries: list[CorpusEntry] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_entries):
        entry = _validate_entry(i, raw)
        if entry.id in seen_ids:
            raise CorpusError("duplicate entry id: %r" % entry.id)
        seen_ids.add(entry.id)
        entries.append(entry)

    return Corpus(
        schema_version=sv,
        target_id=target_id,
        entries=tuple(entries),
    )


def check_old_byte_occurrence(source_content: bytes, entry: CorpusEntry) -> int:
    """Count occurrences of *entry.old* (as UTF-8 bytes) in *source_content*.

    The specification requires exactly one occurrence.  Returns the count.
    """
    old_bytes = entry.old.encode("utf-8")
    return source_content.count(old_bytes)


def compute_source_digest(source_content: bytes) -> str:
    """Compute the SHA-256 hex digest of *source_content*."""
    return hashlib.sha256(source_content).hexdigest()


def validate_corpus_against_sources(
    corpus: Corpus,
    source_reader: dict[str, bytes],
) -> list[str]:
    """Validate a corpus against actual source file contents.

    *source_reader* maps relative source paths to their raw bytes.

    Returns a list of error messages.  An empty list means success.
    Errors include:
      - source file not found
      - stale digest (mismatch between entry.source_digest and actual)
      - old text not found or found more than once
    """
    errors: list[str] = []

    for entry in corpus.entries:
        content = source_reader.get(entry.source)
        if content is None:
            errors.append(
                "entry %r: source %r not found" % (entry.id, entry.source)
            )
            continue

        actual_digest = compute_source_digest(content)
        if actual_digest != entry.source_digest:
            errors.append(
                "entry %r: source_digest mismatch for %r "
                "(expected %s, got %s) -- stale digest hold"
                % (entry.id, entry.source, entry.source_digest, actual_digest)
            )
            continue

        count = check_old_byte_occurrence(content, entry)
        if count == 0:
            errors.append(
                "entry %r: old text not found in %r" % (entry.id, entry.source)
            )
        elif count > 1:
            errors.append(
                "entry %r: old text found %d times in %r (must be exactly 1)"
                % (entry.id, count, entry.source)
            )

    return errors
