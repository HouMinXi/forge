# SPDX-License-Identifier: Apache-2.0
"""#28: a reverted pytest run is RED only when its JUnit report shows a failed
test, or a collection or setup error importing a name the fix adds."""

import ast
import difflib
import os
import subprocess
import sys
import tempfile

import pytest

from code_forge._fixval_junit import AddedNames, added_names, red_witness
from code_forge.fixval import (
    FixvalCandidate,
    FixvalStatus,
    _test_reverted_candidate,
    classify_fixval_candidate,
    run_fixval,
)

CANDIDATE = FixvalCandidate(["test_probe.py"], ["src/calc.py"])
CALC_HELPER = AddedNames(frozenset({("calc", "helper")}), frozenset())
NEW_MODULE = AddedNames(frozenset(), frozenset({"pkg.newmod"}))
NOTHING = AddedNames(frozenset(), frozenset())


def _patch(path, before, after):
    return "diff --git a/%s b/%s\n" % (path, path) + "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/" + path,
            tofile="b/" + path,
        )
    )


def _added(before, after, path="src/calc.py"):
    # Fixtures are complete source images, not snippets inferred from hunks.
    ast.parse(before)
    ast.parse(after)
    return added_names(
        _patch(path, before, after),
        before={path: before.encode()},
        after={path: after.encode()},
    )


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("def helper(x): pass\n", {"helper"}),
        ("async def fetch(): pass\n", {"fetch"}),
        ("class Store: pass\n", {"Store"}),
        ("LIMIT = 3\n", {"LIMIT"}),
        ("LIMIT: int = 3\n", {"LIMIT"}),
        ("from .impl import run, stop as halt\n", {"run", "halt"}),
        ("import json\n", {"json"}),
        ("def outer():\n    def method(self): pass\n", {"outer"}),
        ("def outer():\n    value = 1\n", {"outer"}),
        ("value == 1\n", set()),
        ("if False:\n    x = 1\n", set()),
        ("# def commented():\n", set()),
    ],
)
def test_added_names_reads_module_level_bindings(source, names):
    found = _added("pass\n", source)
    assert {name for _module, name in found.names} == names
    assert found.modules == frozenset()


def test_added_names_maps_paths_to_modules_and_skips_names_that_already_existed():
    before = "def helper(): pass\nOLD = 1\n"
    after = "def helper(x): pass\nNEW = 2\n"
    found = _added(before, after)
    assert found.names == {("src.calc", "NEW"), ("calc", "NEW")}
    created = "diff --git a/lib/pkg/__init__.py b/lib/pkg/__init__.py\nnew file mode 100644\n--- /dev/null\n+++ b/lib/pkg/__init__.py\n@@ -0,0 +1 @@\n+def entry(): pass\n"
    found = added_names(
        created,
        before={"lib/pkg/__init__.py": None},
        after={"lib/pkg/__init__.py": b"def entry(): pass\n"},
    )
    assert found.names == {("lib.pkg", "entry")}
    assert found.modules == {"lib.pkg"}
    assert _added("pass\n", "def nothing(): pass\n", "docs/notes.md") == NOTHING


def test_a_path_that_is_not_an_importable_module_adds_nothing():
    assert _added("pass\n", "def helper(): pass\n", "pkg/foo-bar.py") == NOTHING


@pytest.mark.parametrize(
    ("before", "after", "names"),
    [
        ("", "from .impl import (\n    run,  # see (a)\n    stop as halt,\n)\n", {"run", "halt"}),
        ("from .impl import (\n    run,\n)\n", "from .impl import (stop,\n)\n", {"stop"}),
        ("from .impl import (\n run,\n)\n", "from .impl import (\n run,\n    stop,\n)\n", {"stop"}),
        ("from .impl import (\n run, stop\n)\n", "from .impl import (\n run,\n stop,\n)\n", set()),
        ("", "from .impl import (run,\n    stop)\ndef after(): pass\n", {"run", "stop", "after"}),
        ("from .impl import (run,)\n", "from .impl import (run,)\nif False:\n    value = 1\n", set()),
    ],
)
def test_added_names_reads_multi_line_imports(before, after, names):
    assert {name for _module, name in _added(before, after).names} == names


def test_a_comment_on_the_opening_line_still_opens_the_import():
    found = _added("", "from .impl import (  # noqa: F401\n    helper,\n)\n")
    assert found.names == {("src.calc", "helper"), ("calc", "helper")}
    assert red_witness(_report(_error("collection failure", IMPORT_HELPER)), found) is True


def test_a_function_level_parenthesised_import_binds_nothing():
    found = _added(
        "", "def load():\n    from .impl import (\n        helper,\n    )\n    return helper\n"
    )
    assert {name for _module, name in found.names} == {"load"}


@pytest.mark.parametrize(
    ("before", "after", "names"),
    [
        ("", "from pkg.o import a, \\\n    newname\n", {"a", "newname"}),
        ("", "from pkg.o import \\\n    a, \\\n    newname\nVALUE = 1\n", {"a", "newname", "VALUE"}),
        ("", "import json, \\\n    csv as table\n", {"json", "table"}),
        ("", "from pkg.o import a,\\\n    b,\\\n    newname  # noqa\n", {"a", "b", "newname"}),
        ("from pkg.o import a\n", "from pkg.o import a, \\\n    newname\n", {"newname"}),
        ("", "total = 1 + \\\n    newname\n", {"total"}),
        ("", "def f():\n    from pkg.o import a, \\\n        b\n", {"f"}),
        ("", "import json\nVALUE = 1\n", {"json", "VALUE"}),
        ("", "from pkg.o import (a, \\\n    newname)\nVALUE = 1\n", {"a", "newname", "VALUE"}),
        ("", "import os  # see \\\ndef helper(): pass\n", {"os", "helper"}),
        ("", "from pkg.o import a, \\\n    b  # c \\\nVALUE = 1\n", {"a", "b", "VALUE"}),
    ],
)
def test_added_names_reads_backslash_continued_imports(before, after, names):
    assert {name for _module, name in _added(before, after).names} == names


def test_a_backslash_continued_import_of_an_added_name_is_red():
    found = _added("from pkg.o import a\n", "from pkg.o import a, \\\n    helper\n")
    assert red_witness(_report(_error("collection failure", IMPORT_HELPER)), found) is True


def test_a_definition_after_an_import_whose_comment_ends_in_a_backslash_is_red():
    found = _added("", "import os  # see \\\ndef helper():\n    return os.sep\n")
    assert red_witness(_report(_error("collection failure", IMPORT_HELPER)), found) is True


def test_an_import_left_open_at_the_end_of_a_hunk_does_not_leak_into_the_next():
    before = "from .impl import (\n    old,\n)\n" + "# padding\n" * 16 + "call(\n    old,\n)\n"
    after = before.replace("from .impl import (\n", "from .impl import (\n    run,\n").replace(
        "call(\n", "call(\n    stop,\n"
    )
    found = _added(before, after)
    assert {name for _module, name in found.names} == {"run"}
    found = _added(before, after + "LIMIT = 3\n")
    assert {name for _module, name in found.names} == {"run", "LIMIT"}


RENAME = """\
diff --git a/src/old.py b/src/pkg/newmod.py
similarity index 90%
rename from src/old.py
rename to src/pkg/newmod.py
--- a/src/old.py
+++ b/src/pkg/newmod.py
@@ -1 +1 @@
-X = 1
+X = 2
"""


def test_a_renamed_or_copied_file_adds_its_module():
    for patch in (RENAME, RENAME.replace("rename ", "copy ")):
        found = added_names(
            patch, before={"src/pkg/newmod.py": None}, after={"src/pkg/newmod.py": b"X = 2\n"}
        )
        assert found.modules == {"src.pkg.newmod", "pkg.newmod"}


def test_a_rename_to_a_name_the_module_already_had_adds_no_module():
    rename = RENAME.replace("src/old.py", "src/calc.py").replace("src/pkg/newmod.py", "calc.py")
    found = added_names(rename, before={"calc.py": None}, after={"calc.py": b"X = 2\n"})
    assert found.modules == frozenset()


def test_a_modified_file_adds_no_module():
    assert _added("X = 1\n", "X = 2\n").modules == frozenset()
    plain = "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1 +1 @@\n-X = 1\n+X = 2\n"
    found = added_names(plain, before={"src/calc.py": b"X = 1\n"}, after={"src/calc.py": b"X = 2\n"})
    assert found.modules == frozenset()


def test_unreadable_patch_adds_nothing():
    assert added_names("@@ not a diff\n+def helper():\n", before={}, after={}) == NOTHING


def _report(*cases):
    return "<testsuites><testsuite>%s</testsuite></testsuites>" % "".join(
        '<testcase classname="t" name="n%d">%s</testcase>' % item for item in enumerate(cases)
    )


def _error(message, text=""):
    return '<error message="%s">%s</error>' % (message, text)


IMPORT_HELPER = "E   ImportError: cannot import name 'helper' from 'calc' (/r/src/calc.py)"
IMPORT_NEWMOD = "E   ImportError: cannot import name 'newmod' from 'pkg' (/r/src/pkg/__init__.py)"
NO_ATTRIBUTE_NEWMOD = "E AttributeError: module 'pkg' has no attribute 'newmod'"
NEWMOD2 = AddedNames(frozenset(), frozenset({"pkg.newmod2"}))


@pytest.mark.parametrize(
    ("report", "added", "red"),
    [
        (_report('<failure message="assert 1 == 2"/>'), NOTHING, True),
        (_report(_error("collection failure", IMPORT_HELPER)), CALC_HELPER, True),
        # one testcase: an unexpected pass, then a real failure (every <failure> is read)
        (
            _report('<failure message="[XPASS(strict)] "/><failure message="assert 1 == 2"/>'),
            NOTHING,
            True,
        ),
        (
            _report('<failure message="Failed: Unexpected success"/><failure message="boom"/>'),
            NOTHING,
            True,
        ),
        # a real failure, then an unexpected pass (the last <failure> is not the only one read)
        (
            _report('<failure message="assert 1 == 2"/><failure message="[XPASS(strict)] "/>'),
            NOTHING,
            True,
        ),
        (
            _report(_error("failed on setup with &quot;%s&quot;" % IMPORT_HELPER.removeprefix("E   "))),
            CALC_HELPER,
            True,
        ),
        # `from pkg import newmod`, where the fix creates the module pkg/newmod.py
        (_report(_error("collection failure", IMPORT_NEWMOD)), NEW_MODULE, True),
        (
            _report(
                _error("collection failure", "E AttributeError: module 'pkg' has no attribute 'newmod'")
            ),
            NEW_MODULE,
            True,
        ),
        (
            _report(_error("collection failure", IMPORT_NEWMOD.replace("newmod", "other"))),
            NEW_MODULE,
            False,
        ),
        # a parent of a created module is not RED, nor is a namespace parent
        (
            _report(_error("collection failure", "E ModuleNotFoundError: No module named 'pkg'")),
            NEW_MODULE,
            False,
        ),
        (
            _report(_error("collection failure", "E ModuleNotFoundError: No module named 'pkg.new'")),
            AddedNames(frozenset(), frozenset({"pkg.new.mod"})),
            False,
        ),
        # false-RED boundaries: the module must be exactly `M.X`, and `No module
        # named` must name a module the fix creates, not one that starts with it
        (_report(_error("collection failure", IMPORT_NEWMOD)), NEWMOD2, False),
        (
            _report(_error("collection failure", IMPORT_NEWMOD.replace("'pkg'", "'other'"))),
            NEW_MODULE,
            False,
        ),
        (_report(_error("collection failure", NO_ATTRIBUTE_NEWMOD)), NEWMOD2, False),
        (
            _report(_error("collection failure", NO_ATTRIBUTE_NEWMOD.replace("'pkg'", "'other'"))),
            NEW_MODULE,
            False,
        ),
        (
            _report(_error("collection failure", "E ModuleNotFoundError: No module named 'pkg.newer'")),
            AddedNames(frozenset(), frozenset({"pkg.new"})),
            False,
        ),
        (
            _report(
                _error("collection failure", "E AttributeError: module 'calc' has no attribute 'helper'")
            ),
            CALC_HELPER,
            True,
        ),
        (
            _report(_error("collection failure", "E ModuleNotFoundError: No module named 'pkg.newmod'")),
            NEW_MODULE,
            True,
        ),
        (_report("", _error("collection failure", IMPORT_HELPER)), CALC_HELPER, True),
        # counterexamples: errors that did not judge the test
        (_report(_error("failed on teardown with &quot;%s&quot;" % IMPORT_HELPER)), CALC_HELPER, False),
        (
            _report(_error("collection failure", IMPORT_HELPER.replace("'calc'", "'other'"))),
            CALC_HELPER,
            False,
        ),
        (
            _report(_error("collection failure", IMPORT_HELPER.replace("helper", "helpers"))),
            CALC_HELPER,
            False,
        ),
        (
            _report(
                _error("collection failure", "E AttributeError: module 'calc' has no attribute 'other'")
            ),
            CALC_HELPER,
            False,
        ),
        (_report(_error("collection failure", "E   SyntaxError: invalid syntax")), CALC_HELPER, False),
        (_report(_error("failed on setup with &quot;RuntimeError: db down&quot;")), CALC_HELPER, False),
        (
            _report(_error("collection failure", "E ModuleNotFoundError: No module named 'pkg.other'")),
            NEW_MODULE,
            False,
        ),
        (_report(_error("collection failure", IMPORT_HELPER)), NOTHING, False),
        (_report('<skipped message="no db"/>', ""), CALC_HELPER, False),
        (_report(), CALC_HELPER, False),
        ("<testsuites><testcase>", CALC_HELPER, False),
        ("", CALC_HELPER, False),
    ],
)
def test_red_witness(report, added, red):
    assert red_witness(report, added) is red


UNEXPECTED_PASSES = """\
import unittest

import pytest


@pytest.mark.xfail(strict=True)
def test_strict_xpass():
    pass


@pytest.mark.xfail(strict=True, reason="bug 7")
def test_strict_xpass_with_reason():
    pass


class Case(unittest.TestCase):
    @unittest.expectedFailure
    def test_unexpected_success(self):
        pass
"""


def _real_report(tmp_path, body):
    (tmp_path / "test_probe.py").write_text(body, encoding="utf-8")
    report = tmp_path / "junit.xml"
    run = subprocess.run(
        [*PYTEST, "--junitxml=%s" % report, "test_probe.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1, run.stdout + run.stderr
    return report.read_text(encoding="utf-8")


def test_real_unexpected_passes_are_not_red(tmp_path):
    report = _real_report(tmp_path, UNEXPECTED_PASSES)
    assert report.count("<failure") == 3  # pytest reports each one as a failure
    assert red_witness(report, CALC_HELPER) is False


def test_real_failed_test_beside_unexpected_passes_is_red(tmp_path):
    report = _real_report(tmp_path, UNEXPECTED_PASSES + "\n\ndef test_real():\n    assert 1 == 2\n")
    assert red_witness(report, NOTHING) is True


PYTEST = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist"]


def _reverted(tmp_path, body, added=CALC_HELPER, conftest=None):
    (tmp_path / "test_probe.py").write_text(body, encoding="utf-8")
    if conftest is not None:
        (tmp_path / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "calc.py").write_text("value = 1\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    return _test_reverted_candidate(
        CANDIDATE, [*PYTEST, "test_probe.py"], env, str(tmp_path), added=added
    )


def _inconclusive(result, code):
    assert result.status is FixvalStatus.BLOCK, result
    assert [f.id for f in result.findings] == ["FIXVAL_INCONCLUSIVE"]
    assert "pytest exit %d without a failed test" % code in result.block_message


def test_real_collection_import_of_an_added_name_is_red(tmp_path):
    result = _reverted(tmp_path, "from calc import helper\n\ndef test_it():\n    helper()\n")
    assert result.status is FixvalStatus.PASS, result.block_message


def test_real_collection_import_of_another_name_is_inconclusive(tmp_path):
    _inconclusive(_reverted(tmp_path, "from calc import other\n\ndef test_it():\n    other()\n"), 2)


def test_real_collection_error_that_is_not_an_import_is_inconclusive(tmp_path):
    _inconclusive(_reverted(tmp_path, "raise RuntimeError('broken')\n\ndef test_it():\n    pass\n"), 2)


FIXTURE = "import pytest\n\n@pytest.fixture\ndef dep():\n    %s\n"


def test_real_setup_import_of_an_added_name_is_red(tmp_path):
    conftest = FIXTURE % "from calc import helper\n    return helper"
    result = _reverted(tmp_path, "def test_it(dep):\n    dep()\n", conftest=conftest)
    assert result.status is FixvalStatus.PASS, result.block_message


def test_real_setup_error_that_is_not_an_import_is_inconclusive(tmp_path):
    conftest = FIXTURE % "raise RuntimeError('db down')"
    _inconclusive(_reverted(tmp_path, "def test_it(dep):\n    pass\n", conftest=conftest), 1)


def test_real_teardown_error_beside_a_passing_test_is_inconclusive(tmp_path):
    conftest = FIXTURE % "yield\n    from calc import helper"
    _inconclusive(_reverted(tmp_path, "def test_it(dep):\n    pass\n", conftest=conftest), 1)


def test_real_failed_test_is_red_and_the_report_is_removed(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(reports))
    result = _reverted(tmp_path, "def test_it():\n    assert False\n", added=None)
    assert result.status is FixvalStatus.PASS, result.block_message
    assert list(reports.iterdir()) == []


def test_real_import_of_a_module_the_fix_creates_is_red(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    body = "from pkg import newmod\n\ndef test_it():\n    newmod.run()\n"
    result = _reverted(tmp_path, body, added=NEW_MODULE)
    assert result.status is FixvalStatus.PASS, result.block_message


def test_real_command_with_a_double_dash_still_gets_its_report(tmp_path):
    (tmp_path / "test_probe.py").write_text("def test_it():\n    assert False\n", encoding="utf-8")
    cmd = [*PYTEST, "--", "test_probe.py"]
    result = _test_reverted_candidate(CANDIDATE, cmd, dict(os.environ), str(tmp_path), added=NOTHING)
    assert result.status is FixvalStatus.PASS, result.block_message


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        (["pytest", "-q", "--", "t.py"], ["pytest", "-q", "J", "--", "t.py"]),
        (
            ["python", "-B", "-m", "pytest", "--", "pytest"],
            ["python", "-B", "-m", "pytest", "J", "--", "pytest"],
        ),
        (["uv", "run", "--", "pytest", "t.py"], ["uv", "run", "--", "pytest", "t.py", "J"]),
        (
            ["coverage", "run", "-m", "pytest", "--", "t.py"],
            ["coverage", "run", "-m", "pytest", "J", "--", "t.py"],
        ),
        (["pytest", "t.py"], ["pytest", "t.py", "J"]),
    ],
)
def test_junitxml_goes_before_a_double_dash_of_pytest(tmp_path, monkeypatch, cmd, expected):
    calls = _spy_run(monkeypatch, 1)
    _test_reverted_candidate(CANDIDATE, cmd, {}, str(tmp_path))
    argv = [("J" if arg.startswith("--junitxml=") else arg) for arg in calls[0]]
    assert argv == expected


def test_real_unexpected_passes_alone_are_inconclusive(tmp_path):
    _inconclusive(_reverted(tmp_path, UNEXPECTED_PASSES), 1)


def _spy_run(monkeypatch, returncode):
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        reports = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml=")]
        if reports:
            report = os.path.dirname(reports[0])
            calls.append(("report-dir", report, os.stat(report).st_mode & 0o777, os.listdir(report)))
        return subprocess.CompletedProcess(argv, returncode, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_require_witness_run_passes_no_junitxml(tmp_path, monkeypatch):
    calls = _spy_run(monkeypatch, 1)
    cmd = [*PYTEST, "test_probe.py"]
    result = _test_reverted_candidate(CANDIDATE, cmd, {}, str(tmp_path), require_witness=True)
    assert calls == [cmd]
    assert result is None


def test_report_goes_in_a_fresh_private_directory_that_is_removed(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(reports))
    calls = _spy_run(monkeypatch, 1)
    _inconclusive(_test_reverted_candidate(CANDIDATE, [*PYTEST, "t.py"], {}, str(tmp_path)), 1)
    [argv, (_tag, directory, mode, entries)] = calls
    assert argv[-1] == "--junitxml=%s" % os.path.join(directory, "junit.xml")
    assert os.path.dirname(directory) == str(reports)
    assert os.path.basename(directory).startswith("fixval-junit-")
    assert (mode, entries) == (0o700, [])
    assert list(reports.iterdir()) == []


def test_a_report_directory_that_cannot_be_removed_does_not_lose_the_verdict(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(reports))
    real_rmdir = os.rmdir

    def rmdir(path, *args, **kwargs):
        if "fixval-junit-" in str(path):
            raise PermissionError(13, "injected rmdir failure", str(path))
        return real_rmdir(path, *args, **kwargs)

    calls = _spy_run(monkeypatch, 1)
    monkeypatch.setattr(os, "rmdir", rmdir)
    result = _test_reverted_candidate(CANDIDATE, [*PYTEST, "t.py"], {}, str(tmp_path))
    monkeypatch.setattr(os, "rmdir", real_rmdir)
    _inconclusive(result, 1)
    [_argv, (_tag, directory, _mode, _entries)] = calls
    assert os.listdir(directory) == []  # only the empty directory is left behind


def _git(root, *arguments):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_run_fixval_reverted_module_that_breaks_at_import_is_inconclusive(tmp_path):
    # main counted this exit 2 as RED; the fix removed a raise, it added no name the test imports
    _git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/calc.py").write_text("raise RuntimeError('broken')\nvalue = 1\n", encoding="utf-8")
    (tmp_path / "tests/test_calc.py").write_text("def test_placeholder():\n    pass\n", encoding="utf-8")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "src/calc.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests/test_calc.py").write_text(
        "from calc import value\n\ndef test_calc():\n    assert value == 1\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "--all")
    diff = _git(tmp_path, "diff", "--cached")
    candidate = classify_fixval_candidate(["src/calc.py", "tests/test_calc.py"])
    result = run_fixval(candidate, [*PYTEST, "-q"], tmp_path, "fix calc", diff)
    _inconclusive(result, 2)
    assert (tmp_path / "src/calc.py").read_text(encoding="utf-8") == "value = 1\n"
