# SPDX-License-Identifier: Apache-2.0
"""#28: a reverted pytest run is RED only when its JUnit report shows a failed
test, or a collection or setup error importing a name the fix adds."""

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


def _patch(path, removed, added, new_file=False):
    source = "/dev/null" if new_file else "a/" + path
    header = "diff --git a/%s b/%s\n" % (path, path)
    if new_file:
        header += "new file mode 100644\n"
    body = "".join("-%s\n" % line for line in removed) + "".join("+%s\n" % line for line in added)
    hunk = "@@ -%s +1,%d @@\n" % ("0,0" if new_file else "1,%d" % len(removed), len(added))
    return header + "--- %s\n+++ b/%s\n" % (source, path) + hunk + body


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("def helper(x):", {"helper"}),
        ("async def fetch():", {"fetch"}),
        ("class Store:", {"Store"}),
        ("LIMIT = 3", {"LIMIT"}),
        ("LIMIT: int = 3", {"LIMIT"}),
        ("from .impl import run, stop as halt", {"run", "halt"}),
        ("import json", {"json"}),
        ("    def method(self):", set()),
        ("    value = 1", set()),
        ("value == 1", set()),
        ("else: x = 1", set()),
        ("# def commented():", set()),
    ],
)
def test_added_names_reads_module_level_bindings(line, names):
    found = added_names(_patch("src/calc.py", ["pass"], [line]))
    assert {name for _module, name in found.names} == names
    assert found.modules == frozenset()


def test_added_names_maps_paths_to_modules_and_skips_names_that_already_existed():
    patch = _patch("src/calc.py", ["def helper():", "OLD = 1"], ["def helper(x):", "NEW = 2"])
    patch += _patch("lib/pkg/__init__.py", [], ["def entry():"], new_file=True)
    patch += _patch("docs/notes.md", [], ["def nothing():"])
    found = added_names(patch)
    assert found.names == {
        ("src.calc", "NEW"),
        ("calc", "NEW"),
        ("lib.pkg", "entry"),
    }
    assert found.modules == {"lib.pkg"}


def test_unreadable_patch_adds_nothing():
    assert added_names("@@ not a diff\n+def helper():\n") == NOTHING


def _report(*cases):
    return "<testsuites><testsuite>%s</testsuite></testsuites>" % "".join(
        '<testcase classname="t" name="n%d">%s</testcase>' % item for item in enumerate(cases)
    )


def _error(message, text=""):
    return '<error message="%s">%s</error>' % (message, text)


IMPORT_HELPER = "E   ImportError: cannot import name 'helper' from 'calc' (/r/src/calc.py)"


@pytest.mark.parametrize(
    ("report", "added", "red"),
    [
        (_report('<failure message="assert 1 == 2"/>'), NOTHING, True),
        (_report(_error("collection failure", IMPORT_HELPER)), CALC_HELPER, True),
        (_report(_error("failed on setup with &quot;%s&quot;" % IMPORT_HELPER)), CALC_HELPER, True),
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
