"""#244: a strip-retry skip still reports the first attempt's real cause."""

import subprocess

import pytest

from code_forge import fixval, mutation
from code_forge.baseline_guard import _run_baseline_guard
from tests.test_baseline_failure_causes import CMD, PATCH, private_project  # noqa: F401

FIRST = "Exception ignored in: <function _OwnedTree.__del__>\nChildProcessError: [Errno 10] No child processes\nNo module named pytest\n"
RETRY = "/usr/bin/python3: No module named pytest\n"


def two_phase(calls):
    def run(argv, **kwargs):
        calls.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(argv, 1, "", FIRST if len(calls) == 1 else RETRY)

    return run


def assert_both_causes(description):
    assert "(after env retry)" in description
    assert "first attempt: run 1: returncode 1" in description
    assert "No child processes" in description
    assert RETRY.strip() in description


def test_guard_hands_back_first_cause_and_reports_it_on_retry():
    calls, cause = [], []
    env = {"VIRTUAL_ENV": "/nonexistent-venv", "PATH": "/nonexistent-venv/bin:/usr/bin"}
    run = two_phase(calls)
    status, findings, infra = _run_baseline_guard(
        CMD, env, "/", allow_strip_retry=True, run_command=run, retry_cause=cause
    )
    assert (status, findings, infra) == ("needs_strip_retry", [], [])
    assert len(cause) == 1 and "No child processes" in cause[0]
    status, findings, infra = _run_baseline_guard(
        CMD, {"PATH": "/usr/bin"}, "/", allow_strip_retry=False, run_command=run, first_attempt=cause[0]
    )
    assert status == "skip"
    assert_both_causes(findings[0].description)
    assert infra == [findings[0].description]


def test_no_first_attempt_note_without_retry():
    status, findings, _ = _run_baseline_guard(
        CMD, {"PATH": "/usr/bin"}, "/", allow_strip_retry=False, run_command=two_phase([])
    )
    assert status == "skip" and "first attempt" not in findings[0].description


@pytest.mark.parametrize("caller", ["mutation", "fixval"])
def test_callers_report_first_attempt_after_strip(private_project, monkeypatch, caller):  # noqa: F811
    root, calls = private_project, []
    monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
    monkeypatch.setenv("PATH", "/private-venv/bin:/usr/bin")
    if caller == "mutation":
        monkeypatch.setattr(mutation, "run_owned_command", two_phase(calls))
        findings, _ = mutation.run_mutation(
            ["src/pkg/mod.py"], CMD + ["tests/test_mod.py"], cwd=root, baseline_timeout=7
        )
    else:
        monkeypatch.setattr(fixval.subprocess, "run", two_phase(calls))
        candidate = fixval.FixvalCandidate(["tests/test_mod.py"], ["src/pkg/mod.py"])
        findings = fixval.run_fixval(candidate, CMD, root, "fix: preserve cause", PATCH).findings
    assert len(calls) == 2 and "VIRTUAL_ENV" in calls[0] and "VIRTUAL_ENV" not in calls[1]
    assert len(findings) == 1
    assert_both_causes(findings[0].description)
