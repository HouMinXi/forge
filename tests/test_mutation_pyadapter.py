"""Unit tests for the mutmut adapter mapping rules, probe and registry."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters import (
    get_adapter,
    registered_adapter_ids,
)
from code_forge.mutation_engines.adapters.base import (
    CapabilityState,
    ExecutionContext,
)
from code_forge.mutation_engines.adapters.python_mutmut import (
    ADAPTER_ID,
    MutmutAdapter,
    _load_event,
    _map_outcome,
)
from code_forge.mutation_engines.schemas import (
    Budget,
    NormalizedStatus,
    TargetDeclaration,
)

CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service"


def _event(**overrides) -> dict:
    base = {
        "final": True,
        "plugin": "forge-mutation-report",
        "run_id": "run-u",
        "mutant_id": "m1",
        "executed": 3,
        "failed_assertions": 0,
        "setup_errors": 0,
        "teardown_errors": 0,
        "collection_errors": 0,
        "internal_errors": 0,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "exit_code,event,expected",
    [
        (None, None, NormalizedStatus.PENDING),
        (3, _event(failed_assertions=2), NormalizedStatus.RUNTIME_ERROR),
        (1, None, NormalizedStatus.UNKNOWN),
        (1, _event(failed_assertions=1), NormalizedStatus.KILLED),
        (1, _event(failed_assertions=1, setup_errors=1), NormalizedStatus.UNKNOWN),
        (1, _event(failed_assertions=0), NormalizedStatus.UNKNOWN),
        (1, _event(failed_assertions=1, run_id="other"), NormalizedStatus.UNKNOWN),
        (1, _event(failed_assertions=1, mutant_id="m2"), NormalizedStatus.UNKNOWN),
        (1, _event(failed_assertions=1, final=False), NormalizedStatus.UNKNOWN),
        (0, _event(), NormalizedStatus.SURVIVED),
        (0, None, NormalizedStatus.UNKNOWN),
        (0, _event(executed=0), NormalizedStatus.UNKNOWN),
        (0, _event(internal_errors=1), NormalizedStatus.UNKNOWN),
        (5, None, NormalizedStatus.NO_COVERAGE),
        (33, None, NormalizedStatus.NO_COVERAGE),
        (36, None, NormalizedStatus.TIMED_OUT),
        (24, None, NormalizedStatus.TIMED_OUT),
        (34, None, NormalizedStatus.IGNORED),
        (37, None, NormalizedStatus.NONVIABLE),
        (2, None, NormalizedStatus.RUNTIME_ERROR),
        (99, None, NormalizedStatus.UNKNOWN),
    ],
)
def test_status_mapping(exit_code, event, expected):
    assert _map_outcome(exit_code, event, "run-u", "m1") is expected


def test_load_event_rejects_wrong_plugin(tmp_path):
    path = tmp_path / "run-u__baseline.json"
    path.write_text(json.dumps({"plugin": "other", "final": True}))
    assert _load_event(tmp_path, "run-u", None) is None


def test_load_event_rejects_garbage(tmp_path):
    path = tmp_path / "run-u__baseline.json"
    path.write_text("not json")
    assert _load_event(tmp_path, "run-u", None) is None


def test_registry_has_five_keys():
    ids = registered_adapter_ids()
    assert ids == (
        "go-gremlins",
        "js-stryker",
        "patch-corpus",
        "python-mutmut",
        "rust-cargo-mutants",
    )
    assert isinstance(get_adapter("python-mutmut"), MutmutAdapter)


def test_registry_stub_probe_missing_dependency():
    adapter = get_adapter("go-gremlins")
    context = ExecutionContext(
        run_id="run-u",
        config_digest="c",
        execution_policy_digest="e",
        toolchain_fingerprint="t",
        cgroup_root=CGROUP_ROOT,
        state_root="/tmp",
        approved_python="/usr/bin/python3",
        memory_mb=512,
        pids=64,
        workspace_mb=64,
        process_headroom_mb=128,
    )
    target = TargetDeclaration(
        id="t1",
        adapter="go-gremlins",
        root=".",
        sources=("src/**",),
        tests=("tests/**",),
        inputs=(),
        oracle="test",
        command=("true",),
        execution_profile="p",
        environment="e",
        budget=Budget(
            total_seconds=60,
            baseline_seconds=30,
            mutant_seconds=30,
            concurrency=1,
            memory_mb=512,
            processes=8,
            workspace_mb=64,
            evidence_mb=16,
        ),
    )
    report = adapter.probe(target, context)
    assert report.state is CapabilityState.MISSING_DEPENDENCY


def test_registry_unknown_id():
    with pytest.raises(KeyError):
        get_adapter("no-such-adapter")


def _fake_python(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-python"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _context(python: str, cgroup: str = CGROUP_ROOT) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-u",
        config_digest="c",
        execution_policy_digest="e",
        toolchain_fingerprint="t",
        cgroup_root=cgroup,
        state_root="/tmp",
        approved_python=python,
        memory_mb=512,
        pids=64,
        workspace_mb=64,
        process_headroom_mb=128,
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="t1",
        adapter=ADAPTER_ID,
        root=".",
        sources=("src/**",),
        tests=("tests/**",),
        inputs=(),
        oracle="test",
        command=("true",),
        execution_profile="p",
        environment="e",
        budget=Budget(
            total_seconds=60,
            baseline_seconds=30,
            mutant_seconds=30,
            concurrency=1,
            memory_mb=512,
            processes=8,
            workspace_mb=64,
            evidence_mb=16,
        ),
    )


def test_probe_missing_python(tmp_path):
    report = MutmutAdapter().probe(_target(), _context("/no/such/python"))
    assert report.state is CapabilityState.MISSING_DEPENDENCY
    assert report.errors[0].code == "missing-python"


def test_probe_missing_mutmut(tmp_path):
    python = _fake_python(tmp_path, "exit 1\n")
    report = MutmutAdapter().probe(_target(), _context(python))
    assert report.state is CapabilityState.MISSING_DEPENDENCY


def test_probe_unsupported_version(tmp_path):
    python = _fake_python(tmp_path, "echo 9.9.9\n")
    report = MutmutAdapter().probe(_target(), _context(python))
    assert report.state is CapabilityState.UNSUPPORTED_VERSION
    assert report.resolved_tool_version == "9.9.9"


def test_probe_unavailable_isolation(tmp_path):
    python = _fake_python(tmp_path, "echo 3.8.0\n")
    report = MutmutAdapter().probe(
        _target(), _context(python, cgroup="/no/such/cgroup")
    )
    assert report.state is CapabilityState.UNAVAILABLE_ISOLATION


def test_context_rejects_headroom_violation():
    with pytest.raises(ValueError):
        ExecutionContext(
            run_id="run-u",
            config_digest="c",
            execution_policy_digest="e",
            toolchain_fingerprint="t",
            cgroup_root=CGROUP_ROOT,
            state_root="/tmp",
            approved_python="/usr/bin/python3",
            memory_mb=100,
            pids=64,
            workspace_mb=80,
            process_headroom_mb=30,
        )


def test_context_rejects_bad_run_id():
    with pytest.raises(ValueError):
        ExecutionContext(
            run_id="../x",
            config_digest="c",
            execution_policy_digest="e",
            toolchain_fingerprint="t",
            cgroup_root=CGROUP_ROOT,
            state_root="/tmp",
            approved_python="/usr/bin/python3",
            memory_mb=512,
            pids=64,
            workspace_mb=64,
            process_headroom_mb=128,
        )
