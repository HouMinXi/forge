"""Passive test-only Invocation trace, preinstalled before the installed CLI starts.

``install(fd)`` writes bounded scalar-only JSONL to a duplicated private regular
file descriptor; ``install(path)`` creates it exclusively. No Forge module is
imported, product function replaced, deadline changed, or object modified. The
normal console launcher remains responsible for selecting the installed code.

Readers must reject missing/non-complete footers, sequence gaps and malformed
records. Transport ACK, native admission, and successful host requests are
separate observations. IDs are counters, not object addresses or phase guesses.
"""

import atexit
import dis
import json
import os
import stat
import sys
import sysconfig
import threading
import time
from types import MethodType
import weakref


SCHEMA_VERSION = 1
MAX_EVENTS = 200_000
EVENT_NAMES = frozenset(
    {
        "diagnostic_start",
        "diagnostic_error",
        "diagnostic_dropped",
        "diagnostic_footer",
        "host_request_begin",
        "host_request_end",
        "host_request_error",
        "ticket_create",
        "ticket_submit_begin",
        "ticket_submit",
        "ticket_submit_error",
        "ticket_unknown",
        "ticket_complete",
        "worker_begin",
        "worker_end",
        "target_end",
        "wait_begin",
        "wait_end",
        "wait_error",
        "publish_begin",
        "publish_end",
        "publish_error",
    }
)
_STATES = frozenset(
    {"ack", "empty", "unavailable", "expired", "unknown", "failed", "cancelled", "closed"}
)
_PHASES = frozenset(
    {
        "open",
        "input_manifest",
        "register_operation",
        "start",
        "entered",
        "begin_stage",
        "append",
        "seal",
        "adopt",
        "observe",
        "finish",
        "finalize",
        "maintenance",
    }
)
_FUNCTIONS = {
    "code_forge.invocation_host": frozenset(
        {"HostAuditSession._request", "HostAuditSession._execute", "_Control.__init__"}
    ),
    "code_forge.invocation_io": frozenset(
        {
            "IoTicket.__init__",
            "ProcessAuditIo._submit_locked",
            "ProcessAuditIo._next_locked",
            "ProcessAuditIo._complete_locked",
            "ProcessAuditIo._unknown_locked",
            "ProcessAuditIo.wait",
            "_call_target",
        }
    ),
    "code_forge.invocation_audit": frozenset({"_publish"}),
}
_INSTALLED = None


def _private_descriptor(destination):
    """Never truncate an existing path or take ownership of the caller's FD."""
    fd = (
        os.dup(destination)
        if type(destination) is int
        else os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size != 0
            or os.lseek(fd, 0, os.SEEK_CUR) != 0
        ):
            raise ValueError("diagnostic output must be an empty private regular file")
        import fcntl

        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise ValueError("diagnostic output must be writable")
        os.set_inheritable(fd, False)
        return fd
    except BaseException:
        try:
            os.close(fd)
        except BaseException:  # noqa: BLE001, S110 - cleanup must preserve the validation failure
            pass
        raise


def _package_roots():
    # No importing Forge, metadata discovery, source overrides, or launcher bypass.
    return frozenset(
        os.path.realpath(os.path.join(sysconfig.get_path(key), "code_forge"))
        for key in ("purelib", "platlib")
    )


def _native(value, module_name, class_name):
    module = sys.modules.get("code_forge." + module_name)
    return module is not None and type(value) is vars(module).get(class_name)


def _normal_return(frame):
    # Trace exception events can be caught inside the same function. Only an
    # actual return instruction, not an exception-unwind return event, succeeds.
    return dis.opname[frame.f_code.co_code[frame.f_lasti]] in {"RETURN_VALUE", "RETURN_CONST"}


class Probe:
    """A finite passive observer; unresolved or unsupported work is incomplete.

    ``finish()`` is idempotent and also runs at atexit. ``uninstall()`` is for
    isolated tests, on the owning thread after all other Python threads stop.
    """

    def __init__(self, destination, max_events):
        # Direct construction and install share one ownership boundary. Allocate
        # observer state before acquiring an owned FD; an input FD is borrowed
        # and duplicated only after initialization succeeds.
        self._fd, self._max_events = None, max_events
        self._pid, self._lock = os.getpid(), threading.RLock()
        self._roots = _package_roots()
        self._seq = self._host_id = self._operation_id = self._ticket_id = 0
        self._submit_id = self._publish_id = self._unknown_targets = 0
        self._open_tickets = self._open_workers = 0
        self._failed = self._disabled = self._finished = False
        self._hooks_exposed = False
        self._observed_native = False
        # Weak object identity maps hold no raw arguments, bodies or exceptions.
        self._hosts = weakref.WeakKeyDictionary()
        self._controls = weakref.WeakKeyDictionary()
        self._tickets = weakref.WeakKeyDictionary()
        self._owners = weakref.WeakKeyDictionary()
        self._recorders = weakref.WeakKeyDictionary()
        self._stage_targets = weakref.WeakKeyDictionary()
        # Frame IDs are never serialized and no frame/traceback is retained.
        self._requests, self._submits, self._waits, self._publishes = {}, {}, {}, {}
        self._frames = set()
        self._workers = {}
        self._hook = self._trace
        self._previous_sys = self._previous_threading = None
        # Hold the actual Thread object: integer thread identifiers may be reused.
        self._owner_thread = threading.current_thread()
        self._fd = _private_descriptor(destination)

    def _write(self, event, **fields):
        assert event in EVENT_NAMES
        self._seq += 1
        record = {
            "schema": SCHEMA_VERSION,
            "seq": self._seq,
            "pid": self._pid,
            "tid": threading.get_native_id(),
            "monotonic_ns": time.monotonic_ns(),
            "time_ns": time.time_ns(),
            "event": event,
            **fields,
        }
        if any(type(value) not in (int, str, bool, type(None)) for value in record.values()):
            raise TypeError("non-scalar diagnostic field")
        encoded = (json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        if len(encoded) > 4096:
            raise ValueError("oversized diagnostic record")
        offset = 0
        while offset < len(encoded):
            written = os.write(self._fd, encoded[offset:])
            if written <= 0:
                raise OSError("diagnostic write made no progress")
            offset += written

    def _emit(self, event, **fields):
        if self._disabled or self._finished:
            return
        # Reserve a dropped/error marker and a footer. Stop allocating metadata
        # after the event bound; no unbounded ledger survives a long CLI run.
        if self._seq >= self._max_events - 2:
            self._failed = self._disabled = True
            self._write("diagnostic_dropped", reason="event_limit")
            return
        self._write(event, **fields)

    def _failure(self):
        self._failed = self._disabled = True
        try:
            if not self._finished and self._seq < self._max_events - 1:
                self._write("diagnostic_error", reason="instrumentation_or_emission")
        except BaseException:  # noqa: BLE001, S110 - instrumentation cannot replace native results
            pass

    @staticmethod
    def _phase(phase):
        value = phase.value if _native(phase, "invocation_io", "AuditPhase") else None
        return value if type(value) is str and value in _PHASES else "unknown"

    @staticmethod
    def _deadline(deadline):
        return int(deadline * 1_000_000_000) if type(deadline) in (int, float) else None

    def _host_identity(self, host):
        identity = self._hosts.get(host)
        if identity is None:
            self._host_id += 1
            identity = self._host_id
            self._hosts[host] = identity
        return identity

    @staticmethod
    def _ancestor(frame, scopes):
        parent = frame.f_back
        while parent is not None:
            scope = scopes.get(id(parent))
            if scope is not None:
                return scope
            parent = parent.f_back
        return None

    def _request_begin(self, frame):
        local = frame.f_locals
        if not _native(local["self"], "invocation_host", "HostAuditSession"):
            raise ValueError("non-native host")
        self._operation_id += 1
        fields = {
            "host_id": self._host_identity(local["self"]),
            "operation_id": self._operation_id,
            "phase": self._phase(local["phase"]),
            "deadline_ns": self._deadline(local["deadline"]),
            "attempt": 0,
        }
        self._requests[id(frame)] = fields
        self._emit("host_request_begin", **fields)

    def _control_created(self, frame):
        local = frame.f_locals
        if not _native(local["self"], "invocation_host", "_Control"):
            raise ValueError("non-native control")
        request = self._ancestor(frame, self._requests)
        if request is None or request["host_id"] != self._host_identity(local["host"]):
            raise ValueError("control has no matching native request")
        request["attempt"] += 1
        self._controls[local["self"]] = dict(request)

    def _learn_owner(self, frame):
        host = frame.f_locals["self"]
        owner = vars(host).get("_owner")
        if not _native(owner, "invocation_owner", "InvocationOwner"):
            return
        host_id = self._host_identity(host)
        self._owners[owner] = host_id
        # The owner can advance epochs. Learn each current recorder on every
        # execute return without walking/retaining all historical owner state.
        current = vars(owner).get("_current")
        if _native(current, "invocation_owner", "_Epoch"):
            recorder = vars(current).get("recorder")
            if _native(recorder, "invocation_audit", "AttemptRecorder"):
                self._recorders[recorder] = host_id

    def _stage_host(self, target):
        if _native(target, "invocation_owner_stage", "_Publication"):
            stage = vars(target).get("stage")
        elif _native(target, "invocation_buffered_capture", "_SharedWireCapture"):
            callback = vars(target).get("_write")
            stage = callback.__self__ if type(callback) is MethodType else None
        else:
            return 0, "unknown"
        if _native(stage, "invocation_owner_stage", "OwnerCaptureStage"):
            owner = vars(stage).get("_owner")
            host_id = (
                self._owners.get(owner, 0)
                if _native(owner, "invocation_owner", "InvocationOwner")
                else 0
            )
        elif _native(stage, "invocation_stage_sink", "RecorderStageSink"):
            recorder = vars(stage).get("_recorder")
            host_id = (
                self._recorders.get(recorder, 0)
                if _native(recorder, "invocation_audit", "AttemptRecorder")
                else 0
            )
        else:
            host_id = 0
        kind = (
            "owner_stage"
            if _native(target, "invocation_owner_stage", "_Publication")
            else "capture_append"
        )
        return host_id, kind

    def _ticket_created(self, frame):
        local = frame.f_locals
        ticket, target = local["self"], local["target"]
        if not _native(ticket, "invocation_io", "IoTicket"):
            raise ValueError("non-native ticket")
        self._ticket_id += 1
        fields = {
            "host_id": 0,
            "operation_id": 0,
            "attempt": 0,
            "ticket_id": self._ticket_id,
            "phase": self._phase(local["phase"]),
            "deadline_ns": self._deadline(local["deadline"]),
            "target_kind": "unknown",
        }
        if _native(target, "invocation_host", "_Control"):
            control = self._controls.get(target)
            if control is None:
                raise ValueError("host control correlation missing")
            fields.update(control)
            fields["target_kind"] = "host_control"
            fields["deadline_ns"] = self._deadline(local["deadline"])
        else:
            host_id, kind = self._stage_host(target)
            fields["host_id"], fields["target_kind"] = host_id, kind
            if host_id:
                operation_id = self._stage_targets.get(target)
                if operation_id is None:
                    self._operation_id += 1
                    operation_id = self._operation_id
                    self._stage_targets[target] = operation_id
                fields["operation_id"] = operation_id
                fields["attempt"] = 1
            else:
                self._unknown_targets += 1
        metadata = {"fields": fields, "complete": False, "worker_tid": 0, "submits": 0, "unknown": False}
        self._tickets[ticket] = metadata
        submission = self._ancestor(frame, self._submits)
        if submission is not None:
            submission["ticket"] = metadata
        self._open_tickets += 1
        self._emit("ticket_create", **fields)

    def _ticket(self, ticket):
        if not _native(ticket, "invocation_io", "IoTicket"):
            raise ValueError("non-native ticket")
        metadata = self._tickets.get(ticket)
        if metadata is None:
            raise ValueError("native ticket correlation missing")
        return metadata

    def _submit_begin(self, frame):
        self._submit_id += 1
        self._submits[id(frame)] = {"submit_id": self._submit_id, "ticket": None}
        self._emit(
            "ticket_submit_begin", submit_id=self._submit_id, phase=self._phase(frame.f_locals["phase"])
        )

    def _submit_end(self, frame, result, success):
        submission = self._submits.pop(id(frame))
        metadata = self._ticket(result) if success else submission["ticket"]
        fields = {} if metadata is None else metadata["fields"]
        if success:
            metadata["submits"] += 1
        self._emit(
            "ticket_submit" if success else "ticket_submit_error",
            **fields,
            submit_id=submission["submit_id"],
            created=metadata is not None and metadata["submits"] <= 1,
        )
        # Rejected and empty tickets never enter _complete_locked.
        if success and result._outcome is not None and not metadata["complete"]:
            self._complete(result, result._outcome)

    @staticmethod
    def _native_ack(value):
        admitted = uncertain = None
        if any(
            _native(value, "invocation_audit", name)
            for name in (
                "AuditAcknowledgement",
                "StartAcknowledgement",
                "AcquiredAcknowledgement",
                "StageAcknowledgement",
            )
        ):
            fields = vars(value)
            admitted, uncertain = fields.get("admitted"), fields.get("publication_uncertain")
            # A recognized ACK must supply both boolean fields. Null pairs
            # are reserved for non-ACK values, not malformed native evidence.
            if type(admitted) is not bool or type(uncertain) is not bool:
                raise ValueError("unsupported recognized native ACK metadata")
        return {"native_admitted": admitted, "native_publication_uncertain": uncertain}

    def _outcome(self, outcome):
        if not _native(outcome, "invocation_io", "IoOutcome"):
            raise ValueError("non-native I/O outcome")
        state = outcome.state
        return {
            "transport_state": state if type(state) is str and state in _STATES else "other",
            "io_publication_uncertain": outcome.publication_uncertain is True,
            "not_executed": outcome.not_executed is True,
            "error_present": outcome.error is not None,
            **self._native_ack(outcome.value),
        }

    def _worker_begin(self, ticket):
        if ticket is None:
            return
        metadata = self._ticket(ticket)
        tid = threading.get_native_id()
        if metadata["worker_tid"] or tid in self._workers:
            raise ValueError("duplicate native worker window")
        metadata["worker_tid"] = tid
        self._workers[tid] = metadata
        self._open_workers += 1
        self._emit("worker_begin", **metadata["fields"])

    def _target_end(self, frame, result):
        # _call_target is invoked by _run with its exact ticket still in locals.
        parent = frame.f_back
        if parent is None or parent.f_code.co_qualname != "ProcessAuditIo._run":
            raise ValueError("target has no native worker frame")
        metadata = self._ticket(parent.f_locals["ticket"])
        if self._workers.get(threading.get_native_id()) is not metadata:
            raise ValueError("target has no matching native ticket window")
        value, error = result
        self._emit(
            "target_end",
            **metadata["fields"],
            error_present=error is not None,
            **self._native_ack(value),
        )

    def _complete(self, ticket, outcome):
        metadata = self._ticket(ticket)
        if metadata["complete"]:
            raise ValueError("ticket completed twice")
        if metadata["worker_tid"]:
            tid = threading.get_native_id()
            if metadata["worker_tid"] != tid or self._workers.get(tid) is not metadata:
                raise ValueError("completion outside the native worker window")
            self._emit("worker_end", **metadata["fields"], **self._outcome(outcome))
            del self._workers[tid]
            self._open_workers -= 1
        self._emit("ticket_complete", **metadata["fields"], **self._outcome(outcome))
        metadata["complete"] = True
        self._open_tickets -= 1

    def _publish_begin(self, frame):
        self._publish_id += 1
        metadata = self._workers.get(threading.get_native_id())
        fields = {} if metadata is None else dict(metadata["fields"])
        if metadata is None:
            self._unknown_targets += 1
        local = frame.f_locals
        fields.update(
            publish_id=self._publish_id,
            publication_kind="final"
            if type(local["name"]) is str and local["name"] == "final"
            else "other",
            directory_fd=local["fd"] if type(local["fd"]) is int else None,
        )
        self._publishes[id(frame)] = fields
        self._emit("publish_begin", **fields)

    def _dispatch(self, frame, event, result):
        name, local = frame.f_code.co_qualname, frame.f_locals
        if event == "call":
            if name == "HostAuditSession._request":
                self._request_begin(frame)
            elif name == "ProcessAuditIo._submit_locked":
                self._submit_begin(frame)
            elif name == "ProcessAuditIo._complete_locked":
                # Under the native condition, before it clears the target or
                # wakes controllers. Queued cancellation has no worker window.
                self._complete(local["ticket"], local["outcome"])
            elif name == "ProcessAuditIo.wait":
                fields = self._ticket(local["ticket"])["fields"]
                self._waits[id(frame)] = fields
                self._emit("wait_begin", **fields)
            elif name == "_publish":
                self._publish_begin(frame)
            return
        if event != "return":
            return
        success = _normal_return(frame)
        if name == "HostAuditSession._request":
            self._emit(
                "host_request_end" if success else "host_request_error", **self._requests.pop(id(frame))
            )
        elif name == "HostAuditSession._execute" and success:
            self._learn_owner(frame)
        elif name == "_Control.__init__" and success:
            self._control_created(frame)
        elif name == "IoTicket.__init__" and success:
            self._ticket_created(frame)
        elif name == "ProcessAuditIo._submit_locked":
            self._submit_end(frame, result, success)
        elif name == "ProcessAuditIo._next_locked" and success:
            self._worker_begin(result[0])
        elif name == "ProcessAuditIo._complete_locked" and not success:
            self._failure()
        elif name == "ProcessAuditIo._unknown_locked" and success:
            ticket = local["ticket"]
            metadata = self._ticket(ticket)
            if not metadata["unknown"] and ticket._outcome.state == "unknown":
                metadata["unknown"] = True
                self._emit("ticket_unknown", **metadata["fields"], **self._outcome(ticket._outcome))
        elif name == "_call_target" and success:
            self._target_end(frame, result)
        elif name == "ProcessAuditIo.wait":
            self._emit(
                "wait_end" if success else "wait_error",
                **self._waits.pop(id(frame)),
                **(self._outcome(result) if success else {}),
            )
        elif name == "_publish":
            self._emit("publish_end" if success else "publish_error", **self._publishes.pop(id(frame)))

    def _selected(self, frame):
        module = frame.f_globals.get("__name__")
        names = _FUNCTIONS.get(module) if type(module) is str else None
        if names is None or frame.f_code.co_qualname not in names:
            return False
        loaded = sys.modules.get(module)
        if loaded is None or frame.f_globals is not vars(loaded):
            return False
        filename = frame.f_code.co_filename
        expected = module.rsplit(".", 1)[1] + ".py"
        return (
            os.path.basename(filename) == expected
            and os.path.dirname(filename) in self._roots
            and frame.f_globals.get("__file__") == filename
        )

    def _trace(self, frame, event, result):
        if self._disabled or self._finished:
            return None
        try:
            if event == "call":
                if not self._selected(frame):
                    return None
            # Most Python calls are unrelated to Invocation. Even a filtered
            # syscall pays ptrace overhead, so do no PID syscall until a frame
            # is selected. Still reject fork children before touching our lock.
            if os.getpid() != self._pid:
                return None
            if event == "call":
                frame.f_trace_lines = False
            with self._lock:
                if not self._disabled and not self._finished:
                    if event == "call":
                        self._frames.add(id(frame))
                        self._observed_native = True
                    self._dispatch(frame, event, result)
                    if event == "return":
                        self._frames.discard(id(frame))
            return self._hook
        except BaseException:  # noqa: BLE001 - trace callbacks must preserve the original result/error
            try:
                with self._lock:
                    self._failure()
            except BaseException:  # noqa: BLE001, S110 - even error reporting must stay observational
                self._failed = self._disabled = True
            return None

    def start(self):
        previous_sys, previous_threading = sys.gettrace(), threading.gettrace()
        if previous_sys is not None or previous_threading is not None:
            raise RuntimeError("diagnostic probe cannot replace existing trace hooks")
        if len(threading.enumerate()) != 1:
            raise RuntimeError("diagnostic probe must be installed before threads start")
        # Rejected reentry must not replace the original restoration state with
        # this probe's already-installed hooks.
        self._owner_thread = threading.current_thread()
        self._previous_sys, self._previous_threading = previous_sys, previous_threading
        self._emit("diagnostic_start")
        # The setter may fail after taking effect. Until cleanup, other threads
        # could have inherited this hook; never assume failure means no exposure.
        self._hooks_exposed = True
        threading.settrace(self._hook)
        sys.settrace(self._hook)

    def finish(self):
        if os.getpid() != self._pid:
            return
        with self._lock:
            if self._finished:
                return
            pending = {
                "open_requests": len(self._requests),
                "open_tickets": self._open_tickets,
                "open_workers": self._open_workers,
                "open_waits": len(self._waits),
                "open_submits": len(self._submits),
                "open_publishes": len(self._publishes),
                "open_frames": len(self._frames),
                "unknown_targets": self._unknown_targets,
            }
            # Another tracer replacing either hook makes our coverage partial.
            if sys.gettrace() is not self._hook or threading.gettrace() is not self._hook:
                self._failed = True
            try:
                self._write(
                    "diagnostic_footer",
                    complete=self._observed_native and not self._failed and not any(pending.values()),
                    **pending,
                )
            except BaseException:  # noqa: BLE001 - missing/malformed footer is explicitly incomplete
                self._failed = True
            finally:
                self._finished = True
                try:
                    os.close(self._fd)
                except OSError:
                    self._failed = True

    def uninstall(self):
        global _INSTALLED  # noqa: PLW0603 - one observer per isolated process
        if threading.current_thread() is not self._owner_thread:
            raise RuntimeError("diagnostic probe must be uninstalled on its owning thread")
        if self._hooks_exposed and any(
            thread is not self._owner_thread for thread in threading.enumerate()
        ):
            raise RuntimeError("diagnostic probe must be uninstalled after other Python threads stop")
        finish_error = None
        try:
            self.finish()
        except BaseException as error:  # noqa: BLE001 - restore hooks even after a close interruption
            finish_error = error
        try:
            if sys.gettrace() is self._hook:
                sys.settrace(self._previous_sys)
            if threading.gettrace() is self._hook:
                threading.settrace(self._previous_threading)
            self._hooks_exposed = False
            if _INSTALLED is self:
                _INSTALLED = None
            atexit.unregister(self.finish)
        except BaseException:
            if finish_error is None:
                raise
        if finish_error is not None:
            raise finish_error


def install(destination, *, max_events=MAX_EVENTS):
    """Install before native imports/threads; leave all product code untouched."""
    global _INSTALLED  # noqa: PLW0603 - one observer per isolated process
    if _INSTALLED is not None:
        raise RuntimeError("diagnostic probe already installed")
    if type(max_events) is not int or not 4 <= max_events <= MAX_EVENTS:
        raise ValueError("invalid diagnostic event bound")
    probe = Probe(destination, max_events)
    try:
        probe.start()
        atexit.register(probe.finish)
    except BaseException:
        probe._failed = True
        try:
            probe.uninstall()
        except BaseException:  # noqa: BLE001, S110 - rollback cannot replace the installation failure
            pass
        raise
    _INSTALLED = probe
    return probe
