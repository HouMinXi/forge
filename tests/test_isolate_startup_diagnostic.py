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
    assert info.value.__notes__ == ["%s: %s" % (detail, sup.cgroup_path)]
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
    assert str(info.value) == "%s: %s" % (detail, sup.cgroup_path)
    assert isinstance(info.value, IsolationError)
    # One attempt before the deadline check, one retry, then the deadline.
    assert calls == [sup.cgroup_path, sup.cgroup_path]


def test_cleanup_error_is_bounded_and_printable(tmp_path, monkeypatch):
    odd = type("E\n" + "Z" * 400, (OSError,), {})
    sup, _ = _removal(tmp_path, monkeypatch, [odd(errno.EBUSY, "busy")] * 5)
    os.rmdir(sup.cgroup_path)
    sup.cgroup_path = os.path.join(str(tmp_path), "\x1b[31m" + "p" * 600)

    with pytest.raises(CgroupCleanupError) as info:
        sup._remove_cgroup()

    message = str(info.value)
    assert len(message) == 400
    assert message.startswith(sup.cleanup_error + ": " + str(tmp_path) + "/?[31mppp")
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
    assert message.startswith(detail + ": " + str(tmp_path) + "/ppp")


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


def _probe(tmp_path, monkeypatch, exists, rmdir_error):
    monkeypatch.setattr(isolate, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bwrap"))
    monkeypatch.setattr(
        isolate,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: SimpleNamespace(stdout="cgroup2fs\n"),
            SubprocessError=isolate.subprocess.SubprocessError,
        ),
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
    check = _probe(tmp_path, monkeypatch, True, OSError(errno.EBUSY, "Device or resource busy"))

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert info.value.reason == "probe_cleanup_failed"
    assert re.fullmatch(
        r"probe cgroup was not removed \(type=OSError errno=%d\): %s/\.forge-probe-\w+"
        % (errno.EBUSY, re.escape(str(tmp_path))),
        info.value.detail,
    )
    assert len(info.value.detail) <= 200


def test_probe_cleanup_report_is_bounded(tmp_path, monkeypatch):
    # The detail is cut to 200 characters; the 250-character path comes after
    # the error type and errno, so the cut takes only the path's end.
    root = tmp_path / ("r" * 250)
    root.mkdir()
    check = _probe(root, monkeypatch, True, OSError(errno.EBUSY, "Device or resource busy"))

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    head = "probe cgroup was not removed (type=OSError errno=%d): " % errno.EBUSY
    assert len(info.value.detail) == 200
    assert info.value.detail == (head + str(root))[:200]


def test_probe_cleanup_failure_is_a_note_on_the_probe_error(tmp_path, monkeypatch):
    check = _probe(tmp_path, monkeypatch, False, OSError(errno.EBUSY, "Device or resource busy"))

    with pytest.raises(isolate.IsolationUnavailable) as info:
        check()

    assert info.value.reason == "isolation_unavailable"
    assert "lacks memory.max" in info.value.detail
    assert len(info.value.__notes__) == 1
    assert info.value.__notes__[0].startswith(
        "probe cgroup was not removed (type=OSError errno=%d): %s/" % (errno.EBUSY, tmp_path)
    )


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
    assert info.value.__notes__ == ["run cgroup was not removed before the deadline: forge-diag"]
