"""Failure causes survive mutation and FIXVAL orchestration components.

FIXVAL evidence-boundary mocks are isolated unit inputs, never execution
qualification. Healthy unit records are stopped before source reversal/proof.
"""

import json
import subprocess

import pytest

from code_forge import fixval, mutation
from code_forge.disposition import Disposition
from code_forge.fixval_evidence import EvidenceError, EvidenceSession, Inventory


PATCH = """diff --git a/src/pkg/mod.py b/src/pkg/mod.py
--- a/src/pkg/mod.py
+++ b/src/pkg/mod.py
@@ -1 +1 @@
-value = 0
+value = 1
"""
CMD = ["python3", "-m", "pytest"]


@pytest.fixture
def private_project(tmp_path, monkeypatch):
    source = tmp_path / "src/pkg/mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    test = tmp_path / "tests/test_mod.py"
    test.parent.mkdir()
    test.write_text("def test_value():\n    assert True\n")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.setenv("PYTHONPATH", "/foreign/source")
    return tmp_path


def assert_cause(findings):
    assert len(findings) == 1
    finding = findings[0]
    assert "baseline runner unavailable" in finding.description
    assert "stderr: /caller-marker/python3: No module named pytest" in finding.description
    assert "stdout:" not in finding.description
    assert "flaky" not in finding.description
    assert (
        finding.id,
        finding.source,
        finding.disposition,
        finding.file,
        finding.line_range,
        finding.fingerprint,
    ) == ("MUTATION_SKIPPED", "MUTANT", Disposition.DISMISSED, "", [], "mutation-flaky")


def recording_runner(calls, retry, healthy):
    def run(argv, **kwargs):
        calls.append((list(argv), {**kwargs, "env": dict(kwargs["env"])}))
        if healthy and (not retry or len(calls) > 1):
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(
            argv, 11, "", "/caller-marker/python3: No module named pytest\n"
        )

    return run


def assert_calls(calls, root, retry, healthy, timeout):
    assert len(calls) == (3 + retry if healthy else 1 + retry)
    for index, (argv, kwargs) in enumerate(calls):
        assert argv == CMD + ["tests/test_mod.py"]
        assert kwargs == dict(
            env=kwargs["env"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=str(root),
        )
        assert kwargs["env"]["PYTHONPATH"] == str(root / "src")
        assert ("VIRTUAL_ENV" in kwargs["env"]) is (retry and index == 0)
        if retry and index > 0:
            assert "/private-venv" not in kwargs["env"]["PATH"]


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "retry"])
@pytest.mark.parametrize("healthy", [False, True], ids=["cause", "success"])
def test_mutation_cause_uses_owned_runner_at_each_site(private_project, monkeypatch, retry, healthy):
    root = private_project
    calls, escaped = [], []
    if retry:
        monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
        monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")
    monkeypatch.setattr(mutation, "run_owned_command", recording_runner(calls, retry, healthy))

    def default_dispatch(argv, **kwargs):
        escaped.append(argv)
        return subprocess.CompletedProcess(argv, 11, "", "No module named pytest")

    monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", default_dispatch)

    def stop_engine(*args, **kwargs):
        assert healthy, "unexpected mutmut resolution after skip"
        raise RuntimeError("qualified baseline reached resolver")

    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", stop_engine)
    if healthy:
        with pytest.raises(RuntimeError, match="qualified baseline reached resolver"):
            mutation.run_mutation(
                ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7
            )
    else:
        findings, infra = mutation.run_mutation(
            ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7
        )
        assert not escaped, "baseline bypassed owned runner"
        assert_cause(findings)
        assert infra == [findings[0].description]
    assert not escaped, "baseline bypassed owned runner"
    assert_calls(calls, root, retry, healthy, 7)
    assert (root / "src/pkg/mod.py").read_text() == "value = 1\n"
    assert not (root / "mutants").exists() and not (root / "setup.cfg").exists()
    journal = json.loads((root / ".code-forge/mutation-owner.lock").read_text())
    assert journal["phase"] == "complete"


def recording_fixval_boundary(calls, observation):
    """Supply isolated unit evidence input, never actual owner qualification."""

    def execute(session, env, *, phase, timeout):
        calls.append((list(session.command), {"env": dict(env), "phase": phase, "timeout": timeout}))
        result = observation(len(calls))
        record = {
            "phase": phase,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "status": "error" if result.returncode else "complete",
            "ownership": {"cleanup_complete": True},  # isolated boundary input, no live authority
        }
        session.phases.append(record)
        if result.returncode:
            raise EvidenceError("closed pytest evidence unavailable")
        inventory = Inventory(
            {
                "tests/test_mod.py::test_value": (
                    "tests/test_mod.py",
                    "passed",
                    "passed",
                    "passed",
                    False,
                )
            },
            0,
            f"{len(calls):064x}",
            100,
            "9.1.1",
        )
        record["inventory"] = inventory
        return inventory

    return execute


def assert_fixval_calls(calls, root, retry, healthy):
    assert len(calls) == (3 + retry if healthy else 1 + retry)
    for index, (argv, kwargs) in enumerate(calls):
        assert argv == CMD + ["tests/test_mod.py"]
        assert kwargs["timeout"] == 120
        assert kwargs["phase"] == (
            "fixed:0:0" if index == 0 else f"fixed:{int(retry)}:{index - int(retry)}"
        )
        assert kwargs["env"]["PYTHONPATH"] == str(root / "src")
        assert ("VIRTUAL_ENV" in kwargs["env"]) is (retry and index == 0)
        if retry and index > 0:
            assert "/private-venv" not in kwargs["env"]["PATH"]


def assert_fixval_error(result):
    assert result.status == fixval.FixvalStatus.ERROR
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.id == "FIXVAL_ERROR"
    assert finding.source == "FIXVAL"
    assert finding.disposition == Disposition.UNCERTAIN
    assert finding.file == "" and finding.line_range == []
    assert finding.fingerprint.startswith("fixval-")
    assert result.infra_errors == [finding.description]
    assert finding.error == finding.description
    assert result.advisories == []
    return finding


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "retry"])
@pytest.mark.parametrize("healthy", [False, True], ids=["cause", "success"])
def test_fixval_cause_survives_fail_closed_boundary(private_project, monkeypatch, retry, healthy):
    root = private_project
    calls, transactions = [], []
    if retry:
        monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
        monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")

    def observation(count):
        if healthy and (not retry or count > 1):
            return subprocess.CompletedProcess(CMD, 0, "", "")
        return subprocess.CompletedProcess(
            CMD, 11, "", "/caller-marker/python3: No module named pytest\n"
        )

    monkeypatch.setattr(EvidenceSession, "execute", recording_fixval_boundary(calls, observation))

    def stop_reversal(*args, **kwargs):
        assert healthy, "failed baseline reached reversal"
        transactions.append(args)
        # Synthetic unit inventories must never produce a full qualified gate result.
        raise EvidenceError("unit baseline boundary reached; no execution qualification")

    monkeypatch.setattr(fixval, "_transactional_probe", stop_reversal)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH)
    finding = assert_fixval_error(result)
    if healthy:
        assert len(transactions) == 1
        assert "unit baseline boundary reached" in finding.description
    else:
        assert not transactions
        assert "stderr: /caller-marker/python3: No module named pytest" in finding.description
        assert "stdout:" not in finding.description
        assert "fixed:" in finding.description
        assert "11" in finding.description
        assert "flaky" not in finding.description
    assert_fixval_calls(calls, root, retry, healthy)
    assert (root / "src/pkg/mod.py").read_text() == "value = 1\n"
    assert not list(root.glob(".fixval*"))


@pytest.mark.parametrize("caller", ["mutation", "fixval"])
@pytest.mark.parametrize("retry", [False, True], ids=["initial", "retry"])
@pytest.mark.parametrize("module", ["pytest", "pytest_project_helper"], ids=["exact", "prefix"])
def test_failed_runner_text_does_not_authorize_each_caller_before_engine(
    private_project, monkeypatch, caller, retry, module
):
    root = private_project
    calls = []
    if retry:
        monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
        monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")

    def execute(argv, **kwargs):
        calls.append((list(argv), {**kwargs, "env": dict(kwargs["env"])}))
        if retry and len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, "", "/usr/bin/python3: No module named pytest\n")
        output = f"E AssertionError: No module named {module}\nFAILED tests/test_mod.py::test_value\n1 failed\n"
        return subprocess.CompletedProcess(argv, 1, output, "")

    def forbidden(*args, **kwargs):
        raise AssertionError("failed baseline started engine or reversal")

    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", forbidden)
    monkeypatch.setattr(fixval, "_transactional_probe", forbidden)
    if caller == "mutation":
        monkeypatch.setattr(mutation, "run_owned_command", execute)
        monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", forbidden)
        findings, infra = mutation.run_mutation(
            ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7
        )
        assert infra == [findings[0].description]
    else:

        def observation(count):
            if retry and count == 1:
                return subprocess.CompletedProcess(
                    CMD, 1, "", "/usr/bin/python3: No module named pytest\n"
                )
            output = f"E AssertionError: No module named {module}\nFAILED tests/test_mod.py::test_value\n1 failed\n"
            return subprocess.CompletedProcess(CMD, 1, output, "")

        monkeypatch.setattr(EvidenceSession, "execute", recording_fixval_boundary(calls, observation))
        candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
        result = fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH)
        findings = [assert_fixval_error(result)]
    assert len(findings) == 1
    if caller == "mutation":
        assert findings[0].fingerprint == "mutation-flaky"
        assert "baseline failed" in findings[0].description
        assert ("after env retry" in findings[0].description) is retry
        assert_calls(calls, root, retry, False, 7)
    else:
        assert "stdout:" in findings[0].description
        assert (
            "fixed:1:0" in findings[0].description if retry else "fixed:0:0" in findings[0].description
        )
        assert_fixval_calls(calls, root, retry, False)
    assert "runner unavailable" not in findings[0].description
    assert "tests/test_mod.py::test_value" in findings[0].description
    assert (root / "src/pkg/mod.py").read_text() == "value = 1\n"
    assert not (root / "mutants").exists() and not (root / "setup.cfg").exists()
    assert not list(root.glob(".fixval*"))
