# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL never runs stale bytecode of the files it reverts or restores (#257).

CPython and pytest validate cached bytecode by whole-second source mtime plus
size. These tests make a revert or restore land on the fixed file's exact
mtime (same second) with the same size, deterministically, instead of racing
the clock.
"""

import os
from pathlib import Path
import py_compile
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
    """The user's own run in the checkout, writing and reading bytecode
    (under PYTHONPYCACHEPREFIX when the test set one)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
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


def _same_second_revert(monkeypatch, roots, source: Path):
    """After each revert, cached bytecode of the fixed file looks current."""
    real = FixvalTransaction.reverse

    def reverse(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        _restamp(roots, source)
        return result

    monkeypatch.setattr(FixvalTransaction, "reverse", reverse)


def _fixval(root: Path, case: str, diff: str, cmd=PYTEST):
    name = CASES[case][0]
    candidate = FixvalCandidate(["tests/test_calc.py"], [name])
    return run_fixval(candidate, cmd, root, "fix calc", diff)


def _pycs(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.pyc"))


@pytest.mark.parametrize("case", sorted(CASES))
def test_reverted_run_does_not_execute_fixed_bytecode(tmp_path, monkeypatch, case):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, case)
    assert _user_pytest(tmp_path).returncode == 0  # the checkout now caches the fixed bytecode
    assert _restamp([tmp_path], source) >= 1
    _same_second_revert(monkeypatch, [tmp_path], source)
    result = _fixval(tmp_path, case, diff)
    assert result.status is FixvalStatus.PASS, result.block_message


def test_reverted_run_ignores_fixed_bytecode_under_an_inherited_pycache_prefix(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    prefix = tmp_path / "prefix"
    checkout = tmp_path / "repo"
    checkout.mkdir()
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(prefix))
    source, diff = _checkout(checkout, "module")
    assert _user_pytest(checkout).returncode == 0
    assert _restamp([prefix], source) >= 1 and _pycs(checkout) == []
    _same_second_revert(monkeypatch, [prefix], source)
    result = _fixval(checkout, "module", diff)
    assert result.status is FixvalStatus.PASS, result.block_message


# A runner that resets the environment still writes the reverted bytecode:
# the cache drop after the restore is what keeps the checkout correct then.
RESETTING = ["env", "-u", "PYTHONDONTWRITEBYTECODE", *PYTEST]


@pytest.mark.parametrize("cmd", [PYTEST, RESETTING], ids=["pytest", "env-resetting-runner"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_restored_checkout_runs_the_fix_not_reverted_bytecode(tmp_path, monkeypatch, case, cmd):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, case)
    result = _fixval(tmp_path, case, diff, cmd)
    assert result.status is FixvalStatus.PASS, result.block_message
    _restamp([tmp_path], source)  # the restore lands in the reverted run's second
    assert not [pyc for pyc in _pycs(tmp_path) if Path(pyc).name.startswith(source.stem + ".")]
    if cmd is PYTEST:
        assert _pycs(tmp_path) == []  # FIXVAL's own children write no bytecode
    user = _user_pytest(tmp_path)
    assert user.returncode == 0, user.stdout


def test_untouched_caches_stay_warm(tmp_path, monkeypatch):
    # timing guard: only the touched files' caches go; no private
    # PYTHONPYCACHEPREFIX, which made every child recompile the stdlib and
    # site-packages (a trivial run went 0.22s -> 0.84s)
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, "module")
    (tmp_path / "src/other.py").write_text("x = 1\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "src"))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    subprocess.run([sys.executable, "-c", "import calc, other"], cwd=tmp_path, env=env, check=True)
    kept = [pyc for pyc in _pycs(tmp_path) if "other." in pyc]
    stamps = {pyc: (tmp_path / pyc).stat().st_mtime_ns for pyc in kept}
    assert kept and any("calc." in pyc for pyc in _pycs(tmp_path))
    envs = []
    real = fixval._isolated_bytecode_env

    def recording(env):
        isolated = real(env)
        envs.append(isolated)
        return isolated

    monkeypatch.setattr(fixval, "_isolated_bytecode_env", recording)
    assert _fixval(tmp_path, "module", diff).status is FixvalStatus.PASS
    assert envs and all(
        e["PYTHONDONTWRITEBYTECODE"] == "1" and "PYTHONPYCACHEPREFIX" not in e for e in envs
    )
    assert "PYTHONDONTWRITEBYTECODE" not in os.environ  # the caller's env untouched
    assert [pyc for pyc in _pycs(tmp_path) if "calc." in pyc] == []
    assert {pyc: (tmp_path / pyc).stat().st_mtime_ns for pyc in kept} == stamps


def test_every_reverted_child_env_writes_no_bytecode(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    from code_forge import baseline_guard

    seen = []
    real_guard = baseline_guard._run_baseline_guard
    real_run = fixval._test_reverted_candidate

    def guard(cmd, env, *args, **kwargs):
        seen.append(("guard", env.get("PYTHONDONTWRITEBYTECODE")))
        if kwargs.get("allow_strip_retry"):
            return "needs_strip_retry", [], []
        return real_guard(cmd, env, *args, **kwargs)

    def reverted(candidate, cmd, env, *args, **kwargs):
        seen.append(("reverted", env.get("PYTHONDONTWRITEBYTECODE")))
        return real_run(candidate, cmd, env, *args, **kwargs)

    monkeypatch.setattr(fixval, "_run_baseline_guard", guard)
    monkeypatch.setattr(fixval, "_test_reverted_candidate", reverted)
    monkeypatch.setenv("VIRTUAL_ENV", "/nonexistent-venv")
    _, diff = _checkout(tmp_path, "module")
    assert _fixval(tmp_path, "module", diff).status is FixvalStatus.PASS
    assert seen == [("guard", "1"), ("guard", "1"), ("reverted", "1")]


@pytest.mark.parametrize("when", ["before-run", "after-restore"])
def test_cache_drop_failure_is_a_transaction_block(tmp_path, monkeypatch, when):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, "module")
    calls = []
    real = fixval._drop_cached_bytecode

    def drop(root, paths, env, cmd=()):
        calls.append(list(paths))
        if (len(calls) == 1) == (when == "before-run"):
            raise PermissionError("pycache is read-only")
        return real(root, paths, env, cmd)

    monkeypatch.setattr(fixval, "_drop_cached_bytecode", drop)
    result = _fixval(tmp_path, "module", diff)
    assert calls == [["src/calc.py"], ["src/calc.py"]]
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_TRANSACTION"]
    assert "pycache is read-only" in result.block_message
    assert source.read_text(encoding="utf-8") == CASES["module"][2]


def test_drop_cached_bytecode_removes_exactly_the_touched_caches(tmp_path):
    from code_forge.fixval import _drop_cached_bytecode

    prefix = tmp_path / "prefix"
    names = [
        "pkg/__pycache__/mod.cpython-312.pyc",
        "pkg/__pycache__/mod.cpython-312.opt-1.pyc",
        "tests/__pycache__/conftest.cpython-312-pytest-9.1.1.pyc",
        "pkg/__pycache__/[x].cpython-312.pyc",
        "pkg/__pycache__/mod2.cpython-312.pyc",
        "pkg/__pycache__/other.cpython-312.pyc",
        "pkg/sub/__pycache__/mod.cpython-312.pyc",
        "pkg/__pycache__/notes.cpython-312.pyc",
    ]
    mirror = str(tmp_path / "pkg")[1:]
    names_in_prefix = [
        os.path.join(mirror, "mod.cpython-312.pyc"),
        os.path.join(mirror, "other.cpython-312.pyc"),
    ]
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"")
    for name in names_in_prefix:
        (prefix / name).parent.mkdir(parents=True, exist_ok=True)
        (prefix / name).write_bytes(b"")
    touched = ["pkg/mod.py", "tests/conftest.py", "pkg/[x].py", "pkg/notes.md", "pkg/missing.py"]
    _drop_cached_bytecode(str(tmp_path), touched, {"PYTHONPYCACHEPREFIX": str(prefix)})
    left = sorted(
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.pyc") if prefix not in p.parents
    )
    assert left == sorted(names[4:])
    assert sorted(str(p.relative_to(prefix)) for p in prefix.rglob("*.pyc")) == [names_in_prefix[1]]
    # a relative prefix is the children's: resolved against the checkout
    (prefix / names_in_prefix[0]).write_bytes(b"")
    _drop_cached_bytecode(str(tmp_path), ["pkg/mod.py"], {"PYTHONPYCACHEPREFIX": "prefix"})
    assert not (prefix / names_in_prefix[0]).exists()


def test_overfit_guard_drops_its_file_cache_around_the_run(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    (tmp_path / "src/__pycache__").mkdir(parents=True)
    (tmp_path / "src/mod.py").write_text("def f():\n    value = 1\n    return value\n", encoding="utf-8")
    stale = tmp_path / "src/__pycache__/mod.cpython-312.pyc"
    seen = []

    def run(cmd, *, env, **kwargs):
        seen.append((env["PYTHONDONTWRITEBYTECODE"], "PYTHONPYCACHEPREFIX" in env, stale.exists()))
        stale.write_bytes(b"written by a resetting runner")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stale.write_bytes(b"stale")
    monkeypatch.setattr(fixval.subprocess, "run", run)
    assert (
        run_overfit_guard(FixvalCandidate(["tests/test_mod.py"], ["src/mod.py"]), ["pytest"], tmp_path)
        == []
    )
    assert seen == [("1", False, False)]
    assert not stale.exists()


# --- #259: the remaining stale-bytecode sources ---------------------------


@pytest.mark.parametrize("form", ["separate-absolute", "joined-relative"])
def test_reverted_run_ignores_fixed_bytecode_under_a_command_line_pycache_prefix(
    tmp_path, monkeypatch, form
):
    _launcher_env(monkeypatch)
    checkout = tmp_path / "repo"
    checkout.mkdir()
    prefix = checkout / ".pyc-prefix"
    option = (
        ["-X", "pycache_prefix=%s" % prefix]
        if form == "separate-absolute"
        else ["-Xpycache_prefix=.pyc-prefix"]
    )
    cmd = [sys.executable, *option, *PYTEST[1:]]
    source, diff = _checkout(checkout, "module")
    env = dict(os.environ, PYTHONPATH=str(checkout / "src"))
    user = subprocess.run(
        cmd + ["tests/test_calc.py"], cwd=checkout, env=env, capture_output=True, timeout=60
    )
    assert user.returncode == 0
    assert _restamp([prefix], source) >= 1
    _same_second_revert(monkeypatch, [prefix], source)
    result = _fixval(checkout, "module", diff, cmd)
    assert result.status is FixvalStatus.PASS, result.block_message
    assert not list(prefix.rglob("calc.*.pyc"))


def test_pycache_prefixes_reads_the_env_and_every_x_option():
    from code_forge.fixval import _pycache_prefixes

    cmd = [
        "python",
        "-X",
        "frozen_modules=off",
        "-X",
        "pycache_prefix=/a",
        "-Xpycache_prefix=b",
        "-Xutf8",
        "-m",
        "pytest",
        "-X",
    ]
    assert _pycache_prefixes({"PYTHONPYCACHEPREFIX": "/env"}, cmd) == ["/env", "/a", "b"]
    assert _pycache_prefixes({}, ["pytest", "-X", "pycache_prefix="]) == []


def _added_module_checkout(root: Path) -> tuple[str, Path]:
    """The fix adds src/newmod.py; a sourceless src/newmod.pyc of the fix
    sits beside it (a legacy build artifact)."""
    _git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "tests/test_calc.py").write_text("def test_placeholder():\n    pass\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "base")
    (root / "src/newmod.py").write_text("value = 2\n", encoding="utf-8")
    (root / "tests/test_calc.py").write_text(
        "import newmod\n\ndef test_calc():\n    assert newmod.value == 2\n", encoding="utf-8"
    )
    _git(root, "add", "--all")
    legacy = root / "src/newmod.pyc"
    py_compile.compile(str(root / "src/newmod.py"), cfile=str(legacy), doraise=True)
    return _git(root, "diff", "--cached", "--binary"), legacy


def test_sourceless_bytecode_of_an_added_module_blocks_instead_of_hollow(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    diff, legacy = _added_module_checkout(tmp_path)
    stamp = legacy.read_bytes()
    candidate = FixvalCandidate(["tests/test_calc.py"], ["src/newmod.py"])
    result = run_fixval(candidate, PYTEST, tmp_path, "add newmod", diff)
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_TRANSACTION"]
    assert "sourceless bytecode src/newmod.pyc" in result.block_message
    assert (tmp_path / "src/newmod.py").read_text(encoding="utf-8") == "value = 2\n"
    assert legacy.read_bytes() == stamp  # not FIXVAL's to delete


def test_sourceless_bytecode_beside_a_kept_source_is_harmless(tmp_path, monkeypatch):
    # the source wins over a sourceless .pyc, so a modified module measures
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, "module")
    py_compile.compile(str(source), cfile=str(source.with_suffix(".pyc")), doraise=True)
    assert _fixval(tmp_path, "module", diff).status is FixvalStatus.PASS


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize("mode", [0o311, 0o555], ids=["unlistable", "read-only"])
def test_a_pycache_that_cannot_be_dropped_is_a_transaction_block(tmp_path, monkeypatch, mode):
    _launcher_env(monkeypatch)
    source, diff = _checkout(tmp_path, "module")
    assert _user_pytest(tmp_path).returncode == 0
    _restamp([tmp_path], source)
    _same_second_revert(monkeypatch, [tmp_path], source)
    cache = tmp_path / "src/__pycache__"
    cache.chmod(mode)
    try:
        result = _fixval(tmp_path, "module", diff)
    finally:
        cache.chmod(0o755)
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_TRANSACTION"]
    assert "Permission denied" in result.block_message
    assert source.read_text(encoding="utf-8") == CASES["module"][2]


def test_drop_tolerates_only_an_already_missing_cache(tmp_path, monkeypatch):
    from code_forge.fixval import _drop_cached_bytecode

    cache = tmp_path / "pkg/__pycache__"
    cache.mkdir(parents=True)
    (cache / "mod.cpython-312.pyc").write_bytes(b"")
    (tmp_path / "other/__pycache__").parent.mkdir()
    (tmp_path / "other/__pycache__").write_bytes(b"")  # not a directory: nothing cached
    _drop_cached_bytecode(str(tmp_path), ["other/mod.py", "gone/mod.py"], {})
    real = os.unlink
    for error in (FileNotFoundError, PermissionError, OSError):

        def unlink(path, *args, error=error, **kwargs):
            if str(path).endswith(".pyc"):
                raise error(5, "injected", str(path))
            return real(path, *args, **kwargs)

        monkeypatch.setattr(fixval.os, "unlink", unlink)
        if error is FileNotFoundError:  # removed concurrently: fine
            _drop_cached_bytecode(str(tmp_path), ["pkg/mod.py"], {})
        else:
            with pytest.raises(error):
                _drop_cached_bytecode(str(tmp_path), ["pkg/mod.py"], {})


def test_restore_drops_reverted_bytecode_under_a_command_line_pycache_prefix(tmp_path, monkeypatch):
    _launcher_env(monkeypatch)
    prefix = tmp_path / "prefix"
    checkout = tmp_path / "repo"
    checkout.mkdir()
    option = ["-X", "pycache_prefix=%s" % prefix]
    source, diff = _checkout(checkout, "module")
    result = _fixval(checkout, "module", diff, [*RESETTING[:3], sys.executable, *option, *PYTEST[1:]])
    assert result.status is FixvalStatus.PASS, result.block_message
    assert not list(prefix.rglob("calc.*.pyc"))  # the reverted run's cache went
    env = dict(os.environ, PYTHONPATH=str(checkout / "src"))
    user = subprocess.run(
        [sys.executable, *option, *PYTEST[1:], "tests/test_calc.py"],
        cwd=checkout,
        env=env,
        capture_output=True,
    )
    assert user.returncode == 0


def test_sourceless_check_and_drop_match_exact_python_names(tmp_path):
    from code_forge.fixval import _drop_cached_bytecode, _refuse_sourceless_bytecode

    (tmp_path / "src").mkdir()
    (tmp_path / "src/x.pyc").write_bytes(b"")
    (tmp_path / "src/x..pyc").write_bytes(b"")
    _refuse_sourceless_bytecode(str(tmp_path), ["src/xyz", "src/x.txt", "src/x.py.orig"])
    cache = tmp_path / "src/__pycache__"
    cache.mkdir()
    (cache / "MOD.cpython-312.pyc").write_bytes(b"")
    _drop_cached_bytecode(str(tmp_path), ["src/mod.py"], {})
    assert (cache / "MOD.cpython-312.pyc").exists()
