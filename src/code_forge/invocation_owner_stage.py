"""Opaque v2 owner bridge. Publication and owner-visible credit are distinct.

Only the finite supervisor runs recorder I/O. The controller commits a known,
matching ACK under short bounded locks; unknown/late completion is never credit.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import threading
import time

from .invocation_audit import AcquiredAcknowledgement, AuditAcknowledgement, AuditFault, StageHandle
from .invocation_io import AuditPhase, ProcessAuditIo
from .invocation_buffered_capture import SharedBufferedCaptureSession
from .invocation_stage_sink import RecorderStageSink, StageSinkUnavailable


class OwnerStageUnavailable(StageSinkUnavailable):
    """Safe failure metadata only; no raw recorder diagnostics."""


@dataclass(frozen=True)
class _Published:
    ticket: object
    outcome: object


class _CaptureView:
    """Only the acquisition protocol, with fatal ownership retained by the owner."""

    def __init__(self, stage, capture):
        self._stage, self._capture = stage, capture

    @contextmanager
    def _guard(self):
        self._stage._require_controller()
        try:
            yield
        except GeneratorExit:
            # Closing an acquisition iterator is not an audit exception.
            raise
        except BaseException as error:  # noqa: BLE001 - preserve the actual fatal and its cause.
            if isinstance(error, MemoryError) or not isinstance(error, Exception):
                self._stage._owner._refused = True
            raise

    def append(self, data):
        with self._guard():
            return self._capture.append(data)

    def chunks(self, response, *, preserve_primary=False):
        with self._guard():
            yield from self._capture.chunks(response, preserve_primary=preserve_primary)

    def lines(self, response):
        with self._guard():
            yield from self._capture.lines(response)

    def report_incomplete(self, code):
        with self._guard():
            return self._capture.report_incomplete(code)

    def seal(self, *, interrupted=False):
        with self._guard():
            return self._capture.seal(interrupted=interrupted)

    @property
    def buffered_bytes(self):
        with self._guard():
            return self._capture.buffered_bytes


class _Publication:
    def __init__(self, stage, phase, *, snapshot=None, usage=None, observation=None):
        self.stage, self.phase, self.snapshot, self.usage = stage, phase, snapshot, usage
        self.observation = observation

    def audit_io(self, phase, payload):
        if phase is not self.phase or payload:
            raise ValueError("invalid owner stage command")
        stage = self.stage
        if phase is AuditPhase.BEGIN:
            return stage._recorder.begin_stage(
                stage._lease.attempt_id, layer=stage._layer, action_deadline=stage._deadline
            )
        if phase is AuditPhase.SEAL:
            return stage._sink.seal(self.snapshot)
        if phase is AuditPhase.ADOPT:
            return stage._recorder.adopt_stage(
                stage._handle,
                checkpoint=stage._checkpoint,
                complete=self.snapshot.complete,
                original_bytes=self.snapshot.observed_bytes if self.snapshot.eof_seen else None,
                usage=self.usage,
                dispatch_state=stage._dispatch,
                action_deadline=stage._deadline,
            )
        if phase is AuditPhase.OBSERVE:
            return stage._recorder.observe_response(
                attempt_id=stage._lease.attempt_id,
                source_observation_id=stage._adopted[1].observation_id,
                observation_id=None,
                usage=self.usage,
                **self.observation,
            )
        raise ValueError("unsupported owner stage command")


class OwnerCaptureStage:
    """One lease/layer capability, constructed only through AttemptLease.

    ``capture`` is the acquisition facade. Its snapshot is not adoption credit.
    ``seal`` and ``adopt`` are controller calls, never calls from an I/O worker.
    Cancellation is terminal; it cannot erase an already committed adoption.
    """

    @classmethod
    def begin(
        cls,
        lease,
        *,
        supervisor,
        session,
        layer,
        warn,
        limit_bytes,
        strict,
        chunk_bytes,
        control_timeout_s,
    ):
        if type(supervisor) is not ProcessAuditIo or not isinstance(
            session, SharedBufferedCaptureSession
        ):
            raise TypeError("expected shared audit I/O")
        if threading.current_thread() is supervisor._worker:
            lease._owner._refused = True
            raise OwnerStageUnavailable("recursive audit I/O wait")
        if session._io is not supervisor or not callable(warn):
            raise ValueError("foreign capture session")
        if (
            type(control_timeout_s) not in (int, float)
            or not math.isfinite(control_timeout_s)
            or control_timeout_s <= 0
        ):
            raise ValueError("invalid control timeout")
        self = cls()
        self._control_timeout = control_timeout_s
        self._owner, self._lease, self._io = lease._owner, lease, supervisor
        self._deadline, self._layer = lease.action_deadline, layer
        self._lock = threading.Lock()
        self._cancelled = self._busy = False
        self._sink = self._checkpoint = self._snapshot = self._adopted = None
        self._capture = self._capture_view = None
        self._tickets = []
        self._warn = warn
        self._owner._check_process("acquired", lease)
        with self._owner_lock():
            record = self._owner._lease(lease)
            if (
                self._owner._audit_schema_version != 2
                or self._owner._refused
                or record.settlement_started
                or record.dispatch == "not_dispatched"
                or layer not in {"wire", "stdout", "stderr", "decoded"}
                or layer in record.stages
                or layer in record.captures
            ):
                self._fail("stage admission unavailable")
            self._recorder, self._dispatch = record.recorder, record.dispatch
            record.stages[layer] = self
        published = self._request(AuditPhase.BEGIN)
        ack = published.outcome.value
        try:
            self._handle = ack.handle
            if (
                type(self._handle) is not StageHandle
                or self._handle.identity.attempt_id != lease.attempt_id
                or self._handle.identity.run_id != lease.operation.context.run_id
                or self._handle.identity.layer != layer
            ):
                self._fail("foreign stage admission")
            self._sink = RecorderStageSink(self._recorder, ack, action_deadline=self._deadline)
            self._capture = session.new_capture(
                write=self._write,
                warn=self._notice,
                limit_bytes=limit_bytes,
                action_deadline=self._deadline,
                chunk_bytes=chunk_bytes,
                strict=strict,
            )
            self._capture_view = _CaptureView(self, self._capture)
        except BaseException:
            self._owner._refused = True
            try:
                self.cancel()
            except BaseException:  # noqa: BLE001, S110 - preserve the original admission failure.
                pass
            raise
        return self

    @contextmanager
    def _owner_lock(self):
        remaining = min(0.1, self._deadline - time.monotonic())
        if remaining <= 0 or not self._owner._mutex.acquire(timeout=remaining):
            self._fail("owner stage lock unavailable")
        try:
            yield
        finally:
            self._owner._mutex.release()

    def _fail(self, message):
        self._owner._refused = True
        raise OwnerStageUnavailable(message)

    def _notice(self, code):
        self._owner._refused = True
        self._warn(code)

    @property
    def capture(self):
        return self._capture_view

    def _write(self, data):
        # This callback is already on the shared worker. Never submit/wait here.
        with self._lock:
            if self._cancelled:
                raise OwnerStageUnavailable("stage cancelled")
        try:
            return self._sink.write(data)
        except BaseException:  # noqa: BLE001 - latch before the supervisor hands off a fatal.
            self._owner._refused = True
            raise

    def _require_controller(self):
        self._io._check_process()
        if threading.current_thread() is self._io._worker:
            self._fail("recursive audit I/O wait")

    def _request(self, phase, *, snapshot=None, usage=None, observation=None):
        self._require_controller()
        target = _Publication(self, phase, snapshot=snapshot, usage=usage, observation=observation)
        with self._lock:
            if self._cancelled or self._busy:
                self._fail("stage unavailable")
            self._busy = True
        ticket = None
        handoff = phase in (AuditPhase.ADOPT, AuditPhase.OBSERVE)
        retained = False
        try:
            ticket = self._io.submit(
                target,
                phase,
                deadline=min(self._deadline, time.monotonic() + self._control_timeout),
                **({"handoff": True} if handoff else {}),
            )
            with self._lock:
                self._tickets.append(ticket)
                cancelled = self._cancelled
            if cancelled:
                self._io.abandon(ticket)
            outcome = self._io.wait(ticket)
            if outcome.error is not None:
                self._owner._refused = True
                raise outcome.error
            with self._lock:
                if self._cancelled or outcome.state != "ack":
                    self._fail("stage publication unavailable")
            published = _Published(ticket, outcome)
            retained = handoff
            return published
        except BaseException as error:
            self._owner._refused = True
            if ticket is not None:
                self._cleanup_request(ticket, error, abandon=True)
            raise
        finally:
            if not retained:
                with self._lock:
                    self._busy = False
                    if ticket in self._tickets:
                        self._tickets.remove(ticket)

    def _cleanup_request(self, ticket, primary=None, *, abandon=False):
        # Cleanup cannot replace the controller's primary failure. The owner
        # helper retains the cleanup exception privately without changing its chain.
        chain = (
            None
            if primary is None
            else (
                BaseException.__cause__.__get__(primary),
                BaseException.__context__.__get__(primary),
                BaseException.__suppress_context__.__get__(primary),
                BaseException.__traceback__.__get__(primary),
            )
        )
        try:
            if abandon:
                self._io.abandon(ticket)
            else:
                self._io.release_handoff(ticket)
        except BaseException as error:
            self._owner._refused = True
            if primary is not None and error is not primary:
                from .invocation_owner import _raise_handoff_primary

                _raise_handoff_primary(primary, chain, error)
            raise

    @contextmanager
    def _handoff_request(self, phase, **kwargs):
        published = self._request(phase, **kwargs)
        primary = None
        try:
            yield published
        except BaseException as error:
            self._owner._refused = True
            primary = error
            raise
        finally:
            try:
                self._cleanup_request(published.ticket, primary)
            finally:
                with self._lock:
                    self._busy = False
                    if published.ticket in self._tickets:
                        self._tickets.remove(published.ticket)

    def seal(self):
        self._require_controller()
        with self._lock:
            if self._cancelled:
                self._fail("stage cancelled")
            if self._checkpoint is not None:
                return self._snapshot
        try:
            snapshot = self._capture.seal()
        except BaseException:  # noqa: BLE001 - capture assembly fatals must also refuse the owner.
            self._owner._refused = True
            raise
        published = self._request(AuditPhase.SEAL, snapshot=snapshot)
        checkpoint = published.outcome.value
        with self._lock:
            if self._cancelled:
                self._fail("stage cancelled")
            self._snapshot, self._checkpoint = snapshot, checkpoint
        return snapshot

    def adopt(self, *, usage=None):
        self._require_controller()
        with self._lock:
            if self._cancelled or self._snapshot is None or self._checkpoint is None:
                self._fail("unsealed stage")
            if self._adopted is not None:
                if self._adopted[0] != usage:
                    self._fail("conflicting adoption")
                return self._adopted[1]
            snapshot = self._snapshot
        with self._handoff_request(AuditPhase.ADOPT, snapshot=snapshot, usage=usage) as published:
            ack = published.outcome.value
            # Publication held no owner lock. Cancel and this credit commit serialize
            # under the stage lock; the bounded owner lock never spans recorder I/O.
            with self._owner_lock(), self._lock:
                record = self._owner._lease(self._lease)
                identity = self._checkpoint.identity
                if (
                    self._cancelled
                    or published.ticket._io is not self._io
                    or published.ticket._outcome is not published.outcome
                    or published.outcome.state != "ack"
                    or time.monotonic() >= published.ticket._deadline
                    or record.settlement_started
                    or record.stages.get(self._layer) is not self
                    or not isinstance(ack, AcquiredAcknowledgement)
                    or ack.publication_uncertain
                    or ack.ref is None
                    or ack.observation_id != identity.observation_id
                    or ack.ref.artifact_id != identity.artifact_id
                    or ack.ref.layer != self._layer
                    or ack.ref.retained_bytes != self._checkpoint.retained_bytes
                    or ack.ref.sha256 != self._checkpoint.sha256
                    or type(ack.admitted) is not bool
                    or ack.admitted != snapshot.complete
                    or ack.ref.partial != (not snapshot.complete)
                    or ack.ref.original_bytes != (snapshot.observed_bytes if snapshot.eof_seen else None)
                    or (ack.admitted and ack.fault is not None)
                ):
                    self._fail("stage adoption unavailable")
                if self._adopted is not None:
                    if self._adopted[0] != usage:
                        self._fail("conflicting adoption")
                    return self._adopted[1]
                try:
                    captures = dict(record.captures)
                    captures[self._layer] = (snapshot.accepted_bytes, snapshot.complete)
                    refs = record.refs + [ack.ref]
                    observations = record.observations | {ack.observation_id}
                    usage_known = record.usage_known or (
                        usage is not None and usage.availability in ("known", "partial")
                    )
                    adopted = (usage, ack)
                    if not ack.admitted:
                        self._owner._refused = True
                        fault = ack.fault or AuditFault(
                            "persistence_error", "acquired", self._lease.attempt_id
                        )
                        if fault not in self._owner._faults:
                            self._owner._faults.append(fault)
                    # All fallible preparation precedes the owner-visible commit.
                    record.captures, record.refs, record.observations = captures, refs, observations
                    record.usage_known, self._adopted = usage_known, adopted
                except BaseException:  # noqa: BLE001 - preserve fatal identity and sticky refusal.
                    self._owner._refused = True
                    raise
            return ack

    def observe(self, *, frame_offset, frame_length, usage=None, observed_backend=None):
        self._require_controller()
        with self._lock:
            if self._cancelled or self._adopted is None:
                self._fail("unadopted observation source")
        with self._handoff_request(
            AuditPhase.OBSERVE,
            usage=usage,
            observation=dict(
                frame_offset=frame_offset, frame_length=frame_length, observed_backend=observed_backend
            ),
        ) as published:
            ack = published.outcome.value
            with self._owner_lock(), self._lock:
                record = self._owner._lease(self._lease)
                if (
                    self._cancelled
                    or time.monotonic() >= published.ticket._deadline
                    or record.settlement_started
                    or published.ticket._io is not self._io
                    or published.ticket._outcome is not published.outcome
                    or self._adopted[1].observation_id not in record.observations
                    or not isinstance(ack, AuditAcknowledgement)
                    or not ack.admitted
                    or ack.publication_uncertain
                ):
                    self._fail("stage observation unavailable")
                record.usage_known |= usage is not None and usage.availability in ("known", "partial")
            return ack

    def cancel(self):
        self._io._check_process()
        with self._lock:
            if self._cancelled or (self._adopted is not None and not self._busy):
                return
            self._cancelled = True
            tickets, sink = tuple(self._tickets), self._sink
        self._owner._refused = True
        for ticket in tickets:
            self._io.abandon(ticket)
        # Recorder cancellation is explicitly a nonblocking signal, not I/O.
        if sink is not None:
            sink.cancel()
        if self._capture is not None:
            # The reviewed capture cancellation marks running tickets unknown
            # and keeps their charge; never drop its queue by hand.
            from .invocation_stream import CaptureUnavailable

            try:
                self._capture.seal(interrupted=True)
            except CaptureUnavailable:
                pass
