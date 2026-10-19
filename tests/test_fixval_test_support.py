# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL keeps test scaffolding at its fixed state (#254).

Reverting a new tests/conftest.py fixture or a tests/ helper made a hollow test
error for the wrong reason, which read as "test failed on revert" -> PASS.
"""

from pathlib import Path
import subprocess
import sys

import pytest

from code_forge.fixval import (
    FixvalCandidate,
    FixvalSkip,
    FixvalStatus,
    _filter_non_test_patch,
    classify_fixval_candidate,
    run_fixval,
)

PYTEST = [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist", "-q"]


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


def _repo(root: Path, fixed: dict[str, str]) -> str:
    """Commit src/calc.py (value = 1), then stage the fix plus `fixed` files."""
    _git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src/calc.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tests/test_calc.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "base")
    (root / "src/calc.py").write_text("value = 2\n", encoding="utf-8")
    for name, text in fixed.items():
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", "--all")
    return _git(root, "diff", "--cached", "--binary")


def _run(root: Path, diff: str, changed: list[str]):
    candidate = classify_fixval_candidate(changed)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == ["src/calc.py"]
    return run_fixval(candidate, PYTEST, root, "fix calc", diff)


HOLLOW_FIXTURE = {
    "tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef expected():\n    return 2\n",
    "tests/test_calc.py": "def test_calc(expected):\n    assert expected == 2\n",
}
HOLLOW_HELPER = {
    "tests/helpers.py": "EXPECTED = 2\n",
    "tests/test_calc.py": "from helpers import EXPECTED\n\ndef test_calc():\n    assert EXPECTED == 2\n",
}


@pytest.mark.parametrize(
    "fixed", [HOLLOW_FIXTURE, HOLLOW_HELPER], ids=["conftest-fixture", "tests-helper"]
)
def test_hollow_test_using_new_scaffolding_is_hollow_not_pass(tmp_path, fixed):
    diff = _repo(tmp_path, fixed)
    result = _run(tmp_path, diff, ["src/calc.py", *fixed])
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    # scaffolding and the fix are back at their fixed state afterwards
    assert (tmp_path / "src/calc.py").read_text(encoding="utf-8") == "value = 2\n"
    for name, text in fixed.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text


def test_real_test_using_new_fixture_still_passes(tmp_path):
    fixed = {
        "tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef expected():\n    return 2\n",
        "tests/test_calc.py": "import calc\n\ndef test_calc(expected):\n    assert calc.value == expected\n",
    }
    diff = _repo(tmp_path, fixed)
    result = _run(tmp_path, diff, ["src/calc.py", *fixed])
    assert result.status is FixvalStatus.PASS, result.block_message


def test_revert_patch_keeps_scaffolding():
    diff = "".join(
        "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-a = 1\n+a = 2\n".format(name)
        for name in (
            "src/calc.py",
            "tests/conftest.py",
            "pkg/tests/helpers.py",
            "conftest.py",
            "test/helpers.py",
        )
    )
    kept = _filter_non_test_patch(diff)
    assert "src/calc.py" in kept and "test/helpers.py" in kept
    assert "conftest.py" not in kept and "tests/helpers.py" not in kept


@pytest.mark.parametrize(
    "root",
    ["", "/home/u/tests/checkout/", "/srv/tests/conftest.py-dir/"],
    ids=["relative", "absolute-under-tests", "absolute-odd-ancestor"],
)
def test_classification_excludes_scaffolding_relative_to_checkout(root):
    files = [
        "src/calc.py",
        "tests/conftest.py",
        "pkg/tests/helpers.py",
        "test/helpers.py",
        "tests/test_calc.py",
    ]
    candidate = classify_fixval_candidate([root + f for f in files])
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.test_files == [root + "tests/test_calc.py"]
    # singular test/ stays production (#135)
    assert candidate.non_test_files == [root + "src/calc.py", root + "test/helpers.py"]


def test_scaffolding_only_diff_is_not_a_candidate():
    skip = classify_fixval_candidate(["tests/conftest.py", "tests/helpers.py", "tests/test_calc.py"])
    assert skip == FixvalSkip(reason="no non-test file in diff")
