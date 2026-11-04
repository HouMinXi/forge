"""Owned synthetic CI diagnostics, without collection or native host probing.

The only real children below run this transport with its workload replaced by
an inert Python fixture. They exercise abrupt termination, never pytest/native
workloads, system services, privilege changes or host-state discovery.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scripts import ci_private_transport as transport
from scripts import ci_test_shards as ci
from tests.test_ci_private_transport import (
    CANARY,
    approved_plan,
    cli_args,
    evidence,
    execution,
    fake_collector,
    fake_execution,
    materialize_public,
    public_text,
)

ROOT = Path(__file__).resolve().parents[1]
INVOCATION = "a" * 32
JOBS = (
    ("plan", None, ("plan",)),
    ("process-contracts", None, ("ownership", "cgroup", "local")),
    ("service-free", "shard-0", ("shard-0",)),
    ("service-free", "shard-1", ("shard-1",)),
    ("service-free", "shard-2", ("shard-2",)),
    ("service-free", "shard-3", ("shard-3",)),
    ("linux-tests", None, ("verify",)),
)
JOB_IDS = ("plan", "process", "shard-0", "shard-1", "shard-2", "shard-3", "aggregate")


def prepare(tmp_path, monkeypatch, command="run", phase="shard-0"):
    example = evidence()
    args, policy = cli_args(tmp_path, example, command, phase if command == "run" else None)
    monkeypatch.setattr(ci, "POLICY", policy)
    diagnostic = tmp_path / "diagnostic-private"
    args += ["--diagnostic-directory", str(diagnostic), "--diagnostic-invocation", INVOCATION]
    return example, args, diagnostic


def record(directory, operation="shard-0"):
    return json.loads((directory / (operation + ".json")).read_text())["record"]


def publish(tmp_path, private, job="service-free", status="failure", phase="shard-0"):
    return transport.project_diagnostics(
        private,
        tmp_path / "diagnostic-public",
        job,
        status,
        phase,
        invocations={
            operation: INVOCATION for operation in transport._diagnostic_operations(job, phase)
        },
    )


def no_public_canary(tmp_path, capsys):
    captured = capsys.readouterr()
    exposed = captured.out + captured.err
    for name in ("public", "diagnostic-public"):
        exposed += public_text(tmp_path / name)
    assert CANARY not in exposed
    assert "Traceback" not in exposed
    assert "/home/" not in exposed
    return captured


@pytest.mark.parametrize("job,phase,operations", JOBS, ids=JOB_IDS)
@pytest.mark.parametrize("status", transport.STATUSES)
def test_missing_records_are_unavailable_not_success_or_not_run(
    tmp_path, job, phase, operations, status
):
    value = publish(tmp_path, tmp_path / "missing", job, status, phase)
    assert value == {
        "schema": "forge-public-linux-ci-diagnostics-v1",
        "job": job,
        "job_result": status,
        "operations": [
            {"operation": op, "stage": "unknown", "state": "UNAVAILABLE", "code": "NOT_RECORDED"}
            for op in operations
        ],
        "raw_evidence": "NOT_PUBLISHED",
    }
    assert len(json.dumps(value)) <= 1024


@pytest.mark.parametrize("phase", ci.PHASES)
def test_success_records_original_validators_and_argv_complete(monkeypatch, tmp_path, capsys, phase):
    example, args, private = prepare(tmp_path, monkeypatch, phase=phase)
    commands = fake_execution(monkeypatch, execution(example.plan, phase))
    assert transport.main(args) == 0
    assert record(private, phase) == {
        "operation": phase,
        "stage": "publication",
        "state": "COMPLETED",
        "code": "COMPLETED",
    }
    command = commands[0]
    selection = ci.selection(phase)
    if phase.startswith("shard-"):
        selection = [*approved_plan(example)["phases"][phase]["modules"], *selection]
    expected = ci.command_for_pytest(
        phase,
        example.plan["sha"],
        tmp_path / "private" / f"receipt-{phase}.json",
        [*selection, "--durations=20", f"--junitxml={tmp_path / 'private' / (phase + '.xml')}"],
    )
    assert command == expected
    job = "service-free" if phase.startswith("shard-") else "process-contracts"
    value = publish(tmp_path, private, job, "success", phase if job == "service-free" else None)
    assert next(r for r in value["operations"] if r["operation"] == phase) == record(private, phase)
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("command", ("plan", "verify"))
def test_plan_and_aggregate_complete_only_after_original_validation(
    monkeypatch, tmp_path, capsys, command
):
    example, args, private = prepare(tmp_path, monkeypatch, command)
    if command == "plan":
        fake_collector(monkeypatch, example)
    else:
        materialize_public(tmp_path / "private", example)
        monkeypatch.setenv(
            "CI_NEEDS_JSON",
            json.dumps(
                {name: {"result": "success"} for name in ("plan", "process-contracts", "service-free")}
            ),
        )
    assert transport.main(args) == 0
    assert record(private, command)["state"] == "COMPLETED"
    assert record(private, command)["stage"] == ("publication" if command == "plan" else "aggregate")
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("returncode", (-15, -9, 1, 2, 5, 17, 124, 137, 143, 255))
def test_nonzero_child_retains_code_and_execution_boundary(monkeypatch, tmp_path, capsys, returncode):
    example, args, private = prepare(tmp_path, monkeypatch)
    fake_execution(monkeypatch, execution(example.plan, "shard-0"), returncode)
    assert transport.main(args) == returncode
    assert record(private) == {
        "operation": "shard-0",
        "stage": "execution",
        "state": "FAILED",
        "code": "NONZERO_EXIT",
    }
    assert not (tmp_path / "public/receipt-shard-0.json").exists()
    assert "Traceback" in (private / "shard-0.log").read_text()
    publish(tmp_path, private)
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize(
    "failure,expected_stage",
    (
        ("input", "input_validation"),
        ("outcome", "receipt_validation"),
        ("coverage", "coverage_validation"),
        ("publication", "publication"),
    ),
)
def test_failed_boundary_distinguishes_input_outcome_coverage_and_publication(
    monkeypatch, tmp_path, capsys, failure, expected_stage
):
    example, args, private = prepare(tmp_path, monkeypatch)
    receipt = execution(example.plan, "shard-0")
    if failure == "input":
        (tmp_path / "inputs/manifest.json").write_text(CANARY)
    elif failure == "outcome":
        receipt["reports"][0]["outcome"] = "failed"
    elif failure == "coverage":
        removed = receipt["collected"].pop()
        receipt["reports"] = [r for r in receipt["reports"] if r["nodeid"] != removed]
    else:
        write = transport._write_json

        def fail_public(path, data):
            if Path(path).parent == tmp_path / "public":
                raise OSError(CANARY)
            return write(path, data)

        monkeypatch.setattr(transport, "_write_json", fail_public)
    fake_execution(monkeypatch, receipt)
    assert transport.main(args) == 1
    assert record(private)["stage"] == expected_stage
    assert record(private)["state"] == "FAILED"
    publish(tmp_path, private)
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("command", ("plan", "verify"))
def test_plan_collection_and_aggregate_failures_have_distinct_boundaries(
    monkeypatch, tmp_path, capsys, command
):
    example, args, private = prepare(tmp_path, monkeypatch, command)
    if command == "plan":

        def fail(*args, **kwargs):
            raise subprocess.CalledProcessError(17, [CANARY], output=CANARY)

        monkeypatch.setattr(ci, "collect_plan", fail)
        expected_code, stage, code = 17, "collection", "NONZERO_EXIT"
    else:
        materialize_public(tmp_path / "private", example)
        monkeypatch.setenv(
            "CI_NEEDS_JSON",
            json.dumps(
                {
                    name: {"result": "failure" if name == "service-free" else "success"}
                    for name in ("plan", "process-contracts", "service-free")
                }
            ),
        )
        expected_code, stage, code = 1, "aggregate", "REJECTED"
    assert transport.main(args) == expected_code
    assert record(private, command) == {
        "operation": command,
        "stage": stage,
        "state": "FAILED",
        "code": code,
    }
    assert "Traceback" in (private / (command + ".log")).read_text()
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize(
    "kind,expected_code,exitcode",
    (("exception", "INTERNAL_ERROR", 1), ("interrupt", "INTERRUPTED", 130)),
)
def test_private_traceback_locates_exception_without_public_exception_data(
    monkeypatch, tmp_path, capsys, kind, expected_code, exitcode
):
    _, args, private = prepare(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise (OSError(CANARY) if kind == "exception" else KeyboardInterrupt(CANARY))

    monkeypatch.setattr(transport.subprocess, "run", fail)
    assert transport.main(args) == exitcode
    assert record(private)["code"] == expected_code
    text = (private / "shard-0.log").read_text()
    assert "Traceback" in text and CANARY in text
    publish(tmp_path, private)
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("failure", ("checkpoint", "traceback", "close", "all"))
@pytest.mark.parametrize("kind,exitcode", (("child", 17), ("interrupt", 130)))
def test_diagnostic_io_cannot_replace_original_child_or_interrupt_code(
    monkeypatch, tmp_path, capsys, failure, kind, exitcode
):
    _, args, private = prepare(tmp_path, monkeypatch)

    def workload(args, diagnostic):
        diagnostic.enter("execution")
        raise (SystemExit(17) if kind == "child" else KeyboardInterrupt(CANARY))

    monkeypatch.setattr(transport, "_execute", workload)

    def broken(*args, **kwargs):
        raise OSError(CANARY)

    if failure in ("checkpoint", "all"):
        monkeypatch.setattr(transport._Diagnostic, "failed", broken)
    if failure in ("traceback", "all"):
        monkeypatch.setattr(transport.traceback, "print_exc", broken)
    if failure in ("close", "all"):
        real_log = transport._log

        @contextmanager
        def bad_close(*args):
            try:
                with real_log(*args) as stream:
                    yield stream
            finally:
                raise OSError(CANARY)

        monkeypatch.setattr(transport, "_log", bad_close)
    assert transport.main(args) == exitcode
    assert record(private)["state"] in ("FAILED", "INCOMPLETE")
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("mask", (0o022, 0o027, 0o077), ids=("022", "027", "077"))
def test_diagnostics_keep_private_modes_and_original_workload_umask(monkeypatch, tmp_path, capsys, mask):
    example, args, private = prepare(tmp_path, monkeypatch)
    fake = fake_execution(monkeypatch, execution(example.plan, "shard-0"))
    runner = transport.subprocess.run

    def inspect(*args, **kwargs):
        assert os.umask(mask) == mask
        assert private.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in private.iterdir())
        return runner(*args, **kwargs)

    monkeypatch.setattr(transport.subprocess, "run", inspect)
    previous = os.umask(mask)
    try:
        assert transport.main(args) == 0
        assert os.umask(mask) == mask
    finally:
        os.umask(previous)
    assert len(fake) == 1
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in private.iterdir())
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize(
    "kind",
    (
        "extra-field",
        "canary-stage",
        "canary-code",
        "wrong-op",
        "wrong-stage",
        "complete-early",
        "incomplete-with-success",
        "unknown-with-failure",
        "failed-with-success",
        "unrecognized-state",
        "invalid-json",
        "duplicate-key",
        "nonfinite",
        "extra-file",
        "temporary-file",
        "extra-op",
        "symlink",
        "hardlink-log",
        "fifo",
        "directory",
    ),
)
def test_projection_rejects_untrusted_or_cross_bound_checkpoint_without_stale_output(tmp_path, kind):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "shard-0.json"
    data = {"operation": "shard-0", "stage": "execution", "state": "FAILED", "code": "NONZERO_EXIT"}
    if kind == "extra-field":
        data["traceback"] = CANARY
    elif kind == "canary-stage":
        data["stage"] = CANARY
    elif kind == "canary-code":
        data["code"] = CANARY
    elif kind == "wrong-op":
        data["operation"] = "shard-1"
    elif kind == "wrong-stage":
        data["stage"] = "aggregate"
    elif kind == "complete-early":
        data.update(state="COMPLETED", code="COMPLETED")
    elif kind == "incomplete-with-success":
        data.update(state="INCOMPLETE", code="COMPLETED")
    elif kind == "unknown-with-failure":
        data.update(state="UNAVAILABLE", stage="unknown")
    elif kind == "failed-with-success":
        data["code"] = "COMPLETED"
    elif kind == "unrecognized-state":
        data["state"] = CANARY
    target.write_text(json.dumps({"invocation": INVOCATION, "record": data}))
    if kind == "invalid-json":
        target.write_text(CANARY)
    elif kind == "duplicate-key":
        target.write_text('{"operation":"shard-0","operation":"shard-1"}')
    elif kind == "nonfinite":
        target.write_text('{"operation":"shard-0","stage":NaN,"state":"FAILED","code":"REJECTED"}')
    elif kind in ("extra-file", "temporary-file", "extra-op"):
        name = {
            "extra-file": "raw.txt",
            "temporary-file": ".ci-public-fixture",
            "extra-op": "shard-1.json",
        }[kind]
        (private / name).write_text(CANARY)
    elif kind in ("symlink", "fifo", "directory"):
        target.unlink()
        if kind == "symlink":
            (tmp_path / "secret").write_text(CANARY)
            target.symlink_to(tmp_path / "secret")
        elif kind == "fifo":
            os.mkfifo(target)
        else:
            target.mkdir()
    elif kind == "hardlink-log":
        (tmp_path / "secret").write_text(CANARY)
        os.link(tmp_path / "secret", private / "shard-0.log")
    public = tmp_path / "diagnostic-public"
    public.mkdir()
    (public / "diagnostics.json").write_text("stale")
    with pytest.raises((transport.TransportError, OSError, ValueError)):
        publish(tmp_path, private)
    assert not (public / "diagnostics.json").exists()


@pytest.mark.parametrize("target", ("coverage", "plan-input", "manifest-input", "private-root"))
def test_overlapping_diagnostic_directory_is_rejected_before_chmod_or_workload(
    monkeypatch, tmp_path, capsys, target
):
    _, args, _ = prepare(tmp_path, monkeypatch)
    overlap = (
        tmp_path
        / {
            "coverage": "public",
            "plan-input": "inputs",
            "manifest-input": "inputs",
            "private-root": "private",
        }[target]
    )
    overlap.mkdir(exist_ok=True)
    overlap.chmod(0o755)
    args[args.index("--diagnostic-directory") + 1] = str(overlap)
    called = []
    monkeypatch.setattr(transport, "_execute", lambda *args: called.append(True))
    assert transport.main(args) == 1
    assert called == [] and overlap.stat().st_mode & 0o777 == 0o755
    no_public_canary(tmp_path, capsys)


def test_stale_success_is_removed_before_initial_checkpoint_failure(monkeypatch, tmp_path, capsys):
    _, args, private = prepare(tmp_path, monkeypatch)
    private.mkdir()
    (private / "shard-0.json").write_text(
        json.dumps(
            {"operation": "shard-0", "stage": "publication", "state": "COMPLETED", "code": "COMPLETED"}
        )
    )
    public = tmp_path / "public"
    public.mkdir()
    (public / "receipt-shard-0.json").write_text("stale")

    def broken(*args):
        raise OSError(CANARY)

    monkeypatch.setattr(transport._Diagnostic, "enter", broken)
    assert transport.main(args) == 1
    assert not (private / "shard-0.json").exists()
    assert not (public / "receipt-shard-0.json").exists()
    assert publish(tmp_path, private)["operations"][0]["state"] == "UNAVAILABLE"
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize(
    "stage",
    (
        "initialization",
        "input_validation",
        "execution",
        "receipt_validation",
        "coverage_validation",
        "publication",
    ),
)
def test_killed_owned_fixture_leaves_only_incomplete_fixed_boundary(tmp_path, stage):
    private = tmp_path / "diagnostic-private"
    ready = tmp_path / "ready"
    script = """import sys, time
from pathlib import Path
from scripts import ci_private_transport as t
private, ready, stage, canary = sys.argv[1:]
def work(args, diagnostic):
    diagnostic.enter(stage)
    print(canary, flush=True)
    Path(ready).write_text("ready")
    time.sleep(60)
t._execute = work
raise SystemExit(t.main(["run", "--sha", "a"*40, "--directory", str(Path(private).parent / "raw"), "--public-directory", str(Path(private).parent / "public"), "--plan", str(Path(private).parent / "input/plan.json"), "--phase", "shard-0", "--diagnostic-directory", private, "--diagnostic-invocation", "a"*32]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(private), str(ready), stage, CANARY],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    assert process.returncode == -9
    assert CANARY.encode() not in stdout + stderr
    assert CANARY in (private / "shard-0.log").read_text()
    value = publish(tmp_path, private)
    assert value["operations"] == [
        {"operation": "shard-0", "stage": stage, "state": "INCOMPLETE", "code": "NO_TERMINAL_RECORD"}
    ]
    assert CANARY not in json.dumps(value)


def test_zero_system_exit_is_not_a_completed_operation(monkeypatch, tmp_path, capsys):
    _, args, private = prepare(tmp_path, monkeypatch)

    def exit_early(args, diagnostic):
        raise SystemExit(0)

    monkeypatch.setattr(transport, "_execute", exit_early)
    assert transport.main(args) == 0  # Preserve the old CLI's explicit zero-exit semantics.
    assert record(private)["state"] == "INCOMPLETE"
    assert record(private)["code"] == "NO_TERMINAL_RECORD"
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo", "directory"))
def test_unsafe_private_traceback_target_stops_before_workload(monkeypatch, tmp_path, capsys, kind):
    _, args, private = prepare(tmp_path, monkeypatch)
    private.mkdir()
    external = tmp_path / "external"
    external.write_text(CANARY)
    log = private / "shard-0.log"
    if kind == "symlink":
        log.symlink_to(external)
    elif kind == "hardlink":
        os.link(external, log)
    elif kind == "fifo":
        os.mkfifo(log)
    else:
        log.mkdir()
    called = []
    monkeypatch.setattr(transport, "_execute", lambda *args: called.append(True))
    assert transport.main(args) == 1
    assert not called and external.read_text() == CANARY
    assert not (private / "shard-0.json").exists()
    no_public_canary(tmp_path, capsys)


def test_oversized_checkpoint_rejected_before_json_decoding(monkeypatch, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "shard-0.json").write_text(CANARY * 100)
    decoded = []
    monkeypatch.setattr(transport, "_decode_json", lambda *args: decoded.append(True))
    with pytest.raises(transport.TransportError):
        publish(tmp_path, private)
    assert not decoded
    assert not (tmp_path / "diagnostic-public/diagnostics.json").exists()


@pytest.mark.parametrize(
    "job,phase",
    (
        ("plan", "shard-0"),
        ("process-contracts", "ownership"),
        ("linux-tests", "shard-0"),
        ("service-free", None),
        ("service-free", "ownership"),
    ),
)
def test_cross_job_phase_binding_fails_closed(tmp_path, job, phase):
    with pytest.raises(transport.TransportError):
        publish(tmp_path, tmp_path / "missing", job=job, phase=phase)
    assert not (tmp_path / "diagnostic-public/diagnostics.json").exists()


def test_diagnostic_record_cannot_substitute_for_aggregate_receipts(tmp_path):
    example = evidence()
    directory = tmp_path / "public"
    plan = materialize_public(directory, example)
    (directory / "receipt-shard-0.json").unlink()
    (directory / "diagnostics.json").write_text(
        json.dumps(
            {"operation": "shard-0", "stage": "publication", "state": "COMPLETED", "code": "COMPLETED"}
        )
    )
    with pytest.raises(transport.TransportError):
        transport.verify(
            plan,
            directory,
            plan["sha"],
            example.policy,
            example.manifest,
            {name: {"result": "success"} for name in ("plan", "process-contracts", "service-free")},
        )


@pytest.mark.parametrize("moment", ("before-replace", "after-replace"))
def test_kill_during_checkpoint_atomic_write_never_falls_back_to_stale_success(tmp_path, moment):
    private = tmp_path / "diagnostic-private"
    ready = tmp_path / "ready"
    script = """import os, sys, time
from pathlib import Path
from scripts import ci_private_transport as t
private, ready, moment = sys.argv[1:]
def work(args, diagnostic):
    replace = os.replace
    def stop_at_replace(*args, **kwargs):
        if moment == "after-replace":
            replace(*args, **kwargs)
        Path(ready).write_text("ready")
        time.sleep(60)
        if moment == "before-replace":
            replace(*args, **kwargs)
    t.os.replace = stop_at_replace
    diagnostic.enter("execution")
t._execute = work
raise SystemExit(t.main(["run", "--sha", "a"*40, "--directory", str(Path(private).parent / "raw"), "--public-directory", str(Path(private).parent / "public"), "--plan", str(Path(private).parent / "input/plan.json"), "--phase", "shard-0", "--diagnostic-directory", private, "--diagnostic-invocation", "a"*32]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(private), str(ready), moment],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    assert process.returncode == -9 and stdout == stderr == b""
    if moment == "before-replace":
        with pytest.raises(transport.TransportError):
            publish(tmp_path, private)
        assert not (tmp_path / "diagnostic-public/diagnostics.json").exists()
    else:
        assert publish(tmp_path, private)["operations"] == [
            {
                "operation": "shard-0",
                "stage": "execution",
                "state": "INCOMPLETE",
                "code": "NO_TERMINAL_RECORD",
            }
        ]


@pytest.mark.parametrize(
    "state,stage,code",
    (
        ("COMPLETED", "publication", "COMPLETED"),
        ("FAILED", "execution", "NONZERO_EXIT"),
        ("INCOMPLETE", "receipt_validation", "NO_TERMINAL_RECORD"),
    ),
)
@pytest.mark.parametrize("expected", ("", "b" * 32), ids=("missing-generation", "different-generation"))
def test_every_stale_state_is_unavailable_without_current_workflow_generation(
    tmp_path, state, stage, code, expected
):
    private = tmp_path / "private"
    private.mkdir()
    (private / "shard-0.json").write_text(
        json.dumps(
            {
                "invocation": INVOCATION,
                "record": {"operation": "shard-0", "state": state, "stage": stage, "code": code},
            }
        )
    )
    value = transport.project_diagnostics(
        private,
        tmp_path / "diagnostic-public",
        "service-free",
        "failure",
        "shard-0",
        invocations={"shard-0": expected},
    )
    assert value["operations"] == [
        {"operation": "shard-0", "stage": "unknown", "state": "UNAVAILABLE", "code": "NOT_RECORDED"}
    ]
    assert INVOCATION not in json.dumps(value)
    assert "invocation" not in json.dumps(value)


@pytest.mark.parametrize(
    "kind", ("missing", "extra", "duplicate-cli", "invalid", "not-string", "unknown-operation")
)
def test_expected_generation_must_be_explicit_fixed_operation_mapping(tmp_path, capsys, kind):
    values = {"shard-0": INVOCATION}
    if kind == "missing":
        values = {}
    elif kind == "extra":
        values["shard-1"] = INVOCATION
    elif kind == "invalid":
        values["shard-0"] = CANARY
    elif kind == "not-string":
        values["shard-0"] = 123
    elif kind == "unknown-operation":
        values = {CANARY: INVOCATION}
    else:
        args = [
            "diagnostics",
            "--job",
            "service-free",
            "--status",
            "failure",
            "--phase",
            "shard-0",
            "--directory",
            str(tmp_path / "private"),
            "--public-directory",
            str(tmp_path / "diagnostic-public"),
            "--invocation",
            "shard-0=" + INVOCATION,
            "--invocation",
            "shard-0=" + INVOCATION,
        ]
        assert transport.main(args) == 1
        no_public_canary(tmp_path, capsys)
        return
    with pytest.raises(transport.TransportError):
        transport.project_diagnostics(
            tmp_path / "private",
            tmp_path / "diagnostic-public",
            "service-free",
            "failure",
            "shard-0",
            invocations=values,
        )
    assert not (tmp_path / "diagnostic-public/diagnostics.json").exists()


@pytest.mark.parametrize("failure", ("coverage-clear", "checkpoint-clear", "log-create"))
def test_early_initialization_failure_cannot_replay_old_completed_or_failure(
    monkeypatch, tmp_path, capsys, failure
):
    _, args, private = prepare(tmp_path, monkeypatch)
    private.mkdir()
    checkpoint = private / "shard-0.json"
    checkpoint.write_text(
        json.dumps(
            {
                "invocation": "b" * 32,
                "record": {
                    "operation": "shard-0",
                    "stage": "publication",
                    "state": "COMPLETED",
                    "code": "COMPLETED",
                },
            }
        )
    )
    public = tmp_path / "public"
    public.mkdir()
    if failure == "coverage-clear":
        (public / "receipt-shard-0.json").mkdir()
    elif failure == "checkpoint-clear":
        clear = transport._clear_public

        def reject_private(path, names):
            if Path(path) == private:
                raise OSError(CANARY)
            return clear(path, names)

        monkeypatch.setattr(transport, "_clear_public", reject_private)
    else:
        (private / "shard-0.log").mkdir()
    called = []
    monkeypatch.setattr(transport, "_execute", lambda *args: called.append(True))
    assert transport.main(args) == 1 and called == []
    if failure == "log-create":
        # A nonregular private target correctly rejects the entire publication.
        with pytest.raises(transport.TransportError):
            publish(tmp_path, private)
    else:
        assert publish(tmp_path, private)["operations"][0]["state"] == "UNAVAILABLE"
    no_public_canary(tmp_path, capsys)


@pytest.mark.parametrize(
    "invocation",
    (None, "", "ABCDEF" * 6, CANARY),
    ids=("absent", "empty", "wrong-shape", "private-canary"),
)
def test_invalid_new_generation_never_launches_workload(monkeypatch, tmp_path, capsys, invocation):
    _, args, private = prepare(tmp_path, monkeypatch)
    if invocation is None:
        args = args[:-2]
    else:
        args[-1] = invocation
    called = []
    monkeypatch.setattr(transport, "_execute", lambda *args: called.append(True))
    assert transport.main(args) == 1 and not called
    assert not private.exists()
    no_public_canary(tmp_path, capsys)


def test_private_log_close_failure_cannot_leave_a_fresh_completed_record(monkeypatch, tmp_path, capsys):
    example, args, private = prepare(tmp_path, monkeypatch)
    fake_execution(monkeypatch, execution(example.plan, "shard-0"))
    real_log = transport._log

    @contextmanager
    def fail_diagnostic_close(directory, name):
        with real_log(directory, name) as stream:
            yield stream
        if Path(directory) == private:
            raise OSError(CANARY)

    monkeypatch.setattr(transport, "_log", fail_diagnostic_close)
    assert transport.main(args) == 1
    assert record(private) == {
        "operation": "shard-0",
        "stage": "publication",
        "state": "FAILED",
        "code": "INTERNAL_ERROR",
    }
    publish(tmp_path, private)
    no_public_canary(tmp_path, capsys)
