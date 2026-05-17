# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for snapshot persistence and invalidation."""

import json

import pytest

from forge.errors import (
    BaselineResolutionError,
    CorruptedSnapshotError,
    SnapshotSchemaMismatchError,
)
from forge.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    Snapshot,
    SnapshotEntry,
    _hash_file,
    find_existing_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_path_for,
    validate_snapshot,
)


class TestSnapshotPathFor:
    def test_standard_location(self, tmp_path):
        p = snapshot_path_for("abc123", tmp_path)
        assert p == tmp_path / ".forge" / "snapshots" / "abc123.json"


class TestSaveLoadRoundTrip:
    """SC-7, SC-15, SC-16."""

    def test_save_load_roundtrip(self, tmp_path):
        """SC-7: save then load returns equivalent snapshot."""
        snap = Snapshot(
            source_hash="deadbeef",
            files=[
                SnapshotEntry(path="a.py", content_hash="aaa"),
                SnapshotEntry(path="b.py", content_hash="bbb"),
            ],
            finding_dispositions={"fp1": "ACCEPT", "fp2": "FIX"},
        )
        path = snapshot_path_for("deadbeef", tmp_path)
        save_snapshot(snap, path)
        loaded = load_snapshot(path)
        assert loaded is not None
        assert loaded.source_hash == "deadbeef"
        assert len(loaded.files) == 2
        assert loaded.files[0].path == "a.py"
        assert loaded.files[0].content_hash == "aaa"
        assert loaded.finding_dispositions == {"fp1": "ACCEPT", "fp2": "FIX"}

    def test_missing_file_returns_none(self, tmp_path):
        """SC-15: missing snapshot file -> None."""
        path = tmp_path / "nonexistent.json"
        assert load_snapshot(path) is None

    def test_corrupted_json_raises(self, tmp_path):
        """Corrupted JSON raises CorruptedSnapshotError."""
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        with pytest.raises(CorruptedSnapshotError, match="cannot parse"):
            load_snapshot(path)

    def test_schema_mismatch_raises(self, tmp_path):
        """SC-16: schema_version mismatch raises error."""
        path = tmp_path / "old.json"
        data = {
            "schema_version": 999,
            "source_hash": "abc",
            "files": [],
            "finding_dispositions": {},
        }
        path.write_text(json.dumps(data))
        with pytest.raises(
            SnapshotSchemaMismatchError, match="schema_version=999"
        ):
            load_snapshot(path)

    def test_missing_source_hash_raises(self, tmp_path):
        """Missing source_hash -> CorruptedSnapshotError (not KeyError)."""
        path = tmp_path / "nosource.json"
        data = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "files": [],
            "finding_dispositions": {},
        }
        path.write_text(json.dumps(data))
        with pytest.raises(CorruptedSnapshotError, match="invalid snapshot"):
            load_snapshot(path)

    def test_bad_entry_fields_raises(self, tmp_path):
        """Malformed file entry -> CorruptedSnapshotError (not TypeError)."""
        path = tmp_path / "badentry.json"
        data = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "source_hash": "abc",
            "files": [{"path": "a.py"}],
            "finding_dispositions": {},
        }
        path.write_text(json.dumps(data))
        with pytest.raises(CorruptedSnapshotError, match="invalid snapshot"):
            load_snapshot(path)

    def test_schema_version_independent(self):
        """SC-16: SNAPSHOT_SCHEMA_VERSION is independent constant."""
        from forge.state import SCHEMA_VERSION as STATE_SV

        # They may happen to be equal but are independent constants
        assert isinstance(SNAPSHOT_SCHEMA_VERSION, int)
        assert isinstance(STATE_SV, int)


class TestFindExistingSnapshot:
    """SC-14 / H5."""

    def test_returns_none_for_missing(self, tmp_path):
        assert find_existing_snapshot("nonexistent", tmp_path) is None

    def test_returns_path_for_present(self, tmp_path):
        snap = Snapshot(source_hash="abc")
        path = snapshot_path_for("abc", tmp_path)
        save_snapshot(snap, path)
        result = find_existing_snapshot("abc", tmp_path)
        assert result is not None
        assert result == path


class TestValidateSnapshot:
    """SC-12, SC-13."""

    @pytest.fixture
    def initial_snapshot(self, tmp_path):
        """Create a snapshot from initial_set fixture files."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "a.py").write_text('print("a")\n')
        (root / "b.py").write_text('print("b")\n')
        (root / "c.py").write_text('print("c")\n')

        files = [root / "a.py", root / "b.py", root / "c.py"]
        snap = Snapshot(
            source_hash="initial",
            files=[
                SnapshotEntry(path=f.relative_to(root).as_posix(),
                              content_hash=_hash_file(f))
                for f in files
            ],
        )
        return snap, root

    def test_all_unchanged(self, initial_snapshot):
        """SC-12: all files unchanged."""
        snap, root = initial_snapshot
        files = [root / "a.py", root / "b.py", root / "c.py"]
        result = validate_snapshot(snap, files, root)
        assert result.unchanged == ["a.py", "b.py", "c.py"]
        assert result.missing == []
        assert result.changed == []
        assert result.added == []

    def test_one_modified(self, initial_snapshot):
        """SC-12: one file modified (b.py)."""
        snap, root = initial_snapshot
        (root / "b.py").write_text('print("b modified")\n')
        files = [root / "a.py", root / "b.py", root / "c.py"]
        result = validate_snapshot(snap, files, root)
        assert result.unchanged == ["a.py", "c.py"]
        assert result.changed == ["b.py"]
        assert result.missing == []
        assert result.added == []

    def test_one_removed(self, initial_snapshot):
        """SC-12: one file removed (b.py)."""
        snap, root = initial_snapshot
        files = [root / "a.py", root / "c.py"]
        result = validate_snapshot(snap, files, root)
        assert result.unchanged == ["a.py", "c.py"]
        assert result.missing == ["b.py"]
        assert result.changed == []
        assert result.added == []

    def test_one_added(self, initial_snapshot):
        """SC-12: one file added (d.py)."""
        snap, root = initial_snapshot
        (root / "d.py").write_text('print("d")\n')
        files = [
            root / "a.py", root / "b.py",
            root / "c.py", root / "d.py",
        ]
        result = validate_snapshot(snap, files, root)
        assert result.unchanged == ["a.py", "b.py", "c.py"]
        assert result.added == ["d.py"]
        assert result.missing == []
        assert result.changed == []

    def test_path_outside_root_raises(self, initial_snapshot):
        """SC-13 / H6: file outside root raises BaselineResolutionError."""
        snap, root = initial_snapshot
        outside_file = root.parent / "outside.py"
        outside_file.write_text("outside")
        with pytest.raises(
            BaselineResolutionError, match="outside snapshot root"
        ):
            validate_snapshot(snap, [outside_file], root)

    def test_binary_hash_invalidation(self, tmp_path):
        """H1: binary file content change -> different hash."""
        root = tmp_path / "binproject"
        root.mkdir()
        f = root / "data.bin"
        f.write_bytes(b"\x00\x01\x02")
        h1 = _hash_file(f)
        f.write_bytes(b"\x00\x01\x03")
        h2 = _hash_file(f)
        assert h1 != h2
