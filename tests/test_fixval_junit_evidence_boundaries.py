# SPDX-License-Identifier: Apache-2.0
"""Complete source images and real exception types bound FIXVAL's import evidence."""

from contextlib import contextmanager
import difflib
import os

import pytest

from code_forge._fixval_junit import AddedNames, added_names
from code_forge._fixval_transaction import FixvalTransaction, TransactionError
from code_forge.fixval import FixvalStatus, _reverted_run, classify_fixval_candidate
from tests.test_fixval_junit_witness import (
    CALC_HELPER,
    FIXTURE,
    NOTHING,
    PYTEST,
    _added,
    _git,
    _inconclusive,
    _patch,
    _reverted,
)


@pytest.mark.parametrize("quote", ['"""', "'''"])
@pytest.mark.parametrize("inside", ["def helper(): pass", "helper = 1", "from x import helper"])
def test_string_contents_are_not_added_bindings(quote, inside):
    found = _added("value = 1\n", f"value = 1\nDOC = {quote}\n{inside}\n{quote}\n")
    assert ("calc", "helper") not in found.names
    assert {name for _, name in found.names} == {"DOC"}


def test_hunk_starting_inside_a_docstring_uses_complete_images():
    before = '"""\n' + "description\n" * 20 + '"""\n'
    after = before.replace("description\n", "def helper(): pass\n", 1)
    patch = "diff --git a/src/calc.py b/src/calc.py\n--- a/src/calc.py\n+++ b/src/calc.py\n@@ -2 +2 @@\n-description\n+def helper(): pass\n"
    assert (
        added_names(
            patch, before={"src/calc.py": before.encode()}, after={"src/calc.py": after.encode()}
        )
        == NOTHING
    )


@pytest.mark.parametrize(
    "before",
    [
        "helper = 1\n" + "# outside hunk\n" * 20,
        "if flag:\n    helper = 1\n",
        "def f():\n    helper = 1\n",
        "class C:\n    helper = 1\n",
    ],
)
def test_possible_existing_bindings_do_not_become_new(before):
    found = _added(before, before + "def helper(): pass\n")
    assert ("calc", "helper") not in found.names


@pytest.mark.parametrize(
    "after",
    [
        "helper: int\n",
        "if flag:\n    helper = 1\n",
        "def helper(): pass\ndel helper\n",
        "def helper(): pass\nif flag:\n    del helper\n",
    ],
)
def test_ambiguous_or_removed_bindings_do_not_count(after):
    assert ("calc", "helper") not in _added("", after).names


@pytest.mark.parametrize("missing", ["both", "before", "after", "key"])
def test_missing_complete_images_are_inconclusive(missing):
    patch = _patch("src/calc.py", "pass\n", "def helper(): pass\n")
    kwargs = {"before": {"src/calc.py": b"pass\n"}, "after": {"src/calc.py": b"def helper(): pass\n"}}
    if missing == "both":
        kwargs = {}
    elif missing == "key":
        kwargs["before"] = {}
    else:
        del kwargs[missing]
    assert added_names(patch, **kwargs) == NOTHING


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("bad", [b"def incomplete(:\n", b"\x00", b"# coding: nonexistent\n"])
def test_unparseable_complete_image_is_inconclusive(side, bad):
    kwargs = {"before": {"src/calc.py": b"pass\n"}, "after": {"src/calc.py": b"def helper(): pass\n"}}
    kwargs[side]["src/calc.py"] = bad
    assert added_names(_patch("src/calc.py", "pass\n", "def helper(): pass\n"), **kwargs) == NOTHING


def test_python_encoding_cookie_in_complete_source_is_supported():
    before = b"# coding: latin-1\n# caf\xe9\n"
    after = before + b"def helper(): pass\n"
    found = added_names(
        _patch("src/calc.py", "", "def helper(): pass\n"),
        before={"src/calc.py": before},
        after={"src/calc.py": after},
    )
    assert ("calc", "helper") in found.names


_IMPORT_TEXT = "cannot import name 'helper' from 'calc'"


@pytest.mark.parametrize(
    "message",
    [
        _IMPORT_TEXT,
        "x\nImportError: " + _IMPORT_TEXT,
        "x\nE   ImportError: " + _IMPORT_TEXT,
        "ImportError: " + _IMPORT_TEXT,
        "x\n\nImportError: " + _IMPORT_TEXT,
    ],
)
@pytest.mark.parametrize("phase", ["collection", "setup"])
def test_real_runtime_error_with_import_wording_is_not_red(tmp_path, message, phase):
    statement = "raise RuntimeError(%r)" % message
    if phase == "collection":
        result = _reverted(tmp_path, statement + "\n\ndef test_it():\n    pass\n")
    else:
        result = _reverted(tmp_path, "def test_it(dep):\n    pass\n", conftest=FIXTURE % statement)
    _inconclusive(result, 2 if phase == "collection" else 1)


@pytest.mark.parametrize("phase", ["collection", "setup"])
def test_real_chained_import_then_runtime_error_is_not_red(tmp_path, phase):
    body = "try:\n    from calc import helper\nexcept ImportError as exc:\n    raise RuntimeError('later error') from exc\n"
    if phase == "collection":
        result = _reverted(tmp_path, body + "\ndef test_it():\n    pass\n")
    else:
        fixture = "import pytest\n@pytest.fixture\ndef dep():\n" + "".join(
            "    " + line + "\n" for line in body.splitlines()
        )
        result = _reverted(tmp_path, "def test_it(dep):\n    pass\n", conftest=fixture)
    _inconclusive(result, 2 if phase == "collection" else 1)


@pytest.mark.parametrize("phase", ["collection", "setup"])
@pytest.mark.parametrize(
    "operation,added",
    [
        ("from calc import helper", CALC_HELPER),
        ("import calc; calc.helper", CALC_HELPER),
        ("import missing_added_module", AddedNames(frozenset(), frozenset({"missing_added_module"}))),
    ],
)
def test_real_supported_exception_types_keep_positive_evidence(tmp_path, phase, operation, added):
    if phase == "collection":
        result = _reverted(tmp_path, operation + "\ndef test_it():\n    pass\n", added=added)
    else:
        result = _reverted(
            tmp_path, "def test_it(dep):\n    pass\n", added=added, conftest=FIXTURE % operation
        )
    assert result.status is FixvalStatus.PASS, result.block_message


@pytest.mark.parametrize("operation", ["from calc import helper", "import calc; calc.helper"])
def test_real_teardown_import_or_attribute_error_is_not_red(tmp_path, operation):
    result = _reverted(
        tmp_path, "def test_it(dep):\n    pass\n", conftest=FIXTURE % ("yield\n    " + operation)
    )
    _inconclusive(result, 1)


@contextmanager
def _transaction(root, patch):
    transaction = FixvalTransaction(root, patch)
    transaction.prepare()
    try:
        yield transaction
    finally:
        errors = transaction.restore(False)
        try:
            transaction.close()
        finally:
            assert not errors, errors


def test_transaction_returns_fixed_and_reverted_owned_source_images(tmp_path):
    path = tmp_path / "calc.py"
    before, after = "value = 1\n", "value = 1\ndef helper(): pass\n"
    path.write_text(after)
    patch = _patch("calc.py", before, after)
    patch_path = tmp_path / "change.patch"
    patch_path.write_text(patch)
    with _transaction(tmp_path, patch) as transaction:
        assert transaction.python_sources() == {"calc.py": after.encode()}
        assert transaction.reverse(str(patch_path)).returncode == 0
        transaction.mark_reverted()
        assert transaction.python_sources(reverted=True) == {"calc.py": before.encode()}
        assert transaction.python_sources() == {"calc.py": after.encode()}
    assert path.read_text() == after


@pytest.mark.parametrize("kind", ["symlink", "oversized"])
def test_transaction_does_not_supply_unusable_python_image(tmp_path, kind):
    path = tmp_path / "calc.py"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("def helper(): pass\n")
        path.symlink_to(target)
        patch = "diff --git a/calc.py b/calc.py\nold mode 100644\nnew mode 120000\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-pass\n+target\n"
    else:
        path.write_bytes(b"#" * (1024 * 1024 + 1))
        patch = _patch("calc.py", "pass\n", "# large\n")
    with _transaction(tmp_path, patch) as transaction:
        assert "calc.py" not in transaction.python_sources()


def test_reverse_image_cannot_be_requested_before_reverse(tmp_path):
    (tmp_path / "calc.py").write_text("value = 2\n")
    with _transaction(tmp_path, _patch("calc.py", "value = 1\n", "value = 2\n")) as transaction:
        with pytest.raises(TransactionError, match="reverse image is not available"):
            transaction.python_sources(reverted=True)


@pytest.mark.parametrize(
    "addition,red",
    [('DOC = """\ndef helper(): pass\n"""\n', False), ("def helper(): pass\n", True)],
)
def test_complete_transaction_distinguishes_string_from_binding(tmp_path, addition, red):
    _git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    before = "value = 1\n"
    after = before + addition
    (tmp_path / "src/calc.py").write_text(before)
    (tmp_path / "tests/test_calc.py").write_text("def test_it(): pass\n")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "src/calc.py").write_text(after)
    # Only the real function addition makes this import possible.
    (tmp_path / "tests/test_calc.py").write_text("from calc import helper\ndef test_it(): pass\n")
    _git(tmp_path, "add", "--all")
    diff = _git(tmp_path, "diff", "--cached")
    candidate = classify_fixval_candidate(["src/calc.py", "tests/test_calc.py"])
    # Exercise the revert transaction directly; run_fixval's preliminary green
    # check correctly refuses this test before it reaches the RED phase.

    result = _reverted_run(
        candidate,
        _patch("src/calc.py", before, after),
        [*PYTEST, "tests/test_calc.py"],
        {**os.environ, "PYTHONPATH": str(tmp_path / "src")},
        tmp_path,
        None,
    )
    if red:
        assert result.status is FixvalStatus.PASS, result.block_message
    else:
        _inconclusive(result, 2)
    assert (tmp_path / "src/calc.py").read_text() == after
    assert diff


@pytest.mark.parametrize("source", ["obj.helper = 1\n", "obj[helper] = 1\n"])
def test_assignment_target_loads_do_not_bind_module_names(source):
    assert _added("", source) == NOTHING


@pytest.mark.parametrize(
    "source,names",
    [
        ("one, *rest = items\n", {"one", "rest"}),
        ("one, (two, three) = items\n", {"one", "two", "three"}),
        ("from __future__ import annotations\n", {"annotations"}),
    ],
)
def test_actual_assignment_and_future_import_bindings_are_retained(source, names):
    assert {name for _, name in _added("", source).names} == names


@pytest.mark.parametrize("kind", ["created", "deleted", "renamed"])
def test_transaction_preserves_absence_and_rename_source_images(tmp_path, kind):
    if kind == "created":
        patch = "diff --git a/calc.py b/calc.py\nnew file mode 100644\n--- /dev/null\n+++ b/calc.py\n@@ -0,0 +1 @@\n+value = 2\n"
        (tmp_path / "calc.py").write_text("value = 2\n")
        fixed, reverted = {"calc.py": b"value = 2\n"}, {"calc.py": None}
    elif kind == "deleted":
        patch = "diff --git a/calc.py b/calc.py\ndeleted file mode 100644\n--- a/calc.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n"
        fixed, reverted = {"calc.py": None}, {"calc.py": b"value = 1\n"}
    else:
        patch = "diff --git a/old.py b/calc.py\nsimilarity index 90%\nrename from old.py\nrename to calc.py\n--- a/old.py\n+++ b/calc.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
        (tmp_path / "calc.py").write_text("value = 2\n")
        fixed = {"calc.py": b"value = 2\n", "old.py": None}
        reverted = {"calc.py": None, "old.py": b"value = 1\n"}
    patch_path = tmp_path / "change.patch"
    patch_path.write_text(patch)
    with _transaction(tmp_path, patch) as transaction:
        assert transaction.python_sources() == fixed
        assert transaction.reverse(str(patch_path)).returncode == 0
        transaction.mark_reverted()
        assert transaction.python_sources(reverted=True) == reverted
        assert transaction.python_sources() == fixed


@pytest.mark.parametrize("replacement", ["file", "fifo"])
def test_transaction_rejects_replaced_snapshot_without_blocking(tmp_path, monkeypatch, replacement):
    (tmp_path / "calc.py").write_text("value = 2\n")
    with _transaction(tmp_path, _patch("calc.py", "value = 1\n", "value = 2\n")) as transaction:
        entry = next(entry for entry in transaction.entries if entry.path == "calc.py")
        original = entry.snapshot
        os.rename(
            original,
            "saved-test-original",
            src_dir_fd=transaction.saved_fd,
            dst_dir_fd=transaction.saved_fd,
        )
        try:
            if replacement == "file":
                fd = os.open(
                    original, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=transaction.saved_fd
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"def helper(): pass\n")
            else:
                os.mkfifo(original, dir_fd=transaction.saved_fd)
                # Guard the actual open flags, failing before opening the FIFO
                # if the nonblocking requirement regresses.
                real_open = os.open

                def checked_open(path, flags, *args, **kwargs):
                    if path == original and kwargs.get("dir_fd") == transaction.saved_fd:
                        assert flags & os.O_NONBLOCK
                    return real_open(path, flags, *args, **kwargs)

                monkeypatch.setattr(os, "open", checked_open)
            with pytest.raises(TransactionError):
                transaction.python_sources()
        finally:
            if replacement == "fifo":
                monkeypatch.setattr(os, "open", real_open)
            os.unlink(original, dir_fd=transaction.saved_fd)
            os.rename(
                "saved-test-original",
                original,
                src_dir_fd=transaction.saved_fd,
                dst_dir_fd=transaction.saved_fd,
            )


@pytest.mark.parametrize(
    "before_source,expected_names",
    [
        (b"def helper(): pass\n", frozenset()),
        (b"value = 1\n", frozenset({("calc", "helper")})),
    ],
)
def test_rename_with_shared_module_alias_compares_original_bindings(before_source, expected_names):
    patch = _rename_patch("src/calc.py", "calc.py", before_source, b"def helper(): pass\n")
    found = added_names(
        patch,
        before={"src/calc.py": before_source, "calc.py": None},
        after={"src/calc.py": None, "calc.py": b"def helper(): pass\n"},
    )
    assert found == AddedNames(expected_names, frozenset())


@pytest.mark.parametrize("old_image", ["missing", None, b"def broken("])
def test_rename_with_shared_module_alias_requires_usable_original_image(old_image):
    before = {"calc.py": None}
    if old_image != "missing":
        before["src/calc.py"] = old_image
    found = added_names(
        _rename_patch("src/calc.py", "calc.py", b"value = 1\n", b"def helper(): pass\n"),
        before=before,
        after={"calc.py": b"def helper(): pass\n"},
    )
    assert found == NOTHING


def test_rename_to_distinct_module_alias_still_introduces_module_and_binding():
    source = b"def helper(): pass\n"
    found = added_names(
        _rename_patch("src/calc.py", "newcalc.py", source, source),
        before={"src/calc.py": source, "newcalc.py": None},
        after={"src/calc.py": None, "newcalc.py": source},
    )
    assert found == AddedNames(frozenset({("newcalc", "helper")}), frozenset({"newcalc"}))


def _rename_patch(source, target, before, after):
    return f"diff --git a/{source} b/{target}\nrename from {source}\nrename to {target}\n" + "".join(
        difflib.unified_diff(
            before.decode().splitlines(keepends=True),
            after.decode().splitlines(keepends=True),
            fromfile=f"a/{source}",
            tofile=f"b/{target}",
        )
    )


@pytest.mark.parametrize(
    "prior",
    [
        "for helper in items:\n    pass\n",
        "with manager() as helper:\n    pass\n",
        "if (helper := value):\n    pass\n",
        "match value:\n    case helper:\n        pass\n",
        "match value:\n    case [*helper]:\n        pass\n",
        "match value:\n    case {**helper}:\n        pass\n",
        "try:\n    action()\nexcept Exception as helper:\n    pass\n",
        "def existing(helper):\n    pass\n",
    ],
    ids=["for", "with", "walrus", "match-as", "match-star", "match-rest", "except", "argument"],
)
@pytest.mark.parametrize("renamed", [False, True], ids=["same-path", "shared-rename-alias"])
def test_all_possible_prior_bindings_exclude_new_binding_claim(prior, renamed):
    # This is the conservative AddedNames API contract, not a claim that all
    # possible prior bindings execute or are module-visible at runtime.
    after = prior + "def helper(): pass\n"
    if renamed:
        found = added_names(
            _rename_patch("src/calc.py", "calc.py", prior.encode(), after.encode()),
            before={"src/calc.py": prior.encode(), "calc.py": None},
            after={"src/calc.py": None, "calc.py": after.encode()},
        )
    else:
        found = _added(prior, after)
    assert not any(name == "helper" for _, name in found.names)
    assert found.modules == frozenset()


@pytest.mark.parametrize("renamed", [False, True], ids=["same-path", "shared-rename-alias"])
def test_prior_star_import_makes_added_binding_claim_uncertain(renamed):
    prior = b"from provider import *\n"
    after = prior + b"def helper(): pass\n"
    if renamed:
        found = added_names(
            _rename_patch("src/calc.py", "calc.py", prior, after),
            before={"src/calc.py": prior, "calc.py": None},
            after={"src/calc.py": None, "calc.py": after},
        )
    else:
        found = _added(prior.decode(), after.decode())
    assert found == NOTHING


def test_prior_star_import_does_not_erase_distinct_new_module_evidence():
    prior = b"from provider import *\n"
    after = prior + b"def helper(): pass\n"
    found = added_names(
        _rename_patch("src/calc.py", "newcalc.py", prior, after),
        before={"src/calc.py": prior, "newcalc.py": None},
        after={"src/calc.py": None, "newcalc.py": after},
    )
    assert found.modules == frozenset({"newcalc"})
