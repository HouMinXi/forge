"""The baseline guard decides whether the test command can start."""

from code_forge.baseline_guard import (
    _is_runner_missing,
    _strip_venv_from_env,
)


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
