# SPDX-License-Identifier: Apache-2.0
"""Subprocess fixtures must not write bytecode into the reviewed checkout (#37)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
VAR = "PYTHONDONTWRITEBYTECODE"

# Modules whose launches build their own sanitized environment. The variable
# never reaches those children, so only an explicit `-B` keeps them from
# writing src/**/__pycache__.
SANITIZED_LAUNCHERS = (
    "tests/test_cli_empty_diff.py",
    "tests/test_trust_empty_backends.py",
    "tests/test_pytest_report_plugin.py",
)


def _caches(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("__pycache__"))


def _env_without_bytecode_flag(**extra: str) -> dict[str, str]:
    """The runner may export the variable (CI does); the checks must not depend on it."""
    env = {k: v for k, v in os.environ.items() if k != VAR}
    env.update(extra)
    return env


def _copy_checkout(dest: Path) -> Path:
    """Copy what a test run needs, minus any caches already on disk."""
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("src", "tests"):
        shutil.copytree(ROOT / name, dest / name, ignore=skip)
    for name in ("pyproject.toml", "conftest.py"):
        shutil.copy2(ROOT / name, dest / name)
    return dest


def test_conftest_exports_no_bytecode_to_children():
    """Import conftest in a child that starts without the variable and read it back."""
    result = subprocess.run(
        [sys.executable, "-B", "-c", f"import os, tests.conftest; print(os.environ.get({VAR!r}))"],
        cwd=ROOT,
        env=_env_without_bytecode_flag(PYTHONPATH=os.pathsep.join([str(ROOT), str(SRC)])),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_inheriting_child_leaves_no_cache_in_checkout_copy(tmp_path):
    copy = tmp_path / "src"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert _caches(copy) == []
    subprocess.run(
        [sys.executable, "-c", "import code_forge.cli"],
        env=_env_without_bytecode_flag(PYTHONPATH=str(copy), **{VAR: "1"}),
        cwd=tmp_path,
        check=True,
    )
    assert _caches(copy) == []


def test_sanitized_launchers_leave_no_cache_in_checkout_copy(tmp_path):
    """Run the sanitized launchers against a copy and look for caches under its src.

    Each launcher resolves ``src`` relative to its own file, so its children
    import from the copy. Dropping ``-B`` from any of them makes the copy grow
    a ``__pycache__``. The inner run is told not to write bytecode itself and
    runs without the variable, so only the launchers' own flags are measured.
    """
    copy = _copy_checkout(tmp_path / "checkout")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            *SANITIZED_LAUNCHERS,
        ],
        cwd=copy,
        env=_env_without_bytecode_flag(),
        capture_output=True,
        text=True,
        timeout=900,
    )
    # 0 = all passed, 1 = some tests failed (host-dependent ones may). 2-5 mean
    # the inner run never exercised the launchers.
    assert result.returncode in (0, 1), result.stdout[-2000:] + result.stderr[-2000:]
    assert " passed" in result.stdout, result.stdout[-2000:]
    assert _caches(copy / "src") == []
