"""Independent expected observations for the local invocation journal."""

import dataclasses
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import uuid

import pytest

import code_forge.invocation_audit as audit


RUN = "00000000-0000-4000-8000-000000000001"
LOGICAL = "00000000-0000-4000-8000-000000000002"
SNAPSHOT = "00000000-0000-4000-8000-000000000003"
GROUP = "00000000-0000-4000-8000-000000000004"
SOURCE = "a" * 64
DIGEST = hashlib.sha256(b"request").hexdigest()
BACKEND = audit.BackendObservation("openai", "fixture", "model", None, "b" * 64)
COMMON = {"schema_version", "run_id", "event_id", "sequence", "event", "utc", "elapsed_s"}
SUMMARY_KEYS = {
    "schema_version",
    "run_id",
    "state",
    "audit_complete",
    "source_binding",
    "logical_call_count",
    "admitted_attempt_count",
    "observed_api_send_count",
    "observed_cli_launch_count",
    "unknown_dispatch_count",
    "outcome_counts",
    "observation_count",
    "usage",
    "timing",
    "cost",
    "fault_codes",
}
CONTEXT_KEYS = {
    "run_id",
    "source_hash",
    "snapshot_id",
    "round_index",
    "pass_name",
    "group_id",
    "group_diff_sha256",
    "purpose",
    "parent_logical_id",
}


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def utc():
    return "2026-10-06T12:00:00Z"


def context(**changes):
    return dataclasses.replace(
        audit.InvocationContext(
            RUN, None, None, 0, "qodo-review", GROUP, "c" * 64, "review", None
        ),
        **changes,
    )


@pytest.fixture
def disk_root():
    # The checkout is on the declared host filesystem; /tmp is not assumed.
    path = Path(__file__).resolve().parents[1] / ".planning" / "audit-fixtures" / str(uuid.uuid4())
    path.mkdir(parents=True, mode=0o700)
    return path / "investigation"


@pytest.fixture
def recorder(disk_root):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=clock, utc_now=utc)
    assert owner.open().admitted, "fresh supported root must durably admit without a token"
    return owner, clock


def start(owner, ctx=None, logical=LOGICAL, parent=None, cause="initial"):
    ack = owner.start(
        ctx or context(),
        logical_id=logical,
        parent_id=parent,
        cause=cause,
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert ack.admitted and ack.fault is None and ack.attempt_id is not None
    assert str(uuid.UUID(ack.attempt_id)) == ack.attempt_id
    return ack.attempt_id


def usage(
    inp=None,
    out=None,
    cache=None,
    reasoning=None,
    *,
    kind="final",
    input_semantics="total_includes_cache",
    output_semantics="total_includes_reasoning",
    availability=None,
):
    values = (inp, out, cache, reasoning)
    label = availability or (
        "known"
        if all(x is not None for x in values)
        else "unknown"
        if all(x is None for x in values)
        else "partial"
    )
    return audit.UsageObservation(
        inp,
        out,
        cache,
        reasoning,
        label,
        input_semantics,
        output_semantics,
        kind,
        "native",
        "attempt",
        {},
    )


def acquire(owner, attempt, observation, data=b"raw", identity=None, layer="wire", complete=True):
    return owner.acquired(
        attempt, layer=layer, data=data, complete=complete, usage=observation, observation_id=identity
    )


def finish(owner, attempt, duration=1.0, outcome="completed", dispatch="entered_api_send"):
    return owner.finish(
        attempt, outcome=outcome, error_class=None, duration_s=duration, dispatch_state=dispatch
    )


def events(owner):
    content = (owner.root / RUN / "journal").read_bytes()
    result = []
    offset = 0
    while offset < len(content):
        size = int(content[offset : offset + 8], 16)
        assert content[offset + 8 : offset + 9] == b" "
        payload = content[offset + 9 : offset + 9 + size]
        assert content[offset + 9 + size : offset + 10 + size] == b"\n"
        result.append(json.loads(payload))
        offset += size + 10
    return result


def field(subtotal, total, unknown=0, invalid=0):
    return {
        "known_subtotal": subtotal,
        "total": total,
        "unknown_attempt_count": unknown,
        "invalid_attempt_count": invalid,
    }


def test_empty_schema_and_unknown_cost(disk_root):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    summary = owner.summary()
    assert set(summary) == SUMMARY_KEYS
    assert summary["cost"] is None
    assert summary["usage"]["input_tokens"] == field(0, None)
    assert summary["timing"] == {
        "work_s": None,
        "wall_s": None,
        "known_work_s": 0.0,
        "unknown_duration_count": 0,
    }


def test_automatic_open_and_closed_disk_schema(recorder):
    owner, _ = recorder
    assert owner.snapshot().capability.filesystem_type in ("ext4", "btrfs")
    run = owner.root / RUN
    assert run.stat().st_mode & 0o777 == 0o700
    assert owner.root.stat().st_mode & 0o777 == 0o700
    assert (owner.root / "root.lock").stat().st_size == 0
    records = events(owner)
    assert [r["event"] for r in records] == ["run_open"]
    assert set(records[0]) == COMMON | {"context", "capability", "owner", "reservation"}
    assert records[0]["context"] == dataclasses.asdict(context())
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in run.iterdir())


@pytest.mark.parametrize(
    "value,expected", [(0, field(0, 0)), (None, field(0, None, 1)), (7, field(7, 7))]
)
def test_explicit_zero_and_null_remain_distinct(recorder, value, expected):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(value, 0, 0, 0)).admitted
    assert finish(owner, attempt).admitted
    assert owner.summary()["usage"]["input_tokens"] == expected


@pytest.mark.parametrize("kind,counts", [("cumulative", [2, 5, 9]), ("incremental", [2, 3, 4])])
def test_host_identity_streams_and_replay(recorder, kind, counts):
    owner, _ = recorder
    attempt = start(owner)
    last = None
    for count in counts:
        last = acquire(owner, attempt, usage(count, 0, 0, 0, kind=kind))
        assert last.admitted
    before = events(owner)
    replay = acquire(owner, attempt, usage(counts[-1], 0, 0, 0, kind=kind), identity=last.observation_id)
    assert replay == last and events(owner) == before
    assert finish(owner, attempt).admitted
    summary = owner.summary()
    assert summary["usage"]["input_tokens"] == field(9, 9)
    assert summary["observation_count"] == 3


def test_distinct_equal_increments_and_changed_identity(recorder):
    owner, _ = recorder
    attempt = start(owner)
    first = acquire(owner, attempt, usage(2, 0, 0, 0, kind="incremental"))
    second = acquire(owner, attempt, usage(2, 0, 0, 0, kind="incremental"))
    assert first.observation_id != second.observation_id
    assert owner.summary()["usage"]["input_tokens"]["known_subtotal"] == 4
    denied = acquire(
        owner, attempt, usage(3, 0, 0, 0, kind="incremental"), identity=first.observation_id
    )
    assert not denied.admitted and denied.fault.code == "integrity_error"
    assert owner.summary()["usage"]["input_tokens"]["known_subtotal"] == 4
    assert owner.snapshot().audit_complete is False


@pytest.mark.parametrize("bad", [True, -1, 1.0, math.inf, math.nan, "2", object()])
def test_invalid_usage_is_typed_and_does_not_serialize(recorder, bad):
    owner, _ = recorder
    attempt = start(owner)
    denied = acquire(owner, attempt, usage(bad, 0, 0, 0))
    assert not denied.admitted and denied.ref is None and denied.fault.code == "invalid_input"
    assert owner.snapshot().audit_complete is False


@pytest.mark.parametrize(
    "second", [usage(1, 0, 0, 0, kind="cumulative"), usage(3, 0, 0, 0, kind="incremental")]
)
def test_regression_or_mixed_stream_preserves_prior_measurement(recorder, second):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(5, 0, 0, 0, kind="cumulative")).admitted
    denied = acquire(owner, attempt, second)
    assert not denied.admitted
    assert finish(owner, attempt, outcome="failed").admitted
    assert owner.summary()["usage"]["input_tokens"] == field(5, None, invalid=1)


@pytest.mark.parametrize(
    "inp,cache,ins,out,reasoning,outs,expected_in,expected_out",
    [
        (10, 3, "total_includes_cache", 8, 2, "total_includes_reasoning", 10, 8),
        (10, 3, "uncached_excludes_cache", 8, 2, "visible_excludes_reasoning", 13, 10),
        (10, None, "uncached_excludes_cache", 8, None, "visible_excludes_reasoning", None, None),
        (10, 3, "unknown", 8, 2, "unknown", None, None),
    ],
)
def test_declared_subset_semantics(
    recorder, inp, cache, ins, out, reasoning, outs, expected_in, expected_out
):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(
        owner, attempt, usage(inp, out, cache, reasoning, input_semantics=ins, output_semantics=outs)
    ).admitted
    assert finish(owner, attempt).admitted
    totals = owner.summary()["usage"]
    assert totals["input_tokens"]["total"] == expected_in
    assert totals["output_tokens"]["total"] == expected_out
    assert totals["cached_input_tokens"]["known_subtotal"] == (cache or 0)
    assert totals["reasoning_tokens"]["known_subtotal"] == (reasoning or 0)


def test_failed_known_plus_unknown_and_no_dispatch(recorder):
    owner, _ = recorder
    a = start(owner)
    assert acquire(owner, a, usage(6, 4, 2, 1)).admitted
    assert finish(owner, a, outcome="failed").admitted
    b = start(owner, logical=str(uuid.uuid4()))
    assert finish(owner, b, dispatch="unknown", duration=None).admitted
    c = start(owner, logical=str(uuid.uuid4()))
    assert finish(owner, c, outcome="refused", dispatch="not_dispatched", duration=None).admitted
    summary = owner.summary()
    assert summary["usage"]["input_tokens"] == field(6, None, 1)
    assert summary["observed_api_send_count"] == 1
    assert summary["unknown_dispatch_count"] == 1
    assert summary["outcome_counts"] == {
        "completed": 1,
        "failed": 1,
        "refused": 1,
        "cancelled": 0,
        "incomplete": 0,
        "unknown": 0,
    }


def test_overlapping_owner_intervals_work_six_wall_four(recorder):
    owner, clock = recorder
    a = start(owner)
    clock.value = 1
    b = start(owner, logical=str(uuid.uuid4()))
    clock.value = 3
    assert finish(owner, a, duration=3).admitted
    clock.value = 4
    assert finish(owner, b, duration=3).admitted
    assert owner.finalize("completed").admitted
    assert owner.summary()["timing"] == {
        "work_s": 6.0,
        "wall_s": 4.0,
        "known_work_s": 6.0,
        "unknown_duration_count": 0,
    }


def test_full_start_attribution_reopens_without_mutation(recorder):
    owner, _ = recorder
    a = start(owner)
    child_logical = str(uuid.uuid4())
    child = context(
        round_index=1,
        pass_name="adversarial-qe",
        group_id=str(uuid.uuid4()),
        group_diff_sha256="d" * 64,
        purpose="repair",
        parent_logical_id=LOGICAL,
    )
    b = start(owner, child, child_logical, a, "excerpt_repair")
    retry = start(owner, child, child_logical, b, "retry")
    expected = {a: context(), b: child, retry: child}
    snapshot = owner.snapshot()
    assert dict(snapshot.attempt_contexts) == expected
    with pytest.raises(TypeError):
        snapshot.attempt_contexts[a] = child
    object.__setattr__(child, "purpose", "caller-mutated")
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert reopened.attempt_contexts[b].purpose == "repair"
    assert reopened.attempt_contexts[retry].purpose == "repair"
    assert reopened.summary == owner.summary()
    starts = [row for row in events(owner) if row["event"] == "start"]
    assert all(set(row["context"]) == CONTEXT_KEYS for row in starts)
    assert starts[1]["parent_id"] == a and starts[1]["context"]["parent_logical_id"] == LOGICAL
    assert starts[2]["parent_id"] == b and starts[2]["context"]["parent_logical_id"] == LOGICAL


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": str(uuid.uuid4())},
        {"source_hash": "d" * 64, "snapshot_id": SNAPSHOT},
        {"snapshot_id": str(uuid.uuid4()), "source_hash": SOURCE},
        {"round_index": 2},
        {"group_id": str(uuid.uuid4())},
        {"purpose": "other"},
        {"parent_logical_id": str(uuid.uuid4())},
    ],
)
def test_retry_cannot_rebind_full_context(recorder, changes):
    owner, _ = recorder
    a = start(owner)
    ack = owner.start(
        context(**changes),
        logical_id=LOGICAL,
        parent_id=a,
        cause="retry",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not ack.admitted and ack.attempt_id is None and ack.fault.code == "identity_conflict"
    assert owner.summary()["admitted_attempt_count"] == 1


def test_exact_sensitive_raw_closed_metadata_preview_default_off(recorder):
    owner, _ = recorder
    attempt = start(owner)
    sensitive = b"API_KEY=secret\nAuthorization: bearer sensitive\nhttps://secret.invalid?q=key"
    ack = acquire(owner, attempt, usage(0, 0, 0, 0), data=sensitive)
    assert ack.admitted and ack.ref.sha256 == hashlib.sha256(sensitive).hexdigest()
    raw = owner.root / RUN / ("raw-" + ack.ref.artifact_id)
    assert raw.read_bytes() == sensitive and raw.stat().st_mode & 0o777 == 0o600
    rows = events(owner)
    assert "secret" not in json.dumps(rows) and "preview" not in json.dumps(rows)
    acquired = [r for r in rows if r["event"] == "acquired"][0]
    assert set(acquired) == COMMON | {
        "attempt_id",
        "observation_id",
        "layer",
        "ref",
        "complete",
        "usage",
    }
    assert not (owner.root.parent / "receipts").exists()


def test_missing_finish_and_torn_tail_remain_ambiguous(recorder):
    owner, _ = recorder
    start(owner)
    before = (owner.root / RUN / "journal").read_bytes()
    with (owner.root / RUN / "journal").open("ab") as stream:
        stream.write(b"00000020 {")
    torn = (owner.root / RUN / "journal").read_bytes()
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert reopened.state == "incomplete" and not reopened.audit_complete
    assert reopened.summary["unknown_dispatch_count"] == 1
    assert reopened.summary["outcome_counts"]["unknown"] == 1
    assert (owner.root / RUN / "journal").read_bytes() == torn and torn.startswith(before)


def test_denied_open_or_start_has_zero_fixture_dispatches(disk_root):
    dispatches = []
    owner = audit.AttemptRecorder(
        disk_root, context=context(source_hash=None, snapshot_id=SNAPSHOT), monotonic=Clock(), utc_now=utc
    )
    ack = owner.open()
    if ack.admitted:
        dispatches.append("send")
    assert ack.fault.code == "invalid_input" and not dispatches


def test_primary_exception_identity_survives_sink_fault(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    original = RuntimeError("caller result")

    def fail(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr(os, "fsync", fail)
    try:
        try:
            raise original
        except RuntimeError:
            ack = owner.acquired(
                attempt, layer="wire", data=b"retained caller bytes", complete=True, usage=None
            )
            assert not ack.admitted and ack.fault.code == "persistence_error"
            raise
    except RuntimeError as observed:
        assert observed is original
    assert not owner.snapshot().audit_complete


def test_finish_and_finalize_replay_and_conflict(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    before = (owner.root / RUN / "journal").read_bytes()
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    assert (owner.root / RUN / "journal").read_bytes() == before
    assert not owner.finalize("failed").admitted
    assert not owner.start(
        context(),
        logical_id=str(uuid.uuid4()),
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    ).admitted


def test_preview_opt_in_is_sensitive_and_bounded(disk_root):
    owner = audit.AttemptRecorder(
        disk_root, context=context(), monotonic=Clock(), utc_now=utc, preview_enabled=True
    )
    assert owner.open().admitted
    attempt = start(owner)
    ack = acquire(owner, attempt, None, data=b'\xff"\nsecret' * 500)
    assert ack.admitted
    row = events(owner)[-1]
    assert row["ref"]["preview_sensitive"] is True
    assert len(row["ref"]["preview"]) <= 400
    assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    replay = acquire(owner, attempt, None, data=b'\xff"\nsecret' * 500, identity=ack.observation_id)
    assert replay.admitted and replay.ref == ack.ref


@pytest.mark.parametrize("size", [256 * 1024 - 1, 256 * 1024, 256 * 1024 + 1])
def test_exact_raw_cap_prefix_and_decoded_independence(recorder, size):
    owner, _ = recorder
    attempt = start(owner)
    data = b"x" * size
    ack = acquire(owner, attempt, None, data=data)
    assert ack.ref.retained_bytes == min(size, 256 * 1024)
    assert ack.ref.original_bytes == size
    assert ack.ref.sha256 == hashlib.sha256(data[: 256 * 1024]).hexdigest()
    assert ack.admitted == (size <= 256 * 1024)
    assert ack.ref.partial == (size > 256 * 1024)
    if ack.admitted:
        decoded = acquire(owner, attempt, None, data=b"decoded", layer="decoded")
        assert decoded.admitted and decoded.ref.layer == "decoded"
        overflow = acquire(owner, attempt, None, data=b"y" * (256 * 1024), layer="stderr")
        assert not overflow.admitted and overflow.ref.retained_bytes == 256 * 1024 - size


@pytest.mark.parametrize("site", ["file_sync", "rename", "directory_sync", "event_commit"])
def test_each_acquired_commit_cut_publishes_no_usable_ref(recorder, monkeypatch, site):
    owner, _ = recorder
    attempt = start(owner)
    old_sync = os.fsync
    old_rename = os.rename
    old_append = audit._append

    def sync(fd):
        directory = os.fstat(fd).st_mode & 0o170000 == 0o040000
        if (
            site == "file_sync"
            and not directory
            and "/temp-raw-" in os.readlink("/proc/self/fd/" + str(fd))
        ) or (
            site == "directory_sync"
            and directory
            and os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root / RUN)
            and any(p.name.startswith("raw-") for p in (owner.root / RUN).iterdir())
        ):
            raise OSError("cut")
        return old_sync(fd)

    def rename(*args, **kwargs):
        if site == "rename":
            raise OSError("cut")
        return old_rename(*args, **kwargs)

    def append(fd, event):
        if site == "event_commit" and event["event"] == "acquired":
            raise OSError("cut")
        return old_append(fd, event)

    with monkeypatch.context() as cuts:
        cuts.setattr(os, "fsync", sync)
        cuts.setattr(os, "rename", rename)
        cuts.setattr(audit, "_append", append)
        ack = acquire(owner, attempt, None, data=b"orphan evidence")
        assert not ack.admitted and ack.ref is None and ack.fault.code == "persistence_error"
    raw_names = [
        p.name for p in (owner.root / RUN).iterdir() if p.name.startswith(("raw-", "temp-raw-"))
    ]
    assert raw_names, "failed raw publication preserves exact owned bytes"
    assert sum((owner.root / RUN / n).stat().st_size for n in raw_names) == len(b"orphan evidence")
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not reopened.audit_complete and reopened.summary["observation_count"] == 0


def test_terminal_freeze_denial_preserves_snapshot_and_disk(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    before = owner.snapshot()
    disk = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    assert owner.finalize("completed").admitted
    assert owner.snapshot() == before
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == disk
    terminal = other_owner(owner.root, str(uuid.uuid4()))
    assert terminal.open().admitted and terminal.finalize("completed").admitted
    active = [other_owner(owner.root, str(uuid.uuid4())) for _ in range(31)]
    assert all(sibling.open().admitted for sibling in active)
    assert audit.ROOT_BYTES == 512 * 1024 * 1024
    assert all(events(sibling)[0]["reservation"]["root_bytes"] == audit.ROOT_BYTES for sibling in active)
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            runs, charge = audit._inventory(handle)
            assert charge + audit.RUN_BYTES - runs[RUN]["charge"] > audit.ROOT_BYTES
    finally:
        handle.close()
    denied = owner.set_frozen(True)
    assert not denied.admitted and denied.fault == audit.AuditFault("quota_refused", "freeze", None)
    assert owner.snapshot() == before
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == disk
    assert owner.set_frozen(True) == denied
    assert active[0].finalize("completed").admitted
    assert owner.set_frozen(True).admitted
    frozen = (owner.root / RUN / "journal").read_bytes()
    assert owner.set_frozen(True).admitted
    assert (owner.root / RUN / "journal").read_bytes() == frozen
    assert not owner.set_frozen(False).admitted


def test_failed_freeze_after_admission_keeps_full_charge(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    original = audit._append

    def fail(fd, event):
        if event["event"] == "freeze":
            raise OSError("cut")
        return original(fd, event)

    with monkeypatch.context() as cut:
        cut.setattr(audit, "_append", fail)
        assert not owner.set_frozen(True).admitted
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            rows, charge = audit._inventory(handle)
            assert charge == 16 * 1024 * 1024 and rows[RUN]["frozen"]
    finally:
        handle.close()
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


def test_terminal_maintenance_tombstone_precedes_delete_and_sync_preserves_charge(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(0, 0, 0, 0), data=b"payload" * 1000).admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted

    def later():
        return "2026-10-14T12:00:00Z"

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            before_charge = audit._inventory(handle)[1]
    finally:
        handle.close()
    original_unlink = os.unlink
    calls = []

    def unlink(name, *args, **kwargs):
        assert (owner.root / RUN / "tombstone").exists()
        calls.append(name)
        return original_unlink(name, *args, **kwargs)

    original_sync = os.fsync

    def fail_removal_sync(fd):
        if calls and os.fstat(fd).st_mode & 0o170000 == 0o040000:
            raise OSError("removal sync")
        return original_sync(fd)

    with monkeypatch.context() as cuts:
        cuts.setattr(os, "unlink", unlink)
        cuts.setattr(os, "fsync", fail_removal_sync)
        ack = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=later, action_deadline=100
        )
        assert not ack.admitted
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            after_charge = audit._inventory(handle)[1]
            assert after_charge >= before_charge, "uncommitted shrink cannot fund new admission"
    finally:
        handle.close()
    assert (owner.root / RUN / "tombstone").exists()


def test_frozen_active_and_unknown_ownership_are_not_pruned(recorder):
    owner, _ = recorder
    start(owner)
    assert owner.set_frozen(True).admitted

    def later():
        return "2027-01-01T12:00:00Z"

    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=later, action_deadline=100
    ).admitted
    assert (owner.root / RUN).exists()
    (owner.root / "foreign").write_bytes(b"unknown")
    denied = audit.maintain_audit_root(owner.root, monotonic=Clock(), utc_now=later, action_deadline=100)
    assert not denied.admitted and (owner.root / "foreign").read_bytes() == b"unknown"


@pytest.mark.parametrize("failure", ["unsupported", "malformed", "file_sync", "directory_sync"])
def test_automatic_profile_and_required_primitives_refuse(disk_root, monkeypatch, failure):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    original = audit._profile
    sync = os.fsync

    def profile(fd):
        if failure in ("unsupported", "malformed"):
            raise audit._Rejected("unsupported_profile")
        return original(fd)

    def fsync(fd):
        directory = os.fstat(fd).st_mode & 0o170000 == 0o040000
        if (failure == "file_sync" and not directory) or (failure == "directory_sync" and directory):
            raise OSError("cut")
        return sync(fd)

    monkeypatch.setattr(audit, "_profile", profile)
    monkeypatch.setattr(os, "fsync", fsync)
    ack = owner.open()
    assert not ack.admitted and ack.fault.code in ("unsupported_profile", "persistence_error")
    assert owner.snapshot().audit_complete is False


def test_symlink_hardlink_and_replaced_root_refuse(recorder):
    owner, _ = recorder
    attempt = start(owner)
    ack = acquire(owner, attempt, None)
    path = owner.root / RUN / ("raw-" + ack.ref.artifact_id)
    os.link(path, owner.root / RUN / ("raw-" + str(uuid.uuid4())))
    assert not finish(owner, attempt).admitted
    assert not owner.snapshot().audit_complete


def other_owner(root, run_id):
    return audit.AttemptRecorder(root, context=context(run_id=run_id), monotonic=Clock(), utc_now=utc)


def _child_source(root, run_id):
    return (
        "import sys,time\nfrom pathlib import Path\nfrom contextlib import contextmanager\n"
        "import code_forge.invocation_audit as a\noriginal=a._root_lock\n"
        "@contextmanager\ndef synchronized(handle,**kwargs):\n"
        " print('ready',flush=True)\n sys.stdin.readline()\n"
        " with original(handle,**kwargs): yield\n"
        "a._root_lock=synchronized\n"
        "r=a.AttemptRecorder(Path("
        + repr(str(root))
        + "),context=a.InvocationContext("
        + repr(run_id)
        + ",None,None,None,None,None,None,'fixture',None),monotonic=time.monotonic,"
        "utc_now=lambda:'2026-10-06T12:00:00Z')\n"
        "ack=r.open()\nprint('admitted' if ack.admitted else ack.fault.code,flush=True)\n"
    )


def test_real_two_process_last_full_slot_and_tombstone_blocks_extra(disk_root):
    import subprocess

    owners = []
    for _ in range(31):
        owner = other_owner(disk_root, str(uuid.uuid4()))
        assert owner.open().admitted
        owners.append(owner)
    children = []
    try:
        for _ in range(2):
            child = subprocess.Popen(
                ["/usr/bin/python3", "-B", "-c", _child_source(disk_root, str(uuid.uuid4()))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
            assert child.stdout.readline() == "ready\n"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = [child.communicate(timeout=5) for child in children]
        assert sorted(out.strip() for out, err in results) == ["admitted", "quota_refused"]
        assert all(child.returncode == 0 for child in children)
        assert all(not err for out, err in results)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
    handle = audit._prepare_audit_root(disk_root, create=False)
    try:
        with audit._root_lock(handle):
            assert audit._inventory(handle)[1] == 512 * 1024 * 1024
    finally:
        handle.close()
    assert not other_owner(disk_root, str(uuid.uuid4())).open().admitted


def test_real_held_lock_start_deadline_and_no_dispatch(recorder):
    import subprocess

    owner, _ = recorder
    source = (
        "import fcntl,sys\nf=open(" + repr(str(owner.root / "root.lock")) + ',"r+")\n'
        'fcntl.flock(f,fcntl.LOCK_EX)\nprint("held",flush=True)\nsys.stdin.readline()\nf.close()\n'
    )
    child = subprocess.Popen(
        ["/usr/bin/python3", "-B", "-c", source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline() == "held\n"
        import time

        before = time.monotonic()
        denied = owner.start(
            context(),
            logical_id=LOGICAL,
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=0.05,
        )
        elapsed = time.monotonic() - before
        assert not denied.admitted and denied.fault.code == "lock_timeout"
        assert 0.04 <= elapsed < 0.5
        assert owner.summary()["admitted_attempt_count"] == 0
        child.communicate("release\n", timeout=5)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def test_owned_fork_rejects_inherited_mutex_without_unlocking_parent(recorder):
    import fcntl
    import select
    import sys

    owner, _ = recorder
    coverage = sys.modules.get("coverage")
    collector = coverage.Coverage.current() if coverage else None
    child_coverage = owner.root.parent / "fork-coverage"
    fd = os.open(owner.root / "root.lock", os.O_RDWR | os.O_CLOEXEC)
    fcntl.flock(fd, fcntl.LOCK_EX)
    read_fd, write_fd = os.pipe()
    owner._mutex.acquire()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_fd)
            ack = owner.open()
            os.write(
                write_fd, b"identity_conflict" if ack.fault.code == "identity_conflict" else b"wrong"
            )
            os.close(fd)
            os.close(write_fd)
        finally:
            if collector is not None:
                measured = coverage.CoverageData(basename=str(child_coverage))
                measured.update(collector.get_data())
                measured.write()
            os._exit(0)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 2)[0]
        assert os.read(read_fd, 64) == b"identity_conflict"
        os.waitpid(child_pid, 0)
        if collector is not None:
            measured = coverage.CoverageData(basename=str(child_coverage))
            measured.read()
            collector.get_data().update(measured)
        contender = os.open(owner.root / "root.lock", os.O_RDWR | os.O_CLOEXEC)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
    finally:
        owner._mutex.release()
        os.close(read_fd)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_real_tombstone_charge_plus_32_envelopes_refuses(disk_root):
    tiny = other_owner(disk_root, str(uuid.uuid4()))
    assert tiny.open().admitted
    assert tiny.finalize("completed").admitted
    assert audit.maintain_audit_root(
        disk_root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
    ).admitted
    assert (disk_root / tiny._context["run_id"] / "tombstone").exists()
    for _ in range(31):
        assert other_owner(disk_root, str(uuid.uuid4())).open().admitted
    denied = other_owner(disk_root, str(uuid.uuid4())).open()
    assert not denied.admitted and denied.fault.code == "quota_refused"


def test_real_31_full_two_four_mib_terminal_freeze_refuses(disk_root):
    terminal = []
    for _ in range(2):
        owner = other_owner(disk_root, str(uuid.uuid4()))
        assert owner.open().admitted
        for _ in range(16):
            ctx = context(run_id=owner._context["run_id"])
            ack = owner.start(
                ctx,
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=100,
            )
            assert ack.admitted
            assert acquire(owner, ack.attempt_id, usage(0, 0, 0, 0), data=b"x" * (256 * 1024)).admitted
            assert finish(owner, ack.attempt_id).admitted
        assert owner.finalize("completed").admitted
        terminal.append(owner)
    for _ in range(31):
        assert other_owner(disk_root, str(uuid.uuid4())).open().admitted
    owner = terminal[0]
    before = owner.snapshot()
    run = disk_root / owner._context["run_id"]
    disk = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()}
    denied = owner.set_frozen(True)
    assert not denied.admitted and denied.fault.code == "quota_refused"
    assert owner.snapshot() == before
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir()} == disk
    reopened = audit.AttemptRecorder.reopen(disk_root, owner._context["run_id"])
    assert reopened.summary == before.summary
    assert owner.set_frozen(True) == denied


def test_root_descriptor_close_failure_is_not_an_admitted_ack(recorder, monkeypatch):
    owner, _ = recorder
    close = audit._RootHandle.close

    def broken_close(handle):
        close(handle)
        raise OSError("close diagnostic")

    monkeypatch.setattr(audit._RootHandle, "close", broken_close)
    ack = owner.start(
        context(),
        logical_id=LOGICAL,
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not ack.admitted and ack.attempt_id is None and ack.fault.code == "persistence_error"
    assert not owner.snapshot().audit_complete


@pytest.mark.parametrize(
    "field,bad",
    [
        ("run_id", "no"),
        ("source_hash", "short"),
        ("snapshot_id", "no"),
        ("round_index", True),
        ("round_index", -1),
        ("pass_name", ""),
        ("group_id", "no"),
        ("group_diff_sha256", "short"),
        ("purpose", object()),
        ("parent_logical_id", "no"),
    ],
)
def test_invalid_context_field_never_admits(disk_root, field, bad):
    owner = audit.AttemptRecorder(
        disk_root, context=context(**{field: bad}), monotonic=Clock(), utc_now=utc
    )
    denied = owner.open()
    assert not denied.admitted and denied.fault.code == "invalid_input"
    assert not disk_root.exists()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("format", "custom"),
        ("requested_backend", {}),
        ("requested_model", "https://secret.invalid?q=key"),
        ("requested_effort", object()),
        ("endpoint_fingerprint", "https://secret.invalid"),
        ("observed_model", []),
        ("observed_backend", "x" * 129),
    ],
)
def test_closed_backend_inputs_do_not_serialize_caller_objects(recorder, field, bad):
    owner, _ = recorder
    projected = dataclasses.replace(BACKEND, **{field: bad})
    denied = owner.start(
        context(),
        logical_id=LOGICAL,
        parent_id=None,
        cause="initial",
        backend=projected,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not denied.admitted and denied.fault.code == "invalid_input"
    assert owner.summary()["admitted_attempt_count"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"native_usage": {"authorization": 3}},
        {"native_usage": {"input_tokens": True}},
        {"native_usage": {"input_tokens": {"secret": 1}}},
        {"availability": "known"},
        {"snapshot_kind": "opaque"},
        {"input_semantics": "opaque"},
        {"scope": "opaque"},
        {"observation_source": "provider-authority"},
        {"input_tokens": 1, "cached_input_tokens": 2},
    ],
)
def test_closed_usage_native_projection_and_subsets(recorder, changes):
    owner, _ = recorder
    attempt = start(owner)
    sample = usage(None, None, None, None)
    if "input_tokens" in changes:
        sample = usage(1, 0, 2, 0)
    denied = acquire(owner, attempt, dataclasses.replace(sample, **changes))
    assert not denied.admitted and denied.ref is None and denied.fault.code == "invalid_input"


def test_final_cumulative_seals_without_adding_and_missing_fields_do_not_erase(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(5, 4, 2, 1, kind="cumulative")).admitted
    assert acquire(owner, attempt, usage(None, 6, None, None, kind="cumulative")).admitted
    assert acquire(owner, attempt, usage(5, 6, 2, 1, kind="final")).admitted
    assert finish(owner, attempt).admitted
    totals = owner.summary()["usage"]
    assert totals["input_tokens"] == field(5, 5)
    assert totals["output_tokens"] == field(6, 6)
    assert totals["cached_input_tokens"] == field(2, 2)


def test_read_only_missing_root_and_invalid_run_do_not_create(disk_root):
    assert not audit.AttemptRecorder.reopen(disk_root, RUN).audit_complete
    assert not disk_root.exists()
    ack = audit.maintain_audit_root(disk_root, monotonic=Clock(), utc_now=utc, action_deadline=100)
    assert not ack.admitted and not disk_root.exists()
    assert not audit.AttemptRecorder.reopen(disk_root, object()).audit_complete


def test_native_usage_is_host_copied_before_caller_mutation(recorder):
    owner, _ = recorder
    attempt = start(owner)
    native = {"prompt_tokens": 3, "completion_tokens": 0}
    observation = dataclasses.replace(usage(3, 0, 0, 0), native_usage=native)
    assert acquire(owner, attempt, observation).admitted
    native["prompt_tokens"] = 999
    assert events(owner)[-1]["usage"]["native_usage"] == {"prompt_tokens": 3, "completion_tokens": 0}


@pytest.mark.parametrize("change", ["missing_context", "cross_group"])
def test_tampered_start_context_is_not_invented_on_reopen(recorder, change):
    owner, _ = recorder
    attempt = start(owner)
    rows = events(owner)
    if change == "missing_context":
        rows[1].pop("context")
    else:
        rows[1]["context"]["source_hash"] = "d" * 64
    encoded = []
    for row in rows:
        payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        encoded.append(("%08x " % len(payload)).encode() + payload + b"\n")
    path = owner.root / RUN / "journal"
    path.write_bytes(b"".join(encoded))
    before = path.read_bytes()
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not reopened.audit_complete and not reopened.attempt_contexts
    assert path.read_bytes() == before and attempt not in reopened.attempt_contexts


def test_reused_run_descriptor_inventory_sees_new_committed_raw(recorder):
    owner, _ = recorder
    attempt = start(owner)
    fd = os.open(owner.root / RUN, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        before = audit._disk_run(fd, RUN)["ordinary"]
        assert acquire(owner, attempt, None, data=b"x" * 10000).admitted
        after = audit._disk_run(fd, RUN)["ordinary"]
        assert after > before + 10000
        assert after == sum(p.stat().st_size for p in (owner.root / RUN).iterdir())
    finally:
        os.close(fd)


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_exact_encoded_start_event_cap(recorder, delta):
    owner, _ = recorder
    initial = start(owner)
    template = events(owner)[1]
    base = len(json.dumps(template, sort_keys=True, separators=(",", ":")).encode()) + 10
    digits = 2048 + delta - base + 1
    large = 10 ** (digits - 1)
    ctx = context()
    denied_or_admitted = owner.start(
        ctx,
        logical_id=str(uuid.uuid4()),
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=large,
        action_deadline=100,
    )
    assert denied_or_admitted.admitted == (delta <= 0)
    if delta <= 0:
        row = events(owner)[-1]
        assert len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()) + 10 == 2048 + delta
        assert set(row["context"]) == CONTEXT_KEYS
    else:
        assert denied_or_admitted.fault.code == "quota_refused"
        assert owner.summary()["admitted_attempt_count"] == 1
    assert initial in owner.snapshot().attempt_contexts


def test_fsync_deadline_after_start_commit_refuses_dispatch(recorder, monkeypatch):
    owner, clock = recorder
    old_append = audit._append

    def delayed(fd, event):
        old_append(fd, event)
        if event["event"] == "start":
            clock.value = 101

    monkeypatch.setattr(audit, "_append", delayed)
    denied = owner.start(
        context(),
        logical_id=LOGICAL,
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not denied.admitted and denied.attempt_id is None and denied.fault.code == "lock_timeout"
    durable = events(owner)
    assert [event["sequence"] for event in durable] == list(range(len(durable)))
    attempt = next(event["attempt_id"] for event in durable if event["event"] == "start")
    assert attempt in owner.snapshot().attempt_contexts
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not replay.audit_complete and attempt in replay.attempt_contexts
    assert owner.summary()["observed_api_send_count"] == 0
    assert finish(owner, attempt, outcome="refused", dispatch="not_dispatched").admitted
    assert owner.finalize("failed").admitted
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


def _filler(root, target_charge, *, terminal=True):
    owner = other_owner(root, str(uuid.uuid4()))
    assert owner.open().admitted
    run_id = owner._context["run_id"]
    run = root / run_id
    while True:
        current = sum(p.stat().st_size for p in run.iterdir())
        logical = str(uuid.uuid4())
        ack = owner.start(
            context(run_id=run_id),
            logical_id=logical,
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=100,
        )
        assert ack.admitted
        rows = []
        data = (run / "journal").read_bytes()
        offset = 0
        while offset < len(data):
            size = int(data[offset : offset + 8], 16)
            rows.append(json.loads(data[offset + 9 : offset + 9 + size]))
            offset += size + 10
        common = {
            "schema_version": 1,
            "run_id": run_id,
            "event_id": RUN,
            "sequence": len(rows),
            "utc": utc(),
            "elapsed_s": 0.0,
        }
        remaining = target_charge - 16 * 1024 - sum(p.stat().st_size for p in run.iterdir())

        def overhead(size, common=common, ack=ack, rows=rows):
            ref = {
                "artifact_id": RUN,
                "layer": "wire",
                "sha256": "a" * 64,
                "retained_bytes": size,
                "original_bytes": size,
                "partial": False,
            }
            acquired = {
                **common,
                "event": "acquired",
                "attempt_id": ack.attempt_id,
                "observation_id": RUN,
                "layer": "wire",
                "ref": ref,
                "complete": True,
                "usage": None,
            }
            final = {
                **common,
                "sequence": len(rows) + 1,
                "event": "finish",
                "attempt_id": ack.attempt_id,
                "outcome": "completed",
                "error_class": None,
                "duration_s": 1.0,
                "dispatch_state": "entered_api_send",
            }
            return sum(
                len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()) + 10
                for row in (acquired, final)
            )

        size = min(256 * 1024, max(0, remaining - overhead(256 * 1024)))
        for _ in range(3):
            size = min(256 * 1024, remaining - overhead(size))
        assert size >= 0 and current < target_charge - 16 * 1024
        assert acquire(owner, ack.attempt_id, None, data=b"x" * size).admitted
        assert finish(owner, ack.attempt_id).admitted
        retained = sum(p.stat().st_size for p in run.iterdir())
        if retained == target_charge - 16 * 1024:
            break
        assert retained < target_charge - 16 * 1024
    if terminal:
        assert owner.finalize("completed").admitted
    return owner


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_actual_terminal_freeze_post_delta_root_n_minus_one_n_plus_one(disk_root, delta):
    _filler(disk_root, 8 * 1024 * 1024)
    _filler(disk_root, 8 * 1024 * 1024 + delta)
    target = other_owner(disk_root, str(uuid.uuid4()))
    assert target.open().admitted
    assert target.finalize("completed").admitted
    for _ in range(30):
        assert other_owner(disk_root, str(uuid.uuid4())).open().admitted
    before = target.snapshot()
    run = disk_root / target._context["run_id"]
    bytes_before = {p.name: p.read_bytes() for p in run.iterdir()}
    ack = target.set_frozen(True)
    assert ack.admitted == (delta <= 0)
    handle = audit._prepare_audit_root(disk_root, create=False)
    try:
        with audit._root_lock(handle):
            charge = audit._inventory(handle)[1]
    finally:
        handle.close()
    if delta <= 0:
        assert charge == 512 * 1024 * 1024 + delta
    else:
        assert ack.fault == audit.AuditFault("quota_refused", "freeze", None)
        assert target.snapshot() == before
        assert {p.name: p.read_bytes() for p in run.iterdir()} == bytes_before
        assert charge < 512 * 1024 * 1024
        assert target.set_frozen(True) == ack


@pytest.mark.parametrize("competitor", ["freeze", "new_run"])
def test_actual_freeze_competes_for_one_delta_slot(disk_root, competitor):
    import subprocess

    run_ids = [str(uuid.uuid4()) for _ in range(2 if competitor == "freeze" else 1)]

    def freezer(run_id):
        return (
            "import sys\nfrom pathlib import Path\nimport code_forge.invocation_audit as a\n"
            "r=a.AttemptRecorder(Path("
            + repr(str(disk_root))
            + "),context=a.InvocationContext("
            + repr(run_id)
            + ',None,None,None,None,None,None,"fixture",None),monotonic=lambda:0.0,'
            'utc_now=lambda:"2026-10-06T12:00:00Z")\n'
            'assert r.open().admitted\nassert r.finalize("completed").admitted\n'
            'print("ready",flush=True)\nsys.stdin.readline()\n'
            'ack=r.set_frozen(True)\nprint("admitted" if ack.admitted else ack.fault.code,flush=True)\n'
        )

    children = []
    try:
        for run_id in run_ids:
            child = subprocess.Popen(
                ["/usr/bin/python3", "-B", "-c", freezer(run_id)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
            assert child.stdout.readline() == "ready\n"
        handle = audit._prepare_audit_root(disk_root, create=False)
        try:
            with audit._root_lock(handle):
                rows, _ = audit._inventory(handle)
                q = max(rows[n]["charge"] for n in run_ids)
        finally:
            handle.close()
        _filler(disk_root, 16 * 1024 * 1024 - q)
        for _ in range(30):
            assert other_owner(disk_root, str(uuid.uuid4())).open().admitted
        if competitor == "new_run":
            child = subprocess.Popen(
                ["/usr/bin/python3", "-B", "-c", _child_source(disk_root, str(uuid.uuid4()))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
            assert child.stdout.readline() == "ready\n"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = [child.communicate(timeout=5) for child in children]
        assert sorted(out.strip() for out, err in results) == ["admitted", "quota_refused"]
        assert all(
            not err and child.returncode == 0
            for child, (out, err) in zip(children, results, strict=True)
        )
        handle = audit._prepare_audit_root(disk_root, create=False)
        try:
            with audit._root_lock(handle):
                assert audit._inventory(handle)[1] <= 512 * 1024 * 1024
        finally:
            handle.close()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()


def test_owner_can_settle_after_raw_sink_failure(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)

    def cut(*args, **kwargs):
        raise OSError("raw rename")

    with monkeypatch.context() as failure:
        failure.setattr(os, "rename", cut)
        assert not acquire(owner, attempt, None, data=b"partial owned evidence").admitted
    assert finish(owner, attempt, outcome="incomplete", dispatch="unknown", duration=None).admitted
    assert owner.finalize("incomplete").admitted
    assert not owner.snapshot().audit_complete
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not reopened.audit_complete and reopened.summary["outcome_counts"]["incomplete"] == 1


@pytest.mark.parametrize("failure", ["none", "unlink", "directory_sync", "root_sync"])
def test_expired_tombstone_keeps_metadata_and_charge(recorder, monkeypatch, failure):
    owner, _ = recorder
    assert owner.finalize("completed").admitted
    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
    ).admitted
    before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            charge = audit._inventory(handle)[1]
    finally:
        handle.close()
    old_unlink, old_sync, old_rmdir = os.unlink, os.fsync, os.rmdir
    deleted, removed = [], []

    def unlink(name, *args, **kwargs):
        if failure == "unlink" and deleted:
            raise OSError("purge unlink")
        answer = old_unlink(name, *args, **kwargs)
        deleted.append(name)
        return answer

    def rmdir(name, *args, **kwargs):
        answer = old_rmdir(name, *args, **kwargs)
        removed.append(name)
        return answer

    def sync(fd):
        if (
            failure == "directory_sync"
            and deleted
            and not removed
            and stat.S_ISDIR(os.fstat(fd).st_mode)
        ):
            raise OSError("purge directory sync")
        if (
            failure == "root_sync"
            and removed
            and os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root)
        ):
            raise OSError("purge root sync")
        return old_sync(fd)

    with monkeypatch.context() as cut:
        cut.setattr(os, "unlink", unlink)
        cut.setattr(os, "rmdir", rmdir)
        cut.setattr(os, "fsync", sync)
        ack = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-23T12:00:00Z", action_deadline=100
        )
    assert ack.admitted
    assert not deleted and not removed, "terminal metadata is retained without a final purge"
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            after = audit._inventory(handle)[1]
    finally:
        handle.close()
    assert after == charge
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before


def test_young_terminal_is_not_pruned(recorder):
    owner, _ = recorder
    assert owner.finalize("completed").admitted
    before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=utc, action_deadline=100
    ).admitted
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before


def test_expired_tombstone_is_visible_to_fresh_recorder_without_recreation(recorder, monkeypatch):
    owner, _ = recorder
    assert owner.finalize("completed").admitted
    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
    ).admitted
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            before = audit._inventory(handle)[1]
    finally:
        handle.close()
    removed = []
    old_rmdir, old_sync, old_mkdir = os.rmdir, os.fsync, os.mkdir

    def rmdir(name, *args, **kwargs):
        answer = old_rmdir(name, *args, **kwargs)
        removed.append(name)
        return answer

    def sync(fd):
        if removed and os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root):
            raise OSError("post-rmdir root sync")
        return old_sync(fd)

    def mkdir(name, *args, **kwargs):
        if removed and name == RUN:
            raise OSError("exact-name recreation also fails")
        return old_mkdir(name, *args, **kwargs)

    with monkeypatch.context() as cut:
        cut.setattr(os, "rmdir", rmdir)
        cut.setattr(os, "fsync", sync)
        cut.setattr(os, "mkdir", mkdir)
        ack = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-23T12:00:00Z", action_deadline=100
        )
    assert ack.admitted and not removed
    fresh = audit.AttemptRecorder(
        owner.root,
        context=dataclasses.replace(context(), run_id=str(uuid.uuid4())),
        monotonic=Clock(),
        utc_now=utc,
    )
    assert fresh.open().admitted
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            observed = audit._inventory(handle)[1]
    finally:
        handle.close()
    assert observed >= before + 16 * 1024 * 1024, (
        "fresh owner must also retain the failed-deletion charge"
    )


def _rewrite_journal(owner, rows):
    frames = []
    for row in rows:
        payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        frames.append(("%08x " % len(payload)).encode() + payload + b"\n")
    (owner.root / RUN / "journal").write_bytes(b"".join(frames))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("artifact_id", "f" * 36),
        ("sha256", "invalid"),
        ("retained_bytes", True),
        ("original_bytes", -1),
        ("partial", "true"),
        ("authorization", "secret"),
    ],
)
def test_raw_reference_disk_schema_is_closed(recorder, field, bad):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, None, data=b"exact").admitted
    rows = events(owner)
    rows[-1]["ref"][field] = bad
    _rewrite_journal(owner, rows)
    snapshot = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not snapshot.audit_complete
    assert snapshot.attempt_contexts[attempt] == context(), (
        "valid start prefix survives a later malformed record"
    )


def test_reopen_late_invalid_context_preserves_earlier_valid_attempt(recorder):
    owner, _ = recorder
    first = start(owner)
    second = start(
        owner,
        context(parent_logical_id=LOGICAL),
        logical=str(uuid.uuid4()),
        parent=first,
        cause="correction",
    )
    rows = events(owner)
    rows[-1]["context"]["purpose"] = "https://endpoint/?token=secret"
    _rewrite_journal(owner, rows)
    snapshot = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not snapshot.audit_complete
    assert dict(snapshot.attempt_contexts) == {first: context()}
    assert second not in snapshot.attempt_contexts


@pytest.mark.parametrize("name,payload", [("admission", b"{"), ("final", b"{"), ("unknown", b"x")])
def test_malformed_disk_metadata_refuses_readiness(recorder, name, payload):
    owner, _ = recorder
    if name == "final":
        assert owner.finalize("completed").admitted
    path = owner.root / RUN / name
    path.write_bytes(payload)
    path.chmod(0o600)
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


def test_duplicate_journal_frame_contributes_once(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(3, 0, 0, 0)).admitted
    assert finish(owner, attempt).admitted
    before = owner.summary()
    path = owner.root / RUN / "journal"
    raw = path.read_bytes()
    frame = raw.splitlines(keepends=True)[-1]
    with path.open("ab") as stream:
        stream.write(frame)
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary == before


def test_zero_write_refuses_and_preserves_known_resources(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    with monkeypatch.context() as cut:
        cut.setattr(os, "write", lambda *args: 0)
        ack = acquire(owner, attempt, None, data=b"x")
    assert not ack.admitted and ack.ref is None
    assert not owner.snapshot().audit_complete
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


@pytest.mark.parametrize("bad", ["f" * 36, "not-a-date"])
def test_malformed_uuid_or_timestamp_refuses_before_open(disk_root, bad):
    if len(bad) == 36:
        ctx, timestamp = context(run_id=bad), utc
    else:
        ctx, timestamp = context(), lambda: bad
    owner = audit.AttemptRecorder(disk_root, context=ctx, monotonic=Clock(), utc_now=timestamp)
    assert not owner.open().admitted and not disk_root.exists()


def test_reopen_close_failure_is_typed_and_incomplete(recorder, monkeypatch):
    owner, _ = recorder
    original = audit._RootHandle.close

    def cut(handle):
        original(handle)
        raise OSError("replay root close")

    with monkeypatch.context() as failure:
        failure.setattr(audit._RootHandle, "close", cut)
        observed = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not observed.audit_complete and observed.faults[-1].code == "persistence_error"


def test_missing_owned_lock_is_not_recreated_by_read_only_replay(recorder):
    owner, _ = recorder
    (owner.root / "root.lock").unlink()
    before = tuple(owner.root.iterdir())
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    assert tuple(owner.root.iterdir()) == before


@pytest.mark.parametrize("site", ["event", "admission", "root_sync"])
def test_open_commit_each_required_site_is_wired(disk_root, monkeypatch, site):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    original_append, original_publish, original_sync = audit._append, audit._publish, os.fsync

    def append(fd, event):
        if site == "event" and event["event"] == "run_open":
            raise OSError("run-open event")
        return original_append(fd, event)

    def publish(fd, name, data, **kwargs):
        if site == "admission" and name == "admission":
            raise OSError("admission publication")
        return original_publish(fd, name, data, **kwargs)

    def sync(fd):
        if (
            site == "root_sync"
            and (disk_root / RUN / "admission").exists()
            and os.readlink("/proc/self/fd/" + str(fd)) == str(disk_root)
        ):
            raise OSError("admitted-directory root sync")
        return original_sync(fd)

    monkeypatch.setattr(audit, "_append", append)
    monkeypatch.setattr(audit, "_publish", publish)
    monkeypatch.setattr(os, "fsync", sync)
    ack = owner.open()
    assert not ack.admitted and ack.fault is not None
    assert not owner.snapshot().audit_complete


@pytest.mark.parametrize("site", ["event", "marker", "directory_sync", "root_sync"])
def test_finalize_commit_each_required_site_is_wired(recorder, monkeypatch, site):
    owner, _ = recorder
    original_append, original_publish, original_sync = audit._append, audit._publish, os.fsync
    published = []

    def append(fd, event):
        if site == "event" and event["event"] == "run_final":
            raise OSError("terminal event")
        return original_append(fd, event)

    def publish(fd, name, data, **kwargs):
        if site == "marker" and name == "final":
            raise OSError("terminal marker")
        answer = original_publish(fd, name, data, **kwargs)
        if name == "final":
            published.append(name)
        return answer

    def sync(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        if published and (
            (site == "directory_sync" and path == str(owner.root / RUN))
            or (site == "root_sync" and path == str(owner.root))
        ):
            raise OSError("post-terminal sync")
        return original_sync(fd)

    monkeypatch.setattr(audit, "_append", append)
    monkeypatch.setattr(audit, "_publish", publish)
    monkeypatch.setattr(os, "fsync", sync)
    assert not owner.finalize("completed").admitted
    assert not owner.snapshot().audit_complete


def test_finish_event_commit_is_wired(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    original = audit._append

    def cut(fd, event):
        if event["event"] == "finish":
            raise OSError("finish event")
        return original(fd, event)

    monkeypatch.setattr(audit, "_append", cut)
    assert not finish(owner, attempt).admitted
    assert owner.summary()["outcome_counts"]["completed"] == 0


def test_invalid_availability_is_a_typed_observation_fault(recorder):
    owner, _ = recorder
    attempt = start(owner)
    bad = usage(None, None, None, None, availability="invalid")
    ack = acquire(owner, attempt, bad)
    assert not ack.admitted and ack.fault.code == "invalid_input"
    assert finish(owner, attempt, outcome="incomplete", dispatch="unknown", duration=None).admitted
    assert owner.finalize("incomplete").admitted
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


def test_frozen_disk_replays_immutable_snapshot(recorder):
    owner, _ = recorder
    assert owner.set_frozen(True).admitted
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary == owner.summary()


def test_conflicting_observation_identity_on_disk_preserves_prefix(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(3, 0, 0, 0)).admitted
    before = events(owner)
    altered = json.loads(json.dumps(before[-1]))
    altered["event_id"] = str(uuid.uuid4())
    altered["sequence"] += 1
    altered["usage"]["input_tokens"] = 4
    _rewrite_journal(owner, before + [altered])
    observed = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not observed.audit_complete
    assert observed.summary["observation_count"] == 1
    assert observed.summary["usage"]["input_tokens"]["known_subtotal"] == 3


def test_callback_value_error_and_lock_io_error_return_typed_fault(recorder, monkeypatch):
    import fcntl

    owner, _ = recorder

    def bad_time():
        raise ValueError("untrusted callback message")

    owner._utc_now = bad_time
    ack = owner.start(
        context(),
        logical_id=LOGICAL,
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not ack.admitted and ack.fault.code == "invalid_input"
    owner._utc_now = utc

    def bad_lock(*args):
        raise OSError("kernel lock I/O")

    monkeypatch.setattr(fcntl, "flock", bad_lock)
    ack = owner.open()
    assert not ack.admitted and ack.fault.code == "persistence_error"


@pytest.mark.parametrize("unbound", [False, True])
def test_single_start_context_disk_and_reopen_golden(recorder, unbound):
    owner, _ = recorder
    # Role attribution varies without changing the standalone source identity.
    ctx = (
        context(round_index=None, pass_name=None, group_id=None, group_diff_sha256=None)
        if unbound
        else context()
    )
    attempt = start(owner, ctx)
    expected = {
        "run_id": RUN,
        "source_hash": None,
        "snapshot_id": None,
        "round_index": None if unbound else 0,
        "pass_name": None if unbound else "qodo-review",
        "group_id": None if unbound else GROUP,
        "group_diff_sha256": None if unbound else "c" * 64,
        "purpose": "review",
        "parent_logical_id": None,
    }
    assert events(owner)[1]["context"] == expected
    snap = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert dataclasses.asdict(snap.attempt_contexts[attempt]) == expected
    assert snap.summary["usage"]["input_tokens"] == {
        "known_subtotal": 0,
        "total": None,
        "unknown_attempt_count": 1,
        "invalid_attempt_count": 0,
    }


@pytest.mark.parametrize("site", ["journal_file_sync", "journal_name_sync"])
def test_open_journal_sync_precedes_usable_admission(disk_root, monkeypatch, site):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    original = os.fsync

    def cut(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        if site == "journal_file_sync" and path.endswith("/journal"):
            raise OSError("journal file sync")
        if (
            site == "journal_name_sync"
            and path == str(disk_root / RUN)
            and (disk_root / RUN / "journal").exists()
            and not (disk_root / RUN / "admission").exists()
        ):
            raise OSError("journal name sync")
        return original(fd)

    monkeypatch.setattr(os, "fsync", cut)
    assert not owner.open().admitted


def test_pending_terminal_marker_temp_is_full_and_never_ready(recorder):
    owner, _ = recorder
    path = owner.root / RUN / ("temp-final-" + str(uuid.uuid4()))
    path.write_bytes(b"{")
    path.chmod(0o600)
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            assert audit._inventory(handle)[1] == 16 * 1024 * 1024
    finally:
        handle.close()


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_actual_ordinary_run_encoded_boundary(disk_root, delta):
    template = {
        "schema_version": 1,
        "run_id": RUN,
        "event_id": RUN,
        "sequence": 999,
        "event": "start",
        "utc": utc(),
        "elapsed_s": 0.0,
        "context": dataclasses.asdict(context()),
        "attempt_id": RUN,
        "logical_id": RUN,
        "parent_id": None,
        "cause": "initial",
        "backend": dataclasses.asdict(BACKEND),
        "request_digest": DIGEST,
        "request_bytes": 7,
    }
    prospective = len(json.dumps(template, sort_keys=True, separators=(",", ":")).encode()) + 10
    owner = _filler(disk_root, 16 * 1024 * 1024 - prospective + delta, terminal=False)
    run_id = owner.summary()["run_id"]
    rows = [
        json.loads(line[9:-1])
        for line in (disk_root / run_id / "journal").read_bytes().splitlines(keepends=True)
    ]
    assert len(str(len(rows))) == 3, "independent encoded-size fixture has the actual sequence width"
    before = sum(p.stat().st_size for p in (disk_root / run_id).iterdir())
    ack = owner.start(
        context(run_id=run_id),
        logical_id=str(uuid.uuid4()),
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert ack.admitted == (delta <= 0)
    if delta <= 0:
        after = sum(p.stat().st_size for p in (disk_root / run_id).iterdir())
        assert after == before + prospective == 16 * 1024 * 1024 - 16 * 1024 + delta
    else:
        assert ack.attempt_id is None and ack.fault.code == "quota_refused"
        assert owner.summary()["admitted_attempt_count"] == len(
            [r for r in rows if r["event"] == "start"]
        )


def test_refusal_marker_cannot_exceed_reserved_partition(recorder):
    owner, _ = recorder
    path = owner.root / RUN / ("temp-fault-" + str(uuid.uuid4()))
    path.write_bytes(b"x" * (16 * 1024 - 100))
    path.chmod(0o600)
    before = (owner.root / RUN / "journal").read_bytes()
    ack = owner.start(
        context(purpose="https://invalid"),
        logical_id=LOGICAL,
        parent_id=None,
        cause="initial",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not ack.admitted
    assert (owner.root / RUN / "journal").read_bytes() == before
    assert path.read_bytes() == b"x" * (16 * 1024 - 100)
    assert not (owner.root / RUN / "fault").exists()


def test_maintenance_close_failure_is_typed_after_known_resource_closure(recorder, monkeypatch):
    owner, _ = recorder
    original = audit._RootHandle.close
    closed = []

    def cut(handle):
        original(handle)
        closed.append(handle.fd)
        raise OSError("maintenance root close")

    with monkeypatch.context() as failure:
        failure.setattr(audit._RootHandle, "close", cut)
        ack = audit.maintain_audit_root(owner.root, monotonic=Clock(), utc_now=utc, action_deadline=100)
    assert not ack.admitted and ack.fault.code == "persistence_error"
    assert closed == [-1]


def _fd_observations():
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            result[name] = os.readlink("/proc/self/fd/" + name)
        except FileNotFoundError:
            pass
    return result


@pytest.mark.parametrize(
    "phase", ["start", "acquired", "finish", "finalize", "freeze", "reopen", "maintenance", "inventory"]
)
def test_owned_descriptors_close_before_return_after_each_sink_fault(recorder, monkeypatch, phase):
    owner, _ = recorder
    attempt = start(owner)
    if phase == "maintenance":
        assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    elif phase == "finalize":
        assert finish(owner, attempt).admitted
    before = _fd_observations()
    original_sync, original_read, original_list = os.fsync, os.read, os.listdir

    def sync(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        if path.startswith(str(owner.root / RUN)):
            raise OSError("owned sink sync")
        return original_sync(fd)

    def read(fd, size):
        if os.readlink("/proc/self/fd/" + str(fd)).endswith("/journal"):
            raise OSError("owned reader")
        return original_read(fd, size)

    def listing(fd):
        if type(fd) is int and os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root):
            raise OSError("fresh inventory description")
        return original_list(fd)

    with monkeypatch.context() as cut:
        cut.setattr(os, "fsync", sync)
        if phase == "reopen":
            cut.setattr(os, "read", read)
            assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
        elif phase == "inventory":
            cut.setattr(os, "listdir", listing)
            assert not owner.start(
                context(),
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=100,
            ).admitted
        elif phase == "start":
            assert not owner.start(
                context(),
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=100,
            ).admitted
        elif phase == "acquired":
            assert not acquire(owner, attempt, None).admitted
        elif phase == "finish":
            assert not finish(owner, attempt).admitted
        elif phase == "finalize":
            assert not owner.finalize("completed").admitted
        elif phase == "freeze":
            assert not owner.set_frozen(True).admitted
        else:
            assert not audit.maintain_audit_root(
                owner.root,
                monotonic=Clock(),
                utc_now=lambda: "2026-10-14T12:00:00Z",
                action_deadline=100,
            ).admitted
    assert _fd_observations() == before


def test_failed_root_preparation_closes_observed_descriptors(disk_root, monkeypatch):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    before = _fd_observations()

    def cut(fd):
        raise audit._Rejected("unsupported_profile")

    monkeypatch.setattr(audit, "_profile", cut)
    assert not owner.open().admitted
    assert _fd_observations() == before


@pytest.mark.parametrize("site", ["ancestor_handoff", "failed_root_cleanup"])
def test_root_prepare_close_error_keeps_next_descriptor_owned_and_typed(disk_root, monkeypatch, site):
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=Clock(), utc_now=utc)
    before = _fd_observations()
    original = os.close
    cuts = []

    def close(fd):
        try:
            path = os.readlink("/proc/self/fd/" + str(fd))
        except FileNotFoundError:
            return original(fd)
        selected = str(disk_root.parent) if site == "ancestor_handoff" else str(disk_root)
        if path == selected and not cuts:
            original(fd)
            cuts.append(path)
            raise OSError("close reported after release")
        return original(fd)

    def profile(fd):
        raise audit._Rejected("unsupported_profile")

    ack = None
    with monkeypatch.context() as cut:
        cut.setattr(os, "close", close)
        if site == "failed_root_cleanup":
            cut.setattr(audit, "_profile", profile)
        try:
            ack = owner.open()
        except OSError:
            pass
    assert cuts
    assert _fd_observations() == before, (
        "the newly opened descriptor stays owned during ancestor handoff"
    )
    assert ack is not None and not ack.admitted and ack.fault is not None


@pytest.mark.parametrize(
    "total,subset", [("input_tokens", "cached_input_tokens"), ("output_tokens", "reasoning_tokens")]
)
def test_partial_included_subset_conflict_refuses_and_preserves_prior_live_and_disk(
    recorder, total, subset
):
    owner, _ = recorder
    attempt = start(owner)
    first = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0}
    first.update({total: 10, subset: 5})
    known = usage(
        first["input_tokens"],
        first["output_tokens"],
        first["cached_input_tokens"],
        first["reasoning_tokens"],
        kind="cumulative",
    )
    ack = acquire(owner, attempt, known, data=b"prior exact response")
    assert ack.admitted
    raw = owner.root / RUN / ("raw-" + ack.ref.artifact_id)
    before = raw.read_bytes()
    second = dict(first, **{total: None, subset: 20})
    partial = usage(
        second["input_tokens"],
        second["output_tokens"],
        second["cached_input_tokens"],
        second["reasoning_tokens"],
        kind="cumulative",
        availability="partial",
    )
    denied = acquire(owner, attempt, partial, data=b"conflicting caller response")
    assert not denied.admitted and denied.ref is None and denied.observation_id is None
    assert denied.fault.code == "invalid_input"
    assert raw.read_bytes() == before == b"prior exact response"
    assert finish(owner, attempt).admitted
    assert owner.finalize("incomplete").admitted
    live = owner.snapshot()
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not live.audit_complete and not reopened.audit_complete
    assert live.summary == reopened.summary
    assert live.summary["observation_count"] == 1
    for field, expected in ((total, 10), (subset, 5)):
        assert live.summary["usage"][field] == {
            "known_subtotal": expected,
            "total": None,
            "unknown_attempt_count": 0,
            "invalid_attempt_count": 1,
        }


@pytest.mark.parametrize(
    "total,subset", [("input_tokens", "cached_input_tokens"), ("output_tokens", "reasoning_tokens")]
)
def test_partial_included_subset_conflict_on_disk_preserves_valid_measured_prefix(
    recorder, total, subset
):
    owner, _ = recorder
    attempt = start(owner)
    values = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0}
    values.update({total: 10, subset: 5})
    assert acquire(
        owner,
        attempt,
        usage(
            values["input_tokens"],
            values["output_tokens"],
            values["cached_input_tokens"],
            values["reasoning_tokens"],
            kind="cumulative",
        ),
        data=b"first response",
    ).admitted
    values[subset] = 6
    assert acquire(
        owner,
        attempt,
        usage(
            values["input_tokens"],
            values["output_tokens"],
            values["cached_input_tokens"],
            values["reasoning_tokens"],
            kind="cumulative",
        ),
        data=b"second response",
    ).admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    raw_before = {
        p.name: p.read_bytes() for p in (owner.root / RUN).iterdir() if p.name.startswith("raw-")
    }
    rows = events(owner)
    last = [e for e in rows if e["event"] == "acquired"][-1]
    last["usage"].update({total: None, subset: 20, "availability": "partial"})
    _rewrite_journal(owner, rows)
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not reopened.audit_complete and reopened.state == "incomplete"
    assert reopened.attempt_contexts[attempt] == context()
    assert reopened.summary["usage"][total]["known_subtotal"] == 10
    assert reopened.summary["usage"][subset]["known_subtotal"] == 5
    assert reopened.summary["usage"][total]["total"] is None
    assert reopened.summary["usage"][subset]["total"] is None
    assert reopened.summary["usage"][total]["invalid_attempt_count"] == 1
    assert {
        p.name: p.read_bytes() for p in (owner.root / RUN).iterdir() if p.name.startswith("raw-")
    } == raw_before
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                disk = audit._disk_run(fd, RUN)
                assert disk["charge"] == 16 * 1024 * 1024 and disk["torn"]
    finally:
        handle.close()


@pytest.mark.parametrize(
    "total,subset", [("input_tokens", "cached_input_tokens"), ("output_tokens", "reasoning_tokens")]
)
def test_partial_included_subset_valid_update_keeps_enclosing_measurement(recorder, total, subset):
    owner, _ = recorder
    attempt = start(owner)
    values = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0}
    values.update({total: 10, subset: 5})
    assert acquire(
        owner,
        attempt,
        usage(
            values["input_tokens"],
            values["output_tokens"],
            values["cached_input_tokens"],
            values["reasoning_tokens"],
            kind="cumulative",
        ),
    ).admitted
    values.update({total: None, subset: 8})
    assert acquire(
        owner,
        attempt,
        usage(
            values["input_tokens"],
            values["output_tokens"],
            values["cached_input_tokens"],
            values["reasoning_tokens"],
            kind="cumulative",
            availability="partial",
        ),
    ).admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    snap = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert snap.audit_complete and snap.summary == owner.summary()
    assert snap.summary["usage"][total]["total"] == 10
    assert snap.summary["usage"][subset]["total"] == 8


@pytest.mark.parametrize("corruption", ["missing_artifact", "null_ref", "unknown_ref_field"])
@pytest.mark.parametrize(
    "operation", ["open", "start", "acquired", "finish", "finalize", "freeze", "maintenance"]
)
def test_malformed_sibling_ref_is_typed_and_cannot_fund_or_interrupt_writer(
    recorder, corruption, operation
):
    malformed, _ = recorder
    first = start(malformed)
    assert acquire(malformed, first, None, data=b"orphan retained exact response").admitted
    writer_id = str(uuid.uuid4())
    writer = other_owner(malformed.root, writer_id)
    assert writer.open().admitted
    own_attempt = start(writer, context(run_id=writer_id), logical=str(uuid.uuid4()))
    prior = acquire(
        writer, own_attempt, usage(7, 0, 0, 0, kind="cumulative"), data=b"writer prior response"
    )
    assert prior.admitted
    prior_path = writer.root / writer_id / ("raw-" + prior.ref.artifact_id)
    if operation == "finalize":
        assert finish(writer, own_attempt).admitted
    rows = events(malformed)
    if corruption == "missing_artifact":
        rows[-1]["ref"].pop("artifact_id")
    elif corruption == "null_ref":
        rows[-1]["ref"] = None
    else:
        rows[-1]["ref"]["authorization"] = "unclosed metadata"
    _rewrite_journal(malformed, rows)
    journal = malformed.root / RUN / "journal"
    before = journal.read_bytes()
    new_id = str(uuid.uuid4())
    target = other_owner(writer.root, new_id) if operation == "open" else writer
    ack = None
    try:
        if operation == "open":
            ack = target.open()
        elif operation == "start":
            ack = target.start(
                context(run_id=writer_id),
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=100,
            )
        elif operation == "acquired":
            ack = acquire(
                target, own_attempt, usage(8, 0, 0, 0, kind="cumulative"), data=b"unadmitted response"
            )
        elif operation == "finish":
            ack = finish(target, own_attempt)
        elif operation == "finalize":
            ack = target.finalize("completed")
        elif operation == "freeze":
            ack = target.set_frozen(True)
        else:
            ack = audit.maintain_audit_root(
                writer.root, monotonic=Clock(), utc_now=utc, action_deadline=100
            )
    except (KeyError, TypeError):
        pass
    assert ack is not None and not ack.admitted and ack.fault.code == "integrity_error"
    if operation != "maintenance":
        assert not target.snapshot().audit_complete
    assert not (writer.root / new_id).exists()
    assert journal.read_bytes() == before
    assert prior_path.read_bytes() == b"writer prior response"
    handle = audit._prepare_audit_root(writer.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                bad = audit._disk_run(fd, RUN)
                assert bad["charge"] == 16 * 1024 * 1024 and bad["torn"]
    finally:
        handle.close()


def _terminal_with_raw(owner):
    attempt = start(owner)
    ack = acquire(owner, attempt, usage(10, 2, 1, 1), data=b"barrier exact retained response")
    assert ack.admitted and finish(owner, attempt).admitted
    return "raw-" + ack.ref.artifact_id


def _trace_required_barrier(owner, raw_name, traced):
    directory = str(owner.root / RUN)
    required = {directory + "/" + n for n in ("journal", "admission", "final", raw_name)}
    assert required <= set(traced)
    assert directory in traced and str(owner.root) in traced
    assert max(traced.index(p) for p in required) < traced.index(directory)
    assert traced.index(directory) < len(traced) - 1 - traced[::-1].index(str(owner.root))


@pytest.mark.parametrize("site", ["run", "root"])
def test_failed_terminal_sync_and_fault_write_requires_current_barrier_then_recovers(
    recorder, monkeypatch, site
):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    handle = audit._prepare_audit_root(owner.root, create=False)
    sync, write = os.fsync, os.write
    cuts = []
    selected = str(owner.root / RUN) if site == "run" else str(owner.root)

    def fail_sync(fd):
        if os.readlink("/proc/self/fd/" + str(fd)) == selected and (owner.root / RUN / "final").exists():
            cuts.append("required_" + site)
            raise OSError("required terminal barrier failed")
        return sync(fd)

    def fail_fault(fd, data):
        if cuts and os.readlink("/proc/self/fd/" + str(fd)).endswith("/journal"):
            cuts.append("secondary_fault_write")
            raise OSError("secondary diagnostic unavailable")
        return write(fd, data)

    try:
        with monkeypatch.context() as cut:
            cut.setattr(os, "fsync", fail_sync)
            cut.setattr(os, "write", fail_fault)
            ack = owner.finalize("completed")
            assert not ack.admitted and ack.fault.code == "persistence_error"
            assert "secondary_fault_write" not in cuts and not owner.snapshot().audit_complete
            with audit._root_lock(handle):
                with pytest.raises(OSError):
                    audit._inventory(handle)
            contender = other_owner(owner.root, str(uuid.uuid4()))
            assert not contender.open().admitted
            replay = audit.AttemptRecorder.reopen(owner.root, RUN)
            assert not replay.audit_complete and replay.state == "incomplete"
            maintenance = audit.maintain_audit_root(
                owner.root,
                monotonic=Clock(),
                utc_now=lambda: "2026-10-20T12:00:00Z",
                action_deadline=100,
            )
            assert not maintenance.admitted and not (owner.root / RUN / "tombstone").exists()
        traced = []

        def recovered(fd):
            answer = sync(fd)
            traced.append(os.readlink("/proc/self/fd/" + str(fd)))
            return answer

        with monkeypatch.context() as restored:
            restored.setattr(os, "fsync", recovered)
            with audit._root_lock(handle):
                rows, charged = audit._inventory(handle)
            _trace_required_barrier(owner, raw_name, traced)
            assert charged == rows[RUN]["ordinary"] + 16384 < 16 * 1024 * 1024
            replay = audit.AttemptRecorder.reopen(owner.root, RUN)
            assert replay.audit_complete and replay.state == "completed"
        assert not owner.snapshot().audit_complete  # Recovery never rewrites the original failed ack.
        assert (owner.root / RUN / raw_name).read_bytes() == b"barrier exact retained response"
    finally:
        handle.close()


@pytest.mark.parametrize("site", ["journal", "admission", "final", "raw", "run", "root"])
@pytest.mark.parametrize("operation", ["inventory", "reopen", "open", "freeze", "maintenance"])
def test_current_evidence_sync_failure_blocks_every_terminal_consumer(
    recorder, monkeypatch, site, operation
):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    selected = (
        str(owner.root)
        if site == "root"
        else str(run)
        if site == "run"
        else str(run / (raw_name if site == "raw" else site))
    )
    sync = os.fsync
    failures = []
    journal = (run / "journal").read_bytes()
    raw = (run / raw_name).read_bytes()

    def fail(fd):
        if os.readlink("/proc/self/fd/" + str(fd)) == selected:
            failures.append(selected)
            raise OSError("current evidence sync cut")
        return sync(fd)

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with monkeypatch.context() as cut:
            cut.setattr(os, "fsync", fail)
            if operation == "inventory":
                with audit._root_lock(handle):
                    with pytest.raises(OSError):
                        audit._inventory(handle)
            elif operation == "reopen":
                assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
            elif operation == "open":
                contender = other_owner(owner.root, str(uuid.uuid4()))
                assert not contender.open().admitted
                assert not (owner.root / contender._context["run_id"]).exists()
            elif operation == "freeze":
                assert not owner.set_frozen(True).admitted
                assert not (run / "freeze").exists()
            else:
                ack = audit.maintain_audit_root(
                    owner.root,
                    monotonic=Clock(),
                    utc_now=lambda: "2026-10-20T12:00:00Z",
                    action_deadline=100,
                )
                assert not ack.admitted and not (run / "tombstone").exists()
        assert failures
        assert (run / "journal").read_bytes() == journal and (run / raw_name).read_bytes() == raw
        assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    finally:
        handle.close()


@pytest.mark.parametrize("change", ["missing", "bytes", "replacement"])
def test_terminal_inventory_validates_raw_before_capacity_or_pruning(recorder, monkeypatch, change):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    path = owner.root / RUN / raw_name
    if change == "missing":
        path.unlink()
    elif change == "bytes":
        path.write_bytes(b"different retained response")
    else:
        sync = os.fsync
        replaced = []

        def replace_during_sync(fd):
            answer = sync(fd)
            if os.readlink("/proc/self/fd/" + str(fd)) == str(path) and not replaced:
                replacement = path.with_name("temp-raw-" + str(uuid.uuid4()))
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(path)
                replaced.append(True)
            return answer

        monkeypatch.setattr(os, "fsync", replace_during_sync)
    contender = other_owner(owner.root, str(uuid.uuid4()))
    denied = contender.open()
    assert not denied.admitted and denied.fault.code in ("integrity_error", "persistence_error")
    assert not (owner.root / contender._context["run_id"]).exists()
    if change == "replacement":
        assert replaced
    else:
        assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


@pytest.mark.parametrize("site", ["journal", "admission", "final", "raw"])
def test_current_evidence_content_close_failure_is_typed_and_closes_owned_descriptor(
    recorder, monkeypatch, site
):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    selected = str(owner.root / RUN / (raw_name if site == "raw" else site))
    sync, close = os.fsync, os.close
    synced, failed = set(), []

    def track(fd):
        answer = sync(fd)
        if os.readlink("/proc/self/fd/" + str(fd)) == selected:
            synced.add(fd)
        return answer

    def fail_close(fd):
        if fd in synced and os.readlink("/proc/self/fd/" + str(fd)) == selected:
            synced.remove(fd)
            close(fd)
            failed.append(fd)
            raise OSError("content close cut after underlying close")
        return close(fd)

    with monkeypatch.context() as cut:
        cut.setattr(os, "fsync", track)
        cut.setattr(os, "close", fail_close)
        contender = other_owner(owner.root, str(uuid.uuid4()))
        ack = contender.open()
        assert not ack.admitted and ack.fault.code == "persistence_error"
        assert failed
    for fd in failed:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("when", ["before", "after"])
def test_terminal_barrier_validates_exact_raw_both_sides_of_sync(recorder, monkeypatch, when):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    raw = owner.root / RUN / raw_name
    original = raw.read_bytes()
    sync = os.fsync
    traced = []
    altered = []
    if when == "before":
        raw.write_bytes(b"x" * len(original))

    def change(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        answer = sync(fd)
        traced.append(path)
        if when == "after" and path == str(owner.root) and not altered:
            raw.write_bytes(b"x" * len(original))
            altered.append(True)
        return answer

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with monkeypatch.context() as cut:
            cut.setattr(os, "fsync", change)
            with audit._root_lock(handle):
                with pytest.raises(audit._Rejected) as failure:
                    audit._inventory(handle)
                assert failure.value.code == "integrity_error"
        if when == "before":
            assert not traced
        else:
            _trace_required_barrier(owner, raw_name, traced)
            assert altered
    finally:
        handle.close()


@pytest.mark.parametrize("scope", ["inventory", "checked", "reopen", "maintenance"])
def test_terminal_barrier_content_run_root_order_at_actual_shared_consumers(
    recorder, monkeypatch, scope
):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    original = os.fsync
    traced = []

    def sync(fd):
        answer = original(fd)
        traced.append(os.readlink("/proc/self/fd/" + str(fd)))
        return answer

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with monkeypatch.context() as trace:
            trace.setattr(os, "fsync", sync)
            if scope == "reopen":
                assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
            elif scope == "maintenance":
                ack = audit.maintain_audit_root(
                    owner.root, monotonic=Clock(), utc_now=utc, action_deadline=100
                )
                assert ack.admitted
            else:
                with audit._root_lock(handle):
                    if scope == "inventory":
                        rows, _ = audit._inventory(handle)
                        assert rows[RUN]["terminal"]
                    else:
                        with audit._run_dir(handle, RUN) as fd:
                            assert owner._checked(handle, fd)["terminal"]
        _trace_required_barrier(owner, raw_name, traced)
    finally:
        handle.close()


def test_independent_process_terminal_inventory_runs_current_barrier(recorder):
    import subprocess

    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    script = (
        "import os,json\nfrom pathlib import Path\nimport code_forge.invocation_audit as a\n"
        "root=Path(" + repr(str(owner.root)) + ")\n"
        "h=a._prepare_audit_root(root,create=False)\noriginal=os.fsync\ntraced=[]\n"
        "def sync(fd):\n result=original(fd)\n traced.append(os.readlink('/proc/self/fd/'+str(fd)))\n return result\n"
        "os.fsync=sync\n"
        "try:\n with a._root_lock(h):\n  rows,charge=a._inventory(h)\n"
        " print(json.dumps({'charge':charge,'traced':traced,'row':rows["
        + repr(RUN)
        + ']["ordinary"]}),flush=True)\n'
        "finally: h.close()\n"
    )
    child = subprocess.Popen(
        ["/usr/bin/python3", "-B", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0 and not err
        result = json.loads(out)
        _trace_required_barrier(owner, raw_name, result["traced"])
        assert result["charge"] == result["row"] + 16384 < 16 * 1024 * 1024
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


def test_maintenance_removed_raw_cannot_shrink_before_current_root_barrier(recorder, monkeypatch):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    before = _current_charge(owner)
    sync = os.fsync
    cuts = []

    def fail_after_removal(fd):
        if (
            os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root)
            and not (owner.root / RUN / raw_name).exists()
        ):
            cuts.append(True)
            raise OSError("post-removal current root barrier")
        return sync(fd)

    with monkeypatch.context() as cut:
        cut.setattr(os, "fsync", fail_after_removal)
        ack = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-20T12:00:00Z", action_deadline=100
        )
    assert cuts and not ack.admitted
    assert not (owner.root / RUN / raw_name).exists() and (owner.root / RUN / "tombstone").exists()
    assert _current_charge(owner) >= before


def _current_charge(owner):
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            return audit._inventory(handle)[0][RUN]["charge"]
    finally:
        handle.close()


def test_terminal_barrier_rereads_journal_after_content_and_directory_sync(recorder, monkeypatch):
    owner, _ = recorder
    _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    rows = events(owner)
    rows[-1]["audit_complete"] = False
    sync = os.fsync
    changed = []

    def rewrite(fd):
        answer = sync(fd)
        if os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root) and not changed:
            _rewrite_journal(owner, rows)
            changed.append(True)
        return answer

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with monkeypatch.context() as cut:
            cut.setattr(os, "fsync", rewrite)
            with audit._root_lock(handle):
                with pytest.raises(audit._Rejected) as failure:
                    audit._inventory(handle)
                assert failure.value.code == "integrity_error"
        assert changed
    finally:
        handle.close()


_CLOSED_OPEN_FIELDS = {
    "context": {
        "run_id": "not-uuid",
        "source_hash": "bad-hash",
        "snapshot_id": "not-uuid",
        "round_index": -1,
        "pass_name": "https://invalid",
        "group_id": "not-uuid",
        "group_diff_sha256": "bad-hash",
        "purpose": "",
        "parent_logical_id": "not-uuid",
    },
    "capability": {
        "profile_id": "unknown",
        "filesystem_type": "tmpfs",
        "root_device": -1,
        "root_inode": 0,
        "root_mount_id": 0,
        "runtime_fingerprint": "bad-hash",
    },
    "owner": {"pid": -1, "start_ticks": "not-decimal", "owner_id": "not-uuid"},
    "reservation": {"run_bytes": 0, "marker_bytes": 0, "root_bytes": 0, "body_bytes": 0},
}
_CLOSED_OPEN_CASES = [
    (projection, field, mutation)
    for projection, values in _CLOSED_OPEN_FIELDS.items()
    for field in values
    for mutation in ("missing", "type", "value")
] + [
    (projection, None, mutation) for projection in _CLOSED_OPEN_FIELDS for mutation in ("extra", "shape")
]
_MARKER_FIELDS = (
    "schema_version",
    "run_id",
    "owner",
    "event_id",
    "utc",
    "terminal",
    "frozen",
    "tombstone",
    "ordinary_bytes",
    "marker_partition_bytes",
)
_CLOSED_MARKER_CASES = [
    (field, mutation) for field in _MARKER_FIELDS for mutation in ("missing", "type", "value")
] + [(None, "extra"), (None, "shape")]


def _closed_terminal(recorder, monkeypatch, marker="final"):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    if marker == "fault":
        bad = owner.start(
            context(purpose="https://invalid"),
            logical_id=str(uuid.uuid4()),
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=100,
        )
        assert not bad.admitted
        assert owner.finalize("incomplete").admitted
    else:
        assert owner.finalize("completed").admitted
    if marker == "freeze":
        assert owner.set_frozen(True).admitted
    elif marker == "tombstone":

        def retained(*args, **kwargs):
            raise OSError("retain raw after durable tombstone for closed-schema control")

        with monkeypatch.context() as cut:
            cut.setattr(os, "unlink", retained)
            result = audit.maintain_audit_root(
                owner.root,
                monotonic=Clock(),
                utc_now=lambda: "2026-10-20T12:00:00Z",
                action_deadline=100,
            )
        assert not result.admitted and (owner.root / RUN / "tombstone").exists()
    assert (owner.root / RUN / raw_name).read_bytes() == b"barrier exact retained response"
    return owner, raw_name


def _mutate_closed_projection(value, field, mutation, bad_value):
    if mutation == "shape":
        return []
    if mutation == "extra":
        value["undeclared"] = 0
    elif mutation == "missing":
        value.pop(field)
    elif mutation == "type":
        value[field] = []
    else:
        value[field] = bad_value
    return value


def _assert_closed_corruption_refuses(owner, raw_name, *, prefix=True):
    run = owner.root / RUN
    journal = (run / "journal").read_bytes()
    raw = (run / raw_name).read_bytes()
    observed = None
    try:
        observed = audit.AttemptRecorder.reopen(owner.root, RUN)
    except OverflowError:
        pass
    assert observed is not None, "finite malformed time must return a typed observation"
    assert not observed.audit_complete and observed.state == "incomplete"
    if prefix:
        assert observed.summary["usage"]["input_tokens"]["known_subtotal"] == 10
        assert observed.summary["admitted_attempt_count"] == 1
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                candidate = audit._disk_run(fd, RUN)
                assert (
                    candidate["charge"] == 16 * 1024 * 1024
                    and candidate["torn"]
                    and candidate["schema_invalid"]
                )
            with pytest.raises(audit._Rejected) as failure:
                audit._inventory(handle)
            assert failure.value.code == "integrity_error"
    finally:
        handle.close()
    contender = other_owner(owner.root, str(uuid.uuid4()))
    denied = contender.open()
    assert not denied.admitted and denied.fault.code == "integrity_error"
    maintenance = audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-11-20T12:00:00Z", action_deadline=100
    )
    assert not maintenance.admitted and maintenance.fault.code == "integrity_error"
    assert (run / "journal").read_bytes() == journal and (run / raw_name).read_bytes() == raw


@pytest.mark.parametrize("projection,field,mutation", _CLOSED_OPEN_CASES)
def test_closed_run_open_projection_matrix_refuses_authority(
    recorder, monkeypatch, projection, field, mutation
):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[0][projection] = _mutate_closed_projection(
        rows[0][projection], field, mutation, _CLOSED_OPEN_FIELDS[projection].get(field)
    )
    _rewrite_journal(owner, rows)
    if projection == "owner":
        path = owner.root / RUN / "admission"
        data = json.loads(path.read_bytes())
        data["owner"] = rows[0]["owner"]
        path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name, prefix=False)


@pytest.mark.parametrize("marker", ["admission", "final", "fault", "freeze", "tombstone"])
@pytest.mark.parametrize("field,mutation", _CLOSED_MARKER_CASES)
def test_closed_marker_projection_matrix_preserves_valid_prefix(
    recorder, monkeypatch, marker, field, mutation
):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, marker)
    path = owner.root / RUN / marker
    data = json.loads(path.read_bytes())
    invalid = {
        "schema_version": True,
        "run_id": str(uuid.uuid4()),
        "owner": {},
        "event_id": "not-uuid",
        "utc": "invalid",
        "terminal": "unknown",
        "frozen": not data["frozen"],
        "tombstone": not data["tombstone"],
        "ordinary_bytes": -1,
        "marker_partition_bytes": 0,
    }
    data = _mutate_closed_projection(data, field, mutation, invalid.get(field))
    path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize("marker", ["admission", "final", "fault", "freeze", "tombstone"])
@pytest.mark.parametrize(
    "field,mutation",
    [
        (field, mutation)
        for field in ("pid", "start_ticks", "owner_id")
        for mutation in ("missing", "type", "value")
    ]
    + [(None, "extra")],
)
def test_closed_marker_owner_matrix(recorder, monkeypatch, marker, field, mutation):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, marker)
    path = owner.root / RUN / marker
    data = json.loads(path.read_bytes())
    data["owner"] = _mutate_closed_projection(
        data["owner"], field, mutation, {"pid": -1, "start_ticks": "bad", "owner_id": "bad"}.get(field)
    )
    path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize("change", ["no_open", "late_open", "second_open"])
def test_closed_run_open_ordering_preserves_only_valid_prefix(recorder, monkeypatch, change):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    if change == "no_open":
        rows.pop(0)
    elif change == "late_open":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        duplicate = dict(rows[0], event_id=str(uuid.uuid4()))
        rows.insert(-1, duplicate)
    for index, row in enumerate(rows):
        row["sequence"] = index
    assert _typed_closed_rejection(lambda: audit._fold(rows))
    _rewrite_journal(owner, rows)
    _assert_closed_corruption_refuses(owner, raw_name, prefix=change == "second_open")


@pytest.mark.parametrize(
    "site",
    [
        "context_extra",
        "owner_missing",
        "reservation_zero",
        "capability_extra",
        "final_extra",
        "elapsed_overflow",
    ],
)
@pytest.mark.parametrize("operation", ["start", "acquired", "finish", "finalize", "freeze"])
def test_closed_schema_malformed_sibling_is_typed_for_existing_writer(
    recorder, monkeypatch, site, operation
):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    writer_id = str(uuid.uuid4())
    writer = other_owner(owner.root, writer_id)
    assert writer.open().admitted
    attempt = start(writer, context(run_id=writer_id), logical=str(uuid.uuid4()))
    prior = acquire(writer, attempt, None, data=b"prior unaffected writer")
    assert prior.admitted
    if operation == "finalize":
        assert finish(writer, attempt).admitted
    rows = events(owner)
    if site == "context_extra":
        rows[0]["context"]["undeclared"] = 0
    elif site == "owner_missing":
        rows[0]["owner"].pop("owner_id")
    elif site == "reservation_zero":
        rows[0]["reservation"]["run_bytes"] = 0
    elif site == "capability_extra":
        rows[0]["capability"]["undeclared"] = 0
    elif site == "elapsed_overflow":
        rows[-1]["elapsed_s"] = 10**309
    else:
        path = owner.root / RUN / "final"
        data = json.loads(path.read_bytes())
        data["undeclared"] = 0
        path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _rewrite_journal(owner, rows)
    if operation == "start":
        ack = writer.start(
            context(run_id=writer_id),
            logical_id=str(uuid.uuid4()),
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=100,
        )
    elif operation == "acquired":
        ack = acquire(writer, attempt, None)
    elif operation == "finish":
        ack = finish(writer, attempt)
    elif operation == "finalize":
        ack = writer.finalize("completed")
    else:
        ack = writer.set_frozen(True)
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert not writer.snapshot().audit_complete
    assert (
        writer.root / writer_id / ("raw-" + prior.ref.artifact_id)
    ).read_bytes() == b"prior unaffected writer"
    assert (owner.root / RUN / raw_name).read_bytes() == b"barrier exact retained response"


@pytest.mark.parametrize(
    "value",
    [10**309, -(10**309), float("inf"), float("nan"), True],
    ids=["overflow", "negative", "infinity", "nan", "bool"],
)
def test_finite_time_corruption_is_typed_and_retains_prefix(recorder, monkeypatch, value):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[-1]["elapsed_s"] = value
    # Independent framing permits the invalid value for a genuine on-disk parser control.
    framed = []
    for row in rows:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        assert len(encoded) + 10 <= 2048
        framed.append(("%08x " % len(encoded)).encode() + encoded + b"\n")
    (owner.root / RUN / "journal").write_bytes(b"".join(framed))
    _assert_closed_corruption_refuses(owner, raw_name)


def test_large_finite_time_and_integer_usage_keep_distinct_domains(recorder):
    owner, _ = recorder
    attempt = start(owner)
    big = 10**309
    observed = usage(big, 0, 0, 0)
    assert acquire(owner, attempt, observed).admitted
    assert finish(owner, attempt, duration=10**308).admitted
    assert owner.finalize("completed").admitted
    rows = events(owner)
    rows[-1]["elapsed_s"] = 10**308
    _rewrite_journal(owner, rows)
    snap = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert snap.audit_complete
    assert snap.summary["usage"]["input_tokens"]["known_subtotal"] == big
    assert snap.summary["usage"]["input_tokens"]["total"] == big
    assert snap.summary["timing"]["wall_s"] == 10**308
    assert snap.summary["timing"]["work_s"] == float(10**308)


@pytest.mark.parametrize("marker", ["admission", "final", "fault", "freeze", "tombstone"])
@pytest.mark.parametrize("relation", ["owner", "event", "utc", "terminal"])
def test_closed_marker_relationships_reject_valid_type_drift(recorder, monkeypatch, marker, relation):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, marker)
    path = owner.root / RUN / marker
    data = json.loads(path.read_bytes())
    if relation == "owner":
        data["owner"]["owner_id"] = str(uuid.uuid4())
    elif relation == "event":
        data["event_id"] = events(owner)[-1]["event_id"] if marker == "tombstone" else str(uuid.uuid4())
    elif relation == "utc":
        data["utc"] = "2026-10-07T12:00:00Z"
    else:
        data["terminal"] = "failed" if data["terminal"] != "failed" else "cancelled"
    path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize("marker", ["admission", "fault"])
def test_closed_marker_zero_ordinary_partition(recorder, monkeypatch, marker):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, marker)
    path = owner.root / RUN / marker
    data = json.loads(path.read_bytes())
    data["ordinary_bytes"] = 1
    path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize("marker", ["admission", "final", "fault", "freeze", "tombstone"])
def test_closed_marker_invalid_json_is_not_free_capacity(recorder, monkeypatch, marker):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, marker)
    (owner.root / RUN / marker).write_bytes(b"{")
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize("field", ["source_hash", "group_diff_sha256", "run_id"])
def test_closed_open_context_valid_type_binding_drift(recorder, monkeypatch, field):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[0]["context"][field] = (
        str(uuid.uuid4()) if field == "run_id" else SOURCE if field == "source_hash" else None
    )
    assert _typed_closed_rejection(lambda: audit._open_projection(rows[0]))
    _rewrite_journal(owner, rows)
    _assert_closed_corruption_refuses(owner, raw_name, prefix=False)


@pytest.mark.parametrize("field", ["filesystem_type", "root_device", "root_inode", "root_mount_id"])
def test_closed_open_capability_valid_type_drift_refuses_public_authority(recorder, monkeypatch, field):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    actual = rows[0]["capability"][field]
    rows[0]["capability"][field] = (
        ("ext4" if actual == "btrfs" else "btrfs") if field == "filesystem_type" else actual + 1
    )
    _rewrite_journal(owner, rows)
    run = owner.root / RUN
    before = {name: (run / name).read_bytes() for name in ("journal", raw_name)}
    observed = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not observed.audit_complete and observed.state == "incomplete"
    assert observed.summary["usage"]["input_tokens"]["known_subtotal"] == 10
    denied = other_owner(owner.root, str(uuid.uuid4())).open()
    assert not denied.admitted and denied.fault.code == "integrity_error"
    maintenance = audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-11-20T12:00:00Z", action_deadline=100
    )
    assert not maintenance.admitted and maintenance.fault.code == "integrity_error"
    assert {name: (run / name).read_bytes() for name in before} == before


def test_closed_terminal_freeze_uses_committed_outcome_after_live_fault(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    # An invalid later call latches only the live owner; its terminal event stays immutable.
    refused = owner.finalize("not-a-state")
    assert not refused.admitted and not owner.snapshot().audit_complete
    assert owner.set_frozen(True).admitted
    marker = json.loads((owner.root / RUN / "freeze").read_bytes())
    assert marker["terminal"] == "completed"
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert reopened.summary["usage"]["input_tokens"]["known_subtotal"] == 10
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            rows, _ = audit._inventory(handle)
            assert rows[RUN]["frozen"] and not rows[RUN]["schema_invalid"]
    finally:
        handle.close()
    assert (owner.root / RUN / raw_name).read_bytes() == b"barrier exact retained response"


@pytest.mark.parametrize("reason", ["audit_incomplete", "fault"])
def test_closed_completed_terminal_requires_clean_history(recorder, monkeypatch, reason):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, "fault" if reason == "fault" else "final")
    rows = events(owner)
    rows[-1]["outcome"] = "completed"
    rows[-1]["audit_complete"] = reason != "audit_incomplete"
    _rewrite_journal(owner, rows)
    path = owner.root / RUN / "final"
    data = json.loads(path.read_bytes())
    data["terminal"] = "completed"
    path.write_bytes(json.dumps(data, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


def _typed_closed_rejection(action):
    try:
        action()
    except audit._Rejected:
        return True
    except (TypeError, KeyError, ValueError, OverflowError):
        return False
    return False


@pytest.mark.parametrize("projection,field,mutation", _CLOSED_OPEN_CASES)
def test_closed_run_open_fold_is_typed_at_each_projection(
    recorder, monkeypatch, projection, field, mutation
):
    owner, _ = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[0][projection] = _mutate_closed_projection(
        rows[0][projection], field, mutation, _CLOSED_OPEN_FIELDS[projection].get(field)
    )
    assert _typed_closed_rejection(lambda: audit._fold(rows))


@pytest.mark.parametrize("field,mutation", _CLOSED_MARKER_CASES)
def test_closed_marker_projection_is_typed_at_declared_fields(recorder, monkeypatch, field, mutation):
    owner, _ = _closed_terminal(recorder, monkeypatch)
    marker = json.loads((owner.root / RUN / "final").read_bytes())
    bad = {
        "schema_version": True,
        "run_id": "invalid",
        "owner": {},
        "event_id": "invalid",
        "utc": "invalid",
        "terminal": "unknown",
        "frozen": 1,
        "tombstone": 1,
        "ordinary_bytes": 16 * 1024 * 1024,
        "marker_partition_bytes": 0,
    }
    marker = _mutate_closed_projection(marker, field, mutation, bad.get(field))
    assert _typed_closed_rejection(lambda: audit._marker_projection(marker))


@pytest.mark.parametrize(
    "field,mutation",
    [
        (field, mutation)
        for field in ("pid", "start_ticks", "owner_id")
        for mutation in ("missing", "type", "value")
    ]
    + [(None, "extra")],
)
def test_closed_marker_owner_projection_is_typed(recorder, monkeypatch, field, mutation):
    owner, _ = _closed_terminal(recorder, monkeypatch)
    marker = json.loads((owner.root / RUN / "final").read_bytes())
    marker["owner"] = _mutate_closed_projection(
        marker["owner"], field, mutation, {"pid": 0, "start_ticks": "bad", "owner_id": "bad"}.get(field)
    )
    assert _typed_closed_rejection(lambda: audit._marker_projection(marker))


@pytest.mark.parametrize("site", ["origin", "deadline", "elapsed", "duration", "maintenance_deadline"])
def test_finite_time_overflow_is_typed_on_public_input_paths(recorder, site):
    owner, clock = recorder
    if site == "origin":
        other = audit.AttemptRecorder(
            owner.root, context=context(run_id=str(uuid.uuid4())), monotonic=lambda: 10**309, utc_now=utc
        )
        refused = other.open()
    elif site == "maintenance_deadline":
        refused = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=utc, action_deadline=10**309
        )
    else:
        attempt = start(owner)
        if site == "duration":
            refused = owner.finish(
                attempt,
                outcome="completed",
                error_class=None,
                duration_s=10**309,
                dispatch_state="entered_api_send",
            )
        else:
            if site == "elapsed":
                clock.value = 10**309
            refused = owner.start(
                context(),
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=10**309 if site == "deadline" else 100,
            )
    assert not refused.admitted and refused.fault.code == "invalid_input"


def test_closed_marker_requires_open_binding(recorder, monkeypatch):
    owner, _ = _closed_terminal(recorder, monkeypatch)
    metadata = {"final": json.loads((owner.root / RUN / "final").read_bytes())}
    assert _typed_closed_rejection(lambda: audit._marker_projections(metadata, []))


def test_closed_tombstone_cannot_coexist_with_freeze(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch, "tombstone")
    rows = events(owner)
    frozen = dict(rows[-1], event="freeze", event_id=str(uuid.uuid4()), sequence=len(rows), frozen=True)
    frozen.pop("outcome")
    frozen.pop("audit_complete")
    rows.append(frozen)
    _rewrite_journal(owner, rows)
    marker = json.loads((owner.root / RUN / "final").read_bytes())
    marker.update(event_id=frozen["event_id"], frozen=True)
    (owner.root / RUN / "freeze").touch(mode=0o600)
    (owner.root / RUN / "freeze").write_bytes(json.dumps(marker, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


def test_closed_marker_requires_matching_terminal_event(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)[:-1]
    _rewrite_journal(owner, rows)
    _assert_closed_corruption_refuses(owner, raw_name)


def test_closed_active_freeze_then_terminal_retains_valid_marker_order(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(10, 2, 1, 1)).admitted
    assert finish(owner, attempt).admitted
    assert owner.set_frozen(True).admitted
    assert owner.finalize("completed").admitted
    snap = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert snap.audit_complete and snap.state == "completed"
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            rows, _ = audit._inventory(handle)
            assert rows[RUN]["frozen"] and rows[RUN]["charge"] == 16 * 1024 * 1024
    finally:
        handle.close()


def test_closed_fault_after_terminal_refuses_authority(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[-1]["outcome"] = "incomplete"
    final_path = owner.root / RUN / "final"
    marker = json.loads(final_path.read_bytes())
    marker["terminal"] = "incomplete"
    final_path.write_bytes(json.dumps(marker, separators=(",", ":")).encode())
    fault = dict(
        rows[-1],
        event="audit_fault",
        event_id=str(uuid.uuid4()),
        sequence=len(rows),
        code="invalid_input",
        phase="start",
        attempt_id=None,
    )
    fault.pop("outcome")
    fault.pop("audit_complete")
    rows.append(fault)
    _rewrite_journal(owner, rows)
    marker.update(event_id=fault["event_id"], terminal=None, ordinary_bytes=0)
    (owner.root / RUN / "fault").touch(mode=0o600)
    (owner.root / RUN / "fault").write_bytes(json.dumps(marker, separators=(",", ":")).encode())
    _assert_closed_corruption_refuses(owner, raw_name)


@pytest.mark.parametrize(
    "value", [10**309, float("inf"), float("nan")], ids=["overflow", "infinity", "nan"]
)
def test_finite_time_domain_rejects_before_serialization(value):
    assert _typed_closed_rejection(lambda: audit._number(value))


def test_framed_invalid_time_without_terminal_marker_retains_typed_refusal(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[-1]["elapsed_s"] = 10**309
    assert all(len(json.dumps(row, separators=(",", ":")).encode()) + 10 <= 2048 for row in rows)
    _rewrite_journal(owner, rows)
    (owner.root / RUN / "final").unlink()
    data = (owner.root / RUN / "journal").read_bytes()
    parsed = audit._read_events(data, RUN)
    assert parsed[3] is True and parsed[4] is True
    _assert_closed_corruption_refuses(owner, raw_name)


def test_disk_fold_call_rejects_invalid_projection_before_replay(recorder, monkeypatch):
    owner, raw_name = _closed_terminal(recorder, monkeypatch)
    rows = events(owner)
    rows[0]["context"]["undeclared"] = 0
    _rewrite_journal(owner, rows)
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                candidate = audit._disk_run(fd, RUN)
                assert candidate["charge"] == 16 * 1024 * 1024 and candidate["schema_invalid"]
    finally:
        handle.close()
    _assert_closed_corruption_refuses(owner, raw_name, prefix=False)


def test_pruned_terminal_freeze_refuses_before_mutation_and_keeps_root_usable(recorder):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted

    def later():
        return "2026-10-20T12:00:00Z"

    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=later, action_deadline=100
    ).admitted
    run = owner.root / RUN
    assert (run / "tombstone").exists() and not (run / raw_name).exists()
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    reopened = audit.AttemptRecorder.reopen(owner.root, RUN).summary
    owner._utc_now = later
    ack = owner.set_frozen(True)
    assert not ack.admitted and ack.fault == audit.AuditFault("finalized", "freeze", None)
    assert not owner.snapshot().audit_complete
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary == reopened
    sibling = other_owner(owner.root, str(uuid.uuid4()))
    assert sibling.open().admitted
    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=later, action_deadline=100
    ).admitted
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before


def _assert_semantic_history_refused(owner, raw_name, *, attempts, subtotal):
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                candidate = audit._disk_run(fd, RUN)
                assert candidate["charge"] == 16 * 1024 * 1024
                assert candidate["torn"] and candidate["schema_invalid"]
            with pytest.raises(audit._Rejected) as denied:
                audit._inventory(handle)
            assert denied.value.code == "integrity_error"
    finally:
        handle.close()
    replay = audit.AttemptRecorder.reopen(owner.root, RUN).summary
    assert replay["state"] == "incomplete" and not replay["audit_complete"]
    assert replay["admitted_attempt_count"] == attempts
    assert replay["usage"]["input_tokens"]["known_subtotal"] == subtotal
    json.dumps(replay, allow_nan=False)
    assert not other_owner(owner.root, str(uuid.uuid4())).open().admitted
    ack = audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-20T12:00:00Z", action_deadline=100
    )
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert (run / raw_name).exists()
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    return replay


@pytest.mark.parametrize("change", ["acquired_after_finish", "first_retry", "existing_initial"])
def test_semantic_admission_replay_rejects_impossible_writer_history(recorder, change):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    if change == "existing_initial":
        first = next(row["attempt_id"] for row in events(owner) if row["event"] == "start")
        retry = start(owner, parent=first, cause="retry")
        assert acquire(owner, retry, usage(3, 0, 0, 0)).admitted
        assert finish(owner, retry).admitted
    assert owner.finalize("completed").admitted
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary["audit_complete"]
    rows = events(owner)
    starts = [row for row in rows if row["event"] == "start"]
    if change == "acquired_after_finish":
        acquired = next(i for i, row in enumerate(rows) if row["event"] == "acquired")
        ended = next(i for i, row in enumerate(rows) if row["event"] == "finish")
        rows[acquired], rows[ended] = rows[ended], rows[acquired]
        for sequence, row in enumerate(rows):
            row["sequence"] = sequence
        expected_attempts, expected_subtotal = 1, 0
    elif change == "first_retry":
        starts[0]["cause"] = "retry"
        expected_attempts, expected_subtotal = 0, 0
    else:
        starts[1]["cause"] = "initial"
        expected_attempts, expected_subtotal = 1, 10
    _rewrite_journal(owner, rows)
    _assert_semantic_history_refused(
        owner, raw_name, attempts=expected_attempts, subtotal=expected_subtotal
    )


@pytest.mark.parametrize("existing", [False, True])
def test_live_start_cause_matches_logical_registration(recorder, existing):
    owner, _ = recorder
    parent = start(owner) if existing else None
    before = events(owner)
    ack = owner.start(
        context(),
        logical_id=LOGICAL,
        parent_id=parent,
        cause="initial" if existing else "retry",
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        action_deadline=100,
    )
    assert not ack.admitted and ack.attempt_id is None
    assert ack.fault.code == "identity_conflict"
    assert events(owner)[: len(before)] == before
    assert len([row for row in events(owner) if row["event"] == "start"]) == int(existing)


def test_live_acquisition_after_finish_refuses_without_new_raw(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    before = events(owner)
    ack = acquire(owner, attempt, usage(10, 0, 0, 0))
    assert not ack.admitted and ack.ref is None and ack.fault.code == "identity_conflict"
    assert events(owner)[: len(before)] == before
    assert not list((owner.root / RUN).glob("raw-*"))
    assert not any(row["event"] == "acquired" for row in events(owner))


@pytest.mark.parametrize("error_type", [TypeError, ValueError, OverflowError])
@pytest.mark.parametrize("callback_call", [1, 2])
def test_lock_entry_monotonic_validation_error_returns_typed_fault_and_closes(
    recorder, error_type, callback_call
):
    owner, _ = recorder
    calls = []

    def callback():
        calls.append(len(calls) + 1)
        if len(calls) == callback_call:
            raise error_type("private callback validation detail")
        return 0.0

    owner._monotonic = callback
    before_fds = set(os.listdir("/proc/self/fd"))
    before = (owner.root / RUN / "journal").read_bytes()
    try:
        ack = owner.start(
            context(),
            logical_id=LOGICAL,
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=100,
        )
    except (TypeError, ValueError, OverflowError):
        ack = None
    assert ack is not None, "callback validation failure returns a safe acknowledgement"
    assert not ack.admitted and ack.attempt_id is None
    assert ack.fault == audit.AuditFault("invalid_input", "start", None)
    assert calls == list(range(1, callback_call + 1))
    assert set(os.listdir("/proc/self/fd")) == before_fds
    assert (owner.root / RUN / "journal").read_bytes() == before
    assert not owner.snapshot().audit_complete
    assert "private" not in json.dumps(dataclasses.asdict(ack))


def test_finite_duration_aggregate_refuses_finish_before_commit(recorder):
    owner, _ = recorder
    first = start(owner)
    assert finish(owner, first, duration=10**308).admitted
    second = start(owner, logical=str(uuid.uuid4()))
    before = events(owner)
    ack = finish(owner, second, duration=10**308)
    assert not ack.admitted and ack.fault == audit.AuditFault("invalid_input", "finish", second)
    assert events(owner)[: len(before)] == before
    assert not any(row["event"] == "finish" and row["attempt_id"] == second for row in events(owner))
    summary = owner.summary()
    assert not summary["audit_complete"] and summary["timing"]["known_work_s"] == float(10**308)
    assert summary["timing"]["work_s"] is None and summary["timing"]["unknown_duration_count"] == 1
    json.dumps(summary, allow_nan=False)
    assert finish(owner, second, duration=None, outcome="incomplete", dispatch="unknown").admitted
    assert owner.finalize("incomplete").admitted
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary == owner.summary()


def test_finite_duration_aggregate_corruption_is_incomplete_and_never_free_capacity(recorder):
    owner, _ = recorder
    first = start(owner)
    acquired = acquire(owner, first, usage(10, 0, 0, 0), data=b"retained aggregate proof")
    assert acquired.admitted and finish(owner, first, duration=10**308).admitted
    second = start(owner, logical=str(uuid.uuid4()))
    assert acquire(owner, second, usage(10, 0, 0, 0)).admitted
    assert finish(owner, second).admitted and owner.finalize("completed").admitted
    rows = events(owner)
    next(row for row in rows if row["event"] == "finish" and row["attempt_id"] == second)[
        "duration_s"
    ] = 10**308
    _rewrite_journal(owner, rows)
    replay = _assert_semantic_history_refused(
        owner, "raw-" + acquired.ref.artifact_id, attempts=2, subtotal=20
    )
    assert replay["timing"]["known_work_s"] == float(10**308)
    assert replay["timing"]["work_s"] is None and replay["timing"]["unknown_duration_count"] == 1


@pytest.mark.parametrize("excluded", ["unknown_duration", "not_dispatched"])
def test_large_finite_work_keeps_unknown_and_undispatched_intervals_distinct(recorder, excluded):
    owner, _ = recorder
    first = start(owner)
    assert acquire(owner, first, usage(10**309, 0, 0, 0)).admitted
    assert finish(owner, first, duration=10**308).admitted
    second = start(owner, logical=str(uuid.uuid4()))
    duration, dispatch = (
        (None, "entered_api_send") if excluded == "unknown_duration" else (10**308, "not_dispatched")
    )
    assert finish(owner, second, duration=duration, dispatch=dispatch).admitted
    assert owner.finalize("completed").admitted
    summary = owner.summary()
    assert summary["timing"]["known_work_s"] == float(10**308)
    assert summary["timing"]["work_s"] == (None if excluded == "unknown_duration" else float(10**308))
    assert summary["timing"]["unknown_duration_count"] == int(excluded == "unknown_duration")
    assert summary["usage"]["input_tokens"]["known_subtotal"] == 10**309
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary == summary
    json.dumps(summary, allow_nan=False)


@pytest.mark.parametrize("layer", ["wire", "stderr", "decoded"])
@pytest.mark.parametrize("partial_kind", ["incomplete_input", "body_cap", "complete"])
@pytest.mark.parametrize("fault_sink", ["intact", "append_loss"])
def test_partial_acquisition_truth_survives_supplemental_fault_loss(
    recorder, monkeypatch, layer, partial_kind, fault_sink
):
    owner, _ = recorder
    attempt = start(owner)
    body = b"x" * (256 * 1024 + 1) if partial_kind == "body_cap" else b"prefix"
    complete = partial_kind != "incomplete_input"
    partial = partial_kind != "complete"
    original = audit._append
    cuts = []

    def append(fd, event):
        if event["event"] == "audit_fault" and fault_sink == "append_loss":
            cuts.append(event["event_id"])
            raise OSError("supplemental audit fault append before write")
        return original(fd, event)

    with monkeypatch.context() as cut:
        cut.setattr(audit, "_append", append)
        ack = acquire(owner, attempt, usage(10, 2, 1, 1), data=body, layer=layer, complete=complete)
    assert ack.admitted is (not partial)
    assert ack.ref.partial is partial
    assert ack.ref.retained_bytes == min(len(body), 256 * 1024)
    assert ack.ref.original_bytes == len(body)
    assert bool(cuts) is (partial and fault_sink == "append_loss")
    if partial:
        assert ack.fault == audit.AuditFault("quota_refused", "acquired", attempt)
    run = owner.root / RUN
    raw = run / ("raw-" + ack.ref.artifact_id)
    assert raw.read_bytes() == body[: 256 * 1024]
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    rows = events(owner)
    assert sum(e["event"] == "audit_fault" for e in rows) == int(partial and fault_sink == "intact")
    folded_faults = audit._fold(rows)[-1]
    assert folded_faults == ([audit.AuditFault("quota_refused", "acquired", attempt)] if partial else [])
    live, replay = owner.snapshot(), audit.AttemptRecorder.reopen(owner.root, RUN)
    assert live.audit_complete is (not partial)
    assert replay.audit_complete is (not partial)
    assert replay.state == ("incomplete" if partial else "active")
    assert dict(replay.attempt_contexts) == {attempt: context()}
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert _current_charge(owner) == 16 * 1024 * 1024
    assert finish(owner, attempt, outcome="incomplete" if partial else "completed").admitted
    assert owner.finalize("incomplete" if partial else "completed").admitted
    live, replay = owner.snapshot(), audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.summary == live.summary
    assert replay.audit_complete is (not partial)
    expected = field(10, None, invalid=1) if partial else field(10, 10)
    assert replay.summary["usage"]["input_tokens"] == expected
    assert raw.read_bytes() == body[: 256 * 1024]


@pytest.mark.parametrize(
    "change",
    [
        "incomplete_without_partial",
        "hidden_truncation",
        "false_partial",
        "unknown_incomplete_without_partial",
    ],
)
def test_acquisition_flag_relations_reject_impossible_disk_history(recorder, change):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(5, 0, 0, 0, kind="cumulative"), data=b"prior").admitted
    ack = acquire(owner, attempt, usage(10, 0, 0, 0, kind="cumulative"), data=b"later")
    assert ack.admitted
    rows = events(owner)
    target = rows[-1]
    if change == "incomplete_without_partial":
        target["complete"] = False
    elif change == "hidden_truncation":
        target["ref"]["original_bytes"] += 1
    elif change == "false_partial":
        target["ref"]["partial"] = True
    else:
        target["ref"]["original_bytes"] = None
        target["complete"] = False
    _rewrite_journal(owner, rows)
    _assert_semantic_history_refused(owner, "raw-" + ack.ref.artifact_id, attempts=1, subtotal=5)


@pytest.mark.parametrize("complete,partial", [(True, False), (True, True), (False, True)])
def test_unknown_original_length_keeps_declared_partial_truth(recorder, complete, partial):
    owner, _ = recorder
    attempt = start(owner)
    ack = acquire(owner, attempt, usage(10, 0, 0, 0), data=b"exact")
    rows = events(owner)
    rows[-1]["ref"].update(original_bytes=None, partial=partial)
    rows[-1]["complete"] = complete
    _rewrite_journal(owner, rows)
    before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.audit_complete is (not partial)
    assert replay.state == ("incomplete" if partial else "active")
    assert replay.summary["usage"]["input_tokens"]["known_subtotal"] == 10
    assert (owner.root / RUN / ("raw-" + ack.ref.artifact_id)).read_bytes() == b"exact"
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before


@pytest.mark.parametrize("first", ["cutoff", "eligible", "before_final", "invalid"])
@pytest.mark.parametrize("later", ["backward", "invalid"])
def test_pruning_clock_uses_one_validated_transaction_sample(recorder, first, later):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    times = {
        "cutoff": "2026-10-13T12:00:00Z",
        "eligible": "2026-10-13T12:00:01Z",
        "before_final": "2026-10-06T11:59:59Z",
        "invalid": "invalid",
    }
    calls = []

    def clock():
        calls.append(len(calls) + 1)
        return (
            times[first]
            if len(calls) == 1
            else ("2026-10-13T12:00:00Z" if later == "backward" else "invalid")
        )

    ack = audit.maintain_audit_root(owner.root, monotonic=Clock(), utc_now=clock, action_deadline=100)
    assert calls == [1]
    if first in ("before_final", "invalid"):
        assert not ack.admitted and ack.fault.code in ("integrity_error", "invalid_input")
        assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    elif first == "cutoff":
        assert ack.admitted
        assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    else:
        assert ack.admitted and not (run / raw_name).exists()
        tombstone = json.loads((run / "tombstone").read_bytes())
        assert tombstone["utc"] == times["eligible"]
        audit._marker_projections({"tombstone": tombstone}, events(owner))
        assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
        retained = {p.name: p.read_bytes() for p in run.iterdir()}
        assert audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=lambda: "2026-11-01T12:00:00Z", action_deadline=100
        ).admitted
        assert {p.name: p.read_bytes() for p in run.iterdir()} == retained


def test_pruning_rejects_invalid_proposed_tombstone_before_first_write(recorder, monkeypatch):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    original = audit._marker

    def marker(*args, **kwargs):
        encoded = original(*args, **kwargs)
        if kwargs.get("tombstone"):
            row = json.loads(encoded)
            row["utc"] = "2026-10-13T12:00:00Z"
            return json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        return encoded

    monkeypatch.setattr(audit, "_marker", marker)
    ack = audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
    )
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert (run / raw_name).exists()
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before


@pytest.mark.parametrize("site", ["before_delete", "after_delete", "retained_publication"])
def test_pruning_invalid_current_state_never_acknowledges_success(recorder, monkeypatch, site):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    original_publish, original_unlink = audit._publish, os.unlink
    cuts = []

    def corrupt():
        row = json.loads((run / "tombstone").read_bytes())
        row["utc"] = "2026-10-13T12:00:00Z"
        (run / "tombstone").write_bytes(json.dumps(row, separators=(",", ":")).encode())
        cuts.append(site)

    def publish(fd, name, data, **kwargs):
        result = original_publish(fd, name, data, **kwargs)
        if name == "tombstone" and (
            (site == "before_delete" and not kwargs.get("replace"))
            or (site == "retained_publication" and kwargs.get("replace"))
        ):
            corrupt()
        return result

    def unlink(name, *args, **kwargs):
        result = original_unlink(name, *args, **kwargs)
        if site == "after_delete" and name == raw_name:
            corrupt()
        return result

    monkeypatch.setattr(audit, "_publish", publish)
    monkeypatch.setattr(os, "unlink", unlink)
    ack = audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
    )
    assert cuts == [site]
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert (run / raw_name).exists() is (site == "before_delete")
    assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                disk = audit._disk_run(fd, RUN)
                assert disk["torn"] and disk["schema_invalid"] and disk["charge"] == 16 * 1024 * 1024
    finally:
        handle.close()


@pytest.mark.parametrize("bad", [None, "value_error", "os_error"])
def test_pruning_invalid_clock_callback_preserves_all_bytes(recorder, bad):
    owner, _ = recorder
    _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}

    def clock():
        if bad == "value_error":
            raise ValueError("invalid caller clock")
        if bad == "os_error":
            raise OSError("unavailable caller clock")
        return None

    ack = audit.maintain_audit_root(owner.root, monotonic=Clock(), utc_now=clock, action_deadline=100)
    assert not ack.admitted
    assert ack.fault.code == ("invalid_input" if bad is None else "persistence_error")
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before


def test_pruning_predelete_current_root_barrier_failure_preserves_raw(recorder, monkeypatch):
    owner, _ = recorder
    raw_name = _terminal_with_raw(owner)
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    before_charge = _current_charge(owner)
    original = os.fsync
    cuts = []

    def sync(fd):
        if (
            (run / "tombstone").exists()
            and (run / raw_name).exists()
            and (os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root))
        ):
            cuts.append(True)
            raise OSError("current root barrier before destructive pruning")
        return original(fd)

    with monkeypatch.context() as cut:
        cut.setattr(os, "fsync", sync)
        ack = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-14T12:00:00Z", action_deadline=100
        )
    assert cuts and not ack.admitted and ack.fault.code == "persistence_error"
    assert (run / raw_name).read_bytes() == b"barrier exact retained response"
    assert _current_charge(owner) >= before_charge


@pytest.mark.parametrize("gap", [0, 10, 0.25])
@pytest.mark.parametrize("overlap", [False, True])
def test_wall_uses_persisted_admitted_open_epoch(disk_root, gap, overlap):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=clock, utc_now=utc)
    clock.value = gap
    assert owner.open().admitted
    assert owner.summary()["timing"]["wall_s"] is None
    first = start(owner)
    if overlap:
        clock.value = gap + 1
        second = start(owner, logical=str(uuid.uuid4()))
        clock.value = gap + 3
        assert finish(owner, first, duration=3).admitted
        clock.value = gap + 4
        assert finish(owner, second, duration=3).admitted
    else:
        clock.value = gap + 4
        assert finish(owner, first, duration=4).admitted
    assert owner.finalize("completed").admitted
    durable = events(owner)
    assert durable[0]["elapsed_s"] == gap
    assert durable[-1]["elapsed_s"] == gap + 4
    expected = {
        "work_s": 6.0 if overlap else 4.0,
        "wall_s": 4.0,
        "known_work_s": 6.0 if overlap else 4.0,
        "unknown_duration_count": 0,
    }
    assert owner.summary()["timing"] == expected
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary["timing"] == expected


def _prune_inventory(owner):
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            rows, charge = audit._inventory(handle)
            return rows[RUN], charge
    finally:
        handle.close()


def _two_terminal_raw(owner):
    for payload in (b"first synthetic raw", b"second synthetic raw"):
        attempt = start(owner, logical=str(uuid.uuid4()))
        assert acquire(owner, attempt, usage(0, 0, 0, 0), data=payload).admitted
        assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    return {p.name: p.read_bytes() for p in (owner.root / RUN).glob("raw-*")}


def _aged_prune(owner, when="2026-10-14T12:00:00Z"):
    return audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: when, action_deadline=100
    )


@pytest.mark.parametrize(
    "phase",
    ["before_first", "after_first", "after_all", "root_sync", "retained_before", "retained_after"],
)
def test_interrupted_prune_retries_to_durable_retained_charge(recorder, monkeypatch, phase):
    owner, _ = recorder
    original_raw = _two_terminal_raw(owner)
    run = owner.root / RUN
    _, before_charge = _prune_inventory(owner)
    original_unlink, original_sync, original_publish = os.unlink, os.fsync, audit._publish
    deleted = []
    triggered = []

    def unlink(name, *args, **kwargs):
        if name.startswith("raw-"):
            assert (run / "tombstone").exists()
            if (phase == "before_first" and not deleted) or (phase == "after_first" and deleted):
                triggered.append("unlink")
                raise OSError("owned unlink interruption")
            result = original_unlink(name, *args, **kwargs)
            deleted.append(name)
            return result
        return original_unlink(name, *args, **kwargs)

    def sync(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        if len(deleted) == 2 and (
            (phase == "after_all" and path == str(run))
            or (phase == "root_sync" and path == str(owner.root))
        ):
            triggered.append("sync")
            raise OSError("owned sync interruption")
        return original_sync(fd)

    def publish(fd, name, data, **kwargs):
        if name == "tombstone" and kwargs.get("replace"):
            if phase == "retained_before":
                triggered.append("publication")
                raise OSError("owned publication interruption")
            result = original_publish(fd, name, data, **kwargs)
            if phase == "retained_after":
                triggered.append("after durable publication")
                raise OSError("owned acknowledgement interruption")
            return result
        return original_publish(fd, name, data, **kwargs)

    with monkeypatch.context() as cut:
        cut.setattr(os, "unlink", unlink)
        cut.setattr(os, "fsync", sync)
        cut.setattr(audit, "_publish", publish)
        ack = _aged_prune(owner)
    assert triggered and not ack.admitted and ack.fault.code == "persistence_error"
    retained_before_retry = json.loads((run / "tombstone").read_bytes())
    remaining = {p.name: p.read_bytes() for p in run.glob("raw-*")}
    assert remaining == {n: b for n, b in original_raw.items() if n not in deleted}
    disk, failed_charge = _prune_inventory(owner)
    assert disk["terminal"] and not disk["torn"] and not disk["frozen"]
    if phase != "retained_after":
        assert failed_charge == before_charge, "no committed retained marker may fund shrink"
    else:
        assert failed_charge < before_charge, "the marker already durably committed before ack loss"
    assert _aged_prune(owner, "2026-10-15T12:00:00Z").admitted
    assert not list(run.glob("raw-*")), "restored retry must finish qualified raw deletion"
    disk, charge = _prune_inventory(owner)
    marker = json.loads((run / "tombstone").read_bytes())
    assert charge == disk["ordinary"] + audit.MARKER_BYTES < before_charge
    assert marker["ordinary_bytes"] == disk["ordinary"]
    assert marker["event_id"] == retained_before_retry["event_id"]
    assert marker["utc"] == retained_before_retry["utc"]
    assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    before_repeat = {p.name: p.read_bytes() for p in run.iterdir()}
    assert _aged_prune(owner).admitted
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before_repeat
    assert _prune_inventory(owner)[1] == charge


def _interrupt_prune_before_raw(owner, monkeypatch):
    original = os.unlink

    def fail(name, *args, **kwargs):
        if name.startswith("raw-"):
            raise OSError("owned raw retained before deletion")
        return original(name, *args, **kwargs)

    with monkeypatch.context() as cut:
        cut.setattr(os, "unlink", fail)
        ack = _aged_prune(owner)
    assert not ack.admitted and ack.fault.code == "persistence_error"
    assert (owner.root / RUN / "tombstone").exists()


def test_interrupted_prune_zero_byte_raw_is_not_completed(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(0, 0, 0, 0), data=b"").admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    _interrupt_prune_before_raw(owner, monkeypatch)
    disk, charge = _prune_inventory(owner)
    assert disk["ordinary"] == disk["tombstone"]["ordinary_bytes"]
    assert list((owner.root / RUN).glob("raw-*"))
    assert _aged_prune(owner).admitted
    assert not list((owner.root / RUN).glob("raw-*"))
    assert _prune_inventory(owner)[1] == charge


def test_interrupted_prune_refuses_understated_partition(recorder, monkeypatch):
    owner, _ = recorder
    raw = _two_terminal_raw(owner)
    _interrupt_prune_before_raw(owner, monkeypatch)
    marker_path = owner.root / RUN / "tombstone"
    marker = json.loads(marker_path.read_bytes())
    marker["ordinary_bytes"] -= 1
    marker_path.write_bytes(json.dumps(marker, separators=(",", ":")).encode())
    before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    ack = _aged_prune(owner)
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).glob("raw-*")} == raw


@pytest.mark.parametrize("drift", ["marker", "freeze", "foreign", "root_sync"])
def test_interrupted_prune_current_state_guard_preserves_raw(recorder, monkeypatch, drift):
    owner, _ = recorder
    raw = _two_terminal_raw(owner)
    _interrupt_prune_before_raw(owner, monkeypatch)
    run = owner.root / RUN
    original_inventory, original_sync = audit._inventory, os.fsync
    injected = []

    def inventory(handle):
        result = original_inventory(handle)
        if not injected:
            injected.append(drift)
            if drift == "marker":
                path = run / "tombstone"
                marker = json.loads(path.read_bytes())
                marker["utc"] = "2026-10-13T12:00:00Z"
                path.write_bytes(json.dumps(marker, separators=(",", ":")).encode())
            elif drift == "freeze":
                path = run / ("temp-freeze-" + str(uuid.uuid4()))
                path.write_bytes(b"incomplete owned marker")
                path.chmod(0o600)
            elif drift == "foreign":
                path = run / "foreign-resource"
                path.write_bytes(b"foreign fixture data")
                path.chmod(0o600)
        return result

    def sync(fd):
        if (
            injected
            and drift == "root_sync"
            and os.readlink("/proc/self/fd/" + str(fd)) == str(owner.root)
        ):
            raise OSError("current resumed root barrier")
        return original_sync(fd)

    with monkeypatch.context() as cut:
        cut.setattr(audit, "_inventory", inventory)
        cut.setattr(os, "fsync", sync)
        ack = _aged_prune(owner)
    assert injected and not ack.admitted
    assert ack.fault.code in ("integrity_error", "persistence_error")
    assert {p.name: p.read_bytes() for p in run.glob("raw-*")} == raw
    if drift == "foreign":
        assert (run / "foreign-resource").read_bytes() == b"foreign fixture data"
    if drift == "marker":
        with pytest.raises(audit._Rejected, match="integrity_error"):
            _prune_inventory(owner)
        assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    elif drift != "foreign":
        assert _prune_inventory(owner)[1] >= sum(len(b) for b in raw.values()) + audit.MARKER_BYTES


def test_completed_prune_tombstone_is_retained_without_publication(recorder, monkeypatch):
    owner, _ = recorder
    _two_terminal_raw(owner)
    assert _aged_prune(owner).admitted
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    before_charge = _prune_inventory(owner)[1]

    def refuse(*args, **kwargs):
        raise OSError("completed prune cannot publish or delete again")

    with monkeypatch.context() as cut:
        cut.setattr(audit, "_publish", refuse)
        cut.setattr(audit, "_marker", refuse)
        cut.setattr(os, "unlink", refuse)
        assert _aged_prune(owner, "2026-11-01T12:00:00Z").admitted
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert _prune_inventory(owner)[1] == before_charge


@pytest.mark.parametrize("completed", [False, True])
def test_interrupted_prune_backward_time_preserves_bytes(recorder, monkeypatch, completed):
    owner, _ = recorder
    _two_terminal_raw(owner)
    if completed:
        assert _aged_prune(owner).admitted
    else:
        _interrupt_prune_before_raw(owner, monkeypatch)
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    charge = _prune_inventory(owner)[1]
    ack = _aged_prune(owner, "2026-10-13T12:00:01Z")
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert _prune_inventory(owner)[1] == charge


@pytest.mark.parametrize("gap", [0.0, 10.0])
def test_wall_preserves_large_integer_terminal_offset(disk_root, gap):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=clock, utc_now=utc)
    clock.value = gap
    assert owner.open().admitted
    attempt = start(owner)
    clock.value = gap + 4
    assert finish(owner, attempt, duration=4).admitted
    assert owner.finalize("completed").admitted
    rows = events(owner)
    assert rows[0]["elapsed_s"] == gap
    rows[-1]["elapsed_s"] = 10**308
    _rewrite_journal(owner, rows)
    snap = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert snap.audit_complete
    assert snap.summary["timing"]["wall_s"] == 10**308 - int(gap)
    assert type(snap.summary["timing"]["wall_s"]) is int
    assert snap.summary["timing"]["known_work_s"] == 4


def _grow_acquired_ref(owner, row, extra):
    path = owner.root / RUN / ("raw-" + row["ref"]["artifact_id"])
    body = path.read_bytes() + extra
    path.write_bytes(body)
    row["ref"].update(
        retained_bytes=len(body), original_bytes=len(body), sha256=hashlib.sha256(body).hexdigest()
    )
    return path.name


@pytest.mark.parametrize("layers", [("wire", "stdout"), ("stdout", "stderr"), ("decoded", "decoded")])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_aggregate_cap_replay_boundaries_preserve_valid_prefix(recorder, layers, delta):
    owner, _ = recorder
    attempt = start(owner)
    first = audit.BODY_BYTES // 2
    second = audit.BODY_BYTES + min(delta, 0) - first
    assert acquire(
        owner, attempt, usage(2, 0, 0, 0, kind="cumulative"), data=b"a" * first, layer=layers[0]
    ).admitted
    assert acquire(
        owner, attempt, usage(5, 0, 0, 0, kind="cumulative"), data=b"b" * second, layer=layers[1]
    ).admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    rows = events(owner)
    acquired = [r for r in rows if r["event"] == "acquired"]
    if delta == 1:
        raw = _grow_acquired_ref(owner, acquired[-1], b"x")
        _rewrite_journal(owner, rows)
        assert sum(r["ref"]["retained_bytes"] for r in acquired) == audit.BODY_BYTES + 1
        summary = _assert_semantic_history_refused(owner, raw, attempts=1, subtotal=2)
        assert summary["observation_count"] == 1
        assert "integrity_error" in summary["fault_codes"]
    else:
        before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
        replay = audit.AttemptRecorder.reopen(owner.root, RUN)
        assert replay.audit_complete and replay.summary == owner.summary()
        assert replay.summary["observation_count"] == 2
        assert replay.summary["usage"]["input_tokens"]["known_subtotal"] == 5
        assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before


@pytest.mark.parametrize("layer", ["stderr", "decoded"])
def test_aggregate_cap_replay_many_small_refs_counts_exact_sum(recorder, layer):
    owner, _ = recorder
    attempt = start(owner)
    for i in range(16):
        assert acquire(
            owner,
            attempt,
            usage(i + 1, 0, 0, 0, kind="cumulative"),
            data=b"a" * (audit.BODY_BYTES // 16),
            layer=layer,
        ).admitted
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    rows = events(owner)
    acquired = [r for r in rows if r["event"] == "acquired"]
    raw = _grow_acquired_ref(owner, acquired[-1], b"x")
    _rewrite_journal(owner, rows)
    assert max(r["ref"]["retained_bytes"] for r in acquired) < audit.BODY_BYTES
    _assert_semantic_history_refused(owner, raw, attempts=1, subtotal=15)


@pytest.mark.parametrize(
    "same_attempt,second_layer", [(True, "decoded"), (False, "stdout"), (False, "decoded")]
)
def test_aggregate_cap_independent_attempts_and_partitions(recorder, same_attempt, second_layer):
    owner, _ = recorder
    first = start(owner)
    assert acquire(
        owner, first, usage(2, 0, 0, 0, kind="cumulative"), data=b"a" * audit.BODY_BYTES, layer="wire"
    ).admitted
    second = first if same_attempt else start(owner, logical=str(uuid.uuid4()))
    assert acquire(
        owner,
        second,
        usage(5, 0, 0, 0, kind="cumulative"),
        data=b"b" * audit.BODY_BYTES,
        layer=second_layer,
    ).admitted
    assert finish(owner, first).admitted
    if not same_attempt:
        assert finish(owner, second).admitted
    assert owner.finalize("completed").admitted
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.audit_complete and replay.summary == owner.summary()
    assert replay.summary["usage"]["input_tokens"]["known_subtotal"] == (5 if same_attempt else 7)


@pytest.mark.parametrize("layer", ["stdout", "decoded"])
def test_aggregate_cap_genuine_acquired_duplicates_count_once(recorder, layer):
    owner, _ = recorder
    attempt = start(owner)
    data = b"a" * (audit.BODY_BYTES // 2)
    first = acquire(owner, attempt, usage(2, 0, 0, 0, kind="cumulative"), data=data, layer=layer)
    assert first.admitted
    assert acquire(owner, attempt, usage(5, 0, 0, 0, kind="cumulative"), data=data, layer=layer).admitted
    before = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    repeated = acquire(
        owner,
        attempt,
        usage(2, 0, 0, 0, kind="cumulative"),
        data=data,
        layer=layer,
        identity=first.observation_id,
    )
    assert repeated == first
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    rows = events(owner)
    position = max(i for i, r in enumerate(rows) if r["event"] == "acquired")
    duplicate_rows = rows[: position + 1] + [rows[position]] + rows[position + 1 :]
    try:
        folded = audit._fold(duplicate_rows)
    except audit._Rejected:
        folded = None
    assert folded is not None, "an exact acquired replay cannot consume its partition twice"
    assert len(folded[2]) == 2
    _rewrite_journal(owner, duplicate_rows)
    before_replay = {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()}
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.audit_complete and replay.summary == owner.summary()
    assert replay.summary["observation_count"] == 2
    assert {p.name: p.read_bytes() for p in (owner.root / RUN).iterdir()} == before_replay


@pytest.mark.parametrize("layers", [("wire", "stderr"), ("decoded", "decoded")])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_aggregate_cap_public_writer_boundary_is_unchanged(recorder, layers, delta):
    owner, _ = recorder
    attempt = start(owner)
    first = audit.BODY_BYTES // 2
    assert acquire(
        owner, attempt, usage(2, 0, 0, 0, kind="cumulative"), data=b"a" * first, layer=layers[0]
    ).admitted
    data = b"b" * (audit.BODY_BYTES + delta - first)
    ack = acquire(owner, attempt, usage(5, 0, 0, 0, kind="cumulative"), data=data, layer=layers[1])
    assert ack.ref.retained_bytes == len(data) - max(delta, 0)
    assert ack.ref.original_bytes == len(data)
    assert ack.ref.partial == (delta == 1) and ack.admitted == (delta != 1)
    assert (owner.root / RUN / ("raw-" + ack.ref.artifact_id)).read_bytes() == data[
        : ack.ref.retained_bytes
    ]
    assert finish(owner, attempt, outcome="incomplete" if delta == 1 else "completed").admitted
    assert owner.finalize("incomplete" if delta == 1 else "completed").admitted
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.audit_complete == (delta != 1)
    assert replay.summary == owner.summary()


@pytest.mark.parametrize("site", ["journal", "admission", "final"])
def test_decoder_recursion_preserves_prefix_and_refuses_shared_consumers(recorder, monkeypatch, site):
    owner, clock = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    clock.value = 2.0
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    rows = events(owner)
    data = (run / site).read_bytes()
    if site == "journal":
        target = audit._frame(rows[-1])[9:-1]
    else:
        target = data
    original = audit.json.loads
    calls = []

    def decode(value, *args, **kwargs):
        if value == target:
            calls.append(value)
            raise RecursionError("fixture decoder failure on ordinary JSON")
        return original(value, *args, **kwargs)

    before = {p.name: p.read_bytes() for p in run.iterdir()}
    with monkeypatch.context() as patch:
        patch.setattr(audit.json, "loads", decode)
        replay = audit.AttemptRecorder.reopen(owner.root, RUN)
        assert not replay.audit_complete and replay.state == "incomplete"
        assert replay.summary["admitted_attempt_count"] == 1
        assert replay.summary["timing"]["known_work_s"] == 1.0
        assert dict(replay.attempt_contexts) == {attempt: context()}
        assert any(f.code == "integrity_error" for f in replay.faults)
        handle = audit._prepare_audit_root(owner.root, create=False)
        try:
            with audit._root_lock(handle), audit._run_dir(handle, RUN) as fd:
                disk = audit._disk_run(fd, RUN)
                assert disk["torn"] and disk["schema_invalid"]
                assert disk["charge"] == audit.RUN_BYTES
        finally:
            handle.close()
        neighbor = audit.AttemptRecorder(
            owner.root, context=context(run_id=str(uuid.uuid4())), monotonic=Clock(), utc_now=utc
        )
        denied = neighbor.open()
        assert not denied.admitted and denied.fault.code == "integrity_error"
        maintenance = audit.maintain_audit_root(
            owner.root, monotonic=Clock(), utc_now=utc, action_deadline=100
        )
        assert not maintenance.admitted and maintenance.fault.code == "integrity_error"
    assert calls
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete


def test_decoder_recursion_in_host_tombstone_is_typed_before_publication(recorder, monkeypatch):
    owner, _ = recorder
    assert owner.finalize("completed").admitted
    run = owner.root / RUN
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    original = audit.json.loads
    failures = []

    def decode(value, *args, **kwargs):
        parsed = original(value, *args, **kwargs)
        if isinstance(parsed, dict) and parsed.get("tombstone") is True:
            failures.append(value)
            raise RecursionError("fixture decoder failure on ordinary host marker")
        return parsed

    with monkeypatch.context() as patch:
        patch.setattr(audit.json, "loads", decode)
        ack = audit.maintain_audit_root(
            owner.root,
            monotonic=Clock(),
            utc_now=lambda: "2026-10-15T12:00:00Z",
            action_deadline=100,
        )
    assert failures and not ack.admitted and ack.fault.code == "integrity_error"
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert audit.maintain_audit_root(
        owner.root, monotonic=Clock(), utc_now=lambda: "2026-10-15T12:00:00Z", action_deadline=100
    ).admitted


def test_decoder_boundary_does_not_hide_unrelated_runtime_error(monkeypatch):
    failure = RuntimeError("unrelated decoder programming failure")

    def decode(value):
        raise failure

    monkeypatch.setattr(audit.json, "loads", decode)
    with pytest.raises(RuntimeError) as caught:
        audit._decode_json(b"{}")
    assert caught.value is failure


@pytest.mark.parametrize("site", ["read", "append", "sync", "inventory", "lock"])
@pytest.mark.parametrize("reject_descriptor", [False, True])
def test_owned_file_open_is_nonblocking_validated_and_closed(
    recorder, monkeypatch, site, reject_descriptor
):
    owner, _ = recorder
    start(owner)
    handle = audit._prepare_audit_root(owner.root, create=False)
    original_open = audit.os.open
    original_stat = audit.os.fstat
    opened = []
    target = "root.lock" if site in ("inventory", "lock") else "journal"

    def open_file(path, flags, *args, **kwargs):
        result = original_open(path, flags, *args, **kwargs)
        if path == target:
            opened.append((result, flags))
        return result

    def descriptor_stat(fd):
        value = original_stat(fd)
        if opened and fd == opened[-1][0] and reject_descriptor:
            fields = list(value)
            fields[0] = stat.S_IFDIR | 0o600
            return os.stat_result(fields)
        return value

    try:
        with audit._run_dir(handle, RUN) as fd, monkeypatch.context() as patch:
            patch.setattr(audit.os, "open", open_file)
            patch.setattr(audit.os, "fstat", descriptor_stat)

            def operation():
                if site == "read":
                    assert audit._read_file(fd, "journal") == (owner.root / RUN / "journal").read_bytes()
                elif site == "append":
                    audit._append(fd, events(owner)[-1])
                elif site == "sync":
                    audit._sync_file(fd, "journal")
                elif site == "inventory":
                    assert audit._inventory(handle)[1] == audit.RUN_BYTES
                else:
                    with audit._root_lock(handle):
                        pass

            if reject_descriptor:
                with pytest.raises(audit._Rejected) as caught:
                    operation()
                assert caught.value.code == "integrity_error"
            else:
                operation()
            assert opened and all(flags & os.O_NONBLOCK for _, flags in opened)
            for held, _ in opened:
                with pytest.raises(OSError):
                    original_stat(held)
        with audit._root_lock(handle):
            assert audit._inventory(handle)[1] == audit.RUN_BYTES
    finally:
        handle.close()


def test_owned_descriptor_rejection_is_public_typed_and_lock_is_reusable(recorder, monkeypatch):
    owner, _ = recorder
    original_open = audit.os.open
    original_stat = audit.os.fstat
    held = []

    def open_file(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "admission":
            held.append(fd)
            assert flags & os.O_NONBLOCK
        return fd

    def descriptor_stat(fd):
        value = original_stat(fd)
        if held and fd == held[-1]:
            fields = list(value)
            fields[0] = stat.S_IFDIR | 0o600
            return os.stat_result(fields)
        return value

    with monkeypatch.context() as patch:
        patch.setattr(audit.os, "open", open_file)
        patch.setattr(audit.os, "fstat", descriptor_stat)
        denied = owner.start(
            context(),
            logical_id=LOGICAL,
            parent_id=None,
            cause="initial",
            backend=BACKEND,
            request_digest=DIGEST,
            request_bytes=7,
            action_deadline=100,
        )
    assert held and not denied.admitted and denied.fault.code == "integrity_error"
    assert denied.attempt_id is None
    for fd in held:
        with pytest.raises(OSError):
            original_stat(fd)
    neighbor = audit.AttemptRecorder(
        owner.root, context=context(run_id=str(uuid.uuid4())), monotonic=Clock(), utc_now=utc
    )
    assert neighbor.open().admitted


@pytest.mark.parametrize("method", ["acquired", "finish"])
@pytest.mark.parametrize("bad", ["f" * 36, "-" * 36, RUN.replace("-", "f"), None])
def test_invalid_attempt_diagnostics_keep_root_readable(recorder, method, bad):
    owner, _ = recorder
    earlier = start(owner)
    if method == "acquired":
        refused = acquire(owner, bad, None)
    else:
        refused = finish(owner, bad)
    assert not refused.admitted
    assert refused.fault == audit.AuditFault("invalid_input", method, None)
    fault = next(event for event in events(owner) if event["event"] == "audit_fault")
    assert fault["attempt_id"] is None
    assert not owner.snapshot().audit_complete
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not replay.audit_complete and earlier in replay.attempt_contexts
    assert finish(owner, earlier).admitted
    assert owner.finalize("failed").admitted
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("method", ["acquired", "finish"])
def test_canonical_foreign_attempt_diagnostic_is_preserved(recorder, method):
    owner, _ = recorder
    foreign = str(uuid.uuid4())
    refused = acquire(owner, foreign, None) if method == "acquired" else finish(owner, foreign)
    assert refused.fault == audit.AuditFault("identity_conflict", method, foreign)
    assert owner.snapshot().faults == (refused.fault,)
    assert audit.AttemptRecorder.reopen(owner.root, RUN).faults == (refused.fault,)
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("semantics", ["included", "excluded"])
@pytest.mark.parametrize(
    "final_values", [(None, 6, None, None), (0, 6, None, None), (None, None, None, None)]
)
def test_explicit_final_availability_preserves_measured_subtotals(recorder, semantics, final_values):
    owner, _ = recorder
    attempt = start(owner)
    first = (0, 4, 0, 1) if final_values[0] == 0 else (5, 4, 2, 1)
    options = (
        {"input_semantics": "uncached_excludes_cache", "output_semantics": "visible_excludes_reasoning"}
        if semantics == "excluded"
        else {}
    )
    assert acquire(owner, attempt, usage(*first, kind="cumulative", **options)).admitted
    assert acquire(owner, attempt, usage(*final_values, kind="final", **options)).admitted
    assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    values = [old if new is None else new for old, new in zip(first, final_values, strict=True)]
    available = [value is not None for value in final_values]
    if semantics == "excluded":
        values[0] += values[2]
        values[1] += values[3]
        available[0] &= available[2]
        available[1] &= available[3]
    expected = {
        name: field(value, value if known else None, unknown=int(not known))
        for name, value, known in zip(
            ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"),
            values,
            available,
            strict=True,
        )
    }
    assert owner.summary()["usage"] == expected
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary["usage"] == expected


def test_invalid_final_usage_keeps_prior_known_measurements(recorder):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(5, 4, 2, 1, kind="cumulative")).admitted
    refused = acquire(owner, attempt, usage(availability="invalid", kind="final"))
    assert not refused.admitted and refused.fault.code == "invalid_input"
    assert finish(owner, attempt).admitted
    expected = {
        name: field(value, None, invalid=1)
        for name, value in zip(
            ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"),
            (5, 4, 2, 1),
            strict=True,
        )
    }
    assert owner.summary()["usage"] == expected
    assert audit.AttemptRecorder.reopen(owner.root, RUN).summary["usage"] == expected


def test_incremental_only_stream_does_not_require_final_usage(recorder):
    owner, _ = recorder
    attempt = start(owner)
    for sample in [(2, 1, 0, 0), (3, 2, 0, 0), (4, 3, 0, 0)]:
        assert acquire(owner, attempt, usage(*sample, kind="incremental")).admitted
    assert finish(owner, attempt).admitted
    assert owner.summary()["usage"] == {
        "input_tokens": field(9, 9),
        "output_tokens": field(6, 6),
        "cached_input_tokens": field(0, 0),
        "reasoning_tokens": field(0, 0),
    }


@pytest.mark.parametrize("site", ["start", "acquired", "finish", "freeze"])
def test_committed_event_is_reconciled_before_revalidation(recorder, monkeypatch, site):
    owner, _ = recorder
    earlier = start(owner)
    original_append = audit._append
    original_revalidate = audit._revalidate
    armed = False
    triggered = False

    def append(fd, event):
        nonlocal armed
        original_append(fd, event)
        if event["event"] == site:
            armed = True

    def revalidate(handle):
        nonlocal armed, triggered
        if armed and not triggered:
            armed = False
            triggered = True
            raise OSError("ordinary post-append revalidation failure")
        original_revalidate(handle)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(audit, "_revalidate", revalidate)
        if site == "start":
            refused = owner.start(
                context(),
                logical_id=str(uuid.uuid4()),
                parent_id=None,
                cause="initial",
                backend=BACKEND,
                request_digest=DIGEST,
                request_bytes=7,
                action_deadline=100,
            )
            assert refused.attempt_id is None
        elif site == "acquired":
            refused = acquire(owner, earlier, usage(5, 4, 2, 1))
        elif site == "finish":
            refused = finish(owner, earlier)
        else:
            refused = owner.set_frozen(True)
    assert triggered and not refused.admitted and refused.fault.code == "persistence_error"
    durable = events(owner)
    assert [event["sequence"] for event in durable] == list(range(len(durable)))
    assert sum(event["event"] == site for event in durable) == (2 if site == "start" else 1)
    assert durable[-1]["event"] == "audit_fault"
    assert len(owner._events) == len(durable)
    assert not owner.snapshot().audit_complete
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not replay.audit_complete and earlier in replay.attempt_contexts
    if site == "start":
        assert owner.summary()["admitted_attempt_count"] == 2
        assert owner.summary()["observed_api_send_count"] == 0
        later = durable[-2]["attempt_id"]
        assert finish(owner, later, outcome="refused", dispatch="not_dispatched").admitted
    if site == "acquired":
        assert owner.summary()["observation_count"] == 1
    if site == "freeze":
        assert not owner._frozen
        journal = (owner.root / RUN / "journal").read_bytes()
        retry = owner.set_frozen(True)
        assert not retry.admitted and retry.fault.code == "integrity_error"
        after_retry = events(owner)
        assert sum(event["event"] == "freeze" for event in after_retry) == 1
        assert len(after_retry) == len(durable)
        assert after_retry[-1]["event"] == "audit_fault"
        assert (owner.root / RUN / "journal").read_bytes() == journal
        assert not owner.snapshot().audit_complete
        assert other_owner(owner.root, str(uuid.uuid4())).open().admitted
    if site != "finish":
        assert finish(owner, earlier).admitted
    assert owner.finalize("failed").admitted
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.state == "incomplete" and not replay.audit_complete
    assert all(fault.code != "integrity_error" for fault in replay.faults)
    assert earlier in replay.attempt_contexts
    if site == "freeze":
        marker = json.loads((owner.root / RUN / "final").read_bytes())
        assert marker["frozen"] is True
        handle = audit._prepare_audit_root(owner.root, create=False)
        try:
            with audit._root_lock(handle):
                inventory, charge = audit._inventory(handle)
                assert inventory[RUN]["charge"] == audit.RUN_BYTES
                assert charge >= audit.RUN_BYTES
        finally:
            handle.close()
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


def test_committed_terminal_without_marker_refuses_retry_without_journal_fault(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    original_publish = audit._publish

    def publish(fd, name, value):
        if name == "final":
            raise OSError("ordinary final marker publication failure")
        return original_publish(fd, name, value)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_publish", publish)
        refused = owner.finalize("completed")
    assert not refused.admitted and refused.fault.code == "persistence_error"
    assert not owner._closed
    durable = events(owner)
    assert durable[-1]["event"] == "run_final"
    assert [event["sequence"] for event in durable] == list(range(len(durable)))
    assert not any(event["event"] == "audit_fault" for event in durable)
    journal = (owner.root / RUN / "journal").read_bytes()
    retry = owner.finalize("completed")
    assert not retry.admitted and retry.fault.code == "integrity_error"
    assert (owner.root / RUN / "journal").read_bytes() == journal
    assert not owner.snapshot().audit_complete
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not replay.audit_complete and attempt in replay.attempt_contexts
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle):
            assert audit._inventory(handle)[1] == audit.RUN_BYTES
    finally:
        handle.close()
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("site", ["open", "start", "acquired", "finish", "run_final", "freeze", "audit_fault"])
def test_append_journal_close_reconciles_exact_committed_history(disk_root, monkeypatch, site):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=context(), monotonic=clock, utc_now=utc)
    earlier = None
    if site != "open":
        assert owner.open().admitted
        earlier = start(owner)
    if site == "run_final":
        assert finish(owner, earlier).admitted
    original_append = audit._append
    original_close = os.close
    active = False
    cuts = []

    def append(fd, event):
        nonlocal active
        active = event["event"] == ("run_open" if site == "open" else site)
        try:
            return original_append(fd, event)
        finally:
            active = False

    def close(fd):
        if active and not cuts and os.readlink("/proc/self/fd/" + str(fd)).endswith("/journal"):
            original_close(fd)
            cuts.append(fd)
            raise OSError("journal close diagnostic after actual write/fsync/close")
        return original_close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(os, "close", close)
        if site == "open":
            refused = owner.open()
        elif site == "start":
            refused = owner.start(
                context(), logical_id=str(uuid.uuid4()), parent_id=None, cause="initial",
                backend=BACKEND, request_digest=DIGEST, request_bytes=7, action_deadline=100,
            )
        elif site in ("acquired", "audit_fault"):
            refused = acquire(owner, earlier, usage(5, 4, 2, 1), complete=site != "audit_fault")
        elif site == "finish":
            refused = finish(owner, earlier)
        elif site == "run_final":
            refused = owner.finalize("completed")
        else:
            refused = owner.set_frozen(True)
    assert len(cuts) == 1 and not refused.admitted
    assert refused.fault.code == ("quota_refused" if site == "audit_fault" else "persistence_error")
    durable = events(owner)
    assert [event["sequence"] for event in durable] == list(range(len(durable)))
    assert owner._events == durable
    assert not owner.snapshot().audit_complete
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert replay.summary["observation_count"] == int(site in ("acquired", "audit_fault"))
    assert any(f.code == "integrity_error" for f in replay.faults) == (site in ("open", "run_final"))
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted

    if site == "open":
        assert not owner._opened and not owner._closed
        assert not (owner.root / RUN / "admission").exists()
        journal = (owner.root / RUN / "journal").read_bytes()
        assert not owner.open().admitted
        assert (owner.root / RUN / "journal").read_bytes() == journal
    elif site == "run_final":
        assert durable[-1]["event"] == "run_final"
        assert not (owner.root / RUN / "final").exists()
        journal = (owner.root / RUN / "journal").read_bytes()
        assert not owner.finalize("completed").admitted
        assert (owner.root / RUN / "journal").read_bytes() == journal
    else:
        if site == "start":
            later = [event["attempt_id"] for event in durable if event["event"] == "start"][-1]
            assert refused.attempt_id is None
            assert finish(owner, later, outcome="refused", dispatch="not_dispatched").admitted
        if site != "finish":
            assert finish(owner, earlier).admitted
        assert owner.finalize("failed").admitted
        replay = audit.AttemptRecorder.reopen(owner.root, RUN)
        assert not replay.audit_complete and replay.state == "incomplete"
        assert not any(f.code == "integrity_error" for f in replay.faults)
        if site == "freeze":
            assert json.loads((owner.root / RUN / "final").read_bytes())["frozen"] is True
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize(
    "cut", [
        "prior", "torn", "duplicate", "conflict", "fsync", "run_fsync", "root_fsync", "reread",
        "run_rebound", "root_rebound", "late_run_rebound", "late_root_rebound", "journal_rebound",
    ]
)
def test_append_reconciliation_uncertain_bytes_never_authorize_diagnostic(recorder, monkeypatch, cut):
    owner, _ = recorder
    attempt = start(owner)
    original_append = audit._append
    original_sync = audit._sync_file
    original_read = audit._read_file
    original_fsync = os.fsync
    injected = OSError("original acquisition append failure")
    captured = []
    after_append = False
    reads = 0
    journal = owner.root / RUN / "journal"
    saved = None

    def append(fd, event):
        nonlocal after_append, saved
        if event["event"] != "acquired":
            return original_append(fd, event)
        if cut in ("prior", "torn"):
            if cut == "torn":
                opened = os.open("journal", os.O_WRONLY | os.O_APPEND, dir_fd=fd)
                try:
                    os.write(opened, audit._frame(event)[:20])
                    os.fsync(opened)
                finally:
                    os.close(opened)
        else:
            original_append(fd, event)
            if cut in ("duplicate", "conflict"):
                extra = event.copy()
                if cut == "conflict":
                    extra["event_id"] = str(uuid.uuid4())
                original_append(fd, extra)
            if cut == "run_rebound":
                saved = owner.root / (RUN + "-saved")
                (owner.root / RUN).rename(saved)
                (owner.root / RUN).mkdir(mode=0o700)
            if cut == "root_rebound":
                saved = owner.root.with_name(owner.root.name + "-saved")
                owner.root.rename(saved)
                owner.root.mkdir(mode=0o700)
        after_append = True
        actual = (saved / ("journal" if cut == "run_rebound" else RUN + "/journal")) if saved else journal
        captured.append(actual.read_bytes())
        raise injected

    def sync(fd, name):
        nonlocal saved
        if after_append and cut == "fsync" and name == "journal":
            raise OSError("secondary reconciliation fsync failure")
        result = original_sync(fd, name)
        if after_append and name == "journal" and saved is None:
            if cut == "late_run_rebound":
                saved = owner.root / (RUN + "-saved")
                (owner.root / RUN).rename(saved)
                (owner.root / RUN).mkdir(mode=0o700)
            elif cut == "late_root_rebound":
                saved = owner.root.with_name(owner.root.name + "-saved")
                owner.root.rename(saved)
                owner.root.mkdir(mode=0o700)
            elif cut == "journal_rebound":
                saved = journal.with_name("journal-saved")
                journal.rename(saved)
                journal.write_bytes(saved.read_bytes())
                journal.chmod(0o600)
        return result

    def fsync(fd):
        if after_append and cut in ("run_fsync", "root_fsync"):
            path = owner.root / RUN if cut == "run_fsync" else owner.root
            held, current = os.fstat(fd), path.stat()
            if (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                raise OSError("secondary reconciliation directory fsync failure")
        return original_fsync(fd)

    def read(fd, name, bound=audit.RUN_BYTES):
        nonlocal reads
        data = original_read(fd, name, bound)
        if after_append and cut == "reread" and name == "journal":
            reads += 1
            if reads == 2:
                return data + b"uncertain"
        return data

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(audit, "_sync_file", sync)
        patch.setattr(audit, "_read_file", read)
        patch.setattr(os, "fsync", fsync)
        refused = acquire(owner, attempt, usage(5, 4, 2, 1))
    if saved:
        if cut in ("run_rebound", "late_run_rebound"):
            (owner.root / RUN).rmdir()
            saved.rename(owner.root / RUN)
        elif cut == "journal_rebound":
            journal.unlink()
            saved.rename(journal)
        else:
            owner.root.rmdir()
            saved.rename(owner.root)
    assert not refused.admitted and refused.fault.code == "persistence_error"
    assert not owner.snapshot().audit_complete and len(captured) == 1
    assert owner._append_uncertain == (cut != "prior")
    if cut == "prior":
        assert [e["event"] for e in events(owner)] == ["run_open", "start", "audit_fault"]
        assert owner._events == events(owner)
        assert finish(owner, attempt, outcome="failed").admitted
        assert owner.finalize("failed").admitted
    else:
        assert journal.read_bytes() == captured[0]
        assert [e["event"] for e in owner._events] == ["run_open", "start"]
        assert not finish(owner, attempt, outcome="failed").admitted
        assert not owner.finalize("failed").admitted
        assert journal.read_bytes() == captured[0]
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    if cut != "prior":
        assert not (owner.root / RUN / "fault").exists()
    if cut in ("torn", "conflict", "prior"):
        assert not replay.audit_complete
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle), audit._run_dir(handle, RUN) as fd:
            assert audit._disk_run(fd, RUN)["charge"] == audit.RUN_BYTES
    finally:
        handle.close()
    # Framed conflicting sequence is a corrupt namespace; an incomplete prefix
    # remains charged under the existing inventory contract, never reclaimable.
    neighbor = other_owner(owner.root, str(uuid.uuid4())).open()
    assert neighbor.admitted == (cut != "conflict")


def test_append_reconciliation_secondary_fault_preserves_original_exception(recorder, monkeypatch):
    owner, _ = recorder
    attempt = start(owner)
    original = OSError("original append identity")
    event = owner._event(
        "finish", attempt_id=attempt, outcome="failed", error_class=None,
        duration_s=1.0, dispatch_state="entered_api_send",
    )

    def append(fd, event):
        raise original

    def history(*args):
        raise ValueError("secondary readback validation failure")

    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle), audit._run_dir(handle, RUN) as fd:
            with monkeypatch.context() as patch:
                patch.setattr(audit, "_append", append)
                patch.setattr(owner, "_journal_history", history)
                with pytest.raises(OSError) as caught:
                    owner._append_event(handle, fd, event)
            assert caught.value is original and owner._append_uncertain
            before = (owner.root / RUN / "journal").read_bytes()
            with pytest.raises(audit._Rejected):
                owner._append_event(handle, fd, event)
            with pytest.raises(audit._Rejected):
                owner._checked(handle, fd)
            assert (owner.root / RUN / "journal").read_bytes() == before
    finally:
        handle.close()


def test_append_diagnostic_requires_current_exact_history(recorder):
    owner, _ = recorder
    attempt = start(owner)
    external = owner._event(
        "finish", attempt_id=attempt, outcome="failed", error_class=None,
        duration_s=1.0, dispatch_state="entered_api_send",
    )
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle), audit._run_dir(handle, RUN) as fd:
            audit._append(fd, external)
    finally:
        handle.close()
    journal = owner.root / RUN / "journal"
    before = journal.read_bytes()
    refused = acquire(owner, attempt, None)
    assert not refused.admitted and refused.fault.code == "integrity_error"
    assert journal.read_bytes() == before
    assert not (owner.root / RUN / "fault").exists()
    assert not owner.snapshot().audit_complete
    replay = audit.AttemptRecorder.reopen(owner.root, RUN)
    assert not any(f.code == "integrity_error" for f in replay.faults)
    assert replay.summary["admitted_attempt_count"] == 1
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("invalid", ["finish", "raw"])
def test_append_reconciliation_validates_fold_and_raw_contents(recorder, invalid):
    owner, _ = recorder
    attempt = start(owner)
    if invalid == "raw":
        acquired = acquire(owner, attempt, usage(5, 4, 2, 1))
        assert acquired.admitted
        (owner.root / RUN / ("raw-" + acquired.ref.artifact_id)).write_bytes(b"bad")
        expected = owner._events
    else:
        event = owner._event(
            "finish", attempt_id=attempt, outcome="failed", error_class=None,
            duration_s=-1.0, dispatch_state="entered_api_send",
        )
        expected = owner._events + [event]
        with (owner.root / RUN / "journal").open("ab") as stream:
            stream.write(audit._frame(event))
            stream.flush()
            os.fsync(stream.fileno())
    journal = owner.root / RUN / "journal"
    before = journal.read_bytes()
    handle = audit._prepare_audit_root(owner.root, create=False)
    try:
        with audit._root_lock(handle), audit._run_dir(handle, RUN) as fd:
            with pytest.raises(audit._Rejected):
                owner._journal_history(handle, fd, ((before, expected),))
    finally:
        handle.close()
    assert journal.read_bytes() == before


def bound_context(**changes):
    return context(source_hash=SOURCE, snapshot_id=SNAPSHOT, **changes)


def input_manifest(**changes):
    return {
        "schema_version": 1, "run_id": RUN, "snapshot_id": SNAPSHOT,
        "mode": "diff", "legacy_source_hash": SOURCE,
        "exact_diff_sha256": hashlib.sha256(b"exact input\n").hexdigest(), "diff_bytes": 12,
        "files": [], "repositories": [], **changes,
    }


def test_input_manifest_required_bound_start_and_immutable_readback(disk_root):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    manifest = input_manifest()
    assert owner.publish_input_manifest(manifest).admitted
    before = (owner.root / RUN / "input-manifest.json").read_bytes()
    assert owner.publish_input_manifest(manifest).admitted
    attempt = start(owner, bound_context())
    assert finish(owner, attempt).admitted
    assert owner.finalize("completed").admitted
    view = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert view.input_manifest["exact_diff_sha256"] == manifest["exact_diff_sha256"]
    assert (owner.root / RUN / "input-manifest.json").read_bytes() == before
    with pytest.raises(TypeError):
        view.input_manifest["mode"] = "files"


def test_settled_acquired_partial_native_and_entered_sibling_keep_incomplete(recorder):
    owner, _ = recorder
    first = start(owner)
    sibling = start(owner, logical=str(uuid.uuid4()))
    payload = b'{"usage":2}\n{"usage":5}\npartial'
    raw = owner.settle_acquired(
        first, layer="wire", data=payload, complete=False, usage=None,
        dispatch_state="entered_api_send",
    )
    assert not raw.admitted and raw.ref and raw.observation_id
    for offset, count in [(0, 2), (12, 5)]:
        assert owner.observe_response(
            first, source_observation_id=raw.observation_id, frame_offset=offset,
            frame_length=11, usage=usage(count, 1, 0, 0, kind="cumulative"), observed_backend=None,
        ).admitted
    assert owner.settle_acquired(
        sibling, layer="stderr", data=b"stderr", complete=True, usage=None,
        dispatch_state="entered_cli_launch",
    ).admitted
    view = owner.scoped_snapshot()
    assert not view.audit_complete and view.summary["usage"]["input_tokens"]["known_subtotal"] == 5
    assert view.summary["usage"]["input_tokens"]["total"] is None
    assert view.summary["observed_api_send_count"] == 1
    assert view.summary["observed_cli_launch_count"] == 1
    assert not owner.start(
        context(), logical_id=str(uuid.uuid4()), parent_id=None, cause="initial", backend=BACKEND,
        request_digest=DIGEST, request_bytes=7, action_deadline=100,
    ).admitted
    assert finish(owner, first, outcome="failed").admitted
    assert finish(owner, sibling, outcome="failed", dispatch="entered_cli_launch").admitted
    assert owner.finalize("failed").admitted
    replay = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert not replay.audit_complete
    assert replay.summary["usage"] == owner.scoped_snapshot().summary["usage"]
    assert replay.summary["timing"] == owner.scoped_snapshot().summary["timing"]
    assert (owner.root / RUN / ("raw-" + raw.ref.artifact_id)).read_bytes() == payload


def test_response_observed_native_identity_range_and_exact_replay(recorder):
    owner, _ = recorder
    attempt = start(owner)
    raw = acquire(owner, attempt, None, data=b'{"model":"observed"}')
    observed = dataclasses.replace(BACKEND, observed_backend="actual", observed_model="observed")
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=20,
                usage=usage(0, 0, 0, 0), observed_backend=observed)
    assert owner.observe_response(attempt, **args).admitted
    response = owner.scoped_snapshot().attempts[0].response_observations[0]
    assert response.observed_backend.observed_model == "observed"
    assert owner.observe_response(attempt, observation_id=response.observation_id, **args).admitted
    assert len(owner.scoped_snapshot().attempts[0].response_observations) == 1
    assert owner.scoped_snapshot().attempts[0].requested_backend.observed_model is None


def test_scoped_projection_full_causal_fold_unequal_roles_and_work_wall(recorder):
    owner, clock = recorder
    first = start(owner, context(pass_name="one"))
    clock.value = 1
    second = start(owner, context(pass_name="two", parent_logical_id=LOGICAL),
                   logical=str(uuid.uuid4()), parent=first, cause="correction")
    assert acquire(owner, first, usage(3510, 10, 0, 0)).admitted
    assert acquire(owner, second, usage(7885, 8, 0, 0)).admitted
    clock.value = 3
    assert finish(owner, first, duration=3).admitted
    clock.value = 4
    assert finish(owner, second, duration=3).admitted
    assert owner.finalize("completed").admitted
    scope = {"pass_name": "two"}
    selected = owner.scoped_snapshot(scope=scope)
    replay = audit.AttemptRecorder.reopen_scoped(owner.root, RUN, scope=scope)
    assert selected.summary == replay.summary
    assert selected.summary["usage"]["input_tokens"]["total"] == 7885
    assert selected.summary["usage"]["output_tokens"]["total"] == 8
    assert selected.summary["timing"]["work_s"] == 3
    assert selected.summary["timing"]["wall_s"] == 4
    assert owner.scoped_snapshot().summary["timing"]["work_s"] == 6
    assert selected.attempts[0].parent_id == first
    with pytest.raises(TypeError):
        selected.summary["usage"]["input_tokens"]["total"] = 0
    empty = owner.scoped_snapshot(scope={"pass_name": "absent"})
    assert empty.summary["usage"]["input_tokens"]["total"] is None
    assert empty.summary["usage"]["input_tokens"]["known_subtotal"] == 0


@pytest.mark.parametrize("change", [
    {"frame_offset": -1}, {"frame_offset": True}, {"frame_length": 0}, {"frame_length": True},
    {"frame_length": 99}, {"source_observation_id": str(uuid.uuid4())},
    {"usage": None, "observed_backend": None},
    {"usage": dataclasses.replace(usage(1, 1, 0, 0), observation_source="derived")},
    {"usage": dataclasses.replace(usage(1, 1, 0, 0), scope="cli_process")},
    {"observed_backend": BACKEND},
    {"observed_backend": dataclasses.replace(BACKEND, requested_model="wrong", observed_model="actual")},
])
def test_response_observed_invalid_projection_refuses_without_native_event(recorder, change):
    owner, _ = recorder
    attempt = start(owner)
    raw = acquire(owner, attempt, None, data=b'{"usage":1}')
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=11,
                usage=usage(1, 1, 0, 0), observed_backend=None)
    args.update(change)
    refused = owner.observe_response(attempt, **args)
    assert not refused.admitted
    assert not any(e["event"] == "response_observed" for e in events(owner))
    assert not owner.scoped_snapshot().audit_complete


@pytest.mark.parametrize("layer,data", [
    ("stderr", b'{"usage":1}'), ("decoded", b'{"usage":1}'), ("wire", b'{"usage":'),
    ("wire", b'data: {"usage":1}'), ("wire", b'[]'), ("wire", b'\xff'),
])
def test_response_observed_source_layer_and_complete_native_frame(recorder, layer, data):
    owner, _ = recorder
    attempt = start(owner)
    raw = acquire(owner, attempt, None, data=data, layer=layer)
    assert not owner.observe_response(
        attempt, source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(data),
        usage=usage(1, 1, 0, 0), observed_backend=None,
    ).admitted
    assert not any(e["event"] == "response_observed" for e in events(owner))


@pytest.mark.parametrize("stream", [False, True])
def test_response_observed_invalid_snapshot_keeps_prior_subtotal(recorder, stream):
    owner, _ = recorder
    attempt = start(owner)
    body = b'data: {"usage":5}\n\n' if stream else b'{"usage":5}'
    raw = acquire(owner, attempt, None, data=body)
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(body),
                observed_backend=None)
    assert owner.observe_response(attempt, usage=usage(5, 1, 0, 0, kind="cumulative"), **args).admitted
    invalid = usage(availability="invalid", kind="final")
    assert not owner.observe_response(attempt, usage=invalid, **args).admitted
    view = owner.scoped_snapshot()
    assert view.summary["usage"]["input_tokens"]["known_subtotal"] == 5
    assert view.summary["usage"]["input_tokens"]["total"] is None
    assert view.summary["usage"]["input_tokens"]["invalid_attempt_count"] == 1
    replay = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert replay.summary["usage"] == view.summary["usage"] and not replay.audit_complete
    assert len(view.attempts[0].response_observations) == 2


@pytest.mark.parametrize("dispatch", [None, "not_dispatched", "bad", True, 1])
def test_settled_acquired_requires_real_dispatch_observation(recorder, dispatch):
    owner, _ = recorder
    attempt = start(owner)
    assert not owner.settle_acquired(
        attempt, layer="wire", data=b"raw", complete=True, usage=None, dispatch_state=dispatch,
    ).admitted
    assert not any(e["event"] == "settled_acquired" for e in events(owner))


@pytest.mark.parametrize("dispatch", ["entered_api_send", "entered_cli_launch", "possibly_sent", "unknown"])
def test_settled_acquired_replay_same_identity_and_dispatch_conflict(recorder, dispatch):
    owner, _ = recorder
    attempt = start(owner)
    args = dict(layer="wire", data=b"raw", complete=True, usage=None, dispatch_state=dispatch)
    raw = owner.settle_acquired(attempt, **args)
    assert raw.admitted
    assert owner.settle_acquired(attempt, observation_id=raw.observation_id, **args).admitted
    assert owner.scoped_snapshot().summary["admitted_attempt_count"] == 1
    assert len([e for e in events(owner) if e["event"] == "settled_acquired"]) == 1
    if dispatch in ("entered_api_send", "entered_cli_launch"):
        assert not finish(owner, attempt, dispatch="not_dispatched").admitted
    else:
        assert finish(owner, attempt).admitted


@pytest.mark.parametrize("scope", [None, {}, {"pass_name":None}, {"round_index":None},
    {"group_id":None}, {"purpose":None}, {"round_index":0}, {"group_id":GROUP}])
def test_scoped_projection_none_empty_and_explicit_null(recorder, scope):
    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(0, 0, 0, 0)).admitted
    assert finish(owner, attempt, duration=0).admitted
    expected = int(scope is None or not scope or None not in scope.values())
    view = owner.scoped_snapshot(scope=scope)
    assert len(view.attempts) == expected
    assert view.summary["usage"]["input_tokens"]["total"] == (0 if expected else None)
    assert view.summary["cost"] is None
    assert audit.AttemptRecorder.reopen_scoped(owner.root, RUN, scope=scope).summary == view.summary


@pytest.mark.parametrize("scope", [[], True, {"bad":1}, {"round_index":True},
    {"round_index":float("nan")}, {"group_id":"bad"}, {"pass_name":1}, {"purpose":""}])
def test_scoped_projection_invalid_filter_is_safe_readonly_fault(recorder, scope):
    owner, _ = recorder
    start(owner)
    before = (owner.root / RUN / "journal").read_bytes()
    view = owner.scoped_snapshot(scope=scope)
    assert not view.audit_complete and any(f.code == "invalid_input" for f in view.faults)
    assert owner.snapshot().audit_complete
    assert (owner.root / RUN / "journal").read_bytes() == before


@pytest.mark.parametrize("mode", ["diff", "files", "joint"])
def test_input_manifest_modes_utf8_quota_and_retained_maintenance(disk_root, mode):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=clock, utc_now=utc)
    assert owner.open().admitted
    manifest = input_manifest(mode=mode)
    if mode != "diff":
        manifest.update(exact_diff_sha256=None, diff_bytes=None)
    if mode == "files":
        manifest["files"] = [{"path":"a.py", "sha256":hashlib.sha256(b"").hexdigest(), "bytes":0}]
    if mode == "joint":
        manifest["repositories"] = [{"label":"repo", "revision":"13aa851", "sha256":SOURCE, "bytes":12}]
    assert owner.publish_input_manifest(manifest).admitted
    assert audit.AttemptRecorder.reopen_scoped(owner.root, RUN).input_manifest["mode"] == mode
    attempt = start(owner, bound_context())
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    before = (owner.root / RUN / "input-manifest.json").read_bytes()
    assert audit.maintain_audit_root(
        owner.root, monotonic=clock, utc_now=lambda:"2026-10-20T12:00:00Z", action_deadline=100,
    ).admitted
    assert (owner.root / RUN / "input-manifest.json").read_bytes() == before
    assert audit.AttemptRecorder.reopen_scoped(owner.root, RUN).input_manifest["mode"] == mode


@pytest.mark.parametrize("change", [
    {"schema_version":True}, {"run_id":str(uuid.uuid4())}, {"snapshot_id":str(uuid.uuid4())},
    {"legacy_source_hash":"d"*64}, {"mode":"bad"}, {"files":()}, {"repositories":()},
    {"exact_diff_sha256":None}, {"diff_bytes":True}, {"diff_bytes":-1},
    {"mode":"files"}, {"extra":1},
    {"files":[{"path":"../bad", "sha256":SOURCE, "bytes":1}]},
    {"repositories":[{"label":"repo", "revision":"main", "sha256":SOURCE, "bytes":1}]},
])
def test_input_manifest_invalid_closed_binding_refuses(disk_root, change):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    assert not owner.publish_input_manifest(input_manifest(**change)).admitted
    assert not (owner.root / RUN / "input-manifest.json").exists()
    assert not any(e["event"] == "input_manifest" for e in events(owner))


def test_input_manifest_missing_corrupt_changed_and_old_history_refuse_writer(disk_root):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    ack = owner.start(bound_context(), logical_id=LOGICAL, parent_id=None, cause="initial", backend=BACKEND,
                      request_digest=DIGEST, request_bytes=7, action_deadline=100)
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert not any(e["event"] == "start" for e in events(owner))
    # The original source-bound history is still observable, with no fabricated manifest.
    assert audit.AttemptRecorder.reopen_scoped(owner.root, RUN).input_manifest is None


def test_input_manifest_committed_bytes_corruption_refuses_source_and_neighbor(disk_root):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and owner.publish_input_manifest(input_manifest()).admitted
    path = owner.root / RUN / "input-manifest.json"
    path.write_bytes(path.read_bytes().replace(b"exact_diff", b"wrong_diff"))
    assert not owner.start(bound_context(), logical_id=LOGICAL, parent_id=None, cause="initial", backend=BACKEND,
                           request_digest=DIGEST, request_bytes=7, action_deadline=100).admitted
    assert not audit.AttemptRecorder.reopen_scoped(owner.root, RUN).audit_complete
    assert not other_owner(owner.root, str(uuid.uuid4())).open().admitted


def test_settled_acquired_invalid_native_sibling_still_settles_real_interval(recorder):
    owner, _ = recorder
    first = start(owner)
    sibling = start(owner, logical=str(uuid.uuid4()))
    body = b'{"usage":5}'
    raw = acquire(owner, first, None, data=body)
    assert owner.observe_response(first, source_observation_id=raw.observation_id, frame_offset=0,
                                  frame_length=len(body), usage=usage(5, 1, 0, 0), observed_backend=None).admitted
    assert not owner.observe_response(
        first, source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(body),
        usage=usage(availability="invalid"), observed_backend=None,
    ).admitted
    second = owner.settle_acquired(sibling, layer="stdout", data=b"actual sibling", complete=True,
                                  usage=None, dispatch_state="entered_cli_launch")
    assert second.admitted and second.ref
    assert finish(owner, first, outcome="failed", duration=3).admitted
    assert finish(owner, sibling, outcome="failed", duration=3, dispatch="entered_cli_launch").admitted
    assert owner.finalize("failed").admitted
    view = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert not view.audit_complete and view.summary["timing"]["work_s"] == 6
    assert view.summary["usage"]["input_tokens"]["known_subtotal"] == 5
    assert view.summary["usage"]["input_tokens"]["total"] is None


@pytest.mark.parametrize("cut", ["none", "journal_torn", "raw_corrupt", "raw_missing", "run_rebound"])
def test_settled_acquired_native_fault_still_requires_storage_integrity(recorder, cut):
    owner, _ = recorder
    first = start(owner)
    sibling = start(owner, logical=str(uuid.uuid4()))
    data = b'{"usage":5}'
    raw = acquire(owner, first, None, data=data)
    assert owner.observe_response(first, source_observation_id=raw.observation_id, frame_offset=0,
                                  frame_length=len(data), usage=usage(5, 1, 0, 0), observed_backend=None).admitted
    assert not owner.observe_response(first, source_observation_id=raw.observation_id, frame_offset=0,
                                      frame_length=len(data), usage=usage(availability="invalid"),
                                      observed_backend=None).admitted
    path = owner.root / RUN / ("raw-" + raw.ref.artifact_id)
    saved = None
    if cut == "journal_torn":
        with (owner.root / RUN / "journal").open("ab") as stream:
            stream.write(b"partial")
    elif cut == "raw_corrupt":
        path.write_bytes(b"wrong")
    elif cut == "raw_missing":
        path.unlink()
    elif cut == "run_rebound":
        saved = owner.root / (RUN + "-saved")
        (owner.root / RUN).rename(saved)
        (owner.root / RUN).mkdir(mode=0o700)
    ack = owner.settle_acquired(sibling, layer="stderr", data=b"sibling", complete=True,
                                usage=None, dispatch_state="entered_cli_launch")
    assert ack.admitted == (cut == "none")
    if saved:
        (owner.root / RUN).rmdir()
        saved.rename(owner.root / RUN)
    assert not owner.scoped_snapshot().audit_complete
    assert not owner.start(context(), logical_id=str(uuid.uuid4()), parent_id=None, cause="initial",
                           backend=BACKEND, request_digest=DIGEST, request_bytes=7,
                           action_deadline=100).admitted


@pytest.mark.parametrize("site", ["input_manifest", "response_observed", "settled_acquired"])
def test_input_manifest_response_observed_settled_acquired_real_append_close(recorder, disk_root, monkeypatch, site):
    if site == "input_manifest":
        owner = audit.AttemptRecorder(disk_root.with_name("bound-close"), context=bound_context(),
                                      monotonic=Clock(), utc_now=utc)
        assert owner.open().admitted
        attempt = None
        raw = None
    else:
        owner, _ = recorder
        attempt = start(owner)
        raw = acquire(owner, attempt, None, data=b'{"usage":5}') if site == "response_observed" else None
    original = audit._append
    close = os.close
    active = False
    cuts = []
    def append(fd, event):
        nonlocal active
        active = event["event"] == site
        try:
            return original(fd, event)
        finally:
            active = False
    def release(fd):
        if active and not cuts and os.readlink("/proc/self/fd/"+str(fd)).endswith("/journal"):
            close(fd)
            cuts.append(fd)
            raise OSError("released journal descriptor after committed event")
        return close(fd)
    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(os, "close", release)
        if site == "input_manifest":
            ack = owner.publish_input_manifest(input_manifest())
        elif site == "settled_acquired":
            ack = owner.settle_acquired(attempt, layer="stdout", data=b"raw", complete=True,
                                        usage=None, dispatch_state="entered_cli_launch")
        else:
            ack = owner.observe_response(attempt, source_observation_id=raw.observation_id,
                                         frame_offset=0, frame_length=11, usage=usage(5, 1, 0, 0),
                                         observed_backend=None)
    assert len(cuts) == 1 and not ack.admitted and ack.fault.code == "persistence_error"
    durable = events(owner)
    assert owner._events == durable and [e["sequence"] for e in durable] == list(range(len(durable)))
    replay = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert not replay.audit_complete and not owner.scoped_snapshot().audit_complete
    if site == "input_manifest":
        assert replay.input_manifest == owner.scoped_snapshot().input_manifest
    else:
        assert finish(owner, attempt, outcome="failed",
                      dispatch="entered_cli_launch" if site == "settled_acquired" else "entered_api_send").admitted
    assert owner.finalize("failed").admitted
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("cut", ["publish", "append", "readback", "space"])
def test_input_manifest_failed_publication_never_grants_dispatch(disk_root, monkeypatch, cut):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    original_publish = audit._publish
    original_append = audit._append
    original_read = audit._manifest_contents
    original_space = audit._space
    def publish(fd, name, data, **kwargs):
        if name == "input-manifest.json" and cut == "publish":
            raise OSError("manifest publication failure")
        return original_publish(fd, name, data, **kwargs)
    def append(fd, event):
        if event["event"] == "input_manifest" and cut == "append":
            raise OSError("manifest event not committed")
        return original_append(fd, event)
    def read(fd, rows):
        if cut == "readback" and audit._manifest_event(rows) is not None:
            raise OSError("manifest readback failure")
        return original_read(fd, rows)
    def space(fd, run_id, ordinary_delta=0, marker_delta=0):
        if cut == "space" and ordinary_delta:
            raise audit._Rejected("quota_refused")
        return original_space(fd, run_id, ordinary_delta, marker_delta)
    with monkeypatch.context() as patch:
        patch.setattr(audit, "_publish", publish)
        patch.setattr(audit, "_append", append)
        patch.setattr(audit, "_manifest_contents", read)
        patch.setattr(audit, "_space", space)
        assert not owner.publish_input_manifest(input_manifest()).admitted
    assert not owner.start(bound_context(), logical_id=LOGICAL, parent_id=None, cause="initial",
                           backend=BACKEND, request_digest=DIGEST, request_bytes=7, action_deadline=100).admitted
    assert not any(e["event"] == "start" for e in events(owner))
    assert not owner.scoped_snapshot().audit_complete


def test_input_manifest_utf8_payload_bound_and_immutable_conflict(disk_root):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    manifest = input_manifest(mode="files", exact_diff_sha256=None, diff_bytes=None,
                              files=[{"path":"a/\u00e9.py", "sha256":SOURCE, "bytes":0}])
    assert owner.publish_input_manifest(manifest).admitted
    before = (owner.root / RUN / "input-manifest.json").read_bytes()
    assert b"\xc3\xa9" in before
    manifest["files"][0]["bytes"] = 9
    assert owner.scoped_snapshot().input_manifest["files"][0]["bytes"] == 0
    assert not owner.publish_input_manifest(manifest).admitted
    assert (owner.root / RUN / "input-manifest.json").read_bytes() == before
    huge = input_manifest(mode="files", exact_diff_sha256=None, diff_bytes=None,
                          files=[{"path":f"{i:04d}/"+"a"*1000, "sha256":SOURCE, "bytes":1}
                                 for i in range(512)])
    with pytest.raises(audit._Rejected) as caught:
        audit._manifest(huge, dataclasses.asdict(bound_context()))
    assert caught.value.code == "quota_refused"


def test_input_manifest_old_bound_history_and_old_vocabulary_refusal(disk_root, monkeypatch):
    # Closed legacy event fixtures predate mandatory input manifests.
    root = disk_root.with_name("old-history")
    prior = audit.AttemptRecorder(root, context=context(), monotonic=Clock(), utc_now=utc)
    assert prior.open().admitted
    beginning = start(prior)
    assert finish(prior, beginning).admitted and prior.finalize("completed").admitted
    rows = events(prior)
    for event in rows:
        if event["event"] in ("run_open", "start"):
            event["context"] = dataclasses.asdict(bound_context())
    _rewrite_journal(prior, rows)
    before = (root/RUN/"journal").read_bytes()
    replay = audit.AttemptRecorder.reopen_scoped(root, RUN)
    assert replay.audit_complete and replay.input_manifest is None and len(replay.attempts) == 1
    assert replay.attempts[0].context == bound_context()
    assert (root/RUN/"journal").read_bytes() == before

    current = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert current.open().admitted and current.publish_input_manifest(input_manifest()).admitted
    before = (current.root/RUN/"journal").read_bytes()
    legacy_vocabulary = {key: value for key, value in audit._EXTRA.items()
                         if key not in ("input_manifest", "response_observed", "settled_acquired")}
    with monkeypatch.context() as patch:
        patch.setattr(audit, "_EXTRA", legacy_vocabulary)
        assert not audit.AttemptRecorder.reopen(current.root, RUN).audit_complete
    assert (current.root/RUN/"journal").read_bytes() == before


@pytest.mark.parametrize("operation", ["same_attempt_pipe", "settlement_replay", "invalid_response_replay"])
def test_settled_acquired_native_fault_same_attempt_and_identity_replay(recorder, operation):
    owner, _ = recorder
    first = start(owner)
    sibling = start(owner, logical=str(uuid.uuid4()))
    body = b'{"usage":5}'
    raw = acquire(owner, first, None, data=body)
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(body),
                observed_backend=None)
    assert owner.observe_response(first, usage=usage(5, 1, 0, 0), **args).admitted
    invalid = usage(availability="invalid")
    assert not owner.observe_response(first, usage=invalid, **args).admitted
    if operation == "invalid_response_replay":
        identity = owner.scoped_snapshot().attempts[0].response_observations[-1].observation_id
        before = (owner.root/RUN/"journal").read_bytes()
        ack = owner.observe_response(first, observation_id=identity, usage=invalid, **args)
        assert not ack.admitted and ack.fault.code == "invalid_input"
        assert (owner.root/RUN/"journal").read_bytes() == before
    else:
        attempt = first if operation == "same_attempt_pipe" else sibling
        values = dict(layer="stderr", data=b"actual second pipe", complete=True, usage=None,
                      dispatch_state="entered_api_send")
        settled = owner.settle_acquired(attempt, **values)
        assert settled.admitted and settled.ref is not None
        if operation == "settlement_replay":
            before = (owner.root/RUN/"journal").read_bytes()
            repeated = owner.settle_acquired(attempt, observation_id=settled.observation_id, **values)
            assert repeated.admitted and repeated.ref == settled.ref
            assert (owner.root/RUN/"journal").read_bytes() == before
    assert not owner.scoped_snapshot().audit_complete
    assert owner.scoped_snapshot().summary["usage"]["input_tokens"]["known_subtotal"] == 5
    assert owner.scoped_snapshot().summary["usage"]["input_tokens"]["total"] is None
    assert finish(owner, first, outcome="failed").admitted
    assert finish(owner, sibling, outcome="failed").admitted
    assert owner.finalize("failed").admitted
    assert not audit.AttemptRecorder.reopen_scoped(owner.root, RUN).audit_complete


@pytest.mark.parametrize("history", ["completed", "native_fault"])
def test_response_observed_durable_frame_range_requires_actual_complete_body(recorder, history):
    owner, _ = recorder
    first = start(owner)
    sibling = start(owner, logical=str(uuid.uuid4()))
    body = b'{"usage":5}'
    raw = acquire(owner, first, None, data=body)
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(body),
                observed_backend=None)
    assert owner.observe_response(first, usage=usage(5, 1, 0, 0), **args).admitted
    if history == "completed":
        assert finish(owner, first).admitted and finish(owner, sibling).admitted
        assert owner.finalize("completed").admitted
    else:
        assert not owner.observe_response(first, usage=usage(availability="invalid"), **args).admitted
    rows = events(owner)
    native = next(event for event in rows if event["event"] == "response_observed")
    native["frame_offset"] = 1
    native["frame_length"] = len(body)-1
    _rewrite_journal(owner, rows)
    # These bytes are in range but are not a complete native JSON frame.
    assert (owner.root/RUN/("raw-"+raw.ref.artifact_id)).read_bytes() == body
    if history == "completed":
        assert not audit.AttemptRecorder.reopen_scoped(owner.root, RUN).audit_complete
        assert not other_owner(owner.root, str(uuid.uuid4())).open().admitted
    else:
        owner._events = rows
        assert not owner.settle_acquired(sibling, layer="stderr", data=b"real pipe", complete=True,
                                         usage=None, dispatch_state="entered_api_send").admitted


def test_response_observed_input_manifest_native_immutable_tombstone(disk_root):
    clock = Clock()
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=clock, utc_now=utc)
    assert owner.open().admitted and owner.publish_input_manifest(input_manifest()).admitted
    attempt = start(owner, bound_context())
    body = b'{"usage":0}'
    raw = acquire(owner, attempt, None, data=body)
    native = {"prompt_tokens":0}
    observation = dataclasses.replace(usage(0, 0, 0, 0), native_usage=native)
    assert owner.observe_response(attempt, source_observation_id=raw.observation_id, frame_offset=0,
                                  frame_length=len(body), usage=observation,
                                  observed_backend=dataclasses.replace(BACKEND, observed_model="actual")).admitted
    native["prompt_tokens"] = 999
    view = owner.scoped_snapshot()
    assert view.attempts[0].raw_refs == (raw.ref,)
    assert view.attempts[0].response_observations[0].usage.native_usage["prompt_tokens"] == 0
    with pytest.raises(TypeError):
        view.attempts[0].response_observations[0].usage.native_usage["prompt_tokens"] = 9
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.attempts[0].duration_s = 9
    assert finish(owner, attempt).admitted and owner.finalize("completed").admitted
    assert audit.maintain_audit_root(owner.root, monotonic=clock,
                                    utc_now=lambda:"2026-10-20T12:00:00Z", action_deadline=100).admitted
    assert not (owner.root/RUN/("raw-"+raw.ref.artifact_id)).exists()
    replay = audit.AttemptRecorder.reopen_scoped(owner.root, RUN)
    assert replay.audit_complete and replay.input_manifest["mode"] == "diff"
    assert replay.summary["usage"]["input_tokens"]["total"] == 0
    assert replay.attempts[0].response_observations[0].observed_backend.observed_model == "actual"


@pytest.mark.parametrize("field,bad", [
    ("path", "../bad"), ("path", "/absolute"), ("path", "a//b"), ("path", "a\\b"),
    ("path", "a\0b"), ("path", "a"*1025), ("sha256", "bad"),
    ("bytes", True), ("bytes", -1), ("bytes", 1 << 63), ("extra", 1),
])
def test_input_manifest_file_row_guard_has_unmasked_real_writer_control(disk_root, field, bad):
    row = {"path":"a.py", "sha256":SOURCE, "bytes":0}
    manifest = input_manifest(mode="files", exact_diff_sha256=None, diff_bytes=None, files=[row])
    assert audit._manifest(manifest, dataclasses.asdict(bound_context()))
    row[field] = bad
    with pytest.raises(audit._Rejected):
        audit._manifest(manifest, dataclasses.asdict(bound_context()))
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and not owner.publish_input_manifest(manifest).admitted
    assert not (owner.root/RUN/"input-manifest.json").exists()


@pytest.mark.parametrize("field,bad", [
    ("label", ""), ("label", "a"*129), ("revision", "a\0b"), ("revision", 1),
    ("sha256", "bad"), ("bytes", True), ("bytes", -1), ("bytes", 1 << 63), ("extra", 1),
])
def test_input_manifest_repository_row_guard_has_unmasked_real_writer_control(disk_root, field, bad):
    row = {"label":"repo", "revision":"main", "sha256":SOURCE, "bytes":0}
    manifest = input_manifest(mode="joint", exact_diff_sha256=None, diff_bytes=None, repositories=[row])
    assert audit._manifest(manifest, dataclasses.asdict(bound_context()))
    row[field] = bad
    with pytest.raises(audit._Rejected):
        audit._manifest(manifest, dataclasses.asdict(bound_context()))
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and not owner.publish_input_manifest(manifest).admitted
    assert not (owner.root/RUN/"input-manifest.json").exists()


@pytest.mark.parametrize("case", ["unsorted_paths", "duplicate_paths", "duplicate_repositories", "many_files", "many_repositories"])
def test_input_manifest_order_count_and_identity_guard(disk_root, case):
    row = {"path":"a.py", "sha256":SOURCE, "bytes":0}
    if case in ("unsorted_paths", "duplicate_paths", "many_files"):
        rows = [dict(row, path="b.py"), row] if case == "unsorted_paths" else [row]*2
        if case == "many_files":
            rows = [dict(row, path=f"{i:03d}.py") for i in range(513)]
        manifest = input_manifest(mode="files", exact_diff_sha256=None, diff_bytes=None, files=rows)
    else:
        row = {"label":"repo", "revision":"main", "sha256":SOURCE, "bytes":0}
        rows = [row]*2 if case == "duplicate_repositories" else [dict(row, label=str(i)) for i in range(513)]
        manifest = input_manifest(mode="joint", exact_diff_sha256=None, diff_bytes=None, repositories=rows)
    with pytest.raises(audit._Rejected):
        audit._manifest(manifest, dataclasses.asdict(bound_context()))
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and not owner.publish_input_manifest(manifest).admitted
    assert not any(event["event"] == "input_manifest" for event in events(owner))


def test_response_observed_event_cap_refuses_before_native_commit(recorder):
    owner, _ = recorder
    attempt = start(owner)
    body = b'{"usage":1}'
    raw = acquire(owner, attempt, None, data=body)
    oversized = dataclasses.replace(usage(1, 1, 0, 0),
                                    native_usage={key:10**160 for key in audit._NATIVE})
    ack = owner.observe_response(attempt, source_observation_id=raw.observation_id,
                                 frame_offset=0, frame_length=len(body), usage=oversized,
                                 observed_backend=None)
    assert not ack.admitted and ack.fault.code == "quota_refused"
    assert not any(event["event"] == "response_observed" for event in events(owner))
    assert (owner.root/RUN/("raw-"+raw.ref.artifact_id)).read_bytes() == body


def test_scoped_projection_three_unequal_roles_native_usage_and_null_cost(recorder):
    owner, clock = recorder
    roles = ["qodo-review", "code-review-expert", "adversarial-qe"]
    attempts = []
    for role, inp, out in zip(roles, (3510, 7885, 888), (10, 8, 5), strict=True):
        attempt = start(owner, context(pass_name=role), logical=str(uuid.uuid4()))
        attempts.append(attempt)
        body = json.dumps({"usage":{"input_tokens":inp,"output_tokens":out}}).encode()
        raw = acquire(owner, attempt, None, data=body)
        assert owner.observe_response(attempt, source_observation_id=raw.observation_id,
                                      frame_offset=0, frame_length=len(body), usage=usage(inp, out, 0, 0),
                                      observed_backend=None).admitted
    clock.value = 4
    for attempt in attempts:
        assert finish(owner, attempt, duration=3).admitted
    assert owner.finalize("completed").admitted
    for role, inp, out in zip(roles, (3510, 7885, 888), (10, 8, 5), strict=True):
        scope = {"pass_name":role}
        view = audit.AttemptRecorder.reopen_scoped(owner.root, RUN, scope=scope)
        assert view.summary == owner.scoped_snapshot(scope=scope).summary
        assert view.summary["usage"]["input_tokens"]["total"] == inp
        assert view.summary["usage"]["output_tokens"]["total"] == out
        assert view.summary["timing"]["work_s"] == 3 and view.summary["timing"]["wall_s"] == 4
        assert view.summary["cost"] is None and view.summary["admitted_attempt_count"] == 1
        assert view.attempts[0].duration_s == 3
        assert view.attempts[0].dispatch_state == "entered_api_send" and view.attempts[0].outcome == "completed"
    assert owner.scoped_snapshot().summary["usage"]["input_tokens"]["total"] == 12283
    assert owner.scoped_snapshot().summary["timing"]["work_s"] == 9


@pytest.mark.parametrize("site", ["before", "checked_after", "direct_after"])
def test_input_manifest_each_qualified_readback_site_is_required(disk_root, monkeypatch, site):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    if site == "before":
        path = owner.root/RUN/"journal"
        with path.open("ab") as stream:
            stream.write(b"torn")
        before = path.read_bytes()
        assert not owner.publish_input_manifest(input_manifest()).admitted
        assert not (owner.root/RUN/"input-manifest.json").exists() and path.read_bytes() == before
        return
    checked = owner._checked
    read = audit._manifest_contents
    in_checked = False
    cuts = []
    def validate(handle, fd, **kwargs):
        nonlocal in_checked
        in_checked = True
        try:
            return checked(handle, fd, **kwargs)
        finally:
            in_checked = False
    def readback(fd, rows):
        if (not cuts and audit._manifest_event(rows) is not None
                and in_checked == (site == "checked_after")):
            cuts.append(site)
            raise OSError("specific postpublication readback failure")
        return read(fd, rows)
    with monkeypatch.context() as patch:
        patch.setattr(owner, "_checked", validate)
        patch.setattr(audit, "_manifest_contents", readback)
        ack = owner.publish_input_manifest(input_manifest())
    assert cuts == [site] and not ack.admitted
    assert not owner.scoped_snapshot().audit_complete
    assert (owner.root/RUN/"input-manifest.json").read_bytes()


@pytest.mark.parametrize("cut", ["journal_close", "final_publish", "committed_marker"])
def test_failed_finalization_cannot_publish_freeze(recorder, monkeypatch, cut):
    owner, _ = recorder
    attempt = start(owner)
    assert finish(owner, attempt).admitted
    intact = other_owner(owner.root, str(uuid.uuid4()))
    assert intact.open().admitted and intact.finalize("completed").admitted
    assert intact.set_frozen(True).admitted
    original_append, original_close, original_publish = audit._append, os.close, audit._publish
    active = False
    hits = []

    def append(fd, event):
        nonlocal active
        active = event["event"] == "run_final"
        try:
            return original_append(fd, event)
        finally:
            active = False

    def close(fd):
        if cut == "journal_close" and active and not hits and os.readlink(
            "/proc/self/fd/" + str(fd)
        ).endswith("/journal"):
            original_close(fd)
            hits.append("journal_close")
            raise OSError("journal close diagnostic after actual write/fsync/close")
        return original_close(fd)

    def publish(fd, name, value, **kwargs):
        if name == "final" and cut != "journal_close":
            hits.append(cut)
            if cut == "committed_marker":
                original_publish(fd, name, value, **kwargs)
            raise OSError("final marker publication acknowledgement failed")
        return original_publish(fd, name, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(os, "close", close)
        patch.setattr(audit, "_publish", publish)
        denied = owner.finalize("completed")
    assert hits == [cut] and not denied.admitted and denied.fault.code == "persistence_error"
    assert not owner._closed and not owner.snapshot().audit_complete
    assert events(owner)[-1]["event"] == "run_final"
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted
    before = (owner.root / RUN / "journal").read_bytes()
    final_path = owner.root / RUN / "final"
    final_before = final_path.read_bytes() if final_path.exists() else None
    frozen = owner.set_frozen(True)
    assert not frozen.admitted and frozen.fault.code == "finalized"
    assert not (owner.root / RUN / "freeze").exists()
    assert (owner.root / RUN / "journal").read_bytes() == before
    assert (final_path.read_bytes() if final_path.exists() else None) == final_before
    assert denied.fault in owner.snapshot().faults and not owner.snapshot().audit_complete
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted
    assert not owner.start(
        context(), logical_id=str(uuid.uuid4()), parent_id=None, cause="initial",
        backend=BACKEND, request_digest=DIGEST, request_bytes=7, action_deadline=100,
    ).admitted


class ManifestEqualText(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        return True


@pytest.mark.parametrize("field", ["run_id", "snapshot_id", "legacy_source_hash"])
@pytest.mark.parametrize("underlying", ["canonical", "spoofed"])
def test_manifest_exact_builtin_binding_types(disk_root, field, underlying):
    good = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert good.open().admitted and good.publish_input_manifest(input_manifest()).admitted
    owner = audit.AttemptRecorder(disk_root.parent / "bad", context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    manifest = input_manifest()
    manifest[field] = ManifestEqualText(manifest[field] if underlying == "canonical" else "spoofed")
    ack = owner.publish_input_manifest(manifest)
    assert not ack.admitted and ack.fault.code == "invalid_input"
    assert not (owner.root / RUN / "input-manifest.json").exists()
    assert not any(event["event"] == "input_manifest" for event in events(owner))
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("field", ["run_id", "snapshot_id", "legacy_source_hash"])
def test_manifest_binding_rejects_before_custom_equality(disk_root, field):
    calls = []

    class ExplodingText(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(other)
            raise AssertionError("custom equality must not run before type validation")

    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    manifest = input_manifest()
    manifest[field] = ExplodingText(manifest[field])
    ack = owner.publish_input_manifest(manifest)
    assert not ack.admitted and ack.fault.code == "invalid_input" and not calls
    assert not (owner.root / RUN / "input-manifest.json").exists()
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("site", ["top", "file", "repository"])
def test_manifest_closed_keys_require_builtin_strings(disk_root, site):
    class ForeignKey(str):
        pass

    manifest = input_manifest()
    if site == "file":
        manifest.update(mode="files", exact_diff_sha256=None, diff_bytes=None,
                        files=[{"path":"a.py", "sha256":SOURCE, "bytes":1}])
        row, key = manifest["files"][0], "path"
    elif site == "repository":
        manifest.update(mode="joint", exact_diff_sha256=None, diff_bytes=None,
                        repositories=[{"label":"repo", "revision":"ea87", "sha256":SOURCE, "bytes":1}])
        row, key = manifest["repositories"][0], "label"
    else:
        row, key = manifest, "run_id"
    good = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert good.open().admitted and good.publish_input_manifest(manifest).admitted
    value = row.pop(key)
    row[ForeignKey(key)] = value
    owner = audit.AttemptRecorder(disk_root.parent / "bad", context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted
    ack = owner.publish_input_manifest(manifest)
    assert not ack.admitted and ack.fault.code == "integrity_error"
    assert not (owner.root / RUN / "input-manifest.json").exists()
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("site", ["top", "files", "file", "repositories", "repository"])
def test_manifest_custom_containers_have_no_publication(disk_root, site):
    class ForeignDict(dict):
        pass

    class ForeignList(list):
        pass

    manifest = input_manifest()
    if site in ("files", "file"):
        manifest.update(mode="files", exact_diff_sha256=None, diff_bytes=None,
                        files=[{"path":"a.py", "sha256":SOURCE, "bytes":1}])
    if site in ("repositories", "repository"):
        manifest.update(mode="joint", exact_diff_sha256=None, diff_bytes=None,
                        repositories=[{"label":"repo", "revision":"ea87", "sha256":SOURCE, "bytes":1}])
    good = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert good.open().admitted and good.publish_input_manifest(manifest).admitted
    if site == "top":
        manifest = ForeignDict(manifest)
    elif site in ("files", "repositories"):
        manifest[site] = ForeignList(manifest[site])
    else:
        key = "files" if site == "file" else "repositories"
        manifest[key][0] = ForeignDict(manifest[key][0])
    owner = audit.AttemptRecorder(disk_root.parent / "bad", context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and not owner.publish_input_manifest(manifest).admitted
    assert not (owner.root / RUN / "input-manifest.json").exists()
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


_CURRENT_RECEIPT_CASES = [
    (operation, target) for operation in ("finish", "finalize", "freeze", "unfrozen")
    for target in ("raw", "journal", "admission", "manifest", "final", "freeze")
    if (target != "final" or operation != "finish")
    and (target != "freeze" or operation == "freeze")
]


@pytest.mark.parametrize("operation,target", _CURRENT_RECEIPT_CASES)
@pytest.mark.parametrize("corruption", ["changed", "missing"])
def test_idempotent_receipt_checks_current_files(disk_root, operation, target, corruption):
    owner = audit.AttemptRecorder(disk_root, context=bound_context(), monotonic=Clock(), utc_now=utc)
    assert owner.open().admitted and owner.publish_input_manifest(input_manifest()).admitted
    attempt = start(owner, bound_context())
    raw = acquire(owner, attempt, usage(5, 4, 2, 1))
    assert raw.admitted and finish(owner, attempt).admitted
    if operation != "finish":
        assert owner.finalize("completed").admitted
    if operation == "freeze":
        assert owner.set_frozen(True).admitted
    call = (lambda: finish(owner, attempt)) if operation == "finish" else (
        (lambda: owner.finalize("completed")) if operation == "finalize" else (
            lambda: owner.set_frozen(operation == "freeze")
        )
    )
    before = (owner.root / RUN / "journal").read_bytes()
    assert call().admitted and (owner.root / RUN / "journal").read_bytes() == before
    assert audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
    name = "raw-" + raw.ref.artifact_id if target == "raw" else (
        "input-manifest.json" if target == "manifest" else target
    )
    path = owner.root / RUN / name
    original = path.read_bytes()
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"x" * len(original) if target == "raw" else b"{")
    try:
        denied = call()
        assert not denied.admitted and denied.fault.code in ("integrity_error", "persistence_error")
        assert not owner.snapshot().audit_complete
        assert not audit.AttemptRecorder.reopen(owner.root, RUN).audit_complete
        if corruption == "missing":
            assert not path.exists()
        else:
            assert path.read_bytes() == (b"x" * len(original) if target == "raw" else b"{")
    finally:
        path.write_bytes(original)
        path.chmod(0o600)
    faults = owner.snapshot().faults
    assert call().admitted and path.read_bytes() == original
    assert not owner.snapshot().audit_complete and owner.snapshot().faults == faults
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


@pytest.mark.parametrize("reader", ["live", "reopened"])
def test_scope_requires_builtin_keys_without_writer_fault(recorder, reader):
    class ForeignKey(str):
        pass

    owner, _ = recorder
    attempt = start(owner)
    assert acquire(owner, attempt, usage(5, 4, 2, 1)).admitted and finish(owner, attempt).admitted
    method = owner.scoped_snapshot if reader == "live" else (
        lambda **kwargs: audit.AttemptRecorder.reopen_scoped(owner.root, RUN, **kwargs)
    )
    assert method(scope={"purpose":"review"}).audit_complete
    before = (owner.root / RUN / "journal").read_bytes()
    view = method(scope={ForeignKey("purpose"):"review"})
    assert not view.audit_complete and any(f.code == "invalid_input" for f in view.faults)
    assert owner.snapshot().audit_complete and (owner.root / RUN / "journal").read_bytes() == before


def test_unfrozen_ack_requires_actual_matching_freeze_state(recorder, monkeypatch):
    owner, _ = recorder
    assert owner.set_frozen(False).admitted
    original_append, original_close = audit._append, os.close
    active = False
    hits = []

    def append(fd, event):
        nonlocal active
        active = event["event"] == "freeze"
        try:
            return original_append(fd, event)
        finally:
            active = False

    def close(fd):
        if active and not hits and os.readlink("/proc/self/fd/" + str(fd)).endswith("/journal"):
            original_close(fd)
            hits.append(fd)
            raise OSError("freeze journal close diagnostic after actual write/fsync/close")
        return original_close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(os, "close", close)
        denied = owner.set_frozen(True)
    assert len(hits) == 1 and not denied.admitted and denied.fault.code == "persistence_error"
    assert not owner._frozen and json.loads((owner.root/RUN/"freeze").read_bytes())["frozen"] is True
    before = (owner.root/RUN/"journal").read_bytes()
    refused = owner.set_frozen(False)
    assert not refused.admitted and refused.fault.code == "integrity_error"
    assert (owner.root/RUN/"journal").read_bytes() == before and not owner.snapshot().audit_complete
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted


def test_idempotent_native_fault_receipts_keep_storage_settlement_checks(recorder):
    owner, _ = recorder
    attempt = start(owner)
    body = b'{"usage":5}'
    raw = acquire(owner, attempt, None, data=body)
    args = dict(source_observation_id=raw.observation_id, frame_offset=0, frame_length=len(body),
                observed_backend=None)
    assert owner.observe_response(attempt, usage=usage(5, 1, 0, 0), **args).admitted
    assert not owner.observe_response(attempt, usage=usage(availability="invalid"), **args).admitted
    assert finish(owner, attempt, outcome="failed").admitted
    assert finish(owner, attempt, outcome="failed").admitted
    assert owner.finalize("failed").admitted and owner.finalize("failed").admitted
    path = owner.root/RUN/("raw-"+raw.ref.artifact_id)
    original = path.read_bytes()
    path.write_bytes(b'x'*len(original))
    assert not finish(owner, attempt, outcome="failed").admitted
    assert not owner.finalize("failed").admitted
    path.write_bytes(original)
    faults = owner.snapshot().faults
    assert finish(owner, attempt, outcome="failed").admitted and owner.finalize("failed").admitted
    assert not owner.snapshot().audit_complete and owner.snapshot().faults == faults
    view = owner.scoped_snapshot()
    assert view.summary["usage"]["input_tokens"]["known_subtotal"] == 5
    assert view.summary["usage"]["input_tokens"]["total"] is None
    assert not audit.AttemptRecorder.reopen_scoped(owner.root, RUN).audit_complete
    assert not owner.set_frozen(True).admitted


@pytest.mark.parametrize("operation", ["finish", "finalize"])
@pytest.mark.parametrize("repeated", [False, True])
def test_orphan_settlement_receipts_validate_all_committed_raw(disk_root, monkeypatch, operation, repeated):
    def prepared(root):
        owner = audit.AttemptRecorder(root, context=context(), monotonic=Clock(), utc_now=utc)
        assert owner.open().admitted
        first = start(owner)
        second = start(owner, logical=str(uuid.uuid4()))
        raw = acquire(owner, first, usage(5, 4, 2, 1))
        assert raw.admitted
        original_append = audit._append

        def append(fd, event):
            if event["event"] == "acquired" and event["layer"] == "stderr":
                raise OSError("raw file published before acquired event failed")
            return original_append(fd, event)

        with monkeypatch.context() as patch:
            patch.setattr(audit, "_append", append)
            denied = acquire(owner, first, None, data=b"retained orphan", layer="stderr")
        assert not denied.admitted and denied.fault.code == "persistence_error"
        names = [p.name for p in (owner.root/RUN).iterdir() if p.name.startswith("raw-")]
        assert len(names) == 2 and not owner.snapshot().audit_complete
        if operation == "finalize":
            assert finish(owner, first, outcome="failed").admitted
            assert finish(owner, second, outcome="failed").admitted
            def call():
                return owner.finalize("failed")
        else:
            def call():
                return finish(owner, first, outcome="failed")
        if repeated:
            assert call().admitted
        return owner, raw, call

    intact, _, intact_call = prepared(disk_root)
    assert intact_call().admitted and not intact.snapshot().audit_complete
    owner, raw, call = prepared(disk_root.parent/"corrupted")
    path = owner.root/RUN/("raw-"+raw.ref.artifact_id)
    original = path.read_bytes()
    path.write_bytes(b'x'*len(original))
    try:
        ack = call()
        assert not ack.admitted and ack.fault.code == "integrity_error"
        assert path.read_bytes() == b'x'*len(original) and not owner.snapshot().audit_complete
    finally:
        path.write_bytes(original)
    faults = owner.snapshot().faults
    assert call().admitted and not owner.snapshot().audit_complete
    assert owner.snapshot().faults == faults
    assert other_owner(owner.root, str(uuid.uuid4())).open().admitted
