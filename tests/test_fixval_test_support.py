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
    FixvalStatus,
    _filter_non_test_patch,
    _is_test_support,
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


def _run(root: Path, diff: str, changed: list[str], reverted: list[str]):
    candidate = classify_fixval_candidate(changed)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == reverted
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
    result = _run(tmp_path, diff, ["src/calc.py", *fixed], ["src/calc.py"])
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
    result = _run(tmp_path, diff, ["src/calc.py", *fixed], ["src/calc.py"])
    assert result.status is FixvalStatus.PASS, result.block_message


def _support_only_repo(root: Path, test_body: str) -> str:
    """The fix is the conftest itself (a guard, as in #225)."""
    _git(root, "init", "-q")
    (root / "tests").mkdir()
    (root / "tests/conftest.py").write_text("GUARD = False\n", encoding="utf-8")
    (root / "tests/test_guard.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "base")
    (root / "tests/conftest.py").write_text("GUARD = True\n", encoding="utf-8")
    (root / "tests/test_guard.py").write_text(test_body, encoding="utf-8")
    _git(root, "add", "--all")
    return _git(root, "diff", "--cached", "--binary")


@pytest.mark.parametrize(
    ("body", "status", "ids"),
    [
        ("from conftest import GUARD\n\ndef test_guard():\n    assert GUARD\n", FixvalStatus.PASS, []),
        ("def test_guard():\n    assert True\n", FixvalStatus.BLOCK, ["FIXVAL_HOLLOW"]),
    ],
    ids=["guarded", "hollow"],
)
def test_scaffolding_only_fix_is_still_reverted(tmp_path, body, status, ids):
    diff = _support_only_repo(tmp_path, body)
    changed = ["tests/conftest.py", "tests/test_guard.py"]
    result = _run(tmp_path, diff, changed, ["tests/conftest.py"])
    assert result.status is status, result.block_message
    assert [finding.id for finding in result.findings] == ids
    assert (tmp_path / "tests/conftest.py").read_text(encoding="utf-8") == "GUARD = True\n"


def _diff(*names: str) -> str:
    return "".join(
        "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-a = 1\n+a = 2\n".format(name)
        for name in names
    )


def test_revert_patch_keeps_scaffolding_beside_production():
    kept = _filter_non_test_patch(
        _diff(
            "src/calc.py",
            "tests/conftest.py",
            "pkg/tests/helpers.py",
            "conftest.py",
            "test/helpers.py",
            "tests/test_calc.py",
        )
    )
    assert "src/calc.py" in kept and "test/helpers.py" in kept
    assert "conftest.py" not in kept and "tests/helpers.py" not in kept and "test_calc" not in kept


def test_revert_patch_reverts_scaffolding_when_it_is_the_fix():
    kept = _filter_non_test_patch(_diff("tests/conftest.py", "tests/helpers.py", "tests/test_calc.py"))
    assert kept == _diff("tests/conftest.py", "tests/helpers.py")
    # a test block outside tests/ is not a production change either
    assert _filter_non_test_patch(_diff("tests/conftest.py", "test_calc.py")) == _diff(
        "tests/conftest.py"
    )


FILES = [
    "src/calc.py",
    "tests/conftest.py",
    "pkg/tests/helpers.py",
    "test/helpers.py",
    "tests/test_calc.py",
]


def test_classification_is_relative_to_the_checkout_under_a_tests_dir(tmp_path):
    checkout = tmp_path / "tests" / "checkout"
    (checkout / ".git").mkdir(parents=True)
    paths = [str(checkout / f) for f in FILES]
    # a lone production file: no common prefix to lean on
    candidate = classify_fixval_candidate([paths[0], paths[-1]])
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == [paths[0]]
    candidate = classify_fixval_candidate(paths)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.test_files == [paths[-1]]
    # singular test/ stays production (#135)
    assert candidate.non_test_files == [paths[0], paths[3]]


def test_worktree_git_file_marks_the_checkout(tmp_path):
    checkout = tmp_path / "tests" / "wt"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    paths = [str(checkout / f) for f in ("src/calc.py", "tests/helpers.py", "tests/test_calc.py")]
    candidate = classify_fixval_candidate(paths)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == [paths[0]]


def test_absolute_path_outside_any_checkout_keeps_main_classification(tmp_path):
    paths = [str(tmp_path / f) for f in ("src/calc.py", "tests/helpers.py", "tests/test_calc.py")]
    candidate = classify_fixval_candidate(paths)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == paths[:2]
    # beside a known checkout, an unplaceable path is still production
    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    inside = [str(checkout / "src/calc.py"), str(checkout / "tests/test_calc.py")]
    candidate = classify_fixval_candidate([inside[0], paths[1], inside[1]])
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == [inside[0], paths[1]]


def test_relative_classification():
    candidate = classify_fixval_candidate(FILES)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == ["src/calc.py", "test/helpers.py"]


def test_scaffolding_only_classification_reverts_scaffolding():
    candidate = classify_fixval_candidate(
        ["tests/conftest.py", "tests/helpers.py", "tests/test_calc.py"]
    )
    assert candidate == FixvalCandidate(
        test_files=["tests/test_calc.py"], non_test_files=["tests/conftest.py", "tests/helpers.py"]
    )


@pytest.mark.parametrize(
    ("path", "support"),
    [
        ("tests\\conftest.py", True),
        ("pkg\\tests\\helpers.py", True),
        ("pkg\\conftest.py", True),
        ("test\\helpers.py", False),
        ("src\\tests.py", False),
        ("src\\calc.py", False),
        ("pkg\\tests", False),
    ],
)
def test_windows_separators_are_normalised(path, support):
    assert _is_test_support(path) is support
    candidate = classify_fixval_candidate(["src/calc.py", path, "tests/test_calc.py"])
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == (["src/calc.py"] if support else ["src/calc.py", path])
