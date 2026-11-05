"""Real private-FD cleanup controls, not recorder durability qualification."""

import asyncio
import errno
import os
from types import SimpleNamespace

import pytest

import code_forge.invocation_audit as audit


RUN = "00000000-0000-4000-8000-000000000001"
SITES = ("run_dir", "read_file", "publish")
PRIMARY_TYPES = (RuntimeError, KeyboardInterrupt, SystemExit, asyncio.CancelledError)


def _fd_observations():
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
    (root / RUN).mkdir(mode=0o700)
    (root / "input").write_bytes(b"private input")
    (root / "input").chmod(0o600)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield root, fd
    finally:
        os.close(fd)


def _exercise(private_root, monkeypatch, site, primary, close_error, *, point="body"):
    root, parent_fd = private_root
    original_open, original_close = os.open, os.close
    original_read, original_write = os.read, os.write
    original_sync = os.fsync
    original_regular, original_qualified_dir = audit._regular, audit._qualified_dir
    owned, closed, active = [], [], set()
    validations = []
    before = _fd_observations()

    def open_private(path, flags, mode=0o777, *, dir_fd=None):
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd == parent_fd and path != ".":
            owned.append(fd)
            active.add(fd)
        return fd

    def close_private(fd):
        original_close(fd)
        if fd in active:
            active.remove(fd)
            closed.append(fd)
            if close_error is not None:
                error = close_error[len(closed) - 1] if type(close_error) is tuple else close_error
                raise error

    def read_private(fd, size):
        if fd in active and primary is not None and point == "body":
            raise primary
        return original_read(fd, size)

    def write_private(fd, data):
        if fd in active and primary is not None and point == "body":
            raise primary
        return original_write(fd, data)

    def sync_private(fd):
        if fd in active and primary is not None and point == "fsync":
            raise primary
        return original_sync(fd)

    def regular_private(fd):
        result = original_regular(fd)
        validations.append(fd)
        if point == "validation" or (point == "stable" and len(validations) == 2):
            raise primary
        return result

    def qualify_private(fd):
        result = original_qualified_dir(fd)
        if point == "validation":
            raise primary
        return result

    def checkpoint():
        raise primary

    caught = None
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", open_private)
        patch.setattr(os, "close", close_private)
        patch.setattr(os, "read", read_private)
        patch.setattr(os, "write", write_private)
        patch.setattr(os, "fsync", sync_private)
        patch.setattr(audit, "_regular", regular_private)
        patch.setattr(audit, "_qualified_dir", qualify_private)
        try:
            if site in ("run_dir", "nested"):
                with audit._run_dir(SimpleNamespace(fd=parent_fd), RUN) as fd:
                    assert fd in owned
                    if site == "nested":
                        audit._read_file(parent_fd, "input")
                    elif primary is not None:
                        raise primary
            elif site == "read_file":
                options = {"_stable": point == "stable"}
                if point == "checkpoint":
                    options["_checkpoint"] = checkpoint
                assert audit._read_file(parent_fd, "input", **options) == b"private input"
            else:
                audit._publish(parent_fd, "output", b"private output")
        except BaseException as error:  # noqa: BLE001 -- Inspect exact primary and fatal identities.
            caught = error
    expected = 2 if site == "nested" else 1
    assert len(owned) == expected and closed == owned[::-1], "Close each real owned FD exactly once."
    assert _fd_observations() == before, "The close injection releases the FD before raising."
    if site == "publish":
        assert (root / "output").exists() is (primary is None and close_error is None)
    return caught


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("kind", PRIMARY_TYPES)
@pytest.mark.parametrize("close_fails", (False, True), ids=("normal-close", "close-eio"))
def test_primary_identity_survives_close(private_root, monkeypatch, site, kind, close_fails):
    primary = kind("primary")
    close_error = OSError(errno.EIO, "close failed after release") if close_fails else None
    caught = _exercise(private_root, monkeypatch, site, primary, close_error)
    assert caught is primary
    if close_fails:
        assert primary._invocation_audit_close_errors == (close_error,)
        assert primary.__notes__ == [
            "Invocation audit FD close also failed; see _invocation_audit_close_errors."
        ]
    else:
        assert vars(primary) == {}


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("close_fails", (False, True), ids=("normal-close", "close-eio"))
def test_close_without_primary(private_root, monkeypatch, site, close_fails):
    close_error = OSError(errno.EIO, "close failed after release") if close_fails else None
    assert _exercise(private_root, monkeypatch, site, None, close_error) is close_error


@pytest.mark.parametrize("site", SITES)
def test_callers_handled_error_is_not_the_primary(private_root, monkeypatch, site):
    caller_error = RuntimeError("unrelated caller handler")
    close_error = OSError(errno.EIO, "close failed after release")
    try:
        raise caller_error
    except RuntimeError:
        assert _exercise(private_root, monkeypatch, site, None, close_error) is close_error
    assert vars(caller_error) == {}


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("notes", ("absent", "list", "malformed", "subclass"))
def test_primary_metadata_and_private_close_evidence(private_root, monkeypatch, site, notes):
    calls = []

    class HostilePrimary(RuntimeError):
        def __getattribute__(self, name):
            if name in ("__dict__", "__notes__", "add_note", "_invocation_audit_close_errors"):
                calls.append(name)
                raise AssertionError("must bypass primary attribute hooks")
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            if name == "__traceback__":
                # contextlib restores the entering traceback when propagating a
                # generator-context-manager body failure, independently of close.
                return super().__setattr__(name, value)
            calls.append(name)
            raise AssertionError("must bypass primary attribute hooks")

    class HostileClose(OSError):
        def __str__(self):
            calls.append("str")
            raise AssertionError("must not format private close data")

        def __repr__(self):
            calls.append("repr")
            raise AssertionError("must not format private close data")

    class HostileNotes(list):
        def append(self, value):
            calls.append("append")
            raise AssertionError("must not invoke notes subclass hooks")

    primary = HostilePrimary("primary")
    cause, context = ValueError("existing cause"), LookupError("existing context")
    BaseException.__cause__.__set__(primary, cause)
    BaseException.__context__.__set__(primary, context)
    BaseException.__suppress_context__.__set__(primary, True)
    state = BaseException.__dict__["__dict__"].__get__(primary)
    previous = None
    if notes != "absent":
        previous = {
            "list": ["existing note"],
            "malformed": "existing malformed notes",
            "subclass": HostileNotes(["existing note"]),
        }[notes]
        state["__notes__"] = previous
    close_error = HostileClose(errno.EIO, "PRIVATE-CLOSE-PAYLOAD")
    assert _exercise(private_root, monkeypatch, site, primary, close_error) is primary
    assert not calls
    assert primary.__cause__ is cause and primary.__context__ is context
    assert primary.__suppress_context__ is True
    assert state["_invocation_audit_close_errors"] == (close_error,)
    if notes in ("malformed", "subclass"):
        assert state["__notes__"] is previous
        assert previous == ("existing malformed notes" if notes == "malformed" else ["existing note"])
    else:
        if notes == "list":
            assert state["__notes__"] is previous and previous[0] == "existing note"
        assert "PRIVATE-CLOSE-PAYLOAD" not in " ".join(state["__notes__"])
        assert state["__notes__"][-1] == (
            "Invocation audit FD close also failed; see _invocation_audit_close_errors."
        )


@pytest.mark.parametrize(
    "site,point",
    [
        ("run_dir", "validation"),
        ("read_file", "validation"),
        ("read_file", "stable"),
        ("read_file", "checkpoint"),
        ("publish", "fsync"),
    ],
)
def test_failures_before_return_or_yield_keep_identity(private_root, monkeypatch, site, point):
    primary = KeyboardInterrupt("primary")
    close_error = OSError(errno.EIO, "close failed after release")
    assert _exercise(private_root, monkeypatch, site, primary, close_error, point=point) is primary
    assert primary._invocation_audit_close_errors == (close_error,)


def test_nested_cleanup_retains_each_close_error(private_root, monkeypatch):
    primary = asyncio.CancelledError("primary")
    close_errors = (OSError(errno.EIO, "file close"), OSError(errno.EIO, "directory close"))
    assert _exercise(private_root, monkeypatch, "nested", primary, close_errors) is primary
    assert primary._invocation_audit_close_errors == close_errors
    assert len(primary.__notes__) == 2


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("existing_notes", (False, True), ids=("new-notes", "existing-notes"))
def test_primary_dictionary_hooks_are_not_called(private_root, monkeypatch, site, existing_notes):
    calls = []

    class HostileState(dict):
        def get(self, *args):
            calls.append("get")
            raise AssertionError("must bypass exception dictionary hooks")

        def __contains__(self, key):
            calls.append("contains")
            raise AssertionError("must bypass exception dictionary hooks")

        def __getitem__(self, key):
            calls.append("getitem")
            raise AssertionError("must bypass exception dictionary hooks")

        def __setitem__(self, key, value):
            calls.append("setitem")
            raise AssertionError("must bypass exception dictionary hooks")

    primary = KeyboardInterrupt("primary")
    state = HostileState({"__notes__": ["prior note"]} if existing_notes else {})
    primary.__dict__ = state
    close_error = OSError(errno.EIO, "close failed after release")
    assert _exercise(private_root, monkeypatch, site, primary, close_error) is primary
    assert not calls
    assert dict.__getitem__(state, "_invocation_audit_close_errors") == (close_error,)
    notes = dict.__getitem__(state, "__notes__")
    assert len(notes) == (2 if existing_notes else 1)
    if existing_notes:
        assert notes[0] == "prior note"
