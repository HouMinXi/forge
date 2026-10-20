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


def _repo(root: Path, fixed: dict[str, str], base: dict[str, str] | None = None) -> str:
    """Commit src/calc.py (value = 1) plus `base`, then stage the fix plus `fixed` files."""
    _git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src/calc.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tests/test_calc.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )
    for name, text in (base or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
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
    # an unplaceable path is not a known source change: support is reverted too
    support = [str(checkout / "tests/conftest.py"), inside[1]]
    candidate = classify_fixval_candidate([paths[0], *support])
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == [paths[0], support[0]]


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


CONFTEST_FIX = ('import os\nos.environ["FORGE_FLAG"] = "1"\n', "import os\n")
GUARDED_TEST = 'import os\n\ndef test_env():\n    assert os.environ.get("FORGE_FLAG") == "1"\n'
HOLLOW_TEST = "def test_env():\n    assert True\n"


def _support_beside_repo(root: Path, other: str, test_body: str) -> str:
    """A conftest fix that also touches one other non-test file (#255 review, /tmp/d255)."""
    _git(root, "init", "-q")
    (root / "tests").mkdir()
    (root / other).parent.mkdir(parents=True, exist_ok=True)
    (root / "tests/conftest.py").write_text(CONFTEST_FIX[1], encoding="utf-8")
    (root / "tests/test_env.py").write_text("def test_env():\n    pass\n", encoding="utf-8")
    (root / other).write_text("notes\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "base")
    (root / "tests/conftest.py").write_text(CONFTEST_FIX[0], encoding="utf-8")
    (root / "tests/test_env.py").write_text(test_body, encoding="utf-8")
    (root / other).write_text("notes\nflag documented\n", encoding="utf-8")
    _git(root, "add", "--all")
    return _git(root, "diff", "--cached", "--binary")


def _assert_restored(root: Path, other: str, test_body: str):
    assert (root / "tests/conftest.py").read_text(encoding="utf-8") == CONFTEST_FIX[0]
    assert (root / "tests/test_env.py").read_text(encoding="utf-8") == test_body
    assert (root / other).read_text(encoding="utf-8") == "notes\nflag documented\n"
    assert _git(root, "status", "--porcelain") == "".join(
        "M  %s\n" % name for name in sorted([other, "tests/conftest.py", "tests/test_env.py"])
    )


OTHERS = ["docs/notes.md", "CHANGELOG", "docs/notes.rst", "README.rst", "mkdocs.yml", "src/calc.py"]


@pytest.mark.parametrize("other", OTHERS)
def test_conftest_fix_beside_another_change_is_pass(tmp_path, other):
    # first run keeps conftest and passes; the retry reverts it and the test fails
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    changed = [other, "tests/conftest.py", "tests/test_env.py"]
    result = _run(tmp_path, diff, changed, [other])
    assert result.status is FixvalStatus.PASS, result.block_message
    assert result.findings == []
    _assert_restored(tmp_path, other, GUARDED_TEST)


@pytest.mark.parametrize("other", ["docs/notes.rst", "mkdocs.yml"])
def test_hollow_test_beside_conftest_and_another_change_still_blocks(tmp_path, other):
    diff = _support_beside_repo(tmp_path, other, HOLLOW_TEST)
    result = _run(tmp_path, diff, [other, "tests/conftest.py", "tests/test_env.py"], [other])
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    # the deciding run is the retry, which reverted the conftest too
    reverted = result.block_message.split("Reverted files:\n")[1].split("\n\n")[0]
    assert reverted.split() == sorted(
        [other, "tests/conftest.py"], key=[other, "tests/conftest.py"].index
    )
    _assert_restored(tmp_path, other, HOLLOW_TEST)


def test_hollow_message_names_only_what_the_deciding_run_reverted(tmp_path):
    # #254: the retry only breaks the test (fixture gone), so the first run decides
    diff = _repo(tmp_path, HOLLOW_FIXTURE)
    result = _run(tmp_path, diff, ["src/calc.py", *HOLLOW_FIXTURE], ["src/calc.py"])
    reverted = result.block_message.split("Reverted files:\n")[1].split("\n\n")[0]
    assert reverted.split() == ["src/calc.py"]


def test_retry_timeout_is_not_pass_and_the_fix_is_restored(tmp_path, monkeypatch):
    import subprocess as sp

    from code_forge import fixval

    real = fixval._test_reverted_candidate
    calls = []

    def second_times_out(*args, **kwargs):
        calls.append(kwargs.get("require_witness"))
        if len(calls) == 2:
            raise sp.TimeoutExpired(args[1], 600)
        return real(*args, **kwargs)

    monkeypatch.setattr(fixval, "_test_reverted_candidate", second_times_out)
    other = "docs/notes.rst"
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    result = _run(tmp_path, diff, [other, "tests/conftest.py", "tests/test_env.py"], [other])
    assert calls == [False, True]
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_TRANSACTION"]
    _assert_restored(tmp_path, other, GUARDED_TEST)


def test_unwitnessed_retry_keeps_the_first_hollow(tmp_path, monkeypatch):
    from code_forge import fixval

    real = fixval._test_reverted_candidate

    def retry_has_no_verdict(*args, **kwargs):
        return None if kwargs.get("require_witness") else real(*args, **kwargs)

    monkeypatch.setattr(fixval, "_test_reverted_candidate", retry_has_no_verdict)
    other = "mkdocs.yml"
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    result = _run(tmp_path, diff, [other, "tests/conftest.py", "tests/test_env.py"], [other])
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    assert result.block_message.split("Reverted files:\n")[1].split("\n\n")[0].split() == [other]
    _assert_restored(tmp_path, other, GUARDED_TEST)


PYTEST_CMD = [sys.executable, "-B", "-m", "pytest", "-q"]
COLOR_FAILED = "\x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[31m\x1b[1m1 error\x1b[0m\x1b[31m in 0.03s\x1b[0m\n"
COLOR_ERROR = "\x1b[31m\x1b[1m1 error\x1b[0m\x1b[31m in 0.03s\x1b[0m\n"
QQ_FAILED = "=== short test summary info ===\nFAILED tests/test_a.py::test_x - assert 0\nERROR tests/test_a.py::test_e\n"
QQ_ERROR = "=== short test summary info ===\nERROR tests/test_a.py::test_e - RuntimeError\n"


@pytest.mark.parametrize(
    ("code", "stdout", "witnessed"),
    [
        (1, "F\n1 failed in 0.01s\n", True),
        (1, "===== 1 failed, 2 passed, 1 error in 0.10s =====\n", True),
        (1, "E\nERROR tests/test_a.py::test_a\n1 error in 0.00s\n", False),
        (2, "!!! Interrupted: 1 error during collection !!!\n1 error in 0.06s\n", False),
        (1, "==== 2 errors, 3 warnings in 1.5s ====\n", False),
        (1, "1 failed in 0.1s\nrerun\n1 error in 0.1s\n", False),
        # line-start anchor: a summary-like phrase mid-line is not the summary
        (1, "1 error in 0.1s\nlog: retried, 2 failed in 9s window\n", False),
        (1, "10 failed in 2s\n", True),
        (1, "0 failed, 1 error in 2s\n", False),
        # --color=yes / PY_COLORS: colour codes are stripped before matching
        (1, COLOR_FAILED, True),
        (1, COLOR_ERROR, False),
        # -qq: no closing summary; the short-summary entries decide
        (1, QQ_FAILED, True),
        (1, QQ_ERROR, False),
        (1, "log line\n  FAILED indented\n", False),
        # -rN -qq or anything unreadable: no verdict
        (1, "", False),
        # exit 2/3/4/5 under pytest: no verdict whatever the output says
        (2, "1 failed in 0.1s\n", False),
        (4, "ERROR: usage: pytest [options]\n", False),
        (5, "no tests ran in 0.01s\n", False),
        (-9, "1 failed in 0.1s\n", False),
        (-15, "", False),
    ],
)
def test_witnessed_failure_under_pytest(code, stdout, witnessed):
    import subprocess as sp

    from code_forge.fixval import _witnessed_failure

    assert _witnessed_failure(PYTEST_CMD, sp.CompletedProcess([], code, stdout, "")) is witnessed


@pytest.mark.parametrize(
    ("code", "witnessed"), [(1, True), (2, True), (4, True), (-9, False), (-15, False)]
)
def test_witnessed_failure_other_runners_keep_nonzero_fails(code, witnessed):
    import subprocess as sp

    from code_forge.fixval import _witnessed_failure

    result = sp.CompletedProcess([], code, "make: *** [test] Error 1\n", "")
    assert _witnessed_failure(["make", "pytest"], result) is witnessed


def test_revert_patch_projections():
    diff = _diff("docs/notes.rst", "tests/conftest.py", "tests/helpers.py", "tests/test_calc.py")
    assert _filter_non_test_patch(diff) == _diff("docs/notes.rst")
    assert _filter_non_test_patch(diff, revert_support=True) == _diff(
        "docs/notes.rst", "tests/conftest.py", "tests/helpers.py"
    )
    rename = (
        "diff --git a/src/tool.py b/docs/tool.md\nsimilarity index 100%\n"
        "rename from src/tool.py\nrename to docs/tool.md\n"
    )
    assert _filter_non_test_patch(rename + _diff("tests/conftest.py", "tests/test_calc.py")) == rename


def test_relative_classification_does_not_depend_on_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .git anywhere above a relative path's parents
    candidate = classify_fixval_candidate(FILES)
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == ["src/calc.py", "test/helpers.py"]


def test_no_retry_when_no_support_was_kept(tmp_path, monkeypatch):
    from code_forge import fixval

    real = fixval._test_reverted_candidate
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs.get("require_witness"))
        return real(*args, **kwargs)

    monkeypatch.setattr(fixval, "_test_reverted_candidate", counted)
    hollow = {"tests/test_calc.py": "def test_calc():\n    assert True\n"}
    diff = _repo(tmp_path, hollow)
    result = _run(tmp_path, diff, ["src/calc.py", *hollow], ["src/calc.py"])
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    assert calls == [False]


@pytest.mark.parametrize("first", ["pass", "transaction-block"])
def test_only_a_hollow_first_run_is_retried(tmp_path, monkeypatch, first):
    from code_forge import fixval

    real = fixval._test_reverted_candidate
    calls = []

    def first_run(*args, **kwargs):
        calls.append(kwargs.get("require_witness"))
        if first == "pass":
            return real(*args, **kwargs)
        return fixval._transaction_block("injected", str(tmp_path))

    monkeypatch.setattr(fixval, "_test_reverted_candidate", first_run)
    other = "docs/notes.rst"
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    if first == "pass":  # the doc revert alone already fails the test
        (tmp_path / "tests/test_env.py").write_text(
            GUARDED_TEST
            + "\n\ndef test_doc():\n    assert 'flag' in open('%s').read()\n" % (tmp_path / other),
            encoding="utf-8",
        )
        _git(tmp_path, "add", "--all")
        diff = _git(tmp_path, "diff", "--cached", "--binary")
    result = _run(tmp_path, diff, [other, "tests/conftest.py", "tests/test_env.py"], [other])
    assert calls == [False]
    expected = ["FIXVAL_TRANSACTION"] if first != "pass" else []
    assert [finding.id for finding in result.findings] == expected


def test_first_run_collection_error_stays_pass_as_on_main(tmp_path):
    # the test imports a name the fix adds: exit 2 on reverted code, judged as on main (#28)
    fixed = {
        "src/calc.py": "value = 2\n\ndef helper():\n    return value\n",
        "tests/test_calc.py": "from calc import helper\n\ndef test_calc():\n    assert helper() == 2\n",
    }
    diff = _repo(tmp_path, fixed)
    result = _run(tmp_path, diff, list(fixed), ["src/calc.py"])
    assert result.status is FixvalStatus.PASS, result.block_message


ADDOPT_FIXTURE = {
    "tests/conftest.py": (
        "import pytest\n\ndef pytest_addoption(parser):\n    parser.addoption('--forge-flag')\n\n"
        "@pytest.fixture\ndef expected():\n    return 2\n"
    ),
    "tests/test_calc.py": "def test_calc(expected):\n    assert expected == 2\n",
}


@pytest.mark.parametrize(
    ("fixed", "base", "extra"),
    [
        (HOLLOW_FIXTURE, {"pytest.ini": "[pytest]\naddopts = --color=yes\n"}, []),
        (HOLLOW_FIXTURE, {}, ["--color=yes"]),
        (HOLLOW_FIXTURE, {}, ["-qq"]),
        (HOLLOW_HELPER, {}, ["-qq", "--color=yes"]),
        # the retry lacks the conftest option: pytest exits 4 with no summary
        (ADDOPT_FIXTURE, {"pytest.ini": "[pytest]\naddopts = --forge-flag=1\n"}, []),
    ],
    ids=["addopts-color", "color", "qq", "qq-color-helper", "exit-4"],
)
def test_unreadable_retry_keeps_the_254_hollow(tmp_path, fixed, base, extra):
    diff = _repo(tmp_path, fixed, base)
    candidate = classify_fixval_candidate(["src/calc.py", *fixed])
    assert isinstance(candidate, FixvalCandidate)
    result = run_fixval(candidate, PYTEST + extra, tmp_path, "fix calc", diff)
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"], result.block_message
    assert result.block_message.split("Reverted files:\n")[1].split("\n\n")[0].split() == ["src/calc.py"]
    for name, text in fixed.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text


@pytest.mark.parametrize("extra", [["--color=yes"], ["-qq"]])
def test_readable_retry_failure_is_pass_under_color_and_qq(tmp_path, extra):
    other = "docs/notes.rst"
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    candidate = classify_fixval_candidate([other, "tests/conftest.py", "tests/test_env.py"])
    assert isinstance(candidate, FixvalCandidate)
    result = run_fixval(candidate, PYTEST + extra, tmp_path, "fix env", diff)
    assert result.status is FixvalStatus.PASS, result.block_message
    _assert_restored(tmp_path, other, GUARDED_TEST)


def test_limitation_support_fix_that_only_removes_errors_is_hollow(tmp_path, monkeypatch):
    """Pinned trade-off (fail-closed): repairing a fixture that raised, beside
    another non-test change, reads like #254's "test needs the support" and is
    FIXVAL_HOLLOW (main: PASS). FIXVAL_WAIVER is the escape."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "tests").mkdir()
    (tmp_path / "CHANGELOG.rst").write_text("v1\n", encoding="utf-8")
    broken = "import pytest\n\n@pytest.fixture\ndef flag():\n    raise RuntimeError('broken')\n"
    (tmp_path / "tests/conftest.py").write_text(broken, encoding="utf-8")
    (tmp_path / "tests/test_flag.py").write_text("def test_flag(flag):\n    pass\n", encoding="utf-8")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "CHANGELOG.rst").write_text("v1\nfixed the flag fixture\n", encoding="utf-8")
    (tmp_path / "tests/conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef flag():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "tests/test_flag.py").write_text(
        "def test_flag(flag):\n    assert flag == 1\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "--all")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    changed = ["CHANGELOG.rst", "tests/conftest.py", "tests/test_flag.py"]
    result = _run(tmp_path, diff, changed, ["CHANGELOG.rst"])
    assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    assert result.block_message.split("Reverted files:\n")[1].split("\n\n")[0].split() == [
        "CHANGELOG.rst"
    ]
    monkeypatch.setenv("FIXVAL_WAIVER", "fixture repair; errors-only on revert")
    waived = run_fixval(classify_fixval_candidate(changed), PYTEST, tmp_path, "fix flag", diff)
    assert waived.status is FixvalStatus.WAIVED


def _strip_then_pass(env_seen):
    def guard(cmd, env, root, *, allow_strip_retry, retry_cause=None, first_attempt=None, **_kw):
        env_seen.append(dict(env))
        if allow_strip_retry:
            retry_cause.append("run 1: returncode 11; stderr: No module named pytest")
            return ("needs_strip_retry", [], [])
        return ("passed", [], [])

    return guard


@pytest.mark.parametrize("decider", ["retry-pass", "retry-hollow", "first-hollow-stands"])
def test_env_strip_advisory_is_reported_once_whichever_run_decides(tmp_path, monkeypatch, decider):
    # #246 on top of #255: the strip-retry note goes on the final verdict,
    # whether the first reverted run or the support-reverting retry decided
    from code_forge import fixval

    monkeypatch.setenv("VIRTUAL_ENV", "/private-venv")
    monkeypatch.setattr(fixval, "_run_baseline_guard", _strip_then_pass([]))
    if decider == "first-hollow-stands":
        real = fixval._test_reverted_candidate

        def retry_has_no_verdict(*args, **kwargs):
            return None if kwargs.get("require_witness") else real(*args, **kwargs)

        monkeypatch.setattr(fixval, "_test_reverted_candidate", retry_has_no_verdict)
    other = "mkdocs.yml"
    body = HOLLOW_TEST if decider == "retry-hollow" else GUARDED_TEST
    diff = _support_beside_repo(tmp_path, other, body)
    result = _run(tmp_path, diff, [other, "tests/conftest.py", "tests/test_env.py"], [other])
    expected = {
        "retry-pass": [],
        "retry-hollow": ["FIXVAL_HOLLOW"],
        "first-hollow-stands": ["FIXVAL_HOLLOW"],
    }
    assert [finding.id for finding in result.findings] == expected[decider]
    notes = [a for a in result.advisories if a.id == "FIXVAL_BASELINE_ENV_RETRY"]
    assert len(notes) == 1 and "VIRTUAL_ENV /private-venv" in notes[0].description
    _assert_restored(tmp_path, other, body)


def _retry_case(tmp_path, monkeypatch, retry):
    """A HOLLOW first run, then `retry(real, args, kwargs)` for the retry call."""
    from code_forge import fixval

    real = fixval._test_reverted_candidate
    calls = []

    def spy(*args, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("require_witness"):
            return retry(real, args, kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(fixval, "_test_reverted_candidate", spy)
    other = "docs/notes.rst"
    diff = _support_beside_repo(tmp_path, other, GUARDED_TEST)
    candidate = classify_fixval_candidate([other, "tests/conftest.py", "tests/test_env.py"])
    result = run_fixval(candidate, PYTEST, tmp_path, "fix calc", diff, baseline_timeout=2400)
    _assert_restored(tmp_path, other, GUARDED_TEST)
    return result, calls


def test_the_hollow_retry_gets_the_gate_timeout(tmp_path, monkeypatch):
    """#255 review Low 1: the retry is not left on the 600s default."""
    result, calls = _retry_case(tmp_path, monkeypatch, lambda real, a, k: real(*a, **k))
    assert [call.get("require_witness") for call in calls] == [False, True]
    assert [call.get("timeout") for call in calls] == [2400, 2400]
    assert result.status is FixvalStatus.PASS, result.block_message


def test_a_timed_out_hollow_retry_is_inconclusive(tmp_path, monkeypatch):
    """#255 review Low 2: a retry that times out reports the timeout
    (FIXVAL_INCONCLUSIVE BLOCK), not the first run's HOLLOW or a PASS."""

    def sleeps(real, args, kwargs):
        slow = [sys.executable, "-c", "import time; time.sleep(30)"]
        return real(args[0], slow, *args[2:], **{**kwargs, "timeout": 1})

    result, _ = _retry_case(tmp_path, monkeypatch, sleeps)
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_INCONCLUSIVE"]
    assert "timed out after 1s" in result.block_message


@pytest.mark.parametrize(
    ("cmd", "runs_pytest"),
    [
        # #267 Low 1: the run-wrapper unwrapping
        (["uv", "run", "pytest", "-q"], True),
        (["poetry", "run", "python", "-m", "pytest"], True),
        (["pdm", "run", "pytest"], True),
        (["pipenv", "run", "pytest"], True),
        (["hatch", "run", "pytest"], True),
        (["uv", "run", "make", "pytest"], False),
        # #267 Low 2: shapes main fell back to "nonzero = fails" for
        (["uv", "run", "--frozen", "pytest"], True),
        (["uv", "run", "--frozen", "--no-sync", "python", "-m", "pytest"], True),
        (["coverage", "run", "-m", "pytest"], True),
        (["coverage", "run", "--branch", "-m", "pytest", "tests"], True),
        (["env", "A=1", "pytest"], True),
        (["env", "-i", "A=1", "B=2", "python", "-m", "pytest"], True),
        (["A=1", "pytest"], True),
        (["py", "-m", "pytest"], True),
        (["py", "-3.12", "-m", "pytest"], True),
        (["py.exe", "-m", "pytest"], True),
        # still not pytest
        (["coverage", "run", "script.py"], False),
        (["coverage", "report"], False),
        (["env", "make", "pytest"], False),
        (["py", "script.py", "pytest"], False),
        (["tox", "-e", "pytest"], False),
        (["uv", "run"], False),
        (["env"], False),
        # #269 review P2: a value-taking option's value is not the program
        (["uv", "run", "--with", "pytest", "make", "test"], False),
        (["uv", "run", "--with", "pytest", "./run_tests.sh"], False),
        (["uv", "run", "--with", "pytest", "bash", "-c", "pytest; exit 3"], False),
        (["env", "-u", "PYTEST", "make"], False),
        (["uv", "run", "--with", "pytest", "pytest"], True),
        (["uv", "run", "--with=pytest", "pytest"], True),
        (["uv", "run", "-p", "3.12", "pytest"], True),
        (["uv", "run", "--python", "3.12", "pytest"], True),
        (["env", "-u", "PYTEST", "pytest"], True),
        (["coverage", "run", "--source", "src", "-m", "pytest"], True),
        (["uv", "run", "--", "pytest"], True),
        # an unknown option stops unwrapping: not pytest ("nonzero = fails")
        (["uv", "run", "--some-new-flag", "pytest"], False),
        (["env", "--some-new-flag", "pytest"], False),
        (["coverage", "run", "--some-new-flag", "-m", "pytest"], False),
        # #269 review Lows
        (["uv", "pip", "pytest"], False),
        (["coverage", "report", "-m", "pytest"], False),
        (["poetry", "run", "env", "A=1", "pytest"], True),
        (["uv", "run", "env", "A=1", "coverage", "run", "-m", "pytest"], True),
        (["./x=1", "pytest"], False),
        (["1A=1", "pytest"], False),
        (["A-B=1", "pytest"], False),
    ],
)
def test_runs_pytest_sees_through_wrappers(cmd, runs_pytest):
    from code_forge.fixval import _runs_pytest

    assert _runs_pytest(cmd) is runs_pytest


@pytest.mark.parametrize(
    "cmd",
    [
        ["uv", "run", "pytest"],
        ["uv", "run", "--frozen", "pytest"],
        ["coverage", "run", "-m", "pytest"],
        ["env", "A=1", "pytest"],
        ["py", "-m", "pytest"],
    ],
)
def test_a_fixture_error_under_a_wrapped_pytest_is_no_witness(cmd):
    """#267: a retry that only errors (fixture gone) is not a failed test."""
    from code_forge.fixval import _witnessed_failure

    result = subprocess.CompletedProcess(cmd, 1, QQ_ERROR, "")
    assert _witnessed_failure(cmd, result) is False


@pytest.mark.parametrize("cmd", [["coverage", "run", "-m", "pytest"], ["uv", "run", "pytest"]])
def test_wrapped_pytest_exit_5_on_reverted_code_is_inconclusive(monkeypatch, cmd):
    """#269: wrapped pytest also gets #252's exit classification; exit 5
    (no tests collected) never judged the test, so it is not PASS."""
    from code_forge import fixval

    def no_tests_collected(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 5, "no tests ran in 0.01s\n", "")

    monkeypatch.setattr(fixval.subprocess, "run", no_tests_collected)
    candidate = classify_fixval_candidate(["src/calc.py", "tests/test_calc.py"])
    result = fixval._test_reverted_candidate(candidate, cmd, {}, ".")
    assert result.status is FixvalStatus.BLOCK
    assert [finding.id for finding in result.findings] == ["FIXVAL_INCONCLUSIVE"]
    assert "pytest exit 5 (no tests collected)" in result.block_message
