# SPDX-License-Identifier: Apache-2.0
"""Exercise the real pytest bootstrap against an unrelated Git repository."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("injection", ["repository", "config"])
def test_inherited_git_environment_cannot_modify_other_repo(tmp_path, injection):
    source = Path(__file__).resolve().parent
    suite = tmp_path / "suite"
    victim = tmp_path / "unrelated"
    suite.mkdir()
    victim.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env["PYTHONPATH"] = str(source.parent / "src")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for root in (suite, victim):
        subprocess.run(["git", "init", "--quiet", str(root)],
                       env=env, check=True, capture_output=True, timeout=10)
    hook = victim / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    before = {path.relative_to(victim): path.read_bytes()
              for path in (victim / ".git").rglob("*") if path.is_file()}
    tests = suite / "tests"
    tests.mkdir()
    shutil.copy2(source / "conftest.py", tests / "conftest.py")
    shutil.copy2(source / "test_install_hooks.py", tests / "test_install_hooks.py")
    if injection == "repository":
        env.update(GIT_DIR=str(victim / ".git"), GIT_WORK_TREE=str(victim),
                   GIT_COMMON_DIR=str(victim / ".git"),
                   GIT_INDEX_FILE=str(victim / ".git" / "index"))
    else:
        env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.filemode",
                   GIT_CONFIG_VALUE_0="false", GIT_CONFIG_KEY_1="core.hooksPath",
                   GIT_CONFIG_VALUE_1=str(victim / ".git" / "hooks"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
         "tests/test_install_hooks.py::TestNonForgeHookWithBackup::test_non_forge_hook_with_backup_blocks",
         "tests/test_install_hooks.py::TestHooksPathAbort::test_hooks_path_set_aborts",
         "tests/test_install_hooks.py::TestHooksPathAbort::test_hooks_path_unset_succeeds"],
        cwd=suite, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    after = {path.relative_to(victim): path.read_bytes()
             for path in (victim / ".git").rglob("*") if path.is_file()}
    assert after == before, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout, result.stdout
