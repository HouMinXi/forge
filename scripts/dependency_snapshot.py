"""Bounded dependency evidence only. Never a service launcher or admission gate."""

import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import types
import time
import sys
from contextlib import contextmanager

ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
MAX_ITEMS = 64
MAX_COUNT_ITEMS = 512
MAX_STRING = 4096
MAX_RECEIPT = 262144
MANAGER = "org.freedesktop.systemd1.Manager"
UNIT = "org.freedesktop.systemd1.Unit"
PROPERTIES = "org.freedesktop.DBus.Properties"
OBJECT = "/org/freedesktop/systemd1"
REASONS = frozenset(
    "identity private_endpoint private_credentials private_connect changed timeout output io tool source format command library abi version type limit method snapshot cleanup unexpected".split()
)


class Stop(ValueError):
    def __init__(self, reason):
        if reason not in REASONS:
            reason = "unexpected"
        super().__init__(reason)
        self.reason = reason


def need(value, reason):
    if not value:
        raise Stop(reason)


def limit(category):
    error = Stop("limit")
    error.limit_category = category
    raise error


def remaining(deadline):
    value = deadline - time.monotonic()
    need(value > 0, "timeout")
    return value


MANAGER_FIELDS = {
    "Version": "s",
    "UnitPath": "as",
    "ControlGroup": "s",
    "SystemState": "s",
    "NJobs": "u",
}
UNIT_FIELDS = dict.fromkeys(
    "Id Following LoadState ActiveState SubState FragmentPath SourcePath OnSuccessJobMode OnFailureJobMode".split(),
    "s",
)
UNIT_FIELDS.update(
    dict.fromkeys(
        "NeedDaemonReload Transient DefaultDependencies StopWhenUnneeded RefuseManualStart RefuseManualStop".split(),
        "b",
    )
)
UNIT_FIELDS.update(
    dict.fromkeys(
        "Names DropInPaths Requires Requisite Wants BindsTo PartOf Upholds RequiredBy RequisiteOf WantedBy BoundBy UpheldBy ConsistsOf Conflicts ConflictedBy Before After OnSuccess OnSuccessOf OnFailure OnFailureOf Triggers TriggeredBy PropagatesReloadTo ReloadPropagatedFrom PropagatesStopTo StopPropagatedFrom JoinsNamespaceOf SliceOf RequiresMountsFor".split(),
        "as",
    )
)
UNIT_FIELDS["Job"] = "(uo)"
BASE_SHA256 = "988503e24b9fd3c42d4ccd2f2a3616e6cbccfa33311a1789c14a56f178c1cfc3"


def metadata():
    """Load reviewed stacked source, never a stale .pyc or arbitrary search-path module."""
    path = Path(__file__).with_name("fixed_runner_metadata.py")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= 65536, "source")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(65537)
        need(len(data) == info.st_size <= 65536, "source")
    finally:
        os.close(fd)
    need(hashlib.sha256(data).hexdigest() == BASE_SHA256, "source")
    module = types.ModuleType("_reviewed_metadata")
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)  # noqa: S102 -- exact hash-bound reviewed source.
    return module


@contextmanager
def verified_channel(base, deadline):
    uid = os.getuid()
    need(uid == os.geteuid() != 0 and os.getgid() == os.getegid() != 0, "identity")
    # This existing system-manager bootstrap uses systemctl GetAll internally.
    data, tool_identity = base.file_bytes(Path("/usr/bin/systemctl"), 0, 16777216)
    need(
        data.startswith(b"\x7fELF") and tool_identity[2] & 0o111 and not tool_identity[2] & 0o6000,
        "tool",
    )
    identity = base.manager(uid)
    channel = None
    try:
        before = base.endpoint_snapshot(uid)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(min(2, remaining(deadline)))
        channel.connect(f"/run/user/{uid}/systemd/private")
        peer = base.peer_credentials(channel)
        need(peer["pid"] == identity["pid"] and peer["uid"] == uid, "private_credentials")
        need(base.endpoint_snapshot(uid) == before, "changed")
        base.live(identity, uid)
        channel.setblocking(False)

        def recheck():
            remaining(deadline)
            base.live(identity, uid)
            need(base.file_bytes(Path("/usr/bin/systemctl"), 0, 16777216)[1] == tool_identity, "changed")
            need(base.system_manager_pid(uid, deadline) == identity["pid"], "changed")
            need(base.endpoint_snapshot(uid) == before, "changed")

        yield channel, recheck, {"pid": identity["pid"], "starttime": identity["starttime"], "uid": uid}
    finally:
        if channel is not None:
            channel.close()  # detached sockets are harmless; library owns transferred FD.
        os.close(identity["fd"])


class Bus:
    """Only nonvariadic public sd-bus ABI; never implement D-Bus wire protocol."""

    def __init__(self, library, channel, deadline):
        self.lib, self.deadline, self.bus = library, deadline, C.c_void_p()
        p, i, s, ch = C.c_void_p, C.c_int, C.c_char_p, C.c_char
        signatures = {
            "sd_bus_new": ([C.POINTER(p)], i),
            "sd_bus_set_fd": ([p, i, i], i),
            "sd_bus_set_bus_client": ([p, i], i),
            "sd_bus_start": ([p], i),
            "sd_bus_close": ([p], None),
            "sd_bus_unref": ([p], p),
            "sd_bus_message_new_method_call": ([p, C.POINTER(p), s, s, s, s], i),
            "sd_bus_message_set_auto_start": ([p, i], i),
            "sd_bus_message_set_allow_interactive_authorization": ([p, i], i),
            "sd_bus_message_append_basic": ([p, ch, p], i),
            "sd_bus_call": ([p, p, C.c_uint64, p, C.POINTER(p)], i),
            "sd_bus_message_get_signature": ([p, i], s),
            "sd_bus_message_enter_container": ([p, ch, s], i),
            "sd_bus_message_exit_container": ([p], i),
            "sd_bus_message_at_end": ([p, i], i),
            "sd_bus_message_read_basic": ([p, ch, p], i),
            "sd_bus_message_unref": ([p], p),
        }
        try:
            for name, (args, result) in signatures.items():
                fn = getattr(library, name)
                fn.argtypes, fn.restype = args, result
        except AttributeError as exc:
            raise Stop("abi") from exc
        try:
            self.check(library.sd_bus_new(C.byref(self.bus)))
            fd = channel.detach()
            value = library.sd_bus_set_fd(self.bus, fd, fd)
            if value < 0:
                os.close(fd)
            self.check(value)
            self.check(library.sd_bus_set_bus_client(self.bus, 0))
            self.check(library.sd_bus_start(self.bus))
        except BaseException:  # noqa: BLE001 -- release owned bus, then re-raise interruption.
            self.close()
            raise

    def check(self, code):
        remaining(self.deadline)
        if code < 0:
            error = Stop("io")
            error.errno = -code
            raise error
        return code

    def close(self):
        if self.bus:
            self.lib.sd_bus_close(self.bus)
            self.lib.sd_bus_unref(self.bus)
            self.bus = C.c_void_p()

    @contextmanager
    def container(self, msg, kind, signature):
        need(
            self.check(self.lib.sd_bus_message_enter_container(msg, kind.encode(), signature.encode()))
            > 0,
            "type",
        )
        yield
        need(self.check(self.lib.sd_bus_message_at_end(msg, 0)) > 0, "type")
        self.check(self.lib.sd_bus_message_exit_container(msg))

    def read(self, msg, signature):
        if signature.startswith("a"):
            result = []
            with self.container(msg, "a", signature[1:]):
                while not self.check(self.lib.sd_bus_message_at_end(msg, 0)):
                    if len(result) >= MAX_ITEMS:
                        limit("array_items")
                    result.append(self.read(msg, signature[1:]))
            return result
        if signature.startswith("("):
            with self.container(msg, "r", signature[1:-1]):
                return [self.read(msg, kind) for kind in signature[1:-1]]
        need(signature in ("s", "o", "u", "b"), "type")
        value = (
            C.c_char_p() if signature in ("s", "o") else C.c_uint32() if signature == "u" else C.c_int()
        )
        need(
            self.check(self.lib.sd_bus_message_read_basic(msg, signature.encode(), C.byref(value))) > 0,
            "type",
        )
        if signature in ("s", "o"):
            raw = value.value
            if raw is None:
                limit("null_string")
            if len(raw) > MAX_STRING:
                limit("string_bytes")
            return raw.decode("utf-8", "strict")
        if signature == "b":
            need(value.value in (0, 1), "type")
            return bool(value.value)
        return value.value

    def request(self, path, interface, method, args, signature):
        allowed = (
            path == OBJECT
            and interface == MANAGER
            and signature == {"ListUnits": "a(ssssssouso)", "ListJobs": "a(usssoo)"}.get(method)
            and not args
        )
        if interface == PROPERTIES and method == "Get" and len(args) == 2:
            target, field = args
            fields = (
                MANAGER_FIELDS
                if path == OBJECT and target == MANAGER
                else UNIT_FIELDS
                if target == UNIT and re.fullmatch(r"/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+", path)
                else {}
            )
            allowed = field in fields and signature == fields.get(field)
        need(allowed, "method")
        request, reply = C.c_void_p(), C.c_void_p()
        try:
            self.check(
                self.lib.sd_bus_message_new_method_call(
                    self.bus, C.byref(request), None, path.encode(), interface.encode(), method.encode()
                )
            )
            self.check(self.lib.sd_bus_message_set_auto_start(request, 0))
            self.check(self.lib.sd_bus_message_set_allow_interactive_authorization(request, 0))
            for arg in args:
                self.check(
                    self.lib.sd_bus_message_append_basic(
                        request, b"s", C.cast(C.c_char_p(arg.encode()), C.c_void_p)
                    )
                )
            self.check(
                self.lib.sd_bus_call(
                    self.bus,
                    request,
                    max(1, int(remaining(self.deadline) * 1000000)),
                    None,
                    C.byref(reply),
                )
            )
            expected = "v" if method == "Get" else signature
            need(self.lib.sd_bus_message_get_signature(reply, 1) == expected.encode(), "type")
            if method == "Get":
                with self.container(reply, "v", signature):
                    value = self.read(reply, signature)
            else:
                value = self.read(reply, signature)
            need(self.check(self.lib.sd_bus_message_at_end(reply, 1)) > 0, "type")
            return value
        except Stop as exc:
            if exc.reason == "limit":
                exc.limit_context = (
                    "manager_property"
                    if method == "Get" and args[0] == MANAGER
                    else "unit_property"
                    if method == "Get"
                    else method
                )
                if method == "Get":
                    exc.limit_field = args[1]
            raise
        finally:
            if reply:
                self.lib.sd_bus_message_unref(reply)
            if request:
                self.lib.sd_bus_message_unref(request)

    def count_units(self):
        """Count a later reply, without retaining values; libsystemd allocates the whole reply."""
        skip = self.lib.sd_bus_message_skip
        skip.argtypes, skip.restype = [C.c_void_p, C.c_char_p], C.c_int
        request, reply = C.c_void_p(), C.c_void_p()
        try:
            self.check(
                self.lib.sd_bus_message_new_method_call(
                    self.bus, C.byref(request), None, OBJECT.encode(), MANAGER.encode(), b"ListUnits"
                )
            )
            self.check(self.lib.sd_bus_message_set_auto_start(request, 0))
            self.check(self.lib.sd_bus_message_set_allow_interactive_authorization(request, 0))
            self.check(
                self.lib.sd_bus_call(
                    self.bus,
                    request,
                    max(1, int(remaining(self.deadline) * 1000000)),
                    None,
                    C.byref(reply),
                )
            )
            need(self.lib.sd_bus_message_get_signature(reply, 1) == b"a(ssssssouso)", "type")
            need(
                self.check(self.lib.sd_bus_message_enter_container(reply, b"a", b"(ssssssouso)")) > 0,
                "type",
            )
            count = 0
            while count < MAX_COUNT_ITEMS and not self.check(self.lib.sd_bus_message_at_end(reply, 0)):
                need(self.check(skip(reply, b"(ssssssouso)")) > 0, "type")
                count += 1
            more = not bool(self.check(self.lib.sd_bus_message_at_end(reply, 0)))
            if not more:
                self.check(self.lib.sd_bus_message_exit_container(reply))
                need(self.check(self.lib.sd_bus_message_at_end(reply, 1)) > 0, "type")
            return {
                "exact_count": None if more else count,
                "validated_prefix_count": count,
                "has_more": more,
                "sample": "LATER_NONATOMIC",
                "receive_allocation": "NOT_BOUNDED",
            }
        finally:
            if reply:
                self.lib.sd_bus_message_unref(reply)
            if request:
                self.lib.sd_bus_message_unref(request)

    def properties(self, path, interface, fields):
        result = {}
        for field, signature in fields.items():
            try:
                result[field] = self.request(path, PROPERTIES, "Get", (interface, field), signature)
            except Stop as exc:
                exc.field = field
                exc.object_path = path
                raise
        return result


def encode(value):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    need(len(raw) <= MAX_RECEIPT, "output")
    return raw


def collect(bus, recheck):
    def manager():
        value = bus.properties(OBJECT, MANAGER, MANAGER_FIELDS)
        need(re.match(r"255(?:[.\-+~ ]|$)", value["Version"]) is not None, "version")
        return value

    def roster():
        rows = bus.request(OBJECT, MANAGER, "ListUnits", (), "a(ssssssouso)")
        if len(rows) > MAX_ITEMS:
            error = Stop("limit")
            error.limit_category, error.limit_context = "array_items", "ListUnits"
            raise error
        # Do not retain Description or job details from ListUnits.
        result = sorted((row[0], row[6], row[2], row[3], row[4]) for row in rows)
        need(len({row[1] for row in result}) == len(result), "snapshot")
        return result

    def jobs():
        result = bus.request(OBJECT, MANAGER, "ListJobs", (), "a(usssoo)")
        if len(result) > MAX_ITEMS:
            error = Stop("limit")
            error.limit_category, error.limit_context = "array_items", "ListJobs"
            raise error
        return sorted(result)

    recheck()
    before = manager(), roster(), jobs()
    units = []
    for name, path, load, active, sub in before[1]:
        fields = bus.properties(path, UNIT, UNIT_FIELDS)
        need(
            (fields["Id"], fields["LoadState"], fields["ActiveState"], fields["SubState"])
            == (name, load, active, sub),
            "changed",
        )
        units.append({"path": path, "properties": fields})
        encode(units)
    after = manager(), roster(), jobs()
    recheck()
    need(before == after, "changed")
    return {"manager": before[0], "roster": before[1], "jobs": before[2], "units": units}


def diagnostic_count(bus, recheck, verify_library, deadline):
    """Discard a count unless both identity checks and library verification succeed."""
    try:
        recheck()
        count = bus.count_units()
        recheck()
        verify_library()
        remaining(deadline)
        return count
    except (Stop, OSError, ValueError, AttributeError):
        return None


def ordinary_identity():
    need(os.getuid() == os.geteuid() != 0 and os.getgid() == os.getegid() != 0, "identity")


def reason_of(exc):
    reason = getattr(exc, "reason", "io")
    return reason if reason in REASONS else "unexpected"


def observe():
    deadline = time.monotonic() + 25
    result = {
        "schema": "forge-dependency-snapshot-v1",
        "status": "STOP",
        "complete": False,
        "qualification": "NOT_RUN",
        "service_execution": "NOT_REQUESTED",
        "library_package_version": "NOT_OBSERVED",
        "reason": None,
        "snapshot_consistency": "BOUNDED_NONATOMIC",
        "dependency_closure": "NOT_ESTABLISHED",
    }
    try:
        ordinary_identity()
        base = metadata()
        path = Path("/usr/lib/x86_64-linux-gnu/libsystemd.so.0").resolve(strict=True)
        data, identity = base.file_bytes(path, 0, 16777216)
        need(data.startswith(b"\x7fELF"), "library")
        result["library"] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
        library = C.CDLL(str(path))
        with verified_channel(base, deadline) as (channel, recheck, process):
            result["process"] = process
            bus = Bus(library, channel, deadline)
            try:
                try:
                    snapshot = collect(bus, recheck)
                except Stop as exc:
                    if (
                        exc.reason == "limit"
                        and getattr(exc, "limit_context", None) == "ListUnits"
                        and getattr(exc, "limit_category", None) == "array_items"
                    ):

                        def verify_library():
                            need(base.file_bytes(path, 0, 16777216)[1] == identity, "changed")

                        count = diagnostic_count(bus, recheck, verify_library, deadline)
                        if count is not None:
                            result["list_units_count"] = count
                    raise
            finally:
                bus.close()
            need(base.file_bytes(path, 0, 16777216)[1] == identity, "changed")
            remaining(deadline)
            result.update(status="OBSERVED", complete=True, snapshot=snapshot)
            encode(result)
    except (Stop, OSError, ValueError, AttributeError) as exc:
        # Fixed reasons only; never persist bus error strings or exception text.
        result.pop("snapshot", None)
        if reason_of(exc) == "limit":
            category = getattr(exc, "limit_category", None)
            context = getattr(exc, "limit_context", None)
            field = getattr(exc, "limit_field", None)
            result["limit_category"] = (
                category if category in {"array_items", "string_bytes", "null_string"} else None
            )
            result["limit_context"] = (
                context
                if context in {"ListUnits", "ListJobs", "manager_property", "unit_property"}
                else None
            )
            allowed_fields = (
                MANAGER_FIELDS
                if context == "manager_property"
                else UNIT_FIELDS
                if context == "unit_property"
                else {}
            )
            result["limit_field"] = field if field in allowed_fields else None
        result.update(
            status="STOP",
            complete=False,
            reason=reason_of(exc),
            errno=getattr(exc, "errno", None),
            failed_field=getattr(exc, "field", None),
            failed_object=getattr(exc, "object_path", None),
        )
    return result


def main():
    """Only the no-argument entry is public; worker has no command-line escape."""
    need(not sys.argv[1:], "format")
    ordinary_identity()
    base = metadata()
    # Trusted bounded-size bootstrap above is outside the watchdog. The child
    # covers library work and queries; the reviewed capture reaps its direct child.
    worker = (
        "import runpy,sys; m=runpy.run_path(sys.argv[1]); "
        "sys.stdout.buffer.write(m['encode'](m['observe']())+b'\\n')"
    )
    output = base.capture(
        [sys.executable, "-I", "-c", worker, str(Path(__file__).resolve())],
        ENV,
        time.monotonic() + 30,
        limit=MAX_RECEIPT,
    )
    if output["reason"] or output["exit_code"] != 0 or output["stderr_bytes"]:
        result = {
            "schema": "forge-dependency-snapshot-v1",
            "status": "STOP",
            "complete": False,
            "qualification": "NOT_RUN",
            "reason": output["reason"] or "command",
        }
    else:
        result = json.loads(output["stdout"])
        need(isinstance(result, dict) and result.get("status") in ("STOP", "OBSERVED"), "format")
    sys.stdout.buffer.write(encode(result) + b"\n")
    return 0 if result["status"] == "OBSERVED" else 2


def entry():
    try:
        return main()
    except (Stop, OSError, ValueError, AttributeError) as exc:
        result = {
            "schema": "forge-dependency-snapshot-v1",
            "status": "STOP",
            "complete": False,
            "qualification": "NOT_RUN",
            "reason": reason_of(exc),
            "errno": getattr(exc, "errno", None),
        }
        sys.stdout.buffer.write(encode(result) + b"\n")
        return 2


if __name__ == "__main__":
    sys.exit(entry())
