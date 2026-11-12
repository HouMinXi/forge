"""Real owned-FD controls for five close sites, not storage durability qualification.

The stage case controls journal admission/state only. Its actual raw-file open,
validation, writes, sync, readback and close run on private files; neither the
release helper nor the owning function is replaced.
"""

import asyncio
import errno
import fcntl
import hashlib
import os
from types import SimpleNamespace

import pytest

from code_forge import invocation_audit as audit


SITES = ("sync_file", "entries", "inventory", "bounded_stage_entries", "write_stage_chunk")
KINDS = (RuntimeError, KeyboardInterrupt, SystemExit, MemoryError, asyncio.CancelledError)
SECONDARIES = (OSError, KeyboardInterrupt, SystemExit, MemoryError, asyncio.CancelledError)
IDENTITY = audit.StageIdentity(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
    "wire",
)


def _fds():
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            result[name] = os.readlink("/proc/self/fd/" + name)
        except FileNotFoundError:
            pass
    return result


@pytest.fixture
def private_root(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield root, fd
    finally:
        os.close(fd)


def _stage(patch, root, *, append=False):
    handle = audit.StageHandle(IDENTITY)
    state = dict(
        handle=handle,
        sealed=False,
        adopted=None,
        capped=False,
        last_write=None,
        checkpoint=None,
        hasher=hashlib.sha256(),
        pending=False,
    )
    if append:
        path = root / ("raw-" + IDENTITY.artifact_id)
        path.write_bytes(b"prefix")
        path.chmod(0o600)
        info = path.stat()
        state["hasher"] = hashlib.sha256(b"prefix")
        state["checkpoint"] = audit.StageCheckpoint(IDENTITY, 6, state["hasher"].hexdigest())
        state["file_identity"] = audit._file_identity(info)
        state["inode"] = (info.st_dev, info.st_ino)
        state["last_write"] = (
            (0, 6, state["hasher"].hexdigest()),
            audit.StageAcknowledgement(True, handle, state["checkpoint"], 6, False, None),
        )
    appended = []
    recorder = SimpleNamespace(
        _events=[],
        _staged=0,
        _event=lambda kind, **values: dict(event=kind, **values),
        _append_stage_event=lambda fd, event, checkpoint: appended.append(event),
    )
    folded = ({IDENTITY.attempt_id: {"start": {"backend": {"format": "openai"}}}},)
    patch.setattr(audit, "_stage_journal_state", lambda *a, **k: (folded, None, None, None, None, {}))
    patch.setattr(audit, "_fold", lambda *a, **k: None)
    patch.setattr(audit, "_space", lambda *a, **k: None)
    patch.setattr(audit, "_file_slots", lambda *a, **k: None)
    return recorder, state, appended


def _exercise(
    private_root,
    monkeypatch,
    site,
    primary=None,
    secondary=None,
    *,
    point="body",
    reuse=False,
    append=False,
):
    root, parent = private_root
    if site in ("sync_file", "inventory", "bounded_stage_entries", "entries"):
        name = "root.lock" if site == "inventory" else "input"
        (root / name).write_bytes(b"" if site == "inventory" else b"payload")
        (root / name).chmod(0o600)
    real_open, real_close = os.open, os.close
    real_regular, real_directory = audit._regular, audit._directory
    real_sync, real_listdir, real_write = os.fsync, os.listdir, audit._write_all
    before = _fds()
    owned, closed, active, replacements = [], [], set(), []
    body, appended = [], []

    def is_target(path, flags, dir_fd):
        if dir_fd != parent:
            return False
        return (
            path == "input"
            if site == "sync_file"
            else path == "root.lock"
            if site == "inventory"
            else path == "."
            if site in ("entries", "bounded_stage_entries")
            else path == "raw-" + IDENTITY.artifact_id and bool(flags & os.O_WRONLY)
        )

    def opened(path, flags, mode=0o777, *, dir_fd=None):
        target = is_target(path, flags, dir_fd)
        if target and point == "open":
            assert primary is not None
            raise primary
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if target:
            owned.append(fd)
            active.add(fd)
        return fd

    def close(fd):
        if fd not in active:
            return real_close(fd)
        active.remove(fd)
        closed.append(fd)
        real_close(fd)
        if reuse:
            replacements.append(fcntl.fcntl(parent, fcntl.F_DUPFD_CLOEXEC, fd))
        if secondary is not None:
            raise secondary
        return None

    def fail(fd, at):
        if fd in active:
            body.append(at)
            if primary is not None and point == at:
                raise primary

    def regular(fd, *args, **kwargs):
        value = real_regular(fd, *args, **kwargs)
        fail(fd, "body" if site == "inventory" else "validation")
        return value

    def directory(fd):
        value = real_directory(fd)
        fail(fd, "validation")
        return value

    def sync(fd):
        real_sync(fd)
        fail(fd, "body" if site == "sync_file" else "fsync")

    def listing(fd):
        result = real_listdir(fd)
        fail(fd, "body")
        return result

    def write(fd, data, **kwargs):
        real_write(fd, data, **kwargs)
        fail(fd, "body")

    def checkpoint():
        if active:
            fail(next(iter(active)), "body" if site == "bounded_stage_entries" else "checkpoint")
        elif point == "before_open":
            assert primary is not None
            raise primary

    caught = result = None
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", opened)
        patch.setattr(os, "close", close)
        patch.setattr(os, "fsync", sync)
        patch.setattr(os, "listdir", listing)
        patch.setattr(audit, "_regular", regular)
        patch.setattr(audit, "_directory", directory)
        patch.setattr(audit, "_write_all", write)
        if site == "write_stage_chunk":
            recorder, state, appended = _stage(patch, root, append=append)
            prior_checkpoint = state["checkpoint"]
            prior_hasher = state["hasher"]
            prior_digest = prior_hasher.hexdigest()
            prior_last_write = state["last_write"]
        try:
            if site == "sync_file":
                result = audit._sync_file(parent, "input", _checkpoint=checkpoint)
            elif site == "entries":
                result = audit._entries(parent)
            elif site == "inventory":
                result = audit._inventory(SimpleNamespace(fd=parent), _checkpoint=checkpoint)
            elif site == "bounded_stage_entries":
                result = audit._bounded_stage_entries(parent, checkpoint)
            else:
                result = audit.AttemptRecorder._write_stage_chunk(
                    recorder, parent, state, checkpoint, 6 if append else 0, b"payload"
                )
        except BaseException as error:  # noqa: BLE001 - Inspect fatal and ordinary identities.
            caught = error
    if point in ("open", "before_open"):
        assert owned == closed == []
    else:
        assert len(owned) == 1 and closed == owned and not active
        assert body, "The actual owned operation must run before its close."
    for fd in replacements:
        assert fd == owned[0] and os.path.samestat(os.fstat(fd), os.fstat(parent))
        real_close(fd)
    assert _fds() == before, "No owned descriptor leaks or close retries."
    if site == "write_stage_chunk":
        healthy = primary is None and secondary is None
        assert len(appended) == int(healthy), "A failed close cannot publish a stage checkpoint."
        assert (state["checkpoint"] is not prior_checkpoint) is healthy
        if not healthy:
            assert state["hasher"] is prior_hasher and state["hasher"].hexdigest() == prior_digest
            assert state["last_write"] is prior_last_write
            assert state["pending"] is (point != "before_open")
            assert result is None, "No successful acknowledgement escapes a failed operation."
        if healthy:
            assert result.admitted and result.accepted_bytes == 7 and not result.partial
            assert (root / ("raw-" + IDENTITY.artifact_id)).read_bytes() == (
                b"prefixpayload" if append else b"payload"
            )
            assert not state["pending"]
    elif caught is None:
        if site == "entries":
            assert result == ["input"]
        elif site == "bounded_stage_entries":
            assert result == {"input"}
        elif site == "inventory":
            assert result == ({}, 0)
        else:
            assert result is None
    return caught


def _primary(kind=RuntimeError):
    value = kind("operation")
    value.__cause__ = LookupError("cause")
    value.__context__ = ValueError("context")
    value.__suppress_context__ = True
    return value


@pytest.mark.parametrize("site", SITES)
def test_healthy_close(private_root, monkeypatch, site):
    assert _exercise(private_root, monkeypatch, site) is None


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("kind", KINDS)
def test_primary_only(private_root, monkeypatch, site, kind):
    primary = _primary(kind)
    assert _exercise(private_root, monkeypatch, site, primary) is primary
    assert "_invocation_audit_close_errors" not in vars(primary)


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("kind", SECONDARIES)
def test_close_only(private_root, monkeypatch, site, kind):
    secondary = kind("release")
    assert _exercise(private_root, monkeypatch, site, secondary=secondary) is secondary


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("secondary_kind", SECONDARIES)
def test_double_fault_preserves_primary(private_root, monkeypatch, site, kind, secondary_kind):
    primary, secondary = _primary(kind), secondary_kind("release")
    chain = primary.__cause__, primary.__context__, primary.__suppress_context__
    assert _exercise(private_root, monkeypatch, site, primary, secondary) is primary
    assert (primary.__cause__, primary.__context__, primary.__suppress_context__) == chain
    assert primary._invocation_audit_close_errors == (secondary,)


@pytest.mark.parametrize("site", SITES)
def test_handled_caller_error_is_not_primary(private_root, monkeypatch, site):
    caller, secondary = RuntimeError("caller"), OSError(errno.EIO, "release")
    try:
        raise caller
    except RuntimeError:
        assert _exercise(private_root, monkeypatch, site, secondary=secondary) is secondary
    assert not vars(caller)


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("double", (False, True))
def test_close_failure_never_retries_reused_fd(private_root, monkeypatch, site, double):
    primary = _primary() if double else None
    secondary = OSError(errno.EIO, "released")
    assert _exercise(private_root, monkeypatch, site, primary, secondary, reuse=True) is (
        primary if double else secondary
    )


@pytest.mark.parametrize("site", SITES)
def test_open_failure_owns_no_fd(private_root, monkeypatch, site):
    primary = OSError(errno.EACCES, "open")
    assert _exercise(private_root, monkeypatch, site, primary, point="open") is primary
    assert not vars(primary)


@pytest.mark.parametrize(
    "site,point",
    [
        ("sync_file", "validation"),
        ("sync_file", "checkpoint"),
        ("entries", "validation"),
        ("bounded_stage_entries", "validation"),
        ("write_stage_chunk", "validation"),
        ("write_stage_chunk", "checkpoint"),
        ("write_stage_chunk", "fsync"),
    ],
)
def test_pre_body_failure_keeps_primary(private_root, monkeypatch, site, point):
    primary, secondary = _primary(KeyboardInterrupt), OSError(errno.EIO, "release")
    assert _exercise(private_root, monkeypatch, site, primary, secondary, point=point) is primary
    assert primary._invocation_audit_close_errors == (secondary,)


@pytest.mark.parametrize("site", ("inventory", "bounded_stage_entries", "write_stage_chunk"))
def test_checkpoint_before_open_has_no_cleanup(private_root, monkeypatch, site):
    primary = _primary()
    assert _exercise(private_root, monkeypatch, site, primary, point="before_open") is primary
    assert "_invocation_audit_close_errors" not in vars(primary)


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("diagnostic", ("_cleanup_chain", "_retain_cleanup_error"))
def test_diagnostic_failure_cannot_replace_primary(private_root, monkeypatch, site, diagnostic):
    def broken(*args, **kwargs):
        raise MemoryError("diagnostic")

    monkeypatch.setattr(audit, diagnostic, broken)
    primary, secondary = _primary(), OSError(errno.EIO, "release")
    assert _exercise(private_root, monkeypatch, site, primary, secondary) is primary


@pytest.mark.parametrize("site", SITES)
def test_falsy_hostile_primary_hooks_are_not_called(private_root, monkeypatch, site):
    calls = []

    class Hostile(RuntimeError):
        def __bool__(self):
            calls.append("bool")
            return False

        def __getattribute__(self, name):
            if name in ("__dict__", "__cause__", "__context__", "__suppress_context__", "__notes__"):
                calls.append(name)
                raise AssertionError("exception hooks must not run")
            return super().__getattribute__(name)

    primary, secondary = Hostile("primary"), OSError(errno.EIO, "release")
    assert _exercise(private_root, monkeypatch, site, primary, secondary) is primary
    assert not calls
    state = BaseException.__dict__["__dict__"].__get__(primary)
    assert state["_invocation_audit_close_errors"] == (secondary,)


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("indirect", (False, True))
def test_reused_cleanup_chain_is_restored(private_root, monkeypatch, site, indirect):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "release")
    tail = LookupError("secondary context")
    secondary.__context__ = tail
    if indirect:
        middle = ValueError("middle")
        middle.__context__ = secondary
        primary.__context__ = middle
    else:
        primary.__context__ = secondary
    nodes = [primary, primary.__cause__, primary.__context__, secondary, tail]
    snapshot = [(node, node.__cause__, node.__context__, node.__suppress_context__) for node in nodes]
    assert _exercise(private_root, monkeypatch, site, primary, secondary) is primary
    assert [
        (node, node.__cause__, node.__context__, node.__suppress_context__) for node in nodes
    ] == snapshot
    assert primary._invocation_audit_close_errors == (secondary,)


@pytest.mark.parametrize("append", (False, True), ids=("create", "append"))
@pytest.mark.parametrize("fault", ("healthy", "primary", "close", "double"))
def test_stage_create_and_append_publication_boundary(private_root, monkeypatch, append, fault):
    primary = _primary(KeyboardInterrupt) if fault in ("primary", "double") else None
    secondary = OSError(errno.EIO, "release") if fault in ("close", "double") else None
    assert _exercise(
        private_root, monkeypatch, "write_stage_chunk", primary, secondary, append=append
    ) is (primary if primary is not None else secondary)


@pytest.mark.parametrize("point", ("validation", "checkpoint", "fsync", "open", "before_open"))
def test_stage_append_early_fault_cannot_advance_checkpoint(private_root, monkeypatch, point):
    primary = _primary(KeyboardInterrupt)
    secondary = OSError(errno.EIO, "release")
    assert (
        _exercise(
            private_root,
            monkeypatch,
            "write_stage_chunk",
            primary,
            secondary,
            point=point,
            append=True,
        )
        is primary
    )


@pytest.mark.parametrize("double", (False, True))
def test_stage_append_does_not_reclose_reused_fd(private_root, monkeypatch, double):
    primary = _primary() if double else None
    secondary = OSError(errno.EIO, "release")
    assert _exercise(
        private_root,
        monkeypatch,
        "write_stage_chunk",
        primary,
        secondary,
        append=True,
        reuse=True,
    ) is (primary if double else secondary)
