"""Offline composition controls for private checkpoints and public failure ordinals.

Synthetic receipts are written at the mocked subprocess boundary. No pytest
child, native workload, service, host probe or network operation is launched.
"""

import errno
from pathlib import Path
import sys

import pytest

from scripts import ci_private_transport as transport
from scripts import ci_test_shards as ci
from tests import test_ci_failure_diagnostic as failure
from tests import test_ci_failure_diagnostics as checkpoints
from tests.test_ci_private_transport import CANARY, execution, expected_collection, fake_execution


@pytest.fixture
def combined(tmp_path, monkeypatch):
    case = failure._case(tmp_path, monkeypatch)
    case.checkpoints = tmp_path / "diagnostic-private"
    case.args = [
        "run",
        *failure._args(case),
        "--failure-diagnostic",
        "--diagnostic-directory",
        str(case.checkpoints),
        "--diagnostic-invocation",
        checkpoints.INVOCATION,
    ]
    return case


def _close_fault(monkeypatch, *paths, fail=True):
    """Fail real owned-stream close, rather than replacing the workload/logger."""
    real_fdopen = transport.os.fdopen
    opened = []

    class CloseFault:
        def __init__(self, stream):
            self.stream = stream
            self.fd = stream.fileno()
            self.close_calls = 0
            self.error = OSError(CANARY)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            self.close_calls += 1
            self.stream.close()
            if fail:
                raise self.error

    def fdopen(fd, *args, **kwargs):
        stream = real_fdopen(fd, *args, **kwargs)
        info = transport.os.fstat(fd)
        for path in paths:
            if not path.exists():
                continue
            target = path.stat()
            if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
                stream = CloseFault(stream)
                opened.append(stream)
                break
        return stream

    monkeypatch.setattr(transport.os, "fdopen", fdopen)
    return opened


def _diagnostic_faults(case, monkeypatch, fault):
    def broken(*_args, **_kwargs):
        raise OSError(CANARY)

    if fault in {"sidecar", "all"}:
        writer = transport._write_json

        def write(path, value):
            if Path(path) == case.state_path:
                broken()
            return writer(path, value)

        monkeypatch.setattr(transport, "_write_json", write)
    if fault in {"checkpoint", "all"}:
        monkeypatch.setattr(transport._Diagnostic, "failed", broken)
    if fault in {"traceback", "all"}:
        monkeypatch.setattr(transport.traceback, "print_exc", broken)
    paths = []
    if fault in {"inner-close", "all"}:
        paths.append(case.private / (failure.PHASE + ".log"))
    if fault in {"outer-close", "all"}:
        paths.append(case.checkpoints / (failure.PHASE + ".log"))
    return _close_fault(monkeypatch, *paths) if paths else []


def _assert_closed(streams):
    for stream in streams:
        assert stream.stream.closed and stream.close_calls == 1
        with pytest.raises(OSError) as caught:
            transport.os.fstat(stream.fd)
        assert caught.value.errno == errno.EBADF


def test_combined_success_keeps_strict_receipt_and_completed_checkpoint(
    combined, monkeypatch, tmp_path, capsys
):
    receipt = execution(combined.example.plan, failure.PHASE)
    commands = fake_execution(monkeypatch, receipt)
    assert combined.state_path.exists()

    assert transport.main(combined.args) == 0

    assert len(commands) == 1
    assert not combined.state_path.exists()
    assert ci.read(combined.public / combined.receipt_path.name) == expected_collection(
        receipt, combined.example.manifest
    )
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "publication",
        "state": "COMPLETED",
        "code": "COMPLETED",
    }
    assert not (combined.public / "failure.json").exists()
    published = checkpoints.publish(tmp_path, combined.checkpoints, status="success")
    assert published["operations"] == [checkpoints.record(combined.checkpoints)]
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", "")


@pytest.mark.parametrize("returncode", (1, 17, -15), ids=("pytest-failure", "child-17", "signal-15"))
def test_combined_failed_child_keeps_code_checkpoint_and_safe_ordinal(
    combined, monkeypatch, tmp_path, capsys, returncode
):
    report = failure._failure_report(combined.receipt)
    commands = fake_execution(monkeypatch, combined.receipt, returncode)

    assert transport.main(combined.args) == returncode

    assert len(commands) == 1
    assert ci.read(combined.state_path) == {**combined.state, "returncode": returncode}
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution",
        "state": "FAILED",
        "code": "NONZERO_EXIT",
    }
    assert not (combined.public / combined.receipt_path.name).exists()
    result = failure._assert_public(
        combined,
        failure._export(combined),
        status="recorded" if returncode == 1 else "suppressed",
        stage="pytest",
    )
    if returncode == 1:
        assert result["tests"] == [
            {
                "id": f"public-{combined.example.manifest['nodes'].index(report['nodeid'])}",
                "stage": "call",
                "status": "failed",
                "kind": None,
            }
        ]
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", transport.ERROR + "\n")


@pytest.mark.parametrize(
    "returncode,close_fault,expected_code,stage",
    ((17, False, 17, "pytest"), (0, True, 1, "log_close"), (17, True, 17, "pytest")),
    ids=("child-no-fault", "close-only", "child-and-close"),
)
def test_combined_inner_log_close_preserves_child_code(
    combined, monkeypatch, tmp_path, capsys, returncode, close_fault, expected_code, stage
):
    receipt = execution(combined.example.plan, failure.PHASE)
    commands = fake_execution(monkeypatch, receipt, returncode)
    streams = []
    if close_fault:
        streams = _close_fault(monkeypatch, combined.private / (failure.PHASE + ".log"))

    assert transport.main(combined.args) == expected_code

    assert len(commands) == 1
    if close_fault:
        assert len(streams) == 1
        _assert_closed(streams)
    assert ci.read(combined.state_path) == {
        **combined.state,
        "stage": stage,
        "returncode": returncode,
    }
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution" if returncode else "coverage_validation",
        "state": "FAILED",
        "code": "NONZERO_EXIT" if returncode else "INTERNAL_ERROR",
    }
    assert not (combined.public / combined.receipt_path.name).exists()
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", transport.ERROR + "\n")


@pytest.mark.parametrize(
    "damage", ("bad-plan", "missing-manifest", "bad-sha", "bad-invocation", "checkpoint-setup")
)
def test_combined_prework_failure_clears_stale_failure_state(
    combined, monkeypatch, tmp_path, capsys, damage
):
    def no_child(*_args, **_kwargs):
        pytest.fail("Invalid input must not reach the child boundary")

    monkeypatch.setattr(transport.subprocess, "run", no_child)
    if damage == "bad-plan":
        combined.plan_path.write_text(CANARY)
    elif damage == "missing-manifest":
        combined.manifest_path.unlink()
    elif damage in {"bad-sha", "bad-invocation"}:
        option = "--sha" if damage == "bad-sha" else "--diagnostic-invocation"
        combined.args[combined.args.index(option) + 1] = CANARY
    else:

        def initialization_failure(*_args, **_kwargs):
            assert not combined.state_path.exists()
            raise OSError(CANARY)

        monkeypatch.setattr(transport._Diagnostic, "__init__", initialization_failure)
    assert combined.state_path.exists()

    assert transport.main(combined.args) == 1

    assert not combined.state_path.exists()
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(
        combined, failure._export(combined), status="unavailable", stage="unavailable"
    )
    published = checkpoints.publish(tmp_path, combined.checkpoints)
    expected_state = "UNAVAILABLE" if damage in {"bad-invocation", "checkpoint-setup"} else "FAILED"
    assert published["operations"][0]["state"] == expected_state
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", transport.ERROR + "\n")


@pytest.mark.parametrize(
    "fault", ("none", "sidecar", "checkpoint", "traceback", "inner-close", "outer-close", "all")
)
@pytest.mark.parametrize("kind,exitcode", (("child", 17), ("interrupt", 130)))
def test_combined_diagnostic_io_preserves_actual_workload_exit(
    combined, monkeypatch, tmp_path, capsys, fault, kind, exitcode
):
    if kind == "child":
        commands = fake_execution(monkeypatch, combined.receipt, 17)
    else:
        commands = []

        def interrupt(command, **kwargs):
            commands.append(command)
            kwargs["stdout"].write(CANARY)
            raise KeyboardInterrupt(CANARY)

        monkeypatch.setattr(transport.subprocess, "run", interrupt)
    streams = _diagnostic_faults(combined, monkeypatch, fault)

    assert transport.main(combined.args) == exitcode

    assert len(commands) == 1
    assert len(streams) == (2 if fault == "all" else int(fault.endswith("close")))
    _assert_closed(streams)
    if fault in {"sidecar", "all"}:
        assert not combined.state_path.exists()
    else:
        assert ci.read(combined.state_path) == {
            **combined.state,
            "returncode": 17 if kind == "child" else None,
        }
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution",
        "state": "INCOMPLETE" if fault in {"checkpoint", "all"} else "FAILED",
        "code": (
            "NO_TERMINAL_RECORD"
            if fault in {"checkpoint", "all"}
            else "NONZERO_EXIT"
            if kind == "child"
            else "INTERRUPTED"
        ),
    }
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(
        combined,
        failure._export(combined),
        status="suppressed" if kind == "child" and fault not in {"sidecar", "all"} else "unavailable",
    )
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", transport.ERROR + "\n")


@pytest.mark.parametrize(
    "fault", ("none", "sidecar", "checkpoint", "traceback", "inner-close", "outer-close", "all")
)
@pytest.mark.parametrize("kind", ("child", "interrupt"))
def test_combined_direct_wrapper_preserves_exception_identity_and_cause(
    combined, monkeypatch, tmp_path, capsys, fault, kind
):
    original = SystemExit(17) if kind == "child" else KeyboardInterrupt(CANARY)
    cause = RuntimeError(CANARY)
    context = ValueError(CANARY)
    original.add_note("Existing producer note.")
    commands = []

    def fail(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(CANARY)
        try:
            raise context
        except ValueError:
            raise original from cause

    monkeypatch.setattr(transport.subprocess, "run", fail)
    streams = _diagnostic_faults(combined, monkeypatch, fault)
    args = transport._parser().parse_args(combined.args)

    with pytest.raises(type(original)) as caught:
        transport._execute_diagnosed(args)

    assert caught.value is original
    assert caught.value.__cause__ is cause
    assert caught.value.__context__ is context
    assert caught.value.__suppress_context__ is True
    assert not getattr(original, "_ci_diagnostic_errors", ())
    assert getattr(original, "_ci_log_close_errors", ()) == tuple(
        stream.error for stream in reversed(streams)
    )
    assert original.__notes__[0] == "Existing producer note."
    assert len(original.__notes__) == 1 + len(streams)
    assert CANARY not in " ".join(original.__notes__)
    assert len(commands) == 1
    _assert_closed(streams)
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(combined, failure._export(combined), status="unavailable")
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", "")


@pytest.mark.parametrize("callback", ("traceback", "checkpoint"))
@pytest.mark.parametrize("secondary_kind", ("interrupt", "exit"))
@pytest.mark.parametrize("chain", ("explicit", "implicit"))
def test_combined_fatal_diagnostic_callback_preserves_original_chain(
    combined, monkeypatch, tmp_path, capsys, callback, secondary_kind, chain
):
    original = SystemExit(17)
    cause = RuntimeError(CANARY)
    context = ValueError(CANARY)
    secondary = KeyboardInterrupt(CANARY) if secondary_kind == "interrupt" else SystemExit(99)
    commands, callbacks = [], []

    def fail(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(CANARY)
        try:
            raise context
        except ValueError:
            if chain == "explicit":
                raise original from cause
            raise original  # noqa: B904 - Exercise preservation of an implicit exception context.

    def diagnostic_failure(*_args, **_kwargs):
        callbacks.append(callback)
        raise secondary

    monkeypatch.setattr(transport.subprocess, "run", fail)
    if callback == "traceback":
        monkeypatch.setattr(transport.traceback, "print_exc", diagnostic_failure)
    else:
        monkeypatch.setattr(transport._Diagnostic, "failed", diagnostic_failure)
    args = transport._parser().parse_args(combined.args)

    with pytest.raises(SystemExit) as caught:
        transport._execute_diagnosed(args)

    assert caught.value is original and original.code == 17
    assert original.__cause__ is (cause if chain == "explicit" else None)
    assert original.__context__ is context
    assert original.__suppress_context__ is (chain == "explicit")
    assert len(commands) == 1 and callbacks == [callback]
    assert ci.read(combined.state_path) == {**combined.state, "returncode": None}
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution",
        "state": "INCOMPLETE",
        "code": "NO_TERMINAL_RECORD",
    }
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(combined, failure._export(combined), status="unavailable")
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", "")


@pytest.mark.parametrize("callback", ("traceback", "checkpoint"))
@pytest.mark.parametrize("secondary_kind", ("interrupt", "exit"))
@pytest.mark.parametrize("chain", ("explicit", "implicit"))
@pytest.mark.parametrize("close_fault", (False, True), ids=("no-fault", "close-fault"))
def test_combined_fatal_callback_retains_private_secondary_cleanup(
    combined, monkeypatch, tmp_path, capsys, callback, secondary_kind, chain, close_fault
):
    original = SystemExit(17)
    cause, context = RuntimeError(CANARY), ValueError(CANARY)
    secondary = KeyboardInterrupt(CANARY) if secondary_kind == "interrupt" else SystemExit(99)
    previous = RuntimeError(CANARY)
    previous_close = OSError(CANARY)
    original._ci_diagnostic_errors = (previous,)
    original._ci_log_close_errors = (previous_close,)
    original.add_note("Existing producer note.")
    metadata = original.__dict__
    notes, close_errors = original.__notes__, original._ci_log_close_errors
    commands, callbacks = [], []

    def fail(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(CANARY)
        try:
            raise context
        except ValueError:
            if chain == "explicit":
                raise original from cause
            raise original  # noqa: B904 - Exercise preservation of an implicit exception context.

    def diagnostic_failure(*_args, **_kwargs):
        callbacks.append(callback)
        raise secondary

    monkeypatch.setattr(transport.subprocess, "run", fail)
    target = transport.traceback if callback == "traceback" else transport._Diagnostic
    monkeypatch.setattr(target, "print_exc" if callback == "traceback" else "failed", diagnostic_failure)
    streams = _close_fault(
        monkeypatch, combined.checkpoints / (failure.PHASE + ".log"), fail=close_fault
    )
    args = transport._parser().parse_args(combined.args)
    stdout, stderr = sys.stdout, sys.stderr

    with pytest.raises(SystemExit) as caught:
        transport._execute_diagnosed(args)

    assert caught.value is original and original.code == 17
    assert original.__cause__ is (cause if chain == "explicit" else None)
    assert original.__context__ is context
    assert original.__suppress_context__ is (chain == "explicit")
    assert sys.stdout is stdout and sys.stderr is stderr
    assert len(commands) == 1 and callbacks == [callback]
    assert len(streams) == 1
    _assert_closed(streams)
    assert original.__dict__ is metadata
    assert original._ci_log_close_errors is close_errors
    assert original._ci_diagnostic_errors[0] is previous
    assert len(original._ci_diagnostic_errors) == 2
    assert original._ci_diagnostic_errors[1] is secondary
    retained = original._ci_diagnostic_errors[1]
    if close_fault:
        assert retained._ci_log_close_errors[0] is streams[0].error
        assert len(retained._ci_log_close_errors) == 1
        assert len(retained.__notes__) == 1
        assert CANARY not in retained.__notes__[0]
    else:
        assert "_ci_log_close_errors" not in retained.__dict__
        assert "__notes__" not in retained.__dict__
    assert original.__notes__ is notes
    assert notes[0] == "Existing producer note."
    assert len(notes) == 2 and "_ci_diagnostic_errors" in notes[1]
    assert CANARY not in " ".join(notes)
    assert ci.read(combined.state_path) == {**combined.state, "returncode": None}
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution",
        "state": "INCOMPLETE",
        "code": "NO_TERMINAL_RECORD",
    }
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(combined, failure._export(combined), status="unavailable")
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", "")


@pytest.mark.parametrize("callback", ("traceback", "checkpoint"))
@pytest.mark.parametrize(
    "metadata_kind", ("opaque", "tuple-subclass", "notes-subclass", "hostile-exception")
)
def test_combined_secondary_evidence_bypasses_hostile_metadata_hooks(
    combined, monkeypatch, tmp_path, capsys, callback, metadata_kind
):
    hooks = []

    def reject(*_args, **_kwargs):
        hooks.append("unexpected metadata hook")
        raise AssertionError("Private evidence must not invoke metadata hooks")

    class HostileTuple(tuple):
        __iter__ = __add__ = __len__ = __str__ = __repr__ = reject

    class HostileNotes(list):
        append = __iter__ = __str__ = __repr__ = reject

    class HostileExit(SystemExit):
        def __getattribute__(self, name):
            if name in {"__dict__", "__notes__", "_ci_diagnostic_errors", "add_note", "with_traceback"}:
                reject()
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            if name in {
                "__dict__",
                "__notes__",
                "_ci_diagnostic_errors",
                "__cause__",
                "__context__",
                "__suppress_context__",
            }:
                reject()
            super().__setattr__(name, value)

        __str__ = __repr__ = reject

    original = HostileExit(17) if metadata_kind == "hostile-exception" else SystemExit(17)
    cause, context = RuntimeError(CANARY), ValueError(CANARY)
    secondary = KeyboardInterrupt(CANARY)
    prior = RuntimeError(CANARY)
    previous = (
        HostileTuple((prior,))
        if metadata_kind == "tuple-subclass"
        else object()
        if metadata_kind == "opaque"
        else (prior,)
    )
    notes = HostileNotes(["Existing producer note."]) if metadata_kind == "notes-subclass" else []
    state = BaseException.__dict__["__dict__"].__get__(original)
    state["_ci_diagnostic_errors"] = previous
    state["__notes__"] = notes
    commands, callbacks = [], []

    def fail(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(CANARY)
        try:
            raise context
        except ValueError:
            raise original from cause

    def diagnostic_failure(*_args, **_kwargs):
        callbacks.append(callback)
        raise secondary

    monkeypatch.setattr(transport.subprocess, "run", fail)
    # Isolate bookkeeping from the traceback module's intentional formatting.
    monkeypatch.setattr(transport.traceback, "print_exc", lambda: None)
    target = transport.traceback if callback == "traceback" else transport._Diagnostic
    monkeypatch.setattr(target, "print_exc" if callback == "traceback" else "failed", diagnostic_failure)
    streams = _close_fault(monkeypatch, combined.checkpoints / (failure.PHASE + ".log"))
    args = transport._parser().parse_args(combined.args)

    with pytest.raises(SystemExit) as caught:
        transport._execute_diagnosed(args)

    assert caught.value is original
    assert SystemExit.code.__get__(original) == 17
    assert BaseException.__cause__.__get__(original) is cause
    assert BaseException.__context__.__get__(original) is context
    assert BaseException.__suppress_context__.__get__(original) is True
    assert len(commands) == 1 and callbacks == [callback]
    assert len(streams) == 1
    _assert_closed(streams)
    assert BaseException.__dict__["__dict__"].__get__(original) is state
    retained = state["_ci_diagnostic_errors"]
    assert type(retained) is tuple and len(retained) == 2
    assert retained[0] is (prior if type(previous) is tuple else previous)
    assert retained[1] is secondary
    assert secondary._ci_log_close_errors[0] is streams[0].error
    assert len(secondary._ci_log_close_errors) == 1
    assert state["__notes__"] is notes
    if metadata_kind == "notes-subclass":
        assert list.__len__(notes) == 1
        assert list.__getitem__(notes, 0) == "Existing producer note."
    else:
        assert len(notes) == 1 and "_ci_diagnostic_errors" in notes[0]
        assert CANARY not in notes[0]
    assert not hooks
    assert not (combined.public / combined.receipt_path.name).exists()
    failure._assert_public(combined, failure._export(combined), status="unavailable")
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", "")


@pytest.mark.parametrize("close_fault", (False, True), ids=("no-fault", "close-fault"))
@pytest.mark.parametrize("kind", ("exception", "child", "interrupt"))
def test_owned_log_preserves_primary_chain_and_private_cleanup_evidence(
    tmp_path, monkeypatch, capsys, close_fault, kind
):
    original = {
        "exception": RuntimeError(CANARY),
        "child": SystemExit(17),
        "interrupt": KeyboardInterrupt(CANARY),
    }[kind]
    cause = RuntimeError(CANARY)
    context = ValueError(CANARY)
    original.add_note("Existing producer note.")
    directory = tmp_path / "private"
    path = directory / "direct.log"
    streams = _close_fault(monkeypatch, path) if close_fault else []
    stdout, stderr = sys.stdout, sys.stderr

    with pytest.raises(type(original)) as caught, transport._log(directory, path.name) as stream:
        stream.write(CANARY)
        print(CANARY, file=sys.stderr)
        try:
            raise context
        except ValueError:
            raise original from cause

    assert caught.value is original
    assert caught.value.__cause__ is cause
    assert caught.value.__context__ is context
    assert caught.value.__suppress_context__ is True
    assert stream.closed
    assert sys.stdout is stdout and sys.stderr is stderr
    assert getattr(original, "_ci_log_close_errors", ()) == tuple(item.error for item in streams)
    assert original.__notes__[0] == "Existing producer note."
    assert len(original.__notes__) == 1 + len(streams)
    assert CANARY not in " ".join(original.__notes__)
    assert path.read_text() == CANARY + CANARY + "\n"
    _assert_closed(streams)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("close_fault", (False, True), ids=("no-fault", "close-only"))
def test_owned_log_success_or_close_only_control(tmp_path, monkeypatch, capsys, close_fault):
    directory = tmp_path / "private"
    path = directory / "direct.log"
    streams = _close_fault(monkeypatch, path) if close_fault else []
    stdout, stderr = sys.stdout, sys.stderr

    def workload():
        with transport._log(directory, path.name) as stream:
            print(CANARY)
        assert stream.closed

    if close_fault:
        with pytest.raises(OSError) as caught:
            workload()
        assert len(streams) == 1
        assert caught.value is streams[0].error
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    else:
        workload()

    assert sys.stdout is stdout and sys.stderr is stderr
    assert path.read_text() == CANARY + "\n"
    _assert_closed(streams)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "damage",
    (
        "late-report",
        "late-subtest",
        "extra-field",
        "receipt-sha",
        "receipt-phase",
        "receipt-code",
        "state-sha",
        "state-phase",
        "state-run",
        "state-attempt",
        "plan-sha",
    ),
)
def test_combined_malformed_or_mismatched_evidence_suppresses_entire_list(
    combined, monkeypatch, tmp_path, capsys, damage
):
    known_failure = failure._failure_report(combined.receipt)
    if damage in {"late-report", "late-subtest"}:
        unknown = dict(known_failure, nodeid=known_failure["nodeid"] + CANARY)
        combined.receipt["reports" if damage == "late-report" else "subtests"].append(unknown)
    elif damage == "extra-field":
        combined.receipt["reports"][-1]["private_detail"] = CANARY
    elif damage in {"receipt-sha", "receipt-phase", "receipt-code"}:
        key, value = {
            "receipt-sha": ("sha", "b" * 40),
            "receipt-phase": ("phase", "shard-1"),
            "receipt-code": ("exitstatus", 2),
        }[damage]
        combined.receipt[key] = value
    commands = fake_execution(monkeypatch, combined.receipt, 1)

    assert transport.main(combined.args) == 1

    assert len(commands) == 1
    assert checkpoints.record(combined.checkpoints) == {
        "operation": failure.PHASE,
        "stage": "execution",
        "state": "FAILED",
        "code": "NONZERO_EXIT",
    }
    if damage.startswith("state-"):
        state = ci.read(combined.state_path)
        key = damage.removeprefix("state-")
        state[key] = {"sha": "b" * 40, "phase": "shard-1", "run": "42", "attempt": "9"}[key]
        ci.write(combined.state_path, state)
    elif damage == "plan-sha":
        combined.plan["sha"] = "b" * 40
        ci.write(combined.plan_path, combined.plan)
    assert known_failure in combined.receipt["reports"]
    result = failure._assert_public(combined, failure._export(combined), status="suppressed")
    assert result["tests"] == []
    assert not (combined.public / combined.receipt_path.name).exists()
    checkpoints.publish(tmp_path, combined.checkpoints)
    assert checkpoints.no_public_canary(tmp_path, capsys) == ("", transport.ERROR + "\n")
