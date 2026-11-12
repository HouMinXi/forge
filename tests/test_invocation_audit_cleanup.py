"""Owning-site cleanup must not replace an already entering exception.

Pinned here: the lock unlock, the lock close and the journal close. The run-directory,
file-read and publish closes are pinned by tests/test_invocation_audit_close.py.
"""

import asyncio
import errno
import fcntl
import os
import sys
import time
import uuid

import pytest

from code_forge import invocation_audit as audit


PRIMARY_TYPES = (RuntimeError, KeyboardInterrupt, SystemExit, MemoryError, asyncio.CancelledError)
SECONDARY_TYPES = (OSError, KeyboardInterrupt, SystemExit, MemoryError, asyncio.CancelledError)


def _fds():
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            result[name] = os.readlink("/proc/self/fd/" + name)
        except FileNotFoundError:
            pass
    return result


@pytest.fixture
def root_handle(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    handle = audit._prepare_audit_root(root, create=False)
    assert type(handle) is audit._RootHandle
    try:
        yield root, handle
    finally:
        handle.close()


def _error(kind, label):
    return kind(errno.EIO, label) if kind is OSError else kind(label)


def _observe_close(patch, owned, close_error, reuse_from):
    real_close = os.close
    attempts, replacements = [], []

    def close(fd):
        if fd not in owned:
            return real_close(fd)
        attempts.append(fd)
        real_close(fd)
        if len(attempts) == 1:
            if reuse_from is not None:
                replacements.append(fcntl.fcntl(reuse_from, fcntl.F_DUPFD_CLOEXEC, fd))
            if close_error is not None:
                raise close_error() if callable(close_error) else close_error

    patch.setattr(os, "close", close)
    return attempts, replacements


def _assert_single_close(owned, attempts, reuse_from, replacements):
    replacement_live = False
    for fd in replacements:
        assert reuse_from is not None
        try:
            current = os.fstat(fd)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            replacement_live = os.path.samestat(current, os.fstat(reuse_from))
            if replacement_live:
                os.close(fd)
    if reuse_from is not None:
        assert replacements == owned and replacement_live, "The reused descriptor must stay open."
    assert len(owned) == 1 and attempts == owned, "Each owned descriptor is closed only once."


def _root_cleanup(
    root_handle,
    monkeypatch,
    primary,
    unlock_error=None,
    close_error=None,
    *,
    point="body",
    reuse_from=None,
):
    root, handle = root_handle
    real_flock, real_close = fcntl.flock, os.close
    real_open, real_regular = os.open, audit._regular
    owned, unlocks, acquired = [], [], []
    before = _fds()
    entered = False

    def open_lock(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "root.lock" and dir_fd == handle.fd:
            owned.append(fd)
        return fd

    def regular(fd, *args, **kwargs):
        result = real_regular(fd, *args, **kwargs)
        if fd in owned and point == "validation":
            assert primary is not None
            raise primary
        return result

    def flock(fd, operation):
        result = real_flock(fd, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            acquired.append(fd)
        elif operation == fcntl.LOCK_UN:
            assert fd in owned
            unlocks.append(fd)
            if unlock_error is not None:
                raise unlock_error
        return result

    caught = None
    with monkeypatch.context() as patch:
        patch.setattr(fcntl, "flock", flock)
        patch.setattr(os, "open", open_lock)
        attempts, replacements = _observe_close(patch, owned, close_error, reuse_from)
        patch.setattr(audit, "_regular", regular)
        try:
            with audit._root_lock(handle, create=True):
                entered = True
                if primary is not None:
                    raise primary
        except BaseException as error:  # noqa: BLE001 - Inspect exact primary/fatal identities.
            caught = error
    _assert_single_close(owned, attempts, reuse_from, replacements)
    assert entered is (point == "body")
    assert unlocks == acquired == (owned if point == "body" else [])
    assert _fds() == before
    # A distinct open description can take the actual lock after unwind.
    contender = os.open(root / "root.lock", os.O_RDWR | os.O_CLOEXEC)
    try:
        real_flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        real_flock(contender, fcntl.LOCK_UN)
    finally:
        real_close(contender)
    return caught


def _journal_cleanup(
    root_handle, monkeypatch, primary, close_error=None, *, point="body", reuse_from=None
):
    root, handle = root_handle
    real_write = audit._write_all
    real_open, real_regular, real_sync = os.open, audit._regular, os.fsync
    owned, writes, synced = [], [], []
    before = _fds()
    event = dict(
        schema_version=1,
        run_id=str(uuid.uuid4()),
        event_id=str(uuid.uuid4()),
        sequence=0,
        event="run_final",
        utc="2026-11-10T21:00:00Z",
        elapsed_s=0.0,
        outcome="failed",
        audit_complete=False,
    )

    def open_journal(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "journal" and dir_fd == handle.fd:
            owned.append(fd)
        return fd

    def regular(fd, *args, **kwargs):
        result = real_regular(fd, *args, **kwargs)
        if fd in owned and point == "validation":
            assert primary is not None
            raise primary
        return result

    def sync(fd):
        result = real_sync(fd)
        if fd in owned:
            synced.append(fd)
            if point == "fsync" and primary is not None:
                raise primary
        return result

    def write(fd, data, **kwargs):
        result = real_write(fd, data, **kwargs)
        writes.append(fd)
        if point == "body" and primary is not None:
            raise primary
        return result

    caught = None
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", open_journal)
        patch.setattr(audit, "_regular", regular)
        patch.setattr(audit, "_write_all", write)
        patch.setattr(os, "fsync", sync)
        attempts, replacements = _observe_close(patch, owned, close_error, reuse_from)
        try:
            audit._append(handle.fd, event)
        except BaseException as error:  # noqa: BLE001 - Inspect exact primary/fatal identities.
            caught = error
    _assert_single_close(owned, attempts, reuse_from, replacements)
    assert writes == ([] if point == "validation" else owned)
    if point == "fsync":
        assert synced == owned
    assert ((root / "journal").stat().st_size > 0) is (point != "validation")
    assert _fds() == before
    return caught


def _chain(primary):
    return (primary.__cause__, primary.__context__, primary.__suppress_context__)


def _graph(primary):
    result, pending, seen = [], [primary], set()
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        context = current.__context__
        cause = current.__cause__
        result.append((current, context, cause, current.__suppress_context__))
        pending.extend((context, cause))
    return result


def _walk_context(primary):
    result, current, seen = [], primary, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append((current, current.__context__, current.__cause__, current.__suppress_context__))
        current = current.__context__
    return result


def _primary(kind):
    value = kind("original operation failure")
    value.__cause__ = LookupError("existing cause")
    value.__context__ = ValueError("existing context")
    value.__suppress_context__ = True
    return value


def _cleanup_at(site, root_handle, monkeypatch, primary, secondary, *, reuse_from=None):
    if site == "journal-close":
        return _journal_cleanup(root_handle, monkeypatch, primary, secondary, reuse_from=reuse_from)
    if site == "unlock":
        return _root_cleanup(root_handle, monkeypatch, primary, unlock_error=secondary)
    assert site == "lock-close"
    return _root_cleanup(root_handle, monkeypatch, primary, close_error=secondary, reuse_from=reuse_from)


@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
@pytest.mark.parametrize("indirect", [False, True], ids=["direct", "indirect"])
def test_cleanup_preserves_reused_context_links(root_handle, monkeypatch, site, indirect):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    tail = LookupError("context tail")
    secondary.__context__ = tail
    if indirect:
        predecessor = ValueError("context predecessor")
        predecessor.__context__ = secondary
        primary.__context__ = predecessor
    else:
        primary.__context__ = secondary
    cause, context, suppressed = _chain(primary)
    caught = _cleanup_at(site, root_handle, monkeypatch, primary, secondary)
    assert caught is primary
    assert primary.__cause__ is cause
    assert primary.__context__ is context
    assert primary.__suppress_context__ is suppressed
    if indirect:
        assert context is not None and context.__context__ is secondary
    assert secondary.__context__ is tail, "Restoring the head must not leave a cycle or lose its tail."


@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
def test_reused_cleanup_cause_keeps_its_context_tail(root_handle, monkeypatch, site):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    tail = LookupError("cause tail")
    secondary.__context__ = tail
    primary.__cause__ = secondary
    before = _walk_context(primary) + _walk_context(secondary)
    caught = _cleanup_at(site, root_handle, monkeypatch, primary, secondary)
    assert caught is primary
    assert _walk_context(primary) + _walk_context(secondary) == before
    assert secondary.__context__ is tail


def test_nested_cause_context_graph_is_preserved(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    cause = LookupError("nested cause")
    deeper = RuntimeError("deeper cause")
    tail = ValueError("cause context")
    deeper.__context__ = tail
    tail.__context__ = deeper
    cause.__cause__ = deeper
    secondary.__cause__ = cause
    primary.__cause__ = secondary
    before = _graph(primary)
    caught = _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary)
    assert caught is primary
    assert _graph(primary) == before


def test_reused_nested_cause_keeps_its_tail(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    cause = LookupError("nested cause")
    tail = ValueError("cause tail")
    cause.__context__ = tail
    secondary.__cause__ = cause
    primary.__context__ = secondary

    def release_nested():
        try:
            raise cause
        except LookupError:
            raise OSError(errno.EIO, "cleanup failed after release") from cause

    before = _graph(primary) + _graph(cause)
    caught = _cleanup_at("journal-close", root_handle, monkeypatch, primary, release_nested)
    assert caught is primary
    assert _graph(cause) == before[len(_graph(primary)) :]


def test_restore_failure_keeps_primary(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")

    def fail_restore(snapshot):
        raise MemoryError("restore unavailable")

    monkeypatch.setattr(audit, "_restore_chain", fail_restore)
    assert _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary) is primary


def test_evidence_retention_failure_keeps_primary(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")

    def fail_retain(primary, secondary, close=False):
        raise MemoryError("evidence retention unavailable")

    monkeypatch.setattr(audit, "_retain_cleanup_error", fail_retain)
    assert _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary) is primary
    assert "_invocation_audit_close_errors" not in vars(primary)


def test_snapshot_diagnostic_failure_still_releases(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    root, handle = root_handle
    real_write = audit._write_all
    opened = []

    def failing_snapshot(error):
        raise MemoryError("chain snapshot unavailable")

    def failing_diagnostic(primary, field, secondary):
        raise MemoryError("diagnostic unavailable")

    def write(fd, data, **kwargs):
        opened.append(fd)
        return real_write(fd, data, **kwargs)

    monkeypatch.setattr(audit, "_cleanup_chain", failing_snapshot)
    monkeypatch.setattr(audit, "_retain_diagnostic", failing_diagnostic)
    monkeypatch.setattr(audit, "_write_all", write)
    # The harness asserts the single close and the unchanged fd set; this case
    # only needs the primary to survive a failing diagnostic.
    caught = _journal_cleanup(root_handle, monkeypatch, primary, OSError(errno.EIO, "close failed"))
    assert caught is primary


def test_resident_empty_notes_list_still_receives_the_note(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    notes = []
    vars(primary)["__notes__"] = notes
    assert _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary) is primary
    assert vars(primary)["__notes__"] is notes, "A resident list keeps its identity."
    assert notes, "An empty resident note list still receives the diagnostic note."


def test_retention_does_not_ask_resident_values_for_truthiness(root_handle, monkeypatch):
    asked = []

    class LoudValue:
        def __bool__(self):
            asked.append("bool")
            return False

    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    vars(primary)["_invocation_audit_close_errors"] = LoudValue()
    assert _cleanup_at("lock-close", root_handle, monkeypatch, primary, secondary) is primary
    assert asked == [], "Retention must not evaluate exception-owned values."
    stored = vars(primary)["_invocation_audit_close_errors"]
    assert type(stored) is tuple and stored[-1] is secondary


def test_first_cleanup_error_keeps_chain_when_close_reuses_context(root_handle, monkeypatch):
    unlock = _primary(KeyboardInterrupt)
    close = OSError(errno.EIO, "close failed after release")
    unlock.__context__ = close
    assert _root_cleanup(root_handle, monkeypatch, None, unlock, close) is unlock
    assert unlock.__context__ is close and close.__context__ is None


@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
@pytest.mark.parametrize("collision", ["evidence", "notes"])
def test_cleanup_metadata_never_compares_user_keys(root_handle, monkeypatch, site, collision):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    cause, context, suppressed = _chain(primary)
    calls = []

    class CollidingKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(other)
            primary.__context__ = RuntimeError("key comparison ran")
            return str.__eq__(self, other)

    field = "_invocation_audit_cleanup_errors" if site == "unlock" else "_invocation_audit_close_errors"
    key = CollidingKey(field if collision == "evidence" else "__notes__")
    marker = object()
    state = vars(primary)
    state[key] = marker
    assert _cleanup_at(site, root_handle, monkeypatch, primary, secondary) is primary
    assert not calls, "Diagnostic lookup must not call a resident key's comparison hook."
    assert primary.__cause__ is cause
    assert primary.__context__ is context
    assert primary.__suppress_context__ is suppressed
    entries = list(dict.items(state))
    assert len(entries) == 1 and entries[0][0] is key and entries[0][1] is marker


def test_cleanup_keeps_existing_evidence_and_notes(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    previous = LookupError("existing diagnostic")
    secondary = OSError(errno.EIO, "private cleanup detail")
    state = vars(primary)
    notes = ["existing note"]
    state["_invocation_audit_cleanup_errors"] = previous
    state["__notes__"] = notes
    assert _cleanup_at("unlock", root_handle, monkeypatch, primary, secondary) is primary
    evidence = dict.get(state, "_invocation_audit_cleanup_errors")
    assert type(evidence) is tuple and evidence == (previous, secondary)
    assert state["__notes__"] is notes and notes[0] == "existing note" and len(notes) == 2
    assert "private cleanup detail" not in notes[1]


@pytest.mark.parametrize("site", ["lock-close", "journal-close"])
def test_cleanup_does_not_close_reused_descriptor(root_handle, monkeypatch, site):
    root, _ = root_handle
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "close failed after release")
    path = root / "sentinel"
    path.write_bytes(b"owned sentinel")
    seed = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        assert (
            _cleanup_at(site, root_handle, monkeypatch, primary, secondary, reuse_from=seed) is primary
        )
        assert os.read(seed, 32) == b"owned sentinel"
    finally:
        os.close(seed)


@pytest.mark.parametrize("kind", PRIMARY_TYPES)
@pytest.mark.parametrize("cleanup_kind", SECONDARY_TYPES)
@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
def test_owning_cleanup_preserves_entering_exception(root_handle, monkeypatch, kind, cleanup_kind, site):
    primary = _primary(kind)
    secondary = _error(cleanup_kind, "PRIVATE-SECONDARY-DATA")
    chain = _chain(primary)
    if site == "journal-close":
        caught = _journal_cleanup(root_handle, monkeypatch, primary, secondary)
    else:
        caught = _root_cleanup(
            root_handle,
            monkeypatch,
            primary,
            unlock_error=secondary if site == "unlock" else None,
            close_error=secondary if site == "lock-close" else None,
        )
    assert caught is primary, (
        "Cleanup must not convert an entering fatal into a different exception/refusal."
    )
    assert _chain(primary) == chain
    field = "_invocation_audit_cleanup_errors" if site == "unlock" else "_invocation_audit_close_errors"
    assert vars(primary)[field] == (secondary,)
    assert all("PRIVATE-SECONDARY-DATA" not in note for note in primary.__notes__)


@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
@pytest.mark.parametrize("cleanup_kind", SECONDARY_TYPES)
def test_cleanup_without_entering_error_is_failure(root_handle, monkeypatch, site, cleanup_kind):
    secondary = _error(cleanup_kind, "cleanup failed")
    if site == "journal-close":
        caught = _journal_cleanup(root_handle, monkeypatch, None, secondary)
    else:
        caught = _root_cleanup(
            root_handle,
            monkeypatch,
            None,
            unlock_error=secondary if site == "unlock" else None,
            close_error=secondary if site == "lock-close" else None,
        )
    assert caught is secondary


@pytest.mark.parametrize("kind", PRIMARY_TYPES)
def test_unlock_and_close_errors_keep_original_operation(root_handle, monkeypatch, kind):
    primary = _primary(kind)
    unlock = OSError(errno.EIO, "unlock failed after release")
    close = KeyboardInterrupt("close interrupted after release")
    chain = _chain(primary)
    assert _root_cleanup(root_handle, monkeypatch, primary, unlock, close) is primary
    assert _chain(primary) == chain
    assert primary._invocation_audit_cleanup_errors == (unlock,)
    assert primary._invocation_audit_close_errors == (close,)


def test_unlock_failure_wins_over_subsequent_close(root_handle, monkeypatch):
    unlock = KeyboardInterrupt("unlock interrupted after release")
    close = OSError(errno.EIO, "close failed after release")
    assert _root_cleanup(root_handle, monkeypatch, None, unlock, close) is unlock
    assert unlock._invocation_audit_close_errors == (close,)


def test_callers_unrelated_handler_is_not_entering_primary(root_handle, monkeypatch):
    unrelated = RuntimeError("outer handler")
    unlock = OSError(errno.EIO, "unlock failed after release")
    try:
        raise unrelated
    except RuntimeError:
        assert _root_cleanup(root_handle, monkeypatch, None, unlock) is unlock
    assert vars(unrelated) == {}


@pytest.mark.parametrize("point", ["body", "validation"])
@pytest.mark.parametrize("site", ["lock-close", "journal-close"])
@pytest.mark.parametrize("kind", PRIMARY_TYPES)
def test_precleanup_failure_paths_keep_the_exact_primary(root_handle, monkeypatch, point, site, kind):
    primary = _primary(kind)
    secondary = KeyboardInterrupt("close interrupted after release")
    chain = _chain(primary)
    exercise = _root_cleanup if site == "lock-close" else _journal_cleanup
    assert exercise(root_handle, monkeypatch, primary, close_error=secondary, point=point) is primary
    assert _chain(primary) == chain
    assert primary._invocation_audit_close_errors == (secondary,)


@pytest.mark.parametrize("kind", PRIMARY_TYPES)
def test_journal_sync_failure_keeps_the_exact_primary(root_handle, monkeypatch, kind):
    primary = _primary(kind)
    secondary = OSError(errno.EIO, "close failed after release")
    chain = _chain(primary)
    assert _journal_cleanup(root_handle, monkeypatch, primary, secondary, point="fsync") is primary
    assert _chain(primary) == chain
    assert primary._invocation_audit_close_errors == (secondary,)


@pytest.mark.parametrize("site", ["unlock", "lock-close", "journal-close"])
def test_successful_operation_and_cleanup_stay_successful(root_handle, monkeypatch, site):
    exercise = _journal_cleanup if site == "journal-close" else _root_cleanup
    assert exercise(root_handle, monkeypatch, None) is None


@pytest.mark.parametrize("site", ["unlock", "journal-close"])
def test_missing_chain_snapshot_still_releases_once(root_handle, monkeypatch, site):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")
    snapshots = []

    def fail_snapshot(error):
        snapshots.append(error)
        raise MemoryError("chain snapshot unavailable")

    monkeypatch.setattr(audit, "_cleanup_chain", fail_snapshot)
    assert _cleanup_at(site, root_handle, monkeypatch, primary, secondary) is primary
    assert snapshots and all(error is primary for error in snapshots)
    field = "_invocation_audit_cleanup_errors" if site == "unlock" else "_invocation_audit_close_errors"
    assert vars(primary)[field] == (secondary,)


@pytest.mark.parametrize("reuse_context", [False, True], ids=["fresh-error", "reused-context"])
def test_secondary_evidence_failure_cannot_replace_primary(root_handle, monkeypatch, reuse_context):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "unlock failed after release")
    if reuse_context:
        primary.__context__ = secondary
    chain = _chain(primary)
    previous_trace = sys.gettrace()
    injected = []

    def fail_evidence(frame, event, arg):
        if (
            event == "line"
            and frame.f_code is audit._retain_cleanup_error.__code__
            and "state" in frame.f_locals
        ):
            injected.append(True)
            raise MemoryError("evidence allocation failed")
        return fail_evidence

    sys.settrace(fail_evidence)
    try:
        caught = _root_cleanup(root_handle, monkeypatch, primary, unlock_error=secondary)
    finally:
        sys.settrace(previous_trace)
    assert injected == [True] and caught is primary
    assert _chain(primary) == chain, "A failed evidence write must not cost the entering chain."


def test_real_recorder_fatal_is_not_swallowed_by_unlock(root_handle, monkeypatch):
    root, _ = root_handle
    context = audit.InvocationContext(
        str(uuid.uuid4()), None, None, None, None, None, None, "cleanup-test", None
    )
    recorder = audit.AttemptRecorder(
        root,
        context=context,
        monotonic=time.monotonic,
        utc_now=lambda: "2026-11-10T21:00:00Z",
        audit_schema_version=2,
    )
    primary = KeyboardInterrupt("inventory interrupted")
    secondary = OSError(errno.EIO, "unlock failed after release")
    real_flock = fcntl.flock
    unlocks = []
    before = _fds()

    def inventory(*args, **kwargs):
        raise primary

    def flock(fd, operation):
        result = real_flock(fd, operation)
        if operation == fcntl.LOCK_UN:
            unlocks.append(fd)
            raise secondary
        return result

    caught = None
    ack = None
    with monkeypatch.context() as patch:
        patch.setattr(audit, "_inventory", inventory)
        patch.setattr(fcntl, "flock", flock)
        try:
            ack = recorder.open()
        except BaseException as error:  # noqa: BLE001 - Verify this exact fatal, not any exception.
            caught = error
    assert caught is primary and ack is None
    assert primary._invocation_audit_cleanup_errors == (secondary,)
    assert len(unlocks) == 1 and _fds() == before


def test_close_failure_does_not_borrow_the_callers_handler(root_handle, monkeypatch):
    unrelated = RuntimeError("outer handler")
    close_error = OSError(errno.EIO, "close failed after release")
    try:
        raise unrelated
    except RuntimeError:
        caught = _root_cleanup(root_handle, monkeypatch, None, close_error=close_error)
    assert caught is close_error, "A close failure must surface, not hide behind the caller's handler."
    assert vars(unrelated) == {}


class FalsyError(Exception):
    truthiness_checks = 0

    def __bool__(self):
        type(self).truthiness_checks += 1
        return False


def test_falsy_primary_is_not_replaced_by_cleanup(root_handle, monkeypatch):
    primary = FalsyError("falsy entering exception")
    close_error = OSError(errno.EIO, "close failed after release")
    FalsyError.truthiness_checks = 0
    caught = _root_cleanup(root_handle, monkeypatch, primary, close_error=close_error)
    assert caught is primary, "A falsy entering exception is still the primary."
    assert FalsyError.truthiness_checks == 0, "Cleanup must not ask the exception for truthiness."
    assert primary._invocation_audit_close_errors == (close_error,)


def test_restore_does_not_run_subclass_property_setters(root_handle, monkeypatch):
    calls = []

    class PropertyError(KeyboardInterrupt):
        @property
        def __context__(self):
            return BaseException.__dict__["__context__"].__get__(self)

        @__context__.setter
        def __context__(self, value):
            calls.append("__context__")

    primary = PropertyError("entering primary")
    tail = LookupError("tail")
    BaseException.__dict__["__context__"].__set__(primary, tail)
    close_error = OSError(errno.EIO, "close failed after release")
    calls.clear()
    caught = _root_cleanup(root_handle, monkeypatch, primary, close_error=close_error)
    assert caught is primary
    assert calls == [], "Restoring links must not run a subclass property setter."
    assert BaseException.__dict__["__context__"].__get__(primary) is tail


def test_snapshot_error_is_recorded(root_handle, monkeypatch):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "cleanup failed after release")

    def failing_snapshot(error):
        raise MemoryError("chain snapshot unavailable")

    monkeypatch.setattr(audit, "_cleanup_chain", failing_snapshot)
    assert _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary) is primary
    recorded = vars(primary).get("_invocation_audit_snapshot_errors")
    assert type(recorded) is tuple and any(isinstance(item, MemoryError) for item in recorded)


def test_chain_snapshot_does_not_run_property_getters(root_handle, monkeypatch):
    reads = []

    class ReadCountingError(KeyboardInterrupt):
        @property
        def __context__(self):
            reads.append("__context__")
            return BaseException.__dict__["__context__"].__get__(self)

        @__context__.setter
        def __context__(self, value):
            BaseException.__dict__["__context__"].__set__(self, value)

    primary = ReadCountingError("entering primary")
    BaseException.__dict__["__context__"].__set__(primary, LookupError("tail"))
    secondary = OSError(errno.EIO, "cleanup failed after release")
    reads.clear()
    assert _cleanup_at("journal-close", root_handle, monkeypatch, primary, secondary) is primary
    assert reads == [], "Snapshotting the chain must read through the base descriptor."
