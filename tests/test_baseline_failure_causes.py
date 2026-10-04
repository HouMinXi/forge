"""Failure causes survive the real mutation and fix-validation callers."""

import json
import subprocess

import pytest

from code_forge import fixval, mutation
from code_forge.disposition import Disposition


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
    assert (finding.id, finding.source, finding.disposition, finding.file, finding.line_range, finding.fingerprint) == (
        "MUTATION_SKIPPED", "MUTANT", Disposition.DISMISSED, "", [], "mutation-flaky"
    )


def recording_runner(calls, retry, healthy):
    def run(argv, **kwargs):
        calls.append((list(argv), {**kwargs, "env": dict(kwargs["env"])}))
        if healthy and (not retry or len(calls) > 1):
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 11, "", "/caller-marker/python3: No module named pytest\n")

    return run


def assert_calls(calls, root, retry, healthy, timeout):
    assert len(calls) == (3 + retry if healthy else 1 + retry)
    for index, (argv, kwargs) in enumerate(calls):
        assert argv == CMD + ["tests/test_mod.py"]
        assert kwargs == dict(
            env=kwargs["env"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, cwd=str(root),
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
            mutation.run_mutation(["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7)
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


@pytest.mark.parametrize("retry", [False, True], ids=["initial", "retry"])
@pytest.mark.parametrize("healthy", [False, True], ids=["cause", "success"])
def test_fixval_cause_survives_findings_only_skip(private_project, monkeypatch, retry, healthy):
    root = private_project
    calls = []
    if retry:
        monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
        monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")
    monkeypatch.setattr(fixval.subprocess, "run", recording_runner(calls, retry, healthy))

    def stop_reversal(*args, **kwargs):
        assert healthy, "unexpected transaction after skip"
        raise RuntimeError("qualified baseline reached transaction")

    allocate = fixval.tempfile.mkstemp
    def stop_allocation(*args, **kwargs):
        assert healthy, "unexpected patch allocation after skip"
        return allocate(*args, **kwargs, dir=root)

    monkeypatch.setattr(fixval, "FixvalTransaction", stop_reversal)
    monkeypatch.setattr(fixval.tempfile, "mkstemp", stop_allocation)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    if healthy:
        with pytest.raises(RuntimeError, match="qualified baseline reached transaction"):
            fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH)
    else:
        result = fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH)
        assert result.status == fixval.FixvalStatus.SKIPPED
        assert_cause(result.findings)
        assert result.advisories == [] and result.block_message == ""
    assert_calls(calls, root, retry, healthy, 120)
    assert (root / "src/pkg/mod.py").read_text() == "value = 1\n"
    assert not list(root.glob(".fixval*"))


@pytest.mark.parametrize("caller", ["mutation", "fixval"])
@pytest.mark.parametrize("retry", [False, True], ids=["initial", "retry"])
@pytest.mark.parametrize("module", ["pytest", "pytest_project_helper"], ids=["exact", "prefix"])
def test_failed_runner_text_skips_each_caller_before_engine(private_project, monkeypatch, caller, retry, module):
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
    monkeypatch.setattr(fixval, "FixvalTransaction", forbidden)
    monkeypatch.setattr(fixval.tempfile, "mkstemp", forbidden)
    if caller == "mutation":
        monkeypatch.setattr(mutation, "run_owned_command", execute)
        monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", forbidden)
        findings, infra = mutation.run_mutation(
            ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7
        )
        assert infra == [findings[0].description]
    else:
        monkeypatch.setattr(fixval.subprocess, "run", execute)
        candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
        result = fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH)
        assert result.status == fixval.FixvalStatus.SKIPPED
        findings = result.findings
    assert len(findings) == 1 and findings[0].fingerprint == "mutation-flaky"
    assert "baseline failed" in findings[0].description
    assert "runner unavailable" not in findings[0].description
    assert "tests/test_mod.py::test_value" in findings[0].description
    assert ("after env retry" in findings[0].description) is retry
    assert_calls(calls, root, retry, False, 7 if caller == "mutation" else 120)
    assert (root / "src/pkg/mod.py").read_text() == "value = 1\n"
    assert not (root / "mutants").exists() and not (root / "setup.cfg").exists()
    assert not list(root.glob(".fixval*"))
