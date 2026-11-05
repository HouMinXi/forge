"""Pure persisted-policy compatibility checks, not real-storage qualification."""

from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import json

import pytest

from code_forge import invocation_audit as audit


RUN = "00000000-0000-4000-8000-000000000001"
OWNER = {"pid": 1, "start_ticks": "1", "owner_id": RUN}
START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _utc(seconds=0):
    return (START + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _event(version=1, *, sequence=0):
    return {
        "schema_version": version,
        "run_id": RUN,
        "event_id": f"00000000-0000-4000-8000-{sequence + 2:012d}",
        "sequence": sequence,
        "event": "run_open" if sequence == 0 else "run_final",
        "utc": _utc(),
        "elapsed_s": 0.0,
        **(
            {"context": {}, "capability": {}, "owner": OWNER, "reservation": {}}
            if sequence == 0
            else {"outcome": "completed", "audit_complete": True}
        ),
    }


def _valid_open(version):
    context = audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None)
    return dict(
        _event(version),
        context=asdict(context),
        capability={
            "profile_id": "linux-local-audit-v1",
            "filesystem_type": "ext4",
            "root_device": 1,
            "root_inode": 1,
            "root_mount_id": 1,
            "runtime_fingerprint": "a" * 64,
        },
        reservation=audit._reservation_policy(version),
    )


@pytest.mark.parametrize("version", [1, 2])
def test_semantic_empty_terminal_run_and_all_markers_share_version(version):
    opened, final = _valid_open(version), _event(version, sequence=1)
    rows, _, _, torn, invalid = audit._read_events(audit._frame(opened) + audit._frame(final), RUN)
    assert not torn and not invalid
    assert audit._fold(rows)[3] == final
    markers = {
        "admission": json.loads(audit._marker(opened, OWNER)),
        "final": json.loads(audit._marker(final, OWNER, terminal="completed")),
    }
    audit._marker_projections(markers, rows)
    assert all(marker["schema_version"] == version for marker in markers.values())


@pytest.mark.parametrize("version", [1, 2])
def test_writer_event_selects_explicit_version_without_opening_storage(version, tmp_path):
    recorder = audit.AttemptRecorder(
        tmp_path,
        context=audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None),
        monotonic=lambda: 0.0,
        utc_now=_utc,
        audit_schema_version=version,
    )
    assert recorder._construction_fault is None
    event = recorder._event("run_final", outcome="completed", audit_complete=True)
    assert event["schema_version"] == version


@pytest.mark.parametrize("version,days", [(1, 7), (2, 3)])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_tombstone_eligibility_uses_persisted_version(version, days, offset):
    opened, final = _event(version), _event(version, sequence=1)
    tomb = dict(final, utc=_utc(days * 86400 + offset), event_id=RUN)
    marker = json.loads(audit._marker(tomb, OWNER, terminal="completed", tombstone=True))
    if offset > 0:
        audit._marker_projections({"tombstone": marker}, [opened, final])
    else:
        with pytest.raises(audit._Rejected):
            audit._marker_projections({"tombstone": marker}, [opened, final])


def test_four_day_tombstone_does_not_relax_legacy_policy():
    for version in (1, 2):
        opened, final = _event(version), _event(version, sequence=1)
        tomb = dict(final, utc=_utc(4 * 86400), event_id=RUN)
        marker = json.loads(audit._marker(tomb, OWNER, terminal="completed", tombstone=True))
        if version == 1:
            with pytest.raises(audit._Rejected):
                audit._marker_projections({"tombstone": marker}, [opened, final])
        else:
            audit._marker_projections({"tombstone": marker}, [opened, final])


def test_marker_version_cannot_override_run_policy():
    opened, final = _event(1), _event(1, sequence=1)
    tomb = dict(final, schema_version=2, utc=_utc(4 * 86400), event_id=RUN)
    marker = json.loads(audit._marker(tomb, OWNER, terminal="completed", tombstone=True))
    with pytest.raises(audit._Rejected):
        audit._marker_projections({"tombstone": marker}, [opened, final])


@pytest.mark.parametrize("version", [1, 2])
def test_framing_accepts_each_supported_version_without_semantic_claim(version):
    rows, _, _, torn, invalid = audit._read_events(audit._frame(_event(version)), RUN)
    assert len(rows) == 1
    assert not torn and not invalid
    # The empty nested projections intentionally do not qualify as a complete run.
    with pytest.raises(audit._Rejected):
        audit._fold(rows)


@pytest.mark.parametrize("version", [True, False, 0, 3, "2", None])
def test_unknown_or_coerced_version_is_not_accepted(version):
    rows, _, _, torn, invalid = audit._read_events(audit._frame(_event(version)), RUN)
    assert not rows and torn and invalid
    with pytest.raises(audit._Rejected):
        audit._retention_seconds(version)


@pytest.mark.parametrize("first,second", [(1, 2), (2, 1)])
def test_mixed_journal_versions_preserve_only_valid_prefix(first, second):
    data = audit._frame(_event(first)) + audit._frame(_event(second, sequence=1))
    rows, _, _, torn, invalid = audit._read_events(data, RUN)
    assert rows == [_event(first)]
    assert torn and invalid


@pytest.mark.parametrize("existing", [1, 2])
@pytest.mark.parametrize("requested", [1, 2])
def test_root_admission_never_mixes_client_formats(existing, requested):
    rows = {RUN: {"metadata": {"admission": {"schema_version": existing}}}}
    if existing == requested:
        audit._require_root_version(rows, requested)
    else:
        with pytest.raises(audit._Rejected) as caught:
            audit._require_root_version(rows, requested)
        assert caught.value.code == "identity_conflict"


@pytest.mark.parametrize("version", [1, 2])
def test_fresh_root_can_admit_either_explicit_format(version):
    audit._require_root_version({}, version)


@pytest.mark.parametrize("existing", [1, 2])
@pytest.mark.parametrize("requested", [1, 2])
def test_missing_admission_uses_validated_open_version(existing, requested):
    row = {
        "metadata": {},
        "schema_invalid": False,
        "events": [{"event": "run_open", "schema_version": existing}],
    }
    if existing == requested:
        audit._require_root_version({RUN: row}, requested)
    else:
        with pytest.raises(audit._Rejected, match="identity_conflict"):
            audit._require_root_version({RUN: row}, requested)


@pytest.mark.parametrize("version", [1, 2])
def test_unversioned_crash_prefix_is_not_upgraded_or_recharged(version):
    row = {
        "metadata": {},
        "schema_invalid": False,
        "events": [],
        "torn": True,
        "charge": audit.RUN_BYTES,
    }
    audit._require_root_version({RUN: row}, version)
    assert row["charge"] == audit.RUN_BYTES and row["torn"] and row["metadata"] == {}


@pytest.mark.parametrize(
    "row",
    [
        {"metadata": {}, "schema_invalid": True, "events": []},
        {"metadata": {"fault": {}}, "events": []},
        {"metadata": {}, "events": [{"event": "run_open", "schema_version": 3}]},
        {"metadata": {"admission": None}, "events": [{"event": "start"}]},
        {"metadata": {"admission": {"schema_version": True}}},
        {"metadata": []},
    ],
)
def test_root_unknown_or_malformed_schema_is_typed_rejection(row):
    with pytest.raises(audit._Rejected):
        audit._require_root_version({RUN: row}, 1)


@pytest.mark.parametrize(
    "version,run,wire", [(1, 16 * 1024 * 1024, 256 * 1024), (2, 64 * 1024 * 1024, 8 * 1024 * 1024)]
)
def test_versioned_capacity_is_explicit_without_changing_legacy(version, run, wire):
    policy = audit._reservation_policy(version)
    assert policy["run_bytes"] == run and policy["root_bytes"] == 512 * 1024 * 1024
    assert audit._layer_policy(version, "wire", "openai")[1] == wire
    assert audit._layer_policy(version, "decoded", "openai")[1] == 256 * 1024
    assert audit._layer_policy(version, "stdout", "cli") == audit._layer_policy(version, "stderr", "cli")
    assert audit._layer_policy(version, "stdout", "cli")[1] == 256 * 1024
    assert audit._retention_seconds(version) == (7 if version == 1 else 3) * 86400
    if version == 1:
        assert set(policy) == {"run_bytes", "marker_bytes", "root_bytes", "body_bytes"}


@pytest.mark.parametrize("field", list(audit._reservation_policy(2)))
@pytest.mark.parametrize("mutation", ["missing", "changed", "boolean"])
def test_v2_required_capacity_cannot_be_omitted_or_tampered(field, mutation):
    opened = _valid_open(2)
    if mutation == "missing":
        del opened["reservation"][field]
    else:
        opened["reservation"][field] = (
            True if mutation == "boolean" else opened["reservation"][field] + 1
        )
    with pytest.raises(audit._Rejected):
        audit._fold([opened])


@pytest.mark.parametrize("mutation", ["missing", "legacy_run", "wire", "manifest"])
def test_v2_markers_require_same_capacity_policy(mutation):
    opened = _valid_open(2)
    marker = json.loads(audit._marker(opened, OWNER))
    if mutation == "missing":
        del marker["limits"]
    else:
        field = {"legacy_run": "run_bytes", "wire": "wire_bytes", "manifest": "manifest_bytes"}[mutation]
        marker["limits"][field] = audit.RUN_BYTES if mutation == "legacy_run" else 1
    with pytest.raises(audit._Rejected):
        audit._marker_projections({"admission": marker}, [opened])


@pytest.mark.parametrize("version", [1, 2])
def test_marker_ordinary_limit_tracks_version(version):
    opened = _valid_open(version)
    maximum = audit._run_limit(version) - audit.MARKER_BYTES
    good = json.loads(audit._marker(opened, OWNER, ordinary=maximum))
    assert audit._marker_projection(good)["ordinary_bytes"] == maximum
    good["ordinary_bytes"] += 1
    with pytest.raises(audit._Rejected):
        audit._marker_projection(good)


@pytest.mark.parametrize("version", [1, 2])
def test_inventory_unversioned_failed_open_uses_explicit_root_policy(monkeypatch, version):
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def directory(*args):
        yield -1

    monkeypatch.setattr(audit, "_entries", lambda fd: [RUN])
    monkeypatch.setattr(audit, "_run_dir", directory)
    monkeypatch.setattr(
        audit,
        "_qualified_run",
        lambda *args, **kwargs: dict(
            events=[], metadata={}, schema_invalid=False, charge=audit.RUN_BYTES, torn=True
        ),
    )
    rows, total = audit._inventory(SimpleNamespace(fd=-1), _version=version)
    assert total == rows[RUN]["charge"] == audit._run_limit(version)
    assert rows[RUN]["torn"] and rows[RUN]["metadata"] == {}


@pytest.mark.parametrize("version", [1, 2])
def test_root_inventory_grants_only_whole_versioned_reservations(monkeypatch, version):
    from contextlib import contextmanager
    from types import SimpleNamespace

    capacity = audit.ROOT_BYTES // audit._run_limit(version)
    names = [f"00000000-0000-4000-8000-{i + 100:012d}" for i in range(capacity)]

    @contextmanager
    def directory(*args):
        yield -1

    monkeypatch.setattr(audit, "_entries", lambda fd: names.copy())
    monkeypatch.setattr(audit, "_run_dir", directory)
    monkeypatch.setattr(
        audit,
        "_qualified_run",
        lambda *args, **kwargs: dict(
            events=[_valid_open(version)], schema_invalid=False, charge=audit._run_limit(version)
        ),
    )
    _, total = audit._inventory(SimpleNamespace(fd=-1), _version=version)
    assert total == audit.ROOT_BYTES
    assert total + audit._run_limit(version) > audit.ROOT_BYTES
    names.pop()
    _, prior = audit._inventory(SimpleNamespace(fd=-1), _version=version)
    assert prior + audit._run_limit(version) == audit.ROOT_BYTES
