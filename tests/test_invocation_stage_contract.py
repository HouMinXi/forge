"""Stage protocol controls and qualified-storage reader/writer tests."""

from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError
import hashlib

import pytest

from code_forge import invocation_audit as audit


RUN = "00000000-0000-4000-8000-000000000001"
ATTEMPT = "00000000-0000-4000-8000-000000000002"
OBSERVATION = "00000000-0000-4000-8000-000000000003"
ARTIFACT = "00000000-0000-4000-8000-000000000004"
OTHER = "00000000-0000-4000-8000-000000000005"
SOURCE = "a" * 64


def digest(data):
    return hashlib.sha256(data).hexdigest()


def add(events, kind, **fields):
    event = dict(
        schema_version=2,
        run_id=RUN,
        event_id=f"00000000-0000-4000-8000-{100 + len(events):012d}",
        sequence=len(events),
        event=kind,
        utc="2026-11-04T00:00:00Z",
        elapsed_s=0.0,
        **fields,
    )
    events.append(event)
    return event


def history():
    events = []
    context = asdict(
        audit.InvocationContext(RUN, SOURCE, OTHER, 0, "review", None, None, "review", None)
    )
    add(
        events,
        "run_open",
        context=context,
        capability=dict(
            profile_id="linux-local-audit-v1",
            filesystem_type="ext4",
            root_device=1,
            root_inode=1,
            root_mount_id=1,
            runtime_fingerprint="b" * 64,
        ),
        owner=dict(pid=1, start_ticks="1", owner_id=RUN),
        reservation=audit._reservation_policy(2),
    )
    add(
        events,
        "start",
        context=context.copy(),
        attempt_id=ATTEMPT,
        logical_id=ATTEMPT,
        parent_id=None,
        cause="initial",
        backend=asdict(audit.BackendObservation("openai", "fixture", "model", None, "b" * 64)),
        request_digest=digest(b"request"),
        request_bytes=7,
    )
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OBSERVATION,
        artifact_id=ARTIFACT,
        layer="wire",
    )
    return events


def block(events, data=b"abc"):
    add(events, "stage_checkpoint", artifact_id=ARTIFACT, retained_bytes=len(data), sha256=digest(data))


def seal(events):
    add(events, "stage_seal", artifact_id=ARTIFACT)


def adopt(events, data=b"abc"):
    return add(
        events,
        "acquired",
        attempt_id=ATTEMPT,
        observation_id=OBSERVATION,
        layer="wire",
        ref=asdict(audit.RawRef(ARTIFACT, "wire", digest(data), len(data), len(data), False)),
        complete=True,
        usage=None,
    )


def test_registered_begin_is_incomplete_before_file_creation():
    events = history()
    before = deepcopy(events)
    (projection,) = audit._replay_stage_candidate(events, {})
    assert projection.identity == audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire")
    assert projection.checkpoint_bytes == projection.unverified_tail_bytes == 0
    assert not projection.adopted and not projection.sealed
    assert projection.context.source_hash == SOURCE
    assert projection.context.snapshot_id == OTHER
    assert events == before
    with pytest.raises(FrozenInstanceError):
        projection.adopted = True


def test_multiple_checkpoints_same_prefix_retry_and_exact_once_adoption():
    events = history()
    block(events)
    add(events, "stage_checkpoint", artifact_id=ARTIFACT, retained_bytes=3, sha256=digest(b"abc"))
    block(events, b"abcdef")
    seal(events)
    adopt(events, b"abcdef")
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: b"abcdef"})
    assert projection.adopted and projection.sealed
    assert projection.checkpoint_bytes == 6
    assert projection.unverified_tail_bytes == 0
    # Exact journal retransmission is the same event, not another adoption.
    events.append(events[-1].copy())
    assert audit._replay_stage_candidate(events, {ARTIFACT: b"abcdef"}) == (projection,)


@pytest.mark.parametrize("tail", [b"", b"d", b"def"])
def test_reopen_exposes_unknown_tail_without_permission_to_adopt(tail):
    events = history()
    block(events)
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: b"abc" + tail})
    assert not projection.sealed and not projection.adopted
    assert projection.checkpoint_bytes == 3
    assert projection.unverified_tail_bytes == len(tail)
    # Neither a checkpoint nor a diagnostic projection is a seal/adopt ACK.
    adopt(events)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc" + tail})


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "mixed",
        "unknown_attempt",
        "source_mismatch",
        "unknown_field",
        "unknown_artifact",
        "checkpoint_short",
        "checkpoint_hash",
        "shrink",
        "seal_without_checkpoint",
        "adopt_without_seal",
        "adopt_wrong_artifact",
        "adopt_wrong_observation",
        "adopt_wrong_attempt",
        "adopt_twice",
        "write_after_seal",
        "unknown_raw",
        "sealed_tail",
        "missing_checkpoint_file",
        "changed_prefix",
        "oversized_tail",
        "duplicate_layer",
        "duplicate_observation",
        "closed_attempt",
        "complete_final_with_stage",
    ],
)
def test_negative_protocol_controls(mutation):
    events = history()
    artifacts = {ARTIFACT: b"abc"}
    if mutation == "legacy":
        for event in events:
            event["schema_version"] = 1
    elif mutation == "mixed":
        events[-1]["schema_version"] = 1
    elif mutation == "unknown_attempt":
        events[-1]["attempt_id"] = OTHER
    elif mutation == "source_mismatch":
        events[1]["context"]["source_hash"] = "c" * 64
    elif mutation == "unknown_field":
        events[-1]["surprise"] = True
    elif mutation == "unknown_artifact":
        add(events, "stage_checkpoint", artifact_id=OTHER, retained_bytes=3, sha256=digest(b"abc"))
    elif mutation == "seal_without_checkpoint":
        artifacts = {}
        seal(events)
    elif mutation == "closed_attempt":
        events.pop()
        add(
            events,
            "finish",
            attempt_id=ATTEMPT,
            outcome="failed",
            error_class=None,
            duration_s=0.0,
            dispatch_state="unknown",
        )
        add(
            events,
            "stage_begin",
            attempt_id=ATTEMPT,
            observation_id=OBSERVATION,
            artifact_id=ARTIFACT,
            layer="wire",
        )
    else:
        block(events)
        if mutation == "checkpoint_short":
            events[-1]["retained_bytes"] = 2
        elif mutation == "checkpoint_hash":
            events[-1]["sha256"] = digest(b"wrong")
        elif mutation == "shrink":
            add(events, "stage_checkpoint", artifact_id=ARTIFACT, retained_bytes=2, sha256=digest(b"ab"))
        elif mutation == "adopt_without_seal":
            adopt(events)
        elif mutation.startswith("adopt_"):
            seal(events)
            acquired = adopt(events)
            if mutation == "adopt_wrong_artifact":
                acquired["ref"]["artifact_id"] = OTHER
            elif mutation == "adopt_wrong_observation":
                acquired["observation_id"] = OTHER
            elif mutation == "adopt_wrong_attempt":
                acquired["attempt_id"] = OTHER
            else:
                adopt(events)
        elif mutation == "write_after_seal":
            seal(events)
            add(
                events,
                "stage_checkpoint",
                artifact_id=ARTIFACT,
                retained_bytes=4,
                sha256=digest(b"abcd"),
            )
        elif mutation == "unknown_raw":
            artifacts[OTHER] = b"unknown"
        elif mutation == "sealed_tail":
            seal(events)
            artifacts[ARTIFACT] = b"abcd"
        elif mutation == "missing_checkpoint_file":
            artifacts = {}
        elif mutation == "changed_prefix":
            artifacts[ARTIFACT] = b"abd"
        elif mutation == "oversized_tail":
            artifacts[ARTIFACT] = b"abc" + b"d" * audit.V2_WIRE_BYTES
        elif mutation in ("duplicate_layer", "duplicate_observation"):
            add(
                events,
                "stage_begin",
                attempt_id=ATTEMPT,
                observation_id=OTHER if mutation == "duplicate_layer" else OBSERVATION,
                artifact_id=OTHER,
                layer="wire" if mutation == "duplicate_layer" else "decoded",
            )
        elif mutation == "complete_final_with_stage":
            add(
                events,
                "finish",
                attempt_id=ATTEMPT,
                outcome="completed",
                error_class=None,
                duration_s=0.0,
                dispatch_state="unknown",
            )
            add(events, "run_final", outcome="completed", audit_complete=True)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, artifacts)


@pytest.mark.parametrize("layer,allowed", [("stdout", True), ("stderr", True), ("decoded", True)])
def test_shared_wire_cap_and_separate_decoded_reservation(layer, allowed):
    events = history()
    add(events, "stage_begin", attempt_id=ATTEMPT, observation_id=OTHER, artifact_id=OTHER, layer=layer)
    artifacts = {ARTIFACT: b"a" * audit.BODY_BYTES, OTHER: b"b"}
    if allowed:
        assert len(audit._replay_stage_candidate(events, artifacts)) == 2
    else:
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, artifacts)


def test_registered_v2_stage_vocabulary_uses_production_framing():
    events = history()
    rows, _, _, torn, invalid = audit._read_events(b"".join(map(audit._frame, events)), RUN)
    assert rows == events
    assert not torn and not invalid


def test_metadata_budget_counts_checkpoint_retries(monkeypatch):
    events = history()
    block(events)
    size = sum(len(audit._frame(event)) for event in events) + 3
    monkeypatch.setattr(audit, "V2_RUN_BYTES", size + audit.MARKER_BYTES)
    # Keep declared reservation consistent with the deliberately small test quota.
    events[0]["reservation"]["run_bytes"] = audit.V2_RUN_BYTES
    add(events, "stage_checkpoint", artifact_id=ARTIFACT, retained_bytes=3, sha256=digest(b"abc"))
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})


def test_adoption_transfers_full_cap_without_charging_payload_twice():
    events = history()
    data = b"x" * audit.BODY_BYTES
    block(events, data)
    seal(events)
    adopt(events, data)
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: data})
    assert projection.adopted and projection.checkpoint_bytes == audit.BODY_BYTES


def test_every_checkpoint_hash_is_verified_not_only_latest():
    events = history()
    block(events, b"abd")
    block(events, b"abcdef")
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abcdef"})


def test_same_length_checkpoint_cannot_replace_digest():
    events = history()
    block(events)
    block(events, b"abd")
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abd"})


def test_nonstage_v1_replay_uses_existing_fold_unchanged():
    events = history()[:2]
    for event in events:
        event["schema_version"] = 1
    events[0]["reservation"] = audit._reservation_policy(1)
    assert audit._replay_stage_candidate(events, {}) == ()


def test_registered_begin_with_unknown_bytes_is_not_checkpoint_evidence():
    (projection,) = audit._replay_stage_candidate(history(), {ARTIFACT: b"partial write"})
    assert projection.checkpoint_bytes == 0
    assert projection.checkpoint_sha256 == digest(b"")
    assert projection.unverified_tail_bytes == len(b"partial write")
    assert not projection.sealed and not projection.adopted


def test_duplicate_event_frames_still_consume_metadata_budget(monkeypatch):
    events = history()
    block(events)
    # The artificial quota is a pure byte-budget test, not a storage override.
    budget = sum(len(audit._frame(event)) for event in events) + 100
    monkeypatch.setattr(audit, "V2_RUN_BYTES", budget + audit.MARKER_BYTES)
    events[0]["reservation"]["run_bytes"] = audit.V2_RUN_BYTES
    audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    events.extend([events[-1].copy()] * 2)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})


@pytest.mark.parametrize("ordinary_count", [16, 32, 64])
def test_candidate_uses_one_authoritative_fold_and_one_event_visit(monkeypatch, ordinary_count):
    events = history()[:2]
    for index in range(ordinary_count - 2):
        values = {key: deepcopy(events[1][key]) for key in audit._EXTRA["start"]}
        identity = f"00000000-0000-4000-8000-{1000 + index:012d}"
        values.update(attempt_id=identity, logical_id=identity)
        add(events, "start", **values)
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OBSERVATION,
        artifact_id=ARTIFACT,
        layer="wire",
    )
    block(events)
    calls = []
    visits = []
    stage_visits = []
    original = audit._fold
    original_admit = audit._admit_acquired

    def counted_admit(attempt_id, attempts, *, code):
        stage_visits.append(attempt_id)
        return original_admit(attempt_id, attempts, code=code)

    def counted(rows, *, _observer=None):
        calls.append(len(rows))

        def observe(phase, event, *state):
            visits.append((phase, None if event is None else event["event_id"]))
            _observer(phase, event, *state)

        return original(rows, _observer=observe)

    monkeypatch.setattr(audit, "_fold", counted)
    monkeypatch.setattr(audit, "_admit_acquired", counted_admit)
    audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    assert calls == [ordinary_count]
    assert visits == [
        (phase, event["event_id"]) for event in events[:ordinary_count] for phase in ("before", "after")
    ] + [("before", None)]
    assert stage_visits == [ATTEMPT, ATTEMPT]  # begin and checkpoint, once each


def count_hash_work(monkeypatch):
    """Count constructor/update input and digest work, not elapsed time."""
    original = audit.hashlib.sha256
    work = []

    class CountedHash:
        def __init__(self, data=b""):
            self.hasher = original(data)
            self.row = {"constructor_bytes": len(data), "update_bytes": 0, "digests": 0}
            work.append(self.row)

        def update(self, data):
            self.row["update_bytes"] += len(data)
            self.hasher.update(data)

        def hexdigest(self):
            self.row["digests"] += 1
            return self.hasher.hexdigest()

        def copy(self):
            clone = object.__new__(CountedHash)
            clone.hasher = self.hasher.copy()
            clone.row = {"constructor_bytes": 0, "update_bytes": 0, "digests": 0}
            work.append(clone.row)
            return clone

    monkeypatch.setattr(audit.hashlib, "sha256", CountedHash)
    return work


@pytest.mark.parametrize("checkpoint_count", [16, 32, 64])
@pytest.mark.parametrize("adopted", [False, True])
def test_checkpoint_hash_work_shares_verified_adopted_prefix(monkeypatch, checkpoint_count, adopted):
    events = history()
    data = b"x" * checkpoint_count
    for length in range(1, checkpoint_count + 1):
        block(events, data[:length])
    if adopted:
        seal(events)
        adopt(events, data)
    work = count_hash_work(monkeypatch)
    audit._replay_stage_candidate(events, {ARTIFACT: data})
    # The staged acquired ref is bound to this exact checkpoint. Its content
    # integrity check shares the actual prefix hash within this validation pass.
    assert sum(row["constructor_bytes"] for row in work) == 0
    assert sum(row["update_bytes"] for row in work) == len(data)
    assert len([row for row in work if row["update_bytes"]]) == 1


def test_repeated_checkpoint_reuses_prefix_hash_and_digest(monkeypatch):
    events = history()
    data = b"x" * 4096
    for _ in range(32):
        block(events, data)
    work = count_hash_work(monkeypatch)
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: data})
    assert projection.checkpoint_bytes == len(data)
    assert sum(row["constructor_bytes"] + row["update_bytes"] for row in work) <= len(data)
    (incremental,) = [row for row in work if row["update_bytes"]]
    assert incremental["digests"] <= 2  # empty prefix, then the only growing prefix


def test_unknown_tail_is_not_hashed_as_checkpoint_evidence(monkeypatch):
    events = history()
    block(events)
    work = count_hash_work(monkeypatch)
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: b"abc-unknown-tail"})
    assert projection.unverified_tail_bytes == len(b"-unknown-tail")
    assert sum(row["constructor_bytes"] + row["update_bytes"] for row in work) == 3


def test_incremental_hashers_are_isolated_between_artifacts(monkeypatch):
    events = history()
    block(events, b"abc")
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        artifact_id=OTHER,
        layer="decoded",
    )
    add(events, "stage_checkpoint", artifact_id=OTHER, retained_bytes=2, sha256=digest(b"xy"))
    add(events, "stage_checkpoint", artifact_id=OTHER, retained_bytes=3, sha256=digest(b"xyz"))
    work = count_hash_work(monkeypatch)
    audit._replay_stage_candidate(events, {ARTIFACT: b"abc", OTHER: b"xyz"})
    assert sum(row["constructor_bytes"] for row in work) == 0
    assert sorted(row["update_bytes"] for row in work if row["update_bytes"]) == [3, 3]


def finish_event(events):
    add(
        events,
        "finish",
        attempt_id=ATTEMPT,
        outcome="completed",
        error_class=None,
        duration_s=0.0,
        dispatch_state="unknown",
    )


@pytest.mark.parametrize("boundary", ["before_start", "after_finish", "after_freeze"])
def test_stage_uses_authoritative_active_window(boundary):
    events = history()
    if boundary == "before_start":
        events[1], events[2] = events[2], events[1]
        for sequence, event in enumerate(events):
            event["sequence"] = sequence
    elif boundary == "after_finish":
        finish_event(events)
        block(events)
    else:
        add(events, "freeze", frozen=True)
        block(events)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})


def test_failed_ordinary_acquired_never_reaches_after_adoption(monkeypatch):
    events = history()
    block(events)
    seal(events)
    acquired = adopt(events)
    # Candidate identity and prefix checks pass, but authoritative RawRef typing fails.
    acquired["ref"]["partial"] = "false"
    calls = []
    original = audit._fold

    def traced(rows, *, _observer=None):
        def observe(phase, event, *state):
            calls.append((phase, None if event is None else event["event"]))
            return _observer(phase, event, *state)

        return original(rows, _observer=observe)

    monkeypatch.setattr(audit, "_fold", traced)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    assert calls[-1] == ("before", "acquired")
    assert ("after", "acquired") not in calls
    assert ("before", None) not in calls


@pytest.mark.parametrize("version", [1, 2])
def test_default_fold_and_noop_observer_have_identical_results(version):
    events = history()[:2]
    adopt(events)
    finish_event(events)
    add(events, "run_final", outcome="completed", audit_complete=True)
    for event in events:
        event["schema_version"] = version
    events[0]["reservation"] = audit._reservation_policy(version)
    visits = []

    def observe(phase, event, *_state):
        visits.append((phase, event))

    assert audit._fold(events) == audit._fold(events, _observer=observe)
    assert visits == [(phase, event) for event in events for phase in ("before", "after")] + [
        ("before", None)
    ]


@pytest.mark.parametrize("invalid", ["source", "request_hash", "retry_parent", "terminal", "raw_ref"])
def test_ordinary_invalid_semantics_still_rejected_with_candidate_observer(invalid):
    events = history()[:2]
    if invalid == "source":
        events[1]["context"]["source_hash"] = "c" * 64
    elif invalid == "request_hash":
        events[1]["request_digest"] = "invalid"
    elif invalid == "retry_parent":
        events[1]["cause"] = "retry"
    elif invalid == "terminal":
        add(events, "run_final", outcome="completed", audit_complete=True)
    else:
        adopt(events)["ref"]["partial"] = "false"
    with pytest.raises(audit._Rejected) as original:
        audit._fold(events)
    with pytest.raises(audit._Rejected) as candidate:
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"} if invalid == "raw_ref" else {})
    assert original.value.code == candidate.value.code


def test_authoritative_aggregate_runs_once_after_stage_flush(monkeypatch):
    events = history()
    for length in range(1, 17):
        block(events, b"x" * length)
    calls = []
    original = audit._work_s

    def counted(attempts):
        calls.append(tuple(attempts))
        return original(attempts)

    monkeypatch.setattr(audit, "_work_s", counted)
    audit._replay_stage_candidate(events, {ARTIFACT: b"x" * 16})
    assert calls == [(ATTEMPT,)]


@pytest.mark.parametrize("ref", [None, [], {}, {"artifact_id": ARTIFACT}])
def test_malformed_acquired_ref_rejection_stays_authoritative(ref):
    events = history()
    block(events)
    seal(events)
    adopt(events)["ref"] = ref
    ordinary = [event for event in events if event["event"] not in audit._STAGE_EXTRA]
    with pytest.raises(audit._Rejected) as original:
        audit._fold(ordinary)
    with pytest.raises(audit._Rejected) as candidate:
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    assert original.value.code == candidate.value.code


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("conflict", [None, "hash", "length"])
def test_ordinary_artifact_aliases_preserve_all_ref_constraints(version, conflict, monkeypatch):
    events = history()[:2]
    adopt(events)
    second = adopt(events)
    second["observation_id"] = OTHER
    if conflict == "hash":
        second["ref"]["sha256"] = digest(b"abd")
    elif conflict == "length":
        second["ref"].update(retained_bytes=2, original_bytes=2, sha256=digest(b"ab"))
    for event in events:
        event["schema_version"] = version
    events[0]["reservation"] = audit._reservation_policy(version)
    # Legacy ordinary semantics allow aliases; content validation must enforce all.
    audit._fold(events)
    if conflict:
        data = b"abd" if conflict == "hash" else b"ab"
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, {ARTIFACT: data})
    else:
        work = count_hash_work(monkeypatch)
        assert audit._replay_stage_candidate(events, {ARTIFACT: b"abc"}) == ()
        assert sum(row["constructor_bytes"] for row in work) == 3


@pytest.mark.parametrize("stage_count", [8, 16, 32])
def test_candidate_cap_updates_do_not_rescan_ordinary_observations(monkeypatch, stage_count):
    events = history()[:2]
    adopt(events)
    artifacts = {ARTIFACT: b"abc"}
    for index in range(stage_count):
        ids = [f"00000000-0000-4000-8000-{10000 + 4 * index + j:012d}" for j in range(3)]
        values = {key: deepcopy(events[1][key]) for key in audit._EXTRA["start"]}
        values.update(attempt_id=ids[0], logical_id=ids[0])
        add(events, "start", **values)
        add(
            events,
            "stage_begin",
            attempt_id=ids[0],
            observation_id=ids[1],
            artifact_id=ids[2],
            layer="wire",
        )
        add(events, "stage_checkpoint", artifact_id=ids[2], retained_bytes=1, sha256=digest(b"x"))
        artifacts[ids[2]] = b"xy"  # one verified byte and one unknown tail byte
    state = {"fold": False, "observer": False}
    calls = {"existing": [], "candidate": []}
    charges = []
    artifact_reads = []
    original_fold, original_body = audit._fold, audit._body_bytes
    original_charge = audit._charge_stage_bytes

    class CountedArtifacts(dict):
        def get(self, key, default=None):
            artifact_reads.append(key)
            return super().get(key, default)

    def fold(rows, *, _observer=None):
        state["fold"] = True

        def observe(*args):
            state["observer"] = True
            try:
                return _observer(*args)
            finally:
                state["observer"] = False

        try:
            return original_fold(rows, _observer=observe)
        finally:
            state["fold"] = False

    def body(observations, *args):
        kind = "existing" if state["fold"] and not state["observer"] else "candidate"
        calls[kind].append(len(observations))
        return original_body(observations, *args)

    def charge(buckets, attempt, layer, delta, backend_format=None):
        charges.append((attempt, layer, delta))
        return original_charge(buckets, attempt, layer, delta, backend_format)

    monkeypatch.setattr(audit, "_fold", fold)
    monkeypatch.setattr(audit, "_body_bytes", body)
    monkeypatch.setattr(audit, "_charge_stage_bytes", charge)
    audit._replay_stage_candidate(events, CountedArtifacts(artifacts))
    assert calls == {"existing": [0], "candidate": []}
    assert len(charges) == 1 + 2 * stage_count
    assert sum(delta for _, _, delta in charges) == 3 + 2 * stage_count
    assert len(artifact_reads) == len(set(artifact_reads)) == stage_count


@pytest.mark.parametrize("layer,allowed", [("stdout", True), ("stderr", True), ("decoded", True)])
def test_nonstage_acquired_charge_includes_unadopted_checkpoint(layer, allowed):
    events = history()
    data = b"x" * audit.BODY_BYTES
    block(events, data)
    add(
        events,
        "acquired",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        layer=layer,
        ref=asdict(audit.RawRef(OTHER, layer, digest(b"a"), 1, 1, False)),
        complete=True,
        usage=None,
    )
    if allowed:
        (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: data, OTHER: b"a"})
        assert projection.checkpoint_bytes == audit.BODY_BYTES
    else:
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, {ARTIFACT: data, OTHER: b"a"})


def test_successful_adoption_transfers_charge_without_increment(monkeypatch):
    events = history()
    block(events)
    seal(events)
    adopt(events)
    calls = []
    original = audit._charge_stage_bytes

    def charge(buckets, attempt, layer, delta, backend_format=None):
        calls.append((attempt, layer, delta))
        return original(buckets, attempt, layer, delta, backend_format)

    monkeypatch.setattr(audit, "_charge_stage_bytes", charge)
    (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    assert projection.adopted
    assert calls == [(ATTEMPT, "wire", 3)]


def test_failed_adoption_does_not_transfer_or_add_charge(monkeypatch):
    events = history()
    block(events)
    seal(events)
    adopt(events)["ref"]["partial"] = "false"
    calls = []
    original = audit._charge_stage_bytes

    def charge(buckets, attempt, layer, delta, backend_format=None):
        calls.append(delta)
        return original(buckets, attempt, layer, delta, backend_format)

    monkeypatch.setattr(audit, "_charge_stage_bytes", charge)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})
    assert calls == [3]


def test_full_wire_caps_are_independent_for_distinct_attempts():
    events = history()
    first = b"x" * audit.BODY_BYTES
    block(events, first)
    values = {key: deepcopy(events[1][key]) for key in audit._EXTRA["start"]}
    values.update(attempt_id=OTHER, logical_id=OTHER)
    add(events, "start", **values)
    add(events, "stage_begin", attempt_id=OTHER, observation_id=OTHER, artifact_id=OTHER, layer="wire")
    second = b"y" * audit.BODY_BYTES
    add(events, "stage_checkpoint", artifact_id=OTHER, retained_bytes=len(second), sha256=digest(second))
    projections = audit._replay_stage_candidate(events, {ARTIFACT: first, OTHER: second})
    assert [projection.checkpoint_bytes for projection in projections] == [audit.BODY_BYTES] * 2


@pytest.mark.parametrize("stage_count", [8, 16, 32])
def test_stage_identity_lookups_do_not_scan_existing_stages_or_observations(monkeypatch, stage_count):
    from collections.abc import Mapping

    events = history()[:1]
    template = history()[1]
    artifacts = {}
    for index in range(stage_count):
        ids = [f"00000000-0000-4000-8000-{10000 + 4 * index + j:012d}" for j in range(3)]
        values = {key: deepcopy(template[key]) for key in audit._EXTRA["start"]}
        values.update(attempt_id=ids[0], logical_id=ids[0])
        add(events, "start", **values)
        add(
            events,
            "stage_begin",
            attempt_id=ids[0],
            observation_id=ids[1],
            artifact_id=ids[2],
            layer="wire",
        )
        add(events, "stage_checkpoint", artifact_id=ids[2], retained_bytes=1, sha256=digest(b"x"))
        add(events, "stage_seal", artifact_id=ids[2])
        add(
            events,
            "acquired",
            attempt_id=ids[0],
            observation_id=ids[1],
            layer="wire",
            ref=asdict(audit.RawRef(ids[2], "wire", digest(b"x"), 1, 1, False)),
            complete=True,
            usage=None,
        )
        artifacts[ids[2]] = b"x"
    counters = {"identity_reads": 0, "observation_visits": 0}
    original_fold, original_identity = audit._fold, audit.StageIdentity

    class CountedIdentity(original_identity):
        def __getattribute__(self, name):
            if name in ("attempt_id", "observation_id", "artifact_id", "layer"):
                counters["identity_reads"] += 1
            return super().__getattribute__(name)

    class Observations(Mapping):
        def __init__(self, data):
            self.data = data

        def __getitem__(self, key):
            return self.data[key]

        def __iter__(self):
            return iter(self.data)

        def __len__(self):
            return len(self.data)

        def values(self):
            for value in self.data.values():
                counters["observation_visits"] += 1
                yield value

    def fold(rows, *, _observer=None):
        def observe(phase, event, attempts, observations, terminal, frozen):
            return _observer(phase, event, attempts, Observations(observations), terminal, frozen)

        return original_fold(rows, _observer=observe)

    monkeypatch.setattr(audit, "_fold", fold)
    monkeypatch.setattr(audit, "StageIdentity", CountedIdentity)
    projections = audit._replay_stage_candidate(events, artifacts)
    assert len(projections) == stage_count
    assert counters["identity_reads"] <= 8 * stage_count
    assert counters["observation_visits"] == 0


@pytest.mark.parametrize("crossed", [None, "observation", "layer", "artifact"])
def test_adoption_indexes_require_one_consistent_registered_identity(crossed):
    events = history()
    block(events)
    seal(events)
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        artifact_id=OTHER,
        layer="decoded",
    )
    add(events, "stage_checkpoint", artifact_id=OTHER, retained_bytes=3, sha256=digest(b"abc"))
    add(events, "stage_seal", artifact_id=OTHER)
    acquired = adopt(events)
    if crossed == "observation":
        acquired["observation_id"] = OTHER
    elif crossed == "layer":
        acquired["layer"] = acquired["ref"]["layer"] = "decoded"
    elif crossed == "artifact":
        acquired["ref"]["artifact_id"] = OTHER
    if crossed:
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, {ARTIFACT: b"abc", OTHER: b"abc"})
    else:
        projections = audit._replay_stage_candidate(events, {ARTIFACT: b"abc", OTHER: b"abc"})
        assert [projection.adopted for projection in projections] == [True, False]


@pytest.mark.parametrize("occupied", ["observation", "layer", "artifact"])
def test_stage_begin_indexes_preserve_ordinary_occupied_identity(occupied):
    events = history()[:2]
    adopt(events)
    stage = add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        artifact_id=OTHER,
        layer="decoded",
    )
    if occupied == "observation":
        stage["observation_id"] = OBSERVATION
    elif occupied == "layer":
        stage["layer"] = "wire"
    else:
        stage["artifact_id"] = ARTIFACT
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})


@pytest.mark.parametrize("key", ["observation_id", "artifact_id", "layer"])
def test_adoption_index_keys_are_validated_before_lookup(key):
    events = history()
    block(events)
    seal(events)
    acquired = adopt(events)
    if key == "artifact_id":
        acquired["ref"][key] = []
    else:
        acquired[key] = []
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"})


def test_stage_framing_is_shared_but_never_enables_v1_stage_events():
    events = history()
    encoded = b"".join(map(audit._frame, events))
    recovered, _, _, torn, invalid = audit._read_events(encoded, RUN, _stage_records=True)
    assert recovered == events and not torn and not invalid
    assert audit._read_events(encoded, RUN)[3:] == (False, False)
    for event in events:
        event["schema_version"] = 1
    assert audit._read_events(b"".join(map(audit._frame, events)), RUN, _stage_records=True)[3:] == (
        True,
        True,
    )


def test_readonly_stage_recovery_refuses_unsupported_profile(monkeypatch, tmp_path):
    calls = []

    def denied(root, *, create, _checkpoint):
        calls.append((root, create))
        return audit.AuditFault("unsupported_profile", "open", None)

    monkeypatch.setattr(audit, "_prepare_audit_root", denied)
    result = audit.AttemptRecorder.reopen_stages(tmp_path, RUN)
    assert calls == [(tmp_path, False)]
    assert not result.prefixes_verified and result.stages == ()
    assert result.faults[0].code == "unsupported_profile"
    assert result.charge_bytes == audit.V2_RUN_BYTES
    assert not result.audit_complete and not result.maintenance_eligible


def test_recovery_deadline_expires_before_any_root_access(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("expired operation accessed storage")

    monkeypatch.setattr(audit, "_prepare_audit_root", forbidden)
    result = audit.AttemptRecorder.reopen_stages(
        tmp_path, RUN, monotonic=lambda: 5.0, action_deadline=5.0
    )
    assert not result.prefixes_verified and result.stages == ()
    assert result.faults[0].code == "lock_timeout"


def test_stage_event_deadline_does_not_return_partial_verified_prefix():
    events = history()
    block(events)
    calls = []

    def checkpoint():
        calls.append(None)
        if len(calls) == 5:
            raise audit._Rejected("lock_timeout")

    with pytest.raises(audit._Rejected) as caught:
        audit._replay_stage_candidate(events, {ARTIFACT: b"abc"}, _checkpoint=checkpoint)
    assert caught.value.code == "lock_timeout"


def test_framing_deadline_is_not_misreported_as_torn_journal():
    def expired():
        raise audit._Rejected("lock_timeout")

    with pytest.raises(audit._Rejected) as caught:
        audit._read_events(audit._frame(history()[0]), RUN, _stage_records=True, _checkpoint=expired)
    assert caught.value.code == "lock_timeout"


def test_recovery_file_enumeration_stops_at_4097_without_materializing_all(monkeypatch):
    from types import SimpleNamespace

    seen = []
    closed = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            for index in range(1000000):
                seen.append(index)
                yield SimpleNamespace(name=str(index))

    monkeypatch.setattr(audit.os, "open", lambda *args, **kwargs: 99)
    monkeypatch.setattr(audit.os, "close", closed.append)
    monkeypatch.setattr(audit.os, "scandir", lambda fd: Entries())
    monkeypatch.setattr(audit, "_directory", lambda fd: SimpleNamespace(st_dev=1, st_ino=2))
    with pytest.raises(audit._Rejected) as caught:
        audit._bounded_stage_entries(1, lambda: None)
    assert caught.value.code == "quota_refused"
    assert len(seen) == 4097 and closed == [99]


@pytest.fixture
def native_stage_run():
    """Real profile fixture: admission must assert, never skip or override it."""
    import os
    from pathlib import Path
    from types import SimpleNamespace
    import uuid

    parent = Path(__file__).resolve().parents[1] / ".planning" / "audit-fixtures" / str(uuid.uuid4())
    parent.mkdir(parents=True, mode=0o700)
    root = parent / "stage-recovery"
    context = audit.InvocationContext(**history()[0]["context"])
    recorder = audit.AttemptRecorder(
        root,
        context=context,
        monotonic=lambda: 0.0,
        utc_now=lambda: "2026-11-04T00:00:00Z",
        audit_schema_version=2,
    )
    opened = recorder.open()
    assert opened.admitted, (
        f"real qualified storage required; downstream assertions NOTRUN: {opened.fault}"
    )
    manifest = dict(
        schema_version=1,
        run_id=RUN,
        snapshot_id=OTHER,
        mode="diff",
        legacy_source_hash=SOURCE,
        exact_diff_sha256=digest(b"diff"),
        diff_bytes=4,
        files=[],
        repositories=[],
    )
    assert recorder.publish_input_manifest(manifest).admitted
    started = recorder.start(
        context,
        logical_id=ATTEMPT,
        parent_id=None,
        cause="initial",
        backend=audit.BackendObservation(**history()[1]["backend"]),
        request_digest=digest(b"request"),
        request_bytes=7,
        action_deadline=100.0,
    )
    assert started.admitted
    events = deepcopy(recorder._events)
    add(
        events,
        "stage_begin",
        attempt_id=started.attempt_id,
        observation_id=OBSERVATION,
        artifact_id=ARTIFACT,
        layer="wire",
    )
    block(events)

    def store(rows=events, raw=b"abcTAIL", markers=None):
        handle = audit._prepare_audit_root(root, create=False)
        assert not isinstance(handle, audit.AuditFault)
        try:
            with audit._root_lock(handle):
                with audit._run_dir(handle, RUN) as fd:
                    audit._publish(fd, "journal", b"".join(map(audit._frame, rows)), replace=True)
                    if raw is not None:
                        audit._publish(
                            fd,
                            "raw-" + ARTIFACT,
                            raw,
                            replace=(root / RUN / ("raw-" + ARTIFACT)).exists(),
                        )
                    for name, data in (markers or {}).items():
                        audit._publish(fd, name, data, replace=(root / RUN / name).exists())
                    os.fsync(fd)
        finally:
            handle.close()

    store()
    return SimpleNamespace(
        root=root,
        run=root / RUN,
        recorder=recorder,
        events=events,
        attempt=started.attempt_id,
        store=store,
    )


def test_native_stage_recovery_reads_prefix_and_unknown_tail_without_mutation(native_stage_run):
    fixture = native_stage_run
    before = {
        path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in fixture.run.iterdir()
    }
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert result.prefixes_verified and not result.faults
    (projection,) = result.stages
    assert projection.identity.attempt_id == fixture.attempt
    assert projection.context.source_hash == SOURCE
    assert projection.checkpoint_bytes == 3 and projection.unverified_tail_bytes == 4
    assert not projection.adopted and not projection.sealed
    assert result.charge_bytes == audit.V2_RUN_BYTES and not result.maintenance_eligible
    assert not result.audit_complete
    assert before == {
        path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in fixture.run.iterdir()
    }


def test_native_stage_begin_without_file_remains_empty_diagnostic(native_stage_run):
    fixture = native_stage_run
    fixture.store(fixture.events[:-1], raw=None)
    (fixture.run / ("raw-" + ARTIFACT)).unlink()
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert result.prefixes_verified
    assert result.stages[0].checkpoint_bytes == result.stages[0].unverified_tail_bytes == 0
    assert not result.stages[0].adopted


@pytest.mark.parametrize(
    "damage",
    ["prefix", "orphan", "admission", "manifest", "unknown_marker", "capability", "mode", "missing_raw"],
)
def test_native_stage_recovery_rejects_damage_without_returning_prefix(native_stage_run, damage):
    import json
    import os

    fixture = native_stage_run
    raw = fixture.run / ("raw-" + ARTIFACT)
    if damage == "prefix":
        raw.write_bytes(b"abdTAIL")
    elif damage == "orphan":
        path = fixture.run / ("raw-" + OTHER)
        path.write_bytes(b"unknown")
        path.chmod(0o600)
    elif damage == "admission":
        path = fixture.run / "admission"
        value = json.loads(path.read_bytes())
        value["event_id"] = OTHER
        path.write_bytes(audit._json(value))
    elif damage == "manifest":
        (fixture.run / "input-manifest.json").write_bytes(b"{}")
    elif damage == "unknown_marker":
        path = fixture.run / "unregistered-marker"
        path.write_bytes(b"{}")
        path.chmod(0o600)
    elif damage == "capability":
        fixture.events[0]["capability"]["root_inode"] += 1
        fixture.store()
    elif damage == "mode":
        os.chmod(raw, 0o644)
    else:
        raw.unlink()
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert not result.prefixes_verified and result.stages == () and result.faults
    assert result.charge_bytes == audit.V2_RUN_BYTES and not result.maintenance_eligible


@pytest.mark.parametrize("marker", ["fault", "freeze", "final", "tombstone"])
def test_native_stage_recovery_validates_all_persisted_markers(native_stage_run, marker):
    import json
    import uuid

    fixture = native_stage_run
    rows = deepcopy(fixture.events)
    owner = rows[0]["owner"]
    markers = {}
    if marker == "fault":
        event = add(
            rows, "audit_fault", code="persistence_error", phase="acquired", attempt_id=fixture.attempt
        )
        markers[marker] = audit._marker(event, owner)
    elif marker == "freeze":
        event = add(rows, "freeze", frozen=True)
        markers[marker] = audit._marker(event, owner, frozen=True)
    else:
        if marker == "tombstone":
            seal(rows)
            add(
                rows,
                "acquired",
                attempt_id=fixture.attempt,
                observation_id=OBSERVATION,
                layer="wire",
                ref=asdict(audit.RawRef(ARTIFACT, "wire", digest(b"abc"), 3, 3, False)),
                complete=True,
                usage=None,
            )
        add(
            rows,
            "finish",
            attempt_id=fixture.attempt,
            outcome="incomplete",
            error_class=None,
            duration_s=0.0,
            dispatch_state="unknown",
        )
        event = add(rows, "run_final", outcome="incomplete", audit_complete=False)
        markers["final"] = audit._marker(event, owner, terminal="incomplete")
        if marker == "tombstone":
            tomb = dict(event, event_id=str(uuid.uuid4()), utc="2026-11-08T00:00:00Z")
            markers[marker] = audit._marker(tomb, owner, terminal="incomplete", tombstone=True)
    fixture.store(rows, raw=b"abc" if marker == "tombstone" else b"abcTAIL", markers=markers)
    assert audit.AttemptRecorder.reopen_stages(fixture.root, RUN).prefixes_verified
    value = json.loads(markers[marker])
    value["event_id"] = rows[0]["event_id"]
    fixture.store(
        rows, raw=b"abc" if marker == "tombstone" else b"abcTAIL", markers={marker: audit._json(value)}
    )
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert not result.prefixes_verified and result.stages == ()


def test_native_stage_recovery_detects_same_content_inode_replacement(native_stage_run, monkeypatch):
    fixture = native_stage_run
    original = audit._stage_file_snapshot
    calls = []

    def replaced(fd, checkpoint, run_id):
        value = original(fd, checkpoint, run_id)
        calls.append(None)
        if len(calls) == 1:
            audit._publish(fd, "raw-" + ARTIFACT, b"abcTAIL", replace=True)
        return value

    monkeypatch.setattr(audit, "_stage_file_snapshot", replaced)
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert not result.prefixes_verified and result.stages == ()
    assert len(calls) == 2


def test_native_stage_recovery_does_not_enable_ordinary_replay_or_maintenance(native_stage_run):
    fixture = native_stage_run
    assert audit.AttemptRecorder.reopen_stages(fixture.root, RUN).prefixes_verified
    assert not audit.AttemptRecorder.reopen(fixture.root, RUN).audit_complete
    before = {path.name: path.read_bytes() for path in fixture.run.iterdir()}
    result = audit.maintain_audit_root(
        fixture.root,
        monotonic=lambda: 0.0,
        utc_now=lambda: "2027-01-01T00:00:00Z",
        action_deadline=100.0,
    )
    assert result.admitted  # Valid unadopted stages are protected, not arbitrary orphans.
    assert before == {path.name: path.read_bytes() for path in fixture.run.iterdir()}
    handle = audit._prepare_audit_root(fixture.root, create=False)
    assert not isinstance(handle, audit.AuditFault)
    try:
        with audit._root_lock(handle):
            with audit._run_dir(handle, RUN) as fd:
                disk = audit._disk_run(fd, RUN)
                assert disk["charge"] == audit.V2_RUN_BYTES and disk["stage_incomplete"]
                assert not disk["torn"]
    finally:
        handle.close()


def test_stable_file_read_checks_stat_budget_before_reading(monkeypatch):
    """Pure control-flow check, not storage/profile qualification."""
    from types import SimpleNamespace

    closes = []
    monkeypatch.setattr(audit.os, "open", lambda *args, **kwargs: 99)
    monkeypatch.setattr(audit.os, "close", closes.append)
    monkeypatch.setattr(audit, "_regular", lambda fd: SimpleNamespace(st_size=9))

    def forbidden(*args, **kwargs):
        raise AssertionError("over-budget file was read")

    monkeypatch.setattr(audit.os, "read", forbidden)
    with pytest.raises(audit._Rejected):
        audit._read_file(1, "raw-" + ARTIFACT, 8, _stable=True)
    assert closes == [99]


def test_native_stage_recovery_mid_read_deadline_discards_snapshot(native_stage_run, monkeypatch):
    fixture = native_stage_run
    clock = [0.0]
    original = audit._stage_file_snapshot

    def expire(fd, checkpoint, run_id):
        value = original(fd, checkpoint, run_id)
        clock[0] = 10.0
        return value

    monkeypatch.setattr(audit, "_stage_file_snapshot", expire)
    result = audit.AttemptRecorder.reopen_stages(
        fixture.root, RUN, monotonic=lambda: clock[0], action_deadline=5.0
    )
    assert not result.prefixes_verified and result.stages == ()
    assert result.faults[0].code == "lock_timeout"


def test_native_stage_adopted_record_remains_diagnostic_only(native_stage_run):
    fixture = native_stage_run
    rows = deepcopy(fixture.events)
    seal(rows)
    add(
        rows,
        "acquired",
        attempt_id=fixture.attempt,
        observation_id=OBSERVATION,
        layer="wire",
        ref=asdict(audit.RawRef(ARTIFACT, "wire", digest(b"abc"), 3, 3, False)),
        complete=True,
        usage=None,
    )
    fixture.store(rows, raw=b"abc")
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert result.prefixes_verified and result.stages[0].adopted
    assert result.stages[0].unverified_tail_bytes == 0
    assert result.charge_bytes == audit.V2_RUN_BYTES
    assert not result.audit_complete and not result.maintenance_eligible


def cached_content_history():
    events = history()[:1]
    manifest = dict(
        schema_version=1,
        run_id=RUN,
        snapshot_id=OTHER,
        mode="diff",
        legacy_source_hash=SOURCE,
        exact_diff_sha256=digest(b"diff"),
        diff_bytes=4,
        files=[],
        repositories=[],
    )
    encoded = audit._manifest(manifest, events[0]["context"])
    add(
        events,
        "input_manifest",
        sha256=digest(encoded),
        bytes=len(encoded),
        source_hash=SOURCE,
        snapshot_id=OTHER,
    )
    values = {key: deepcopy(history()[1][key]) for key in audit._EXTRA["start"]}
    add(events, "start", **values)
    for observation in (OBSERVATION, OTHER):
        add(
            events,
            "acquired",
            attempt_id=ATTEMPT,
            observation_id=observation,
            layer="wire",
            ref=asdict(audit.RawRef(ARTIFACT, "wire", digest(b"{}"), 2, 2, False)),
            complete=True,
            usage=None,
        )
    backend = dict(history()[1]["backend"], observed_model="observed")
    for suffix in (6, 7):
        add(
            events,
            "response_observed",
            attempt_id=ATTEMPT,
            observation_id=f"00000000-0000-4000-8000-{suffix:012d}",
            source_observation_id=OBSERVATION,
            frame_offset=0,
            frame_length=2,
            usage=None,
            observed_backend=backend,
        )
    audit._fold(events)
    return events, {"input-manifest.json": encoded, "raw-" + ARTIFACT: b"{}"}


def test_cached_manifest_alias_and_frame_validation_performs_no_extra_reads(monkeypatch):
    events, contents = cached_content_history()

    def forbidden(*args, **kwargs):
        raise AssertionError("stable content cache unexpectedly reread storage")

    monkeypatch.setattr(audit, "_read_file", forbidden)
    monkeypatch.setattr(audit, "_entries", forbidden)
    frames = []
    original = audit._response_frame

    def frame(data, offset, length):
        frames.append((offset, length))
        return original(data, offset, length)

    monkeypatch.setattr(audit, "_response_frame", frame)
    work = count_hash_work(monkeypatch)
    checkpoints = []
    audit._raw_contents(
        -1,
        {"events": events, "tombstone": None},
        _contents=contents,
        _checkpoint=lambda: checkpoints.append(None),
    )
    assert frames == [(0, 2), (0, 2)]
    assert checkpoints
    assert sum(row["constructor_bytes"] for row in work) == len(contents["input-manifest.json"]) + 2


@pytest.mark.parametrize("damage", ["manifest", "frame", "alias", "missing_raw"])
def test_cached_content_uses_the_same_manifest_ref_and_frame_rules(monkeypatch, damage):
    events, contents = cached_content_history()
    if damage == "manifest":
        contents["input-manifest.json"] = b"{}"
    elif damage == "frame":
        contents["raw-" + ARTIFACT] = b"[]"
        for event in events:
            if event["event"] == "acquired":
                event["ref"]["sha256"] = digest(b"[]")
    elif damage == "alias":
        events[4]["ref"]["sha256"] = digest(b"[]")
    else:
        del contents["raw-" + ARTIFACT]

    def forbidden(*args, **kwargs):
        raise AssertionError("cache validation fell back to an unbounded read")

    monkeypatch.setattr(audit, "_read_file", forbidden)
    with pytest.raises(audit._Rejected):
        audit._raw_contents(
            -1, {"events": events, "tombstone": None}, _contents=contents, _checkpoint=lambda: None
        )


def test_native_stage_aliases_and_frames_only_read_each_file_twice(native_stage_run, monkeypatch):
    from collections import Counter

    fixture = native_stage_run
    rows = deepcopy(fixture.events)
    first_observation = "00000000-0000-4000-8000-000000000006"
    second_observation = "00000000-0000-4000-8000-000000000007"
    for observation in (first_observation, second_observation):
        add(
            rows,
            "acquired",
            attempt_id=fixture.attempt,
            observation_id=observation,
            layer="stdout",
            ref=asdict(audit.RawRef(OTHER, "stdout", digest(b"{}"), 2, 2, False)),
            complete=True,
            usage=None,
        )
    backend = dict(history()[1]["backend"], observed_model="observed")
    for suffix in (8, 9):
        add(
            rows,
            "response_observed",
            attempt_id=fixture.attempt,
            observation_id=f"00000000-0000-4000-8000-{suffix:012d}",
            source_observation_id=first_observation,
            frame_offset=0,
            frame_length=2,
            usage=None,
            observed_backend=backend,
        )
    fixture.store(rows, markers={"raw-" + OTHER: b"{}"})
    reads = Counter()
    original = audit._read_file

    def read(fd, name, *args, **kwargs):
        reads[name] += 1
        return original(fd, name, *args, **kwargs)

    monkeypatch.setattr(audit, "_read_file", read)
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert result.prefixes_verified
    assert reads == Counter({path.name: 2 for path in fixture.run.iterdir()})


def test_native_stage_growing_file_fails_stat_budget_before_read(native_stage_run, monkeypatch):
    import os

    fixture = native_stage_run
    original_snapshot = audit._stage_file_snapshot
    original_read = os.read
    calls = []

    def grow(fd, checkpoint, run_id):
        result = original_snapshot(fd, checkpoint, run_id)
        calls.append(None)
        if len(calls) == 1:
            audit._publish(fd, "raw-" + ARTIFACT, b"x" * (audit.V2_WIRE_BYTES + 1), replace=True)
        return result

    def read(fd, length):
        assert os.fstat(fd).st_size <= audit.V2_WIRE_BYTES, (
            "enlarged artifact read before stat rejection"
        )
        return original_read(fd, length)

    monkeypatch.setattr(audit, "_stage_file_snapshot", grow)
    monkeypatch.setattr(audit.os, "read", read)
    result = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert not result.prefixes_verified and not result.stages
    assert result.faults[0].code == "integrity_error"


def control_recorder(tmp_path, clock=lambda: 0.0):
    """Unopened object for pure control-flow tests; never grants storage admission."""
    recorder = audit.AttemptRecorder(
        tmp_path,
        context=audit.InvocationContext(**history()[0]["context"]),
        monotonic=clock,
        utc_now=lambda: "2026-11-04T00:00:00Z",
        audit_schema_version=2,
    )
    return recorder


def test_stage_mutex_wait_exhausting_deadline_starts_no_root_io(monkeypatch, tmp_path):
    clock = [0.0]
    recorder = control_recorder(tmp_path, lambda: clock[0])
    released = []

    class Mutex:
        def acquire(self, *, timeout):
            clock[0] = 5.0
            return True

        def release(self):
            released.append(True)

    recorder._mutex = Mutex()

    def forbidden(*args, **kwargs):
        raise AssertionError("post-deadline root IO started")

    monkeypatch.setattr(recorder, "_guard", forbidden)
    result = recorder.begin_stage(ATTEMPT, layer="wire", action_deadline=5.0)
    assert not result.admitted and result.fault.code == "lock_timeout"
    assert not result.publication_uncertain and result.accepted_bytes == 0
    assert released == [True]


def test_stage_checked_exhausting_deadline_starts_no_inventory(monkeypatch, tmp_path):
    from contextlib import contextmanager

    clock = [0.0]
    recorder = control_recorder(tmp_path, lambda: clock[0])
    recorder._opened = True

    @contextmanager
    def run_dir(*args):
        yield -1

    def checked(*args, **kwargs):
        clock[0] = 5.0

    def guard(_phase, action, **kwargs):
        assert kwargs["_mutex_held"] and not kwargs["_persist_fault"]
        return action(None)

    def forbidden(*args, **kwargs):
        raise AssertionError("inventory ran after preflight exhausted deadline")

    monkeypatch.setattr(audit, "_run_dir", run_dir)
    monkeypatch.setattr(recorder, "_checked", checked)
    monkeypatch.setattr(recorder, "_guard", guard)
    monkeypatch.setattr(audit, "_inventory", forbidden)
    result = recorder.begin_stage(ATTEMPT, layer="wire", action_deadline=5.0)
    assert not result.admitted and result.fault.code == "lock_timeout"
    assert result.accepted_bytes == 0 and result.checkpoint is None


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_stage_journal_failure_marks_unknown_before_publication(monkeypatch, tmp_path, failure):
    recorder = control_recorder(tmp_path)
    event = history()[-1]
    before = recorder._journal_mark()

    def append(*args, **kwargs):
        assert recorder._journal_mark() != before
        raise failure()

    monkeypatch.setattr(audit, "_append", append)
    with pytest.raises(failure):
        recorder._append_stage_event(-1, event, lambda: None)
    assert recorder._append_uncertain and recorder._events == []


def test_stage_create_failure_is_pending_before_any_journal_mutation(monkeypatch, tmp_path):
    recorder = control_recorder(tmp_path)
    recorder._events = history()
    identity = audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire")
    state = dict(
        handle=audit.StageHandle(identity),
        checkpoint=None,
        inode=None,
        pending=False,
        uncertain=False,
        sealed=False,
        capped=False,
        last_write=None,
        adopted=None,
        hasher=hashlib.sha256(),
        file_identity=None,
    )
    before = recorder._journal_mark()
    monkeypatch.setattr(audit, "_file_slots", lambda *args, **kwargs: None)
    monkeypatch.setattr(audit, "_space", lambda *args, **kwargs: None)

    def failed_open(*args, **kwargs):
        assert state["pending"] and recorder._journal_mark() != before
        raise OSError("controlled creation failure")

    monkeypatch.setattr(audit.os, "open", failed_open)
    with pytest.raises(OSError):
        recorder._write_stage_chunk(-1, state, lambda: None, 0, b"abc")
    assert state["pending"] and len(recorder._events) == 3


def test_foreign_stage_handle_has_no_authority_or_storage_access(monkeypatch, tmp_path):
    recorder = control_recorder(tmp_path)
    handle = audit.StageHandle(audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire"))

    def forbidden(*args, **kwargs):
        raise AssertionError("foreign handle accessed root")

    monkeypatch.setattr(recorder, "_guard", forbidden)
    assert not recorder.cancel_stage(handle)
    result = recorder.append_stage(handle, offset=0, data=b"abc", action_deadline=10.0)
    assert not result.admitted and result.checkpoint is None and result.accepted_bytes == 0


@pytest.mark.parametrize("tail", [b"", b"TAIL"])
def test_registered_stage_inventory_is_full_charge_without_global_orphan_permission(monkeypatch, tail):
    """Pure parsed-inventory model, not profile/native storage qualification."""
    events = history()
    block(events)
    files = {
        "journal": b"".join(map(audit._frame, events)),
        "admission": audit._marker(events[0], events[0]["owner"]),
        "raw-" + ARTIFACT: b"abc" + tail,
    }
    monkeypatch.setattr(audit, "_entries", lambda fd: list(files))
    monkeypatch.setattr(audit, "_read_file", lambda fd, name, *args, **kwargs: files[name])
    disk = audit._disk_run(-1, RUN)
    assert not disk["torn"] and not disk["schema_invalid"]
    assert disk["stage_incomplete"] and disk["stage_unknown_tail"] == bool(tail)
    assert disk["charge"] == audit.V2_RUN_BYTES
    audit._raw_contents(-1, disk, _contents=files)
    files["raw-" + OTHER] = b"orphan"
    corrupted = audit._disk_run(-1, RUN)
    assert corrupted["torn"] and corrupted["charge"] == audit.V2_RUN_BYTES
    with pytest.raises(audit._Rejected):
        audit._raw_contents(-1, corrupted, _contents=files)


def test_pruned_adopted_stage_has_no_byte_verified_projection(monkeypatch):
    events = history()
    block(events)
    seal(events)
    adopt(events)
    finish_event(events)
    final = add(events, "run_final", outcome="completed", audit_complete=True)
    tomb_event = dict(final, event_id=OTHER, utc="2026-11-08T00:00:00Z")
    metadata = dict(
        admission=audit._marker(events[0], events[0]["owner"]),
        final=audit._marker(final, events[0]["owner"], terminal="completed"),
        tombstone=audit._marker(tomb_event, events[0]["owner"], terminal="completed", tombstone=True),
    )
    files = {"journal": b"".join(map(audit._frame, events)), **metadata}
    monkeypatch.setattr(audit, "_entries", lambda fd: list(files))
    monkeypatch.setattr(audit, "_read_file", lambda fd, name, *args, **kwargs: files[name])
    disk = audit._disk_run(-1, RUN)
    assert disk["terminal"] and not disk["stage_incomplete"]
    audit._raw_contents(-1, disk, _contents=files)
    assert audit._replay_stage_candidate(events, {}, _pruned={ARTIFACT}) == ()
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {})


@pytest.fixture
def native_stage_writer():
    from pathlib import Path
    from types import SimpleNamespace
    import uuid

    parent = Path(__file__).resolve().parents[1] / ".planning" / "audit-fixtures" / str(uuid.uuid4())
    parent.mkdir(parents=True, mode=0o700)
    root = parent / "stage-writer"
    context = audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None)
    recorder = audit.AttemptRecorder(
        root,
        context=context,
        monotonic=lambda: 0.0,
        utc_now=lambda: "2026-11-04T00:00:00Z",
        audit_schema_version=2,
    )
    opened = recorder.open()
    assert opened.admitted, f"native writer assertions NOTRUN without qualified storage: {opened.fault}"
    started = recorder.start(
        context,
        logical_id=ATTEMPT,
        parent_id=None,
        cause="initial",
        backend=audit.BackendObservation(**history()[1]["backend"]),
        request_digest=digest(b"request"),
        request_bytes=7,
        action_deadline=100.0,
    )
    assert started.admitted
    return SimpleNamespace(recorder=recorder, root=root, run=root / RUN, attempt=started.attempt_id)


def test_native_stage_writer_vertical_lifecycle_and_immutable_adoption(native_stage_writer):
    fixture = native_stage_writer
    recorder = fixture.recorder
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted and begun.checkpoint is None
    handle = begun.handle
    first = recorder.append_stage(handle, offset=0, data=b"abc", action_deadline=100.0)
    assert first.admitted and first.accepted_bytes == 3
    path = fixture.run / ("raw-" + handle.identity.artifact_id)
    inode = path.stat().st_ino
    second = recorder.append_stage(handle, offset=3, data=b"def", action_deadline=100.0)
    assert second.admitted and second.checkpoint.retained_bytes == 6
    assert not recorder.snapshot().audit_complete
    sealed = recorder.seal_stage(handle, checkpoint=second.checkpoint, action_deadline=100.0)
    assert sealed.admitted and sealed.accepted_bytes == 0
    assert not audit._fold(recorder._events)[2]
    adopted = recorder.adopt_stage(
        handle, checkpoint=sealed.checkpoint, complete=True, original_bytes=6, action_deadline=100.0
    )
    assert adopted.admitted and adopted.ref.artifact_id == handle.identity.artifact_id
    assert path.read_bytes() == b"abcdef" and path.stat().st_ino == inode
    assert (
        recorder.adopt_stage(
            handle, checkpoint=sealed.checkpoint, complete=True, original_bytes=6, action_deadline=100.0
        )
        == adopted
    )
    assert sum(event["event"] == "acquired" for event in recorder._events) == 1
    assert recorder.snapshot().audit_complete


def test_native_stage_writer_partial_quota_can_seal_and_adopt(native_stage_writer):
    fixture = native_stage_writer
    recorder = fixture.recorder
    handle = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0).handle
    first = recorder.append_stage(
        handle, offset=0, data=b"x" * (audit.V2_WIRE_BYTES - 1), action_deadline=100.0
    )
    assert first.admitted
    partial = recorder.append_stage(
        handle, offset=audit.V2_WIRE_BYTES - 1, data=b"yz", action_deadline=100.0
    )
    assert not partial.admitted and partial.partial and partial.accepted_bytes == 1
    assert partial.checkpoint.retained_bytes == audit.V2_WIRE_BYTES and not partial.publication_uncertain
    sealed = recorder.seal_stage(handle, checkpoint=partial.checkpoint, action_deadline=100.0)
    assert sealed.admitted and sealed.partial
    adopted = recorder.adopt_stage(
        handle,
        checkpoint=sealed.checkpoint,
        complete=False,
        original_bytes=audit.V2_WIRE_BYTES + 1,
        action_deadline=100.0,
    )
    assert not adopted.admitted and adopted.ref is not None and adopted.ref.partial
    assert sum(event["event"] == "acquired" for event in recorder._events) == 1


def test_native_stage_writer_cancel_never_seals_or_adopts(native_stage_writer):
    fixture = native_stage_writer
    recorder = fixture.recorder
    handle = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0).handle
    appended = recorder.append_stage(handle, offset=0, data=b"abc", action_deadline=100.0)
    assert recorder.cancel_stage(handle)
    assert not recorder.seal_stage(
        handle, checkpoint=appended.checkpoint, action_deadline=100.0
    ).admitted
    assert not recorder.adopt_stage(
        handle, checkpoint=appended.checkpoint, complete=False, original_bytes=3, action_deadline=100.0
    ).admitted
    assert not any(event["event"] == "acquired" for event in recorder._events)


def test_native_stage_writer_terminal_prune_preserves_journal_not_prefix_claim(native_stage_writer):
    fixture = native_stage_writer
    recorder = fixture.recorder
    handle = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0).handle
    appended = recorder.append_stage(handle, offset=0, data=b"abc", action_deadline=100.0)
    sealed = recorder.seal_stage(handle, checkpoint=appended.checkpoint, action_deadline=100.0)
    assert recorder.adopt_stage(
        handle, checkpoint=sealed.checkpoint, complete=True, original_bytes=3, action_deadline=100.0
    ).admitted
    assert recorder.finish(
        fixture.attempt, outcome="completed", error_class=None, duration_s=0.0, dispatch_state="unknown"
    ).admitted
    assert recorder.finalize("completed").admitted
    assert audit.maintain_audit_root(
        fixture.root,
        monotonic=lambda: 0.0,
        utc_now=lambda: "2026-11-08T00:00:00Z",
        action_deadline=100.0,
    ).admitted
    assert audit.AttemptRecorder.reopen(fixture.root, RUN).audit_complete
    diagnostic = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
    assert not diagnostic.prefixes_verified and diagnostic.stages == () and not diagnostic.faults
    assert diagnostic.pruned_artifact_ids == (handle.identity.artifact_id,)


@pytest.mark.parametrize("adopted", [False, True])
def test_stage_raw_validation_reads_and_hashes_each_artifact_once_per_pass(monkeypatch, adopted):
    """I/O-call control only, not qualified storage or durability evidence."""
    events = history()
    data = b"abcdef"
    block(events, data[:3])
    block(events, data)
    if adopted:
        seal(events)
        adopt(events, data)
    reads = []

    def read(fd, name, *args, **kwargs):
        reads.append(name)
        assert name == "raw-" + ARTIFACT
        return data

    def forbidden_sync(*args, **kwargs):
        raise AssertionError("content validation must not sync storage")

    monkeypatch.setattr(audit, "_read_file", read)
    monkeypatch.setattr(audit, "_entries", lambda fd: ["raw-" + ARTIFACT])
    monkeypatch.setattr(audit.os, "fsync", forbidden_sync)
    work = count_hash_work(monkeypatch)
    audit._raw_contents(-1, {"events": events, "tombstone": None})
    assert reads == ["raw-" + ARTIFACT]
    assert sum(row["constructor_bytes"] + row["update_bytes"] for row in work) == len(data)
    # A new validation must read and hash again, never trust a prior stat cache.
    audit._raw_contents(-1, {"events": events, "tombstone": None})
    assert reads == ["raw-" + ARTIFACT] * 2
    assert sum(row["constructor_bytes"] + row["update_bytes"] for row in work) == 2 * len(data)


@pytest.mark.parametrize(
    "cut",
    [
        "partial_raw",
        "raw_fsync",
        "directory_fsync",
        "checkpoint_append",
        "checkpoint_fsync",
        "postwrite_verify",
        "late_deadline",
        "late_cancel",
    ],
)
def test_native_stage_append_cutpoints_never_ack_or_resume(native_stage_writer, monkeypatch, cut):
    """Real admitted recorder; never replace profile or storage qualification."""
    import os
    import stat

    fixture = native_stage_writer
    recorder = fixture.recorder
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted
    handle = begun.handle
    flags = {"raw": False, "journal": False, "hit": False}
    write = audit._write_all
    sync = os.fsync
    append = audit._append
    read = audit._read_file
    stage_append = recorder._append_stage_event
    payload = b"raw-cutpoint-payload"

    def fail():
        flags["hit"] = True
        raise OSError("injected stage cutpoint")

    def write_at_cut(fd, data, **kwargs):
        if data == payload:
            flags["raw"] = True
            if cut == "partial_raw":
                os.write(fd, data[:3])
                fail()
        return write(fd, data, **kwargs)

    def sync_at_cut(fd):
        if flags["raw"]:
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            if (
                (cut == "raw_fsync" and not directory and not flags["journal"])
                or (cut == "directory_fsync" and directory)
                or (cut == "checkpoint_fsync" and flags["journal"])
            ):
                fail()
        return sync(fd)

    def append_at_cut(fd, event, **kwargs):
        if event["event"] == "stage_checkpoint":
            flags["journal"] = True
            if cut == "checkpoint_append":
                fail()
        return append(fd, event, **kwargs)

    def read_at_cut(fd, name, *args, **kwargs):
        if cut == "postwrite_verify" and flags["raw"] and name.startswith("raw-"):
            fail()
        return read(fd, name, *args, **kwargs)

    def late_cut(fd, event, checkpoint):
        result = stage_append(fd, event, checkpoint)
        if event["event"] == "stage_checkpoint" and cut.startswith("late_"):
            flags["hit"] = True
            if cut == "late_cancel":
                assert recorder.cancel_stage(handle)
            else:
                recorder._monotonic = lambda: 100.0
        return result

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_write_all", write_at_cut)
        patch.setattr(audit.os, "fsync", sync_at_cut)
        patch.setattr(audit, "_append", append_at_cut)
        patch.setattr(audit, "_read_file", read_at_cut)
        patch.setattr(recorder, "_append_stage_event", late_cut)
        result = recorder.append_stage(handle, offset=0, data=payload, action_deadline=100.0)
    assert flags["hit"]
    assert not result.admitted and result.accepted_bytes == 0 and result.checkpoint is None
    assert result.publication_uncertain
    assert recorder._stage_handles[handle.identity.artifact_id]["uncertain"]
    recorder._monotonic = lambda: 0.0
    assert not recorder.seal_stage(handle, checkpoint=result.checkpoint, action_deadline=100.0).admitted
    assert not recorder.append_stage(handle, offset=0, data=payload, action_deadline=100.0).admitted
    assert not any(event["event"] == "acquired" for event in recorder._events)


@pytest.mark.parametrize("phase", ["seal", "adopt"])
@pytest.mark.parametrize("failure", ["append", "lost_ack"])
def test_native_stage_terminal_journal_failure_never_duplicates_credit(
    native_stage_writer, monkeypatch, phase, failure
):

    fixture = native_stage_writer
    recorder = fixture.recorder
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted
    handle = begun.handle
    saved = recorder.append_stage(handle, offset=0, data=b"abc", action_deadline=100.0)
    assert saved.admitted
    if phase == "adopt":
        assert recorder.seal_stage(handle, checkpoint=saved.checkpoint, action_deadline=100.0).admitted
    kind = "stage_seal" if phase == "seal" else "acquired"
    original = audit._append
    hits = []

    def cut(fd, event, **kwargs):
        if event["event"] == kind:
            hits.append(kind)
            if failure == "lost_ack":
                original(fd, event, **kwargs)
            raise OSError("injected journal failure")
        return original(fd, event, **kwargs)

    def action():
        if phase == "seal":
            return recorder.seal_stage(handle, checkpoint=saved.checkpoint, action_deadline=100.0)
        return recorder.adopt_stage(
            handle,
            checkpoint=saved.checkpoint,
            complete=True,
            original_bytes=3,
            usage=stage_test_usage(value=7),
            action_deadline=100.0,
        )

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", cut)
        result = action()
    assert hits == [kind] and not result.admitted and result.publication_uncertain
    before = (fixture.run / "journal").read_bytes()
    assert not action().admitted
    assert (fixture.run / "journal").read_bytes() == before
    events, _, _, torn, invalid = audit._read_events(before, RUN)
    assert not torn and not invalid
    assert sum(event["event"] == kind for event in events) == (failure == "lost_ack")

    if phase == "adopt" and failure == "lost_ack":
        first = audit.AttemptRecorder.reopen(fixture.root, RUN)
        second = audit.AttemptRecorder.reopen(fixture.root, RUN)
        assert first.summary["usage"]["input_tokens"]["known_subtotal"] == 7
        assert second.summary["usage"] == first.summary["usage"]
        assert first.summary["observation_count"] == second.summary["observation_count"] == 1

    if phase == "seal" and failure == "lost_ack":
        recovered = audit.AttemptRecorder.reopen_stages(fixture.root, RUN)
        assert recovered.prefixes_verified and len(recovered.stages) == 1
        projection = recovered.stages[0]
        assert projection.sealed and not projection.adopted
        assert projection.checkpoint_bytes == 3 and projection.unverified_tail_bytes == 0
        assert recovered.charge_bytes == audit.V2_RUN_BYTES
        assert not recovered.audit_complete and not recovered.maintenance_eligible
        ordinary = audit.AttemptRecorder.reopen(fixture.root, RUN)
        assert not ordinary.audit_complete
        assert ordinary.summary["observation_count"] == 0
        assert ordinary.summary["usage"]["input_tokens"]["known_subtotal"] == 0
        with pytest.raises(audit._Rejected, match="identity_conflict"):
            recorder._owned_stage(audit.StageHandle(projection.identity))
        assert (fixture.run / "journal").read_bytes() == before


def test_native_stage_chunk_physical_read_hash_and_sync_counts(native_stage_writer, monkeypatch):
    """Counts chunk mutation only; guard/inventory full validations are separate."""
    import os
    import stat

    fixture = native_stage_writer
    recorder = fixture.recorder
    work = count_hash_work(monkeypatch)
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted
    handle = begun.handle
    active = [False]
    reads, syncs = [], []
    original_chunk = recorder._write_stage_chunk
    original_read = audit._read_file
    original_sync = os.fsync
    measured = []

    def chunk(*args, **kwargs):
        active[0] = True
        start = len(work)
        try:
            return original_chunk(*args, **kwargs)
        finally:
            measured.append(work[start:])
            active[0] = False

    def read(fd, name, *args, **kwargs):
        if active[0] and name.startswith("raw-"):
            reads.append((name, kwargs.get("_stable", False)))
        return original_read(fd, name, *args, **kwargs)

    def sync(fd):
        if active[0]:
            held = os.fstat(fd)
            raw = (fixture.run / ("raw-" + handle.identity.artifact_id)).stat()
            syncs.append(
                "directory"
                if stat.S_ISDIR(held.st_mode)
                else "raw"
                if held.st_ino == raw.st_ino
                else "journal"
            )
        return original_sync(fd)

    monkeypatch.setattr(recorder, "_write_stage_chunk", chunk)
    monkeypatch.setattr(audit, "_read_file", read)
    monkeypatch.setattr(audit.os, "fsync", sync)
    for offset, data in [(0, b"abc"), (3, b"def")]:
        assert recorder.append_stage(handle, offset=offset, data=data, action_deadline=100.0).admitted
    name = "raw-" + handle.identity.artifact_id
    # Second chunk also rereads the old prefix for the unchanged _space disk accounting.
    assert reads == [(name, True), (name, False), (name, True)]
    assert syncs == ["raw", "directory", "journal", "raw", "journal"]
    # Per chunk: retry signature hashes input, expected hash updates only delta,
    # actual file verification hashes the full prefix. No old+new concatenation.
    assert [sum(row["constructor_bytes"] for row in rows) for rows in measured] == [6, 9]
    assert [sum(row["update_bytes"] for row in rows) for rows in measured] == [3, 3]


def test_stage_reopen_threads_deadline_into_root_preparation(monkeypatch, tmp_path):
    clock = [0.0]
    calls = []

    def prepare(root, *, create, _checkpoint):
        assert not create
        clock[0] = 2.0
        _checkpoint()
        calls.append("would start fsync")

    monkeypatch.setattr(audit, "_prepare_audit_root", prepare)
    result = audit.AttemptRecorder.reopen_stages(
        tmp_path, RUN, monotonic=lambda: clock[0], action_deadline=1.0
    )
    assert not result.prefixes_verified and result.faults[0].code == "lock_timeout"
    assert not calls


def stage_test_usage(availability="partial", value=5, kind="cumulative"):
    return audit.UsageObservation(
        value, None, None, None, availability, "unknown", "unknown", kind, "native", "attempt", {}
    )


@pytest.mark.parametrize(
    "case",
    [
        "invalid",
        "decrease",
        "after_final",
        "none",
        "unknown",
        "settlement_none",
        "settlement_prior_invalid",
        "ordinary_prior_invalid",
    ],
)
def test_stage_adopt_matches_capture_usage_admission_before_any_write(monkeypatch, tmp_path, case):
    """Runs actual adoption operation, substituting no storage/profile admission."""
    recorder = control_recorder(tmp_path)
    events = history()[:2]
    if case in ("decrease", "after_final", "settlement_prior_invalid", "ordinary_prior_invalid"):
        add(
            events,
            "acquired",
            attempt_id=ATTEMPT,
            observation_id=OTHER,
            layer="decoded",
            ref=asdict(audit.RawRef(OTHER, "decoded", digest(b"x"), 1, 1, False)),
            complete=True,
            usage=asdict(
                stage_test_usage("invalid", None)
                if case.endswith("prior_invalid")
                else stage_test_usage(kind="final" if case == "after_final" else "cumulative")
            ),
        )
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OBSERVATION,
        artifact_id=ARTIFACT,
        layer="wire",
    )
    block(events)
    seal(events)
    recorder._events = events
    identity = audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire")
    handle = audit.StageHandle(identity)
    checkpoint = audit.StageCheckpoint(identity, 3, digest(b"abc"))
    state = dict(sealed=True, checkpoint=checkpoint, capped=False, adopted=None, pending=False)
    writes = []
    monkeypatch.setattr(
        recorder,
        "_stage_guard",
        lambda attempt, handle, deadline, operation, **kwargs: operation(None, -1, state, lambda: None),
    )
    monkeypatch.setattr(audit, "_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(recorder, "_append_stage_event", lambda fd, event, tick: writes.append(event))
    usage = None
    if case == "invalid":
        usage = stage_test_usage("invalid", None)
    elif case == "unknown":
        usage = stage_test_usage("unknown", None)
    elif case in ("decrease", "after_final"):
        usage = stage_test_usage(value=4)
    kwargs = dict(
        checkpoint=checkpoint, complete=True, original_bytes=3, usage=usage, action_deadline=100.0
    )
    if case in ("settlement_none", "settlement_prior_invalid"):
        kwargs["dispatch_state"] = "unknown"
    if case in ("invalid", "decrease", "after_final", "ordinary_prior_invalid"):
        with pytest.raises(audit._Rejected, match="invalid_input"):
            recorder.adopt_stage(handle, **kwargs)
        assert not writes and state["adopted"] is None and not state["pending"]
    else:
        result = recorder.adopt_stage(handle, **kwargs)
        assert result.admitted and len(writes) == 1


@pytest.mark.parametrize("case", ["empty", "partial_quota", "cancel_late_ack"])
def test_native_stage_real_capture_sink_recorder_pipeline(native_stage_writer, monkeypatch, case):
    import io
    from code_forge.invocation_stage_sink import RecorderStageSink, StageSinkUnavailable
    from code_forge.invocation_stream import WireCapture

    fixture = native_stage_writer
    recorder = fixture.recorder
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted
    sink = RecorderStageSink(recorder, begun, action_deadline=100.0)
    warnings = []
    capture = WireCapture(
        write=sink.write, warn=warnings.append, limit_bytes=audit.V2_WIRE_BYTES + 1, chunk_bytes=65536
    )
    if case == "cancel_late_ack":
        original = recorder.append_stage

        def late(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result.admitted
            sink.cancel()
            return result

        monkeypatch.setattr(recorder, "append_stage", late)
    body = (
        b""
        if case == "empty"
        else b"x" * (audit.V2_WIRE_BYTES + 1)
        if case == "partial_quota"
        else b"ab"
    )
    list(capture.chunks(io.BytesIO(body)))
    snapshot = capture.seal()
    if case == "cancel_late_ack":
        assert snapshot.accepted_bytes == 0 and not snapshot.complete
        with pytest.raises(StageSinkUnavailable):
            sink.seal(snapshot)
    else:
        checkpoint = sink.seal(snapshot)
        assert checkpoint.retained_bytes == (audit.V2_WIRE_BYTES if case == "partial_quota" else 0)
        assert snapshot.complete == (case == "empty")
        assert snapshot.accepted_bytes == checkpoint.retained_bytes
    assert not any(
        event["event"] == "acquired" and event["layer"] == "wire" for event in recorder._events
    )


def test_native_stage_begin_lost_ack_recovers_diagnostic_not_handle(native_stage_writer, monkeypatch):
    recorder = native_stage_writer.recorder
    original = audit._append

    def lost(fd, event, **kwargs):
        result = original(fd, event, **kwargs)
        if event["event"] == "stage_begin":
            raise OSError("begin published, ACK lost")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", lost)
        result = recorder.begin_stage(native_stage_writer.attempt, layer="wire", action_deadline=100.0)
    assert not result.admitted and result.publication_uncertain and result.handle is None
    assert not recorder._stage_handles
    reopened = audit.AttemptRecorder.reopen_stages(native_stage_writer.root, RUN)
    assert reopened.prefixes_verified and len(reopened.stages) == 1
    assert not reopened.stages[0].adopted and not reopened.maintenance_eligible
    assert reopened.charge_bytes == audit.V2_RUN_BYTES


@pytest.mark.parametrize(
    "case",
    [
        "settlement",
        "ordinary",
        "bad_dispatch",
        "identity",
        "integrity",
        "uncertain",
        "pending",
        "append_uncertain",
        "tail",
        "cancel",
        "torn_native",
        "torn_no_native",
        "torn_storage_damage",
    ],
)
def test_real_stage_guard_limits_settlement_to_response_faults(monkeypatch, tmp_path, case):
    """Real guard/writable/checked state transitions; no storage qualification."""
    from contextlib import contextmanager

    recorder = control_recorder(tmp_path)
    recorder._opened = True
    recorder._complete = False
    recorder._events = history()
    if case in ("torn_native", "torn_storage_damage"):
        add(
            recorder._events,
            "acquired",
            attempt_id=ATTEMPT,
            observation_id=OTHER,
            layer="stdout",
            ref=asdict(audit.RawRef(OTHER, "stdout", digest(b"{}"), 2, 2, False)),
            complete=True,
            usage=None,
        )
        add(
            recorder._events,
            "response_observed",
            attempt_id=ATTEMPT,
            observation_id="00000000-0000-4000-8000-000000000006",
            source_observation_id=OTHER,
            frame_offset=0,
            frame_length=2,
            usage=asdict(stage_test_usage("invalid", None)),
            observed_backend=None,
        )
        assert audit._native_usage_fault(recorder._events)
    identity = audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire")
    handle = audit.StageHandle(identity)
    state = dict(
        handle=handle,
        sealed=True,
        checkpoint=None,
        capped=False,
        pending=case == "pending",
        uncertain=case == "uncertain",
    )
    recorder._stage_handles[ARTIFACT] = state
    if case in ("identity", "integrity"):
        recorder._faults.append(
            audit.AuditFault(
                "identity_conflict" if case == "identity" else "integrity_error", "acquired", ATTEMPT
            )
        )
    recorder._append_uncertain = case == "append_uncertain"
    if case == "cancel":
        handle._cancel.set()
    calls = []

    @contextmanager
    def directory(*args):
        yield -1

    def qualified(*args, **kwargs):
        calls.append(kwargs)
        return dict(
            events=recorder._events,
            terminal=False,
            stage_unknown_tail=case == "tail",
            torn=case.startswith("torn_"),
            storage_settleable=case != "torn_storage_damage",
        )

    monkeypatch.setattr(audit, "_run_dir", directory)
    monkeypatch.setattr(audit, "_revalidate", lambda *args: None)
    monkeypatch.setattr(audit, "_qualified_run", qualified)
    monkeypatch.setattr(audit, "_inventory", lambda *args, **kwargs: None)
    monkeypatch.setattr(recorder, "_guard", lambda phase, action, **kwargs: action(None))
    completed = []

    def operation(*args):
        completed.append(True)
        return audit.StageAcknowledgement(True, handle, None, 0, False, None)

    result = recorder._stage_guard(
        ATTEMPT,
        handle,
        100.0,
        operation,
        settlement=None if case == "ordinary" else "invalid" if case == "bad_dispatch" else "unknown",
    )
    assert result.admitted == (case in ("settlement", "torn_native"))
    assert bool(completed) == (case in ("settlement", "torn_native"))
    if case in ("settlement", "torn_native"):
        assert calls[0]["settlement"] is True


@pytest.mark.parametrize("extra", ["raw-new", "input-manifest.json", "fault", "tombstone"])
def test_v2_slot_budget_reserves_all_fixed_destinations_and_new_temp(monkeypatch, extra):
    fixed = {"journal", "admission", "input-manifest.json", "final", "fault", "freeze", "tombstone"}
    names = set(fixed) | {f"temp-failed-{i}" for i in range(4087)}
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: names.copy())
    assert audit._file_slots(-1, [], extra_names=(extra,)) <= 4096
    names.add("temp-other-failure")
    if extra == "raw-new":
        with pytest.raises(audit._Rejected, match="quota_refused"):
            audit._file_slots(-1, [], extra_names=(extra,))
    else:
        assert audit._file_slots(-1, [], extra_names=(extra,)) == 4096
    names.add("temp-concurrent-new-entry")
    with pytest.raises(audit._Rejected, match="quota_refused"):
        audit._file_slots(-1, [], extra_names=(extra,))


def test_v2_registered_raw_slot_survives_cancel_and_transfers_on_empty_materialization(monkeypatch):
    names = {"journal", "admission"}
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: names.copy())
    events = history()
    assert audit._file_slots(-1, events) == 9
    # Cancel has no journal event and cannot release the registered slot.
    handle = audit.StageHandle(audit.StageIdentity(RUN, ATTEMPT, OBSERVATION, ARTIFACT, "wire"))
    handle._cancel.set()
    assert audit._file_slots(-1, events) == 9
    names.add("raw-" + ARTIFACT)
    block(events, b"")
    seal(events)
    adopt(events, b"")
    assert audit._file_slots(-1, events) == 9


@pytest.mark.parametrize("kind", ["stage_begin", "audit_fault", "run_final"])
def test_v2_slot_refusal_precedes_any_journal_write(monkeypatch, kind):
    events = history()
    event = dict(events[-1], event=kind)
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: {"journal"})
    monkeypatch.setattr(
        audit, "_read_file", lambda *args, **kwargs: b"".join(map(audit._frame, events[:-1]))
    )

    def reject(*args, **kwargs):
        raise audit._Rejected("quota_refused")

    def forbidden(*args, **kwargs):
        raise AssertionError("journal opened after slot refusal")

    monkeypatch.setattr(audit, "_file_slots", reject)
    monkeypatch.setattr(audit.os, "open", forbidden)
    with pytest.raises(audit._Rejected, match="quota_refused"):
        audit._append(-1, event)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("cut", ["empty_directory", "admission"])
def test_native_stage_failed_open_preserves_full_charge_and_root_version(
    native_stage_writer, monkeypatch, version, cut
):
    from dataclasses import replace

    root = native_stage_writer.root.parent / "root-compat"
    context = audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None)

    def recorder(run_id, schema):
        return audit.AttemptRecorder(
            root,
            context=replace(context, run_id=run_id),
            monotonic=lambda: 0.0,
            utc_now=lambda: "2026-11-04T00:00:00Z",
            audit_schema_version=schema,
        )

    first = recorder(RUN, version)
    original_append = audit._append
    original_publish = audit._publish

    def append(fd, event, **kwargs):
        if cut == "empty_directory" and event["event"] == "run_open":
            raise OSError("crash before journal creation")
        return original_append(fd, event, **kwargs)

    def publish(fd, name, data, **kwargs):
        if cut == "admission" and name == "admission":
            raise OSError("crash after run_open before admission")
        return original_publish(fd, name, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "_append", append)
        patch.setattr(audit, "_publish", publish)
        assert not first.open().admitted
    if cut == "admission":
        # Test the incomplete run's own version evidence, before a healthy run
        # could independently enforce the root's version.
        early = recorder(ARTIFACT, 3 - version).open()
        assert not early.admitted and early.fault.code == "identity_conflict"
        assert not (root / ARTIFACT).exists()
    second = recorder(OTHER, version)
    assert second.open().admitted
    handle = audit._prepare_audit_root(root, create=False)
    assert not isinstance(handle, audit.AuditFault)
    try:
        with audit._root_lock(handle):
            rows, charge = audit._inventory(handle)
            assert rows[RUN]["charge"] == audit._run_limit(version)
            assert not rows[RUN]["terminal"] and charge == 2 * audit._run_limit(version)
    finally:
        handle.close()
    third = recorder(ARTIFACT, 3 - version)
    denied = third.open()
    assert not denied.admitted and denied.fault.code == "identity_conflict"


def test_v2_pruned_adopted_stage_releases_only_validated_tombstone_slot(monkeypatch):
    import json

    events = history()
    block(events)
    seal(events)
    adopt(events)
    names = {"journal", "admission"}
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: names.copy())
    assert audit._file_slots(-1, events) == 9
    names.add("tombstone")
    monkeypatch.setattr(audit, "_read_file", lambda *args, **kwargs: b"{}")
    with pytest.raises(audit._Rejected):
        audit._file_slots(-1, events)
    # Isolate only slot accounting after authoritative marker validation.
    checked = []
    monkeypatch.setattr(
        audit, "_marker_projections", lambda metadata, rows: checked.append((metadata, rows))
    )
    assert audit._file_slots(-1, events) == 8
    assert checked == [({"tombstone": json.loads(b"{}")}, events)]
    assert audit._file_slots(-1, events, extra_names=("tombstone",)) == 8


def test_native_stage_sealed_adoption_can_settle_native_usage_fault(native_stage_writer):
    fixture = native_stage_writer
    recorder = fixture.recorder
    source = recorder.acquired(fixture.attempt, layer="stdout", data=b"{}", complete=True, usage=None)
    assert source.admitted
    begun = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begun.admitted
    saved = recorder.append_stage(begun.handle, offset=0, data=b"abc", action_deadline=100.0)
    assert saved.admitted
    assert recorder.seal_stage(begun.handle, checkpoint=saved.checkpoint, action_deadline=100.0).admitted
    fault = recorder.observe_response(
        fixture.attempt,
        source_observation_id=source.observation_id,
        frame_offset=0,
        frame_length=2,
        usage=stage_test_usage("invalid", None),
        observed_backend=None,
    )
    assert not fault.admitted and fault.fault.code == "invalid_input"
    ordinary = recorder.adopt_stage(
        begun.handle, checkpoint=saved.checkpoint, complete=True, original_bytes=3, action_deadline=100.0
    )
    assert not ordinary.admitted
    settled = recorder.adopt_stage(
        begun.handle,
        checkpoint=saved.checkpoint,
        complete=True,
        original_bytes=3,
        usage=None,
        dispatch_state="unknown",
        action_deadline=100.0,
    )
    assert settled.admitted and settled.ref.artifact_id == begun.handle.identity.artifact_id
    assert not recorder.snapshot().audit_complete
    assert sum(event["event"] == "settled_acquired" for event in recorder._events) == 1


@pytest.mark.parametrize(
    "name", ["raw-" + ARTIFACT, "input-manifest.json", "fault", "final", "freeze", "tombstone"]
)
def test_v2_publication_rechecks_slots_before_temp_creation(monkeypatch, name):
    names = {f"temp-failed-{i}" for i in range(4096)}
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: names.copy())

    def forbidden(*args, **kwargs):
        raise AssertionError("temporary file created after slot exhaustion")

    monkeypatch.setattr(audit.os, "open", forbidden)
    for _ in range(2):
        with pytest.raises(audit._Rejected, match="quota_refused"):
            audit._publish(-1, name, b"{}", replace=name == "tombstone", _slot_events=history())
    assert len(names) == 4096


def test_slot_reservation_event_processing_honors_deadline(monkeypatch):
    monkeypatch.setattr(audit, "_bounded_stage_entries", lambda fd, tick: {"journal", "admission"})
    count = []

    def tick():
        count.append(None)
        if len(count) == 2:
            raise audit._Rejected("lock_timeout")

    with pytest.raises(audit._Rejected, match="lock_timeout"):
        audit._file_slots(-1, history(), _checkpoint=tick)
    assert len(count) == 2


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_v2_openai_wire_stage_actual_prefix_cap_boundary(delta):
    events = history()
    data = b"x" * (audit.V2_WIRE_BYTES + delta)
    block(events, data)
    if delta > 0:
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, {ARTIFACT: data})
    else:
        (projection,) = audit._replay_stage_candidate(events, {ARTIFACT: data})
        assert projection.checkpoint_bytes == len(data)
        assert not projection.adopted


@pytest.mark.parametrize(
    "backend,layer",
    [
        ("cli", "wire"),
        ("cli", "stdout"),
        ("cli", "stderr"),
        ("openai", "decoded"),
        ("anthropic", "wire"),
    ],
)
def test_v2_non_openai_wire_layers_keep_256k_limit(backend, layer):
    events = history()
    events[1]["backend"]["format"] = backend
    events[2]["layer"] = layer
    data = b"x" * (audit.BODY_BYTES + 1)
    block(events, data)
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: data})


def test_v2_cli_stdout_stderr_share_bucket_but_openai_wire_does_not():
    events = history()
    events[1]["backend"]["format"] = "cli"
    events[2]["layer"] = "stdout"
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        artifact_id=OTHER,
        layer="stderr",
    )
    with pytest.raises(audit._Rejected):
        audit._replay_stage_candidate(events, {ARTIFACT: b"x" * audit.BODY_BYTES, OTHER: b"x"})
    events[1]["backend"]["format"] = "openai"
    events[2]["layer"] = "wire"
    assert (
        len(
            audit._replay_stage_candidate(
                events, {ARTIFACT: b"x" * audit.V2_WIRE_BYTES, OTHER: b"x" * audit.BODY_BYTES}
            )
        )
        == 2
    )


@pytest.mark.parametrize(
    "backend,dispatch", [("openai", "entered_cli_launch"), ("cli", "entered_api_send")]
)
@pytest.mark.parametrize("kind", ["finish", "settled_acquired"])
def test_v2_known_dispatch_backend_contradictions_fail_closed(backend, dispatch, kind):
    events = history()[:2]
    events[1]["backend"]["format"] = backend
    if kind == "finish":
        add(
            events,
            kind,
            attempt_id=ATTEMPT,
            outcome="completed",
            error_class=None,
            duration_s=1.0,
            dispatch_state=dispatch,
        )
    else:
        add(
            events,
            kind,
            attempt_id=ATTEMPT,
            observation_id=OBSERVATION,
            layer="wire",
            ref=asdict(audit.RawRef(ARTIFACT, "wire", digest(b""), 0, 0, False)),
            complete=True,
            usage=None,
            dispatch_state=dispatch,
        )
    with pytest.raises(audit._Rejected):
        audit._fold(events)


@pytest.mark.parametrize(
    "layer,expected",
    [("wire", 8 * 1024 * 1024), ("decoded", 256 * 1024), ("stdout", 256 * 1024), ("stderr", 256 * 1024)],
)
def test_v2_raw_read_bounds_come_from_attempt_layer(layer, expected):
    events = history()
    events[2]["layer"] = layer
    assert audit._artifact_limits(events)[ARTIFACT] == expected


@pytest.mark.parametrize("version", [1, 2])
def test_run_exhaustion_precedes_remaining_response_capacity(monkeypatch, version):
    events = history()[:2]
    for event in events:
        event["schema_version"] = version
    events[0]["reservation"] = audit._reservation_policy(version)
    maximum = audit._run_limit(version) - audit.MARKER_BYTES
    monkeypatch.setattr(
        audit, "_disk_run", lambda *args, **kwargs: dict(events=events, ordinary=maximum - 10, marker=0)
    )
    audit._space(-1, RUN, ordinary_delta=10)
    with pytest.raises(audit._Rejected, match="quota_refused"):
        audit._space(-1, RUN, ordinary_delta=11)


def test_native_stage_v2_root_reserves_eight_runs_and_refuses_ninth(native_stage_writer):
    from dataclasses import replace

    fixture = native_stage_writer
    context = audit.InvocationContext(RUN, None, None, 0, "review", None, None, "review", None)
    for index in range(1, 9):
        run_id = f"00000000-0000-4000-8000-{index + 100:012d}"
        recorder = audit.AttemptRecorder(
            fixture.root,
            context=replace(context, run_id=run_id),
            monotonic=lambda: 0.0,
            utc_now=lambda: "2026-11-04T00:00:00Z",
            audit_schema_version=2,
        )
        result = recorder.open()
        assert result.admitted == (index < 8)
        if index == 8:
            assert result.fault.code == "quota_refused" and not (fixture.root / run_id).exists()
    handle = audit._prepare_audit_root(fixture.root, create=False)
    assert not isinstance(handle, audit.AuditFault)
    try:
        with audit._root_lock(handle):
            rows, total = audit._inventory(handle, _version=2)
            assert len(rows) == 8 and total == audit.ROOT_BYTES
            assert all(row["charge"] == audit.V2_RUN_BYTES for row in rows.values())
    finally:
        handle.close()


def test_native_stage_v2_eight_megabyte_prefix_reopens_with_required_limits(native_stage_writer):
    import json

    fixture = native_stage_writer
    recorder = fixture.recorder
    begin = recorder.begin_stage(fixture.attempt, layer="wire", action_deadline=100.0)
    assert begin.admitted
    data = b"x" * audit.V2_WIRE_BYTES
    saved = recorder.append_stage(begin.handle, offset=0, data=data, action_deadline=100.0)
    assert saved.admitted and saved.accepted_bytes == audit.V2_WIRE_BYTES
    recovered = audit.AttemptRecorder.reopen_stages(
        fixture.root, RUN, monotonic=lambda: 0.0, action_deadline=100.0
    )
    assert recovered.prefixes_verified and recovered.charge_bytes == audit.V2_RUN_BYTES
    assert recovered.stages[0].checkpoint_bytes == len(data)
    admission = json.loads((fixture.run / "admission").read_bytes())
    assert admission["limits"] == audit._reservation_policy(2)
    assert recorder.seal_stage(begin.handle, checkpoint=saved.checkpoint, action_deadline=100.0).admitted
    adopted = recorder.adopt_stage(
        begin.handle,
        checkpoint=saved.checkpoint,
        complete=True,
        original_bytes=len(data),
        action_deadline=100.0,
    )
    assert adopted.admitted and adopted.ref.retained_bytes == audit.V2_WIRE_BYTES
    assert audit.AttemptRecorder.reopen(fixture.root, RUN).audit_complete


@pytest.mark.parametrize("backend", ["cli", "anthropic", "vertex", "tool"])
@pytest.mark.parametrize("other_layer", ["stdout", "stderr"])
@pytest.mark.parametrize("overflow", [False, True])
def test_non_openai_v2_wire_shares_legacy_transport_boundary(backend, other_layer, overflow):
    events = history()
    events[1]["backend"]["format"] = backend
    add(
        events,
        "stage_begin",
        attempt_id=ATTEMPT,
        observation_id=OTHER,
        artifact_id=OTHER,
        layer=other_layer,
    )
    artifacts = {ARTIFACT: b"x" * (audit.BODY_BYTES - 1), OTHER: b"yz" if overflow else b"y"}
    if overflow:
        with pytest.raises(audit._Rejected):
            audit._replay_stage_candidate(events, artifacts)
    else:
        assert len(audit._replay_stage_candidate(events, artifacts)) == 2


@pytest.mark.parametrize("backend", ["openai", "cli", "anthropic", "vertex", "tool"])
def test_v1_wire_always_uses_unchanged_shared_transport_bucket(backend):
    assert audit._layer_policy(1, "wire", backend) == audit._layer_policy(1, "stdout", backend)
    assert audit._layer_policy(1, "wire", backend) == audit._layer_policy(1, "stderr", backend)
    assert audit._layer_policy(1, "wire", backend)[1] == audit.BODY_BYTES
