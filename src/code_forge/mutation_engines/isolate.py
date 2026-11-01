"""Bubblewrap sandbox supervisor (spec trust-and-execution boundary).

Each run executes inside its own bubblewrap sandbox: user/pid/network/
uts/ipc namespaces unshared, no network route, a private tmpfs-backed
workspace, and resource limits applied to an owned cgroup v2 child
BEFORE the payload starts.  Limits (memory, pids, zero swap) are set
and read back; a host that cannot apply them maps to the
specification's isolation_unavailable refusal, never a degraded run.

Gate mechanism: the forked child writes its own pid into the run
cgroup and blocks on a gate pipe inside preexec (supervisor code, not
a shell or interpreter in the trusted path); only after the limits are
set and read back does the supervisor open the gate, letting the child
exec bubblewrap.  The payload therefore never runs outside the limits.

Parent-death watchdog: bubblewrap's --die-with-parent binds the death
signal to the thread that forked it, so the supervisor may only be
started from a dedicated long-lived SupervisorThread.  Starting from
any other thread (including a short-lived one) is refused.
"""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass

from code_forge.mutation_engines.schemas import valid_identifier

_REQUIRED_CONTROLLERS = ("memory", "pids")
_CGROUP2_MAGIC = "cgroup2fs"


class IsolationUnavailable(Exception):
    """The host cannot provide the required isolation envelope."""

    def __init__(self, detail: str, reason: str = "isolation_unavailable") -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail
        # Set by the caller when tearing down after this error also failed
        # to remove the run cgroup.
        self.cleanup_failure: CgroupCleanupError | None = None
        # Probe ownership is separate from the payload run cgroup.
        self.probe_cleanup_failure: ProbeCleanupError | None = None


class IsolationError(Exception):
    """A supervisor usage or lifecycle error."""


class ProbeCleanupError(IsolationError):
    """The capability probe cgroup could not be removed."""


class CgroupCleanupError(IsolationError):
    """The run cgroup was still present when the removal deadline passed.

    When the payload itself finished, the caller attaches what it measured
    (receipt, exit code, timeout flag) so the evidence is not lost.
    """

    receipt: object | None = None
    exit_code: int | None = None
    timed_out: bool | None = None


def verify_isolation_support(cgroup_root: str) -> None:
    """Raise IsolationUnavailable unless the delegated root is usable."""
    if shutil.which("bwrap") is None:
        raise IsolationUnavailable("bubblewrap (bwrap) binary not found")
    try:
        fs_type = subprocess.run(
            ["stat", "-fc", "%T", cgroup_root],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationUnavailable("cannot stat cgroup root: %s" % exc) from exc
    if fs_type != _CGROUP2_MAGIC:
        raise IsolationUnavailable(
            "cgroup root %r is not on a cgroup2 mount (got %r)" % (cgroup_root, fs_type)
        )
    if not os.path.isdir(cgroup_root):
        raise IsolationUnavailable("cgroup root %r is not a directory" % cgroup_root)
    try:
        probe = tempfile.mkdtemp(prefix=".forge-probe-", dir=cgroup_root)
    except OSError as exc:
        raise IsolationUnavailable(
            "cannot create child cgroup under %r: %s" % (cgroup_root, exc)
        ) from exc
    failure: BaseException | None = None
    try:
        for fname in ("memory.max", "pids.max", "memory.swap.max", "cgroup.procs"):
            if not os.path.exists(os.path.join(probe, fname)):
                raise IsolationUnavailable(
                    "delegated cgroup lacks %s (controllers memory+pids required)" % fname
                )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            os.rmdir(probe)
        except FileNotFoundError:
            # Already gone: nothing was left behind.
            pass
        except OSError as exc:
            # A probe cgroup left behind is a cleanup failure (#31): report it,
            # without replacing an error that is already on its way out. The
            # path comes last, so a long one cannot cut off the type and errno.
            detail = _bounded("probe cgroup was not removed (%s): %s" % (_error_detail(exc), probe))
            cleanup = ProbeCleanupError(detail)
            cleanup.__cause__ = exc
            if failure is None:
                unavailable = IsolationUnavailable(detail, reason="probe_cleanup_failed")
                unavailable.probe_cleanup_failure = cleanup
                raise unavailable from exc
            if isinstance(failure, IsolationUnavailable):
                failure.probe_cleanup_failure = cleanup
            attach_note(failure, detail)


@dataclass(frozen=True)
class SandboxSpec:
    """One sandboxed run: command, limits, workspace and environment."""

    run_id: str
    command: tuple[str, ...]
    cwd: str
    memory_mb: int
    pids: int
    workspace_mb: int
    env: tuple[tuple[str, str], ...] = ()
    workspace_host: str = ""
    runtime_root: str | None = None
    extra_ro_binds: tuple[tuple[str, str], ...] = ()
    extra_rw_binds: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not valid_identifier(self.run_id):
            raise ValueError("sandbox run_id must be an identifier, got %r" % (self.run_id,))
        if not self.command:
            raise ValueError("sandbox command must be nonempty")
        if self.memory_mb <= 0 or self.pids <= 0 or self.workspace_mb <= 0:
            raise ValueError("sandbox limits must be positive")
        for host, inner in self.extra_ro_binds + self.extra_rw_binds:
            if not host.startswith("/") or not inner.startswith("/"):
                raise ValueError("extra bind paths must be absolute, got %r -> %r" % (host, inner))


def _write(path: str, value: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except UnicodeDecodeError as exc:
        raise IsolationUnavailable("cgroup file %r is not UTF-8 decodable: %s" % (path, exc)) from exc


# Bytes of child startup diagnostic the parent keeps (#31).
_STARTUP_DIAGNOSTIC_LIMIT = 200
# A cleanup error names the cgroup path after the 200-character detail.
_CLEANUP_MESSAGE_LIMIT = 400


def _printable(text: str) -> str:
    """Replace every character outside printable ASCII (0x20-0x7e) with '?'."""
    return "".join(ch if " " <= ch <= "~" else "?" for ch in text)


def _error_detail(exc: BaseException) -> str:
    """Name an exception by type and errno, e.g. ``type=OSError errno=16``."""
    code = getattr(exc, "errno", None)
    return "type=%s errno=%s" % (type(exc).__name__, code if isinstance(code, int) else "none")


def _bounded(text: str) -> str:
    """Printable ASCII, cut to the diagnostic limit, for messages built from paths or errors."""
    return _printable(text)[:_STARTUP_DIAGNOSTIC_LIMIT]


def attach_note(exc: BaseException, note: str) -> None:
    """Add ``note`` to ``exc`` without ever replacing it.

    A note already on ``exc`` is not added again: a start failure noted by
    Supervisor.start can be noted with the same cleanup failure by the
    caller's teardown. A failure while recording the note (for example
    MemoryError) is dropped, so the error in flight is the one that
    propagates.
    """
    try:
        if note in getattr(exc, "__notes__", ()):
            return
        BaseException.add_note(exc, note)
    except BaseException:  # noqa: BLE001, S110 - a note must never mask the failure
        pass


def _startup_diagnostic(stage: str, exc: BaseException) -> bytes:
    """Encode why the forked child stopped before exec, bounded and printable ASCII."""
    text = "error stage=%s %s" % (stage, _error_detail(exc))
    return _printable(text).encode("ascii")[:_STARTUP_DIAGNOSTIC_LIMIT]


def _decode_startup_diagnostic(raw: bytes) -> str:
    """Decode child diagnostic bytes; each byte outside printable ASCII becomes '?'."""
    return _printable(raw.decode("latin-1"))


def _describe_exit(code: int | None) -> str:
    """Describe a child's exit status for a startup error message."""
    if code is None:
        return "still running at deadline"
    if code < 0:
        return "signal %d" % -code
    return "exit %d" % code


def _read_startup_diagnostic(fd: int) -> str:
    """Read what a stopped child left in the status pipe without blocking.

    Another thread's fork can hold a copy of the write end, so EOF is not
    guaranteed; a non-blocking read takes only what is already buffered.
    """
    os.set_blocking(fd, False)
    try:
        raw = os.read(fd, _STARTUP_DIAGNOSTIC_LIMIT)
    except BlockingIOError:
        raw = b""
    if not raw:
        return "no startup diagnostic"
    return _decode_startup_diagnostic(raw)


class _ChildProcess:
    """Minimal forked-child handle (poll/wait/kill) without subprocess."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._status: int | None = None
        self._lock = threading.Lock()

    def poll(self) -> int | None:
        # Two threads may reap concurrently (the supervisor thread and a
        # caller of Supervisor.wait); only one waitpid can win.  The loser
        # gets ChildProcessError and must not clobber the winner's status.
        with self._lock:
            if self._status is not None:
                return self._status
            try:
                done, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return self._status
            if done == self.pid:
                self._status = os.waitstatus_to_exitcode(status)
            return self._status

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.pid, timeout)
            time.sleep(0.02)

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Supervisor:
    """Owns one sandboxed run and its cgroup; starts from its thread only."""

    def __init__(self, spec: SandboxSpec, cgroup_root: str) -> None:
        self.spec = spec
        self.cgroup_root = cgroup_root
        self.cgroup_path = os.path.join(cgroup_root, "forge-" + spec.run_id)
        self.limits_readback: dict[str, str] = {}
        self.gate_opened_monotonic_ns = 0
        self.payload_pid = 0
        self._process: _ChildProcess | None = None
        self._owner_thread: SupervisorThread | None = None
        self._torn_down = False
        # Set when the run cgroup outlived its removal deadline.
        self.cleanup_error: str | None = None

    def _bind_thread(self, thread: "SupervisorThread") -> None:
        self._owner_thread = thread

    def _bwrap_argv(self) -> list[str]:
        spec = self.spec
        argv = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--unshare-ipc",
            "--clearenv",
        ]
        if spec.runtime_root:
            argv += ["--ro-bind", spec.runtime_root, "/"]
        else:
            for d in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"):
                argv += ["--ro-bind", d, d]
        # pytest capture opens /dev/null; --unshare-pid needs a private /proc
        # or the payload cannot see its own pid namespace.
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]  # noqa: S108
        argv += ["--tmpfs", "/workspace"]
        if spec.workspace_host:
            argv += ["--bind", spec.workspace_host, "/workspace"]
        for host, inner in spec.extra_ro_binds:
            argv += ["--ro-bind", host, inner]
        for host, inner in spec.extra_rw_binds:
            argv += ["--bind", host, inner]
        for key, value in spec.env:
            argv += ["--setenv", key, value]
        argv += ["--chdir", spec.cwd, "--"]
        argv += list(spec.command)
        return argv

    def start(self) -> None:
        thread = threading.current_thread()
        if self._owner_thread is None or thread is not self._owner_thread:
            raise IsolationError(
                "supervisor must be started from its dedicated long-lived "
                "supervisor thread (parent-death signal binds to the forking "
                "thread)"
            )
        verify_isolation_support(self.cgroup_root)
        if self._process is not None:
            raise IsolationError("supervisor already started")

        # Pipe and cgroup.procs descriptors still open in the parent; a
        # failed start closes whatever is left.
        parent_fds: list[int] = []

        def close_parent_fd(fd: int) -> None:
            parent_fds.remove(fd)
            os.close(fd)

        os.mkdir(self.cgroup_path)
        try:
            limits = {
                "memory.max": str(self.spec.memory_mb * 1024 * 1024),
                "pids.max": str(self.spec.pids),
                "memory.swap.max": "0",
            }
            for fname, value in limits.items():
                _write(os.path.join(self.cgroup_path, fname), value)
            for fname, expected in limits.items():
                got = _read(os.path.join(self.cgroup_path, fname))
                if got != expected:
                    raise IsolationUnavailable(
                        "limit readback mismatch for %s: wrote %s read %s" % (fname, expected, got)
                    )
            self.limits_readback = dict(limits)

            gate_r, gate_w = os.pipe()
            parent_fds.extend((gate_r, gate_w))
            status_r, status_w = os.pipe()
            parent_fds.extend((status_r, status_w))
            procs_fd = os.open(os.path.join(self.cgroup_path, "cgroup.procs"), os.O_WRONLY)
            parent_fds.append(procs_fd)
            argv = self._bwrap_argv()
            env = {"PATH": "/usr/bin:/bin"}

            # os.fork directly: subprocess.Popen waits for the child's
            # exec confirmation on its internal error pipe, so a child
            # that blocks on the placement gate before exec would
            # deadlock the parent's Popen call.  Forking by hand keeps
            # the gate under our control; status_w is close-on-exec, so
            # EOF means the exec succeeded.
            pid = os.fork()  # noqa: S606 - raw fork is required for the placement gate
            if pid == 0:
                stage = "setup"
                try:
                    os.close(gate_w)
                    os.close(status_r)
                    stage = "place"
                    os.write(procs_fd, str(os.getpid()).encode())
                    os.close(procs_fd)
                    stage = "gate"
                    # EOF means the parent closed the gate without opening
                    # it; waiting on would spin on empty reads forever.
                    if not os.read(gate_r, 1):
                        raise EOFError("gate closed before it was opened")
                    os.close(gate_r)
                    stage = "stdio"
                    devnull = os.open(os.devnull, os.O_RDWR)
                    for stdfd in (0, 1, 2):
                        os.dup2(devnull, stdfd)
                    stage = "exec"
                    os.execvpe(argv[0], argv, env)  # noqa: S606 - exec into bwrap, no shell
                except BaseException as exc:  # noqa: BLE001 - child must never escape
                    try:
                        # A failure here must still leave bytes in the pipe:
                        # an empty pipe at EOF reads as a successful exec.
                        try:
                            message = _startup_diagnostic(stage, exc)
                        except BaseException:  # noqa: BLE001 - fall back to a constant
                            message = b"error stage=unknown"
                        os.write(status_w, message)
                    finally:
                        os._exit(127)

            # Recorded before anything else can fail, so a failed start
            # always kills and reaps the child.
            self._process = _ChildProcess(pid)
            self.payload_pid = pid
            close_parent_fd(gate_r)
            close_parent_fd(procs_fd)
            close_parent_fd(status_w)

            deadline = time.monotonic() + 10
            placed = False
            while time.monotonic() < deadline:
                procs = _read(os.path.join(self.cgroup_path, "cgroup.procs"))
                if str(pid) in procs.split():
                    placed = True
                    break
                if self._process.poll() is not None:
                    break
                time.sleep(0.02)
            if not placed:
                code = self._process.poll()
                raise IsolationError(
                    "payload process was not placed in the run cgroup (child %s; %s)"
                    % (_describe_exit(code), _read_startup_diagnostic(status_r))
                )

            self.gate_opened_monotonic_ns = time.monotonic_ns()
            os.write(gate_w, b"1")
            close_parent_fd(gate_w)
            status = b""
            while len(status) < _STARTUP_DIAGNOSTIC_LIMIT:
                chunk = os.read(status_r, _STARTUP_DIAGNOSTIC_LIMIT - len(status))
                if not chunk:
                    break
                status += chunk
            close_parent_fd(status_r)
            if status:
                raise IsolationError(
                    "payload failed to exec bubblewrap (%s)" % _decode_startup_diagnostic(status)
                )
        except BaseException as exc:  # noqa: BLE001 - cleanup must run on any failure
            try:
                if self._process is not None and self._process.poll() is None:
                    self._process.kill()
            finally:
                for fd in parent_fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                try:
                    self._remove_cgroup(force=True)
                except CgroupCleanupError as cleanup:
                    # The start failure stays the raised error; the cleanup
                    # failure rides along as a note and in cleanup_error.
                    attach_note(exc, str(cleanup))
            raise

    def read_resource_peak(self, name: str) -> int | None:
        """Read an integer counter (e.g. memory.peak) from the run cgroup.

        Returns None when the counter file is absent or unreadable.
        spec: peak counters unavailable on a supported kernel are
        recorded as null, enforcement itself must still work.
        """
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("counter name must be a plain file name, got %r" % name)
        path = os.path.join(self.cgroup_path, name)
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except (OSError, UnicodeDecodeError):
            return None
        if value == "max":
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def wait(self, timeout: float | None = None) -> int:
        if self._process is None:
            raise IsolationError("supervisor not started")
        return self._process.wait(timeout=timeout)

    def _remove_cgroup(self, force: bool = False, timeout: float = 10) -> None:
        kill_file = os.path.join(self.cgroup_path, "cgroup.kill")
        if os.path.exists(kill_file):
            try:
                _write(kill_file, "1")
            except OSError:
                pass
        # Only the outcome of this removal counts: a stale failure text from
        # an earlier attempt must not outlive a later successful removal.
        self.cleanup_error = None
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.rmdir(self.cgroup_path)
            except FileNotFoundError:
                # Already removed (a failed start cleans up once and the later
                # teardown asks again): absence is a successful removal.
                pass
            except OSError as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    break
                if force and os.path.exists(kill_file):
                    # keep killing stragglers between rmdir retries
                    try:
                        _write(kill_file, "1")
                    except OSError:
                        pass
                time.sleep(0.1)
                continue
            return
        # EBUSY (live tasks), ENOTEMPTY or EACCES at the deadline is a real
        # cleanup failure: report it instead of leaving the cgroup silently.
        self.cleanup_error = _bounded(
            "run cgroup was not removed before the deadline (last rmdir error %s)"
            % _error_detail(last_error)
        )
        # The path comes last, so a long one cannot cut off the type and errno.
        raise CgroupCleanupError(
            _printable("%s: %s" % (self.cleanup_error, self.cgroup_path))[:_CLEANUP_MESSAGE_LIMIT]
        ) from last_error

    def teardown(self) -> None:
        """Kill the payload and remove the run cgroup.

        Removal is forced (cgroup.kill is rewritten between retries), so any
        straggler the kill missed is killed again instead of keeping the
        cgroup busy. If an earlier removal already ran out its deadline (a
        failed start), one more attempt is made instead of a second 10 s wait.
        Teardown is only marked done once the cgroup is gone, so a caller can
        retry after a CgroupCleanupError.
        """
        if self._torn_down:
            return
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        # Decided before the call, which resets cleanup_error.
        timeout = 0 if self.cleanup_error is not None else 10
        self._remove_cgroup(force=True, timeout=timeout)
        self._torn_down = True


class SupervisorThread(threading.Thread):
    """Dedicated long-lived thread that owns a Supervisor lifecycle."""

    def __init__(self, supervisor: Supervisor) -> None:
        super().__init__(name="forge-supervisor-" + supervisor.spec.run_id)
        self._supervisor = supervisor
        self._ready = threading.Event()
        self._error: BaseException | None = None
        supervisor._bind_thread(self)

    def run(self) -> None:
        try:
            self._supervisor.start()
            self._ready.set()
            if self._supervisor._process is not None:
                self._supervisor._process.wait()
        except BaseException as exc:  # noqa: BLE001 - deliver any failure to wait_started
            self._error = exc
            self._ready.set()

    def wait_started(self, timeout: float) -> None:
        if not self._ready.wait(timeout):
            raise IsolationError("supervisor thread did not start in time")
        if self._error is not None:
            raise self._error
