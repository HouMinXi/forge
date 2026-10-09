"""Observable owner lifecycle against the real durable recorder."""

import hashlib
import json
from pathlib import Path
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import subprocess
import os
import threading
import asyncio
import signal
import time
import warnings
import sys

import pytest

from code_forge.invocation_audit import (
    AttemptRecorder, AuditAcknowledgement, AuditFault, BackendObservation,
    BODY_BYTES, InvocationContext, UsageObservation,
)
from code_forge.llm_invoke import InvalidJSONResponseError, LLMInvokeError
from code_forge.invocation_owner import InvocationOwner, InvocationSessionSummary


BACKEND = BackendObservation('openai', 'fixture', 'requested', None, None)
DIGEST = hashlib.sha256(b'request').hexdigest()


class Clock:
    value = 10.0

    def __call__(self):
        return self.value


def utc():
    return '2026-10-08T14:40:00Z'


def bound_context(payload=b'diff'):
    return InvocationContext(str(uuid.uuid4()), hashlib.sha256(payload).hexdigest(),
                             str(uuid.uuid4()), None, None, None, None, 'review', None)


def manifest(context, payload=b'diff'):
    return dict(schema_version=1, run_id=context.run_id, snapshot_id=context.snapshot_id,
                mode='diff', legacy_source_hash=context.source_hash,
                exact_diff_sha256=hashlib.sha256(payload).hexdigest(), diff_bytes=len(payload),
                files=[], repositories=[])


@pytest.fixture
def setup_owner():
    root = Path(__file__).resolve().parents[1] / '.planning' / 'owner-fixtures' / str(uuid.uuid4())
    clock = Clock()
    ctx = bound_context()
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock, utc_now=utc)
    assert owner.publish_input(manifest(ctx)).admitted
    return owner, root, ctx, clock


def operation(owner, *, parent=None, cause='initial', maximum=5, purpose='review'):
    ctx = owner.context(round_index=0, pass_name='qodo-review', group_id=None,
                        group_diff_sha256=None, purpose=purpose,
                        parent_logical_id=parent.logical_id if parent else None)
    return owner.operation(ctx, cause=cause, parent=parent, max_attempts=maximum)


def begin(owner, op=None, timeout=600):
    return owner.begin_attempt(op or operation(owner), backend=BACKEND,
                               request_digest=DIGEST, request_bytes=7, timeout_s=timeout)


def finish(lease, **changes):
    kwargs = dict(outcome='completed', error_class=None, duration_s=3.0)
    kwargs.update(changes)
    return lease.finish_once(**kwargs)


def complete(owner, op=None, payload=b'{}', **finish_changes):
    lease = begin(owner, op)
    lease.entered('entered_api_send')
    ack = lease.acquired(layer='wire', data=payload, complete=True)
    assert ack.admitted
    assert finish(lease, **finish_changes).admitted
    return lease, ack


def snapshot(root, ctx):
    return AttemptRecorder.reopen_scoped(root, ctx.run_id)


def usage(value=0):
    return UsageObservation(value, value, 0, 0, 'known', 'total_includes_cache',
                            'total_includes_reasoning', 'final', 'native', 'attempt', {})


def call_result(action):
    """Retain either public return value or raised object for contract assertions."""
    try:
        return action()
    except BaseException as error:  # noqa: BLE001 - assert the exact caller-visible return or exception
        return error


def test_lease_lifecycle_usage_and_immutable_summary(setup_owner):
    owner, root, ctx, clock = setup_owner
    lease = begin(owner)
    assert lease.action_deadline == 610
    lease.entered('entered_api_send')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    assert raw.admitted and raw.observation_id
    assert lease.observe(source_observation_id=raw.observation_id, frame_offset=0, frame_length=2,
                         usage=usage(), observed_backend=replace(BACKEND, observed_model='actual')).admitted
    clock.value = 14
    assert finish(lease).admitted
    result = owner.finalize('completed')
    reopened = snapshot(root, ctx)
    assert result.audit_complete and result.state == 'completed'
    assert result.usage['input_tokens']['total'] == 0
    assert result.timing['work_s'] == 3
    assert result.timing['wall_s'] == 4
    assert result.cost is None
    assert reopened.attempts[0].requested_backend.observed_model is None
    assert reopened.attempts[0].response_observations[0].observed_backend.observed_model == 'actual'
    assert result.run_summaries[0]['run_id'] == ctx.run_id
    with pytest.raises(TypeError):
        result.usage['input_tokens']['total'] = 100
    with pytest.raises(TypeError):
        result.timing['work_s'] = 99
    with pytest.raises(TypeError):
        result.run_summaries[0]['outcome_counts']['completed'] = 99
    with pytest.raises(FrozenInstanceError):
        lease.attempt_id = str(uuid.uuid4())
    with pytest.raises(FrozenInstanceError):
        lease.operation.max_attempts = 500
    with pytest.raises(FrozenInstanceError):
        result.audit_complete = False


@pytest.mark.parametrize('timeout', [300, 600, 2400])
def test_legacy_bounds_preserve_effective_timeout(setup_owner, timeout):
    owner, _, _, clock = setup_owner
    clock.value = 5000
    lease = begin(owner, timeout=timeout)
    assert lease.action_deadline == 5000 + timeout
    assert finish(lease, outcome='cancelled', duration_s=0).admitted
    assert owner.finalize('cancelled').timing['work_s'] is None


@pytest.mark.parametrize('value', [None, 0, -1, True, 1.5, float('nan'), float('inf')])
def test_legacy_bounds_reject_invalid_attempt_limit(setup_owner, value):
    owner, root, ctx, _ = setup_owner
    with pytest.raises((ValueError, TypeError, LLMInvokeError)):
        operation(owner, maximum=value)
    assert snapshot(root, ctx).summary['admitted_attempt_count'] == 0


@pytest.mark.parametrize('value', [None, 0, -1, True, 1.5, float('nan'), float('inf')])
def test_legacy_bounds_reject_invalid_effective_timeout(setup_owner, value):
    owner, root, ctx, _ = setup_owner
    with pytest.raises((ValueError, TypeError, LLMInvokeError)):
        begin(owner, timeout=value)
    assert snapshot(root, ctx).summary['admitted_attempt_count'] == 0


def test_legacy_bounds_retry_and_child_identity(setup_owner):
    owner, root, ctx, _ = setup_owner
    op = operation(owner, maximum=7)
    ids = [complete(owner, op)[0].attempt_id for _ in range(7)]
    child = operation(owner, parent=op, cause='continuation', maximum=2)
    complete(owner, child)
    rows = snapshot(root, ctx).attempts
    assert len({row.logical_id for row in rows[:7]}) == 1
    assert [row.cause for row in rows[:7]] == ['initial'] + ['retry'] * 6
    assert [row.parent_id for row in rows[1:7]] == ids[:-1]
    assert rows[-1].parent_id == ids[-1]
    assert rows[-1].context.parent_logical_id == op.logical_id
    with pytest.raises(LLMInvokeError):
        begin(owner, op)
    assert len(snapshot(root, ctx).attempts) == 8


def test_legacy_bounds_m_plus_seven_is_supplied_shape(setup_owner):
    owner, root, ctx, _ = setup_owner
    parent = operation(owner, maximum=6)
    for _ in range(5):
        complete(owner, parent)
    correction = operation(owner, parent=parent, cause='correction', maximum=1)
    complete(owner, correction)
    continuation = operation(owner, parent=parent, cause='continuation', maximum=2)
    for _ in range(2):
        complete(owner, continuation)
    complete(owner, operation(owner, parent=parent, cause='headroom', maximum=1))
    repair = operation(owner, parent=parent, cause='excerpt_repair', maximum=1)
    complete(owner, repair)
    nested = operation(owner, parent=repair, cause='continuation', maximum=2)
    for _ in range(2):
        complete(owner, nested)
    complete(owner, operation(owner, parent=repair, cause='headroom', maximum=1))
    assert snapshot(root, ctx).summary['observed_api_send_count'] == 6 + 7
    # This witness's correction consumed the sixth primary slot; the caller
    # supplies that policy. It does not request another six primary attempts.


@pytest.mark.parametrize('bad_copy', [False, True])
def test_lease_rejects_cross_owner_or_copied_operation(setup_owner, bad_copy):
    owner, root, ctx, clock = setup_owner
    other = InvocationOwner.create(artifact_root=root.parent / str(uuid.uuid4()),
                                   context=bound_context(), monotonic=clock, utc_now=utc)
    op = operation(owner) if bad_copy else operation(other)
    if bad_copy:
        op = replace(op)
    with pytest.raises(LLMInvokeError):
        begin(owner, op)
    assert not snapshot(root, ctx).attempts


@pytest.mark.parametrize('replay', ['finish', 'publication', 'finalize'])
def test_terminal_replay_requalifies_storage_and_retains_latch(setup_owner, replay):
    owner, root, ctx, _ = setup_owner
    lease, raw = complete(owner)
    if replay == 'finish':
        def replay_call():
            return finish(lease)
    elif replay == 'publication':
        def replay_call():
            return owner.publish_input(manifest(ctx))
    else:
        def replay_call():
            return owner.finalize('completed')
    replay_call()
    journal = root / ctx.run_id / 'journal'
    before = journal.read_bytes()
    replay_call()
    assert journal.read_bytes() == before
    path = root / ctx.run_id / ('raw-' + raw.ref.artifact_id)
    original = path.read_bytes()
    path.write_bytes(b'xx')
    try:
        try:
            result = replay_call()
        except LLMInvokeError as error:
            assert error.kind == 'audit_persistence_error' and not error.retryable
        else:
            assert not getattr(result, 'admitted', getattr(result, 'audit_complete', True))
        with pytest.raises(LLMInvokeError):
            begin(owner)
    finally:
        path.write_bytes(original)
    replay_call()
    assert journal.read_bytes() == before
    assert not owner.finalize('completed').audit_complete


@pytest.mark.parametrize('primary', [ValueError('original'), KeyboardInterrupt(), SystemExit(3),
                                    subprocess.TimeoutExpired('owned', 1),
                                    InvalidJSONResponseError('bad json', raw_response='exact')])
def test_lease_primary_exception_survives_sink_failure(setup_owner, monkeypatch, primary):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    lease.acquired(layer='wire', data=b'{}', complete=True)
    def fail(*args, **kwargs):
        raise OSError('credential-shaped diagnostics must not be copied')
    monkeypatch.setattr(AttemptRecorder, 'finish', fail)
    with pytest.raises(BaseException) as raised:
        finish(lease, outcome='failed', primary_error=primary)
    assert raised.value is primary
    carrier = primary.invocation_observation
    assert carrier.attempt_id == lease.attempt_id and carrier.acquired_bytes == 2
    assert carrier.whole_bytes == 2 and carrier.raw_refs
    assert carrier.secondary_faults
    assert 'credential-shaped' not in repr(carrier)
    if isinstance(primary, InvalidJSONResponseError):
        assert primary.raw_response == 'exact'
    assert snapshot(root, ctx).attempts[0].outcome == 'unknown'


def test_terminal_abandonment_retains_actual_lease(setup_owner):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('possibly_sent')
    result = call_result(lambda: owner.finalize('completed'))
    assert isinstance(result, InvocationSessionSummary)
    assert not result.audit_complete and result.state == 'incomplete'
    assert not (root / ctx.run_id / 'final').exists()
    assert snapshot(root, ctx).attempts[0].outcome == 'unknown'
    with pytest.raises(LLMInvokeError):
        begin(owner)
    assert finish(lease, outcome='cancelled', duration_s=None).admitted
    result = owner.finalize('cancelled')
    assert not result.audit_complete and result.state == 'incomplete'
    assert (root / ctx.run_id / 'final').is_file()


def test_lease_partial_and_sibling_settlement(setup_owner):
    owner, root, ctx, _ = setup_owner
    first, sibling = begin(owner), begin(owner)
    first.entered('entered_api_send')
    sibling.entered('entered_api_send')
    raw = first.acquired(layer='wire', data=b'{}\ntruncated', complete=False)
    assert not raw.admitted and raw.ref and raw.observation_id
    assert first.observe(source_observation_id=raw.observation_id, frame_offset=0, frame_length=2,
                         usage=usage(2), observed_backend=None).admitted
    assert sibling.acquired(layer='wire', data=b'{}', complete=True).admitted
    assert finish(first, outcome='incomplete').admitted
    assert finish(sibling, outcome='failed').admitted
    result = owner.finalize('failed')
    assert not result.audit_complete and result.usage['input_tokens']['known_subtotal'] == 2
    assert result.usage['input_tokens']['total'] is None
    assert len(snapshot(root, ctx).attempts) == 2


def test_lease_cap_plus_one_retains_bounded_bytes(setup_owner):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_cli_launch')
    raw = lease.acquired(layer='stdout', data=b'x' * (BODY_BYTES + 1), complete=False)
    assert not raw.admitted and raw.ref.retained_bytes == BODY_BYTES
    assert (root / ctx.run_id / ('raw-' + raw.ref.artifact_id)).stat().st_size == BODY_BYTES
    with pytest.raises(LLMInvokeError) as caught:
        finish(lease)
    carrier = caught.value.invocation_observation
    assert carrier.acquired_bytes == BODY_BYTES + 1 and carrier.whole_bytes is None


def test_source_epoch_changes_only_after_settlement(setup_owner):
    owner, root, ctx, clock = setup_owner
    op = operation(owner)
    complete(owner, op)
    next_ctx = bound_context(b'new')
    transition = call_result(lambda: owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'new')))
    assert isinstance(transition, AuditAcknowledgement) and transition.admitted
    clock.value = 17
    complete(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and len(result.run_summaries) == 2
    assert {row['run_id'] for row in result.run_summaries} == {ctx.run_id, next_ctx.run_id}
    assert result.timing['known_work_s'] == 6 and result.timing['wall_s'] == 7
    with pytest.raises(LLMInvokeError):
        begin(owner, op)


@pytest.mark.parametrize('fault', ['uuid_only', 'active', 'audit_fault'])
def test_source_epoch_cannot_evade_refusal(setup_owner, fault):
    owner, _, ctx, _ = setup_owner
    if fault == 'active':
        begin(owner)
    elif fault == 'audit_fault':
        assert not owner.publish_input({}).admitted
    next_ctx = replace(ctx, run_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4())) if fault == 'uuid_only' else bound_context(b'new')
    payload = b'diff' if fault == 'uuid_only' else b'new'
    transition = call_result(lambda: owner.advance_source(next_ctx, manifest=manifest(next_ctx, payload)))
    assert isinstance(transition, AuditAcknowledgement) and not transition.admitted
    with pytest.raises(LLMInvokeError):
        begin(owner)


def test_lease_concurrent_distinct_operations_and_replay(setup_owner):
    owner, root, ctx, _ = setup_owner
    ops = [operation(owner, purpose='worker%d' % i) for i in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        leases = list(pool.map(lambda op: complete(owner, op)[0], ops))
        assert all(pool.map(lambda lease: finish(lease).admitted, leases * 3))
    result = owner.finalize('completed')
    assert result.audit_complete and result.timing['known_work_s'] == 15
    assert snapshot(root, ctx).summary['admitted_attempt_count'] == 5


def test_terminal_manifest_is_durable_before_admission():
    root = Path(__file__).resolve().parents[1] / '.planning' / 'owner-fixtures' / str(uuid.uuid4())
    run_id, snapshot_id = str(uuid.uuid4()), str(uuid.uuid4())
    context = InvocationContext(run_id, 'a' * 64, snapshot_id, None, None, None, None, 'review', None)
    manifest = dict(schema_version=1, run_id=run_id, snapshot_id=snapshot_id,
                    mode='diff', legacy_source_hash='a' * 64,
                    exact_diff_sha256=hashlib.sha256(b'diff').hexdigest(),
                    diff_bytes=4, files=[], repositories=[])
    owner = InvocationOwner.create(artifact_root=root, context=context,
                                   monotonic=lambda: 0.0, utc_now=lambda: '2026-10-08T14:40:00Z')
    assert owner.publish_input(manifest).admitted
    published = root / run_id / 'input-manifest.json'
    assert published.is_file(), 'an acknowledgement without durable manifest is not admission'
    assert json.loads(published.read_text()) == manifest


@pytest.mark.parametrize('deadline', [0, 9, 10, 12, 10000])
def test_legacy_bounds_explicit_deadline(setup_owner, deadline):
    _, root, _, clock = setup_owner
    ctx = bound_context()
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock,
                                   utc_now=utc, job_deadline=deadline)
    assert owner.publish_input(manifest(ctx)).admitted
    if deadline <= clock.value:
        with pytest.raises(LLMInvokeError):
            begin(owner)
        assert not snapshot(root, ctx).attempts
    else:
        lease = begin(owner)
        assert lease.action_deadline == min(610, deadline)
        assert finish(lease, outcome='cancelled', duration_s=0).admitted


@pytest.mark.parametrize('deadline', [True, -1, float('nan'), float('inf'), 10 ** 1000])
def test_legacy_bounds_invalid_deadline_has_no_reservation(setup_owner, deadline):
    _, root, _, clock = setup_owner
    ctx = bound_context()
    with pytest.raises(ValueError):
        InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock,
                               utc_now=utc, job_deadline=deadline)
    assert not (root / ctx.run_id).exists()


def test_terminal_empty_session_stays_unmeasured(setup_owner):
    owner, _, _, clock = setup_owner
    clock.value = 13
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'completed'
    assert result.usage['input_tokens']['total'] is None
    assert result.timing['work_s'] is None and result.timing['wall_s'] == 3
    assert not owner.finalize('failed').audit_complete


def test_lease_queued_boundary_refuses_after_sibling_fault(setup_owner):
    owner, root, ctx, _ = setup_owner
    first, queued = begin(owner), begin(owner)
    first.entered('entered_api_send')
    assert not first.acquired(layer='wire', data=b'x', complete=False).admitted
    with pytest.raises(LLMInvokeError):
        queued.entered('entered_api_send')
    assert finish(queued, outcome='cancelled', duration_s=0).admitted
    assert finish(first, outcome='incomplete').admitted
    result = owner.finalize('failed')
    assert not result.audit_complete
    rows = snapshot(root, ctx).attempts
    assert rows[1].dispatch_state == 'not_dispatched'
    assert result.run_summaries[0]['observed_api_send_count'] == 1


@pytest.mark.parametrize('source', ['acquisition', 'native'])
@pytest.mark.parametrize('known', [False, True])
def test_lease_carrier_distinguishes_known_zero_and_unknown_usage(setup_owner, source, known):
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    observation = usage() if known else replace(usage(), input_tokens=None, output_tokens=None,
                                                 cached_input_tokens=None, reasoning_tokens=None,
                                                 availability='unknown')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True,
                         usage=observation if source == 'acquisition' else None)
    assert raw.admitted
    if source == 'native':
        assert lease.observe(source_observation_id=raw.observation_id, frame_offset=0,
                             frame_length=2, usage=observation, observed_backend=None).admitted
    primary = ValueError('model failed')
    with pytest.raises(ValueError) as caught:
        finish(lease, outcome='failed', primary_error=primary)
    assert caught.value is primary
    assert primary.invocation_observation.usage_known is known
    with pytest.raises(FrozenInstanceError):
        primary.invocation_observation.usage_known = not known


@pytest.mark.parametrize('payload', [None, b''])
def test_lease_carrier_empty_and_absent_bytes(setup_owner, payload):
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_cli_launch')
    if payload is not None:
        assert lease.acquired(layer='stdout', data=payload, complete=True).admitted
    primary = ValueError('model failed')
    with pytest.raises(ValueError):
        finish(lease, outcome='failed', primary_error=primary)
    carrier = primary.invocation_observation
    assert carrier.acquired_bytes == (None if payload is None else 0)
    assert carrier.whole_bytes == (None if payload is None else 0)
    assert carrier.acquisition_state == ('unacquired' if payload is None else 'complete')


@pytest.mark.parametrize('problem', ['fixed_context', 'empty_parent', 'unsettled_parent', 'parent_context'])
def test_lease_invalid_context_and_parent_refuse_before_start(setup_owner, problem):
    owner, root, ctx, _ = setup_owner
    parent = operation(owner)
    if problem == 'unsettled_parent':
        begin(owner, parent)
    elif problem == 'parent_context':
        complete(owner, parent)
    before = len(snapshot(root, ctx).attempts)
    with pytest.raises(LLMInvokeError):
        if problem == 'fixed_context':
            owner.operation(replace(ctx, source_hash='b' * 64), cause='initial')
        elif problem == 'parent_context':
            owner.operation(ctx, cause='continuation', parent=parent)
        else:
            operation(owner, parent=parent, cause='continuation')
    assert len(snapshot(root, ctx).attempts) == before


def test_source_epoch_rejects_stale_operation_before_finalization(setup_owner):
    owner, root, ctx, _ = setup_owner
    old = operation(owner)
    complete(owner, old)
    next_ctx = bound_context(b'next')
    assert owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'next')).admitted
    with pytest.raises(LLMInvokeError):
        begin(owner, old)
    assert not snapshot(root, next_ctx).attempts
    assert len(snapshot(root, ctx).attempts) == 1


@pytest.mark.parametrize('problem', ['copy', 'foreign_owner'])
def test_lease_immutable_identity_cannot_be_transplanted(setup_owner, problem):
    owner, root, ctx, clock = setup_owner
    original = begin(owner)
    other = InvocationOwner.create(artifact_root=root.parent / str(uuid.uuid4()),
                                   context=bound_context(), monotonic=clock, utc_now=utc)
    forged = replace(original, _owner=other) if problem == 'foreign_owner' else replace(original)
    with pytest.raises(LLMInvokeError):
        forged.entered('entered_api_send')
    assert snapshot(root, ctx).attempts[0].outcome == 'unknown'


def test_lease_process_identity_refuses_admission(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    actual = os.getpid()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: actual + 1)
        with pytest.raises(LLMInvokeError):
            begin(owner, op)
    assert not snapshot(root, ctx).attempts


@pytest.mark.parametrize('problem', ['before_entered', 'duplicate_layer', 'after_finish', 'invalid_data'])
def test_lease_acquisition_refuses_invalid_sequence(setup_owner, problem):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    if problem != 'before_entered':
        lease.entered('entered_api_send')
    if problem == 'duplicate_layer':
        assert lease.acquired(layer='wire', data=b'{}', complete=True).admitted
    elif problem == 'after_finish':
        finish(lease, outcome='failed')
    before = snapshot(root, ctx).summary['observation_count']
    ack = lease.acquired(layer='wire', data='wrong' if problem == 'invalid_data' else b'xx', complete=True)
    assert not ack.admitted and ack.ref is None and ack.observation_id is None
    assert snapshot(root, ctx).summary['observation_count'] == before


@pytest.mark.parametrize('problem', ['invalid_state', 'contradictory_state', 'after_finish'])
def test_lease_boundary_is_consistent(setup_owner, problem):
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    if problem != 'invalid_state':
        lease.entered('entered_api_send')
    if problem == 'after_finish':
        finish(lease, outcome='failed')
    with pytest.raises(LLMInvokeError):
        lease.entered('invented' if problem == 'invalid_state' else 'entered_cli_launch')


@pytest.mark.parametrize('problem', ['no_reference', 'range', 'empty_native', 'after_finish'])
def test_lease_native_observation_requires_durable_frame(setup_owner, problem):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    if problem == 'after_finish':
        finish(lease)
    ack = lease.observe(source_observation_id=str(uuid.uuid4()) if problem == 'no_reference' else raw.observation_id,
                         frame_offset=1 if problem == 'range' else 0, frame_length=2,
                         usage=None if problem == 'empty_native' else usage(), observed_backend=None)
    assert not ack.admitted and not snapshot(root, ctx).attempts[0].response_observations


def test_lease_sink_refusal_cannot_grant_frame_authority(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    def failure(*args, **kwargs):
        raise OSError('secret')
    monkeypatch.setattr(AttemptRecorder, 'settle_acquired', failure)
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    assert not raw.admitted and raw.ref is None and raw.observation_id is None
    assert not lease.observe(source_observation_id=str(uuid.uuid4()), frame_offset=0, frame_length=2,
                             usage=usage(), observed_backend=None).admitted
    assert not snapshot(root, ctx).attempts[0].raw_refs


def test_terminal_conflict_preserves_primary_identity(setup_owner):
    owner, root, ctx, _ = setup_owner
    lease, _ = complete(owner)
    primary = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught:
        finish(lease, outcome='failed', primary_error=primary)
    assert caught.value is primary
    assert snapshot(root, ctx).attempts[0].outcome == 'completed'
    assert primary.invocation_observation.secondary_faults


@pytest.mark.parametrize('with_primary', [False, True])
def test_terminal_cancellation_from_sink_preserves_entering_exception(setup_owner, monkeypatch, with_primary):
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    interruption = KeyboardInterrupt()
    primary = SystemExit(5) if with_primary else None
    def failure(*args, **kwargs):
        raise interruption
    monkeypatch.setattr(AttemptRecorder, 'finish', failure)
    with pytest.raises(BaseException) as caught:
        finish(lease, outcome='cancelled', primary_error=primary)
    assert caught.value is (primary if with_primary else interruption)
    with pytest.raises(LLMInvokeError):
        begin(owner)


def test_terminal_hostile_exception_metadata_cannot_replace_primary(setup_owner):
    class PrimaryError(Exception):
        def __setattr__(self, name, value):
            raise KeyboardInterrupt()
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    primary = PrimaryError('original')
    with pytest.raises(BaseException) as caught:
        finish(lease, outcome='failed', primary_error=primary)
    assert caught.value is primary


def test_terminal_open_refusal_exposes_safe_unacquired_metadata(setup_owner):
    _, root, ctx, clock = setup_owner
    with pytest.raises(LLMInvokeError) as caught:
        InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock, utc_now=utc)
    carrier = caught.value.invocation_observation
    assert carrier.attempt_id is None and carrier.acquired_bytes is None
    assert carrier.acquisition_state == 'unacquired' and not carrier.raw_refs
    assert caught.value.kind == 'audit_persistence_error' and not caught.value.retryable


@pytest.mark.parametrize('site', ['open', 'publication', 'previous_finalize'])
def test_source_epoch_failed_transition_never_allows_new_send(setup_owner, monkeypatch, site):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    next_ctx = bound_context(b'next')
    body = manifest(next_ctx, b'next')
    if site == 'open':
        (root / next_ctx.run_id).mkdir()
    elif site == 'publication':
        body = {}
    else:
        calls = []
        def refuse(recorder, outcome):
            calls.append(outcome)
            return AuditAcknowledgement(False, AuditFault('persistence_error', 'finalize', None))
        monkeypatch.setattr(AttemptRecorder, 'finalize', refuse)
    assert not owner.advance_source(next_ctx, manifest=body).admitted
    if site == 'previous_finalize':
        assert calls == ['completed']
    with pytest.raises(LLMInvokeError):
        begin(owner)
    assert owner.context(round_index=0, pass_name=None, group_id=None, group_diff_sha256=None,
                         purpose='review', parent_logical_id=None).run_id == ctx.run_id


def test_terminal_finalize_replay_honors_current_recorder_ack(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    assert owner.finalize('completed').audit_complete
    real_finalize = AttemptRecorder.finalize
    calls = []
    def fail_after_public_qualification(recorder, outcome):
        admitted = real_finalize(recorder, outcome)
        assert admitted.admitted
        calls.append(outcome)
        return AuditAcknowledgement(False, AuditFault('persistence_error', 'finalize', None))
    with monkeypatch.context() as patch:
        patch.setattr(AttemptRecorder, 'finalize', fail_after_public_qualification)
        assert not owner.finalize('completed').audit_complete
    assert calls == ['completed']
    assert snapshot(root, ctx).audit_complete
    assert not owner.finalize('completed').audit_complete


def test_lease_new_observations_use_recorder_minted_ids(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    real_capture, real_observe = AttemptRecorder.settle_acquired, AttemptRecorder.observe_response
    calls = []
    def capture(recorder, *args, **kwargs):
        calls.append(('capture', kwargs['observation_id']))
        return real_capture(recorder, *args, **kwargs)
    def observe(recorder, *args, **kwargs):
        calls.append(('observe', kwargs['observation_id']))
        return real_observe(recorder, *args, **kwargs)
    monkeypatch.setattr(AttemptRecorder, 'settle_acquired', capture)
    monkeypatch.setattr(AttemptRecorder, 'observe_response', observe)
    lease = begin(owner)
    lease.entered('entered_api_send')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    for _ in range(2):
        assert lease.observe(source_observation_id=raw.observation_id, frame_offset=0, frame_length=2,
                             usage=usage(), observed_backend=None).admitted
    finish(lease)
    responses = snapshot(root, ctx).attempts[0].response_observations
    assert len(responses) == 2 and responses[0].observation_id != responses[1].observation_id
    assert calls == [('capture', None), ('observe', None), ('observe', None)]


def test_lease_concurrent_same_operation_has_one_admission(setup_owner):
    owner, root, ctx, _ = setup_owner
    op = operation(owner, maximum=1)
    def try_begin(_):
        try:
            return begin(owner, op)
        except LLMInvokeError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        admitted = [lease for lease in pool.map(try_begin, range(4)) if lease is not None]
    assert len(admitted) == 1
    assert len(snapshot(root, ctx).attempts) == 1
    assert finish(admitted[0], outcome='cancelled', duration_s=0).admitted
    assert not owner.finalize('cancelled').audit_complete


def test_terminal_successful_retry_resolves_only_its_own_operation(setup_owner):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    complete(owner, op, outcome='failed', error_class='ValueError')
    complete(owner, op)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'completed'
    assert result.run_summaries[0]['state'] == 'completed'
    assert snapshot(root, ctx).summary['outcome_counts']['failed'] == 1
    assert snapshot(root, ctx).summary['outcome_counts']['completed'] == 1


@pytest.mark.parametrize('failed_outcome, expected', [('failed', 'failed'), ('refused', 'failed'),
                                                    ('cancelled', 'cancelled'), ('incomplete', 'incomplete')])
def test_terminal_independent_unresolved_operation_remains_visible(setup_owner, failed_outcome, expected):
    owner, root, ctx, _ = setup_owner
    complete(owner, outcome=failed_outcome)
    complete(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == expected
    assert result.run_summaries[0]['state'] == expected
    assert snapshot(root, ctx).summary['outcome_counts'][failed_outcome] == 1
    assert owner.finalize('completed').state == expected


def test_source_epoch_failure_survives_changed_source_success(setup_owner):
    owner, root, ctx, _ = setup_owner
    complete(owner, outcome='failed')
    next_ctx = bound_context(b'new')
    assert owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'new')).admitted
    assert snapshot(root, ctx).state == 'failed'
    complete(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'failed'
    assert [row['state'] for row in result.run_summaries] == ['failed', 'completed']


@pytest.mark.parametrize('other, expected', [('cancelled', 'failed'), ('incomplete', 'incomplete')])
def test_terminal_unresolved_outcome_precedence(setup_owner, other, expected):
    owner, _, _, _ = setup_owner
    complete(owner, outcome='failed')
    complete(owner, outcome=other)
    assert owner.finalize('completed').state == expected


def test_terminal_unused_operation_has_no_measured_work(setup_owner):
    owner, _, _, _ = setup_owner
    operation(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'completed'
    assert result.timing['work_s'] is None
    assert result.run_summaries[0]['logical_call_count'] == 0


@pytest.mark.parametrize('outcome', ['refused', 'invented', None])
def test_terminal_invalid_caller_outcome_is_not_normalized_to_success(setup_owner, outcome):
    owner, _, _, _ = setup_owner
    complete(owner)
    result = owner.finalize(outcome)
    assert not result.audit_complete and result.state == 'incomplete'


def test_lease_retry_waits_for_previous_settlement(setup_owner):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    first = begin(owner, op)
    with pytest.raises(LLMInvokeError):
        begin(owner, op)
    assert len(snapshot(root, ctx).attempts) == 1
    assert finish(first, outcome='cancelled', duration_s=0).admitted


def test_lease_audit_latch_stays_incomplete_after_storage_recovers(setup_owner):
    owner, root, ctx, _ = setup_owner
    lease, raw = complete(owner)
    path = root / ctx.run_id / ('raw-' + raw.ref.artifact_id)
    original = path.read_bytes()
    path.write_bytes(b'xx')
    with pytest.raises(LLMInvokeError):
        finish(lease)
    path.write_bytes(original)
    assert finish(lease).admitted
    assert not owner.finalize('completed').audit_complete


def test_lease_native_usage_from_equal_bytes_remains_distinct_attempts(setup_owner):
    owner, root, ctx, _ = setup_owner
    op = operation(owner)
    ids = []
    for _ in range(2):
        lease = begin(owner, op)
        lease.entered('entered_api_send')
        raw = lease.acquired(layer='wire', data=b'{}', complete=True, usage=usage(3))
        ids.append(raw.observation_id)
        finish(lease)
    result = owner.finalize('completed')
    assert len(set(ids)) == 2 and result.usage['input_tokens']['total'] == 6
    assert snapshot(root, ctx).summary['observation_count'] == 2


def test_lease_unentered_acquisition_never_calls_recorder(setup_owner, monkeypatch):
    owner, _, _, _ = setup_owner
    lease = begin(owner)
    original = AttemptRecorder.settle_acquired
    calls = []
    def settle(recorder, *args, **kwargs):
        calls.append(kwargs['dispatch_state'])
        return original(recorder, *args, **kwargs)
    monkeypatch.setattr(AttemptRecorder, 'settle_acquired', settle)
    assert not lease.acquired(layer='wire', data=b'{}', complete=True).admitted
    assert calls == []


def test_lease_untrusted_raw_stays_exact_private_and_out_of_failure_metadata(setup_owner):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    payload = b'"token=private-canary"; $(touch /untrusted-target)\x00\xff'
    raw = lease.acquired(layer='wire', data=payload, complete=True)
    path = root / ctx.run_id / ('raw-' + raw.ref.artifact_id)
    assert path.read_bytes() == payload and path.stat().st_mode & 0o777 == 0o600
    primary = ValueError('model rejected')
    with pytest.raises(BaseException) as caught:
        finish(lease, outcome='failed', primary_error=primary)
    assert caught.value is primary
    assert 'private-canary' not in repr(primary.invocation_observation)
    result = owner.finalize('failed')
    assert result.audit_complete and 'private-canary' not in repr(result)


def test_lease_entered_sibling_can_settle_concurrently_after_fault(setup_owner):
    owner, root, ctx, _ = setup_owner
    first, sibling = begin(owner), begin(owner)
    first.entered('entered_api_send')
    sibling.entered('entered_api_send')
    ready = threading.Event()
    def fail_one():
        assert not first.acquired(layer='wire', data=b'x', complete=False).admitted
        ready.set()
        return finish(first, outcome='incomplete').admitted
    def settle_other():
        assert ready.wait(5)
        assert sibling.acquired(layer='wire', data=b'{}', complete=True).admitted
        return finish(sibling, outcome='failed').admitted
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result, second_result = pool.submit(fail_one), pool.submit(settle_other)
        assert first_result.result(10) and second_result.result(10)
    result = owner.finalize('failed')
    assert not result.audit_complete and result.run_summaries[0]['observed_api_send_count'] == 2
    assert [row.outcome for row in snapshot(root, ctx).attempts] == ['incomplete', 'failed']


PROCESS_ENTRIES = ('publish_input', 'context', 'operation', 'begin_attempt',
                   'entered', 'acquired', 'observe', 'finish_once',
                   'advance_source', 'finalize')


class BlockingClock:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.armed = False

    def __call__(self):
        if self.armed and threading.current_thread().name == 'owner-clock-holder':
            self.entered.set()
            assert self.release.wait(10), 'clock worker was not released'
        return 10.0


def process_actions(owner, ctx, primary=None):
    lease = begin(owner)
    lease.entered('entered_api_send')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    assert raw.admitted
    pending = operation(owner)
    next_ctx = bound_context(b'next')
    actions = {
        'publish_input': lambda: owner.publish_input(manifest(ctx)),
        'context': lambda: owner.context(round_index=0, pass_name=None, group_id=None,
                                         group_diff_sha256=None, purpose='review', parent_logical_id=None),
        'operation': lambda: owner.operation(ctx, cause='initial'),
        'begin_attempt': lambda: begin(owner, pending),
        'entered': lambda: lease.entered('entered_api_send'),
        'acquired': lambda: lease.acquired(layer='text', data=b'{}', complete=True),
        'observe': lambda: lease.observe(source_observation_id=raw.observation_id, frame_offset=0,
                                         frame_length=2, usage=usage(), observed_backend=None),
        'finish_once': lambda: finish(lease, outcome='failed', primary_error=primary),
        'advance_source': lambda: owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'next')),
        'finalize': lambda: owner.finalize('completed'),
    }
    return actions, lease, raw


def refusal_details(result, primary):
    carrier = getattr(result, 'invocation_observation', None)
    return dict(type=type(result).__name__, is_primary=result is primary,
                admitted=getattr(result, 'admitted', None), kind=getattr(result, 'kind', None),
                retryable=getattr(result, 'retryable', None),
                fault=getattr(getattr(result, 'fault', None), 'code', None),
                codes=[fault.code for fault in carrier.secondary_faults] if carrier else [],
                attempt_id=carrier.attempt_id if carrier else None,
                acquired_bytes=carrier.acquired_bytes if carrier else None,
                raw_refs=len(carrier.raw_refs) if carrier else 0,
                raw_response=getattr(result, 'raw_response', None))


def assert_process_refusal(details, entry, primary, lease):
    if entry in ('publish_input', 'advance_source'):
        assert details['type'] == 'AuditAcknowledgement'
        assert details['admitted'] is False and details['fault'] == 'identity_conflict'
    else:
        if primary is None:
            assert details['type'] == 'LLMInvokeError'
            assert details['kind'] == 'audit_persistence_error' and details['retryable'] is False
        else:
            assert details['is_primary']
        if not isinstance(primary, HostilePrimary):
            assert 'identity_conflict' in details['codes']
            if entry in ('entered', 'acquired', 'observe', 'finish_once'):
                assert details['attempt_id'] == lease.attempt_id
                assert details['acquired_bytes'] == 2 and details['raw_refs'] == 1
        if isinstance(primary, InvalidJSONResponseError):
            assert details['raw_response'] == 'exact'


class HostilePrimary(BaseException):
    def __setattr__(self, name, value):
        raise KeyboardInterrupt()


PRIMARY_FACTORIES = (lambda: ValueError('original'), KeyboardInterrupt, lambda: SystemExit(3),
                     lambda: subprocess.TimeoutExpired('owned', 1), asyncio.CancelledError,
                     lambda: InvalidJSONResponseError('bad json', raw_response='exact'), HostilePrimary)


def fork_refusal_case(tmp_path, entry, locked, primary=None):
    clock = BlockingClock()
    ctx = bound_context()
    root = tmp_path / 'audit'
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock, utc_now=utc)
    assert owner.publish_input(manifest(ctx)).admitted
    actions, lease, _ = process_actions(owner, ctx, primary)
    worker_op = operation(owner)
    worker_results = []
    worker = threading.Thread(target=lambda: worker_results.append(call_result(lambda: begin(owner, worker_op))),
                              name='owner-clock-holder')
    clock.armed = locked
    if locked:
        worker.start()
    result_path = tmp_path / 'child.json'
    child = None
    status = None
    killed = False
    reaped = False
    try:
        if locked:
            assert clock.entered.wait(5), 'worker did not reach the public call clock'
        before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=DeprecationWarning, message='This process.*')
            child = os.fork()
        if child == 0:
            signal.signal(signal.SIGALRM, lambda _signum, _frame: os._exit(124))
            signal.alarm(3)
            code = 1
            try:
                results = [refusal_details(call_result(actions[entry]), primary) for _ in range(2)]
                result_path.write_text(json.dumps(results))
                code = 0
            finally:
                os._exit(code)
        (tmp_path / 'child-owner.json').write_text(json.dumps(dict(pid=child, parent_pid=os.getpid())) + '\n')
        limit = time.monotonic() + 4
        while time.monotonic() < limit:
            waited, status = os.waitpid(child, os.WNOHANG)
            if waited == child:
                reaped = True
                break
            time.sleep(0.01)
        else:
            os.kill(child, signal.SIGKILL)
            _, status = os.waitpid(child, 0)
            killed = True
            reaped = True
        after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    finally:
        if child is not None and child > 0 and not reaped:
            os.kill(child, signal.SIGKILL)
            _, status = os.waitpid(child, 0)
        clock.release.set()
        if locked:
            worker.join(10)
    record = dict(entry=entry, locked=locked, pid=child, exit_code=os.waitstatus_to_exitcode(status),
                  killed=killed, reaped=True, absent=not Path(f'/proc/{child}').exists(),
                  worker_joined=not worker.is_alive(), parent_files_unchanged=before == after)
    (tmp_path / 'lifecycle.json').write_text(json.dumps(record, indent=2) + '\n')
    assert not worker.is_alive()
    if locked:
        assert len(worker_results) == 1 and not isinstance(worker_results[0], BaseException)
        assert finish(worker_results[0], outcome='cancelled').admitted
    assert finish(lease, outcome='cancelled').admitted
    assert owner.finalize('cancelled').audit_complete
    assert record['parent_files_unchanged'] and record['absent']
    assert record['exit_code'] == 0 and not killed, record
    results = json.loads(result_path.read_text())
    assert len(results) == 2
    for result in results:
        assert_process_refusal(result, entry, primary, lease)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires POSIX fork')
@pytest.mark.parametrize('entry', PROCESS_ENTRIES)
@pytest.mark.parametrize('locked', [False, True], ids=['unlocked', 'locked'])
def test_foreign_process_public_entries_refuse_before_lock(tmp_path, entry, locked):
    fork_refusal_case(tmp_path, entry, locked)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires POSIX fork')
@pytest.mark.parametrize('factory', PRIMARY_FACTORIES)
def test_foreign_process_finish_preserves_primary(tmp_path, factory):
    fork_refusal_case(tmp_path, 'finish_once', True, factory())


@pytest.mark.parametrize('entry', PROCESS_ENTRIES)
def test_foreign_process_refusal_metadata_in_current_process(setup_owner, monkeypatch, entry):
    owner, root, ctx, _ = setup_owner
    actions, lease, _ = process_actions(owner, ctx)
    actual = os.getpid()
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: actual + 1)
        result = call_result(actions[entry])
    assert_process_refusal(refusal_details(result, None), entry, None, lease)
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert before == after


@pytest.mark.parametrize('factory', PRIMARY_FACTORIES)
def test_foreign_process_copied_lease_preserves_primary_without_borrowed_refs(setup_owner, monkeypatch, factory):
    owner, _, _, _ = setup_owner
    lease, _ = complete(owner)
    copied = replace(lease)
    primary = factory()
    actual = os.getpid()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: actual + 1)
        result = call_result(lambda: finish(copied, outcome='failed', primary_error=primary))
    assert result is primary
    if not isinstance(primary, HostilePrimary):
        observation = primary.invocation_observation
        assert observation.attempt_id is None and not observation.raw_refs
        assert observation.acquired_bytes is None and not observation.usage_known
        assert 'identity_conflict' in [fault.code for fault in observation.secondary_faults]


def test_owner_module_origins_are_the_current_source_tree():
    repo = Path(__file__).resolve().parents[1]
    origins = {}
    for name in ('invocation_owner', 'invocation_audit', 'llm_invoke'):
        module_path = Path(sys.modules['code_forge.' + name].__file__).resolve()
        assert module_path == repo / 'src' / 'code_forge' / (name + '.py')
        origins[name] = dict(path=str(module_path), sha256=hashlib.sha256(module_path.read_bytes()).hexdigest())
    if os.environ.get('OWNER_RUN_OUTPUT'):
        (Path(os.environ['OWNER_RUN_OUTPUT']) / 'module-origins.json').write_text(json.dumps(origins, indent=2) + '\n')


@pytest.mark.parametrize('factory', PRIMARY_FACTORIES)
@pytest.mark.parametrize('foreign_owner', [False, True], ids=['copied', 'other-owner'])
def test_terminal_invalid_lease_preserves_entering_primary(setup_owner, factory, foreign_owner):
    owner, root, ctx, clock = setup_owner
    lease, _ = complete(owner)
    other = InvocationOwner.create(artifact_root=root.parent / str(uuid.uuid4()),
                                   context=bound_context(), monotonic=clock, utc_now=utc)
    invalid = replace(lease, _owner=other) if foreign_owner else replace(lease)
    primary = factory()
    before = (root / ctx.run_id / 'journal').read_bytes()
    result = call_result(lambda: finish(invalid, outcome='failed', primary_error=primary))
    assert result is primary
    assert (root / ctx.run_id / 'journal').read_bytes() == before
    if not isinstance(primary, HostilePrimary):
        assert primary.invocation_observation.attempt_id is None
        assert not primary.invocation_observation.raw_refs
        assert 'identity_conflict' in [fault.code for fault in primary.invocation_observation.secondary_faults]


def mode_manifest(ctx, mode, *, empty=False):
    body = manifest(ctx, b'' if empty else b'fixed')
    body['mode'] = mode
    if mode != 'diff':
        body.update(exact_diff_sha256=None, diff_bytes=None)
    if mode == 'files' and not empty:
        body['files'] = [dict(path='a.py', sha256=hashlib.sha256(b'fixed').hexdigest(), bytes=5)]
    if mode == 'joint' and not empty:
        body['repositories'] = [dict(label='repo-a', revision='rev-a', sha256=hashlib.sha256(b'fixed').hexdigest(), bytes=5)]
    return body


def epoch_owner(tmp_path, mode, *, empty=False, outcome='completed'):
    ctx = replace(bound_context(), source_hash='a' * 64)
    clock = Clock()
    root = tmp_path / 'audit'
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=clock, utc_now=utc)
    body = mode_manifest(ctx, mode, empty=empty)
    assert owner.publish_input(body).admitted
    lease, _ = complete(owner, outcome=outcome)
    return owner, root, ctx, body, lease


def next_manifest(ctx, body, *, changed_alias=False):
    candidate = replace(ctx, run_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4()),
                        source_hash='b' * 64 if changed_alias else ctx.source_hash)
    copied = json.loads(json.dumps(body))
    copied.update(run_id=candidate.run_id, snapshot_id=candidate.snapshot_id,
                  legacy_source_hash=candidate.source_hash)
    return candidate, copied


@pytest.mark.parametrize('mode', ['diff', 'files', 'joint'])
@pytest.mark.parametrize('empty', [False, True], ids=['nonempty', 'empty'])
@pytest.mark.parametrize('changed_alias', [False, True], ids=['same-legacy', 'new-legacy'])
@pytest.mark.parametrize('old_outcome', ['completed', 'failed'])
def test_source_identity_aliases_do_not_authorize_epoch(tmp_path, mode, empty, changed_alias, old_outcome):
    owner, root, ctx, body, lease = epoch_owner(tmp_path, mode, empty=empty, outcome=old_outcome)
    candidate, unchanged = next_manifest(ctx, body, changed_alias=changed_alias)
    before = (root / ctx.run_id / 'journal').read_bytes()
    denied = owner.advance_source(candidate, manifest=unchanged)
    assert not denied.admitted and denied.fault.code == 'identity_conflict'
    assert (root / ctx.run_id / 'journal').read_bytes() == before
    assert snapshot(root, ctx).summary['outcome_counts'][old_outcome] == 1
    assert snapshot(root, candidate).summary['admitted_attempt_count'] == 0
    assert (root / candidate.run_id / 'admission').is_file()
    assert (root / candidate.run_id / 'input-manifest.json').is_file()
    with pytest.raises(LLMInvokeError) as caught:
        begin(owner, lease.operation)
    assert not caught.value.retryable
    third, changed = next_manifest(ctx, body)
    changed.update(mode='diff', exact_diff_sha256=hashlib.sha256(b'new').hexdigest(),
                   diff_bytes=3, files=[], repositories=[])
    assert not owner.advance_source(third, manifest=changed).admitted
    assert not (root / third.run_id).exists()
    result = owner.finalize('completed')
    assert result.state == 'incomplete' and not result.audit_complete
    assert len(result.run_summaries) == 2
    assert result.run_summaries[0]['outcome_counts'][old_outcome] == 1
    assert result.run_summaries[1]['admitted_attempt_count'] == 0


EXACT_FIELD_CASES = (
    ('diff', 'exact_diff_sha256'), ('diff', 'diff_bytes'),
    ('files', 'path'), ('files', 'sha256'), ('files', 'bytes'),
    ('files', 'add'), ('files', 'remove'),
    ('joint', 'label'), ('joint', 'revision'), ('joint', 'sha256'), ('joint', 'bytes'),
    ('joint', 'add'), ('joint', 'remove'),
    ('files', 'mode'), ('joint', 'mode'),
)


def change_exact_field(body, mode, field):
    if field == 'mode':
        body['mode'] = 'joint' if mode == 'files' else 'files'
    elif mode == 'diff':
        body[field] = hashlib.sha256(b'other').hexdigest() if field == 'exact_diff_sha256' else 6
    else:
        rows = body['files' if mode == 'files' else 'repositories']
        if field == 'remove':
            rows.clear()
        elif field == 'add':
            row = dict(rows[0])
            row['path' if mode == 'files' else 'label'] = 'z.py' if mode == 'files' else 'repo-z'
            rows.append(row)
        else:
            rows[0][field] = (6 if field == 'bytes' else hashlib.sha256(b'other').hexdigest()
                              if field == 'sha256' else 'b.py' if field == 'path' else 'new-binding')


@pytest.mark.parametrize('mode,field', EXACT_FIELD_CASES)
@pytest.mark.parametrize('changed_alias', [False, True], ids=['same-legacy', 'new-legacy'])
def test_source_identity_each_exact_field_authorizes_change(tmp_path, mode, field, changed_alias):
    owner, root, ctx, body, _ = epoch_owner(tmp_path, mode, empty=field == 'mode')
    candidate, changed = next_manifest(ctx, body, changed_alias=changed_alias)
    change_exact_field(changed, mode, field)
    assert owner.advance_source(candidate, manifest=changed).admitted
    assert snapshot(root, ctx).state == 'completed'
    complete(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'completed'
    assert [row['admitted_attempt_count'] for row in result.run_summaries] == [1, 1]
    assert [row['run_id'] for row in result.run_summaries] == [ctx.run_id, candidate.run_id]
    assert json.loads((root / ctx.run_id / 'input-manifest.json').read_text()) == body
    assert json.loads((root / candidate.run_id / 'input-manifest.json').read_text()) == changed


@pytest.mark.parametrize('mode', ['diff', 'files', 'joint'])
def test_source_identity_changed_exact_input_preserves_failed_history(tmp_path, mode):
    owner, root, ctx, body, _ = epoch_owner(tmp_path, mode, outcome='failed')
    candidate, changed = next_manifest(ctx, body)
    change_exact_field(changed, mode, 'diff_bytes' if mode == 'diff' else 'bytes')
    assert owner.advance_source(candidate, manifest=changed).admitted
    complete(owner)
    result = owner.finalize('completed')
    assert result.audit_complete and result.state == 'failed'
    assert [row['state'] for row in result.run_summaries] == ['failed', 'completed']
    assert snapshot(root, ctx).summary['outcome_counts']['failed'] == 1


@pytest.mark.parametrize('mode', ['diff', 'files', 'joint'])
def test_source_identity_legacy_context_consistency_stays_required(tmp_path, mode):
    owner, root, ctx, body, _ = epoch_owner(tmp_path, mode)
    candidate, changed = next_manifest(ctx, body, changed_alias=True)
    change_exact_field(changed, mode, 'diff_bytes' if mode == 'diff' else 'bytes')
    changed['legacy_source_hash'] = ctx.source_hash
    denied = owner.advance_source(candidate, manifest=changed)
    assert not denied.admitted
    assert not (root / candidate.run_id / 'input-manifest.json').exists()
    assert snapshot(root, candidate).summary['admitted_attempt_count'] == 0
    third, valid = next_manifest(ctx, body)
    change_exact_field(valid, mode, 'diff_bytes' if mode == 'diff' else 'bytes')
    assert not owner.advance_source(third, manifest=valid).admitted
    assert not (root / third.run_id).exists()
    assert not owner.finalize('completed').audit_complete


@pytest.mark.parametrize('mode', ['diff', 'files', 'joint'])
def test_source_identity_exact_change_cannot_bypass_root_reservation(tmp_path, mode):
    owner, root, ctx, body, _ = epoch_owner(tmp_path, mode)
    for _ in range(31):
        other = AttemptRecorder(root, context=bound_context(), monotonic=Clock(), utc_now=utc)
        assert other.open().admitted
    candidate, changed = next_manifest(ctx, body)
    change_exact_field(changed, mode, 'diff_bytes' if mode == 'diff' else 'bytes')
    denied = owner.advance_source(candidate, manifest=changed)
    assert not denied.admitted and denied.fault.code == 'quota_refused'
    assert not (root / candidate.run_id / 'admission').exists()
    third, valid = next_manifest(ctx, body, changed_alias=True)
    change_exact_field(valid, mode, 'diff_bytes' if mode == 'diff' else 'bytes')
    assert not owner.advance_source(third, manifest=valid).admitted
    assert not (root / third.run_id).exists()
    assert snapshot(root, ctx).summary['admitted_attempt_count'] == 1
    assert not owner.finalize('completed').audit_complete


class TerminalString(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise KeyboardInterrupt('unsafe terminal comparison')


class TerminalInteger(int):
    pass


class TerminalFloat(float):
    pass


INVALID_TERMINALS = [
    ('outcome', None), ('outcome', False), ('outcome', 0), ('outcome', 0.0),
    ('outcome', ''), ('outcome', 'unknown'), ('outcome', TerminalString('completed')),
    ('error_class', False), ('error_class', 0), ('error_class', 0.0),
    ('error_class', ''), ('error_class', 'https://private.invalid'),
    ('error_class', TerminalString('Error')),
    ('duration_s', False), ('duration_s', True), ('duration_s', -1),
    ('duration_s', float('inf')), ('duration_s', float('-inf')),
    ('duration_s', float('nan')), ('duration_s', '0'),
    ('duration_s', TerminalInteger(0)), ('duration_s', TerminalFloat(0.0)),
]


@pytest.mark.parametrize(('field', 'value'), INVALID_TERMINALS)
@pytest.mark.parametrize('replay', [False, True])
@pytest.mark.parametrize('with_primary', [False, True])
def test_terminal_current_invalid_arguments_reach_public_validator(
        setup_owner, monkeypatch, field, value, replay, with_primary):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    args = dict(outcome='completed', error_class='Error', duration_s=0.0)
    if replay:
        assert lease.finish_once(**args).admitted
    before = (root / ctx.run_id / 'journal').read_bytes()
    real_finish = AttemptRecorder.finish
    observed = []
    def public_finish(recorder, **kwargs):
        observed.append(kwargs)
        return real_finish(recorder, **kwargs)
    monkeypatch.setattr(AttemptRecorder, 'finish', public_finish)
    args[field] = value
    primary = ValueError('entering primary') if with_primary else None
    result = call_result(lambda: lease.finish_once(**args, primary_error=primary))
    assert len(observed) == 1 and observed[0][field] is value
    assert result is primary if with_primary else isinstance(result, LLMInvokeError)
    assert 'invalid_input' in [fault.code for fault in result.invocation_observation.secondary_faults]
    assert result.invocation_observation.attempt_id == lease.attempt_id
    assert (root / ctx.run_id / 'journal').read_bytes().startswith(before)
    assert snapshot(root, ctx).attempts[0].outcome == ('completed' if replay else 'unknown')
    assert not owner.finalize('completed').audit_complete


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'refused', 'cancelled', 'incomplete'])
@pytest.mark.parametrize('duration', [None, 0, 0.0, 3, 3.0])
@pytest.mark.parametrize('error_class', [None, 'Error'])
@pytest.mark.parametrize('with_primary', [False, True])
def test_terminal_valid_current_arguments_and_numeric_replay(
        setup_owner, outcome, duration, error_class, with_primary):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    primary = ValueError('entering primary') if with_primary else None
    args = dict(outcome=outcome, duration_s=duration, error_class=error_class, primary_error=primary)
    first = call_result(lambda: lease.finish_once(**args))
    assert first is primary if with_primary else first.admitted
    before = (root / ctx.run_id / 'journal').read_bytes()
    if duration is not None:
        args['duration_s'] = float(duration) if type(duration) is int else int(duration)
    replay = call_result(lambda: lease.finish_once(**args))
    assert replay is primary if with_primary else replay.admitted
    assert (root / ctx.run_id / 'journal').read_bytes() == before
    assert snapshot(root, ctx).attempts[0].outcome == outcome
    assert owner.finalize('failed' if outcome == 'refused' else outcome).audit_complete


@pytest.mark.parametrize(('field', 'value'), [('outcome', 'failed'), ('error_class', 'Different'), ('duration_s', 1)])
@pytest.mark.parametrize('with_primary', [False, True])
def test_terminal_valid_conflict_keeps_durable_event(setup_owner, field, value, with_primary):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    args = dict(outcome='completed', error_class=None, duration_s=0)
    assert lease.finish_once(**args).admitted
    before = (root / ctx.run_id / 'journal').read_bytes()
    args[field] = value
    primary = ValueError('entering primary') if with_primary else None
    result = call_result(lambda: lease.finish_once(**args, primary_error=primary))
    assert result is primary if with_primary else isinstance(result, LLMInvokeError)
    assert 'identity_conflict' in [fault.code for fault in result.invocation_observation.secondary_faults]
    assert (root / ctx.run_id / 'journal').read_bytes().startswith(before)
    assert snapshot(root, ctx).attempts[0].outcome == 'completed'
    assert not owner.finalize('completed').audit_complete


@pytest.mark.parametrize('failure', ['invalid', 'before_write', 'lost_ack'])
@pytest.mark.parametrize('phase', ['entered', 'acquired', 'observe'])
def test_terminal_denied_first_finish_keeps_phase_closed(setup_owner, monkeypatch, failure, phase):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    lease.entered('entered_api_send')
    raw = lease.acquired(layer='wire', data=b'{}', complete=True)
    assert raw.admitted
    real_finish = AttemptRecorder.finish
    def denied(recorder, **kwargs):
        if failure == 'lost_ack':
            assert real_finish(recorder, **kwargs).admitted
        return AuditAcknowledgement(False, AuditFault('persistence_error', 'finish', lease.attempt_id))
    with monkeypatch.context() as patch:
        if failure != 'invalid':
            patch.setattr(AttemptRecorder, 'finish', denied)
        result = call_result(lambda: finish(lease, outcome='failed', duration_s=False if failure == 'invalid' else 0))
    assert isinstance(result, LLMInvokeError)
    assert snapshot(root, ctx).attempts[0].outcome == ('failed' if failure == 'lost_ack' else 'unknown')
    before = tree_bytes(root)
    actions = {
        'entered': lambda: lease.entered('entered_api_send'),
        'acquired': lambda: lease.acquired(layer='stdout', data=b'{}', complete=True),
        'observe': lambda: lease.observe(source_observation_id=raw.observation_id, frame_offset=0,
                                         frame_length=2, usage=usage(), observed_backend=None),
    }
    denied_phase = call_result(actions[phase])
    assert isinstance(denied_phase, LLMInvokeError) if phase == 'entered' else not denied_phase.admitted
    assert tree_bytes(root) == before
    different = call_result(lambda: finish(lease, outcome='cancelled', duration_s=1))
    if failure == 'lost_ack':
        assert isinstance(different, LLMInvokeError)
        assert snapshot(root, ctx).attempts[0].outcome == 'failed'
        assert finish(lease, outcome='failed', duration_s=0).admitted
    else:
        assert different.admitted
        assert snapshot(root, ctx).attempts[0].outcome == 'cancelled'
    assert not owner.finalize('failed').audit_complete


@pytest.mark.parametrize('factory', (*PRIMARY_FACTORIES, lambda: None))
def test_terminal_contested_lock_interrupt_preserves_primary_without_rewaiting(tmp_path, factory):
    clock = BlockingClock()
    ctx = bound_context()
    owner = InvocationOwner.create(artifact_root=tmp_path, context=ctx, monotonic=clock, utc_now=utc)
    assert owner.publish_input(manifest(ctx)).admitted
    lease = begin(owner)
    lease.entered('entered_api_send')
    assert lease.acquired(layer='wire', data=b'{}', complete=True).admitted
    worker_op = operation(owner)
    results = []
    clock.armed = True
    worker = threading.Thread(target=lambda: results.append(call_result(lambda: begin(owner, worker_op))),
                              name='owner-clock-holder')
    worker.start()
    primary = factory()
    secondary = KeyboardInterrupt('private secondary diagnostic')
    frames = []
    def interrupt(signum, frame):
        frames.append((frame, frame.f_lineno))
        raise secondary
    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        assert clock.entered.wait(2)
        signal.setitimer(signal.ITIMER_REAL, 0.03)
        started = time.monotonic()
        result = call_result(lambda: finish(lease, outcome='failed', primary_error=primary))
        elapsed = time.monotonic() - started
        assert elapsed < 1 and not clock.release.is_set() and worker.is_alive()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        clock.release.set()
        worker.join(2)
    assert not worker.is_alive() and len(results) == 1
    assert not isinstance(results[0], BaseException)
    assert result is (primary if primary is not None else secondary)
    assert len(frames) == 1
    interrupted, line = frames[0]
    owner_file = Path(sys.modules['code_forge.invocation_owner'].__file__)
    assert interrupted.f_code.co_filename == str(owner_file)
    assert owner_file.read_text().splitlines()[line - 1].strip() == 'with self._mutex:'
    assert interrupted.f_locals['self'] is owner
    assert interrupted.f_locals['lease'] is lease
    assert interrupted.f_locals['primary'] is primary
    trace = result.__traceback__
    interrupted_count = 0
    while trace is not None:
        interrupted_count += trace.tb_frame is interrupted
        trace = trace.tb_next
    assert interrupted_count == 1
    if primary is not None and not isinstance(primary, HostilePrimary):
        observation = primary.invocation_observation
        assert observation.attempt_id is None and observation.raw_refs == ()
        assert 'persistence_error' in [fault.code for fault in observation.secondary_faults]
        assert 'private secondary' not in repr(observation)
    assert finish(results[0], outcome='cancelled').admitted
    assert finish(lease, outcome='failed').admitted
    assert not owner.finalize('failed').audit_complete


@pytest.mark.parametrize('site', ['process_check', 'observation'])
@pytest.mark.parametrize('factory', PRIMARY_FACTORIES)
def test_terminal_boundary_interruptions_never_replace_primary(setup_owner, monkeypatch, site, factory):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    primary = factory()
    secondary = KeyboardInterrupt('private secondary diagnostic')
    def interrupted(*args, **kwargs):
        raise secondary
    with monkeypatch.context() as patch:
        patch.setattr(owner, '_check_process' if site == 'process_check' else '_observation', interrupted)
        result = call_result(lambda: finish(lease, outcome='failed', primary_error=primary))
    assert result is primary
    if site == 'process_check':
        assert snapshot(root, ctx).attempts[0].outcome == 'unknown'
        assert not owner.finalize('failed').audit_complete
    else:
        assert snapshot(root, ctx).attempts[0].outcome == 'failed'
        assert not owner.finalize('failed').audit_complete


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('failure', ['invalid', 'before_write', 'lost_ack'])
def test_terminal_first_admitted_completion_after_denial_still_raises(setup_owner, monkeypatch, failure):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    real_finish = AttemptRecorder.finish
    def denied(recorder, **kwargs):
        if failure == 'lost_ack':
            assert real_finish(recorder, **kwargs).admitted
        return AuditAcknowledgement(False, AuditFault('persistence_error', 'finish', lease.attempt_id))
    with monkeypatch.context() as patch:
        if failure != 'invalid':
            patch.setattr(AttemptRecorder, 'finish', denied)
        first = call_result(lambda: finish(lease, duration_s=False if failure == 'invalid' else 0))
    assert isinstance(first, LLMInvokeError)
    recovered = call_result(lambda: finish(lease, duration_s=0))
    assert isinstance(recovered, LLMInvokeError)
    assert snapshot(root, ctx).attempts[0].outcome == 'completed'
    assert finish(lease, duration_s=0).admitted
    assert not owner.finalize('completed').audit_complete


class FinalizeString(str):
    pass


class FinalizeTrap(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise KeyboardInterrupt('private equality diagnostic')

    def __ne__(self, other):
        raise KeyboardInterrupt('private inequality diagnostic')


@pytest.mark.parametrize('value', [FinalizeString('completed'), FinalizeTrap('completed'),
                                  None, False, 0, 0.0, float('nan'), b'completed', [], {}, object()])
@pytest.mark.parametrize('replay', [False, True])
def test_finalize_malformed_current_value_refuses_before_comparison_and_cache(setup_owner, monkeypatch, value, replay):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    if replay:
        assert owner.finalize('completed').audit_complete
    before = tree_bytes(root)
    real_finalize = AttemptRecorder.finalize
    calls = []
    def current(recorder, outcome):
        calls.append(outcome)
        return real_finalize(recorder, outcome)
    monkeypatch.setattr(AttemptRecorder, 'finalize', current)
    result = call_result(lambda: owner.finalize(value))
    assert isinstance(result, InvocationSessionSummary)
    assert not result.audit_complete and result.state == 'incomplete' and type(result.state) is str
    assert 'invalid_input' in result.fault_codes
    assert calls == []
    assert tree_bytes(root) == before
    assert snapshot(root, ctx).state == ('completed' if replay else 'active')
    later = owner.finalize('completed')
    assert not later.audit_complete and later.state == 'incomplete'
    assert calls == ['completed' if replay else 'incomplete']


@pytest.mark.parametrize('value', ['', 'unknown', 'refused', 'Completed'])
@pytest.mark.parametrize('replay', [False, True])
def test_finalize_unknown_builtin_values_keep_public_refusal_or_identity_conflict(setup_owner, monkeypatch, value, replay):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    if replay:
        assert owner.finalize('completed').audit_complete
    before = (root / ctx.run_id / 'journal').read_bytes()
    real_finalize = AttemptRecorder.finalize
    calls = []
    def current(recorder, outcome):
        calls.append(outcome)
        return real_finalize(recorder, outcome)
    monkeypatch.setattr(AttemptRecorder, 'finalize', current)
    result = owner.finalize(value)
    assert not result.audit_complete and result.state == 'incomplete'
    assert ('identity_conflict' if replay else 'invalid_input') in result.fault_codes
    assert calls == ([] if replay else [value])
    assert (root / ctx.run_id / 'journal').read_bytes().startswith(before)
    assert not owner.finalize('completed').audit_complete


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled', 'incomplete'])
def test_finalize_builtin_replay_requalifies_current_storage(setup_owner, monkeypatch, outcome):
    owner, root, ctx, _ = setup_owner
    _, raw = complete(owner)
    first = owner.finalize(outcome)
    assert first.audit_complete and first.state == outcome and type(first.state) is str
    before = tree_bytes(root)
    real_finalize = AttemptRecorder.finalize
    calls = []
    def current(recorder, outcome):
        calls.append(outcome)
        return real_finalize(recorder, outcome)
    monkeypatch.setattr(AttemptRecorder, 'finalize', current)
    replay = owner.finalize(outcome)
    assert replay.audit_complete and replay.state == outcome and type(replay.state) is str
    assert calls == [outcome] and tree_bytes(root) == before
    path = root / ctx.run_id / ('raw-' + raw.ref.artifact_id)
    original = path.read_bytes()
    path.write_bytes(b'xx')
    assert not owner.finalize(outcome).audit_complete
    path.write_bytes(original)
    assert not owner.finalize(outcome).audit_complete
    assert calls == [outcome, outcome, outcome]
    assert snapshot(root, ctx).state == outcome


def test_finalize_mixed_epoch_replay_keeps_each_effective_outcome(setup_owner, monkeypatch):
    owner, root, ctx, _ = setup_owner
    complete(owner, outcome='failed')
    next_ctx = bound_context(b'new diff')
    assert owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'new diff')).admitted
    complete(owner)
    real_finalize = AttemptRecorder.finalize
    calls = []
    def current(recorder, outcome):
        calls.append(outcome)
        return real_finalize(recorder, outcome)
    monkeypatch.setattr(AttemptRecorder, 'finalize', current)
    for _ in range(2):
        result = owner.finalize('completed')
        assert result.audit_complete and result.state == 'failed'
    assert calls == ['failed', 'completed', 'failed', 'completed']
    before = tree_bytes(root)
    refused = call_result(lambda: owner.finalize(FinalizeTrap('completed')))
    assert isinstance(refused, InvocationSessionSummary)
    assert not refused.audit_complete and refused.state == 'incomplete'
    assert tree_bytes(root) == before and len(calls) == 4
    assert not owner.finalize('completed').audit_complete
    assert calls[-2:] == ['failed', 'completed']
    assert snapshot(root, ctx).state == 'failed'
    assert snapshot(root, next_ctx).state == 'completed'


@pytest.mark.parametrize('factory', [KeyboardInterrupt, lambda: SystemExit(3), asyncio.CancelledError])
@pytest.mark.parametrize('after_public', [False, True])
@pytest.mark.parametrize('replay', [False, True])
def test_finalize_public_validation_interrupt_is_sticky_and_exact(
        setup_owner, monkeypatch, factory, after_public, replay):
    owner, root, ctx, _ = setup_owner
    complete(owner)
    if replay:
        assert owner.finalize('completed').audit_complete
    real_finalize = AttemptRecorder.finalize
    interruption = factory()
    calls = []
    def interrupted(recorder, outcome):
        calls.append(outcome)
        if after_public:
            assert real_finalize(recorder, outcome).admitted
        raise interruption
    with monkeypatch.context() as patch:
        patch.setattr(AttemptRecorder, 'finalize', interrupted)
        result = call_result(lambda: owner.finalize('completed'))
    assert result is interruption and calls == ['completed']
    assert snapshot(root, ctx).state == ('completed' if replay or after_public else 'active')
    later = owner.finalize('completed')
    assert not later.audit_complete and later.state == 'incomplete'
    assert 'persistence_error' in later.fault_codes
    assert snapshot(root, ctx).state == 'completed'


def test_finalize_process_guard_precedes_malformed_argument(setup_owner, monkeypatch):
    owner, _, _, _ = setup_owner
    complete(owner)
    pid = os.getpid()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'getpid', lambda: pid + 1)
        result = call_result(lambda: owner.finalize(FinalizeTrap('completed')))
    assert isinstance(result, LLMInvokeError)
    faults = result.invocation_observation.secondary_faults
    assert any(fault.code == 'identity_conflict' and fault.phase == 'finalize' for fault in faults)
    assert not any(fault.code == 'invalid_input' for fault in faults)


REQUIRED_CALL_SITES = (
    ('create_open', 'open', 'open'),
    ('publish_input', 'publish_input_manifest', 'start'),
    ('start', 'start', 'start'),
    ('acquired', 'settle_acquired', 'acquired'),
    ('observe', 'observe_response', 'acquired'),
    ('finish', 'finish', 'finish'),
    ('advance_open', 'open', 'open'),
    ('advance_publish', 'publish_input_manifest', 'start'),
    ('advance_finalize', 'finalize', 'finalize'),
    ('finalize', 'finalize', 'finalize'),
)


@pytest.mark.parametrize('site,method,phase', REQUIRED_CALL_SITES)
@pytest.mark.parametrize('after_public', [False, True])
@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit, asyncio.CancelledError, OSError])
def test_required_recorder_call_interruption_is_sticky_exact_and_safe(
        setup_owner, monkeypatch, site, method, phase, after_public, error_type):
    owner, root, ctx, clock = setup_owner
    lease = None
    primary = None
    if site in ('acquired', 'observe', 'finish'):
        lease = begin(owner)
        lease.entered('entered_api_send')
        if site in ('observe', 'finish'):
            raw = lease.acquired(layer='wire', data=b'{}', complete=True)
            assert raw.admitted
    if site.startswith('advance_') or site == 'finalize':
        complete(owner)
    next_op = operation(owner)
    next_ctx = bound_context(b'next source')
    create_root = root.parent / str(uuid.uuid4())
    actions = {
        'create_open': lambda: InvocationOwner.create(artifact_root=create_root, context=next_ctx,
                                                     monotonic=clock, utc_now=utc),
        'publish_input': lambda: owner.publish_input(manifest(ctx)),
        'start': lambda: begin(owner, next_op),
        'acquired': lambda: lease.acquired(layer='wire', data=b'{}', complete=True),
        'observe': lambda: lease.observe(source_observation_id=raw.observation_id, frame_offset=0,
                                        frame_length=2, usage=usage(), observed_backend=None),
        'finish': lambda: finish(lease, outcome='failed', primary_error=primary),
        'finalize': lambda: owner.finalize('completed'),
    }
    for name in ('advance_open', 'advance_publish', 'advance_finalize'):
        actions[name] = lambda: owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'next source'))
    real_method = getattr(AttemptRecorder, method)
    interruption = error_type('private required operation diagnostic')
    calls = []
    def interrupted(recorder, **kwargs):
        calls.append(dict(kwargs))
        if after_public:
            assert real_method(recorder, **kwargs).admitted
        raise interruption
    with monkeypatch.context() as patch:
        patch.setattr(AttemptRecorder, method, interrupted)
        result = call_result(actions[site])
    assert len(calls) == 1
    if error_type is OSError:
        assert result is not interruption
        if site == 'finalize':
            assert not result.audit_complete
        else:
            assert isinstance(result, LLMInvokeError) or not result.admitted
    else:
        assert result is interruption
    if site == 'create_open':
        assert not isinstance(result, InvocationOwner)
        if after_public:
            assert snapshot(create_root, next_ctx).state == 'active'
        return
    # A real next admission tests the owner's failure, not a private fault list.
    before_count = len(snapshot(root, ctx).attempts)
    blocked = call_result(lambda: begin(owner, next_op))
    assert isinstance(blocked, LLMInvokeError) and not blocked.retryable
    expected_id = lease.attempt_id if site in ('acquired', 'observe', 'finish') else None
    faults = blocked.invocation_observation.secondary_faults
    assert AuditFault('persistence_error', phase, expected_id) in faults
    assert 'private required operation diagnostic' not in repr(blocked.invocation_observation)
    assert len(snapshot(root, ctx).attempts) == before_count
    if lease is not None:
        assert finish(lease, outcome='failed').admitted
    final = owner.finalize('completed')
    assert not final.audit_complete and final.state == 'incomplete'
    assert 'persistence_error' in final.fault_codes
    assert not owner.finalize('completed').audit_complete
    refused_ctx = bound_context(b'one more source')
    assert not owner.advance_source(refused_ctx, manifest=manifest(refused_ctx, b'one more source')).admitted
    assert owner.context(round_index=None, pass_name=None, group_id=None, group_diff_sha256=None,
                         purpose='review', parent_logical_id=None).run_id == ctx.run_id


@pytest.mark.parametrize('after_public', [False, True])
@pytest.mark.parametrize('factory', PRIMARY_FACTORIES)
def test_required_finish_interruption_keeps_supplied_primary(setup_owner, monkeypatch, after_public, factory):
    owner, root, ctx, _ = setup_owner
    lease = begin(owner)
    primary = factory()
    secondary = KeyboardInterrupt('private shared boundary diagnostic')
    real_finish = AttemptRecorder.finish
    def interrupted(recorder, **kwargs):
        if after_public:
            assert real_finish(recorder, **kwargs).admitted
        raise secondary
    with monkeypatch.context() as patch:
        patch.setattr(AttemptRecorder, 'finish', interrupted)
        result = call_result(lambda: finish(lease, outcome='failed', primary_error=primary))
    assert result is primary
    if not isinstance(primary, HostilePrimary):
        observation = primary.invocation_observation
        assert observation.attempt_id == lease.attempt_id
        assert AuditFault('persistence_error', 'finish', lease.attempt_id) in observation.secondary_faults
        assert 'private shared boundary diagnostic' not in repr(observation)
    assert snapshot(root, ctx).attempts[0].outcome == ('failed' if after_public else 'unknown')
    assert finish(lease, outcome='failed').admitted
    assert not owner.finalize('failed').audit_complete


@pytest.mark.parametrize('callback_number', [2, 3, 4])
@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit, asyncio.CancelledError, OSError])
def test_required_source_transition_real_utc_interruption_blocks_next_admission(
        tmp_path, callback_number, error_type):
    class InterruptClock:
        armed = False
        count = 0
        error = error_type('private utc diagnostic')
        def __call__(self):
            if self.armed:
                self.count += 1
                if self.count == callback_number:
                    raise self.error
            return utc()
    clock = InterruptClock()
    ctx = bound_context()
    root = tmp_path / 'audit'
    owner = InvocationOwner.create(artifact_root=root, context=ctx, monotonic=Clock(), utc_now=clock)
    assert owner.publish_input(manifest(ctx)).admitted
    complete(owner)
    op = operation(owner)
    next_ctx = bound_context(b'changed source')
    clock.armed = True
    result = call_result(lambda: owner.advance_source(next_ctx, manifest=manifest(next_ctx, b'changed source')))
    clock.armed = False
    if error_type is OSError:
        assert not result.admitted
    else:
        assert result is clock.error
    assert clock.count == callback_number + int(error_type is OSError and callback_number != 2)
    blocked = call_result(lambda: begin(owner, op))
    assert isinstance(blocked, LLMInvokeError)
    phase = {2: 'open', 3: 'start', 4: 'finalize'}[callback_number]
    assert AuditFault('persistence_error', phase, None) in blocked.invocation_observation.secondary_faults
    final = owner.finalize('completed')
    assert not final.audit_complete and final.state == 'incomplete'
    assert 'persistence_error' in final.fault_codes
    assert len(snapshot(root, ctx).attempts) == 1
    assert len(final.run_summaries) == 2
