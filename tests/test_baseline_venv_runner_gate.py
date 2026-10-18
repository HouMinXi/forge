"""#244: never strip a venv that itself provides the baseline runner."""

import os
import subprocess

from code_forge.baseline_guard import _run_baseline_guard, _venv_has_runner


def _venv(tmp_path, with_pytest):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin" / "python3"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    if with_pytest:
        (site / "pytest").mkdir()
        (site / "pytest" / "__init__.py").write_text("")
    return {"VIRTUAL_ENV": str(venv), "PATH": f"{venv}/bin:/usr/bin"}


def _missing(*_a, **_k):
    return subprocess.CompletedProcess([], 1, "", "No module named pytest\n")


def _fnf(*_a, **_k):
    raise FileNotFoundError("helper")


def test_venv_with_runner_blocks_strip_on_missing_text(tmp_path):
    env = _venv(tmp_path, True)
    assert _venv_has_runner(["python3", "-m", "pytest"], env)
    status, _, _ = _run_baseline_guard(
        ["python3", "-m", "pytest"],
        env,
        str(tmp_path),
        allow_strip_retry=True,
        run_command=_missing,
    )
    assert status != "needs_strip_retry"


def test_venv_with_runner_blocks_strip_on_fnf(tmp_path):
    env = _venv(tmp_path, True)
    status, _, _ = _run_baseline_guard(
        ["python3", "-m", "pytest"],
        env,
        str(tmp_path),
        allow_strip_retry=True,
        run_command=_fnf,
    )
    assert status != "needs_strip_retry"


def test_venv_without_runner_still_strips(tmp_path):
    env = _venv(tmp_path, False)
    assert not _venv_has_runner(["python3", "-m", "pytest"], env)
    status, _, _ = _run_baseline_guard(
        ["python3", "-m", "pytest"],
        env,
        str(tmp_path),
        allow_strip_retry=True,
        run_command=_missing,
    )
    assert status == "needs_strip_retry"


def test_interpreter_outside_venv_is_not_vouched_for(tmp_path):
    env = _venv(tmp_path, True)
    assert not _venv_has_runner(["/usr/bin/python3", "-m", "pytest"], env)
    assert os.path.isdir(env["VIRTUAL_ENV"])
