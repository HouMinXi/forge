# SPDX-License-Identifier: Apache-2.0
"""Run one mutation command in a private Linux process owner.

The owner is a subreaper in a fresh interpreter. Orphans remain its children
even if they create another session; pidfds bind signals to the observed
process identity instead of a potentially reused numeric PID.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import select
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_CLEANUP_SECONDS = 5.0
_TERM_SECONDS = 0.25
_POLL_SECONDS = 0.01
_OUTPUT_LIMIT_BYTES = 1024 * 1024
_REPORT_LIMIT_BYTES = 256 * 1024
_READ_BYTES = 64 * 1024
_ENVELOPE_SECONDS = 30.0


class MutationProcessError(RuntimeError):
    def __init__(self, message: str, *, cleanup_complete: bool, report: dict | None = None):
        super().__init__(message)
        self.cleanup_complete = cleanup_complete
        self.report = report or {}


def _bind_cancellation_evidence(cancellation: BaseException, *, note: str | None = None, **evidence):
    """Propagate the first control interruption, retaining an already pending one."""
    try:
        cancellation.__dict__.update(evidence)
        if note is not None:
            cancellation.add_note(note)
    except BaseException as exc:  # noqa: BLE001 - retain cancellation while recording interrupted evidence
        if isinstance(cancellation, Exception) and not isinstance(exc, Exception):
            raise
        cancellation.__dict__.update(evidence, cleanup_complete=False, cleanup_evidence_error=exc)


def limit_address_space(memory_limit_bytes: int) -> None:
    import resource

    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        memory_limit_bytes = min(memory_limit_bytes, hard)
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))


@dataclass(frozen=True)
class _Identity:
    pid: int
    parent: int
    start_ticks: int
    state: str


def _identity(pid: int) -> _Identity | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = raw[raw.rfind(b")") + 2 :].split()
    return _Identity(pid, int(fields[1]), int(fields[19]), fields[0].decode("ascii"))


def _children(identity: _Identity) -> list[int]:
    before = _identity(identity.pid)
    if before is None or before.start_ticks != identity.start_ticks:
        return []
    children: set[int] = set()
    try:
        for task in Path(f"/proc/{identity.pid}/task").iterdir():
            try:
                children.update(int(pid) for pid in (task / "children").read_bytes().split())
            except (FileNotFoundError, ProcessLookupError):
                continue
    except (FileNotFoundError, ProcessLookupError):
        return []
    after = _identity(identity.pid)
    if after is None or after.start_ticks != identity.start_ticks:
        return []
    return sorted(children)


def _pidfd_ready(fd: int) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
        raise OSError("invalid mutation process descriptor")
    return any(mask & select.POLLIN for _, mask in events)


class _OwnedTree:
    def __init__(self):
        self.owner = _identity(os.getpid())
        if self.owner is None:
            raise RuntimeError("cannot identify mutation process owner")
        probe = os.pidfd_open(self.owner.pid)
        os.close(probe)
        self.identities: dict[int, _Identity] = {}
        self.fds: dict[int, int] = {}
        self.reaped: dict[int, int] = {}
        self.cleanup_deadline: float | None = None

    def verify_enumeration(self) -> None:
        """Reject incomplete procfs before a command can create descendants.

        The owner's main task cannot disappear while this method runs. Its
        missing children interface is therefore a capability failure, not the
        transient task-exit race tolerated by _children during discovery.
        """
        path = Path(f"/proc/{self.owner.pid}/task/{self.owner.pid}/children")
        try:
            for child in path.read_bytes().split():
                int(child)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "mutation process ownership unavailable: readable procfs task children "
                "interface required; native command was not launched"
            ) from exc

    def discover(self) -> None:
        pending = [self.owner]
        visited: set[int] = set()
        while pending:
            parent = pending.pop()
            if parent.pid in visited:
                continue
            visited.add(parent.pid)
            if parent.pid in self.fds and _pidfd_ready(self.fds[parent.pid]):
                continue
            for pid in _children(parent):
                identity = _identity(pid)
                if identity is None or identity.parent != parent.pid:
                    continue
                if pid in self.identities:
                    if self.identities[pid].start_ticks != identity.start_ticks:
                        raise RuntimeError("mutation descendant PID identity changed")
                    if pid not in self.fds:
                        continue
                if pid not in self.fds:
                    try:
                        fd = os.pidfd_open(pid)
                    except ProcessLookupError:
                        continue
                    current = _identity(pid)
                    if (
                        current is None
                        or current.start_ticks != identity.start_ticks
                        or current.parent not in (parent.pid, self.owner.pid)
                    ):
                        os.close(fd)
                        continue
                    self.fds[pid] = fd
                    self.identities[pid] = identity
                pending.append(identity)

    def live(self) -> list[int]:
        live = []
        for pid in self.fds:
            identity = self.identities[pid]
            current = _identity(pid)
            if current is None:
                if not _pidfd_ready(self.fds[pid]):
                    live.append(pid)
            elif current.start_ticks == identity.start_ticks:
                live.append(pid)
        return live

    def reap(self, driver: subprocess.Popen) -> None:
        driver_status = driver.poll()
        for pid in list(self.fds):
            identity = self.identities[pid]
            if pid == driver.pid:
                if driver_status is not None:
                    self.reaped[pid] = driver_status
                    self._retire(pid)
                continue
            current = _identity(pid)
            if current is None:
                if not _pidfd_ready(self.fds[pid]):
                    continue
            elif current.start_ticks != identity.start_ticks or current.parent != self.owner.pid:
                continue
            try:
                result = os.waitid(os.P_PIDFD, self.fds[pid], os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                if current is None:
                    self._retire(pid)
                continue
            if result is not None:
                self.reaped[pid] = result.si_status
                self._retire(pid)

    def _retire(self, pid: int) -> None:
        os.close(self.fds.pop(pid))

    def signal(self, pid: int, sig: int) -> None:
        if pid not in self.fds:
            return
        identity = self.identities[pid]
        current = _identity(pid)
        if current is None or current.start_ticks != identity.start_ticks:
            return
        try:
            signal.pidfd_send_signal(self.fds[pid], sig)
        except ProcessLookupError:
            pass

    def cleanup(self, driver: subprocess.Popen, *, drain=None) -> bool:
        started = time.monotonic()
        if self.cleanup_deadline is None:
            self.cleanup_deadline = started + _CLEANUP_SECONDS
        deadline = self.cleanup_deadline
        terminated: set[int] = set()
        empty_polls = 0
        while time.monotonic() < deadline:
            if drain is not None:
                drain(0)
            self.discover()
            self.reap(driver)
            live = self.live()
            for pid in live:
                if time.monotonic() - started >= _TERM_SECONDS:
                    self.signal(pid, signal.SIGKILL)
                elif pid not in terminated:
                    self.signal(pid, signal.SIGTERM)
                    terminated.add(pid)
            if not live and not _children(self.owner) and driver.returncode is not None:
                empty_polls += 1
                if empty_polls >= 2:
                    return True
            else:
                empty_polls = 0
            time.sleep(_POLL_SECONDS)
        return False

    def report(self) -> list[dict]:
        return [
            {
                "pid": pid,
                "start_ticks": item.start_ticks,
                "observed_parent": item.parent,
                "remaining": pid in self.live(),
                "reaped_status": self.reaped.get(pid),
            }
            for pid, item in self.identities.items()
        ]

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)


def _binding_digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


class _BoundedCapture:
    """Fair pipe draining with a shared diagnostic budget and full-stream hashes."""

    def __init__(self, stdout, stderr, limit: int):
        self.limit = limit
        self.retained = 0
        self.prefix = {"stdout": bytearray(), "stderr": bytearray()}
        self.counts = {"stdout": 0, "stderr": 0}
        self.hashes = {name: hashlib.sha256() for name in self.prefix}
        self.eof = {name: False for name in self.prefix}
        self.selector = selectors.DefaultSelector()
        try:
            for name, stream in (("stdout", stdout), ("stderr", stderr)):
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ, name)
        except BaseException:
            self.selector.close()
            raise

    def consume(self, name: str, block: bytes) -> None:
        self.counts[name] += len(block)
        self.hashes[name].update(block)
        keep = min(len(block), self.limit - self.retained)
        self.prefix[name].extend(block[:keep])
        self.retained += keep

    def drain(self, timeout: float = 0) -> None:
        # One bounded read per ready stream, then return to deadline/cleanup work.
        for key, _ in self.selector.select(timeout):
            try:
                block = os.read(key.fd, _READ_BYTES)
            except BlockingIOError:
                continue
            if block:
                self.consume(key.data, block)
            else:
                self.eof[key.data] = True
                self.selector.unregister(key.fileobj)

    def metadata(self) -> dict:
        return {
            "retained_bytes": self.retained,
            "diagnostic_truncated": sum(self.counts.values()) > self.retained,
            "streams": {
                name: {"bytes": self.counts[name], "sha256": self.hashes[name].hexdigest(),
                       "retained_bytes": len(self.prefix[name]), "eof": self.eof[name]}
                for name in self.prefix
            },
        }

    def close(self) -> None:
        self.selector.close()


def _executable_binding(argv: list[str], cwd: str, env: dict[str, str]) -> dict:
    """Pin the selected executable without retaining environment values."""
    command = argv[0]
    if os.path.dirname(command):
        candidates = [os.path.join(cwd, command)]
    else:
        candidates = [os.path.join(cwd, entry, command) for entry in os.get_exec_path(env)]
    selected = next((p for p in candidates if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if selected is None:
        raise FileNotFoundError(f"executable unavailable: {command}")
    path = Path(selected).resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        first = os.fstat(fd)
        if not stat.S_ISREG(first.st_mode):
            raise ValueError("owned executable is not a regular file")
        digest = hashlib.sha256()
        while block := os.read(fd, _READ_BYTES):
            digest.update(block)
        last = os.fstat(fd)
        named = path.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(first, k) != getattr(last, k) or getattr(last, k) != getattr(named, k)
               for k in fields):
            raise ValueError("owned executable changed while binding")
        return {"path": os.path.abspath(selected), "realpath": str(path),
                "dev": last.st_dev, "ino": last.st_ino,
                "size": last.st_size, "mtime_ns": last.st_mtime_ns,
                "ctime_ns": last.st_ctime_ns, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def _bounded_tree_report(tree: _OwnedTree) -> list[dict]:
    # Reject an oversized identity inventory instead of silently dropping identities.
    rows = []
    size = 2
    live = set(tree.live())
    for pid, item in tree.identities.items():
        row = {"pid": pid, "start_ticks": item.start_ticks, "observed_parent": item.parent,
               "remaining": pid in live, "reaped_status": tree.reaped.get(pid)}
        size += len(json.dumps(row).encode("utf-8")) + 2
        if size > _REPORT_LIMIT_BYTES // 2:
            raise ValueError("bounded owner identity report overflow")
        rows.append(row)
    return rows


def _bounded_report(report: dict) -> dict:
    if len(json.dumps(report).encode("utf-8")) + 1 <= _REPORT_LIMIT_BYTES:
        return report
    # An overflow is an explicit failure. This response makes no proof claim.
    return {"cleanup_complete": report.get("cleanup_complete", False),
            "error": "bounded owner control report overflow", "error_kind": "ValueError",
            "capture_version": 1, "report_overflow": True}


def _supervise_bounded(request: dict) -> dict:
    """The same private owner, with opt-in bounded stream/report transport."""
    report = {"capture_version": 1, "cleanup_complete": True, "timed_out": False,
              "cancelled": False, "owned": [], "invocation_nonce": request["invocation_nonce"],
              "caller_pid": request["caller_pid"],
              "caller_start_ticks": request["caller_start_ticks"],
              "cwd": request["cwd"], "argv_sha256": _binding_digest(request["argv"]),
              "env_sha256": _binding_digest(request["env"])}
    driver = tree = capture = None
    cancelled = False

    def cancel(_signal, _frame):
        nonlocal cancelled
        cancelled = True

    def cleanup():
        if driver is not None and not report["cleanup_complete"]:
            report["cleanup_complete"] = tree.cleanup(driver, drain=capture.drain if capture else None)
            report["owned"] = _bounded_tree_report(tree)

    try:
        if not sys.platform.startswith("linux") or not all(
            (hasattr(os, "P_PIDFD"), hasattr(os, "pidfd_open"), hasattr(signal, "pidfd_send_signal"))
        ):
            raise RuntimeError("mutation process ownership requires Linux pidfds and /proc")
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, cancel)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0 or libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot establish mutation process ownership")
        tree = _OwnedTree()
        report.update(owner_pid=tree.owner.pid, owner_start_ticks=tree.owner.start_ticks)
        tree.verify_enumeration()
        caller = _identity(request["caller_pid"])
        if (cancelled or os.getppid() != request["caller_pid"] or caller is None
                or caller.start_ticks != request["caller_start_ticks"]):
            raise RuntimeError("mutation caller identity changed before command launch")
        executable = _executable_binding(request["argv"], request["cwd"], request["env"])
        report["resolved_executable"] = executable
        if cancelled:
            raise RuntimeError("mutation caller exited before command launch")
        cap = request.get("memory_limit_bytes")
        if cap is not None:
            limit_address_space(cap)
        started = time.monotonic()
        deadline = started + request["timeout"]
        driver = subprocess.Popen(
            request["argv"], executable=executable["path"], cwd=request["cwd"], env=request["env"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        report["cleanup_complete"] = False
        identity = _identity(driver.pid)
        if identity is None or identity.parent != tree.owner.pid:
            raise RuntimeError("cannot bind owned driver incarnation")
        report.update(driver_pid=identity.pid, driver_start_ticks=identity.start_ticks)
        # Capture the actual exec inode, before polling can reap a short-lived driver.
        actual = os.stat(f"/proc/{driver.pid}/exe")
        if any(getattr(actual, "st_" + key) != executable[key]
               for key in ("dev", "ino", "size", "mtime_ns", "ctime_ns")):
            raise RuntimeError("owned driver executable identity mismatch")
        tree.discover()
        capture = _BoundedCapture(driver.stdout, driver.stderr, request["output_limit_bytes"])
        try:
            while True:
                tree.discover()
                tree.reap(driver)
                if cancelled or time.monotonic() >= deadline:
                    report["cancelled"] = cancelled
                    report["timed_out"] = not cancelled
                    break
                if driver.returncode is not None:
                    break
                capture.drain(_POLL_SECONDS)
        finally:
            cleanup()
        # Cleanup is bounded separately. A pipe retained after cleanup is never proof.
        drain_deadline = min(started + request["timeout"] + _ENVELOPE_SECONDS - 5,
                             time.monotonic() + 2)
        while not all(capture.eof.values()) and time.monotonic() < drain_deadline:
            capture.drain(_POLL_SECONDS)
        report["returncode"] = driver.returncode
        report["duration_seconds"] = time.monotonic() - started
        if not all(capture.eof.values()):
            raise RuntimeError("bounded diagnostic drain did not reach EOF after cleanup")
    except BaseException as exc:  # noqa: BLE001 - every owner exit attempts teardown
        report["error"] = f"{type(exc).__name__}: {exc}"[:4096]
        report["error_kind"] = type(exc).__name__
        if driver is not None and not report["cleanup_complete"]:
            try:
                # A broken drain must not prevent a second, drain-free cleanup attempt.
                report["cleanup_complete"] = tree.cleanup(driver)
                report["owned"] = _bounded_tree_report(tree)
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the original failure
                report["error"] += f"; cleanup: {cleanup_exc}"[:4096]
    finally:
        report["cancelled"] = cancelled
        if capture is not None:
            report.update(capture.metadata())
            try:
                descriptor = request["diagnostic_fd"]
                for name in ("stdout", "stderr"):
                    view = memoryview(capture.prefix[name])
                    while view:
                        written = os.write(descriptor, view[:_READ_BYTES])
                        if written <= 0:
                            raise OSError("bounded diagnostic transport made no progress")
                        view = view[written:]
            except Exception as exc:  # noqa: BLE001 - transport failure is never successful capture
                report["error"] = f"bounded diagnostic transport failed: {exc}"[:4096]
                report["error_kind"] = type(exc).__name__
            finally:
                capture.close()
        if driver is not None:
            for stream in (driver.stdout, driver.stderr):
                if stream is not None:
                    stream.close()
        if tree is not None:
            tree.close()
    return _bounded_report(report)


def _supervise(request: dict) -> dict:
    if "output_limit_bytes" in request:
        return _supervise_bounded(request)
    report = {"cleanup_complete": True, "timed_out": False, "cancelled": False, "owned": []}
    driver = None
    tree = None
    cancelled = False

    def cancel(_signal, _frame):
        nonlocal cancelled
        cancelled = True

    try:
        if not sys.platform.startswith("linux") or not all(
            (hasattr(os, "P_PIDFD"), hasattr(os, "pidfd_open"), hasattr(signal, "pidfd_send_signal"))
        ):
            raise RuntimeError("mutation process ownership requires Linux pidfds and /proc")
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, cancel)
        parent = request["caller_pid"]
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0 or libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot establish mutation process ownership")
        if os.getppid() != parent:
            cancelled = True
        tree = _OwnedTree()
        report["owner_pid"] = tree.owner.pid
        report["owner_start_ticks"] = tree.owner.start_ticks
        tree.verify_enumeration()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            if cancelled:
                raise RuntimeError("mutation caller exited before command launch")
            cap = request.get("memory_limit_bytes")
            if cap is not None:
                limit_address_space(cap)
            driver = subprocess.Popen(
                request["argv"],
                cwd=request.get("cwd"),
                env=request.get("env"),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            report["cleanup_complete"] = False
            deadline = time.monotonic() + request["timeout"]
            try:
                while driver.poll() is None:
                    tree.reap(driver)
                    tree.discover()
                    if cancelled or time.monotonic() >= deadline:
                        report["cancelled"] = cancelled
                        report["timed_out"] = not cancelled
                        break
                    time.sleep(_POLL_SECONDS)
            finally:
                report["cleanup_complete"] = tree.cleanup(driver)
                report["owned"] = tree.report()
            report["returncode"] = driver.returncode
            stdout.seek(0)
            stderr.seek(0)
            report["stdout"] = base64.b64encode(stdout.read()).decode("ascii")
            report["stderr"] = base64.b64encode(stderr.read()).decode("ascii")
    except BaseException as exc:  # noqa: BLE001 - every exit must attempt owned teardown
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["error_kind"] = type(exc).__name__
        if driver is not None and not report["cleanup_complete"]:
            try:
                report["cleanup_complete"] = tree.cleanup(driver)
                report["owned"] = tree.report()
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the original failure
                report["error"] += f"; cleanup: {cleanup_exc}"
    finally:
        if tree is not None:
            tree.close()
    return report


def _exchange_bounded(
    helper: subprocess.Popen, payload: bytes, timeout: float, *, envelope_deadline: float | None = None,
) -> tuple:
    """Pump the private control pipes; never use communicate or unbounded reads."""
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": _REPORT_LIMIT_BYTES, "stderr": _READ_BYTES}
    pending = memoryview(payload)
    interruption = None
    overflow = False
    started = time.monotonic()
    hard_deadline = envelope_deadline if envelope_deadline is not None else started + timeout + _ENVELOPE_SECONDS
    deadline = hard_deadline - _CLEANUP_SECONDS - 2

    def interrupt(exc):
        nonlocal interruption, deadline
        if interruption is None:
            interruption = exc
            deadline = min(hard_deadline, time.monotonic() + _CLEANUP_SECONDS + 2)
            try:
                helper.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif isinstance(interruption, Exception) and not isinstance(exc, Exception):
            # A later control interruption outranks an ordinary communication failure.
            interruption = exc

    try:
        for name, stream in (("stdin", helper.stdin), ("stdout", helper.stdout),
                             ("stderr", helper.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if name == "stdin"
                              else selectors.EVENT_READ, name)
        while selector.get_map() or helper.poll() is None:
            try:
                if time.monotonic() >= deadline:
                    if interruption is not None:
                        failure = MutationProcessError(
                            "bounded mutation owner did not finish cleanup/report within envelope",
                            cleanup_complete=False,
                        )
                        if not isinstance(interruption, Exception):
                            _bind_cancellation_evidence(
                                interruption, cleanup_complete=False, ownership={}, cleanup_error=failure,
                            )
                            raise interruption from failure
                        raise failure from interruption
                    interrupt(subprocess.TimeoutExpired(helper.args, max(0, deadline - started)))
                for key, _ in selector.select(_POLL_SECONDS):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fd, pending[:_READ_BYTES])
                        except BrokenPipeError:
                            written = len(pending)
                        except BlockingIOError:
                            continue
                        pending = pending[written:]
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        try:
                            block = os.read(key.fd, _READ_BYTES)
                        except BlockingIOError:
                            continue
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = buffers[key.data]
                        available = limits[key.data] - len(buffer)
                        buffer.extend(block[:available])
                        if len(block) > available:
                            overflow = True
                            interrupt(ValueError("bounded owner control report/diagnostic overflow"))
            except BaseException as exc:  # noqa: BLE001 - drain despite caller cancellation
                if isinstance(exc, MutationProcessError) or time.monotonic() >= deadline:
                    raise
                interrupt(exc)
        return bytes(buffers["stdout"]), bytes(buffers["stderr"]), interruption, overflow
    finally:
        selector.close()
        for stream in (helper.stdin, helper.stdout, helper.stderr):
            stream.close()


def _validate_bounded_report(report: dict, request: dict, owner: _Identity, prefix_size: int) -> None:
    if type(report) is not dict or type(report.get("cleanup_complete")) is not bool:
        raise ValueError("invalid bounded owner report")
    expected = {
        "capture_version": 1, "invocation_nonce": request["invocation_nonce"],
        "caller_pid": request["caller_pid"], "caller_start_ticks": request["caller_start_ticks"],
        "owner_pid": owner.pid, "owner_start_ticks": owner.start_ticks,
        "cwd": request["cwd"], "argv_sha256": _binding_digest(request["argv"]),
        "env_sha256": _binding_digest(request["env"]),
    }
    if any(type(report.get(key)) is not type(value) or report[key] != value
           for key, value in expected.items()):
        raise ValueError("bounded owner invocation identity mismatch")
    if report.get("error"):
        return
    for key in ("driver_pid", "driver_start_ticks"):
        if type(report.get(key)) is not int or report[key] <= 0:
            raise ValueError("bounded owner missing driver incarnation")
    if (type(report.get("returncode")) is not int or
            any(type(report.get(k)) is not bool for k in ("timed_out", "cancelled"))):
        raise ValueError("bounded owner missing command outcome")
    if len({report["caller_pid"], report["owner_pid"], report["driver_pid"]}) != 3:
        raise ValueError("bounded owner process identities are contradictory")
    duration = report.get("duration_seconds")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0):
        raise ValueError("bounded owner missing finite duration")
    owned = report.get("owned")
    if type(owned) is not list:
        raise ValueError("bounded owner missing identity inventory")
    seen = set()
    for row in owned:
        if (type(row) is not dict
                or set(row) != {"pid", "start_ticks", "observed_parent", "remaining", "reaped_status"}
                or any(type(row[k]) is not int or row[k] <= 0
                       for k in ("pid", "start_ticks", "observed_parent"))
                or type(row["remaining"]) is not bool or row["pid"] in seen
                or (row["reaped_status"] is not None and type(row["reaped_status"]) is not int)):
            raise ValueError("malformed bounded owner identity inventory")
        if report["cleanup_complete"] and row["remaining"]:
            raise ValueError("bounded owner cleanup contradicts identity inventory")
        seen.add(row["pid"])
    matches = [r for r in owned if r["pid"] == report["driver_pid"]]
    if (len(matches) != 1 or matches[0]["start_ticks"] != report["driver_start_ticks"]
            or matches[0]["observed_parent"] != report["owner_pid"]):
        raise ValueError("bounded owner driver identity inventory mismatch")
    executable = report.get("resolved_executable")
    if (type(executable) is not dict or not isinstance(executable.get("path"), str)
            or not os.path.isabs(executable["path"])
            or not _is_digest(executable.get("sha256"))):
        raise ValueError("bounded owner missing executable binding")
    streams = report.get("streams")
    if type(streams) is not dict or set(streams) != {"stdout", "stderr"}:
        raise ValueError("bounded owner missing stream evidence")
    retained = total = 0
    for row in streams.values():
        if (type(row) is not dict or set(row) != {"bytes", "sha256", "retained_bytes", "eof"}
                or type(row["bytes"]) is not int or type(row["retained_bytes"]) is not int
                or not 0 <= row["retained_bytes"] <= row["bytes"]
                or not _is_digest(row["sha256"]) or row["eof"] is not True):
            raise ValueError("invalid bounded stream evidence or missing EOF")
        retained += row["retained_bytes"]
        total += row["bytes"]
    if (retained > request["output_limit_bytes"] or retained != prefix_size
            or type(report.get("retained_bytes")) is not int or report["retained_bytes"] != retained
            or type(report.get("diagnostic_truncated")) is not bool
            or report["diagnostic_truncated"] != (total > retained)):
        raise ValueError("bounded diagnostic prefix size/truncation mismatch")


def _is_digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _unique_owner_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate bounded owner report field")
        result[key] = value
    return result


def _run_owned_bounded(
    argv, *, timeout, cwd, env, memory_limit_bytes, text, encoding, errors,
    output_limit_bytes, invocation_nonce,
) -> subprocess.CompletedProcess:
    invocation_started = time.monotonic()
    if type(output_limit_bytes) is not int or not 1 <= output_limit_bytes <= _OUTPUT_LIMIT_BYTES:
        raise ValueError("output_limit_bytes must be an integer in [1, 1048576]")
    if (not isinstance(invocation_nonce, str) or len(invocation_nonce) != 32
            or any(c not in "0123456789abcdef" for c in invocation_nonce)):
        raise ValueError("bounded ownership requires a lowercase 128-bit invocation_nonce")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("bounded ownership requires a finite positive timeout")
    from ._mutation_imports import ISOLATED_BOOTSTRAP, prepare_owner

    try:
        authority = prepare_owner(__file__, __name__)
        caller = _identity(os.getpid())
        if caller is None:
            raise ValueError("cannot bind mutation caller incarnation")
    except (OSError, ValueError, TypeError, ImportError, SyntaxError) as exc:
        raise MutationProcessError(f"bounded owner authority unavailable: {exc}", cleanup_complete=True) from exc
    report = {}
    interruption = None
    cleanup_verified = False
    request = {"argv": list(argv), "timeout": timeout, "cwd": os.path.abspath(cwd or os.getcwd()),
               "env": dict(os.environ if env is None else env), "memory_limit_bytes": memory_limit_bytes,
               "caller_pid": caller.pid, "caller_start_ticks": caller.start_ticks,
               "output_limit_bytes": output_limit_bytes, "invocation_nonce": invocation_nonce}
    with tempfile.TemporaryFile() as prefix:
        request["diagnostic_fd"] = prefix.fileno()
        payload = json.dumps({"request": request, "authority": authority}).encode("utf-8")
        if len(payload) > 4 * _OUTPUT_LIMIT_BYTES:
            raise MutationProcessError("bounded owner request overflow", cleanup_complete=True)
        envelope_deadline = invocation_started + timeout + _ENVELOPE_SECONDS
        if time.monotonic() >= envelope_deadline - _CLEANUP_SECONDS - 2:
            raise MutationProcessError("bounded owner preparation exhausted envelope", cleanup_complete=True)
        helper = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", ISOLATED_BOOTSTRAP], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            pass_fds=(prefix.fileno(),),
        )
        exchange_started = False
        try:
            # The helper is still waiting for its request, so cannot launch a driver yet.
            owner = _identity(helper.pid)
            if owner is None or owner.parent != caller.pid:
                raise ValueError("cannot bind mutation owner incarnation")
            exchange_started = True
            raw, _diagnostic, interruption, overflow = _exchange_bounded(
                helper, payload, timeout, envelope_deadline=envelope_deadline,
            )
            if overflow:
                raise ValueError("bounded owner control report/diagnostic overflow")
            report = json.loads(raw, object_pairs_hook=_unique_owner_object)
            size = os.fstat(prefix.fileno()).st_size
            if size > output_limit_bytes:
                raise ValueError("bounded diagnostic transport overflow")
            _validate_bounded_report(report, request, owner, size)
            if not report["cleanup_complete"]:
                raise MutationProcessError("bounded owner cleanup incomplete", cleanup_complete=False, report=report)
            if helper.poll() != 0:
                raise ValueError("bounded owner exited abnormally")
            cleanup_verified = True
            if interruption is None and report.get("cancelled"):
                interruption = KeyboardInterrupt("mutation command was cancelled")
            if interruption is not None:
                if not isinstance(interruption, Exception):
                    raise interruption
                raise MutationProcessError(
                    f"bounded owner communication failed: {interruption}", cleanup_complete=True, report=report,
                ) from interruption
            if report.get("error"):
                if report.get("error_kind") == "FileNotFoundError":
                    failure = FileNotFoundError(report["error"])
                    failure.ownership = report
                    failure.cleanup_complete = True
                    raise failure
                raise MutationProcessError(report["error"], cleanup_complete=True, report=report)
            prefix.seek(0)
            stdout_size = report["streams"]["stdout"]["retained_bytes"]
            stdout = prefix.read(stdout_size)
            stderr = prefix.read(size - stdout_size)
            if len(stdout) != stdout_size or len(stderr) != size - stdout_size or prefix.read(1):
                raise ValueError("bounded diagnostic transport changed while reading")
            for name, value in (("stdout", stdout), ("stderr", stderr)):
                stream = report["streams"][name]
                if len(value) == stream["bytes"] and hashlib.sha256(value).hexdigest() != stream["sha256"]:
                    raise ValueError("bounded diagnostic stream hash mismatch")
            if text or encoding is not None or errors is not None:
                stdout = stdout.decode(encoding or "utf-8", errors or "strict")
                stderr = stderr.decode(encoding or "utf-8", errors or "strict")
            if report["timed_out"]:
                failure = subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
                failure.ownership = report
                failure.cleanup_complete = True
                raise failure
            result = subprocess.CompletedProcess(argv, report["returncode"], stdout, stderr)
            result.ownership = report
            return result
        except BaseException as exc:  # noqa: BLE001 - preserve cancellation with conservative cleanup evidence
            if helper.poll() is None:
                try:
                    helper.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # No request was sent when owner identification fails; close stdin to unblock bootstrap.
                if not helper.stdin.closed:
                    helper.stdin.close()
                if not exchange_started:
                    # The private bootstrap never received a request or launched native work.
                    try:
                        helper.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    finally:
                        helper.stdout.close()
                        helper.stderr.close()
            pending_control = interruption is not None and not isinstance(interruption, Exception)
            if pending_control or not isinstance(exc, Exception):
                cancellation = interruption if pending_control else exc
                cause = exc if cancellation is not exc else None
                try:
                    _bind_cancellation_evidence(
                        cancellation, cleanup_complete=cleanup_verified, ownership=report,
                        **({"cleanup_error": cause} if cause is not None else {}),
                    )
                except BaseException as evidence_exc:  # noqa: BLE001 - first control remains authoritative
                    try:
                        _bind_cancellation_evidence(
                            cancellation, cleanup_complete=False, ownership=report,
                            cleanup_evidence_error=evidence_exc,
                        )
                    finally:
                        raise cancellation from (cause or evidence_exc)
                if cause is not None:
                    raise cancellation from cause
                raise
            if isinstance(exc, (MutationProcessError, subprocess.TimeoutExpired)) or (
                isinstance(exc, FileNotFoundError) and hasattr(exc, "ownership")
            ):
                raise
            failure = MutationProcessError(
                f"invalid bounded owner evidence: {exc}", cleanup_complete=cleanup_verified,
                report=report if isinstance(report, dict) else {},
            )
            raise failure from exc


def run_owned_command(
    argv: list[str],
    *,
    timeout: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    memory_limit_bytes: int | None = None,
    capture_output: bool = True,
    text: bool = False,
    encoding: str | None = None,
    errors: str | None = None,
    check: bool = False,
    output_limit_bytes: int | None = None,
    invocation_nonce: str | None = None,
) -> subprocess.CompletedProcess:
    """Preserve subprocess results and timeout semantics after owned teardown.

    Both bounded options are opt-in: pass output_limit_bytes=1048576 and a fresh
    32-hex invocation_nonce for FIXVAL. Smaller positive diagnostic limits are
    allowed. The limit is combined across both streams; .ownership records
    truncation, full-stream hashes/counts, EOF, identities and invocation bindings.
    No bounded behavior is applied to existing callers which omit these options.
    """
    if not argv or not capture_output or check:
        raise ValueError("owned mutation commands require argv, capture_output and check=False")
    if output_limit_bytes is not None or invocation_nonce is not None:
        return _run_owned_bounded(
            argv, timeout=timeout, cwd=cwd, env=env, memory_limit_bytes=memory_limit_bytes,
            text=text, encoding=encoding, errors=errors, output_limit_bytes=output_limit_bytes,
            invocation_nonce=invocation_nonce,
        )
    from ._mutation_imports import ISOLATED_BOOTSTRAP, prepare_owner

    try:
        authority = prepare_owner(__file__, __name__)
    except (OSError, ValueError, TypeError, ImportError, SyntaxError) as exc:
        raise MutationProcessError(
            f"mutation owner import authority unavailable: {exc}", cleanup_complete=True
        ) from exc
    request = {
        "argv": argv,
        "timeout": timeout,
        "cwd": cwd,
        "env": env,
        "memory_limit_bytes": memory_limit_bytes,
        "caller_pid": os.getpid(),
    }
    helper = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", ISOLATED_BOOTSTRAP],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    interruption = None
    report = {}
    try:
        try:
            raw, diagnostic = helper.communicate(
                json.dumps({"request": request, "authority": authority}).encode("utf-8"),
                timeout=timeout + _CLEANUP_SECONDS + 5,
            )
        except BaseException as exc:  # noqa: BLE001 - every interruption drains the private owner
            interruption = exc
            try:
                helper.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                raw, diagnostic = helper.communicate(timeout=_CLEANUP_SECONDS + 2)
            except Exception as cleanup_exc:  # noqa: BLE001 - ordinary drain failures need cleanup evidence
                raise MutationProcessError(
                    f"mutation owner {helper.pid} did not finish cleanup",
                    cleanup_complete=False,
                ) from cleanup_exc
        try:
            report = json.loads(raw)
            if not isinstance(report, dict) or type(report.get("cleanup_complete")) is not bool:
                raise ValueError("invalid owner report")
        except (ValueError, TypeError) as exc:
            report = {}
            raise MutationProcessError(
                f"mutation owner failed to report cleanup: {diagnostic.decode('utf-8', 'replace')}",
                cleanup_complete=False,
            ) from exc
        if interruption is None and report.get("cancelled"):
            interruption = KeyboardInterrupt("mutation command was cancelled")
        if not report["cleanup_complete"]:
            remaining = ", ".join(
                f"{item['pid']}:{item['start_ticks']}"
                for item in report.get("owned", [])
                if item.get("remaining")
            )
            raise MutationProcessError(
                f"mutation descendants did not finish cleanup: {remaining}",
                cleanup_complete=False,
                report=report,
            )
        if interruption is not None and not isinstance(interruption, Exception):
            _bind_cancellation_evidence(interruption, cleanup_complete=True, ownership=report)
    except BaseException as cleanup_exc:  # noqa: BLE001 - cleanup must not replace control flow
        cancelled = interruption is not None and not isinstance(interruption, Exception)
        if cancelled or not isinstance(cleanup_exc, Exception):
            cancellation = interruption if cancelled else cleanup_exc
            cause = cleanup_exc if cancellation is interruption else interruption
            try:
                _bind_cancellation_evidence(
                    cancellation, cleanup_complete=False, ownership=report,
                    **({"cleanup_error": cause} if cause is not None else {}),
                )
                if cause is not None:
                    label = ("mutation cleanup failed" if cancelled
                             else "mutation owner communication failed")
                    _bind_cancellation_evidence(cancellation, note=f"{label}: {cause}")
            except BaseException as evidence_exc:  # noqa: BLE001 - diagnostic failure is secondary to cancellation
                _bind_cancellation_evidence(
                    cancellation, cleanup_complete=False, ownership=report,
                    cleanup_error=cause if cause is not None else evidence_exc,
                    cleanup_evidence_error=evidence_exc,
                )
            finally:
                if cause is not None:
                    raise cancellation from cause
                raise cancellation
        if interruption is not None:
            failure = MutationProcessError(
                f"mutation owner communication failed: {interruption}; cleanup: {cleanup_exc}",
                cleanup_complete=False,
                report=report,
            )
            failure.cleanup_error = cleanup_exc
            raise failure from interruption
        raise
    if interruption is not None:
        if not isinstance(interruption, Exception):
            raise interruption
        raise MutationProcessError(
            f"mutation owner communication failed: {interruption}",
            cleanup_complete=True,
            report=report,
        ) from interruption
    if report.get("error"):
        if report.get("error_kind") == "FileNotFoundError":
            raise FileNotFoundError(report["error"])
        raise MutationProcessError(report["error"], cleanup_complete=True, report=report)
    stdout = base64.b64decode(report.get("stdout", ""))
    stderr = base64.b64decode(report.get("stderr", ""))
    if text or encoding is not None or errors is not None:
        stdout = stdout.decode(encoding or "utf-8", errors or "strict")
        stderr = stderr.decode(encoding or "utf-8", errors or "strict")
    if report.get("timed_out"):
        error = subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
        error.ownership = report
        raise error
    result = subprocess.CompletedProcess(argv, report["returncode"], stdout, stderr)
    result.ownership = report
    return result


if __name__ == "__main__":
    print(json.dumps(_supervise(json.load(sys.stdin))))
