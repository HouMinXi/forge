"""One host session owns durable attempts across immutable source epochs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import os
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Callable, Mapping
import uuid

from .invocation_audit import (
    AcquiredAcknowledgement, AttemptRecorder, AuditAcknowledgement, AuditFault,
    BackendObservation, InvocationContext, RawRef, UsageObservation,
)


@dataclass(frozen=True)
class InvocationFailureObservation:
    attempt_id: str | None
    raw_refs: tuple[RawRef, ...]
    acquisition_state: str
    acquired_bytes: int | None
    whole_bytes: int | None
    usage_known: bool
    secondary_faults: tuple[AuditFault, ...]


@dataclass(frozen=True)
class InvocationSessionSummary:
    schema_version: int
    session_id: str
    state: str
    audit_complete: bool
    run_summaries: tuple[Mapping, ...]
    usage: Mapping
    timing: Mapping
    cost: None
    fault_codes: tuple[str, ...]


@dataclass(frozen=True)
class InvocationOperation:
    context: InvocationContext
    logical_id: str
    parent_id: str | None
    cause: str
    max_attempts: int
    source_epoch: str


@dataclass(frozen=True)
class AttemptLease:
    attempt_id: str
    operation: InvocationOperation
    action_deadline: float
    _owner: InvocationOwner = field(repr=False, compare=False)

    def entered(self, dispatch_state: str) -> None:
        self._owner._entered(self, dispatch_state)

    def acquired(self, *, layer: str, data: bytes, complete: bool,
                 usage: UsageObservation | None = None) -> AcquiredAcknowledgement:
        return self._owner._acquired(self, layer, data, complete, usage)

    def observe(self, *, source_observation_id: str, frame_offset: int,
                frame_length: int, usage: UsageObservation | None,
                observed_backend: BackendObservation | None) -> AuditAcknowledgement:
        return self._owner._observe(self, source_observation_id, frame_offset,
                                    frame_length, usage, observed_backend)

    def finish_once(self, *, outcome: str, error_class: str | None,
                    duration_s: float | None,
                    primary_error: BaseException | None = None) -> AuditAcknowledgement:
        return self._owner._finish(self, outcome, error_class, duration_s, primary_error)


@dataclass
class _Operation:
    value: InvocationOperation
    leases: list = field(default_factory=list)


@dataclass
class _Lease:
    value: AttemptLease
    recorder: AttemptRecorder
    dispatch: str = 'not_dispatched'
    terminal: dict | None = None
    settlement_started: bool = False
    settled: bool = False
    captures: dict = field(default_factory=dict)
    refs: list = field(default_factory=list)
    observations: set = field(default_factory=set)
    usage_known: bool = False


@dataclass
class _Epoch:
    recorder: AttemptRecorder
    context: InvocationContext
    manifest: Mapping | None = None
    terminal: str | None = None


def _number(value, *, integer=False):
    valid = type(value) is int if integer else type(value) in (int, float)
    if not valid:
        raise ValueError('expected a builtin number')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if value < 0 or not finite:
        raise ValueError('expected a finite nonnegative number')
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _input_identity(manifest):
    return {key: manifest[key] for key in (
        'mode', 'exact_diff_sha256', 'diff_bytes', 'files', 'repositories',
    )}


def _outcome(requested, outcomes):
    order = ('incomplete', 'failed', 'cancelled', 'completed')
    if type(requested) is not str or requested not in order:
        return requested
    observed = {'failed' if value == 'refused' else value for value in outcomes}
    observed.add(requested)
    return next(value for value in order if value in observed)


class InvocationOwner:
    @classmethod
    def create(cls, *, artifact_root: Path, context: InvocationContext,
               monotonic: Callable[[], float], utc_now: Callable[[], str],
               job_deadline: float | None = None) -> InvocationOwner:
        if job_deadline is not None:
            _number(job_deadline)
        self = cls()
        self._mutex = threading.RLock()
        self._pid = os.getpid()
        self._root = artifact_root
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._deadline = job_deadline
        self._start = _number(monotonic())
        self._end = None
        self._session_id = str(uuid.uuid4())
        self._faults = []
        self._operations = {}
        self._leases = {}
        self._final_outcome = None
        recorder = AttemptRecorder(artifact_root, context=context, monotonic=monotonic, utc_now=utc_now)
        self._epochs = [_Epoch(recorder, context)]
        self._current = self._epochs[0]
        ack = self._call(recorder.open, 'open')
        if not ack.admitted:
            self._raise()
        return self

    def _fault(self, code, phase, attempt_id=None):
        fault = AuditFault(code, phase, attempt_id)
        if fault not in self._faults:
            self._faults.append(fault)
        return AuditAcknowledgement(False, fault)

    def _call(self, function, phase, **kwargs):
        attempt_id = kwargs.get('attempt_id')
        try:
            ack = function(**kwargs)
        except Exception:  # noqa: BLE001 - sink diagnostics are not safe invocation metadata
            ack = AuditAcknowledgement(False, AuditFault('persistence_error', phase, attempt_id))
        except BaseException:  # noqa: BLE001 - interruption must retain a required receipt failure
            self._fault('persistence_error', phase, attempt_id)
            raise
        if not ack.admitted:
            fault = ack.fault or AuditFault('persistence_error', phase, attempt_id)
            self._fault(fault.code, fault.phase, fault.attempt_id)
        return ack

    def _observation(self, record=None):
        captures = list(record.captures.values()) if record else []
        acquired = sum(size for size, _ in captures) if captures else None
        complete = bool(captures) and all(done for _, done in captures)
        return InvocationFailureObservation(
            record.value.attempt_id if record else None,
            tuple(record.refs) if record else (),
            'complete' if complete else 'partial' if captures else 'unacquired',
            acquired, acquired if complete else None,
            record.usage_known if record else False, tuple(self._faults),
        )

    def _raise(self, record=None, primary=None):
        if primary is not None:
            try:
                primary.invocation_observation = self._observation(record)
            except BaseException:  # noqa: BLE001 - metadata failure must retain the entering failure
                try:
                    self._fault('persistence_error', 'finish', record.value.attempt_id if record else None)
                finally:
                    raise primary from None
            raise primary
        from .llm_invoke import LLMInvokeError

        error = LLMInvokeError('invocation audit is incomplete',
                               kind='audit_persistence_error', retryable=False)
        error.invocation_observation = self._observation(record)
        raise error

    def _check_process(self, phase, lease=None, primary=None, *, acknowledge=False):
        if os.getpid() == self._pid:
            return None
        # A fork can inherit a mutex held by a thread absent from the child.
        record = self._leases.get(id(lease))
        ack = self._fault('identity_conflict', phase, record.value.attempt_id if record else None)
        if acknowledge:
            return ack
        self._raise(record, primary)

    def _live(self):
        if self._final_outcome is not None:
            self._fault('finalized', 'start')
        if self._faults:
            self._raise()

    def _operation(self, operation):
        registered = self._operations.get(id(operation))
        if registered is None or registered.value is not operation:
            self._fault('identity_conflict', 'start')
            self._raise()
        if operation.source_epoch != self._current.context.run_id:
            self._fault('identity_conflict', 'start')
            self._raise()
        return registered

    def _lease(self, lease):
        record = self._leases.get(id(lease))
        if record is None or record.value is not lease:
            self._fault('identity_conflict', 'acquired')
            self._raise()
        return record

    def publish_input(self, manifest: dict) -> AuditAcknowledgement:
        if (denied := self._check_process('start', acknowledge=True)) is not None:
            return denied
        with self._mutex:
            ack = self._call(self._current.recorder.publish_input_manifest, 'start', manifest=manifest)
            if ack.admitted:
                self._current.manifest = self._current.recorder.scoped_snapshot().input_manifest
            return ack

    def context(self, *, round_index: int | None, pass_name: str | None,
                group_id: str | None, group_diff_sha256: str | None,
                purpose: str, parent_logical_id: str | None) -> InvocationContext:
        self._check_process('start')
        with self._mutex:
            return replace(self._current.context, round_index=round_index, pass_name=pass_name,
                           group_id=group_id, group_diff_sha256=group_diff_sha256,
                           purpose=purpose, parent_logical_id=parent_logical_id)

    def operation(self, context: InvocationContext, *, cause: str,
                  parent: InvocationOperation | None = None,
                  max_attempts: int = 5) -> InvocationOperation:
        self._check_process('start')
        with self._mutex:
            self._live()
            if _number(max_attempts, integer=True) == 0:
                raise ValueError('max_attempts must be positive')
            fixed = self._current.context
            if type(context) is not InvocationContext or any(
                getattr(context, key) != getattr(fixed, key)
                for key in ('run_id', 'source_hash', 'snapshot_id')
            ):
                self._fault('identity_conflict', 'start')
                self._raise()
            parent_id = None
            if parent is not None:
                prior = self._operation(parent)
                if not prior.leases or not prior.leases[-1].settled:
                    self._fault('identity_conflict', 'start')
                    self._raise()
                parent_id = prior.leases[-1].value.attempt_id
            if context.parent_logical_id != (parent.logical_id if parent else None):
                self._fault('identity_conflict', 'start')
                self._raise()
            result = InvocationOperation(context, str(uuid.uuid4()), parent_id, cause,
                                         max_attempts, fixed.run_id)
            self._operations[id(result)] = _Operation(result)
            return result

    def begin_attempt(self, operation: InvocationOperation, *, backend: BackendObservation,
                      request_digest: str, request_bytes: int,
                      timeout_s: int) -> AttemptLease:
        self._check_process('start')
        with self._mutex:
            self._live()
            state = self._operation(operation)
            if _number(timeout_s, integer=True) == 0:
                raise ValueError('timeout_s must be positive')
            if len(state.leases) >= operation.max_attempts or (state.leases and not state.leases[-1].settled):
                self._fault('quota_refused', 'start')
                self._raise()
            deadline = _number(self._monotonic()) + timeout_s
            _number(deadline)
            if self._deadline is not None:
                deadline = min(deadline, self._deadline)
            prior = state.leases[-1].value.attempt_id if state.leases else operation.parent_id
            ack = self._call(
                self._current.recorder.start, 'start', context=operation.context,
                logical_id=operation.logical_id, parent_id=prior,
                cause='retry' if state.leases else operation.cause, backend=backend,
                request_digest=request_digest, request_bytes=request_bytes, action_deadline=deadline,
            )
            if not ack.admitted:
                self._raise()
            lease = AttemptLease(ack.attempt_id, operation, deadline, self)
            record = _Lease(lease, self._current.recorder)
            state.leases.append(record)
            self._leases[id(lease)] = record
            return lease

    def _entered(self, lease, dispatch):
        self._check_process('acquired', lease)
        with self._mutex:
            record = self._lease(lease)
            if (type(dispatch) is not str or dispatch not in
                    ('entered_api_send', 'entered_cli_launch', 'possibly_sent', 'unknown')
                    or record.settlement_started
                    or (self._faults and record.dispatch == 'not_dispatched')
                    or (record.dispatch in ('entered_api_send', 'entered_cli_launch')
                        and dispatch != record.dispatch)):
                self._fault('identity_conflict', 'acquired', lease.attempt_id)
                self._raise(record)
            record.dispatch = dispatch

    def _acquired(self, lease, layer, data, complete, usage):
        self._check_process('acquired', lease)
        with self._mutex:
            record = self._lease(lease)
            if (record.settlement_started or record.dispatch == 'not_dispatched'
                    or type(layer) is not str or layer in record.captures
                    or type(data) is not bytes or type(complete) is not bool):
                denied = self._fault('identity_conflict', 'acquired', lease.attempt_id)
                return AcquiredAcknowledgement(False, None, None, denied.fault)
            record.captures[layer] = (len(data), complete)
            ack = self._call(record.recorder.settle_acquired, 'acquired', attempt_id=lease.attempt_id,
                             layer=layer, data=data, complete=complete, usage=usage,
                             dispatch_state=record.dispatch, observation_id=None)
            if isinstance(ack, AcquiredAcknowledgement):
                if ack.ref is not None and ack.observation_id is not None:
                    record.refs.append(ack.ref)
                    record.observations.add(ack.observation_id)
                    record.usage_known |= usage is not None and usage.availability in ('known', 'partial')
                return ack
            return AcquiredAcknowledgement(False, None, None, ack.fault)

    def _observe(self, lease, source_id, offset, length, usage, backend):
        self._check_process('acquired', lease)
        with self._mutex:
            record = self._lease(lease)
            if record.settlement_started or type(source_id) is not str or source_id not in record.observations:
                return self._fault('identity_conflict', 'acquired', lease.attempt_id)
            ack = self._call(record.recorder.observe_response, 'acquired',
                             source_observation_id=source_id, frame_offset=offset,
                             frame_length=length, usage=usage, observed_backend=backend,
                             observation_id=None, **{'attempt_id': lease.attempt_id})
            if ack.admitted:
                record.usage_known |= usage is not None and usage.availability in ('known', 'partial')
            return ack

    def _finish(self, lease, outcome, error_class, duration, primary):
        record = None
        try:
            self._check_process('finish', lease, primary)
            with self._mutex:
                record = self._lease(lease)
                record.settlement_started = True
                args = dict(attempt_id=lease.attempt_id, outcome=outcome, error_class=error_class,
                            duration_s=duration, dispatch_state=record.dispatch)
                was_settled = record.settled
                # The recorder validates every current request and owns durable replay.
                ack = self._call(record.recorder.finish, 'finish', **args)
                if ack.admitted:
                    record.terminal = args
                    record.settled = True
                if (primary is not None or not ack.admitted or
                        (self._faults and outcome == 'completed' and not was_settled)):
                    self._raise(record, primary)
                return ack
        except BaseException as error:
            if error is primary:
                raise
            # An interrupted mutex wait must not enter a second recovery wait.
            self._fault('persistence_error', 'finish', record.value.attempt_id if record else None)
            if primary is None:
                raise
            self._raise(record, primary)

    def advance_source(self, context: InvocationContext, *, manifest: dict) -> AuditAcknowledgement:
        if (denied := self._check_process('start', acknowledge=True)) is not None:
            return denied
        with self._mutex:
            if (self._faults or self._final_outcome is not None
                    or any(not record.settled for record in self._leases.values())
                    or type(context) is not InvocationContext or context.source_hash is None
                    or any(context.run_id == epoch.context.run_id or
                           context.snapshot_id == epoch.context.snapshot_id for epoch in self._epochs)):
                return self._fault('identity_conflict', 'start')
            recorder = AttemptRecorder(self._root, context=context, monotonic=self._monotonic,
                                       utc_now=self._utc_now)
            candidate = _Epoch(recorder, context)
            self._epochs.append(candidate)
            ack = self._call(recorder.open, 'open')
            if not ack.admitted:
                return ack
            ack = self._call(recorder.publish_input_manifest, 'start', manifest=manifest)
            if not ack.admitted:
                return ack
            candidate.manifest = recorder.scoped_snapshot().input_manifest
            if (self._current.manifest is None or
                    _input_identity(candidate.manifest) == _input_identity(self._current.manifest)):
                return self._fault('identity_conflict', 'start')
            terminal = self._epoch_outcome(self._current, 'completed')
            ack = self._call(self._current.recorder.finalize, 'finalize', outcome=terminal)
            if not ack.admitted:
                return ack
            self._current.terminal = terminal
            self._current = candidate
            return ack

    def _epoch_outcome(self, epoch, requested):
        latest = [operation.leases[-1].terminal['outcome']
                  for operation in self._operations.values()
                  if operation.value.source_epoch == epoch.context.run_id and operation.leases]
        return _outcome(requested, latest)

    def finalize(self, outcome: str) -> InvocationSessionSummary:
        self._check_process('finalize')
        with self._mutex:
            if type(outcome) is not str:
                self._fault('invalid_input', 'finalize')
                return self._summary()
            if any(not record.settled for record in self._leases.values()):
                self._fault('identity_conflict', 'finalize')
                return self._summary()
            if self._final_outcome is not None and self._final_outcome != outcome:
                self._fault('identity_conflict', 'finalize')
                return self._summary()
            self._final_outcome = outcome
            for epoch in self._epochs:
                if epoch.terminal is None:
                    epoch.terminal = 'incomplete' if self._faults else self._epoch_outcome(epoch, outcome)
                self._call(epoch.recorder.finalize, 'finalize', outcome=epoch.terminal)
            if self._end is None:
                self._end = _number(self._monotonic())
            return self._summary()

    def _summary(self):
        snapshots = [AttemptRecorder.reopen_scoped(self._root, epoch.context.run_id) for epoch in self._epochs]
        for snapshot in snapshots:
            for fault in snapshot.faults:
                self._fault(fault.code, fault.phase, fault.attempt_id)
            if not snapshot.audit_complete:
                self._fault('persistence_error', 'replay')
        rows = tuple(snapshot.summary for snapshot in snapshots)
        measured = sum(row['observed_api_send_count'] + row['observed_cli_launch_count'] +
                       row['unknown_dispatch_count'] for row in rows)
        usage = {}
        for name in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens'):
            combined = {key: sum(row['usage'][name][key] for row in rows)
                        for key in ('known_subtotal', 'unknown_attempt_count', 'invalid_attempt_count')}
            combined['total'] = (combined['known_subtotal'] if measured and
                                 not combined['unknown_attempt_count'] and not combined['invalid_attempt_count']
                                 else None)
            usage[name] = combined
        known_work = sum(row['timing']['known_work_s'] for row in rows)
        unknown_work = sum(row['timing']['unknown_duration_count'] for row in rows)
        timing = dict(known_work_s=known_work, unknown_duration_count=unknown_work,
                      work_s=known_work if measured and not unknown_work else None,
                      wall_s=self._end - self._start if self._end is not None else None)
        complete = not self._faults
        state = (_outcome(self._final_outcome, (row['state'] for row in rows))
                 if self._final_outcome is not None else 'active')
        return InvocationSessionSummary(1, self._session_id,
                                        state if complete else 'incomplete',
                                        complete, _freeze(rows), _freeze(usage), _freeze(timing), None,
                                        tuple(dict.fromkeys(fault.code for fault in self._faults)))
