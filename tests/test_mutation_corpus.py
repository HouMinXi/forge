# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the mutation_engines.corpus module.

Covers: corpus JSON parsing, schema validation, single exact old-byte
occurrence, digest binding, stale-digest hold, duplicate entry ids,
unknown fields, bounded identifiers.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from code_forge.mutation_engines.corpus import (
    MAX_CORPUS_ENTRIES,
    MAX_SELECTOR_LEN,
    MAX_SOURCE_PATH_LEN,
    CorpusEntry,
    CorpusError,
    check_old_byte_occurrence,
    compute_source_digest,
    load_corpus,
    validate_corpus_against_sources,
)


# -- Helpers ------------------------------------------------------------------

def _valid_entry(**overrides):
    d = {
        "id": "reject-empty-ref",
        "source": "scripts/check-ref.sh",
        "source_digest": "a" * 64,
        "old": 'if [ -z "$ref" ]; then',
        "new": '# removed guard',
        "operator": "guard-removal",
        "test_selector": "tests/test_scripts.py::test_rejects_empty_ref",
    }
    d.update(overrides)
    return d


def _valid_corpus_dict(**overrides):
    d = {
        "schema_version": 1,
        "target_id": "shell-config",
        "entries": [_valid_entry()],
    }
    d.update(overrides)
    return d


def _corpus_json(**overrides):
    return json.dumps(_valid_corpus_dict(**overrides))


# -- load_corpus --------------------------------------------------------------

class TestLoadCorpus:
    def test_valid_corpus_round_trip(self):
        corpus = load_corpus(_corpus_json())
        assert corpus.schema_version == 1
        assert corpus.target_id == "shell-config"
        assert len(corpus.entries) == 1

        # Round-trip
        d = corpus.to_dict()
        reparsed = load_corpus(json.dumps(d))
        assert reparsed.to_dict() == d

    def test_entry_field_values(self):
        corpus = load_corpus(_corpus_json())
        entry = corpus.entries[0]
        assert entry.id == "reject-empty-ref"
        assert entry.source == "scripts/check-ref.sh"
        assert entry.old == 'if [ -z "$ref" ]; then'
        assert entry.operator == "guard-removal"

    def test_bytes_input(self):
        data = _corpus_json().encode("utf-8")
        corpus = load_corpus(data)
        assert len(corpus.entries) == 1

    def test_reject_invalid_json(self):
        with pytest.raises(CorpusError, match="not valid JSON"):
            load_corpus("{not json")

    def test_reject_binary_input(self):
        with pytest.raises(CorpusError, match="not valid UTF-8"):
            load_corpus(b"\x80\x81\x82")

    def test_reject_non_dict_root(self):
        with pytest.raises(CorpusError, match="root must be a mapping"):
            load_corpus(json.dumps([1, 2, 3]))

    def test_reject_missing_schema_version(self):
        d = _valid_corpus_dict()
        del d["schema_version"]
        with pytest.raises(CorpusError, match="missing required keys"):
            load_corpus(json.dumps(d))

    def test_reject_wrong_schema_version(self):
        with pytest.raises(CorpusError, match="schema_version must be 1"):
            load_corpus(_corpus_json(schema_version=2))

    def test_reject_missing_target_id(self):
        d = _valid_corpus_dict()
        del d["target_id"]
        with pytest.raises(CorpusError, match="missing required keys"):
            load_corpus(json.dumps(d))

    def test_reject_invalid_target_id(self):
        with pytest.raises(CorpusError, match="target_id must match"):
            load_corpus(_corpus_json(target_id="Bad-Id"))

    def test_reject_missing_entries(self):
        d = _valid_corpus_dict()
        del d["entries"]
        with pytest.raises(CorpusError, match="missing required keys"):
            load_corpus(json.dumps(d))

    def test_reject_non_list_entries(self):
        with pytest.raises(CorpusError, match="entries must be a list"):
            load_corpus(_corpus_json(entries="not-a-list"))

    def test_reject_unknown_top_level_keys(self):
        d = _valid_corpus_dict()
        d["extra_key"] = "surprise"
        with pytest.raises(CorpusError, match="unknown keys"):
            load_corpus(json.dumps(d))

    def test_reject_too_many_entries(self):
        entries = [
            _valid_entry(id="e%04d" % i) for i in range(MAX_CORPUS_ENTRIES + 1)
        ]
        with pytest.raises(CorpusError, match="exceeding maximum"):
            load_corpus(_corpus_json(entries=entries))

    def test_reject_duplicate_entry_ids(self):
        entries = [_valid_entry(id="same-id"), _valid_entry(id="same-id")]
        with pytest.raises(CorpusError, match="duplicate entry id"):
            load_corpus(_corpus_json(entries=entries))

    def test_reject_unknown_entry_keys(self):
        entry = _valid_entry()
        entry["extra_field"] = "x"
        with pytest.raises(CorpusError, match="unknown keys"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_missing_entry_keys(self):
        entry = _valid_entry()
        del entry["old"]
        with pytest.raises(CorpusError, match="missing required keys"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_invalid_entry_id(self):
        entry = _valid_entry(id="Bad Id!")
        with pytest.raises(CorpusError, match="id must match"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_empty_source(self):
        entry = _valid_entry(source="")
        with pytest.raises(CorpusError, match="source must be a nonempty"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_absolute_source(self):
        entry = _valid_entry(source="/etc/passwd")
        with pytest.raises(CorpusError, match="source must be relative"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_traversal_in_source(self):
        entry = _valid_entry(source="../outside/file.sh")
        with pytest.raises(CorpusError, match="traversal"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_backslash_in_source(self):
        entry = _valid_entry(source=r"..\outside\file.sh")
        with pytest.raises(CorpusError, match="backslash"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_empty_old(self):
        entry = _valid_entry(old="")
        with pytest.raises(CorpusError, match="old must be nonempty"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_empty_operator(self):
        entry = _valid_entry(operator="")
        with pytest.raises(CorpusError, match="operator must be a nonempty"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_empty_test_selector(self):
        entry = _valid_entry(test_selector="")
        with pytest.raises(CorpusError, match="test_selector must be a nonempty"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_oversized_source_path(self):
        entry = _valid_entry(source="a/" * (MAX_SOURCE_PATH_LEN // 2 + 1) + "f")
        with pytest.raises(CorpusError, match="source path exceeds"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_oversized_test_selector(self):
        entry = _valid_entry(test_selector="x" * (MAX_SELECTOR_LEN + 1))
        with pytest.raises(CorpusError, match="test_selector exceeds"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_non_string_id(self):
        entry = _valid_entry()
        entry["id"] = 42
        with pytest.raises(CorpusError, match="id must be a string"):
            load_corpus(_corpus_json(entries=[entry]))

    def test_reject_non_dict_entry(self):
        with pytest.raises(CorpusError, match="must be a mapping"):
            load_corpus(_corpus_json(entries=["not-a-dict"]))

    def test_reject_non_string_target_id(self):
        with pytest.raises(CorpusError, match="target_id must be a string"):
            load_corpus(_corpus_json(target_id=42))


# -- check_old_byte_occurrence ------------------------------------------------

class TestOldByteOccurrence:
    def test_exactly_one_occurrence(self):
        source = b'if [ -z "$ref" ]; then\n  echo "empty"\nfi\n'
        entry = CorpusEntry(
            id="e1", source="s.sh",
            source_digest="d",
            old='if [ -z "$ref" ]; then',
            new="# removed",
            operator="guard-removal",
            test_selector="t::test",
        )
        assert check_old_byte_occurrence(source, entry) == 1

    def test_zero_occurrences(self):
        source = b"some other content\n"
        entry = CorpusEntry(
            id="e1", source="s.sh",
            source_digest="d",
            old="not-present",
            new="replacement",
            operator="op",
            test_selector="t::test",
        )
        assert check_old_byte_occurrence(source, entry) == 0

    def test_multiple_occurrences(self):
        source = b"hello world hello world\n"
        entry = CorpusEntry(
            id="e1", source="s.sh",
            source_digest="d",
            old="hello",
            new="bye",
            operator="op",
            test_selector="t::test",
        )
        assert check_old_byte_occurrence(source, entry) == 2


# -- validate_corpus_against_sources ------------------------------------------

class TestValidateCorpusAgainstSources:
    def _source_content(self):
        return b'if [ -z "$ref" ]; then\n  exit 1\nfi\n'

    def _matching_digest(self, content):
        return hashlib.sha256(content).hexdigest()

    def test_valid_corpus(self):
        content = self._source_content()
        digest = self._matching_digest(content)
        corpus = load_corpus(_corpus_json(
            entries=[_valid_entry(source_digest=digest)]
        ))
        errors = validate_corpus_against_sources(
            corpus,
            {"scripts/check-ref.sh": content},
        )
        assert errors == []

    def test_missing_source_file(self):
        corpus = load_corpus(_corpus_json())
        errors = validate_corpus_against_sources(corpus, {})
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_stale_digest(self):
        content = self._source_content()
        corpus = load_corpus(_corpus_json(
            entries=[_valid_entry(source_digest="0" * 64)]
        ))
        errors = validate_corpus_against_sources(
            corpus,
            {"scripts/check-ref.sh": content},
        )
        assert len(errors) == 1
        assert "stale digest" in errors[0]

    def test_old_text_not_found(self):
        content = b"no match here\n"
        digest = self._matching_digest(content)
        corpus = load_corpus(_corpus_json(
            entries=[_valid_entry(source_digest=digest)]
        ))
        errors = validate_corpus_against_sources(
            corpus,
            {"scripts/check-ref.sh": content},
        )
        assert len(errors) == 1
        assert "old text not found" in errors[0]

    def test_multiple_old_occurrences(self):
        old_text = 'if [ -z "$ref" ]; then'
        content = (old_text + "\n" + old_text + "\n").encode("utf-8")
        digest = self._matching_digest(content)
        corpus = load_corpus(_corpus_json(
            entries=[_valid_entry(source_digest=digest)]
        ))
        errors = validate_corpus_against_sources(
            corpus,
            {"scripts/check-ref.sh": content},
        )
        assert len(errors) == 1
        assert "found 2 times" in errors[0]


# -- compute_source_digest ----------------------------------------------------

class TestComputeSourceDigest:
    def test_matches_hashlib(self):
        data = b"hello world\n"
        assert compute_source_digest(data) == hashlib.sha256(data).hexdigest()

    def test_empty_content(self):
        assert compute_source_digest(b"") == hashlib.sha256(b"").hexdigest()
