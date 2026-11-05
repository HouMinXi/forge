"""Private-file inventory controls, not recorder admission or recovery qualification."""

from dataclasses import asdict
import os
import uuid

import pytest

from code_forge import invocation_audit as audit


RUN = "00000000-0000-4000-8000-000000000001"
MIB = 1024 * 1024
MARKER_BYTES = 16 * 1024


def _rows(version, ordering):
    reservation = {
        "run_bytes": (16 if version == 1 else 64) * MIB,
        "marker_bytes": MARKER_BYTES,
        "root_bytes": 512 * MIB,
        "body_bytes": 256 * 1024,
    }
    if version == 2:
        reservation.update(
            wire_bytes=8 * MIB,
            cli_bytes=256 * 1024,
            decoded_bytes=256 * 1024,
            manifest_bytes=256 * 1024,
        )
    common = {
        "schema_version": version,
        "run_id": RUN,
        "utc": "2026-11-05T00:00:00Z",
        "elapsed_s": 0.0,
    }
    opened = dict(
        common,
        event="run_open",
        context=asdict(
            audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None)
        ),
        capability={
            "profile_id": "linux-local-audit-v1",
            # Synthetic schema fields, never a substituted filesystem admission.
            "filesystem_type": "ext4",
            "root_device": 1,
            "root_inode": 1,
            "root_mount_id": 1,
            "runtime_fingerprint": "a" * 64,
        },
        owner={"pid": 1, "start_ticks": "1", "owner_id": RUN},
        reservation=reservation,
    )
    final = dict(common, event="run_final", outcome="completed", audit_complete=True)
    if ordering == "no_open":
        rows = [final]
    elif ordering == "late_open":
        rows = [final, opened]
    elif ordering == "second_open":
        rows = [opened, dict(opened), final]
    elif ordering == "invalid_open":
        opened["reservation"]["run_bytes"] = 0
        rows = [opened, final]
    else:
        rows = [opened, final]
    return [
        dict(row, sequence=index, event_id=str(uuid.UUID(int=index + 2)))
        for index, row in enumerate(rows)
    ]


@pytest.fixture
def private_run(tmp_path):
    path = tmp_path / RUN
    path.mkdir(mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield path, fd
    finally:
        os.close(fd)


def _journal(path, rows):
    frames = [audit._frame(row) for row in rows]
    data = b"".join(frames)
    journal = path / "journal"
    journal.write_bytes(data)
    journal.chmod(0o600)
    ordinary = sum(
        len(frame) for row, frame in zip(rows, frames, strict=True) if row["event"] != "run_final"
    )
    return data, ordinary, len(data) - ordinary


def _sparse(path, name, size):
    fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.ftruncate(fd, size)
    finally:
        os.close(fd)


@pytest.mark.parametrize("version", (1, 2))
@pytest.mark.parametrize("ordering", ("no_open", "late_open", "second_open", "invalid_open"))
@pytest.mark.parametrize("checkpoint", (False, True), ids=("direct", "checkpoint"))
def test_corrupt_open_preserves_prefix_measurements_and_full_charge(
    private_run, version, ordering, checkpoint
):
    path, fd = private_run
    rows = _rows(version, ordering)
    before, ordinary, marker = _journal(path, rows)
    ticks = []
    result = (
        audit._disk_run(fd, RUN, _checkpoint=lambda: ticks.append(True))
        if checkpoint
        else audit._disk_run(fd, RUN)
    )
    assert result["events"] == (rows[:1] if ordering == "second_open" else [])
    assert result["ordinary"] == ordinary and result["marker"] == marker
    assert result["charge"] == (16 if version == 1 else 64) * MIB
    assert result["schema_invalid"] and result["torn"]
    assert not result["terminal"] and not result["settleable"] and not result["storage_settleable"]
    assert (path / "journal").read_bytes() == before
    # List through a fresh directory description; fd predates journal creation.
    assert set(os.listdir(path)) == {"journal"}
    assert bool(ticks) is checkpoint
    with pytest.raises(audit._Rejected) as failure:
        audit._require_root_version({RUN: result}, version)
    assert failure.value.code == "integrity_error"


@pytest.mark.parametrize("version", (1, 2))
@pytest.mark.parametrize("ordering", ("valid", "no_open"))
@pytest.mark.parametrize("extra", (0, 1), ids=("at-bound", "over-bound"))
def test_run_byte_limit_applies_before_and_after_corrupt_prefix(private_run, version, ordering, extra):
    path, fd = private_run
    _, ordinary, _ = _journal(path, _rows(version, ordering))
    limit = (16 if version == 1 else 64) * MIB
    _sparse(path, "temp-output-" + RUN, limit - MARKER_BYTES - ordinary + extra)
    if extra:
        with pytest.raises(audit._Rejected) as failure:
            audit._disk_run(fd, RUN)
        assert failure.value.code == "integrity_error"
    else:
        result = audit._disk_run(fd, RUN)
        assert result["ordinary"] == limit - MARKER_BYTES and result["charge"] == limit
        assert result["schema_invalid"] is (ordering == "no_open")


@pytest.mark.parametrize("version", (1, 2))
@pytest.mark.parametrize("ordering", ("valid", "no_open"))
@pytest.mark.parametrize("extra", (0, 1), ids=("at-bound", "over-bound"))
def test_marker_limit_is_not_relaxed_by_corrupt_prefix(private_run, version, ordering, extra):
    path, fd = private_run
    _, _, marker = _journal(path, _rows(version, ordering))
    _sparse(path, "temp-final-" + RUN, MARKER_BYTES - marker + extra)
    if extra:
        with pytest.raises(audit._Rejected) as failure:
            audit._disk_run(fd, RUN)
        assert failure.value.code == "integrity_error"
    else:
        result = audit._disk_run(fd, RUN)
        assert result["marker"] == MARKER_BYTES
        assert result["charge"] == (16 if version == 1 else 64) * MIB


def test_missing_journal_keeps_legacy_unversioned_reservation(private_run):
    _, fd = private_run
    result = audit._disk_run(fd, RUN)
    assert result["events"] == [] and result["charge"] == 16 * MIB
    assert result["ordinary"] == result["marker"] == 0
    assert result["torn"] and not result["terminal"] and not result["storage_settleable"]


@pytest.mark.parametrize("first,second", ((1, 2), (2, 1)))
def test_mixed_framing_keeps_first_version_full_charge(private_run, first, second):
    path, fd = private_run
    opened = _rows(first, "valid")[0]
    final = _rows(second, "valid")[1]
    _journal(path, [opened, final])
    result = audit._disk_run(fd, RUN)
    assert result["events"] == [opened]
    assert result["schema_invalid"] and result["torn"] and not result["terminal"]
    assert result["charge"] == (16 if first == 1 else 64) * MIB


@pytest.mark.parametrize("version", (True, 3))
def test_unparsed_version_does_not_select_larger_reservation(private_run, version):
    path, fd = private_run
    rows = _rows(2, "valid")
    rows[0]["schema_version"] = version
    _journal(path, rows)
    result = audit._disk_run(fd, RUN)
    assert result["events"] == [] and result["schema_invalid"] and result["torn"]
    assert result["charge"] == 16 * MIB


@pytest.mark.parametrize("extra", (0, 1), ids=("at-bound", "over-bound"))
def test_corrupt_v2_raw_read_limit_is_unchanged(private_run, extra):
    path, fd = private_run
    _journal(path, _rows(2, "no_open"))
    _sparse(path, "raw-" + RUN, 256 * 1024 + extra)
    if extra:
        with pytest.raises(audit._Rejected) as failure:
            audit._disk_run(fd, RUN)
        assert failure.value.code == "integrity_error"
    else:
        result = audit._disk_run(fd, RUN)
        assert result["schema_invalid"] and result["charge"] == 64 * MIB
        assert not result["terminal"] and not result["storage_settleable"]


@pytest.mark.parametrize("version", (1, 2))
def test_valid_history_limit_helper_still_requires_open(version):
    with pytest.raises(audit._Rejected) as failure:
        audit._events_run_limit(_rows(version, "no_open"))
    assert failure.value.code == "integrity_error"
