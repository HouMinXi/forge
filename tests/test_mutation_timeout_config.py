"""Keep the mutation deadline separate from the baseline test deadline."""

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
from code_forge.state import Mode
from tests.mutation_result_fixture import write_inventory


def _machine(root, mutation_timeout=None):
    test = {"command": [sys.executable, "-m", "pytest", "-q"], "timeout_seconds": 901}
    if mutation_timeout is not None:
        test["mutation_timeout_seconds"] = mutation_timeout
    gate = root / ".code-forge" / "gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(yaml.safe_dump({"test": test}), encoding="utf-8")
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[root / "src" / "pkg" / "mod.py"],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
        ),
        source_hash="mutation-timeout-probe",
        baseline_spec_repr="empty",
        cwd=root,
        registry={},
        l2_runner=factories.build_l2_runner(),
    )


@pytest.mark.parametrize("configured, expected", [(1800, 1800), (1, 1), (None, 600)])
def test_local_config_reaches_mutmut_command(tmp_path, monkeypatch, configured, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(factories.shutil, "which", lambda name: "/tools/mutmut")
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda command, **kwargs: ["mutmut"])
    machine = _machine(tmp_path, configured)
    seen = []

    def execute(command, **kwargs):
        seen.append((command, kwargs["timeout"]))
        assert kwargs["cwd"] == str(tmp_path)
        if "run" in command:
            write_inventory(tmp_path, "src/pkg/mod.py")
        output = write_inventory(tmp_path, "src/pkg/mod.py") if "results" in command else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    assert machine._run_l2_phase() == []
    runs = [timeout for command, timeout in seen if command[:2] == ["mutmut", "run"]]
    assert runs == [expected]
    baseline = [timeout for command, timeout in seen if command[0] == sys.executable]
    assert baseline == [901, 901, 901]
    assert machine._state.infra_errors == []


@pytest.mark.parametrize("configured, expected", [(1800, 1800), (1, 1), (None, 600)])
def test_ci_config_reaches_generated_payload(
    tmp_path, monkeypatch, configured, expected, run_detached_payload
):
    monkeypatch.setattr(factories.shutil, "which", lambda name: "/tools/mutmut")
    machine = _machine(tmp_path, configured)
    machine.mode = Mode.CI
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    payloads = []
    real_popen = subprocess.Popen

    def spawn(args, **kwargs):
        if not kwargs.get("start_new_session"):
            return real_popen(args, **kwargs)
        payloads.append(args[2])
        return type("Child", (), {"pid": 999999, "wait": lambda self, timeout: 0})()

    monkeypatch.setattr(mutation.subprocess, "Popen", spawn)
    machine._run_ci()
    assert len(payloads) == 1
    seen = []

    def execute(**kwargs):
        seen.append(kwargs)
        return [], []

    monkeypatch.setattr(mutation, "run_mutation", execute)
    run_detached_payload(payloads[0])
    assert len(seen) == 1
    assert seen[0]["timeout"] == expected
    assert seen[0]["baseline_timeout"] == 901


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "600", None, [], {}])
def test_invalid_config_never_reaches_runner(tmp_path, value):
    machine = _machine(tmp_path)
    gate = tmp_path / ".code-forge" / "gate.yaml"
    data = yaml.safe_load(gate.read_text(encoding="utf-8"))
    data["test"]["mutation_timeout_seconds"] = value
    gate.write_text(yaml.safe_dump(data), encoding="utf-8")
    calls = []
    machine.l2_runner = lambda *args, **kwargs: (calls.append(kwargs) or [], [])
    assert machine._run_l2_phase() == []
    assert calls == []
    assert any("mutation_timeout_seconds" in item for item in machine._state.infra_errors)


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "600", None])
@pytest.mark.parametrize("entry", ["direct", "detached"])
def test_public_entry_rejects_unbounded_timeout(tmp_path, value, entry):
    with pytest.raises(ValueError, match="timeout.*positive integer"):
        if entry == "direct":
            mutation.run_mutation([], ["pytest"], cwd=tmp_path, timeout=value)
        else:
            mutation.launch_detached_mutation(
                [], ["pytest"], tmp_path, tmp_path / "result.json", timeout=value
            )
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "configured, explicit, expected",
    [(None, None, 600), (1800, None, 1800), (1800, 47, 47), (1800, 600, 600)],
)
def test_cli_timeout_precedence(tmp_path, monkeypatch, configured, explicit, expected):
    from code_forge.cli import _build_parser, _run_mutation_check

    _machine(tmp_path, configured)
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-a\n+b\n",
        encoding="utf-8",
    )
    argv = ["mutation-check", "--diff", str(diff)]
    if explicit is not None:
        argv += ["--timeout", str(explicit)]
    args = _build_parser().parse_args(argv)
    seen = []
    monkeypatch.setattr(mutation, "run_mutation", lambda **kwargs: (seen.append(kwargs) or [], []))
    assert _run_mutation_check(args, tmp_path) == 0
    assert seen[0]["timeout"] == expected
    assert seen[0]["baseline_timeout"] == 901


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_rejects_nonpositive_deadline(tmp_path, monkeypatch, capsys, value):
    from code_forge.cli import _build_parser, _run_mutation_check

    diff = tmp_path / "change.diff"
    diff.write_text("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
    args = _build_parser().parse_args(["mutation-check", "--diff", str(diff), "--timeout", value])
    calls = []
    monkeypatch.setattr(mutation, "run_mutation", lambda **kwargs: (calls.append(kwargs) or [], []))
    assert _run_mutation_check(args, tmp_path) == 2
    assert calls == []
    assert "positive integer" in capsys.readouterr().err


def test_cli_override_does_not_hide_invalid_config(tmp_path, monkeypatch, capsys):
    from code_forge.cli import _build_parser, _run_mutation_check

    _machine(tmp_path, 0)
    diff = tmp_path / "change.diff"
    diff.write_text("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
    args = _build_parser().parse_args(["mutation-check", "--diff", str(diff), "--timeout", "47"])
    calls = []
    monkeypatch.setattr(mutation, "run_mutation", lambda **kwargs: (calls.append(kwargs) or [], []))
    assert _run_mutation_check(args, tmp_path) == 2
    assert calls == []
    assert "mutation_timeout_seconds" in capsys.readouterr().err


@pytest.mark.parametrize("runner", ["missing", "default"])
def test_noop_runner_accepts_configured_timeout(tmp_path, monkeypatch, runner):
    monkeypatch.setattr(factories.shutil, "which", lambda name: None)
    machine = _machine(tmp_path, 1800)
    if runner == "default":
        machine.l2_runner = StateMachine.__dataclass_fields__["l2_runner"].default
    findings = machine._run_l2_phase()
    expected = ["mutmut not found on PATH"] if runner == "missing" else []
    assert machine._state.infra_errors == expected
    assert [item.fingerprint for item in findings] == (
        ["mutation-unavailable"] if runner == "missing" else []
    )


def test_unmapped_file_does_not_forward_mutation_timeout(tmp_path):
    machine = _machine(tmp_path, 1800)
    machine.resolved_review = replace(machine.resolved_review, source_files=[tmp_path / "README.md"])
    calls = []
    machine.l2_runner = lambda *args, **kwargs: (calls.append(kwargs) or [], [])
    assert machine._run_l2_phase() == []
    assert calls == [{"baseline_timeout": 120}]
    assert machine._state.infra_errors == []
