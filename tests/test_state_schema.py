# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for forge.state -- schema round-trip, error handling, invariants."""

import json
from dataclasses import fields

import pytest

from code_forge.disposition import Disposition
from code_forge.errors import CorruptedStateError, SchemaVersionMismatchError
from code_forge.state import (
    SCHEMA_VERSION,
    Mode,
    State,
    StateFinding,
    Verdict,
    load_state,
    save_state,
)


def _make_finding(
    fid="f-001",
    fp="fp-001",
    source="L0",
    disp=Disposition.CONFIRMED,
    file_path="src/foo.py",
    line_range=None,
    desc="test finding",
    error=None,
    anchor=None,
    evidence_files=None,
):
    """Helper to construct a StateFinding with defaults."""
    return StateFinding(
        id=fid,
        fingerprint=fp,
        source=source,
        disposition=disp,
        file=file_path,
        line_range=line_range if line_range is not None else [10, 20],
        description=desc,
        error=error,
        anchor=anchor,
        evidence_files=evidence_files,
    )


class TestRoundTrip:
    """(a) save_state + load_state preserves all fields."""

    def test_round_trip_with_all_optional_fields(self, tmp_path):
        """SC-5: non-trivial fixture, 3+ findings, all optionals."""
        findings = [
            _make_finding(
                fid="f-001",
                fp="fp-001",
                source="L0",
                disp=Disposition.CONFIRMED,
                error="falsify raised",
                anchor={"before": "old", "after": "new"},
                evidence_files=["src/bar.py", "src/baz.py"],
            ),
            _make_finding(
                fid="f-002",
                fp="fp-002",
                source="L1",
                disp=Disposition.DISMISSED,
            ),
            _make_finding(
                fid="f-003",
                fp="fp-003",
                source="L0",
                disp=Disposition.UNCERTAIN,
                line_range=[1, 5],
            ),
        ]
        state = State(
            round=2,
            mode=Mode.CI,
            source_hash="abc123",
            findings=findings,
            fix_attempts={"fp-001": 2},
            verdict=Verdict.PENDING,
            converged=False,
        )
        path = tmp_path / "state.json"
        save_state(state, path)
        loaded = load_state(path)

        assert loaded is not None
        assert loaded.schema_version == SCHEMA_VERSION
        assert loaded.round == 2
        assert loaded.mode == Mode.CI
        assert loaded.source_hash == "abc123"
        assert loaded.verdict == Verdict.PENDING
        assert loaded.converged is False
        assert loaded.fix_attempts == {"fp-001": 2}
        assert len(loaded.findings) == 3

        # Verify enum reconstruction
        assert loaded.findings[0].disposition == Disposition.CONFIRMED
        assert loaded.findings[1].disposition == Disposition.DISMISSED
        assert loaded.findings[2].disposition == Disposition.UNCERTAIN

        # Verify optional fields preserved
        assert loaded.findings[0].error == "falsify raised"
        assert loaded.findings[0].anchor == {"before": "old", "after": "new"}
        assert loaded.findings[0].evidence_files == [
            "src/bar.py", "src/baz.py"
        ]
        assert loaded.findings[1].error is None
        assert loaded.findings[1].anchor is None
        assert loaded.findings[1].evidence_files is None

        # Verify line_range stays list[int]
        assert loaded.findings[2].line_range == [1, 5]
        assert isinstance(loaded.findings[2].line_range, list)

        # Verify dispositions cache matches findings
        assert loaded.dispositions == {
            "f-001": Disposition.CONFIRMED,
            "f-002": Disposition.DISMISSED,
            "f-003": Disposition.UNCERTAIN,
        }


class TestMissingFile:
    """(b) Missing file -> load_state returns None (SC-3)."""

    def test_missing_file_returns_none(self, tmp_path):
        path = tmp_path / "nonexistent" / "state.json"
        result = load_state(path)
        assert result is None


class TestCorruptedJson:
    """(c) Corrupted JSON -> CorruptedStateError."""

    def test_corrupted_json_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json")
        with pytest.raises(CorruptedStateError, match="cannot parse"):
            load_state(path)


class TestSchemaMismatch:
    """(d) schema_version mismatch -> SchemaVersionMismatchError."""

    def test_schema_mismatch_raises(self, tmp_path):
        path = tmp_path / "state.json"
        data = {
            "schema_version": 999,
            "disposition_protocol_version": 1,
            "round": 0,
            "mode": "LOCAL",
            "findings": [],
            "dispositions": {},
            "fix_attempts": {},
            "verdict": "PENDING",
            "converged": False,
        }
        path.write_text(json.dumps(data))
        with pytest.raises(
            SchemaVersionMismatchError,
            match="schema_version=999",
        ):
            load_state(path)


class TestDispositionsCacheDrift:
    """(e) Dispositions cache out-of-sync -> CorruptedStateError (H1)."""

    def test_cache_drift_raises(self, tmp_path):
        path = tmp_path / "state.json"
        data = {
            "schema_version": SCHEMA_VERSION,
            "disposition_protocol_version": 1,
            "round": 0,
            "mode": "LOCAL",
            "findings": [
                {
                    "id": "f-001",
                    "fingerprint": "fp-001",
                    "source": "L0",
                    "disposition": "CONFIRMED",
                    "file": "x.py",
                    "line_range": [1, 2],
                    "description": "test",
                }
            ],
            "dispositions": {"f-001": "DISMISSED"},
            "fix_attempts": {},
            "verdict": "PENDING",
            "converged": False,
        }
        path.write_text(json.dumps(data))
        with pytest.raises(
            CorruptedStateError,
            match="dispositions cache out of sync",
        ):
            load_state(path)


class TestSaveRebuildsCacheFromFindings:
    """(f) save_state rebuilds dispositions cache from findings (H1)."""

    def test_manual_mutation_overwritten_on_save(self, tmp_path):
        findings = [
            _make_finding(fid="f-001", disp=Disposition.CONFIRMED),
        ]
        state = State(findings=findings)
        # Manually corrupt the cache
        state.dispositions = {"f-001": Disposition.DISMISSED}

        path = tmp_path / "state.json"
        save_state(state, path)

        loaded = load_state(path)
        assert loaded is not None
        # Cache was rebuilt from findings, not from manual mutation
        assert loaded.dispositions == {"f-001": Disposition.CONFIRMED}


class TestStateFindingVsParsersBaseFinding:
    """(g) Both Finding types importable in same module (B3)."""

    def test_no_import_collision(self):
        from code_forge.parsers.base import Finding  # noqa: F811
        from code_forge.state import StateFinding  # noqa: F811

        assert Finding is not StateFinding
        # Verify they have different fields
        finding_fields = {f.name for f in fields(Finding)}
        state_finding_fields = {f.name for f in fields(StateFinding)}
        assert finding_fields != state_finding_fields


class TestStateDefaultInstantiation:
    """SC-10: State dataclass instantiates with no arguments."""

    def test_no_args_instantiation(self):
        s = State()
        assert s.schema_version == SCHEMA_VERSION
        assert s.round == 0
        assert s.mode == Mode.LOCAL
        assert s.findings == []
        assert s.dispositions == {}
        assert s.fix_attempts == {}
        assert s.verdict == Verdict.PENDING
        assert s.converged is False


class TestAutoCreateDirectory:
    """SC-4: save_state creates .code-forge/ directory if it does not exist."""

    def test_auto_create_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "state.json"
        assert not path.parent.exists()
        save_state(State(), path)
        assert path.exists()


class TestStateFindingFieldCount:
    """Guard: field-count assertion catches drift in _finding_from_dict."""

    def test_field_count(self):
        expected = 11  # id, fingerprint, source, disposition, file,
        # line_range, description, error, anchor, evidence_files, is_timeout
        assert len(fields(StateFinding)) == expected


class TestConsecutiveSurvivorRounds:
    """Test 17-18: consecutive_survivor_rounds serialization round-trip."""

    def test_save_state_includes_consecutive_survivor_rounds(self, tmp_path):
        """Test 17: save_state includes consecutive_survivor_rounds in JSON output."""
        state = State()
        state.consecutive_survivor_rounds = 2

        path = tmp_path / "state.json"
        save_state(state, path)

        data = json.loads(path.read_text())
        assert "consecutive_survivor_rounds" in data
        assert data["consecutive_survivor_rounds"] == 2

    def test_load_state_reads_consecutive_survivor_rounds(self, tmp_path):
        """Test 18: load_state reads consecutive_survivor_rounds; defaults to 0 for old state files."""
        # Case 1: new state file with consecutive_survivor_rounds
        state = State()
        state.consecutive_survivor_rounds = 3
        path = tmp_path / "state.json"
        save_state(state, path)

        loaded = load_state(path)
        assert loaded is not None
        assert loaded.consecutive_survivor_rounds == 3

        # Case 2: old state file without consecutive_survivor_rounds
        data = json.loads(path.read_text())
        del data["consecutive_survivor_rounds"]
        path.write_text(json.dumps(data))

        loaded = load_state(path)
        assert loaded is not None
        assert loaded.consecutive_survivor_rounds == 0
