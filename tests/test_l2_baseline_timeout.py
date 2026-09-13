"""Keep the configured test deadline through the LOCAL mutation path."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from code_forge import factories, mutation
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode


def make_machine(tmp_path, command, timeout=None):
    test = {"command": command}
    if timeout is not None:
        test["timeout_seconds"] = timeout
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(yaml.safe_dump({"test": test}))
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("src/sample.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
        ),
        source_hash="timeout-probe",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l2_runner=factories.build_l2_runner(),
    )


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
@pytest.mark.parametrize("configured, expected", [(900, 900), (1, 1), (None, 120)])
def test_deadline_reaches_baseline_subprocess(
    tmp_path, monkeypatch, configured, expected, mode
):
    monkeypatch.setattr(factories.shutil, "which", lambda command: "/tools/mutmut")
    seen = []

    def baseline(command, **kwargs):
        seen.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 1, "baseline stopped", "")

    machine = make_machine(tmp_path, [sys.executable, "-m", "pytest"], configured)
    machine.mode = mode
    monkeypatch.setattr(mutation.subprocess, "run", baseline)
    findings = machine._run_l2_phase()
    assert seen == [expected]
    assert len(findings) == 1
    assert findings[0].fingerprint == "mutation-flaky"


def test_configured_deadline_stops_a_real_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(factories.shutil, "which", lambda command: "/tools/mutmut")
    command = [sys.executable, "-c", "import time; time.sleep(2); raise SystemExit(1)"]
    machine = make_machine(tmp_path, command, 1)
    findings = machine._run_l2_phase()
    assert len(findings) == 1
    assert findings[0].fingerprint == "mutation-baseline-timeout"
    assert machine._state.infra_errors == ["flaky guard: baseline timeout on run 1"]


@pytest.mark.parametrize("runner", ["missing", "default"])
def test_noop_runners_accept_configured_deadline(tmp_path, monkeypatch, runner):
    monkeypatch.setattr(factories.shutil, "which", lambda command: None)
    machine = make_machine(tmp_path, [sys.executable, "-m", "pytest"], 900)
    if runner == "default":
        machine.l2_runner = StateMachine.__dataclass_fields__["l2_runner"].default
    findings = machine._run_l2_phase()
    expected_errors = ["mutmut not found on PATH"] if runner == "missing" else []
    assert machine._state.infra_errors == expected_errors
    expected = ["mutation-unavailable"] if runner == "missing" else []
    assert [finding.fingerprint for finding in findings] == expected


@pytest.mark.parametrize("configured, expected", [(900, 900), (None, 120)])
def test_ci_deadline_reaches_detached_child(tmp_path, monkeypatch, configured, expected):
    monkeypatch.setattr(factories.shutil, "which", lambda command: "/tools/mutmut")
    machine = make_machine(tmp_path, [sys.executable, "-m", "pytest"], configured)
    machine.mode = Mode.CI
    monkeypatch.setattr(
        StateMachine, "_execute_round", lambda self, round_index: None
    )
    captured = []
    real_popen = subprocess.Popen

    class Child:
        pid = 99999999

    def launch(args, **kwargs):
        if kwargs.get("start_new_session"):
            captured.append(args)
            return Child()
        return real_popen(args, **kwargs)

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    machine._run_ci()
    assert len(captured) == 1
    seen = []

    def run_child(diff_files, baseline_cmd, *, cwd, baseline_timeout=120):
        seen.append(baseline_timeout)
        return [], []

    monkeypatch.setattr(mutation, "run_mutation", run_child)
    # Execute only the child script generated above by our own launcher.
    exec(compile(captured[0][2], "<mutation-child>", "exec"), {})  # noqa: S102
    assert seen == [expected]


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
def test_env_retry_keeps_configured_deadline(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(factories.shutil, "which", lambda command: "/tools/mutmut")
    monkeypatch.setenv("VIRTUAL_ENV", "/missing/test-venv")
    machine = make_machine(tmp_path, [sys.executable, "-m", "pytest"], 900)
    machine.mode = mode
    seen = []

    def baseline(command, **kwargs):
        seen.append((kwargs["timeout"], "VIRTUAL_ENV" in kwargs["env"]))
        if len(seen) == 1:
            raise FileNotFoundError("test runner absent")
        return subprocess.CompletedProcess(command, 1, "baseline stopped", "")

    monkeypatch.setattr(mutation.subprocess, "run", baseline)
    findings = machine._run_l2_phase()
    assert seen == [(900, True), (900, False)]
    assert findings[0].fingerprint == "mutation-flaky"


@pytest.mark.parametrize("configured", ["60", None, 0, -1, 1.5])
def test_invalid_deadline_never_reaches_runner(tmp_path, monkeypatch, configured):
    machine = make_machine(tmp_path, [sys.executable, "-m", "pytest"], 900)
    gate = tmp_path / ".code-forge" / "gate.yaml"
    data = yaml.safe_load(gate.read_text())
    data["test"]["timeout_seconds"] = configured
    gate.write_text(yaml.safe_dump(data))
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return [], []

    machine.l2_runner = runner
    assert machine._run_l2_phase() == []
    assert calls == []
    assert len(machine._state.infra_errors) == 1
    assert "test.timeout_seconds" in machine._state.infra_errors[0]
