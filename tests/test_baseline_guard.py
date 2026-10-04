"""The baseline guard decides whether the test command can start."""

import os
import subprocess
import sys

import pytest

from code_forge.baseline_guard import (
    _is_runner_missing,
    _run_baseline_guard,
    _strip_venv_from_env,
)
from code_forge.disposition import Disposition


def test_strip_removes_the_venv_bin_from_path():
    env = {
        "VIRTUAL_ENV": "/work/.venv",
        "PATH": "/work/.venv/bin:/usr/bin",
    }
    stripped = _strip_venv_from_env(env)
    assert "VIRTUAL_ENV" not in stripped
    assert stripped["PATH"] == "/usr/bin"


def test_a_missing_binary_is_a_runner_problem():
    assert _is_runner_missing(["pytest"], None, FileNotFoundError("pytest"))


def test_a_failed_test_is_not_a_missing_runner():
    assert _is_runner_missing(["pytest"], None, RuntimeError("1 failed")) is False


def test_three_passing_runs_report_passed(monkeypatch):
    """The guard needs three clean runs before it trusts the baseline."""
    calls = {"n": 0}

    def fake_run(*_a, **_k):
        calls["n"] += 1

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", fake_run)
    from code_forge.baseline_guard import _run_baseline_guard

    status, findings, errors = _run_baseline_guard(
        ["python3", "-m", "pytest"],
        {},
        "/repo",
        allow_strip_retry=False,
    )
    assert status == "passed"
    assert findings == [] and errors == []
    assert calls["n"] == 3


def test_a_failed_run_keeps_the_return_code_and_node(monkeypatch):
    """A non-zero baseline must say which node failed and with what code."""

    def fake_run(*_a, **_k):
        class R:
            returncode = 1
            stdout = "FAILED tests/test_sample.py::test_one - assert 0\n1 failed in 0.1s\n"
            stderr = ""

        return R()

    monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", fake_run)
    from code_forge.baseline_guard import _run_baseline_guard

    status, _findings, errors = _run_baseline_guard(
        ["python3", "-m", "pytest"],
        {},
        "/repo",
        allow_strip_retry=False,
    )
    assert status == "skip"
    assert errors
    text = errors[0]
    assert "returncode 1" in text
    assert "tests/test_sample.py::test_one" in text


def test_supplied_executor_is_used_at_the_physical_guard_site(monkeypatch):
    import subprocess
    from code_forge.baseline_guard import _run_baseline_guard

    calls = []

    def owned(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def forbidden(*_a, **_k):
        raise AssertionError("guard bypassed supplied owner")

    monkeypatch.setattr("code_forge.baseline_guard.subprocess.run", forbidden)
    status, findings, infra = _run_baseline_guard(
        ["fixture"],
        {"MEASURED": "yes"},
        "/source",
        allow_strip_retry=False,
        timeout=7,
        run_command=owned,
    )
    assert status == "passed" and not findings and not infra and len(calls) == 3
    assert all(
        call[1]["cwd"] == "/source" and call[1]["timeout"] == 7 and call[1]["env"] == {"MEASURED": "yes"}
        for call in calls
    )


@pytest.mark.parametrize("env,retry", [({}, True), ({}, False), ({"VIRTUAL_ENV": "/venv"}, False)])
def test_unavailable_runner_cause_survives_final_skip(env, retry):
    calls = []
    original = env.copy()

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 17, "", "/stderr-marker/python3: No module named pytest\n")

    status, findings, infra = _run_baseline_guard(
        ["python3", "-m", "pytest"], env, "/private", allow_strip_retry=retry, timeout=7, run_command=execute
    )
    assert status == "skip" and len(calls) == 1
    assert env == original
    finding = findings[0]
    assert (finding.id, finding.source, finding.disposition, finding.file, finding.line_range, finding.fingerprint) == (
        "MUTATION_SKIPPED", "MUTANT", Disposition.DISMISSED, "", [], "mutation-flaky"
    )
    for text in (finding.description, infra[0]):
        assert "baseline runner unavailable" in text
        assert "run 1" in text and "returncode 17" in text
        assert "stderr: /stderr-marker/python3: No module named pytest" in text
        assert "stdout:" not in text
        assert "flaky" not in text
        assert ("after env retry" in text) is (not retry)
    assert calls[0][1] == dict(
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7, check=False, cwd="/private"
    )


@pytest.mark.parametrize("venv", ["/venv", ""])
@pytest.mark.parametrize("missing_binary", [False, True])
def test_missing_runner_retry_keeps_empty_channels(venv, missing_binary):
    calls = []

    def execute(argv, **kwargs):
        calls.append(kwargs)
        if missing_binary:
            raise FileNotFoundError("absent-executable")
        return subprocess.CompletedProcess(argv, 1, "", "No module named pytest")

    assert _run_baseline_guard(
        ["python3", "-m", "pytest"], {"VIRTUAL_ENV": venv}, "/private", allow_strip_retry=True, run_command=execute
    ) == ("needs_strip_retry", [], [])
    assert len(calls) == 1


@pytest.mark.parametrize("retry", [False, True])
def test_missing_binary_cause_is_bounded(retry):
    def execute(*args, **kwargs):
        raise FileNotFoundError("absent-executable " + "z" * 250)

    status, findings, infra = _run_baseline_guard(["missing"], {}, "/private", allow_strip_retry=retry, run_command=execute)
    assert status == "skip" and findings[0].fingerprint == "mutation-flaky"
    for text in (findings[0].description, infra[0]):
        assert "run 1: runner could not start" in text
        assert "absent-executable" in text and "z" * 200 not in text
        assert "flaky" not in text


@pytest.mark.parametrize("output,error,expected", [
    (b"out\xff\n marker", b"err\xfe\t marker", "stderr: err\ufffd marker; stdout: out\ufffd marker"),
    ("out\n marker", "err\t marker", "stderr: err marker; stdout: out marker"),
    (None, None, ""),
    ("", " \n\t", ""),
])
def test_timeout_cause_accepts_captured_stream_types(output, error, expected):
    calls = []

    def execute(argv, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(argv, 7, output=output, stderr=error)

    status, findings, infra = _run_baseline_guard(
        ["python3", "-m", "pytest"], {"VIRTUAL_ENV": "/venv"}, "/private", allow_strip_retry=True, timeout=7,
        run_command=execute,
    )
    assert status == "skip" and len(calls) == 1
    assert findings[0].fingerprint == "mutation-baseline-timeout"
    for text in (findings[0].description, infra[0]):
        assert "run 1" in text and "timed out after 7s" in text
        assert expected in text
        if not expected:
            assert "stderr:" not in text and "stdout:" not in text


@pytest.mark.parametrize("first_pass", [False, True])
@pytest.mark.parametrize("error", ["assertion-marker", "No module named project_dependency"])
def test_failed_baseline_cause_keeps_run_nodes_and_output(first_pass, error):
    calls = []
    nodes = "\n".join(f"FAILED tests/test_sample.py::test_{i} - assertion" for i in range(7))

    def execute(argv, **kwargs):
        calls.append(kwargs)
        if first_pass and len(calls) == 1:
            return subprocess.CompletedProcess(argv, 0, "incidental", "incidental")
        return subprocess.CompletedProcess(argv, 19, nodes, error)

    status, findings, infra = _run_baseline_guard(
        ["python3", "-m", "pytest"], {"VIRTUAL_ENV": "/venv"}, "/private", allow_strip_retry=True, run_command=execute
    )
    assert status == "skip" and len(calls) == 1 + first_pass
    for text in (findings[0].description, infra[0]):
        assert f"run {1 + first_pass}: baseline failed" in text
        assert "returncode 19" in text and error in text
        assert "runner unavailable" not in text and "flaky" not in text
        assert "test_4" in text and "test_5" not in text and "test_6" not in text


def test_long_cause_normalizes_and_caps_each_stream():
    def execute(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 3, " \n" + "x" * 201 + "\t", "\n " + "y" * 201)

    _, findings, infra = _run_baseline_guard(["pytest"], {}, "/private", allow_strip_retry=True, run_command=execute)
    expected = "stderr: " + "y" * 200 + "; stdout: " + "x" * 200
    for text in (findings[0].description, infra[0]):
        assert expected in text
        assert "x" * 201 not in text and "y" * 201 not in text
        assert "\n" not in text and "\t" not in text


def test_empty_failure_cause_keeps_only_observed_run_and_code():
    def execute(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 23, None, None)

    status, findings, infra = _run_baseline_guard([], {}, "/private", allow_strip_retry=True, run_command=execute)
    assert status == "skip"
    for text in (findings[0].description, infra[0]):
        assert text == "run 1: baseline failed (returncode 23)"


@pytest.mark.parametrize("retry", [True, False], ids=["initial", "exhausted"])
@pytest.mark.parametrize("stderr,stdout", [
    ("No module named pytest_project_helper", ""),
    ("No module named pytest.extra", ""),
    ("No module named pytest stderr-marker", ""),
    ("ModuleNotFoundError: No module named 'pytest'\nTraceback marker", ""),
    ("No module named pytest", "FAILED tests/test_probe.py::test_runner - assertion\n"),
    ("", "E AssertionError: /usr/bin/python3: No module named pytest\nFAILED tests/test_probe.py::test_runner\n"),
])
def test_runner_text_in_test_failure_is_not_startup_diagnosis(retry, stderr, stdout):
    def execute(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout, stderr)

    status, findings, infra = _run_baseline_guard(
        ["python3", "-m", "pytest"], {}, "/private", allow_strip_retry=retry, run_command=execute
    )
    assert status == "skip"
    assert infra == [findings[0].description]
    assert "baseline failed" in findings[0].description
    assert "runner unavailable" not in findings[0].description
    assert findings[0].fingerprint == "mutation-flaky"
    assert ("after env retry" in findings[0].description) is (not retry)


@pytest.mark.parametrize("module", ["pytest", "pytest_project_helper"], ids=["exact", "prefix"])
def test_real_pytest_failure_containing_missing_runner_text(tmp_path, module):
    test = tmp_path / "test_probe.py"
    test.write_text(
        "import subprocess, sys\n"
        "def test_child_module():\n"
        f"    child = subprocess.run([sys.executable, '-I', '-S', '-m', {module!r}], capture_output=True, text=True)\n"
        "    assert child.returncode == 0, child.stderr\n"
    )
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("VIRTUAL_ENV", None)
    calls = []

    def execute(argv, **kwargs):
        result = subprocess.run(argv, **kwargs)
        calls.append(result)
        return result

    status, findings, infra = _run_baseline_guard(
        [sys.executable, "-B", "-m", "pytest", "-q", str(test)], env, str(tmp_path),
        allow_strip_retry=True, timeout=5, run_command=execute,
    )
    assert len(calls) == 1 and calls[0].returncode == 1
    assert f"No module named {module}" in calls[0].stdout
    assert "FAILED test_probe.py::test_child_module" in calls[0].stdout
    assert "1 failed" in calls[0].stdout
    assert status == "skip" and infra == [findings[0].description]
    assert "baseline failed" in findings[0].description
    assert "runner unavailable" not in findings[0].description


@pytest.mark.parametrize("module", ["pytest", "runner.with.dot"])
@pytest.mark.parametrize("prefix,quoted", [("", False), ("/usr/bin/python3: ", False), ("python3: ", True)])
def test_exact_startup_diagnostic_identifies_configured_module(module, prefix, quoted):
    missing = repr(module) if quoted else module

    def execute(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", f"{prefix}No module named {missing}\n")

    status, findings, infra = _run_baseline_guard(
        ["python3", "-W", "ignore", "-m", module], {}, "/private", allow_strip_retry=True, run_command=execute
    )
    assert status == "skip" and infra == [findings[0].description]
    assert "baseline runner unavailable" in findings[0].description


@pytest.mark.parametrize("stdout,stderr", [
    ("", "No module named pytest_project_helper"),
    ("FAILED tests/test_probe.py::test_runner\nNo module named pytest", ""),
])
def test_existing_retry_eligibility_is_preserved_for_runner_text(stdout, stderr):
    result = subprocess.CompletedProcess([], 1, stdout, stderr)
    assert _is_runner_missing(["python3", "-m", "pytest"], result, None)
    assert _run_baseline_guard(
        ["python3", "-m", "pytest"], {"VIRTUAL_ENV": ""}, "/private", allow_strip_retry=True,
        run_command=lambda *_a, **_k: result,
    ) == ("needs_strip_retry", [], [])
