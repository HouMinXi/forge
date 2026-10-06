"""Configured LOCAL resource caps survive the actual runner boundary."""

import inspect
import os
import shutil
import subprocess
from dataclasses import replace
from unittest.mock import patch

import pytest
import yaml

from code_forge import cli, factories, machine, mutation
from tests.test_local_mutation_copy import make_machine


@pytest.fixture(autouse=True)
def forbid_process_creation(monkeypatch):
    blocked = []

    def refuse(*args, **kwargs):
        blocked.append(True)
        raise AssertionError("recording-only resource control attempted process creation")

    for module, names in (
        (subprocess, ("Popen", "run", "call", "check_call", "check_output")),
        (os, ("fork", "forkpty", "posix_spawn", "posix_spawnp", "system", "execv", "execve")),
    ):
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, refuse)
    yield
    assert blocked == []


def configured_machine(root, settings, runner):
    obj = make_machine(root, "absent", runner)
    gate = root / ".code-forge" / "gate.yaml"
    data = yaml.safe_load(gate.read_text())
    data["test"].update(settings)
    gate.write_text(yaml.safe_dump(data), encoding="utf-8")
    return obj


SETTINGS = [
    ({"mutation_max_children": 1, "mutation_memory_limit_mb": 64}, 1, 64 * 1024**2),
    ({"mutation_max_children": 2}, 2, None),
    ({"mutation_memory_limit_mb": 96}, None, 96 * 1024**2),
]


@pytest.mark.parametrize("settings,children,memory", SETTINGS)
def test_configured_limits_reach_real_local_factory(tmp_path, monkeypatch, settings, children, memory):
    calls = []
    signature = inspect.signature(mutation.run_mutation)

    def record(*args, **kwargs):
        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        calls.append(values.arguments)
        return [], []

    monkeypatch.setattr(factories.shutil, "which", lambda name: "/recording-only/mutmut")
    monkeypatch.setattr(factories, "run_mutation", record)
    obj = configured_machine(tmp_path, settings, factories.build_l2_runner(cwd=tmp_path))
    assert obj._run_l2_phase() == []
    assert obj._state.infra_errors == []
    assert len(calls) == 1
    assert calls[0]["max_children"] == children
    assert calls[0]["memory_limit_bytes"] == memory
    assert calls[0]["cwd"] == tmp_path


def test_repeated_local_calls_forward_both_caps(tmp_path):
    calls = []

    def record(files, command, **kwargs):
        calls.append(kwargs)
        return [], []

    obj = configured_machine(tmp_path, SETTINGS[0][0], record)
    for _ in range(3):
        assert obj._run_l2_phase() == []
    assert len(calls) == 3
    assert obj._state.infra_errors == []
    assert all(c["max_children"] == 1 and c["memory_limit_bytes"] == 64 * 1024**2 for c in calls)


@pytest.mark.parametrize("settings,children,memory", SETTINGS)
def test_ci_and_direct_cli_preserve_same_configured_limits(
    tmp_path, monkeypatch, settings, children, memory
):
    obj = configured_machine(tmp_path, settings, lambda *a, **kw: ([], []))
    monkeypatch.setattr(shutil, "which", lambda name: "/recording-only/mutmut")
    with (
        patch.object(obj, "_execute_round"),
        patch.object(obj, "_receipt_gate_round_errors", return_value=[]),
        patch.object(obj, "_receipt_gate_terminal_errors", return_value=[]),
        patch.object(obj, "_persist_state"),
        patch.object(obj, "_write_ci_ledger_rows"),
        patch.object(obj, "_suppress_known_findings"),
        patch.object(machine, "launch_detached_mutation", return_value=True) as launch,
    ):
        obj._run_ci()
    assert launch.call_count == 1
    assert launch.call_args.kwargs["max_children"] == children
    assert launch.call_args.kwargs["memory_limit_bytes"] == memory
    diff = tmp_path / "change.diff"
    diff.write_text(obj.resolved_review.git_diff, encoding="utf-8")
    args = cli._build_parser().parse_args(["mutation-check", "--diff", str(diff)])
    with patch.object(mutation, "run_mutation", return_value=([], [])) as engine:
        assert cli._run_mutation_check(args, tmp_path) == 0
    assert engine.call_count == 1
    assert engine.call_args.kwargs["max_children"] == children
    assert engine.call_args.kwargs["memory_limit_bytes"] == memory


@pytest.mark.parametrize("unmapped", [False, True])
def test_absent_or_unmapped_settings_keep_strict_runner(tmp_path, unmapped):
    calls = []

    def strict(files, command, *, baseline_timeout):
        calls.append((files, command, baseline_timeout))
        return [], []

    obj = configured_machine(tmp_path, SETTINGS[0][0] if unmapped else {}, strict)
    if unmapped:
        source = tmp_path / "README.md"
        source.write_text("reviewed documentation\n", encoding="utf-8")
        obj.resolved_review = replace(obj.resolved_review, source_files=[source])
    assert obj._run_l2_phase() == []
    assert obj._state.infra_errors == []
    assert len(calls) == 1
    assert calls[0][2] == 120


@pytest.mark.parametrize("kind", ["default", "missing"])
def test_builtin_noop_accepts_configured_caps(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(factories.shutil, "which", lambda name: None)
    runner = (
        machine.StateMachine.__dataclass_fields__["l2_runner"].default
        if kind == "default"
        else factories.build_l2_runner(cwd=tmp_path)
    )
    obj = configured_machine(tmp_path, SETTINGS[0][0], runner)
    findings = obj._run_l2_phase()
    assert obj._state.infra_errors == ([] if kind == "default" else ["mutmut not found on PATH"])
    assert [f.fingerprint for f in findings] == ([] if kind == "default" else ["mutation-unavailable"])


@pytest.mark.parametrize("key", ["mutation_max_children", "mutation_memory_limit_mb"])
@pytest.mark.parametrize("value", [True, 0, "2"])
def test_invalid_gate_caps_do_not_reach_runner(tmp_path, key, value):
    calls = []
    obj = configured_machine(tmp_path, {key: value}, lambda *a, **kw: (calls.append(kw) or [], []))
    assert obj._run_l2_phase() == []
    assert calls == []
    assert len(obj._state.infra_errors) == 1
    assert key in obj._state.infra_errors[0]
