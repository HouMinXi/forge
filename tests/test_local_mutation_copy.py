"""Configured mirror support files survive the LOCAL runner boundary."""

import configparser
import subprocess
import sys
from dataclasses import replace

import pytest
import yaml

from code_forge import factories, mutation
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict
from tests.mutation_result_fixture import write_inventory


def make_machine(root, copy_setting, runner):
    source = root / "src" / "pkg" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")
    test = {"command": [sys.executable, "-m", "pytest", "-q"]}
    if copy_setting != "absent":
        test["also_copy"] = copy_setting
    gate = root / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    gate.write_text(yaml.safe_dump({"test": test}), encoding="utf-8")
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[source],
            baseline_content=None,
            git_diff=(
                "diff --git a/src/pkg/sample.py b/src/pkg/sample.py\n"
                "--- a/src/pkg/sample.py\n+++ b/src/pkg/sample.py\n"
                "@@ -1,2 +1,2 @@\n def sample():\n-    return 0\n+    return 1\n"
            ),
            mode_hint="git",
        ),
        source_hash="local-copy-probe",
        baseline_spec_repr="empty",
        cwd=root,
        registry={},
        l0_runner=lambda registry, files: ([], []),
        l2_runner=runner,
        coverage_l1_active=False,
        coverage_exempt_patterns=["**/*.py"],
    )


@pytest.mark.parametrize("configured", [["scripts/", "settings.json"], []])
def test_local_run_forwards_configured_copy_paths(tmp_path, configured):
    calls = []

    def runner(files, command, **kwargs):
        calls.append((files, command, kwargs))
        return [], []

    machine = make_machine(tmp_path, configured, runner)
    assert machine.run() == Verdict.PASS
    assert len(calls) == 3
    assert all(row[2]["also_copy"] == configured for row in calls)
    assert all(row[0] == [str(tmp_path / "src/pkg/sample.py")] for row in calls)
    assert machine._state.infra_errors == []


def test_unconfigured_local_run_keeps_strict_runner_contract(tmp_path):
    calls = []

    def runner(files, command, *, baseline_timeout):
        calls.append(baseline_timeout)
        return [], []

    machine = make_machine(tmp_path, "absent", runner)
    assert machine.run() == Verdict.PASS
    assert calls == [120, 120, 120]
    assert machine._state.infra_errors == []


def test_none_option_keeps_strict_runner_contract(tmp_path, monkeypatch):
    calls = []

    def runner(files, command, *, baseline_timeout):
        calls.append(baseline_timeout)
        return [], []

    machine = make_machine(tmp_path, "absent", runner)
    monkeypatch.setattr(
        "code_forge.gate_check.load_gate_config",
        lambda path: {"test": {"command": ["pytest"], "also_copy": None}},
    )
    assert machine._run_l2_phase() == []
    assert calls == [120]
    assert machine._state.infra_errors == []


def test_null_gate_setting_remains_invalid(tmp_path):
    calls = []
    machine = make_machine(tmp_path, None, lambda *a, **kw: (calls.append(kw) or [], []))
    assert machine._run_l2_phase() == []
    assert calls == []
    assert "also_copy" in machine._state.infra_errors[0]


@pytest.mark.parametrize("kind", ["default", "missing"])
def test_builtin_noop_accepts_copy_configuration(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(factories.shutil, "which", lambda name: None)
    runner = (
        StateMachine.__dataclass_fields__["l2_runner"].default
        if kind == "default"
        else factories.build_l2_runner(cwd=tmp_path)
    )
    machine = make_machine(tmp_path, ["scripts/"], runner)
    findings = machine._run_l2_phase()
    assert machine._state.infra_errors == ([] if kind == "default" else ["mutmut not found on PATH"])
    assert [f.fingerprint for f in findings] == ([] if kind == "default" else ["mutation-unavailable"])


def test_local_factory_reaches_real_config_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(factories.shutil, "which", lambda name: "/tools/mutmut")
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *a, **kw: ["mutmut"])
    runner = factories.build_l2_runner(cwd=tmp_path)
    machine = make_machine(tmp_path, ["scripts/", "settings.json"], runner)
    seen = []

    def execute(command, **kwargs):
        assert str(kwargs["cwd"]) == str(tmp_path)
        if command[:2] == ["mutmut", "run"]:
            config = configparser.ConfigParser()
            config.read(tmp_path / "setup.cfg")
            seen.append(config["mutmut"]["also_copy"].splitlines())
            write_inventory(tmp_path, "src/pkg/sample.py")
        output = write_inventory(tmp_path, "src/pkg/sample.py") if "results" in command else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    machine.resolved_review = replace(
        machine.resolved_review, source_files=[tmp_path / "src/pkg/sample.py"]
    )
    monkeypatch.setattr(mutation, "run_owned_command", execute)
    assert machine._run_l2_phase() == []
    assert machine._state.infra_errors == []
    assert seen == [["scripts/", "settings.json"]]
    assert machine._state.infra_errors == []
    assert not (tmp_path / "setup.cfg").exists()
