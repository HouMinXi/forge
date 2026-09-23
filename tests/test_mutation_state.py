"""Tests for the run state-root layout and ownership machinery."""

import json
import os

import pytest

from code_forge.mutation_engines.state import (
    OwnerRecord,
    SecondHolderError,
    StateError,
    current_boot_id,
    is_owner_alive,
    make_owner_record,
    publish,
    read_owner,
    read_published,
    recover_run,
    reserve_worker,
    run_dir,
    write_owner,
)

RUN = "run-abc123"


def _owner(state_root, pid=None):
    pid = pid if pid is not None else os.getpid()
    return make_owner_record(
        state_root=str(state_root),
        run_id=RUN,
        pid=pid,
        cgroup_path="/sys/fs/cgroup/forge/runs/" + RUN,
        supervisor_thread="supervisor-main",
    )


def _dead_owner(state_root):
    """An owner record whose pid cannot be alive (constructed, not captured)."""
    return OwnerRecord(
        schema_version=1,
        state_root=str(state_root),
        run_id=RUN,
        pid=999999,
        boot_id=current_boot_id(),
        start_ticks=12345,
        cgroup_path="/sys/fs/cgroup/forge/runs/" + RUN,
        supervisor_thread="supervisor-main",
        created_utc="2026-09-23T00:00:00Z",
    )


def test_make_owner_record_captures_boot_and_start_identity(tmp_path):
    owner = _owner(tmp_path)
    assert owner.boot_id == current_boot_id()
    assert owner.pid == os.getpid()
    assert owner.start_ticks > 0
    assert owner.schema_version == 1


def test_run_dir_validates_run_id(tmp_path):
    with pytest.raises(StateError):
        run_dir(tmp_path, "../escape")
    with pytest.raises(StateError):
        run_dir(tmp_path, "a/b")
    with pytest.raises(StateError):
        run_dir(tmp_path, "")


def test_owner_record_roundtrip(tmp_path):
    d = run_dir(tmp_path, RUN)
    owner = _owner(tmp_path)
    write_owner(d, owner)
    loaded = read_owner(d)
    assert loaded == owner


def test_read_owner_missing_raises(tmp_path):
    d = run_dir(tmp_path, RUN)
    with pytest.raises(StateError):
        read_owner(d)


def test_reserve_worker_rejects_second_holder(tmp_path):
    reserve_worker(tmp_path, "worker-1", _owner(tmp_path))
    with pytest.raises(SecondHolderError):
        reserve_worker(tmp_path, "worker-1", _owner(tmp_path))


def test_reserve_worker_second_worker_ok(tmp_path):
    reserve_worker(tmp_path, "worker-1", _owner(tmp_path))
    res2 = reserve_worker(tmp_path, "worker-2", _owner(tmp_path))
    assert res2.worker_id == "worker-2"


def test_release_worker_frees_reservation(tmp_path):
    res = reserve_worker(tmp_path, "worker-1", _owner(tmp_path))
    res.release()
    reserve_worker(tmp_path, "worker-1", _owner(tmp_path))


def test_is_owner_alive_true_for_self(tmp_path):
    assert is_owner_alive(_owner(tmp_path)) is True


def test_is_owner_alive_false_for_dead_pid(tmp_path):
    owner = _dead_owner(tmp_path)
    assert is_owner_alive(owner) is False


def test_is_owner_alive_false_for_recycled_pid(tmp_path):
    # start_ticks that cannot belong to the pid
    owner = OwnerRecord(
        schema_version=1,
        state_root=str(tmp_path),
        run_id=RUN,
        pid=os.getpid(),
        boot_id=current_boot_id(),
        start_ticks=1,
        cgroup_path="/sys/fs/cgroup/forge/runs/" + RUN,
        supervisor_thread="supervisor-main",
        created_utc="2026-09-23T00:00:00Z",
    )
    assert is_owner_alive(owner) is False


def test_is_owner_alive_false_for_wrong_boot(tmp_path):
    owner = OwnerRecord(
        schema_version=1,
        state_root=str(tmp_path),
        run_id=RUN,
        pid=os.getpid(),
        boot_id="0" * 36,
        start_ticks=1,
        cgroup_path="/sys/fs/cgroup/forge/runs/" + RUN,
        supervisor_thread="supervisor-main",
        created_utc="2026-09-23T00:00:00Z",
    )
    assert is_owner_alive(owner) is False


def test_publish_is_atomic_and_bounded(tmp_path):
    d = run_dir(tmp_path, RUN)
    publish(d, "manifest.json", b'{"ok": true}')
    assert read_published(d, "manifest.json") == b'{"ok": true}'
    # no temporary residue left behind
    leftovers = [p for p in (d / "results").iterdir() if p.name.startswith(".tmp")]
    assert leftovers == []


def test_publish_rejects_escape_names(tmp_path):
    d = run_dir(tmp_path, RUN)
    with pytest.raises(StateError):
        publish(d, "../evil", b"x")
    with pytest.raises(StateError):
        publish(d, "a/b", b"x")


def test_publish_rejects_oversized(tmp_path):
    d = run_dir(tmp_path, RUN)
    with pytest.raises(StateError):
        publish(d, "big.bin", b"x" * (64 * 1024 * 1024 + 1))


def test_read_published_missing_raises(tmp_path):
    d = run_dir(tmp_path, RUN)
    with pytest.raises(StateError):
        read_published(d, "nope.json")


def test_recover_live_owner_refused(tmp_path):
    d = run_dir(tmp_path, RUN)
    write_owner(d, _owner(tmp_path))
    outcome = recover_run(tmp_path, RUN)
    assert outcome.action == "refused_live_owner"
    assert d.exists()


def test_recover_dead_owner_reclaims(tmp_path):
    d = run_dir(tmp_path, RUN)
    (d / "results").mkdir(exist_ok=True)
    (d / "results" / "artifact.bin").write_bytes(b"junk")
    write_owner(d, _dead_owner(tmp_path))
    outcome = recover_run(tmp_path, RUN)
    assert outcome.action == "reclaimed"
    assert not d.exists()


def test_recover_missing_run_dir_is_noop(tmp_path):
    outcome = recover_run(tmp_path, RUN)
    assert outcome.action == "no_run"


def test_recover_unverifiable_owner_holds_without_delete(tmp_path):
    d = run_dir(tmp_path, RUN)
    (d / "owner.json").write_text("{corrupt", encoding="utf-8")
    outcome = recover_run(tmp_path, RUN)
    assert outcome.action == "hold"
    assert d.exists(), "unverifiable ownership must never be deleted"


def test_owner_record_rejects_unknown_fields(tmp_path):
    d = run_dir(tmp_path, RUN)
    owner = _owner(tmp_path)
    write_owner(d, owner)
    raw = json.loads((d / "owner.json").read_text())
    raw["surprise"] = 1
    (d / "owner.json").write_text(json.dumps(raw))
    with pytest.raises(StateError):
        read_owner(d)
