# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL child runs never read or write the checkout's bytecode (#257).

CPython and pytest validate cached bytecode by whole-second source mtime plus
size. These tests make a revert or restore land on the fixed file's exact
mtime (same second) with the same size, deterministically, instead of racing
the clock.
"""

import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from code_forge import fixval
from code_forge._fixval_transaction import FixvalTransaction
from code_forge.fixval import FixvalCandidate, FixvalStatus, run_fixval, run_overfit_guard

PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist"]
CASES = {
    "module": (
        "src/calc.py",
        "value = 1\n",
        "value = 2\n",
        "import calc\n\ndef test_calc():\n    assert calc.value == 2\n",
    ),
    "conftest": (
        "tests/conftest.py",
        "import pytest\n\n@pytest.fixture\ndef expected():\n    return 1\n",
        "import pytest\n\n@pytest.fixture\ndef expected():\n    return 2\n",
        "def test_calc(expected):\n    assert expected == 2\n",
    ),
}


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Minxi Hou",
            "-c",
            "user.email=houminxi@gmail.com",
            *arguments,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout


def _launcher_env(monkeypatch):
    # as under the code-forge launcher: bytecode on in the environment
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)


def _checkout(root: Path, case: str) -> tuple[Path, str]:
    name, before, after, test = CASES[case]
    _git(root, "init", "-q")
    for directory in ("src", "tests"):
        (root / directory).mkdir()
    (root / name).write_text(before, encoding="utf-8")
    (root / "tests/test_calc.py").write_text("def test_placeholder():\n    pass\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "base")
    (root / name).write_text(after, encoding="utf-8")
    (root / "tests/test_calc.py").write_text(test, encoding="utf-8")
    _git(root, "add", "--all")
    return root / name, _git(root, "diff", "--cached", "--binary")


def _user_pytest(root: Path) -> subprocess.CompletedProcess:
    """The user's own run in the checkout, writing and reading __pycache__."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")
    }
    env["PYTHONPATH"] = str(root / "src")
    return subprocess.run(
        PYTEST + ["tests/test_calc.py"], cwd=root, env=env, capture_output=True, text=True, timeout=60
    )


def _restamp(roots, source: Path) -> int:
    """Make every cached .pyc of `source` (CPython's and pytest's rewritten
    one) claim the file's current mtime second and size, as if compiled in
    the same second: the stale case, without racing the clock."""
    stat = source.stat()
    header = struct.pack("<II", int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF)
    count = 0
    for root in roots:
        for pyc in Path(root).rglob(source.stem + ".*.pyc"):
            data = bytearray(pyc.read_bytes())
            if int.from_bytes(data[4:8], "little") == 0:  # timestamp-validated
                data[8:16] = header
                pyc.write_bytes(bytes(data))
                count += 1
    return count


def _same_second_revert(monkeypatch, root: Path, source: Path):
    """After each revert, cached bytecode of the fixed file looks current."""
    real = FixvalTransaction.reverse
    prefixes = []
    real_env = fixval._isolated_bytecode_env

    def recording(env, prefix):
        prefixes.append(prefix)
        return real_env(env, prefix)

    def reverse(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        _restamp([root, *prefixes], source)
        return result

    monkeypatch.setattr(fixval, "_isolated_bytecode_env", recording)
    monkeypatch.setattr(FixvalTransaction, "reverse", reverse)


def _fixval(root: Path, case: str, diff: str):
    name = CASES[case][0]
    candidate = FixvalCandidate(["tests/test_calc.py"], [name])
    return run_fixval(candidate, PYTEST, root, "fix calc", diff)


@pytest.mark.parametrize("case", sorted(CASES))
def test_reverted_run_does_not_execute_fixed_bytecode(tmp_path, monkeypatch, case):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, case)
    assert _user_pytest(tmp_path).returncode == 0  # the checkout now caches the fixed bytecode
    assert _restamp([tmp_path], source) >= 1
    _same_second_revert(monkeypatch, tmp_path, source)
    result = _fixval(tmp_path, case, diff)
    assert result.status is FixvalStatus.PASS, result.block_message


@pytest.mark.parametrize("case", sorted(CASES))
def test_restored_checkout_runs_the_fix_not_reverted_bytecode(tmp_path, monkeypatch, case):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, case)
    result = _fixval(tmp_path, case, diff)
    assert result.status is FixvalStatus.PASS, result.block_message
    _restamp([tmp_path], source)  # the restore lands in the reverted run's second
    assert list(tmp_path.rglob("*.pyc")) == []
    user = _user_pytest(tmp_path)
    assert user.returncode == 0, user.stdout


def test_every_child_env_is_isolated_and_the_prefix_is_removed(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    real = fixval._isolated_bytecode_env
    prefixes = []

    def recording(env, prefix):
        assert os.path.isdir(prefix) and not os.listdir(prefix)
        prefixes.append(prefix)
        isolated = real(env, prefix)
        assert isolated["PYTHONDONTWRITEBYTECODE"] == "1" and isolated["PYTHONPYCACHEPREFIX"] == prefix
        assert "PYTHONDONTWRITEBYTECODE" not in env  # a copy, the caller's env untouched
        return isolated

    monkeypatch.setattr(fixval, "_isolated_bytecode_env", recording)
    _, diff = _checkout(tmp_path, "module")
    assert _fixval(tmp_path, "module", diff).status is FixvalStatus.PASS
    assert len(prefixes) == 1 and not os.path.exists(prefixes[0])


def test_strip_retry_env_stays_isolated(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    from code_forge import baseline_guard

    seen = []
    real = baseline_guard._run_baseline_guard

    def guard(cmd, env, *args, **kwargs):
        seen.append((env.get("PYTHONDONTWRITEBYTECODE"), bool(env.get("PYTHONPYCACHEPREFIX"))))
        if kwargs.get("allow_strip_retry"):
            return "needs_strip_retry", [], []
        return real(cmd, env, *args, **kwargs)

    monkeypatch.setattr(fixval, "_run_baseline_guard", guard)
    monkeypatch.setenv("VIRTUAL_ENV", "/nonexistent-venv")
    _, diff = _checkout(tmp_path, "module")
    assert _fixval(tmp_path, "module", diff).status is FixvalStatus.PASS
    assert seen == [("1", True), ("1", True)]


def test_overfit_guard_child_is_isolated(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/mod.py").write_text("def f():\n    value = 1\n    return value\n", encoding="utf-8")
    seen = []

    def run(cmd, *, env, **kwargs):
        prefix = env["PYTHONPYCACHEPREFIX"]
        seen.append((env["PYTHONDONTWRITEBYTECODE"], prefix, os.path.isdir(prefix)))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fixval.subprocess, "run", run)
    assert (
        run_overfit_guard(FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"]), ["pytest"], tmp_path)
        == []
    )
    assert [(dont, alive) for dont, _prefix, alive in seen] == [("1", True)]
    assert not os.path.exists(seen[0][1])
