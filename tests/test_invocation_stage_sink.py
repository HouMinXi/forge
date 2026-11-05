"""Controlled recorder contract tests, NOT native persistence proof."""

from dataclasses import replace
import hashlib
import io
import threading

import pytest

from code_forge.invocation_audit import (
    AuditFault,
    StageAcknowledgement,
    StageCheckpoint as Checkpoint,
    StageHandle,
    StageIdentity,
)
from code_forge.invocation_stage_sink import RecorderStageSink, StageSinkUnavailable
from code_forge.invocation_stream import WireCapture


def Handle():
    return StageHandle(StageIdentity("run", "attempt", "observation", "artifact", "wire"))


def Ack(
    handle,
    admitted=True,
    checkpoint=None,
    accepted_bytes=0,
    partial=False,
    fault=None,
    publication_uncertain=False,
):
    return StageAcknowledgement(
        admitted, handle, checkpoint, accepted_bytes, partial, fault, publication_uncertain
    )


class Recorder:
    """Only a controlled ACK issuer, not a storage implementation."""

    def __init__(self):
        self.handle = Handle()
        self.data = b""
        self.calls = []
        self.transform = lambda ack: ack
        self.limit = None
        self.capped = False
        self.entered = self.release = None

    def begin(self):
        return Ack(self.handle)

    def checkpoint(self):
        return Checkpoint(self.handle.identity, len(self.data), hashlib.sha256(self.data).hexdigest())

    def append_stage(self, handle, *, offset, data, action_deadline):
        self.calls.append(("append", handle, offset, action_deadline))
        assert offset == len(self.data)
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        accepted = data if self.limit is None else data[: self.limit]
        self.data += accepted
        partial = len(accepted) < len(data)
        self.capped |= partial
        fault = AuditFault("quota_refused", "acquired", handle.identity.attempt_id) if partial else None
        return self.transform(Ack(handle, not partial, self.checkpoint(), len(accepted), partial, fault))

    def seal_stage(self, handle, *, checkpoint=None, action_deadline):
        self.calls.append(("seal", handle, checkpoint, action_deadline))
        return self.transform(Ack(handle, checkpoint=self.checkpoint(), partial=self.capped))

    def cancel_stage(self, handle):
        self.calls.append(("cancel", handle))

    def adopt_stage(self, *args, **kwargs):
        pytest.fail("adapter must not adopt")


def setup():
    recorder = Recorder()
    sink = RecorderStageSink(recorder, recorder.begin(), action_deadline=99.0)
    warnings = []
    capture = WireCapture(write=sink.write, warn=warnings.append, limit_bytes=1000, chunk_bytes=4)
    return recorder, sink, capture, warnings


def test_actual_capture_pipeline_and_empty_body():
    for body in (b"", b"data: [DONE]\n\nextra bytes"):
        recorder, sink, capture, warnings = setup()
        assert b"".join(capture.chunks(io.BytesIO(body))) == body
        snapshot = capture.seal()
        assert snapshot.complete
        assert sink.sealed_checkpoint is None
        checkpoint = sink.seal(snapshot)
        assert checkpoint.retained_bytes == len(body)
        assert sink.sealed_checkpoint is checkpoint
        assert recorder.data == body
        assert not warnings
        assert all(call[0] != "adopt" for call in recorder.calls)


def test_done_without_transport_eof_is_not_complete_but_can_seal_prefix():
    _, sink, capture, _ = setup()
    capture.append(b"data: [DONE]\n\n")
    snapshot = capture.seal()
    assert not snapshot.complete
    assert sink.seal(snapshot).retained_bytes == snapshot.accepted_bytes


@pytest.mark.parametrize("limit", [0, 2])
def test_known_partial_checkpoint_is_preserved_for_owner_seal(limit):
    recorder, sink, capture, warnings = setup()
    recorder.limit = limit
    capture.append(b"abcd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == limit
    assert snapshot.failed and not snapshot.write_uncertain
    assert warnings == ["capture_write_failed"]
    assert isinstance(recorder.begin(), StageAcknowledgement)
    assert not snapshot.complete
    assert sink.seal(snapshot).retained_bytes == limit
    assert not snapshot.complete  # Successful partial seal cannot upgrade acquisition.
    assert recorder.capped


@pytest.mark.parametrize(
    "transform",
    [
        lambda ack: replace(ack, handle=Handle()),
        lambda ack: replace(ack, checkpoint=replace(ack.checkpoint, identity="different")),
        lambda ack: replace(ack, checkpoint=replace(ack.checkpoint, retained_bytes=0)),
        lambda ack: replace(ack, checkpoint=replace(ack.checkpoint, sha256="wrong")),
        lambda ack: replace(ack, accepted_bytes=-1),
        lambda ack: replace(ack, accepted_bytes=5),
        lambda ack: replace(ack, accepted_bytes=True),
        lambda ack: replace(ack, accepted_bytes=0, checkpoint=None, publication_uncertain=True),
    ],
)
def test_invalid_or_unknown_ack_never_extends_capture(transform):
    recorder, sink, capture, _ = setup()
    recorder.transform = transform
    capture.append(b"abcd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == 0 and snapshot.write_uncertain
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)
    assert sink.sealed_checkpoint is None
    assert not any(call[0] == "seal" for call in recorder.calls)


def test_stale_offset_after_valid_prefix_does_not_extend_checkpoint():
    recorder, sink, capture, _ = setup()
    capture.append(b"ab")
    previous = recorder.checkpoint()
    recorder.transform = lambda ack: replace(ack, checkpoint=previous)
    capture.append(b"cd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == 2 and snapshot.write_uncertain
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)


def test_cancel_during_pending_write_freezes_prefix_and_never_seals():
    recorder, sink, capture, _ = setup()
    recorder.entered, recorder.release = threading.Event(), threading.Event()
    worker = threading.Thread(target=capture.append, args=(b"abcd",))
    worker.start()
    try:
        assert recorder.entered.wait(5)
        snapshot = capture.seal(interrupted=True)
        with pytest.raises(StageSinkUnavailable):
            sink.seal(snapshot)
        sink.cancel()
        assert recorder.calls[-1][0] == "cancel"
    finally:
        recorder.release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert capture.seal().accepted_bytes == 0
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)
    assert sink.sealed_checkpoint is None


def test_mismatched_or_unsealed_snapshot_does_not_invoke_seal():
    recorder, sink, capture, _ = setup()
    capture.append(b"ab")
    snapshot = capture.seal()
    for invalid in (
        replace(snapshot, accepted_bytes=1),
        replace(snapshot, sealed=False),
        replace(snapshot, observed_bytes=1),
        replace(snapshot, write_uncertain=True),
    ):
        with pytest.raises(StageSinkUnavailable):
            sink.seal(invalid)
    assert not any(call[0] == "seal" for call in recorder.calls)
    sink.seal(snapshot)


def test_invalid_seal_ack_never_exposes_checkpoint():
    recorder, sink, capture, _ = setup()
    capture.append(b"ab")
    recorder.transform = lambda ack: replace(ack, accepted_bytes=1)
    with pytest.raises(StageSinkUnavailable):
        sink.seal(capture.seal())
    assert sink.sealed_checkpoint is None


def test_invalid_begin_is_rejected():
    recorder = Recorder()
    for ack in (
        replace(recorder.begin(), admitted=False),
        replace(recorder.begin(), publication_uncertain=True),
        replace(recorder.begin(), accepted_bytes=1),
    ):
        with pytest.raises(StageSinkUnavailable):
            RecorderStageSink(recorder, ack, action_deadline=99.0)


def test_raw_error_never_escapes_sink():
    recorder, sink, _, _ = setup()

    def fail(ack):
        raise ValueError("secret raw https://private.invalid")

    recorder.transform = fail
    with pytest.raises(StageSinkUnavailable, match="^stage write unavailable$"):
        sink.write(b"abc")


def test_known_zero_refusal_without_checkpoint_is_not_assumed_sealable():
    recorder, sink, capture, _ = setup()
    capture.append(b"ab")

    def refuse(handle, *, offset, data, action_deadline):
        return Ack(handle, admitted=False, fault="stage_limit")

    recorder.append_stage = refuse
    capture.append(b"cd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == 2
    assert snapshot.failed and not snapshot.write_uncertain
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)
    assert not any(call[0] == "seal" for call in recorder.calls)


def test_concurrent_writer_rejected_without_losing_original_ack():
    recorder, sink, _, _ = setup()
    recorder.entered, recorder.release = threading.Event(), threading.Event()
    results = []
    worker = threading.Thread(target=lambda: results.append(sink.write(b"ab")))
    worker.start()
    try:
        assert recorder.entered.wait(5)
        with pytest.raises(StageSinkUnavailable):
            sink.write(b"cd")
    finally:
        recorder.release.set()
        worker.join(5)
    assert results == [2]


def test_cancel_is_idempotent_and_prevents_empty_write():
    recorder, sink, _, _ = setup()
    sink.cancel()
    sink.cancel()
    assert [call[0] for call in recorder.calls] == ["cancel"]
    with pytest.raises(StageSinkUnavailable):
        sink.write(b"")


def test_cancel_during_seal_discards_late_checkpoint():
    recorder, sink, capture, _ = setup()
    entered, release = threading.Event(), threading.Event()
    original = recorder.seal_stage
    errors = []

    def blocked_seal(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def seal():
        try:
            sink.seal(capture.seal())
        except StageSinkUnavailable as error:
            errors.append(str(error))

    recorder.seal_stage = blocked_seal
    worker = threading.Thread(target=seal)
    worker.start()
    try:
        assert entered.wait(5)
        sink.cancel()
    finally:
        release.set()
        worker.join(5)
    assert errors == ["stage seal unavailable"]
    assert sink.sealed_checkpoint is None


def test_known_zero_refusal_with_confirmed_checkpoint_can_seal():
    recorder, sink, capture, _ = setup()
    capture.append(b"ab")
    checkpoint = recorder.checkpoint()

    def refuse(handle, *, offset, data, action_deadline):
        return Ack(handle, admitted=False, checkpoint=checkpoint, partial=True, fault="stage_limit")

    recorder.append_stage = refuse
    capture.append(b"cd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == 2 and not snapshot.write_uncertain
    assert sink.seal(snapshot) == checkpoint


def test_cancelled_ack_with_no_checkpoint_is_not_a_positive_write():
    recorder, sink, capture, _ = setup()
    recorder.transform = lambda ack: replace(ack, admitted=False, accepted_bytes=0, checkpoint=None)
    capture.append(b"abcd")
    snapshot = capture.seal()
    assert snapshot.accepted_bytes == 0
    # The refusal has no checkpoint and cannot authorize sealing, even if
    # this deliberately inconsistent fixture later offers an expanded prefix.
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)
    assert sink.sealed_checkpoint is None


@pytest.mark.parametrize("operation", ["write", "seal", "cancel"])
def test_memory_error_preserves_identity_and_blocks_seal(operation):
    recorder, sink, capture, _ = setup()
    error = MemoryError("fatal")

    def fail(*args, **kwargs):
        raise error

    if operation == "write":
        recorder.append_stage = fail

        def action():
            return sink.write(b"ab")
    elif operation == "seal":
        recorder.seal_stage = fail

        def action():
            return sink.seal(capture.seal())
    else:
        recorder.cancel_stage = fail
        action = sink.cancel
    with pytest.raises(MemoryError) as raised:
        action()
    assert raised.value is error
    with pytest.raises(StageSinkUnavailable):
        sink.seal(capture.seal())
    assert sink.sealed_checkpoint is None


@pytest.mark.parametrize(
    "transform",
    [
        lambda ack: replace(ack, partial=True),
        lambda ack: replace(ack, fault="unexpected_fault"),
        lambda ack: replace(ack, admitted=False, partial=True),
    ],
)
def test_incoherent_full_acceptance_cannot_appear_complete(transform):
    recorder, sink, capture, _ = setup()
    recorder.transform = transform
    assert b"".join(capture.chunks(io.BytesIO(b"abcd"))) == b"abcd"
    snapshot = capture.seal()
    assert not snapshot.complete and snapshot.accepted_bytes == 0
    with pytest.raises(StageSinkUnavailable):
        sink.seal(snapshot)
