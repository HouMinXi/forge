"""#246: an env-strip retry is recorded even when the retry passes."""

import subprocess

import pytest

from code_forge import fixval, mutation
from code_forge._fixval_transaction import TransactionError
from tests.test_baseline_failure_causes import CMD, PATCH, private_project  # noqa: F401

FIRST = "/caller-marker/python3: No module named pytest\n"


def retry_passes(calls):
    def run(argv, **kwargs):
        calls.append(dict(kwargs["env"]))
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 11, "", FIRST)
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    return run


def in_private_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
    monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")


def run_mutation_stopping_at_resolver(root, monkeypatch, calls):
    monkeypatch.setattr(mutation, "run_owned_command", retry_passes(calls))
    resolved = []
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *a, **k: resolved.append(k["env"]))
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7, _evidence=evidence
    )
    return findings, infra, evidence, resolved


def test_mutation_records_a_passing_strip_retry(private_project, monkeypatch):  # noqa: F811
    in_private_venv(monkeypatch)
    calls = []
    findings, infra, evidence, resolved = run_mutation_stopping_at_resolver(private_project, monkeypatch, calls)
    assert "VIRTUAL_ENV" in calls[0] and all("VIRTUAL_ENV" not in env for env in calls[1:])
    assert resolved and "VIRTUAL_ENV" not in resolved[0]  # the retry passed; measurement continued stripped
    record = evidence["baseline_env_retry"]
    assert record["stripped_virtual_env"] == "/private-venv"
    assert record["first_attempt"].startswith("run 1: returncode 11")
    assert "No module named pytest" in record["first_attempt"]
    outcome = mutation._mutation_outcome(findings, infra, evidence)
    assert outcome["baseline_env_retry"] == record


def test_mutation_without_strip_records_nothing(private_project, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(mutation, "run_owned_command", lambda argv, **k: (
        calls.append(k["env"]) or subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")))
    monkeypatch.setattr(mutation, "_resolve_mutmut_invocation", lambda *a, **k: None)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=private_project, baseline_timeout=7, _evidence=evidence
    )
    assert len(calls) == 3
    assert "baseline_env_retry" not in evidence
    assert "baseline_env_retry" not in mutation._mutation_outcome(findings, infra, evidence)


@pytest.mark.parametrize("retry", [False, True], ids=["no-strip", "strip"])
def test_fixval_advises_a_passing_strip_retry(private_project, monkeypatch, retry):  # noqa: F811
    if retry:
        in_private_venv(monkeypatch)
    calls = []
    run = retry_passes(calls)
    monkeypatch.setattr(fixval.subprocess, "run", run if retry else (
        lambda argv, **k: calls.append(k["env"]) or subprocess.CompletedProcess(argv, 0, "1 passed\n", "")))
    allocate = fixval.tempfile.mkstemp
    monkeypatch.setattr(fixval.tempfile, "mkstemp", lambda *a, **k: allocate(*a, **k, dir=private_project))

    def refuse(*_a, **_k):
        raise TransactionError("stop after baseline")

    monkeypatch.setattr(fixval, "FixvalTransaction", refuse)
    candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
    result = fixval.run_fixval(candidate, CMD, private_project, "fix: record strip", PATCH)
    assert result.status == fixval.FixvalStatus.BLOCK  # the baseline passed; the stub stops the transaction
    notes = [a for a in result.advisories if a.id == "FIXVAL_BASELINE_ENV_RETRY"]
    if not retry:
        assert notes == []
        return
    assert len(notes) == 1
    assert "VIRTUAL_ENV /private-venv" in notes[0].description
    assert "first attempt: run 1: returncode 11" in notes[0].description
    assert "No module named pytest" in notes[0].description
