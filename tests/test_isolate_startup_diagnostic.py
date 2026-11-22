"""#31: a failed sandbox start keeps a bounded child startup diagnostic.

These tests run without cgroups or bubblewrap. The cgroup root is a plain
directory, so the limit files and cgroup.procs are regular files: the real
forked child writes its pid there and the placement check finds it. Each
test then breaks one step of the real start path and reads the error.

Only the isolate module's view of ``os`` is replaced (``_ScopedOs``), so the
overrides never reach pytest, shutil or other threads' code.
"""

import errno
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import Counter
from types import SimpleNamespace

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.isolate import (
    CgroupCleanupError,
    IsolationError,
    SandboxSpec,
    Supervisor,
    SupervisorThread,
)

# The real start path forks from a threaded pytest process, as the real
# isolation tests do; the child only execs or calls os._exit.
pytestmark = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")

_MISSING_BINARY = ("/nonexistent/forge-bwrap",)

# The real removal, kept before ``plain_root`` replaces it with rmtree.
_REAL_REMOVE_CGROUP = Supervisor._remove_cgroup

# Seconds one test may take. Each test here takes well under a second. A
# regression that blocks on a read or loops forever must fail its test, not
# hang the run until the CI step times out (pytest-timeout is not installed).
_TEST_TIME_LIMIT = 15


class _TestHung(AssertionError):
    """Raised in the main thread when a test runs past ``_TEST_TIME_LIMIT``."""


@pytest.fixture(autouse=True)
def _time_limit():
    """Fail a test that runs past the limit, using SIGALRM in the main thread.

    The handler raises in the main thread, which interrupts a blocked read,
    a lock wait or a busy loop there. Forked children do not inherit the timer.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def expire(signum, frame):
        raise _TestHung("test did not finish within %d s" % _TEST_TIME_LIMIT)

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, _TEST_TIME_LIMIT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def plain_root(tmp_path, monkeypatch):
    """A cgroup root that is an ordinary directory."""
    monkeypatch.setattr(isolate, "verify_isolation_support", lambda root: None)
    monkeypatch.setattr(
        Supervisor,
        "_remove_cgroup",
        lambda self, force=False, timeout=10: shutil.rmtree(self.cgroup_path, ignore_errors=True),
    )
    return tmp_path


class _ScopedOs:
    """The isolate module's ``os``: the real module except for the overrides."""

    def __init__(self, **overrides):
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(os, name)


def _scoped_os(monkeypatch, procs_flags=os.O_WRONLY, procs_error=None, **overrides):
    """Patch isolate's ``os`` so cgroup.procs (absent in a plain directory) opens.

    ``procs_flags`` are the flags cgroup.procs is opened with; ``procs_error``
    makes that open fail instead. Every other open is the real one.
    """

    def scoped_open(path, flags, *args, **kwargs):
        if str(path).endswith("cgroup.procs"):
            if procs_error is not None:
                raise procs_error
            return os.open(path, procs_flags | os.O_CREAT, 0o600)
        return os.open(path, flags, *args, **kwargs)

    overrides.setdefault("open", scoped_open)
    scoped = _ScopedOs(**overrides)
    monkeypatch.setattr(isolate, "os", scoped)
    return scoped


def _in_child_of(parent_pid):
    return os.getpid() != parent_pid


def _supervisor(root, argv=("/bin/true",)):
    spec = SandboxSpec(
        run_id="diag",
        command=("/bin/true",),
        cwd="/workspace",
        memory_mb=64,
        pids=32,
        workspace_mb=16,
    )
    sup = Supervisor(spec, str(root))
    sup._bwrap_argv = lambda: list(argv)
    return sup


def _stop(thread, sup):
    """Join the supervisor thread and reap its child, killing a stuck child.

    A child stuck before exec keeps the thread blocked on the status pipe.
    Killing the child closes its end of the pipe, so the thread finishes and
    no process outlives the test.
    """
    thread.join(timeout=5)
    if sup._process is None:
        return
    if thread.is_alive():
        sup._process.kill()
        thread.join(timeout=5)
    try:
        sup._process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        sup._process.kill()
        sup._process.wait(timeout=5)


def _failed_start(root, argv=("/bin/true",), expected=IsolationError):
    """Start a supervisor that must fail; return it and the raised error."""
    sup = _supervisor(root, argv)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        with pytest.raises(expected) as info:
            thread.wait_started(30)
    finally:
        _stop(thread, sup)
    return sup, info.value


def _start_error(root, argv=("/bin/true",), expected=IsolationError):
    return str(_failed_start(root, argv, expected)[1])


def _open_fds():
    return sorted(os.listdir("/proc/self/fd"))


# --- failures on the real start path ---------------------------------------


def test_placement_failure_reports_child_stage_and_errno(plain_root, monkeypatch):
    # A read-only cgroup.procs makes the child's pid write fail with EBADF.
    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY)
    before = _open_fds()

    message = _start_error(plain_root)

    assert message == (
        "payload process was not placed in the run cgroup "
        "(child exit 127; error stage=place type=OSError errno=%d)" % errno.EBADF
    )
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_exec_failure_reports_child_stage_and_errno(plain_root, monkeypatch):
    _scoped_os(monkeypatch)
    before = _open_fds()

    message = _start_error(plain_root, argv=_MISSING_BINARY)

    assert message == (
        "payload failed to exec bubblewrap "
        "(error stage=exec type=FileNotFoundError errno=%d)" % errno.ENOENT
    )
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_setup_failure_reports_setup_stage(plain_root, monkeypatch):
    # The child's first close fails, before it writes its pid.
    parent = os.getpid()

    def close(fd):
        if _in_child_of(parent):
            raise OSError(errno.EIO, "injected close failure")
        os.close(fd)

    _scoped_os(monkeypatch, close=close)

    message = _start_error(plain_root)

    assert message == (
        "payload process was not placed in the run cgroup "
        "(child exit 127; error stage=setup type=OSError errno=%d)" % errno.EIO
    )


def test_gate_failure_reports_gate_stage(plain_root, monkeypatch):
    # The child receives the gate byte, then its read reports an error, so
    # the parent has already opened the gate and reads the exec status.
    parent = os.getpid()

    def read(fd, n):
        data = os.read(fd, n)
        if _in_child_of(parent):
            raise OSError(errno.EIO, "injected gate read failure")
        return data

    _scoped_os(monkeypatch, read=read)

    message = _start_error(plain_root)

    assert message == (
        "payload failed to exec bubblewrap (error stage=gate type=OSError errno=%d)" % errno.EIO
    )


def test_gate_closed_without_opening_reports_gate_stage(plain_root, monkeypatch):
    # Instead of writing the gate byte, the parent swaps its write end of the
    # gate for /dev/null, which closes the pipe, so the child reads EOF. The
    # child must report the gate stage and exit, not spin on empty reads.
    parent = os.getpid()
    devnull = os.open(os.devnull, os.O_WRONLY)

    def write(fd, data):
        if not _in_child_of(parent) and data == b"1":
            os.dup2(devnull, fd)
            return 1
        return os.write(fd, data)

    _scoped_os(monkeypatch, write=write)
    try:
        sup, error = _failed_start(plain_root)
    finally:
        os.close(devnull)

    assert str(error) == (
        "payload failed to exec bubblewrap (error stage=gate type=EOFError errno=none)"
    )
    assert sup._process.poll() == 127


def test_failure_right_after_fork_kills_and_reaps_the_child(plain_root, monkeypatch):
    # The parent's first close after fork fails (after really closing the fd).
    # The child is recorded before that close, so the failed start kills and
    # reaps it instead of leaving it blocked at the gate.
    parent = os.getpid()
    failed = []

    def close(fd):
        os.close(fd)
        if not _in_child_of(parent) and not failed:
            failed.append(fd)
            raise OSError(errno.EIO, "injected close failure")

    _scoped_os(monkeypatch, close=close)
    before = _open_fds()

    sup, error = _failed_start(plain_root, expected=OSError)

    assert "injected close failure" in str(error)
    assert sup._process is not None
    assert sup._process.poll() == -signal.SIGKILL
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_stdio_failure_reports_stdio_stage(plain_root, monkeypatch):
    _scoped_os(monkeypatch, devnull="/nonexistent/forge-devnull")

    message = _start_error(plain_root)

    assert message == (
        "payload failed to exec bubblewrap (error stage=stdio type=FileNotFoundError errno=%d)"
        % errno.ENOENT
    )


@pytest.mark.parametrize("failure", [MemoryError, KeyboardInterrupt])
def test_child_still_reports_when_formatting_the_diagnostic_fails(plain_root, monkeypatch, failure):
    # Without the fallback the child writes nothing, the parent reads EOF and
    # treats the failed exec as a successful start. KeyboardInterrupt is not
    # an Exception, so a fallback that caught only Exception would miss it.
    def broken_diagnostic(stage, exc):
        raise failure

    _scoped_os(monkeypatch)
    monkeypatch.setattr(isolate, "_startup_diagnostic", broken_diagnostic)

    message = _start_error(plain_root, argv=_MISSING_BINARY)

    assert message == "payload failed to exec bubblewrap (error stage=unknown)"


def test_exec_status_non_ascii_bytes_become_question_marks(plain_root, monkeypatch):
    _scoped_os(monkeypatch)
    monkeypatch.setattr(isolate, "_startup_diagnostic", lambda stage, exc: b"\xc3\xa9")

    message = _start_error(plain_root, argv=_MISSING_BINARY)

    assert message == "payload failed to exec bubblewrap (??)"


def test_exec_status_read_stops_at_the_limit(plain_root, monkeypatch):
    # The child writes 300 bytes. Each parent read returns at most 150, so the
    # parent needs a second read, which must ask only for the 50 bytes left
    # under the limit.
    parent = os.getpid()
    requested = []

    def read(fd, size):
        if _in_child_of(parent):
            return os.read(fd, size)
        requested.append(size)
        return os.read(fd, min(size, 150))

    _scoped_os(monkeypatch, read=read)
    monkeypatch.setattr(isolate, "_startup_diagnostic", lambda stage, exc: b"x" * 300)

    message = _start_error(plain_root, argv=_MISSING_BINARY)

    assert message == "payload failed to exec bubblewrap (%s)" % ("x" * 200)
    assert requested == [200, 50]


def test_unplaced_child_still_running_at_deadline(plain_root, monkeypatch):
    # The placement deadline has passed before the first check, while the
    # child is blocked on the gate and has written no diagnostic.
    _scoped_os(monkeypatch)
    clock = iter([0.0])
    monkeypatch.setattr(
        isolate,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(clock, 100.0),
            sleep=time.sleep,
            monotonic_ns=time.monotonic_ns,
        ),
    )
    before = _open_fds()

    message = _start_error(plain_root)

    assert message == (
        "payload process was not placed in the run cgroup "
        "(child still running at deadline; no startup diagnostic)"
    )
    assert _open_fds() == before


def test_procs_open_failure_closes_the_pipes(plain_root, monkeypatch):
    _scoped_os(
        monkeypatch, procs_error=PermissionError(errno.EACCES, "injected cgroup.procs open failure")
    )
    before = _open_fds()

    message = _start_error(plain_root, expected=PermissionError)

    assert "injected cgroup.procs open failure" in message
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_fork_failure_closes_the_pipes_and_cgroup_procs(plain_root, monkeypatch):
    def fork():
        raise BlockingIOError(errno.EAGAIN, "injected fork failure")

    _scoped_os(monkeypatch, fork=fork)
    before = _open_fds()

    message = _start_error(plain_root, expected=BlockingIOError)

    assert "injected fork failure" in message
    assert _open_fds() == before
    assert not (plain_root / "forge-diag").exists()


def test_close_failure_during_cleanup_keeps_the_start_error(plain_root, monkeypatch):
    # Once the placement failure is known, every parent close fails with EIO
    # (after really closing the fd). The placement error must still be the
    # one raised, and every remaining fd must still be closed.
    parent = os.getpid()
    armed = []
    read_diagnostic = isolate._read_startup_diagnostic

    def arm_then_read(fd):
        armed.append(fd)
        return read_diagnostic(fd)

    def close(fd):
        os.close(fd)
        if armed and not _in_child_of(parent):
            raise OSError(errno.EIO, "injected close failure")

    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY, close=close)
    monkeypatch.setattr(isolate, "_read_startup_diagnostic", arm_then_read)
    before = _open_fds()

    message = _start_error(plain_root)

    assert message.startswith("payload process was not placed in the run cgroup (child exit 127;")
    assert _open_fds() == before


def test_start_failure_with_a_busy_cgroup_waits_once(plain_root, monkeypatch):
    # The real _remove_cgroup on the start path. rmdir fails with ENOTEMPTY
    # and moves the fake clock past the deadline, so each removal makes one
    # attempt. The placement error stays the raised error, the cleanup
    # failure is a note, and teardown makes one more attempt instead of a
    # second 10 s wait. Teardown can be retried once the cgroup goes.
    monkeypatch.setattr(Supervisor, "_remove_cgroup", _REAL_REMOVE_CGROUP)
    offset = [0.0]
    rmdir_calls = []
    busy = [True]

    def rmdir(path):
        rmdir_calls.append(path)
        if busy[0]:
            offset[0] += 100.0
            raise OSError(errno.ENOTEMPTY, "Directory not empty")
        shutil.rmtree(path)

    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY, rmdir=rmdir)
    monkeypatch.setattr(
        isolate,
        "time",
        SimpleNamespace(
            monotonic=lambda: time.monotonic() + offset[0],
            sleep=time.sleep,
            monotonic_ns=time.monotonic_ns,
        ),
    )
    sup = _supervisor(plain_root)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        with pytest.raises(IsolationError) as info:
            thread.wait_started(30)
    finally:
        _stop(thread, sup)

    detail = (
        "run cgroup was not removed before the deadline (last rmdir error type=OSError errno=%d)"
        % (errno.ENOTEMPTY)
    )
    assert type(info.value) is IsolationError
    assert str(info.value).startswith("payload process was not placed in the run cgroup")
    tagged = isolate._printable(
        "%s: %s%s" % (detail, isolate._root_tag(sup.cgroup_path), sup.cgroup_path)
    )[:400]
    assert info.value.__notes__[:-1] == [tagged]
    assert info.value.__notes__[-1].startswith("sandbox startup evidence: startup_ns=")
    assert sup.cleanup_error == detail
    assert len(rmdir_calls) == 1

    with pytest.raises(CgroupCleanupError):
        sup.teardown()
    assert len(rmdir_calls) == 2

    busy[0] = False
    sup.teardown()
    assert len(rmdir_calls) == 3
    assert sup.cleanup_error is None
    assert not os.path.exists(sup.cgroup_path)
    sup.teardown()
    assert len(rmdir_calls) == 3


@pytest.mark.parametrize(
    ("procs_flags", "argv", "prefix"),
    [
        (os.O_RDONLY, ("/bin/true",), "payload process was not placed"),
        (os.O_WRONLY, _MISSING_BINARY, "payload failed to exec bubblewrap"),
    ],
    ids=["placement", "exec"],
)
def test_failed_start_closes_each_parent_fd_exactly_once(
    plain_root, monkeypatch, procs_flags, argv, prefix
):
    # A second close of the same number could close an fd another thread
    # has just been given.
    parent = os.getpid()
    created = []
    closed = []

    def pipe():
        fds = os.pipe()
        if not _in_child_of(parent):
            created.extend(fds)
        return fds

    def scoped_open(path, flags, *args, **kwargs):
        if str(path).endswith("cgroup.procs"):
            fd = os.open(path, procs_flags | os.O_CREAT, 0o600)
        else:
            fd = os.open(path, flags, *args, **kwargs)
        if not _in_child_of(parent):
            created.append(fd)
        return fd

    def close(fd):
        if not _in_child_of(parent):
            closed.append(fd)
        os.close(fd)

    _scoped_os(monkeypatch, pipe=pipe, open=scoped_open, close=close)

    message = _start_error(plain_root, argv=argv)

    assert message.startswith(prefix)
    assert len(created) == 5
    assert Counter(closed) == Counter(created)


def test_successful_exec_raises_nothing_and_leaks_no_fd(plain_root, monkeypatch):
    _scoped_os(monkeypatch)
    before = _open_fds()
    sup = _supervisor(plain_root)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        thread.wait_started(30)
        assert sup.wait(timeout=30) == 0
    finally:
        _stop(thread, sup)
    sup.teardown()
    assert _open_fds() == before


# --- encoding and decoding -------------------------------------------------


def test_diagnostic_without_errno_says_none():
    assert isolate._startup_diagnostic("gate", KeyboardInterrupt()) == (
        b"error stage=gate type=KeyboardInterrupt errno=none"
    )


def test_diagnostic_is_bounded_to_200_bytes_of_ascii():
    long_type = type("\u00e9" + "X" * 400, (Exception,), {})

    raw = isolate._startup_diagnostic("exec", long_type())

    assert len(raw) == 200
    assert raw.startswith(b"error stage=exec type=?XXX")
    raw.decode("ascii")


def test_diagnostic_replaces_control_characters():
    # 0x20 and 0x7e are the printable edges; 0x1f, 0x7f, newline and ESC are not.
    odd_type = type("A\x1f \x7e\x7f\n\x1bB", (Exception,), {})

    raw = isolate._startup_diagnostic("exec", odd_type())

    assert raw == b"error stage=exec type=A? ~???B errno=none"


def test_read_diagnostic_does_not_block_on_open_writer():
    read_fd, write_fd = os.pipe()
    try:
        assert isolate._read_startup_diagnostic(read_fd) == "no startup diagnostic"
        os.write(write_fd, b"error stage=place type=OSError errno=13")
        assert isolate._read_startup_diagnostic(read_fd) == "error stage=place type=OSError errno=13"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_read_diagnostic_does_not_hide_a_read_error(monkeypatch):
    # Only "nothing buffered yet" (EAGAIN) means no diagnostic. Another read
    # error, such as EBADF, is a bug and must not read as an empty pipe.
    def read(fd, size):
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr(isolate, "os", _ScopedOs(read=read, set_blocking=lambda fd, flag: None))

    with pytest.raises(OSError) as info:
        isolate._read_startup_diagnostic(3)

    assert info.value.errno == errno.EBADF


def test_read_diagnostic_makes_the_pipe_non_blocking():
    # Data is already buffered, so this read returns either way; the pipe must
    # still be non-blocking, or a read with nothing buffered would wait on a
    # writer that another thread's fork keeps open.
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"x")
        assert isolate._read_startup_diagnostic(read_fd) == "x"
        assert os.get_blocking(read_fd) is False
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_error_detail_ignores_an_errno_that_is_not_an_int():
    # Only an int errno is printed; any other value reads as none.
    odd = type("Odd", (Exception,), {"errno": "x"})

    assert isolate._error_detail(odd()) == "type=Odd errno=none"


def test_read_diagnostic_keeps_only_200_bytes():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\xff" + b"y" * 500)
        text = isolate._read_startup_diagnostic(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert text == "?" + "y" * 199


def test_read_diagnostic_replaces_non_ascii_and_control_bytes():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\xc3\xa9|A\x1f \x7e\x7f\n\x1bB")
        text = isolate._read_startup_diagnostic(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert text == "??|A? ~???B"


@pytest.mark.parametrize(
    ("code", "text"),
    [
        (None, "still running at deadline"),
        (0, "exit 0"),
        (127, "exit 127"),
        (-9, "signal 9"),
        (-1, "signal 1"),
    ],
)
def test_describe_exit(code, text):
    assert isolate._describe_exit(code) == text


# --- run cgroup removal ----------------------------------------------------


def _removal(tmp_path, monkeypatch, rmdir_errors):
    """A supervisor whose rmdir raises each of ``rmdir_errors`` in turn, then succeeds.

    The clock reads 0 (deadline set to 10), then 9.99 (retry), then 10, the
    deadline itself. Sleeps are recorded on ``sup.sleeps`` instead of taken.
    """
    calls = []

    def rmdir(path):
        calls.append(path)
        if len(calls) <= len(rmdir_errors):
            raise rmdir_errors[len(calls) - 1]
        # cgroupfs removes a cgroup directory together with its interface
        # files. A path that cannot exist (too long) counts as removed.
        shutil.rmtree(path, ignore_errors=True)

    _scoped_os(monkeypatch, rmdir=rmdir)
    ticks = iter([0.0, 9.99])
    sleeps = []
    monkeypatch.setattr(
        isolate,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks, 10.0), sleep=sleeps.append),
    )
    sup = _supervisor(tmp_path)
    sup.sleeps = sleeps
    os.mkdir(sup.cgroup_path)
    return sup, calls


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.EBUSY, "Device or resource busy"),
        OSError(errno.ENOTEMPTY, "Directory not empty"),
        PermissionError(errno.EACCES, "Permission denied"),
    ],
    ids=["EBUSY", "ENOTEMPTY", "EACCES"],
)
def test_cgroup_still_present_at_deadline_is_reported(tmp_path, monkeypatch, error):
    sup, calls = _removal(tmp_path, monkeypatch, [error] * 5)

    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup()

    detail = "run cgroup was not removed before the deadline (last rmdir error type=%s errno=%d)" % (
        type(error).__name__,
        error.errno,
    )
    assert sup.cleanup_error == detail
    # The message carries the cgroup path after the detail, cut to 400
    # characters, so the expectation must be cut the same way rather than
    # assuming the host's temporary directory is short.
    note = isolate._printable(
        "%s: %s%s" % (detail, isolate._root_tag(sup.cgroup_path), sup.cgroup_path)
    )[:400]
    assert str(info.value) == note
    assert isinstance(info.value, IsolationError)
    # One attempt before the deadline check, one retry, then the deadline.
    assert calls == [sup.cgroup_path, sup.cgroup_path]


def test_probe_cleanup_receipts_stay_distinct_across_masked_roots(tmp_path, monkeypatch):
    # The probe-cleanup message names the probe cgroup, so it needs its own
    # host-independent check: an ASCII base hides the tag entirely.
    seen = []
    for name in ("\u79d1\u7814\u6240", "\u6d4b\u8bd5\u5ba4"):
        root = tmp_path / name
        root.mkdir()
        check = _probe(root, monkeypatch, False, OSError(errno.EBUSY, "Device or resource busy"))
        probe_path = str(root / ".forge-probe-fixed")
        monkeypatch.setattr(isolate.tempfile, "mkdtemp", lambda _p=probe_path, **k: _p)
        with pytest.raises(isolate.IsolationUnavailable) as info:
            check()
        # The probe failure is a note on the error that was already on its way.
        assert isolate._root_tag(probe_path) in info.value.__notes__[0]
        seen.append(isolate._root_tag(probe_path))
    assert seen[0] != seen[1]


def test_undecodable_cgroup_file_receipts_stay_distinct_across_masked_roots(tmp_path):
    # _read names the file it could not decode; two masked names must not read
    # the same in the receipt.
    seen = []
    for name in ("\u79d1\u7814\u6240", "\u6d4b\u8bd5\u5ba4"):
        path = tmp_path / name / "cgroup.procs"
        path.parent.mkdir()
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(isolate.IsolationUnavailable) as info:
            isolate._read(str(path))
        assert isolate._root_tag(str(path)) in info.value.detail
        seen.append((isolate._root_tag(str(path)), info.value.detail))
    assert seen[0][0] != seen[1][0]
    assert seen[0][1] != seen[1][1]


def test_run_cgroup_cleanup_receipts_stay_distinct_across_masked_roots(tmp_path, monkeypatch):
    # Two runs whose cgroup paths differ only in masked characters must not
    # produce the same cleanup receipt.
    monkeypatch.setattr(isolate, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bwrap"))
    details = []
    for name in ("\u79d1\u7814\u6240", "\u6d4b\u8bd5\u5ba4"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        sup, _ = _removal(tmp_path / name, monkeypatch, [OSError(errno.EBUSY, "busy")] * 5)
        sup.cgroup_path = os.path.join(str(tmp_path), name, "forge-run")
        with pytest.raises(CgroupCleanupError) as info:
            sup._remove_cgroup()
        assert isolate._root_tag(sup.cgroup_path) in str(info.value)
        details.append((isolate._root_tag(sup.cgroup_path), str(info.value)))
    assert details[0][0] != details[1][0]


def test_cleanup_error_is_bounded_and_printable(tmp_path, monkeypatch):
    odd = type("E\n" + "Z" * 400, (OSError,), {})
    sup, _ = _removal(tmp_path, monkeypatch, [odd(errno.EBUSY, "busy")] * 5)
    os.rmdir(sup.cgroup_path)
    sup.cgroup_path = os.path.join(str(tmp_path), "\x1b[31m" + "p" * 600)

    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup()

    message = str(info.value)
    assert len(message) == 400
    assert sup.cleanup_error is not None
    expected = isolate._printable(
        sup.cleanup_error
        + ": "
        + isolate._root_tag(sup.cgroup_path)
        + str(tmp_path)
        + "/?[31m"
        + "p" * 600
    )[:400]
    assert message == expected
    assert all(" " <= ch <= "~" for ch in message)
    assert isinstance(info.value.__cause__, odd)

    prefix = "run cgroup was not removed before the deadline (last rmdir error type=E?ZZZ"
    assert len(sup.cleanup_error) == 200
    assert sup.cleanup_error.startswith(prefix)
    assert all(" " <= ch <= "~" for ch in sup.cleanup_error)


def test_cleanup_error_keeps_type_and_errno_ahead_of_a_long_path(tmp_path, monkeypatch):
    # The message is cut to 400 characters; a long path must not push the
    # error type and errno past the cut.
    sup, _ = _removal(tmp_path, monkeypatch, [OSError(errno.EBUSY, "busy")] * 5)
    os.rmdir(sup.cgroup_path)
    sup.cgroup_path = os.path.join(str(tmp_path), "p" * 600)

    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup()

    detail = (
        "run cgroup was not removed before the deadline (last rmdir error type=OSError errno=%d)"
        % errno.EBUSY
    )
    message = str(info.value)
    assert len(message) == 400
    assert (
        message
        == isolate._printable(
            detail + ": " + isolate._root_tag(sup.cgroup_path) + str(tmp_path) + "/" + "p" * 600
        )[:400]
    )


def test_absent_cgroup_counts_as_removed(tmp_path, monkeypatch):
    sup, calls = _removal(tmp_path, monkeypatch, [FileNotFoundError(errno.ENOENT, "gone")])
    sup.cleanup_error = "stale text from an earlier removal"

    assert sup._remove_cgroup() is None
    assert sup.cleanup_error is None
    assert len(calls) == 1


def test_removal_retries_until_the_cgroup_goes(tmp_path, monkeypatch):
    sup, calls = _removal(tmp_path, monkeypatch, [OSError(errno.EBUSY, "busy")])
    sup.cleanup_error = "stale text from an earlier removal"

    assert sup._remove_cgroup() is None
    assert sup.cleanup_error is None
    assert len(calls) == 2
    assert sup.sleeps == [0.1]
    assert not os.path.exists(sup.cgroup_path)


@pytest.mark.parametrize(("force", "kills"), [(True, 2), (False, 1)])
def test_forced_removal_kills_again_between_retries(tmp_path, monkeypatch, force, kills):
    sup, _ = _removal(tmp_path, monkeypatch, [OSError(errno.EBUSY, "busy")])
    kill_file = os.path.join(sup.cgroup_path, "cgroup.kill")
    open(kill_file, "w").close()
    written = []
    monkeypatch.setattr(isolate, "_write", lambda path, value: written.append((path, value)))

    sup._remove_cgroup(force=force)

    assert written == [(kill_file, "1")] * kills


def test_teardown_reports_a_cgroup_left_behind(tmp_path, monkeypatch):
    sup, calls = _removal(tmp_path, monkeypatch, [OSError(errno.EBUSY, "busy")] * 5)

    with pytest.raises(CgroupCleanupError):
        sup.teardown()

    assert sup.cleanup_error.startswith("run cgroup was not removed before the deadline")
    assert len(calls) == 2


def _teardown_calls(tmp_path, monkeypatch, outcomes):
    """A supervisor whose _remove_cgroup records its arguments and plays ``outcomes``."""
    calls = []

    def remove(self, force=False, timeout=10):
        calls.append((force, timeout))
        outcome = outcomes.pop(0)
        if outcome is not None:
            self.cleanup_error = "busy"
            raise outcome
        self.cleanup_error = None

    monkeypatch.setattr(Supervisor, "_remove_cgroup", remove)
    return _supervisor(tmp_path), calls


def test_teardown_forces_removal_and_can_be_retried(tmp_path, monkeypatch):
    sup, calls = _teardown_calls(tmp_path, monkeypatch, [CgroupCleanupError("busy"), None])

    with pytest.raises(CgroupCleanupError):
        sup.teardown()
    sup.teardown()
    sup.teardown()

    # Forced each time; the retry after a timed-out removal makes one attempt
    # (timeout 0); once removed, teardown is done.
    assert calls == [(True, 10), (True, 0)]


def test_teardown_after_a_failed_start_removal_does_not_wait_again(tmp_path, monkeypatch):
    sup, calls = _teardown_calls(tmp_path, monkeypatch, [None])
    sup.cleanup_error = "run cgroup was not removed before the deadline"

    sup.teardown()

    assert calls == [(True, 0)]


def test_note_failure_never_replaces_the_error():
    error = RuntimeError("in flight")
    error.__notes__ = "not a list"  # makes add_note raise TypeError

    isolate.attach_note(error, "cleanup failed")

    assert error.__notes__ == "not a list"
    fresh = RuntimeError("in flight")
    isolate.attach_note(fresh, "cleanup failed")
    assert fresh.__notes__ == ["cleanup failed"]


def test_the_same_note_is_attached_once():
    # Supervisor.start notes a cleanup failure on the start error, and the
    # caller's own teardown can note the same text again.
    error = RuntimeError("in flight")

    isolate.attach_note(error, "cleanup failed")
    isolate.attach_note(error, "cleanup failed")
    isolate.attach_note(error, "another note")

    assert error.__notes__ == ["cleanup failed", "another note"]


def test_notes_that_share_a_long_prefix_are_both_kept():
    # A start failure and its teardown can fail on the same cgroup with
    # different errno values: the notes differ only in the errno.
    left = "run cgroup was not removed before the deadline (last rmdir error type=OSError errno="
    right = "): /sys/fs/cgroup/forge-run"
    error = RuntimeError("in flight")

    isolate.attach_note(error, left + "16" + right)
    isolate.attach_note(error, left + "39" + right)

    assert error.__notes__ == [left + "16" + right, left + "39" + right]


@pytest.mark.parametrize(
    "notes",
    [
        ("cleanup failed: /x", "cleanup failed"),
        ("cleanup failed", "cleanup failed: /x"),
        ("errno=16", "rmdir errno=16"),
        ("rmdir errno=16", "errno=16"),
    ],
)
def test_a_note_inside_another_note_is_still_added(notes):
    # Only an equal note is skipped, not one that shares a prefix or suffix
    # with another note, or is contained in it.
    error = RuntimeError("in flight")

    for note in notes:
        isolate.attach_note(error, note)

    assert error.__notes__ == list(notes)


@pytest.mark.parametrize(
    "notes",
    [
        ("a" + "x" * 400, "b" + "x" * 400),
        ("x" * 400 + "a", "x" * 400 + "b"),
        ("cleanup failed", "Cleanup failed"),
        ("cleanup failed", "cleanup failed "),
        ("first", "second", "first"),
    ],
)
def test_only_an_equal_note_is_skipped(notes):
    # attach_note does not cut notes, so the 401-character notes are kept whole
    # and differ only in their first or last character. Case and trailing
    # space count, and a repeated note keeps its first place.
    error = RuntimeError("in flight")

    for note in notes:
        isolate.attach_note(error, note)

    assert error.__notes__ == list(dict.fromkeys(notes))


def test_an_equal_note_built_at_runtime_is_skipped():
    # Notes are compared by value, not identity: the second note is a new
    # string object with the same text.
    note = "first"
    same_text = "".join(["fir", "st"])
    assert same_text == note
    assert same_text is not note
    error = RuntimeError("in flight")

    isolate.attach_note(error, note)
    isolate.attach_note(error, same_text)

    assert error.__notes__ == ["first"]


def test_a_note_anywhere_in_the_list_is_not_repeated():
    error = RuntimeError("in flight")

    isolate.attach_note(error, "first")
    isolate.attach_note(error, "second")
    isolate.attach_note(error, "first")
    isolate.attach_note(error, "second")

    assert error.__notes__ == ["first", "second"]


class _Interrupt(BaseException):
    """Not an Exception, like KeyboardInterrupt, but pytest reports it as a failure."""


def test_interrupt_while_noting_never_replaces_the_error():
    # Reading __notes__ raises a BaseException that is not an Exception: the
    # note is dropped, with no second attempt to add it, and the caller still
    # re-raises its own error.
    reads = []

    class Interrupting(RuntimeError):
        @property
        def __notes__(self):
            reads.append("__notes__")
            raise _Interrupt

    try:
        result = isolate.attach_note(Interrupting("in flight"), "cleanup failed")
    except _Interrupt:
        pytest.fail("attach_note let a BaseException from __notes__ escape")

    assert result is None
    assert reads == ["__notes__"]


# --- probe cleanup ----------------------------------------------------------


def _long_child(tmp_path, wanted=250):
    """A child name that keeps the whole path inside a safe length.

    The probe diagnostics are bounded at 200 and 400 characters, so the tests
    need a path longer than the bound. How much room is left depends on where
    pytest put its temporary root: a host that already sits near PATH_MAX leaves
    none, and adding a child there would fail for reasons unrelated to the code.
    """
    room = 3800 - len(os.fsencode(tmp_path)) - 2
    if room < 8:
        pytest.skip("temporary root leaves no room for a child name")
    return "r" * min(wanted, room)


def _probe(tmp_path, monkeypatch, exists, rmdir_error, create_error=None):
    monkeypatch.setattr(isolate, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bwrap"))
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: SimpleNamespace(stdout="cgroup2fs\n", stderr="", returncode=0),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )

    if create_error is not None:
        monkeypatch.setattr(
            isolate.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(create_error)
        )

    def rmdir(path):
        if rmdir_error is not None:
            raise rmdir_error
        os.rmdir(path)

    monkeypatch.setattr(
        isolate,
        "os",
        _ScopedOs(
            rmdir=rmdir,
            path=SimpleNamespace(exists=lambda p: exists, join=os.path.join, isdir=os.path.isdir),
        ),
    )
    return lambda: isolate.verify_isolation_support(str(tmp_path))


def test_probe_cgroup_left_behind_is_reported(tmp_path, monkeypatch):
    removed = []
    check = _probe(tmp_path, monkeypatch, True, OSError(errno.EBUSY, "Device or resource busy"))
    failing_rmdir = isolate.os.rmdir

    def record_failure(path):
        removed.append(path)
        return failing_rmdir(path)

    monkeypatch.setattr(isolate.os, "rmdir", record_failure)
    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert info.value.reason == "probe_cleanup_failed"
    assert len(removed) == 1
    assert re.fullmatch(re.escape(str(tmp_path)) + r"/\.forge-probe-\w+", removed[0])
    expected = "probe cgroup was not removed (type=OSError errno=%d): %s%s" % (
        errno.EBUSY,
        isolate._root_tag(removed[0]),
        removed[0],
    )
    assert info.value.detail == isolate._printable(expected)[:200]


def test_probe_cleanup_report_is_bounded(tmp_path, monkeypatch):
    # The detail is cut to 200 characters; the long path comes after the error
    # type and errno, so the cut takes only the path's end. The child name is
    # sized against the remaining path budget, so the test does not depend on
    # how deep the host's temporary directory already is.
    head = "probe cgroup was not removed (type=OSError errno=%d): " % errno.EBUSY
    root = tmp_path / _long_child(tmp_path)
    root.mkdir()
    check = _probe(root, monkeypatch, True, OSError(errno.EBUSY, "Device or resource busy"))
    # Pin the probe path so the identity tag is knowable; the message names the
    # probe cgroup, not the root it sits under.
    probe_path = str(root / ".forge-probe-fixed")
    monkeypatch.setattr(isolate.tempfile, "mkdtemp", lambda **kwargs: probe_path)

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert len(info.value.detail) == 200
    assert (
        info.value.detail == isolate._printable(head + isolate._root_tag(probe_path) + probe_path)[:200]
    )


def test_masked_root_receipts_stay_distinct_whatever_the_host(tmp_path, monkeypatch):
    # CI runs with an ASCII base, so a case that only masks through tmp_path
    # proves nothing there. The masked name is built explicitly and both the
    # root diagnostic and the run-cgroup cleanup message must carry a tag.
    monkeypatch.setattr(isolate, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bwrap"))
    assert isolate._root_tag("ascii-only") == ""
    assert isolate._root_tag("\u79d1\u7814\u6240") != isolate._root_tag("\u6d4b\u8bd5\u5ba4")
    from pathlib import Path as _Path

    assert isolate._root_tag(_Path("/x/\u79d1")) == isolate._root_tag("/x/\u79d1")

    for name in ("\u79d1\u7814\u6240", "\u6d4b\u8bd5\u5ba4"):
        masked = tmp_path / name
        masked.mkdir()
        failed = SimpleNamespace(
            stdout="",
            stderr="stat: cannot statx '%s': Permission denied" % masked,
            returncode=1,
        )
        monkeypatch.setattr(
            isolate,
            "subprocess",
            SimpleNamespace(
                run=lambda *a, _f=failed, **k: _f,
                SubprocessError=isolate.subprocess.SubprocessError,
            ),
        )
        with pytest.raises(isolate.IsolationUnavailable) as raised:
            isolate.verify_isolation_support(str(masked))
        tag = isolate._root_tag(str(masked))
        assert tag and tag in raised.value.detail

    # A control character is masked by _printable too, so it needs the tag even
    # though it is ASCII.
    assert isolate._root_tag("a\x1bb") != ""


def test_root_diagnostics_keep_their_cause_at_a_long_path(tmp_path, monkeypatch):
    # Every root-level diagnostic carries its cause before the path, so the
    # 200-character cut can shorten the path but never erase why it failed.
    root = tmp_path / _long_child(tmp_path)
    root.mkdir()
    monkeypatch.setattr(isolate, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bwrap"))
    timeout_cls = isolate.subprocess.TimeoutExpired

    def stubbed(stdout):
        monkeypatch.setattr(
            isolate,
            "subprocess",
            SimpleNamespace(
                run=lambda *a, **k: SimpleNamespace(stdout=stdout, stderr="", returncode=0),
                SubprocessError=isolate.subprocess.SubprocessError,
            ),
        )

    stubbed("ext4\n")
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert "not on a cgroup2 mount" in raised.value.detail
    assert "ext4" in raised.value.detail
    assert len(raised.value.detail) <= 200

    # A silent success has nothing to report, and saying "got ''" hides that.
    stubbed("")
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert "reported no filesystem type" in raised.value.detail
    assert len(raised.value.detail) <= 200

    stubbed("cgroup2fs\n")
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root / "absent"))
    # A path that is absent and a path that is a file are different faults.
    assert "does not exist" in raised.value.detail
    assert isolate._printable(str(root / "absent"))[:80] in raised.value.detail
    assert len(raised.value.detail) <= 200

    present_file = root / "not-a-dir"
    present_file.write_text("x")
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(present_file))
    assert "is not a directory" in raised.value.detail
    assert len(raised.value.detail) <= 200

    # A timeout's own text carries the whole command line, so this is the case
    # where a path-first message would lose the reason behind the path.
    timeout = timeout_cls(cmd=["stat", "-fc", "%T", str(root)], timeout=10)
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: (_ for _ in ()).throw(timeout),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    head = "cannot stat cgroup root (type=TimeoutExpired errno=none): "
    assert raised.value.detail.startswith(head)
    assert len(raised.value.detail) == 200

    # A stub without an exit status is not evidence of success.
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: SimpleNamespace(stdout="cgroup2fs\n", stderr=""),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith("cannot stat cgroup root (no exit status): ")

    failed = SimpleNamespace(
        stdout="",
        # GNU stat puts its path ahead of the reason; the message must lift the
        # reason out, or the cut at a long root would trade the reason for it.
        stderr="stat: cannot read file system information for '%s': No such file or directory" % root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: failed,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (exit 1: No such file or directory): "
    )
    assert len(raised.value.detail) == 200

    # A second reason proves the parse reads the reason rather than a literal,
    # and the reason a bounded message carries must never be the path itself.
    denied = SimpleNamespace(
        stdout="",
        stderr="stat: cannot read file system information for '%s': Permission denied" % root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: denied,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: Permission denied): ")
    assert isolate._printable(str(root))[:40] not in raised.value.detail.split("): ")[0]

    # stderr that says nothing falls back to the exit status instead of a blank.
    silent = SimpleNamespace(stdout="", stderr="", returncode=2)
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: silent,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 2: exit 2): ")

    # Output that is not UTF-8 must surface as this error, not as a decode crash
    # escaping the caller's except IsolationUnavailable.
    undecodable_output = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: (_ for _ in ()).throw(undecodable_output),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (type=UnicodeDecodeError errno=none): "
    )

    # A reason that itself contains ": " separates the two parses: keeping the
    # text after the quoted path preserves it whole, while taking the last
    # ": " segment would keep only "bad offset".
    compound = SimpleNamespace(
        stdout="",
        stderr="stat: cannot statx '%s': Invalid argument: bad offset" % root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: compound,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (exit 1: Invalid argument: bad offset): "
    )

    # A root whose name contains a quote cannot appear verbatim in coreutils'
    # message: it switches to double quotes (and above ~300 bytes backslash-
    # escapes the quote). Here the marker split does not fire, and the
    # last-segment fallback still yields the reason.
    quoted_root = root / "a': b"
    quoted_root.mkdir()
    quoted = SimpleNamespace(
        stdout="",
        stderr='stat: cannot statx "%s": Permission denied' % quoted_root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: quoted,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(quoted_root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: Permission denied): ")

    # The longer, escaped form: the path no longer appears verbatim at all, so
    # only the fallback can produce a reason.
    escaped = SimpleNamespace(
        stdout="",
        stderr='stat: cannot read file system information for "%s": Permission denied'
        % str(quoted_root).replace("'", "\\'"),
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: escaped,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(quoted_root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: Permission denied): ")

    # A root that merely prefixes another path on the same line must not be
    # taken for the separator: only a quoted occurrence counts.
    sibling = SimpleNamespace(
        stdout="",
        stderr="stat: cannot statx '%ssibling': Permission denied" % quoted_root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: sibling,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(quoted_root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: Permission denied): ")

    # A warning line before the diagnostic must not become the reason.
    multiline = SimpleNamespace(
        stdout="",
        stderr="stat: warning: locale fallback\nstat: cannot statx '%s': No such file or directory"
        % root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: multiline,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (exit 1: No such file or directory): "
    )

    # The tool failing to launch at all has its own diagnostic.
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError(2, "stat")),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert "FileNotFoundError" in raised.value.detail
    assert len(raised.value.detail) <= 200

    # A warning printed after the diagnostic must not become the reason: stat's
    # own line is the one that quotes the root.
    trailing = SimpleNamespace(
        stdout="",
        stderr="stat: cannot statx '%s': No such file or directory\nlocale: warning: fallback" % root,
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: trailing,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (exit 1: No such file or directory): "
    )

    # Two roots that mask to the same printable text must still be told apart
    # in a receipt, so the message carries a stable tag.
    ascii_detail = None
    for name in ("\u79d1\u7814\u6240", "\u6d4b\u8bd5\u5ba4"):
        masked_root = root / name
        masked_root.mkdir()
        missing = SimpleNamespace(
            stdout="",
            stderr="stat: cannot statx '%s': Permission denied" % masked_root,
            returncode=1,
        )
        monkeypatch.setattr(
            isolate,
            "subprocess",
            SimpleNamespace(
                run=lambda *a, _m=missing, **k: _m,
                SubprocessError=isolate.subprocess.SubprocessError,
            ),
        )
        with pytest.raises(isolate.IsolationUnavailable) as raised:
            isolate.verify_isolation_support(str(masked_root))
        located = re.search(r"#([0-9a-f]{16}) ", raised.value.detail)
        assert located, raised.value.detail
        current = located.group(1)
        assert current != ascii_detail, (current, ascii_detail)
        ascii_detail = current

    # A warning that merely mentions the root, printed after a diagnostic that
    # also mentions it, must not displace the diagnostic.
    two_lines = SimpleNamespace(
        stdout="",
        stderr="stat: cannot statx '%s': No such file or directory\n"
        "hint: check '%s' for a stale mount" % (root, root),
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: two_lines,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith(
        "cannot stat cgroup root (exit 1: No such file or directory): "
    )

    # A reason that mentions the root again must still read as a reason: nothing
    # of the message may be rewritten into a placeholder that reaches a receipt.
    twice = SimpleNamespace(
        stdout="",
        stderr="stat: cannot statx '%s': Permission denied for '%s'" % (root, root),
        returncode=1,
    )
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: twice,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: Permission denied for ")
    assert "<cgroup-root>" not in raised.value.detail

    # stderr that carries only the path falls back to the exit status.
    path_only = SimpleNamespace(stdout="", stderr="stat: '%s'" % root, returncode=1)
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: path_only,
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
    )
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate.verify_isolation_support(str(root))
    assert raised.value.detail.startswith("cannot stat cgroup root (exit 1: exit 1): ")

    undecodable = root / "memory.max"
    undecodable.write_bytes(b"\xff\xfe")
    with pytest.raises(isolate.IsolationUnavailable) as raised:
        isolate._read(str(undecodable))
    assert "not UTF-8 decodable" in raised.value.detail
    assert "UnicodeDecodeError" in raised.value.detail
    assert len(raised.value.detail) <= 200


def test_probe_creation_failure_is_bounded(tmp_path, monkeypatch):
    # Creating the probe can fail on a deep path. The message keeps the cause
    # first and the path last, so the cut cannot erase the error type, and the
    # child name is sized against the remaining budget so the test itself does
    # not depend on how deep the host's temporary directory already is.
    root = tmp_path / _long_child(tmp_path)
    root.mkdir()
    boom = OSError(errno.ENAMETOOLONG, "File name too long")
    check = _probe(root, monkeypatch, False, None, create_error=boom)

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    head = "cannot create child cgroup (type=OSError errno=%d): " % errno.ENAMETOOLONG
    assert info.value.detail.startswith(head + isolate._root_tag(str(root)))
    budget = 200 - len(head) - len(isolate._root_tag(str(root)))
    assert isolate._printable(str(root))[:budget] in info.value.detail
    assert len(info.value.detail) == 200


def test_probe_cleanup_failure_is_a_note_on_the_probe_error(tmp_path, monkeypatch):
    removed = []
    check = _probe(tmp_path, monkeypatch, False, OSError(errno.EBUSY, "Device or resource busy"))
    failing_rmdir = isolate.os.rmdir

    def record(path):
        removed.append(path)
        return failing_rmdir(path)

    monkeypatch.setattr(isolate.os, "rmdir", record)
    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert info.value.reason == "isolation_unavailable"
    assert "lacks memory.max" in info.value.detail
    assert len(removed) == 1
    assert len(info.value.__notes__) == 1
    # The note names the cgroup that survived, cut to the diagnostic limit, so
    # the assertion must not assume a short temporary directory or plain ASCII
    # in it. The recorded path is checked against the shape the probe builds,
    # so an expectation derived from the code's own value cannot pass.
    assert re.fullmatch(re.escape(str(tmp_path)) + r"/\.forge-probe-\w+", removed[0])
    head = "probe cgroup was not removed (type=OSError errno=%d): " % errno.EBUSY
    note = info.value.__notes__[0]
    assert note == isolate._printable(head + isolate._root_tag(removed[0]) + removed[0])[:200]


def test_probe_cgroup_removed_is_silent(tmp_path, monkeypatch):
    check = _probe(tmp_path, monkeypatch, True, None)

    assert check() is None
    assert os.listdir(tmp_path) == []


def test_probe_cgroup_already_gone_is_silent(tmp_path, monkeypatch):
    check = _probe(tmp_path, monkeypatch, True, FileNotFoundError(errno.ENOENT, "gone"))

    assert check() is None


def test_probe_cgroup_already_gone_adds_no_note_to_the_probe_error(tmp_path, monkeypatch):
    check = _probe(tmp_path, monkeypatch, False, FileNotFoundError(errno.ENOENT, "gone"))

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert info.value.reason == "isolation_unavailable"
    assert not hasattr(info.value, "__notes__")


def test_failed_start_keeps_its_error_and_notes_the_cleanup_failure(plain_root, monkeypatch):
    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY)

    def stuck(self, force=False):
        assert force
        raise CgroupCleanupError("run cgroup was not removed before the deadline: forge-diag")

    monkeypatch.setattr(Supervisor, "_remove_cgroup", stuck)
    sup = _supervisor(plain_root)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        with pytest.raises(IsolationError) as info:
            thread.wait_started(30)
    finally:
        _stop(thread, sup)

    assert type(info.value) is IsolationError
    assert str(info.value).startswith(
        "payload process was not placed in the run cgroup (child exit 127;"
    )
    assert info.value.__notes__[:-1] == ["run cgroup was not removed before the deadline: forge-diag"]
    assert info.value.__notes__[-1].startswith("sandbox startup evidence: startup_ns=")


# Measurements are evidence only, not a substitute for native qualification.
def test_startup_timing_excludes_removal_and_keeps_placement_evidence(plain_root, monkeypatch):
    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY, rmdir=shutil.rmtree)
    monkeypatch.setattr(Supervisor, "_remove_cgroup", _REAL_REMOVE_CGROUP)
    ticks = iter((100, 140, 150, 250))
    monkeypatch.setattr(isolate, "_diagnostic_clock_ns", lambda: next(ticks))

    sup, error = _failed_start(plain_root)

    assert sup.startup_elapsed_ns == 40
    assert sup.cgroup_removal_ns == 100
    assert sup.placement_verified is False
    assert sup.startup_gate_opened is False
    assert error.__notes__[-1] == (
        "sandbox startup evidence: startup_ns=40 cgroup_removal_ns=100 "
        "placement_verified=False gate_opened=False"
    )
    assert "stage=place type=OSError errno=%d" % errno.EBADF in str(error)


def test_exec_failure_records_observed_placement_and_opened_gate(plain_root, monkeypatch):
    _scoped_os(monkeypatch)
    sup, error = _failed_start(plain_root, argv=_MISSING_BINARY)

    assert sup.startup_elapsed_ns is not None
    assert sup.placement_verified is True
    assert sup.startup_gate_opened is True
    assert error.__notes__[-1].endswith("placement_verified=True gate_opened=True")
    assert "stage=exec type=FileNotFoundError errno=%d" % errno.ENOENT in str(error)


def test_failed_gate_write_is_not_reported_as_open(plain_root, monkeypatch):
    parent = os.getpid()

    def write(fd, data):
        if os.getpid() == parent and data == b"1":
            raise OSError(errno.EPIPE, "gate write failed")
        return os.write(fd, data)

    _scoped_os(monkeypatch, write=write)
    sup, error = _failed_start(plain_root, expected=OSError)

    assert error.errno == errno.EPIPE
    assert sup.placement_verified is True
    assert sup.startup_gate_opened is False
    assert error.__notes__[-1].endswith("placement_verified=True gate_opened=False")


@pytest.mark.parametrize("value", [-1, 2**63, True, 1.5, float("inf"), None, "bad"])
def test_diagnostic_clock_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setattr(isolate, "time", SimpleNamespace(monotonic_ns=lambda: value))
    assert isolate._diagnostic_clock_ns() is None


def test_clock_failure_keeps_early_start_error_and_resets_old_evidence(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path)
    sup._bind_thread(threading.current_thread())
    sup.placement_verified = sup.startup_gate_opened = True
    sup.cgroup_removal_ns = 999
    error = IsolationError("original")
    cause = OSError(errno.EACCES, "original cause")
    error.__cause__ = cause

    def clock():
        raise KeyboardInterrupt("diagnostic clock failed")

    def fail():
        raise error

    monkeypatch.setattr(isolate, "time", SimpleNamespace(monotonic_ns=clock))
    monkeypatch.setattr(sup, "_start", fail)
    with pytest.raises(IsolationError) as info:
        sup.start()

    assert info.value is error
    assert error.__cause__ is cause
    assert sup.startup_elapsed_ns is None
    assert sup.cgroup_removal_ns is None
    assert error.__notes__ == [
        "sandbox startup evidence: startup_ns=None cgroup_removal_ns=None "
        "placement_verified=False gate_opened=False"
    ]


def test_removal_failure_keeps_original_error_and_its_duration(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path)
    error = CgroupCleanupError("busy")
    cause = OSError(errno.EBUSY, "busy")
    error.__cause__ = cause
    ticks = iter((20, 70))
    monkeypatch.setattr(isolate, "_diagnostic_clock_ns", lambda: next(ticks))

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(sup, "_remove_cgroup_owned", fail)
    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup(force=True)

    assert info.value is error
    assert error.__cause__ is cause
    assert sup.cgroup_removal_ns == 50
    assert error.__notes__ == ["cgroup removal evidence: elapsed_ns=50"]


def test_backwards_diagnostic_clock_is_unknown(monkeypatch):
    monkeypatch.setattr(isolate, "_diagnostic_clock_ns", lambda: 9)
    assert isolate._diagnostic_elapsed_ns(10) is None


def test_failed_note_does_not_replace_start_error(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path)
    sup._bind_thread(threading.current_thread())
    error = IsolationError("original")
    error.__notes__ = "invalid notes container"

    def fail():
        raise error

    monkeypatch.setattr(sup, "_start", fail)
    with pytest.raises(IsolationError) as info:
        sup.start()
    assert info.value is error
    assert error.__notes__ == "invalid notes container"


def test_zero_byte_gate_write_is_not_reported_as_open(plain_root, monkeypatch):
    parent = os.getpid()

    def write(fd, data):
        if os.getpid() == parent and data == b"1":
            return 0
        return os.write(fd, data)

    _scoped_os(monkeypatch, write=write)
    sup, error = _failed_start(plain_root)

    assert sup.placement_verified is True
    assert sup.startup_gate_opened is False
    assert "error stage=gate type=EOFError" in str(error)


@pytest.mark.parametrize("helper", ["_finish_startup_measurement", "_diagnostic_elapsed_ns"])
def test_failed_measurement_cannot_skip_cleanup(plain_root, monkeypatch, helper):
    _scoped_os(monkeypatch, procs_flags=os.O_RDONLY)

    def fail(*args):
        raise MemoryError("measurement failed")

    target = Supervisor if helper == "_finish_startup_measurement" else isolate
    monkeypatch.setattr(target, helper, fail)
    sup, error = _failed_start(plain_root)

    assert "stage=place type=OSError errno=%d" % errno.EBADF in str(error)
    assert not os.path.exists(sup.cgroup_path)
    assert sup._process.poll() is not None


def test_failed_removal_measurement_cannot_replace_cleanup_error(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path)
    error = CgroupCleanupError("busy")

    def fail(**kwargs):
        raise error

    def broken_measurement(start):
        raise MemoryError("measurement failed")

    monkeypatch.setattr(sup, "_remove_cgroup_owned", fail)
    monkeypatch.setattr(isolate, "_diagnostic_elapsed_ns", broken_measurement)
    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup()
    assert info.value is error
    assert sup.cgroup_removal_ns is None


def test_startup_failure_note_is_bounded(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path)
    sup._bind_thread(threading.current_thread())
    error = IsolationError("original")

    def fail():
        sup.startup_elapsed_ns = sup.cgroup_removal_ns = 2**63 - 1
        sup._startup_measured = True
        raise error

    monkeypatch.setattr(sup, "_start", fail)
    with pytest.raises(IsolationError):
        sup.start()
    assert len(error.__notes__[0]) <= isolate._STARTUP_DIAGNOSTIC_LIMIT
    assert all(" " <= char <= "~" for char in error.__notes__[0])
    assert error.__notes__[0].endswith("placement_verified=False gate_opened=False")


def test_wrong_thread_start_does_not_overwrite_owned_attempt_evidence(tmp_path):
    sup = _supervisor(tmp_path)
    sup._startup_started_ns = 100
    sup._startup_measured = True
    sup.startup_elapsed_ns = 20
    sup.cgroup_removal_ns = 30
    sup.placement_verified = sup.startup_gate_opened = True
    with pytest.raises(IsolationError, match="dedicated long-lived"):
        sup.start()
    assert sup._startup_started_ns == 100
    assert sup._startup_measured is True
    assert sup.startup_elapsed_ns == 20
    assert sup.cgroup_removal_ns == 30
    assert sup.placement_verified is True
    assert sup.startup_gate_opened is True
