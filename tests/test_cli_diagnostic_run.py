# SPDX-License-Identifier: Apache-2.0
"""Independent collector controls; no installed-audit runner or qualification."""

import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from qualification.installed_cli import diagnostic_run


def fake_tracer(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-strace"
    path.write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')
    path.chmod(0o700)
    monkeypatch.setattr(diagnostic_run.shutil, "which", lambda *args, **kwargs: str(path))
    monkeypatch.setattr(diagnostic_run, "_terminal_observed", lambda *args, **kwargs: False)


@pytest.mark.parametrize("stage", ["syscalls_create", "argv_conversion", "popen"])
@pytest.mark.parametrize("fail", [False, True], ids=["positive", "failure"])
def test_setup_closes_descriptors_and_preserves_original_exception(tmp_path, monkeypatch, stage, fail):
    fake_tracer(tmp_path, monkeypatch)
    output = tmp_path / "diagnostic"
    primary = OSError(errno.ENOSPC, "synthetic setup failure")
    opened = {"events.jsonl": [], "syscalls.log": []}
    launches = []
    original_open, original_popen = os.open, subprocess.Popen

    def tracked_open(path, *args, **kwargs):
        name = Path(path).name
        if fail and stage == "syscalls_create" and name == "syscalls.log":
            raise primary
        fd = original_open(path, *args, **kwargs)
        if name in opened:
            opened[name].append(fd)
        return fd

    class Argument:
        def __str__(self):
            if fail and stage == "argv_conversion":
                raise primary
            return "raise SystemExit(7)"

    def tracked_popen(command, **kwargs):
        launches.append(command)
        if fail and stage == "popen":
            raise primary
        return original_popen(command, **kwargs)

    monkeypatch.setattr(diagnostic_run.os, "open", tracked_open)
    monkeypatch.setattr(diagnostic_run.subprocess, "Popen", tracked_popen)
    context = diagnostic_run.traced_child(
        [sys.executable, "-c", Argument()],
        case_id="normal",
        cwd=tmp_path,
        env={"PATH": os.defpath},
        output=output,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if fail:
            with pytest.raises(OSError) as caught, context:
                pytest.fail("setup failure must not yield a child")
            assert caught.value is primary
            assert len(launches) == (1 if stage == "popen" else 0)
            assert not (output / "execution.json").exists()
        else:
            with context as child:
                assert child.wait(10) == 7
            assert len(launches) == 1
            rows = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
            assert [row["event"] for row in rows] == ["diagnostic_start", "diagnostic_footer"]
            assert {row["pid"] for row in rows} == {child.pid}
            assert json.loads((output / "execution.json").read_text())["returncode"] == 7
        assert len(opened["events.jsonl"]) == 1
        assert len(opened["syscalls.log"]) == (0 if fail and stage == "syscalls_create" else 1)
        for descriptors in opened.values():
            for fd in descriptors:
                with pytest.raises(OSError) as caught:
                    os.fstat(fd)
                assert caught.value.errno == errno.EBADF
    finally:
        # Keep the deliberately failing pre-fix control from leaking into pytest.
        for descriptors in opened.values():
            for fd in descriptors:
                try:
                    os.close(fd)
                except OSError as error:
                    assert error.errno == errno.EBADF


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_output_is_anchored_before_launch_with_different_child_cwd(tmp_path, monkeypatch, relative):
    fake_tracer(tmp_path, monkeypatch)
    parent, child_cwd = tmp_path / "parent", tmp_path / "child"
    parent.mkdir()
    child_cwd.mkdir()
    monkeypatch.chdir(parent)
    capture = parent / "diagnostic"
    output = Path("diagnostic") if relative else capture
    with diagnostic_run.traced_child(
        [sys.executable, "-c", "import os; print(os.getcwd()); raise SystemExit(7)"],
        case_id="normal",
        cwd=child_cwd,
        env={"PATH": os.defpath},
        output=output,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 7
    assert stdout.decode().strip() == str(child_cwd)
    assert stderr == b""
    assert child.args[child.args.index("-o") + 1] == str(capture / "syscalls.log")
    rows = [json.loads(line) for line in (capture / "events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["diagnostic_start", "diagnostic_footer"]
    assert {row["pid"] for row in rows} == {child.pid}
    assert json.loads((capture / "execution.json").read_text())["returncode"] == 7
    assert not (child_cwd / "diagnostic").exists()
    metadata = json.loads((capture / "execution.json").read_text())
    assert metadata["collector_terminal_observed"] is False
    assert (capture / "syscalls.log").read_bytes() == b""


@pytest.mark.parametrize("target_exists", [False, True], ids=["dangling", "existing"])
def test_output_symlink_is_rejected_without_following_target(tmp_path, monkeypatch, target_exists):
    target = tmp_path / "target"
    if target_exists:
        target.mkdir()
        (target / "untouched").write_text("sentinel")
    link = tmp_path / "diagnostic"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("existing output symlink must not prepare or launch a collector")

    monkeypatch.setattr(diagnostic_run.shutil, "which", forbidden)
    monkeypatch.setattr(diagnostic_run.subprocess, "Popen", forbidden)
    with pytest.raises(FileExistsError):
        with diagnostic_run.traced_child(
            [], case_id="normal", cwd=tmp_path, env={}, output=Path(link.name), stdout=None, stderr=None
        ):
            pytest.fail("must not launch")
    assert link.is_symlink()
    assert target.exists() == target_exists
    if target_exists:
        assert [path.name for path in target.iterdir()] == ["untouched"]
        assert (target / "untouched").read_text() == "sentinel"


@pytest.mark.parametrize("code", [0, 1, 7, 23])
@pytest.mark.parametrize("case_id", sorted(diagnostic_run.CASE_IDS))
def test_exact_child_returncode_and_private_mode_survive(tmp_path, monkeypatch, code, case_id):
    fake_tracer(tmp_path, monkeypatch)
    output = tmp_path / "diagnostic"
    with (
        diagnostic_run._private_stream(tmp_path / "stdout") as stdout,
        diagnostic_run._private_stream(tmp_path / "stderr") as stderr,
    ):
        with diagnostic_run.traced_child(
            [sys.executable, "-c", f"raise SystemExit({code})"],
            case_id=case_id,
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=output,
            stdout=stdout,
            stderr=stderr,
        ) as child:
            assert child.wait(10) == code
    metadata = json.loads((output / "execution.json").read_text())
    assert metadata["returncode"] == code
    assert metadata["pid"] == child.pid
    assert metadata["case_id"] == case_id
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "events.jsonl").stat().st_mode & 0o777 == 0o600
    assert (output / "syscalls.log").stat().st_mode & 0o777 == 0o600
    assert (output / "execution.json").stat().st_mode & 0o777 == 0o600
    # The fake tracer emits no syscall evidence, regardless of child success.
    assert metadata["collector_terminal_observed"] is False
    assert (output / "syscalls.log").read_bytes() == b""
    assert (output / "shim").stat().st_mode & 0o777 == 0o700
    assert (output / "shim/sitecustomize.py").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "stdout").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "stderr").stat().st_mode & 0o777 == 0o600


def test_exact_child_signal_and_original_exception_are_preserved(tmp_path, monkeypatch):
    fake_tracer(tmp_path, monkeypatch)
    output = tmp_path / "diagnostic"
    primary = RuntimeError("synthetic-primary")
    with (
        diagnostic_run._private_stream(tmp_path / "stdout") as stdout,
        diagnostic_run._private_stream(tmp_path / "stderr") as stderr,
    ):
        with pytest.raises(RuntimeError) as caught:
            with diagnostic_run.traced_child(
                [sys.executable, "-c", "import time;time.sleep(60)"],
                case_id="start",
                cwd=tmp_path,
                env={"PATH": os.defpath},
                output=output,
                stdout=stdout,
                stderr=stderr,
            ) as child:
                child.kill()
                assert child.wait(10) == -signal.SIGKILL
                raise primary
    assert caught.value is primary
    assert json.loads((output / "execution.json").read_text())["returncode"] == -signal.SIGKILL
    assert json.loads((output / "execution.json").read_text())["collector_terminal_observed"] is False
    assert (output / "syscalls.log").read_bytes() == b""


def test_existing_destination_and_injected_pythonpath_are_rejected(tmp_path, monkeypatch):
    fake_tracer(tmp_path, monkeypatch)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        with diagnostic_run.traced_child(
            [], case_id="normal", cwd=tmp_path, env={}, output=output, stdout=None, stderr=None
        ):
            pytest.fail("must not launch")
    with pytest.raises(ValueError, match="PYTHONPATH"):
        with diagnostic_run.traced_child(
            [],
            case_id="normal",
            cwd=tmp_path,
            env={"PYTHONPATH": "private"},
            output=tmp_path / "new",
            stdout=None,
            stderr=None,
        ):
            pytest.fail("must not launch")


@pytest.mark.parametrize("case_id", [None, True, 1, [], {}, "", "normal0", "PRIVATE_CASE_MARKER"])
def test_invalid_case_identity_is_rejected_before_launch(tmp_path, monkeypatch, case_id):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid identity must not prepare or launch a collector")

    monkeypatch.setattr(diagnostic_run.shutil, "which", forbidden)
    monkeypatch.setattr(diagnostic_run.subprocess, "Popen", forbidden)
    output = tmp_path / "diagnostic"
    with pytest.raises(ValueError if type(case_id) is str else TypeError) as caught:
        with diagnostic_run.traced_child(
            [], case_id=case_id, cwd=tmp_path, env={}, output=output, stdout=None, stderr=None
        ):
            pytest.fail("must not launch")
    assert not output.exists()
    assert "PRIVATE_CASE_MARKER" not in str(caught.value)


def test_secondary_metadata_write_failure_does_not_mask_primary(tmp_path, monkeypatch):
    fake_tracer(tmp_path, monkeypatch)
    original = diagnostic_run._write_private

    def fail_execution(path, value):
        if path.name == "execution.json":
            raise OSError("private-write-failure")
        return original(path, value)

    monkeypatch.setattr(diagnostic_run, "_write_private", fail_execution)
    primary = ValueError("original")
    with pytest.raises(ValueError) as caught:
        with diagnostic_run.traced_child(
            [sys.executable, "-c", "raise SystemExit(7)"],
            case_id="normal",
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=tmp_path / "diag",
            stdout=None,
            stderr=None,
        ) as child:
            assert child.wait(10) == 7
            raise primary
    assert caught.value is primary
    assert not (tmp_path / "diag/execution.json").exists()
    rows = [json.loads(line) for line in (tmp_path / "diag/events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["diagnostic_start", "diagnostic_footer"]
    assert {row["pid"] for row in rows} == {child.pid}
    assert (tmp_path / "diag/syscalls.log").read_bytes() == b""


def test_bounded_collector_drain_requires_exact_child_terminal(tmp_path):
    log = tmp_path / "syscalls.log"
    log.write_text("123 10.000001 +++ exited with 7 +++\n")
    assert diagnostic_run._terminal_observed(log, 123, timeout=0)
    assert not diagnostic_run._terminal_observed(log, 124, timeout=0)
    log.write_text("123 10.000001 +++ killed by SIGKILL +++\n")
    assert diagnostic_run._terminal_observed(log, 123, timeout=0)
    log.write_text("123 10.000001 exit_group(7) = ?\n")
    assert not diagnostic_run._terminal_observed(log, 123, timeout=0)


def test_caller_timeout_is_preserved_after_exact_child_cleanup(tmp_path, monkeypatch):
    fake_tracer(tmp_path, monkeypatch)
    output = tmp_path / "diagnostic"
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        with diagnostic_run.traced_child(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            case_id="normal",
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=output,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as child:
            try:
                child.wait(timeout=0.01)
            finally:
                # Lifecycle ownership belongs to the harness, not the context.
                child.kill()
                assert child.wait(10) == -signal.SIGKILL
    assert caught.value.timeout == 0.01
    assert caught.value.cmd == child.args
    assert json.loads((output / "execution.json").read_text())["returncode"] == -signal.SIGKILL
    assert json.loads((output / "execution.json").read_text())["collector_terminal_observed"] is False
    assert (output / "syscalls.log").read_bytes() == b""


def test_modules_import_without_reader_audit_runner_or_product_preimport(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(root)!r})
class NoProductOrAudit(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('code_forge', 'installed_audit', 'qualification.installed_audit',
                                'qualification.installed_cli.diagnostic_summary',
                                'tests.test_cli_diagnostic_summary')):
            raise AssertionError('unexpected product, reader, or installed-audit import')
sys.meta_path.insert(0, NoProductOrAudit())
from qualification.installed_cli import diagnostic_probe, diagnostic_run, trace_parser
assert callable(diagnostic_run.traced_child)
assert callable(diagnostic_probe.install)
assert callable(trace_parser.parse_trace)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("capture", ["success", "failed_call", "malformed"])
def test_documented_parser_usage_keeps_raw_result_private(tmp_path, capture):
    root = Path(__file__).resolve().parents[1]
    readme = (root / "qualification/installed_cli/README.md").read_text()
    recipe = readme.split("<!-- private-parser-example -->\n```python\n", 1)[1].split("\n```", 1)[0]
    trace = "71 1.000001 fsync(3</PRIVATE_PATH_MARKER>) = "
    if capture == "success":
        trace += "0 <0.000001>\n71 1.000003 +++ exited with 0 +++\n"
    elif capture == "failed_call":
        trace += "-1 EIO (Input/output error) <0.000001>\n71 1.000003 +++ exited with 1 +++\n"
    else:
        trace = "PRIVATE_MALFORMED_MARKER\n"
    before = set(tmp_path.iterdir())
    code = f"""
import sys
sys.path.insert(0, {str(root)!r})
sys.modules['qualification.installed_cli.diagnostic_summary'] = None
private_trace_text = {trace!r}
{recipe}
assert private_parse['complete'] is {capture != "malformed"!r}
assert 'public' not in private_parse and 'diagnostic_status' not in private_parse
if {capture != "malformed"!r}:
    assert private_parse['events'][0]['fd']['path'] == '/PRIVATE_PATH_MARKER'
    assert private_parse['events'][0]['result'] == {0 if capture == "success" else -1}
else:
    assert private_parse['issues']
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert set(tmp_path.iterdir()) == before


def test_capture_subset_has_no_reader_or_qualification_runner():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "qualification/installed_cli"
    assert {path.name for path in runtime.glob("*.py")} == {
        "capture_bootstrap.py",
        "capture_worker.py",
        "diagnostic_run.py",
        "diagnostic_probe.py",
        "trace_parser.py",
    }
    assert not (root / "tests/test_cli_diagnostic_summary.py").exists()
    assert not (root / "qualification/installed_audit").exists()
    assert not (root / "scripts/qualify_audit_cli.py").exists()


@pytest.fixture
def event_close_fault(monkeypatch):
    """Close once, then reuse that number to detect unsafe close retries."""
    original_open, original_close = os.open, os.close
    state = {"fd": None, "calls": 0, "replacement": None, "error": None}

    def tracked_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if Path(path).name == "events.jsonl":
            state["fd"] = fd
        return fd

    def faulty_close(fd):
        if fd != state["fd"]:
            return original_close(fd)
        state["calls"] += 1
        original_close(fd)
        if state["calls"] == 1:
            replacement = original_open(os.devnull, os.O_RDONLY)
            state["replacement"] = replacement
            assert replacement == fd
            if state["error"] is not None:
                raise state["error"]

    monkeypatch.setattr(diagnostic_run.os, "open", tracked_open)
    monkeypatch.setattr(diagnostic_run.os, "close", faulty_close)
    try:
        yield state
    finally:
        if state["replacement"] is not None:
            # Use the saved close, never the injected fault, for test cleanup.
            try:
                original_close(state["replacement"])
            except OSError as error:
                assert error.errno == errno.EBADF


@pytest.mark.parametrize("stage", ["syscalls_create", "argv_conversion", "popen"])
@pytest.mark.parametrize("primary_kind", ["oserror", "keyboard"])
def test_setup_primary_survives_event_close_failure(
    tmp_path, monkeypatch, event_close_fault, stage, primary_kind
):
    fake_tracer(tmp_path, monkeypatch)
    primary = (
        OSError(errno.ENOSPC, "synthetic setup")
        if primary_kind == "oserror"
        else KeyboardInterrupt("synthetic setup")
    )
    event_close_fault["error"] = OSError(errno.EIO, "synthetic close")
    original_stream = diagnostic_run._private_stream
    launches = []

    def private_stream(path):
        if stage == "syscalls_create" and path.name == "syscalls.log":
            raise primary
        return original_stream(path)

    class Argument:
        def __str__(self):
            if stage == "argv_conversion":
                raise primary
            return "pass"

    def popen(*args, **kwargs):
        launches.append(args)
        raise primary

    monkeypatch.setattr(diagnostic_run, "_private_stream", private_stream)
    monkeypatch.setattr(diagnostic_run.subprocess, "Popen", popen)
    output = tmp_path / "diagnostic"
    observed = None
    try:
        with diagnostic_run.traced_child(
            [sys.executable, "-c", Argument()],
            case_id="normal",
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=output,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ):
            pytest.fail("setup failure must not yield")
    except (OSError, KeyboardInterrupt) as error:
        observed = error
    assert observed is primary
    assert len(launches) == (1 if stage == "popen" else 0)
    assert not (output / "execution.json").exists()
    assert event_close_fault["calls"] == 1
    os.fstat(event_close_fault["replacement"])


@pytest.mark.parametrize("primary_kind", ["none", "runtime", "keyboard", "timeout"])
@pytest.mark.parametrize("close_kind", ["none", "oserror", "keyboard"])
@pytest.mark.parametrize("metadata_kind", ["none", "oserror", "systemexit"])
def test_reaped_child_cleanup_exception_precedence(
    tmp_path, monkeypatch, event_close_fault, primary_kind, close_kind, metadata_kind
):
    fake_tracer(tmp_path, monkeypatch)
    close_error = {
        "none": None,
        "oserror": OSError(errno.EIO, "synthetic close"),
        "keyboard": KeyboardInterrupt("synthetic close"),
    }[close_kind]
    metadata_error = {
        "none": None,
        "oserror": OSError(errno.ENOSPC, "synthetic metadata"),
        "systemexit": SystemExit("synthetic metadata"),
    }[metadata_kind]
    primary = {
        "none": None,
        "runtime": RuntimeError("synthetic body"),
        "keyboard": KeyboardInterrupt("synthetic body"),
        "timeout": None,
    }[primary_kind]
    event_close_fault["error"] = close_error
    writes = []
    original_write = diagnostic_run._write_private

    def write_private(path, value):
        if path.name == "execution.json":
            writes.append(json.loads(value))
            if metadata_error is not None:
                raise metadata_error
        return original_write(path, value)

    monkeypatch.setattr(diagnostic_run, "_write_private", write_private)
    output = tmp_path / "diagnostic"
    code = "import time; time.sleep(60)" if primary_kind == "timeout" else "raise SystemExit(7)"
    observed = None
    try:
        with diagnostic_run.traced_child(
            [sys.executable, "-c", code],
            case_id="normal",
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=output,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as child:
            if primary_kind == "timeout":
                try:
                    child.wait(timeout=0.01)
                except subprocess.TimeoutExpired as error:
                    primary = error
                    raise
                finally:
                    # The caller alone owns cleanup of its exact child.
                    child.kill()
                    assert child.wait(10) == -signal.SIGKILL
            else:
                assert child.wait(10) == 7
                if primary is not None:
                    raise primary
    except (RuntimeError, OSError, KeyboardInterrupt, SystemExit, subprocess.TimeoutExpired) as error:
        observed = error
    assert observed is (primary or close_error or metadata_error)
    assert event_close_fault["calls"] == 1
    os.fstat(event_close_fault["replacement"])
    assert len(writes) == 1
    metadata = writes[0]
    assert metadata["pid"] == child.pid
    assert metadata["returncode"] == (-signal.SIGKILL if primary_kind == "timeout" else 7)
    assert metadata["collector_terminal_observed"] is False
    assert metadata["elapsed_ns"] > 0
    assert (output / "execution.json").exists() == (metadata_error is None)
    if metadata_error is None:
        assert json.loads((output / "execution.json").read_text()) == metadata


@pytest.mark.parametrize("stage", ["before_wrapper", "after_wrapper"])
@pytest.mark.parametrize("error_kind", ["memory", "interrupt"])
@pytest.mark.parametrize("close_failure", [False, True], ids=["close_ok", "close_error"])
def test_private_stream_constructor_failure_closes_only_owned_descriptor(
    tmp_path, monkeypatch, stage, error_kind, close_failure
):
    original_open, original_fdopen, original_close = os.open, os.fdopen, os.close
    primary = MemoryError("private allocation") if error_kind == "memory" else KeyboardInterrupt()
    path = tmp_path / "private"
    state = {"fd": None, "close_calls": 0, "replacement": None, "closefd": None}

    def tracked_open(target, *args, **kwargs):
        descriptor = original_open(target, *args, **kwargs)
        if Path(target) == path:
            state["fd"] = descriptor
        return descriptor

    def failed_constructor(descriptor, *args, **kwargs):
        state["closefd"] = kwargs.get("closefd", True)
        if stage == "after_wrapper":
            # A real wrapper may be allocated and destroyed before failure.
            # closefd=False leaves the raw descriptor owned by the helper.
            wrapper = original_fdopen(descriptor, *args, **kwargs)
            wrapper.close()
        raise primary

    def close_then_reuse(descriptor):
        if descriptor != state["fd"]:
            return original_close(descriptor)
        state["close_calls"] += 1
        original_close(descriptor)
        if close_failure:
            replacement = original_open(os.devnull, os.O_RDONLY)
            state["replacement"] = replacement
            assert replacement == descriptor
            raise OSError(errno.EIO, "private close failure")

    monkeypatch.setattr(diagnostic_run.os, "open", tracked_open)
    monkeypatch.setattr(diagnostic_run.os, "fdopen", failed_constructor)
    monkeypatch.setattr(diagnostic_run.os, "close", close_then_reuse)
    try:
        with pytest.raises(type(primary)) as caught:
            with diagnostic_run._private_stream(path):
                pytest.fail("constructor failure must not yield")
        assert caught.value is primary
        assert state["close_calls"] == 1
        assert state["closefd"] is False
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.read_bytes() == b""
        if close_failure:
            assert state["replacement"] == state["fd"]
            os.fstat(state["replacement"])
        else:
            with pytest.raises(OSError) as closed:
                os.fstat(state["fd"])
            assert closed.value.errno == errno.EBADF
    finally:
        # Also cleans up a deliberately failing old-source control.
        if state["fd"] is not None:
            try:
                original_close(state["fd"])
            except OSError as error:
                assert error.errno == errno.EBADF


@pytest.mark.parametrize("body_failure", [False, True], ids=["body_ok", "body_error"])
@pytest.mark.parametrize("stream_failure", [False, True], ids=["stream_ok", "stream_error"])
@pytest.mark.parametrize("close_failure", [False, True], ids=["close_ok", "close_error"])
def test_private_stream_cleanup_preserves_primary_and_single_close(
    tmp_path, monkeypatch, body_failure, stream_failure, close_failure
):
    original_fdopen, original_open, original_close = os.fdopen, os.open, os.close
    body_error = ValueError("private body")
    stream_error = OSError(errno.ENOSPC, "private stream close")
    close_error = OSError(errno.EIO, "private raw close")
    state = {"fd": None, "stream_calls": 0, "close_calls": 0, "replacement": None}
    path = tmp_path / "private"

    class Stream:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def write(self, value):
            return self.wrapped.write(value)

        def close(self):
            state["stream_calls"] += 1
            self.wrapped.close()
            # A non-owning wrapper close must leave this real FD alive.
            os.fstat(state["fd"])
            if stream_failure:
                raise stream_error

    def wrap(descriptor, *args, **kwargs):
        state["fd"] = descriptor
        return Stream(original_fdopen(descriptor, *args, **kwargs))

    def close_then_reuse(descriptor):
        if descriptor != state["fd"]:
            return original_close(descriptor)
        state["close_calls"] += 1
        original_close(descriptor)
        replacement = original_open(os.devnull, os.O_RDONLY)
        state["replacement"] = replacement
        assert replacement == descriptor
        if close_failure:
            raise close_error

    monkeypatch.setattr(diagnostic_run.os, "fdopen", wrap)
    monkeypatch.setattr(diagnostic_run.os, "close", close_then_reuse)
    expected = (
        body_error
        if body_failure
        else stream_error
        if stream_failure
        else close_error
        if close_failure
        else None
    )
    try:
        try:
            with diagnostic_run._private_stream(path) as stream:
                assert stream.write(b"PRIVATE_STREAM_CONTENT") == len(b"PRIVATE_STREAM_CONTENT")
                if body_failure:
                    raise body_error
        except (ValueError, OSError) as error:
            assert expected is not None and error is expected
        else:
            assert expected is None
        assert state["stream_calls"] == state["close_calls"] == 1
        assert state["replacement"] == state["fd"]
        os.fstat(state["replacement"])
        assert path.read_bytes() == b"PRIVATE_STREAM_CONTENT"
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        if state["replacement"] is not None:
            original_close(state["replacement"])


@pytest.mark.parametrize(
    "line, expected",
    [
        pytest.param("123 10.000001 +++ exited with 7 +++\n", True, id="ordinary-exit"),
        pytest.param("123 10.000001 +++ killed by SIGKILL +++\n", True, id="ordinary-signal"),
        pytest.param("123 10.000001 +++ killed by SIGRT_3 +++\n", True, id="realtime-signal"),
        pytest.param(
            "123 10.000001 +++ killed by SIGSEGV (core dumped) +++\n", True, id="ordinary-core"
        ),
        pytest.param(
            "123 10.000001 +++ killed by SIGRT_3 (core dumped) +++\n", True, id="realtime-core"
        ),
        pytest.param("124 10.000001 +++ killed by SIGKILL +++\n", False, id="wrong-pid-ordinary"),
        pytest.param("124 10.000001 +++ killed by SIGRT_3 +++\n", False, id="wrong-pid-realtime"),
        pytest.param(
            "124 10.000001 +++ killed by SIGRT_3 (core dumped) +++\n", False, id="wrong-pid-core"
        ),
        pytest.param("1123 10.000001 +++ killed by SIGRT_3 +++\n", False, id="pid-suffix-is-not-pid"),
        pytest.param("prefix 123 10.000001 +++ killed by SIGRT_3 +++\n", False, id="line-prefix"),
        pytest.param("123 10.000001 +++ killed by SIGRT_3 +++ suffix\n", False, id="line-suffix"),
        pytest.param("123 10.000001 +++ killed by SIG +++\n", False, id="bare-signal-prefix"),
        pytest.param("123 10.000001 +++ killed by SI +++\n", False, id="truncated-signal-prefix"),
        pytest.param("123 10.000001 +++ killed by SIGRT_3x +++\n", False, id="lowercase-signal-suffix"),
        pytest.param("123 10.000001 +++ killed by SIGRT-3 +++\n", False, id="unsupported-signal-hyphen"),
        pytest.param("123 10.000001 +++ killed by SIGRT+3 +++\n", False, id="unsupported-signal-plus"),
        pytest.param("123 10.000001 +++ killed by SIGRT_3 ++\n", False, id="truncated-terminal-marker"),
        pytest.param(
            "123 10.000001 +++ killed by SIGRT_3 (core dumped +++\n", False, id="truncated-core-suffix"
        ),
        pytest.param(
            "123 10.000001 +++ killed by SIGRT_3 (core dump) +++\n", False, id="malformed-core-suffix"
        ),
        pytest.param("123 +++ killed by SIGRT_3 +++\n", False, id="missing-timestamp"),
        pytest.param("123 10.000001 +++ killed by SIGRT_\n3 +++\n", False, id="split-signal-token"),
        pytest.param("123 10.000001 +++ killed by SIGRT_3 +", False, id="truncated-final-line"),
        pytest.param(
            "124 10.000001 +++ killed by SIGRT_3 +++\n123 10.000002 +++ killed by SIGKILL +++\n",
            True,
            id="exact-pid-among-records",
        ),
    ],
)
def test_terminal_signal_grammar_keeps_exact_pid_and_line_boundaries(tmp_path, line, expected):
    path = tmp_path / "syscalls.log"
    path.write_text(line)
    assert diagnostic_run._terminal_observed(path, 123, timeout=0) is expected


@pytest.mark.parametrize(
    "signal_token, returncode",
    [
        pytest.param("SIGKILL", -9, id="ordinary-negative-return"),
        pytest.param("SIGRT_3", -37, id="realtime-negative-return"),
    ],
)
def test_terminal_observation_does_not_replace_original_negative_returncode(
    tmp_path, monkeypatch, signal_token, returncode
):
    output = tmp_path / "diagnostic"
    launches = []
    monkeypatch.setattr(diagnostic_run.shutil, "which", lambda *args, **kwargs: "/synthetic/strace")

    class Child:
        pid = 123

        def poll(self):
            return returncode

    child = Child()

    def launch(command, **kwargs):
        launches.append(command)
        (output / "syscalls.log").write_text(f"123 10.000001 +++ killed by {signal_token} +++\n")
        return child

    monkeypatch.setattr(diagnostic_run.subprocess, "Popen", launch)
    with diagnostic_run.traced_child(
        ["/synthetic/console"],
        case_id="normal",
        cwd=tmp_path,
        env={"PATH": os.defpath},
        output=output,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as observed:
        assert observed is child and observed.poll() == returncode
    assert len(launches) == 1
    metadata = json.loads((output / "execution.json").read_text())
    assert metadata["pid"] == child.pid
    assert metadata["collector_terminal_observed"] is True
    assert metadata["returncode"] == returncode


@pytest.mark.parametrize(
    "ending",
    [
        pytest.param("exited with 7", id="exit"),
        pytest.param("killed by SIGKILL", id="ordinary-signal"),
        pytest.param("killed by SIGRT_3 (core dumped)", id="realtime-core"),
    ],
)
@pytest.mark.parametrize(
    "boundary, expected",
    [
        pytest.param("first-line", True, id="first-line"),
        pytest.param("exact-window", True, id="zero-offset-window"),
        pytest.param("aligned", True, id="newline-aligned-first-record"),
        pytest.param("wrong-pid-cut", False, id="wrong-pid-suffix-cut"),
        pytest.param("own-pid-cut", False, id="actual-pid-outside-tail"),
        pytest.param("prefix-cut", False, id="non-newline-prefix-cut"),
        pytest.param("later-record", True, id="complete-record-after-partial"),
    ],
)
def test_terminal_tail_requires_authentic_record_start(tmp_path, ending, boundary, expected):
    record = f"123 10.000001 +++ {ending} +++\n".encode()
    pid = 123
    if boundary == "first-line":
        data = record
    elif boundary == "exact-window":
        data = record + b"x" * (65536 - len(record))
    else:
        if boundary == "aligned":
            prefix = b"\n" + record
        elif boundary in ("wrong-pid-cut", "own-pid-cut"):
            prefix = b"1" + record
            if boundary == "own-pid-cut":
                pid = 1123
        elif boundary == "prefix-cut":
            prefix = b"x" + record
        else:
            assert boundary == "later-record"
            prefix = b"partial record\n" + record
        data = prefix + b"x" * (65537 - len(prefix))
    path = tmp_path / "private-syscalls.log"
    with diagnostic_run._private_stream(path) as stream:
        stream.write(data)
    assert path.stat().st_mode & 0o777 == 0o600
    assert diagnostic_run._terminal_observed(path, pid, timeout=0) is expected


@pytest.mark.parametrize("boundary", ["first-line", "later-line", "partial-only"])
def test_terminal_tail_rejects_missing_final_newline(tmp_path, boundary):
    record = b"123 10.000001 +++ exited with 7 +++"
    if boundary == "first-line":
        data = record
    elif boundary == "later-line":
        data = b"x" * (65537 - len(record) - 1) + b"\n" + record
    else:
        assert boundary == "partial-only"
        suffix = b"10.000001 +++ exited with 7 +++"
        data = b"1123" + b" " * (65537 - 4 - len(suffix)) + suffix
        assert b"\n" not in data and len(data) == 65537
    path = tmp_path / "private-syscalls.log"
    with diagnostic_run._private_stream(path) as stream:
        stream.write(data)
    assert not diagnostic_run._terminal_observed(path, 123, timeout=0)


@pytest.mark.parametrize(
    "size, expected, requests",
    [
        pytest.param(0, False, [65536], id="empty-file"),
        pytest.param(65536, True, [65536], id="exact-window"),
        pytest.param(65537, True, [1, 65536], id="one-byte-guard"),
        pytest.param(33554432, True, [1, 65536], id="exact-size-limit"),
        pytest.param(33554433, False, [], id="over-size-limit"),
    ],
)
def test_terminal_tail_keeps_read_and_file_size_bounds(tmp_path, size, expected, requests):
    path = tmp_path / "private-syscalls.log"
    record = b"\n123 10.000001 +++ exited with 7 +++\n"
    with diagnostic_run._private_stream(path) as stream:
        stream.truncate(size)
        if size:
            stream.seek(size - len(record))
            stream.write(record)
    reads = []
    seeks = []

    class TrackedStream:
        def __enter__(self):
            self.stream = path.open("rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def seek(self, offset):
            seeks.append(offset)
            return self.stream.seek(offset)

        def read(self, count):
            assert 0 < count <= 65536
            data = self.stream.read(count)
            reads.append((count, len(data)))
            return data

    class TrackedPath:
        def open(self, mode):
            assert mode == "rb"
            return TrackedStream()

    assert diagnostic_run._terminal_observed(TrackedPath(), 123, timeout=0) is expected
    assert [request for request, _ in reads] == requests
    assert sum(received for _, received in reads) <= 65537
    assert seeks == ([] if size > 33554432 else [max(0, size - 65536 - 1)])
    assert path.stat().st_size == size


@pytest.mark.parametrize(
    "initial_lf, growth, expected",
    [
        pytest.param(True, "", True, id="lf-terminated-eof"),
        pytest.param(True, "2 12.000002 +++ exited with 0 +++\n", True, id="later-other-pid"),
        pytest.param(False, "", False, id="eof-without-lf"),
        pytest.param(False, "\n", False, id="lf-appended-outside-read"),
        pytest.param(False, " trailing-unsupported-text\n", False, id="suffix-outside-read"),
    ],
)
def test_terminal_tail_requires_observed_lf_during_file_growth(
    tmp_path, monkeypatch, initial_lf, growth, expected
):
    path = tmp_path / "private-syscalls.log"
    prefix = b"2 11.000001 fsync(4) = 0 <0.000000>\n" * 2000
    record = b"123 12.000001 +++ exited with 0 +++" + (b"\n" if initial_lf else b"")
    with diagnostic_run._private_stream(path) as stream:
        stream.write(prefix + record)
    original = os.fstat
    calls = []

    class FileOps:
        @staticmethod
        def fstat(fd):
            result = original(fd)
            calls.append(fd)
            if growth:
                with path.open("ab") as stream:
                    stream.write(growth.encode())
            return result

    monkeypatch.setattr(diagnostic_run, "os", FileOps)
    assert diagnostic_run._terminal_observed(path, 123, timeout=0) is expected
    assert len(calls) == 1
    assert path.read_bytes() == prefix + record + growth.encode()


def test_terminal_tail_rechecks_boundary_during_bounded_drain(tmp_path, monkeypatch):
    path = tmp_path / "private-syscalls.log"
    record = b"123 10.000001 +++ exited with 7 +++\n"
    suffix = record + b"x" * (65536 - len(record))
    with diagnostic_run._private_stream(path) as stream:
        stream.write(b"1" + suffix)
    clocks = iter([0.0, 0.01])
    sleeps = []

    class Clock:
        @staticmethod
        def monotonic():
            return next(clocks)

        @staticmethod
        def sleep(delay):
            sleeps.append(delay)
            path.write_bytes(b"\n" + suffix)

    monkeypatch.setattr(diagnostic_run, "time", Clock)
    assert diagnostic_run._terminal_observed(path, 123, timeout=0.5)
    assert sleeps == [0.02]
    assert path.stat().st_mode & 0o777 == 0o600
