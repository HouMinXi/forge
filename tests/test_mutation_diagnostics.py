# SPDX-License-Identifier: Apache-2.0
"""Mutation tool failures must retain diagnostics and never look successful."""

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from code_forge import mutation
from code_forge.basis import derive_basis
from code_forge.disposition import Disposition
from code_forge.mutation import launch_detached_mutation, run_mutation
from code_forge.sarif import build_sarif_log, format_summary
from code_forge.state import State, StateFinding, Verdict, load_state, save_state
from tests.mutation_result_fixture import write_inventory


@pytest.mark.parametrize("phase", ["run", "results"])
@pytest.mark.parametrize(
    "stdout,stderr",
    [
        ("FileNotFoundError: missing source_paths", ""),
        ("", "cannot open cache"),
        ("x" * 5000 + "stdout cause", "y" * 5000 + "stderr cause"),
        ("", ""),
    ],
)
def test_process_failure_keeps_both_stream_tails(tmp_path, phase, stdout, stderr):
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        # The mutmut subcommand is a token of the argv, not the last
        # element: run_mutation appends "--max-children <n>" after "run".
        failed = phase in args
        if "run" in args:
            write_inventory(tmp_path, "source.py")
        return subprocess.CompletedProcess(
            args,
            7 if failed else 0,
            stdout=stdout if failed else "",
            stderr=stderr if failed else "",
        )

    with (
        patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut"),
        patch("code_forge.mutation._resolve_mutmut_invocation", return_value=["mutmut"]),
        patch("code_forge.mutation.run_owned_command", side_effect=command),
    ):
        evidence = {}
        findings, errors = run_mutation(
            ["source.py"], ["pytest"], cwd=tmp_path, _evidence=evidence
        )
    assert len(findings) == 1
    assert findings[0].id == "MUTATION_ERROR"
    assert findings[0].disposition == Disposition.CONFIRMED
    assert findings[0].fingerprint == f"mutation-{phase}-error"
    assert any(phase in args for args in commands)
    assert evidence["baseline_passed"] is False
    assert evidence["completed_measurement"] is False
    assert errors == [findings[0].description]
    _assert_unavailable_evidence(findings[0])
    for message in [findings[0].description, *errors]:
        assert phase in message and "7" in message
        assert len(message) < 2500
        if stdout:
            assert "stdout" in message and stdout[-30:] in message
        if stderr:
            assert "stderr" in message and stderr[-30:] in message
        if not stdout and not stderr:
            assert "no output" in message


def _assert_unavailable_evidence(finding):
    assert finding.source == "INFRA"
    basis = derive_basis(finding)
    assert basis.authority == "infra-unavailable"
    assert basis.falsification_survived is False


def _foreign_container(root):
    (root / "source.py").write_text("def value():\n    return 1\n")
    container = root / ".code-forge" / "mutation-empty-mirror"
    container.mkdir(parents=True)
    return container


def test_owned_preflight_refusal_has_no_executed_authority(tmp_path):
    container = _foreign_container(tmp_path)
    original = container.stat()
    evidence = {}
    with (
        patch("code_forge.mutation._run_baseline_guard") as baseline,
        patch("code_forge.mutation.run_owned_command") as command,
    ):
        findings, infra = run_mutation(
            ["source.py"], ["pytest"], cwd=tmp_path, _evidence=evidence
        )
    baseline.assert_not_called()
    command.assert_not_called()
    assert len(findings) == 1
    finding = findings[0]
    message = "foreign mutation container has no completed ownership record"
    assert finding.id == "MUTATION_ERROR"
    assert finding.fingerprint == "mutation-evidence-error"
    assert finding.disposition == Disposition.CONFIRMED
    assert finding.description == message
    assert finding.file == "" and finding.line_range == []
    assert infra == [message]
    assert evidence["baseline_passed"] is False
    assert evidence["completed_measurement"] is False
    assert evidence["infra_errors"] == infra
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["status"] == "error"
    assert outcome["survivors"] == []
    assert outcome["baseline_passed"] is False
    assert outcome["infra_errors"] == infra
    assert outcome["message"] == "\n".join([message, message])
    current = container.stat()
    assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
    assert list(container.iterdir()) == []
    assert not (tmp_path / "setup.cfg").exists()
    assert (tmp_path / "source.py").read_text() == "def value():\n    return 1\n"

    state_path = tmp_path / "state.json"
    save_state(State(findings=findings, infra_errors=infra, verdict=Verdict.FAIL), state_path)
    state = load_state(state_path)
    assert state is not None
    assert state.findings == findings and state.infra_errors == infra
    result = build_sarif_log(state, {}, "test")["runs"][0]["results"][0]
    assert result["ruleId"] == finding.fingerprint
    assert result["message"]["text"] == message
    assert result["level"] == "error" and "suppressions" not in result
    assert result["properties"]["source"] == "INFRA"
    assert result["properties"]["basis"]["authority"] == "infra-unavailable"
    assert result["properties"]["basis"]["falsification_survived"] is False
    assert "infra=1" in format_summary(state)
    _assert_unavailable_evidence(finding)


def test_detached_owned_preflight_preserves_error_contract(tmp_path, run_detached_payload):
    _foreign_container(tmp_path)
    captured = []

    def spawn(args, **kwargs):
        captured.append(args[2])
        return type("Child", (), {"pid": 1234, "wait": lambda self, timeout=None: 0})()

    result_path = tmp_path / "result.json"
    with patch("code_forge.mutation.subprocess.Popen", side_effect=spawn):
        assert launch_detached_mutation(["source.py"], ["pytest"], tmp_path, result_path)
    with (
        patch("code_forge.mutation._run_baseline_guard") as baseline,
        patch("code_forge.mutation.run_owned_command") as command,
    ):
        run_detached_payload(captured[0])
    baseline.assert_not_called()
    command.assert_not_called()
    data = json.loads(result_path.read_text())
    message = "foreign mutation container has no completed ownership record"
    assert data["status"] == "error"
    assert data["baseline_passed"] is False
    assert data["survivors"] == []
    assert data["inventory"] == {} and data["skipped"] == []
    assert data["infra_errors"] == [message]
    assert data["message"] == "\n".join([message, message])


def test_parsed_survivor_retains_executed_authority(tmp_path):
    (tmp_path / "source.py").write_text("def value():\n    return 1\n")
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        stdout = ""
        if "run" in args:
            write_inventory(tmp_path, "source.py", {"x_example__mutmut_1": "survived"})
        elif "results" in args:
            stdout = "source.x_example__mutmut_1: survived"
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    evidence = {}
    with (
        patch("code_forge.mutation._resolve_mutmut_invocation", return_value=["mutmut"]),
        patch("code_forge.mutation.run_owned_command", side_effect=command),
    ):
        findings, infra = run_mutation(
            ["source.py"], ["pytest"], cwd=tmp_path, _evidence=evidence
        )
    assert infra == [] and len(findings) == 1
    finding = findings[0]
    assert finding.id == "mutant-source.x_example__mutmut_1"
    assert finding.fingerprint == "mutant:source.x_example__mutmut_1"
    assert finding.source == "MUTANT" and finding.disposition == Disposition.CONFIRMED
    basis = derive_basis(finding)
    assert basis.authority == "deterministic-executed"
    assert basis.falsification_survived is True
    assert evidence["baseline_passed"] is True
    assert evidence["completed_measurement"] is True
    assert any("results" in args for args in commands)
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["status"] == "done" and outcome["survivors"] == [finding.id]


@pytest.mark.parametrize("outcome", ["error", "exception", "survivor", "clean"])
def test_detached_script_preserves_outcome(tmp_path, outcome, run_detached_payload):
    captured = {}

    def spawn(args, **kwargs):
        captured["script"] = args[2]
        return type("Child", (), {"pid": 1234, "wait": lambda self, timeout=None: 0})()

    result_path = tmp_path / "result.json"
    with patch("code_forge.mutation.subprocess.Popen", side_effect=spawn):
        assert (
            launch_detached_mutation(
                ["source.py"],
                ["pytest"],
                tmp_path,
                result_path,
            )
            is True
        )
    # Execute the generated production script, replacing only the external run.
    finding = StateFinding(
        id="MUTATION_ERROR" if outcome == "error" else "mutant-example",
        fingerprint="test",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="",
        line_range=[],
        description="tool failed on stdout",
    )
    runner = "code_forge.mutation.run_mutation"
    with patch(runner) as run:
        if outcome == "exception":
            run.side_effect = RuntimeError("runner exploded")
        else:

            def measured(**kwargs):
                kwargs["_evidence"]["baseline_passed"] = outcome in ("clean", "survivor")
                return ([finding] if outcome != "clean" else [], [])

            run.side_effect = measured
        # Only the script generated above by our launcher is executed.
        run_detached_payload(captured["script"])
    data = json.loads(result_path.read_text())
    if outcome in ("error", "exception"):
        assert data["status"] == "error"
        assert data["message"] == (
            "runner exploded" if outcome == "exception" else "tool failed on stdout"
        )
        assert not data["survivors"]
    else:
        assert data["status"] == "done"
        assert data["survivors"] == (["mutant-example"] if outcome == "survivor" else [])


@pytest.mark.integration
def test_multiple_paths_are_read_by_real_mutmut(tmp_path):
    pytest.importorskip("mutmut")
    from code_forge.mutation import _build_mutmut_config

    paths = ["src/first.py", "lib/second.py"]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(exist_ok=True)
        target.write_text("def value():\n    return 1\n")
    (tmp_path / "setup.cfg").write_text(_build_mutmut_config(paths, ["pytest"]))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; import mutmut.configuration as module; "
                "c=module.config() if hasattr(module, 'config') else module.Config.get(); "
                "print(json.dumps([list(map(str,c.source_paths)), c.only_mutate]))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [["lib", "src"], paths]


@pytest.mark.integration
def test_real_mutmut_checks_both_changed_files(tmp_path):
    pytest.importorskip("mutmut")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for name in ("first", "second"):
        (tmp_path / "src" / f"{name}.py").write_text("def value(x):\n    return x + 1\n")
    # Deliberately weak assertions leave a survivor in each real source file.
    (tmp_path / "tests" / "test_values.py").write_text(
        "from first import value as a\nfrom second import value as b\n"
        "def test_values():\n    assert a(10) > 0\n    assert b(10) > 0\n"
    )
    findings, errors = run_mutation(
        ["src/first.py", "src/second.py"],
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=tmp_path,
        timeout=60,
    )
    assert not errors
    assert {f.id.split(".")[0] for f in findings} == {"mutant-first", "mutant-second"}
    assert all(f.disposition == Disposition.CONFIRMED for f in findings)
    assert not (tmp_path / "setup.cfg").exists()
    assert not (tmp_path / "mutants").exists()


@pytest.mark.integration
def test_real_mutmut_stdout_failure_is_visible(tmp_path):
    pytest.importorskip("mutmut")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "probe.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tests" / "test_probe.py").write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "def test_child(tmp_path):\n"
        "    script = Path(__file__).resolve().parents[1] / 'src/probe.py'\n"
        "    p = subprocess.run([sys.executable, str(script)], cwd=tmp_path, "
        "capture_output=True, text=True)\n"
        "    assert p.returncode == 0, p.stderr\n"
    )
    findings, errors = run_mutation(
        ["src/probe.py"],
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=tmp_path,
        timeout=60,
    )
    assert findings[0].id == "MUTATION_ERROR"
    assert any(
        cause in findings[0].description
        for cause in (
            "source_paths",
            "could not find any test case for any mutant",
        )
    )
    assert "stdout" in findings[0].description
    assert errors
    assert not (tmp_path / "setup.cfg").exists()
    assert not (tmp_path / "mutants").exists()
