"""Offline controls only: no manager, service, network listener, or native bus."""

from contextlib import nullcontext
import ctypes as C
import importlib.util
import io
import json
import types
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts" / "dependency_snapshot.py"
spec = importlib.util.spec_from_file_location("snapshot", PATH)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class FakeBus:
    def __init__(self):
        self.calls = []
        self.rosters = 0
        self.drift = False
        self.fail_field = None

    def properties(self, path, interface, fields):
        self.calls.append((path, interface, tuple(fields)))
        if interface == p.MANAGER:
            return {
                "Version": "255.4-1ubuntu8.11",
                "UnitPath": ["/usr/lib/systemd/user"],
                "ControlGroup": "/user.slice",
                "SystemState": "running",
                "NJobs": 0,
            }
        if self.fail_field:
            error = p.Stop("io")
            error.field = self.fail_field
            raise error
        values = {
            key: [] if kind == "as" else False if kind == "b" else [0, "/"] if kind == "(uo)" else ""
            for key, kind in fields.items()
        }
        values.update(Id="x.service", LoadState="loaded", ActiveState="inactive", SubState="dead")
        return values

    def request(self, path, interface, method, args, signature):
        self.calls.append((path, interface, method, args, signature))
        if method == "ListJobs":
            return []
        if method == "ListUnits":
            self.rosters += 1
            return (
                [
                    [
                        "x.service",
                        "discarded description",
                        "loaded",
                        "inactive",
                        "dead",
                        "",
                        "/org/freedesktop/systemd1/unit/x_2eservice",
                        0,
                        "",
                        "/",
                    ]
                ]
                if not self.drift or self.rosters == 1
                else []
            )
        raise AssertionError(method)


class Function:
    def __init__(self, impl=lambda *args: 0):
        self.impl = impl
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.impl(*args)


class Library:
    def __init__(self, fail=None):
        self.fd = None
        self.closed = 0
        self.fail = fail
        names = [
            "new",
            "set_fd",
            "set_bus_client",
            "start",
            "close",
            "unref",
            "message_new_method_call",
            "message_set_auto_start",
            "message_set_allow_interactive_authorization",
            "message_append_basic",
            "call",
            "message_get_signature",
            "message_enter_container",
            "message_exit_container",
            "message_at_end",
            "message_read_basic",
            "message_skip",
            "message_unref",
        ]
        for name in names:
            setattr(self, "sd_bus_" + name, Function())
        self.sd_bus_new.impl = self.new
        self.sd_bus_set_fd.impl = self.set_fd
        self.sd_bus_start.impl = lambda *args: -5 if fail == "start" else 0
        self.sd_bus_close.impl = self.close

    def new(self, output):
        C.cast(output, C.POINTER(C.c_void_p))[0] = 7
        return 0

    def set_fd(self, bus, incoming, outgoing):
        if self.fail == "set_fd":
            return -5
        self.fd = incoming
        return 0

    def close(self, bus):
        import os

        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.closed += 1


class CountLibrary(Library):
    def __init__(self, count, fail_skip=None, bad_signature=False):
        super().__init__()
        self.count = count
        self.skipped = 0
        self.unrefs = []

        def message(bus, output, *args):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 9
            return 0

        def call(bus, request, timeout, error, output):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 10
            return 1

        def skip(msg, signature):
            assert signature == b"(ssssssouso)"
            if fail_skip is not None and self.skipped == fail_skip:
                return -5
            self.skipped += 1
            return 1

        self.sd_bus_message_new_method_call.impl = message
        self.sd_bus_call.impl = call
        self.sd_bus_message_get_signature.impl = lambda *args: (
            b"bad" if bad_signature else b"a(ssssssouso)"
        )
        self.sd_bus_message_enter_container.impl = lambda *args: 1
        self.sd_bus_message_at_end.impl = lambda msg, complete: int(
            complete or self.skipped >= self.count
        )
        self.sd_bus_message_skip.impl = skip
        self.sd_bus_message_unref.impl = lambda msg: self.unrefs.append(msg.value)


class Decoder(Library):
    """Source-shaped traversal events, not a D-Bus wire implementation."""

    def __init__(self, signature, value):
        super().__init__()
        self.events = []
        self.strings = []
        self.build(signature, value)
        self.sd_bus_message_enter_container.impl = self.enter
        self.sd_bus_message_exit_container.impl = lambda msg: self.event("exit")
        self.sd_bus_message_at_end.impl = lambda msg, complete: self.event("end")
        self.sd_bus_message_read_basic.impl = self.basic

    def build(self, signature, value):
        if signature.startswith("a"):
            self.events.append(("enter", (b"a", signature[1:].encode())))
            for item in value:
                self.events.append(("end", 0))
                self.build(signature[1:], item)
            self.events.extend([("end", 1), ("end", 1), ("exit", 1)])
        elif signature.startswith("("):
            self.events.append(("enter", (b"r", signature[1:-1].encode())))
            for kind, item in zip(signature[1:-1], value, strict=True):
                self.build(kind, item)
            self.events.extend([("end", 1), ("exit", 1)])
        else:
            self.events.append(("read", (signature, value)))

    def event(self, name):
        actual, value = self.events.pop(0)
        assert actual == name, (actual, name)
        return value

    def enter(self, msg, kind, signature):
        assert self.event("enter") == (kind, signature)
        return 1

    def basic(self, msg, kind, output):
        signature, value = self.event("read")
        assert signature.encode() == kind
        if kind in (b"s", b"o"):
            raw = value.encode()
            self.strings.append(raw)
            C.cast(output, C.POINTER(C.c_char_p))[0] = raw
        else:
            ctype = C.c_int if kind == b"b" else C.c_uint32
            C.cast(output, C.POINTER(ctype))[0] = value
        return 1


class SnapshotTests(unittest.TestCase):
    def test_exact_stack_import(self):
        base = p.metadata()
        self.assertEqual(base.SOURCE, "scripts/fixed_runner_metadata.py")

    def test_source_drift_rejected(self):
        with patch.object(p, "BASE_SHA256", "0" * 64):
            with self.assertRaisesRegex(p.Stop, "source"):
                p.metadata()

    def test_snapshot_only_expected_fields(self):
        bus = FakeBus()
        calls = []
        result = p.collect(bus, lambda: calls.append("check"))
        self.assertEqual(calls, ["check", "check"])
        self.assertNotIn(b"discarded description", p.encode(result))
        self.assertEqual(set(result["units"][0]["properties"]), set(p.UNIT_FIELDS))
        self.assertNotIn("Environment", p.UNIT_FIELDS)
        self.assertNotIn("ExecStart", p.UNIT_FIELDS)
        self.assertEqual(p.UNIT_FIELDS["RequiresMountsFor"], "as")
        self.assertEqual(p.UNIT_FIELDS["SliceOf"], "as")

    def test_roster_drift(self):
        bus = FakeBus()
        bus.drift = True
        with self.assertRaisesRegex(p.Stop, "changed"):
            p.collect(bus, lambda: None)

    def test_identity_drift(self):
        def failed():
            raise p.Stop("identity")

        bus = FakeBus()
        with self.assertRaisesRegex(p.Stop, "identity"):
            p.collect(bus, failed)
        self.assertEqual(bus.calls, [])

    def test_missing_property_not_empty(self):
        bus = FakeBus()
        bus.fail_field = "SliceOf"
        with self.assertRaises(p.Stop) as error:
            p.collect(bus, lambda: None)
        self.assertEqual(error.exception.field, "SliceOf")

    def test_version_2550_rejected(self):
        bus = FakeBus()
        original = bus.properties

        def fields(*args):
            value = original(*args)
            if "Version" in value:
                value["Version"] = "2550"
            return value

        bus.properties = fields
        with self.assertRaisesRegex(p.Stop, "version"):
            p.collect(bus, lambda: None)

    def test_output_limit(self):
        with self.assertRaisesRegex(p.Stop, "output"):
            p.encode({"value": "a" * p.MAX_RECEIPT})

    def test_deadline_not_reset(self):
        with self.assertRaisesRegex(p.Stop, "timeout"):
            p.remaining(time.monotonic() - 1)

    def test_fd_transfer_and_close(self):
        library = Library()
        left, right = socket.socketpair()
        try:
            fd = left.fileno()
            bus = p.Bus(library, left, time.monotonic() + 1)
            self.assertEqual(left.fileno(), -1)
            self.assertEqual(library.fd, fd)
            self.assertEqual(library.sd_bus_set_bus_client.calls[0][1], 0)
            bus.close()
            bus.close()
            self.assertEqual(library.closed, 1)
        finally:
            left.close()
            right.close()

    def test_start_and_set_fd_failures_release(self):
        import os

        for stage in ("start", "set_fd"):
            with self.subTest(stage=stage):
                library = Library(stage)
                left, right = socket.socketpair()
                fd = left.fileno()
                try:
                    with self.assertRaises(p.Stop):
                        p.Bus(library, left, time.monotonic() + 1)
                    with self.assertRaises(OSError):
                        os.fstat(fd)
                    self.assertEqual(library.closed, 1)
                finally:
                    left.close()
                    right.close()

    def test_missing_symbol_does_not_take_fd(self):
        library = Library()
        del library.sd_bus_call
        left, right = socket.socketpair()
        try:
            with self.assertRaisesRegex(p.Stop, "abi"):
                p.Bus(library, left, time.monotonic() + 1)
            self.assertGreaterEqual(left.fileno(), 0)
        finally:
            left.close()
            right.close()

    def test_method_negative_controls_before_ffi(self):
        bus = object.__new__(p.Bus)
        for method in ("GetAll", "LoadUnit", "StartUnit", "StopUnit", "Reload", "Set", "Ref", "Hello"):
            with self.subTest(method=method), self.assertRaisesRegex(p.Stop, "method"):
                bus.request(p.OBJECT, p.MANAGER, method, (), "s")
        with self.assertRaisesRegex(p.Stop, "method"):
            bus.request(p.OBJECT, p.PROPERTIES, "Get", (p.MANAGER, "Environment"), "as")

    def test_wrong_reply_signature_cleans_messages_and_sets_flags(self):
        library = Library()
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)

        def message(bus, output, *args):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 9
            return 0

        def call(bus, request, timeout, error, output):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 10
            return 1

        library.sd_bus_message_new_method_call.impl = message
        library.sd_bus_call.impl = call
        library.sd_bus_message_get_signature.impl = lambda *args: b"s"
        try:
            with self.assertRaisesRegex(p.Stop, "type"):
                bus.request(p.OBJECT, p.MANAGER, "ListJobs", (), "a(usssoo)")
            self.assertEqual(library.sd_bus_message_set_auto_start.calls[0][1], 0)
            self.assertEqual(library.sd_bus_message_set_allow_interactive_authorization.calls[0][1], 0)
            self.assertEqual(len(library.sd_bus_message_unref.calls), 2)
        finally:
            bus.close()
            right.close()

    def test_source_shaped_decoding(self):
        cases = [
            ("as", ["x.service", "y.target"]),
            ("(uo)", [7, "/org/freedesktop/systemd1/job/7"]),
            (
                "a(ssssssouso)",
                [
                    [
                        "x.service",
                        "description",
                        "loaded",
                        "inactive",
                        "dead",
                        "",
                        "/org/freedesktop/systemd1/unit/x",
                        0,
                        "",
                        "/",
                    ]
                ],
            ),
            (
                "a(usssoo)",
                [
                    [
                        7,
                        "x.service",
                        "start",
                        "waiting",
                        "/org/freedesktop/systemd1/job/7",
                        "/org/freedesktop/systemd1/unit/x",
                    ]
                ],
            ),
            ("as", ["x"] * 64),
        ]
        for signature, value in cases:
            with self.subTest(signature=signature):
                library = Decoder(signature, value)
                left, right = socket.socketpair()
                bus = p.Bus(library, left, time.monotonic() + 1)
                try:
                    self.assertEqual(bus.read(C.c_void_p(10), signature), value)
                    self.assertEqual(library.events, [])
                finally:
                    bus.close()
                    right.close()

    def test_array_65_stops_instead_of_truncating(self):
        library = Decoder("as", ["x"] * 65)
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            with self.assertRaisesRegex(p.Stop, "limit") as caught:
                bus.read(C.c_void_p(10), "as")
            self.assertEqual(caught.exception.limit_category, "array_items")
        finally:
            bus.close()
            right.close()

    def test_invalid_boolean_and_container(self):
        for kind in ("boolean", "container"):
            library = Decoder("b", 2) if kind == "boolean" else Decoder("as", [])
            if kind == "container":
                library.sd_bus_message_enter_container.impl = lambda *args: 0
            left, right = socket.socketpair()
            bus = p.Bus(library, left, time.monotonic() + 1)
            try:
                with self.assertRaisesRegex(p.Stop, "type"):
                    bus.read(C.c_void_p(10), "b" if kind == "boolean" else "as")
            finally:
                bus.close()
                right.close()

    def test_variant_get_success(self):
        library = Decoder("as", ["x.target"])
        library.events.insert(0, ("enter", (b"v", b"as")))
        library.events.extend([("end", 1), ("exit", 1), ("end", 1)])
        library.sd_bus_message_get_signature.impl = lambda *args: b"v"

        def message(bus, output, *args):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 9
            return 0

        def call(bus, request, timeout, error, output):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 10
            return 1

        library.sd_bus_message_new_method_call.impl = message
        library.sd_bus_call.impl = call
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            result = bus.request(
                "/org/freedesktop/systemd1/unit/x", p.PROPERTIES, "Get", (p.UNIT, "Wants"), "as"
            )
            self.assertEqual(result, ["x.target"])
            self.assertEqual(library.events, [])
            self.assertEqual(len(library.sd_bus_message_append_basic.calls), 2)
        finally:
            bus.close()
            right.close()

    def test_new_success_timeout_releases_bus(self):
        library = Library()
        left, right = socket.socketpair()
        try:
            with self.assertRaisesRegex(p.Stop, "timeout"):
                p.Bus(library, left, time.monotonic() - 1)
            self.assertEqual(library.closed, 1)
            self.assertGreaterEqual(left.fileno(), 0)
        finally:
            left.close()
            right.close()

    def test_detach_failure_releases_bus(self):
        library = Library()

        class Channel:
            def detach(self):
                raise OSError(5, "fixture")

        with self.assertRaises(OSError):
            p.Bus(library, Channel(), time.monotonic() + 1)
        self.assertEqual(library.closed, 1)

    def test_jobs_drift(self):
        bus = FakeBus()
        original = bus.request
        job_calls = []

        def request(*args):
            result = original(*args)
            if args[2] == "ListJobs":
                job_calls.append(1)
                if len(job_calls) == 2:
                    return [[1, "x.service", "start", "waiting", "/job/1", "/unit/x"]]
            return result

        bus.request = request
        with self.assertRaisesRegex(p.Stop, "changed"):
            p.collect(bus, lambda: None)

    def test_string_oversize(self):
        library = Decoder("s", "x" * (p.MAX_STRING + 1))
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            with self.assertRaisesRegex(p.Stop, "limit") as caught:
                bus.read(C.c_void_p(10), "s")
            self.assertEqual(caught.exception.limit_category, "string_bytes")
        finally:
            bus.close()
            right.close()

    def test_count_only_bounded_without_reading_values(self):
        for size in (0, 64, 65, 511, 512, 513, 1000):
            with self.subTest(size=size):
                library = CountLibrary(size)
                left, right = socket.socketpair()
                bus = p.Bus(library, left, time.monotonic() + 2)
                try:
                    result = bus.count_units()
                    self.assertEqual(result["exact_count"], size if size <= 512 else None)
                    self.assertEqual(result["validated_prefix_count"], min(size, 512))
                    self.assertEqual(result["has_more"], size > 512)
                    self.assertEqual(result["sample"], "LATER_NONATOMIC")
                    self.assertEqual(result["receive_allocation"], "NOT_BOUNDED")
                    self.assertEqual(library.skipped, min(size, 512))
                    self.assertFalse(library.sd_bus_message_read_basic.calls)
                    self.assertEqual(library.unrefs, [10, 9])
                finally:
                    bus.close()
                    right.close()

    def test_observe_count_preserves_stop_and_does_not_emit_unit_values(self):
        error = p.Stop("limit")
        error.limit_category, error.limit_context = "array_items", "ListUnits"

        class Counter:
            def count_units(self):
                return {
                    "exact_count": 65,
                    "validated_prefix_count": 65,
                    "has_more": False,
                    "sample": "LATER_NONATOMIC",
                    "receive_allocation": "NOT_BOUNDED",
                }

            def close(self):
                pass

        base = types.SimpleNamespace(file_bytes=lambda *args: (b"\x7fELF", "stable"))
        for failure in (False, True):

            def recheck(failure=failure):
                if failure:
                    raise p.Stop("changed")

            with (
                patch.object(p, "ordinary_identity"),
                patch.object(p, "metadata", return_value=base),
                patch.object(p.C, "CDLL"),
                patch.object(p, "Bus", return_value=Counter()),
                patch.object(p, "verified_channel", return_value=nullcontext((None, recheck, {}))),
                patch.object(p, "collect", side_effect=error),
            ):
                result = p.observe()
            self.assertEqual(result["reason"], "limit")
            self.assertEqual(result["status"], "STOP")
            self.assertFalse(result["complete"])
            self.assertEqual("list_units_count" in result, not failure)
            self.assertNotIn("unit-name-secret", json.dumps(result))

    def test_count_diagnostic_discards_on_identity_or_timeout(self):
        class Counter:
            def count_units(self):
                return {"exact_count": 65}

        for failing in ("before", "after", "library", "timeout"):
            calls = []

            def recheck(calls=calls, failing=failing):
                calls.append(1)
                if (failing == "before" and len(calls) == 1) or (failing == "after" and len(calls) == 2):
                    raise p.Stop("changed")

            def verify(failing=failing):
                if failing == "library":
                    raise p.Stop("changed")

            deadline = time.monotonic() + (-1 if failing == "timeout" else 1)
            self.assertIsNone(p.diagnostic_count(Counter(), recheck, verify, deadline))
        self.assertEqual(
            p.diagnostic_count(Counter(), lambda: None, lambda: None, time.monotonic() + 1),
            {"exact_count": 65},
        )

    def test_count_does_not_inspect_malformed_entry_after_ceiling(self):
        library = CountLibrary(513, fail_skip=512)
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            self.assertTrue(bus.count_units()["has_more"])
            self.assertEqual(library.skipped, 512)
        finally:
            bus.close()
            right.close()

    def test_count_failure_and_timeout_release_messages(self):
        for kind in ("skip", "signature", "timeout"):
            with self.subTest(kind=kind):
                library = CountLibrary(
                    10, fail_skip=2 if kind == "skip" else None, bad_signature=kind == "signature"
                )
                left, right = socket.socketpair()
                bus = p.Bus(library, left, time.monotonic() + 1)
                if kind == "timeout":
                    bus.deadline = time.monotonic() - 1
                try:
                    with self.assertRaises(p.Stop):
                        bus.count_units()
                    self.assertEqual(library.unrefs, [10, 9] if kind != "timeout" else [9])
                finally:
                    bus.close()
                    right.close()

    def test_null_string_has_distinct_category(self):
        library = Decoder("s", "x")
        library.sd_bus_message_read_basic.impl = lambda *args: 1
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            with self.assertRaises(p.Stop) as caught:
                bus.read(C.c_void_p(10), "s")
            self.assertEqual(caught.exception.limit_category, "null_string")
        finally:
            bus.close()
            right.close()

    def test_collect_defensive_array_limits_name_request(self):
        for method in ("ListUnits", "ListJobs"):
            bus = FakeBus()
            original = bus.request

            def request(path, interface, actual, args, signature, method=method, original=original):
                return (
                    [None] * 65
                    if actual == method
                    else original(path, interface, actual, args, signature)
                )

            bus.request = request
            with self.assertRaises(p.Stop) as caught:
                p.collect(bus, lambda: None)
            self.assertEqual(caught.exception.limit_category, "array_items")
            self.assertEqual(caught.exception.limit_context, method)

    def test_limit_context_is_finite_and_no_raw_values_escape(self):
        for context, field, expected in (
            ("manager_property", "UnitPath", "UnitPath"),
            ("unit_property", "Wants", "Wants"),
            ("ListUnits", "Wants", None),
            ("secret-context", "secret-field", None),
        ):
            error = p.Stop("limit")
            error.limit_category = "array_items"
            error.limit_context = context
            error.limit_field = field
            with patch.object(p, "ordinary_identity"), patch.object(p, "metadata", side_effect=error):
                result = p.observe()
            self.assertEqual(result["status"], "STOP")
            self.assertFalse(result["complete"])
            self.assertEqual(result["limit_category"], "array_items")
            self.assertEqual(result["limit_field"], expected)
            self.assertNotIn("secret", json.dumps(result))

    def test_request_attaches_allowlisted_context_to_decoder_limit(self):
        library = Decoder("as", ["x"] * 65)
        library.events.insert(0, ("enter", (b"v", b"as")))
        library.sd_bus_message_get_signature.impl = lambda *args: b"v"

        def message(bus, output, *args):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 9
            return 0

        def call(bus, request, timeout, error, output):
            C.cast(output, C.POINTER(C.c_void_p))[0] = 10
            return 1

        library.sd_bus_message_new_method_call.impl = message
        library.sd_bus_call.impl = call
        left, right = socket.socketpair()
        bus = p.Bus(library, left, time.monotonic() + 1)
        try:
            with self.assertRaises(p.Stop) as caught:
                bus.request(p.OBJECT, p.PROPERTIES, "Get", (p.MANAGER, "UnitPath"), "as")
            self.assertEqual(caught.exception.limit_context, "manager_property")
            self.assertEqual(caught.exception.limit_field, "UnitPath")
            self.assertEqual(caught.exception.limit_category, "array_items")
        finally:
            bus.close()
            right.close()

    def test_wrong_method_signature_rejected_before_ffi(self):
        bus = object.__new__(p.Bus)
        with self.assertRaisesRegex(p.Stop, "method"):
            bus.request(p.OBJECT, p.MANAGER, "ListUnits", (), "s")
        with self.assertRaisesRegex(p.Stop, "method"):
            bus.request("/org/freedesktop/systemd1/unit/x", p.PROPERTIES, "Get", (p.UNIT, "Job"), "s")

    def test_baseline_stop_reason_preserved_in_observe(self):
        baseline = p.metadata()
        for reason in ("changed", "private_endpoint", "timeout"):
            with (
                self.subTest(reason=reason),
                patch.object(p, "ordinary_identity"),
                patch.object(p, "metadata", side_effect=baseline.Stop(reason)),
            ):
                result = p.observe()
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["status"], "STOP")
                self.assertFalse(result["complete"])
                self.assertNotIn("snapshot", result)

    def test_root_rejected_before_loader_and_bootstrap(self):
        with (
            patch.object(p.os, "getuid", return_value=0),
            patch.object(p, "metadata") as load,
            patch.object(p.C, "CDLL") as library,
        ):
            result = p.observe()
        self.assertEqual(result["reason"], "identity")
        load.assert_not_called()
        library.assert_not_called()

    def test_source_nonregular_rejected_before_read(self):
        info = types.SimpleNamespace(st_mode=p.stat.S_IFIFO, st_size=0)
        with (
            patch.object(p.os, "open", return_value=77),
            patch.object(p.os, "fstat", return_value=info),
            patch.object(p.os, "close") as close,
            patch.object(p.os, "fdopen") as read,
        ):
            with self.assertRaisesRegex(p.Stop, "source"):
                p.metadata()
        close.assert_called_once_with(77)
        read.assert_not_called()

    def test_entry_exit_and_receipt_contract(self):
        for status, expected in (("STOP", 2), ("OBSERVED", 0)):
            result = {"status": status, "complete": status == "OBSERVED", "qualification": "NOT_RUN"}

            def capture(argv, env, deadline, limit, result=result):
                self.assertEqual(argv[1:3], ["-I", "-c"])
                compile(argv[3], "worker", "exec")
                self.assertNotIn("--worker", argv)
                self.assertEqual(limit, p.MAX_RECEIPT)
                return {"reason": None, "exit_code": 0, "stderr_bytes": 0, "stdout": p.encode(result)}

            output = types.SimpleNamespace(buffer=io.BytesIO())
            base = types.SimpleNamespace(capture=capture)
            with (
                self.subTest(status=status),
                patch.object(p.sys, "argv", [str(PATH)]),
                patch.object(p.sys, "stdout", output),
                patch.object(p, "ordinary_identity"),
                patch.object(p, "metadata", return_value=base),
            ):
                code = p.entry()
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output.buffer.getvalue()), result)

    def test_worker_flag_and_bootstrap_errors_are_finite_nonzero(self):
        for argv, error, reason in (
            ([str(PATH), "--worker"], None, "format"),
            ([str(PATH)], p.Stop("source"), "source"),
        ):
            output = types.SimpleNamespace(buffer=io.BytesIO())
            with (
                patch.object(p.sys, "argv", argv),
                patch.object(p.sys, "stdout", output),
                patch.object(p, "ordinary_identity"),
                patch.object(p, "metadata", side_effect=error) as load,
            ):
                code = p.entry()
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.buffer.getvalue())["reason"], reason)
            if len(argv) > 1:
                load.assert_not_called()

    def test_watchdog_failure_is_finite_nonzero(self):
        result = {"reason": "timeout", "exit_code": -9, "stderr_bytes": 0, "stdout": b"partial"}
        base = types.SimpleNamespace(capture=lambda *args, **kwargs: result)
        output = types.SimpleNamespace(buffer=io.BytesIO())
        with (
            patch.object(p.sys, "argv", [str(PATH)]),
            patch.object(p.sys, "stdout", output),
            patch.object(p, "ordinary_identity"),
            patch.object(p, "metadata", return_value=base),
        ):
            self.assertEqual(p.entry(), 2)
        receipt = json.loads(output.buffer.getvalue())
        self.assertEqual(receipt["reason"], "timeout")
        self.assertFalse(receipt["complete"])

    def test_source_no_alternative_bus_helpers(self):
        source = PATH.read_text()
        for name in ("sd_bus_open", "sd_bus_default", "sd_bus_set_address", "busctl", "LoadUnit"):
            self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()
