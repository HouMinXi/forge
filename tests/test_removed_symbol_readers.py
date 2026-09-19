# SPDX-License-Identifier: Apache-2.0
"""Phase 59-A4-0b: the falsifier is told what the removed lines still feed.

A2 showed the judge blind (8/20); A4-0 gave it the diff (12/20). The three
bug-side misses left (c06 c08 c09) share a shape: a refactor-looking
change removes an identifier that other code in the same tree still reads.
The judge cannot see those readers. Probed on real trees 2026-09-05:
`store_cv_values` dropped from RidgeClassifierCV.__init__ while ridge.py
keeps six reads of self.store_cv_values; `note_object` removed while five
non-test files still call it. One grep each.

RemovedSymbolReaders is a ContextSource that, for each identifier removed
by the diff, lists the post-image files that still reference it. Working
tree only, no index, no snapshot gate (snapshot_sha returns None).
"""
from __future__ import annotations

import ast
import difflib
import subprocess
from pathlib import Path

import pytest

from code_forge.context_sources import (
    FactRow,
    RemovedSymbolReaders,
    gather,
    _removed_identifiers_by_file,
    _looks_like_code,
)
from code_forge.falsify_real import RealFalsifier
from code_forge.state import Disposition, StateFinding


DIFF = """\
diff --git a/pkg/ridge.py b/pkg/ridge.py
--- a/pkg/ridge.py
+++ b/pkg/ridge.py
@@ -10,4 +10,3 @@ class RidgeClassifierCV:
     def __init__(self, alphas=(0.1, 1.0, 10.0), fit_intercept=True,
-                 normalize=False, scoring=None, cv=None, class_weight=None,
-                 store_cv_values=False):
+                 normalize=False, scoring=None, cv=None, class_weight=None):
         super().__init__(alphas=alphas)
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Post-image tree: the parameter is gone from __init__, fit() still
    reads it, a test file also reads it (must be labelled), an unrelated
    file mentions the word inside a comment (must not count)."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ridge.py").write_text(
        "class RidgeClassifierCV:\n"
        "    def __init__(self, alphas=(0.1, 1.0, 10.0), fit_intercept=True,\n"
        "                 normalize=False, scoring=None, cv=None, class_weight=None):\n"
        "        super().__init__(alphas=alphas)\n"
        "\n"
        "    def fit(self, X, y):\n"
        "        if self.store_cv_values:\n"
        "            self.cv_values_ = 1\n"
        "        return self\n")
    (tmp_path / "pkg" / "other.py").write_text(
        "# store_cv_values is documented elsewhere\n"
        "def helper():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ridge.py").write_text(
        "def test_it():\n    r = RidgeClassifierCV(store_cv_values=True)\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "post"], cwd=tmp_path, check=True)
    return tmp_path


def test_removed_identifier_with_live_readers_is_a_fact(tree: Path):
    rows = RemovedSymbolReaders(tree).facts(["pkg/ridge.py"], DIFF)
    by_entity = {r.entity: r for r in rows}
    assert "store_cv_values" in by_entity
    row = by_entity["store_cv_values"]
    assert row.source == "removed-symbol-readers"
    assert row.file == "pkg/ridge.py"
    # fit() reads it; the comment in other.py is not a read.
    assert "pkg/ridge.py:7" in row.dependents
    assert "other.py" not in row.dependents


def test_test_files_are_labelled_not_dropped(tree: Path):
    rows = RemovedSymbolReaders(tree).facts(["pkg/ridge.py"], DIFF)
    row = next(r for r in rows if r.entity == "store_cv_values")
    # The judge should know a test still passes the argument, and that it
    # is a test: that is different evidence from a production read.
    assert "tests/test_ridge.py:2 (test)" in row.dependents


def test_identifiers_with_no_readers_are_not_facts(tree: Path):
    # `class_weight` was on a removed line but is re-added on the + side and
    # still read; `normalize` likewise. Only identifiers that vanish from
    # the post-image AND are still read elsewhere are worth a row.
    rows = RemovedSymbolReaders(tree).facts(["pkg/ridge.py"], DIFF)
    entities = {r.entity for r in rows}
    assert "normalize" not in entities
    assert "class_weight" not in entities
    assert "scoring" not in entities


def test_short_and_common_tokens_are_ignored(tmp_path: Path):
    # `x` (1 char), `ab` (2), `abc` (3) are below the 4-char floor even
    # though every one of them survives in b.py; `pass` is a keyword.
    (tmp_path / "a.py").write_text("x = 1\nab = 2\nabc = 3\n")
    (tmp_path / "b.py").write_text("y = x + ab + abc\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,3 +1,1 @@\n-x = 1\n-ab = 2\n-abc = 3\n-if x:\n"
            "-    pass\n+pass\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["a.py"], diff)
    assert rows == []


def test_source_has_no_snapshot(tree: Path):
    src = RemovedSymbolReaders(tree)
    assert src.snapshot_sha() is None
    r = gather([src], ["pkg/ridge.py"], DIFF, head_sha="abc")
    assert r.errors == [] and r.skipped == []
    assert any(row.source == "removed-symbol-readers" for row in r.rows)


def test_vocabulary_tokens_are_dropped_and_rows_are_fewest_first(tmp_path: Path):
    """On real trees the removed line's English words (`boolean`,
    `values`) and package names (`astropy`, 7048 hits) crowded out the
    one identifier that mattered (`store_cv_values`, 6 hits). Anything
    over max_readers_to_report is vocabulary; what remains is ordered so
    the rarest name, the likeliest API, comes first."""
    (tmp_path / "m.py").write_text(
        "\n".join("v%d = values" % i for i in range(3))
        + "\nrare_name = 1\n")
    (tmp_path / "n.py").write_text("print(rare_name)\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,1 +1,1 @@\n-x = values + rare_name\n+x = 0\n")
    src = RemovedSymbolReaders(tmp_path, max_readers_to_report=2)
    rows = src.facts(["m.py"], diff)
    assert [r.entity for r in rows] == ["rare_name"]
    src = RemovedSymbolReaders(tmp_path, max_readers_to_report=10)
    rows = src.facts(["m.py"], diff)
    assert [r.entity for r in rows] == ["rare_name", "values"]


# ---- the falsifier receives it ------------------------------------------

def _capture(fals, finding):
    seen = {}

    def fake(prompt, **kw):
        seen["prompt"] = prompt
        from types import SimpleNamespace
        return SimpleNamespace(content={"verdict": "DISMISSED", "reasoning": "r"})
    import code_forge.falsify_real as fr
    old = fr.llm_invoke
    fr.llm_invoke = fake
    try:
        fals.falsify(finding)
    finally:
        fr.llm_invoke = old
    return seen["prompt"]


def test_falsifier_prompt_carries_the_readers(tree: Path):
    rows = RemovedSymbolReaders(tree).facts(["pkg/ridge.py"], DIFF)
    f = StateFinding(id="x", fingerprint="x", source="L1",
                     disposition=Disposition.CONFIRMED,
                     file="pkg/ridge.py", line_range=[10, 12],
                     description="store_cv_values dropped but fit still reads it")
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF, context_rows=rows), f)
    assert "store_cv_values" in p
    assert "pkg/ridge.py:7" in p
    assert "still referenced" in p.lower() or "still read" in p.lower()
    # Rows for files the finding is not about are not in this prompt.
    assert "tests/test_ridge.py" in p  # same identifier, labelled test


def test_falsifier_without_rows_is_byte_identical_to_a4_0(tree: Path):
    """No rows, no change: the A4-0 prompt stays exactly what it was."""
    f = StateFinding(id="y", fingerprint="y", source="L1",
                     disposition=Disposition.CONFIRMED,
                     file="pkg/ridge.py", line_range=[10, 12], description="d")
    a = _capture(RealFalsifier(backend=None, diff_text=DIFF), f)
    b = _capture(RealFalsifier(backend=None, diff_text=DIFF, context_rows=[]), f)
    assert a == b
    # And neither carries the section: a==b alone is satisfied by both
    # sides emitting an empty header (injection I6 stayed green on it).
    assert "still referenced" not in a.lower()
    assert a.rstrip().endswith("[ 13]     super().__init__(alphas=alphas)") \
        or a.count("\n## ") == 1  # exactly the diff section


# ---- wiring ---------------------------------------------------------------

def test_factory_threads_context_rows():
    from code_forge.factories import build_falsifier
    rows = [FactRow(entity="e", file="f.py", downstream="1",
                    dependents="g.py:3", source="removed-symbol-readers")]
    f = build_falsifier("real", backend=None, diff_text="", context_rows=rows)
    assert f._context_rows == rows


def test_cli_gathers_readers_and_hands_rows_to_the_falsifier():
    """cli.py must (a) put RemovedSymbolReaders in the gather() source
    list next to GraphTriageSource and (b) pass _ctx.rows to
    build_falsifier. Both are one-line omissions that leave every test
    green and the judge blind again."""
    src = (Path(__file__).resolve().parents[1] / "src" / "code_forge" / "cli.py").read_text()
    i = src.index("_ctx = gather(")
    j = src.index("build_falsifier(", i)
    assert "RemovedSymbolReaders(cwd)" in src[i:j]
    k = src.index(")", j)
    assert "context_rows=" in src[j:k]


def test_l1_prompt_does_not_see_reader_rows():
    """render_context_sources feeds the L1 prompt. Reader rows are
    falsifier evidence, not L1 context; leaking them would change the
    L1 byte-identity oracle (24ed2e0) and double their token cost."""
    from code_forge.context_sources import GatherResult, render_context_sources
    r = GatherResult(rows=[FactRow(entity="e", file="f.py", downstream="1",
                                   dependents="g.py:3",
                                   source="removed-symbol-readers")])
    assert render_context_sources(r) == ""


# ---- rule 2: a constructor parameter dropped while self.<name> is still read

DIFF_C08 = """\
diff --git a/pkg/ridge.py b/pkg/ridge.py
--- a/pkg/ridge.py
+++ b/pkg/ridge.py
@@ -3,6 +3,6 @@ class RidgeClassifierCV(_BaseRidgeCV):
     \"\"\"Ridge classifier with built-in cross-validation.
 
-    store_cv_values : boolean, default=False
-        Flag indicating if the cross-validation values corresponding to
+    Cross-validation values for each alpha (if `store_cv_values=True` and
+        `cv=None`). This attribute exists only when store_cv_values is True.
     \"\"\"
     def __init__(self, alphas=(0.1, 1.0, 10.0), fit_intercept=True,
-                 normalize=False, scoring=None, cv=None, class_weight=None,
-                 store_cv_values=False):
+                 normalize=False, scoring=None, cv=None, class_weight=None):
         super().__init__(alphas=alphas, fit_intercept=fit_intercept,
-            normalize=normalize, scoring=scoring, cv=cv,
-            store_cv_values=store_cv_values)
+            normalize=normalize, scoring=scoring, cv=cv)
"""


@pytest.fixture
def tree_c08(tmp_path: Path) -> Path:
    """The real c08 shape: the name survives on a + line (docstring) so
    rule 1 says 'not removed', but the parameter left the signature and
    fit() still reads self.store_cv_values."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ridge.py").write_text(
        "class RidgeClassifierCV(_BaseRidgeCV):\n"
        "    \"\"\"Ridge classifier with built-in cross-validation.\n"
        "\n"
        "    Cross-validation values for each alpha (if `store_cv_values=True` and\n"
        "        `cv=None`). This attribute exists only when store_cv_values is True.\n"
        "    \"\"\"\n"
        "    def __init__(self, alphas=(0.1, 1.0, 10.0), fit_intercept=True,\n"
        "                 normalize=False, scoring=None, cv=None, class_weight=None):\n"
        "        super().__init__(alphas=alphas, fit_intercept=fit_intercept,\n"
        "            normalize=normalize, scoring=scoring, cv=cv)\n"
        "\n"
        "    def fit(self, X, y):\n"
        "        if self.store_cv_values:\n"
        "            self.cv_values_ = 1\n"
        "        if self.scoring is None and self.cv is None:\n"
        "            pass\n"
        "        return self\n"
        "\n"
        "class Other:\n"
        "    def __init__(self):\n"
        "        self.gone_param = 0\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "post"], cwd=tmp_path, check=True)
    return tmp_path


def test_dropped_parameter_with_live_self_read_is_a_fact(tree_c08: Path):
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], DIFF_C08)
    by = {r.entity: r for r in rows}
    assert "store_cv_values" in by, [r.entity for r in rows]
    row = by["store_cv_values"]
    assert row.source == "removed-symbol-readers"
    assert "parameter" in row.dependents  # the row says what kind of removal
    assert "pkg/ridge.py:13" in row.dependents  # the self. read in fit()


def test_parameter_still_in_signature_is_not_a_fact(tree_c08: Path):
    # `scoring` and `cv` were on removed lines, are re-added to the
    # signature, AND fit() reads self.scoring / self.cv: rule 2 must
    # look at the + side of the signature, not just the - side.
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], DIFF_C08)
    assert {r.entity for r in rows} == {"store_cv_values"}


def test_self_assignment_is_not_a_read(tree_c08: Path):
    # `gone_param` leaves a signature and the only surviving self.gone_param
    # is the constructor's own assignment: a write, not a dependency.
    diff = ("diff --git a/pkg/ridge.py b/pkg/ridge.py\n"
            "--- a/pkg/ridge.py\n+++ b/pkg/ridge.py\n"
            "@@ -17,2 +17,2 @@ class Other:\n"
            "-    def __init__(self, gone_param=0):\n"
            "+    def __init__(self):\n"
            "         self.gone_param = 0\n")
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], diff)
    assert rows == []


def test_dropped_parameter_with_no_self_read_is_not_a_fact(tmp_path: Path):
    (tmp_path / "a.py").write_text(
        "class A:\n    def __init__(self, x):\n        self.x = x\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -2,2 +2,2 @@ class A:\n"
            "-    def __init__(self, x, unused_flag=False):\n"
            "+    def __init__(self, x):\n"
            "         self.x = x\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["a.py"], diff)
    assert rows == []


def test_removed_prose_lines_are_not_mined_for_identifiers(tmp_path: Path):
    """A docstring line removed from the file is not an API removal. On
    real trees `values`, `cross`, `indicating` from removed prose each had
    dozens of readers and buried the one identifier that mattered."""
    (tmp_path / "m.py").write_text(
        "def f():\n    return indicating\n"
        "def g():\n    return each\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-        Flag indicating if the values for each alpha (see below).\n"
            "-    x = 1\n+    x = 2\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["m.py"], diff)
    assert rows == []


# ---- A4-0c: readers carry the lines, not just the addresses -----------------

def test_reader_rows_carry_post_image_snippets(tree_c08: Path):
    """A4-0b measured 13/20 and the judge on c08 said it: 'the diff alone
    cannot confirm the parent class's signature... without verifying the
    parent class's code the runtime error cannot be proven or disproven.'
    It had ridge.py:1077 as an address and nothing behind the door. Each
    reader now carries the line itself."""
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], DIFF_C08)
    row = next(r for r in rows if r.entity == "store_cv_values")
    assert row.snippets, "no snippets attached"
    # keyed by location, value is the post-image source line
    assert row.snippets["pkg/ridge.py:13"].strip() == "if self.store_cv_values:"


def test_snippets_include_the_enclosing_def_line(tree_c08: Path):
    """A read inside fit() only means something with 'def fit' visible:
    the judge's question on c08 was which function still depends on the
    parameter. One extra line per reader, the nearest def/class above."""
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], DIFF_C08)
    row = next(r for r in rows if r.entity == "store_cv_values")
    assert row.enclosing["pkg/ridge.py:13"] == "def fit(self, X, y):"


def test_enclosing_is_the_outer_def_not_a_nested_helper(tmp_path: Path):
    """Real sklearn tree: the read at ridge.py:1077 sits in fit() after a
    nested `def identity_estimator()`; nearest-def-above named the
    helper. The enclosing def is the nearest header with LESS
    indentation than the read."""
    (tmp_path / "m.py").write_text(
        "class C:\n"
        "    def fit(self):\n"
        "        def helper():\n"
        "            return 1\n"
        "        if self.victim:\n"
        "            pass\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
            "@@ -2,1 +2,1 @@ class C:\n"
            "-    def __init__(self, victim=0):\n"
            "+    def __init__(self):\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["m.py"], diff)
    row = next(r for r in rows if r.entity == "victim")
    assert row.enclosing["m.py:5"] == "def fit(self):"


def test_falsifier_prompt_shows_the_lines(tree_c08: Path):
    rows = RemovedSymbolReaders(tree_c08).facts(["pkg/ridge.py"], DIFF_C08)
    f = StateFinding(id="c", fingerprint="c", source="L1",
                     disposition=Disposition.CONFIRMED,
                     file="pkg/ridge.py", line_range=[7, 10],
                     description="store_cv_values dropped")
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF_C08, context_rows=rows), f)
    assert "pkg/ridge.py:13" in p
    assert "if self.store_cv_values:" in p
    assert "def fit(self, X, y):" in p


def test_snippet_budget_is_bounded(tmp_path: Path):
    """Twelve readers max per symbol, one line each plus its def: the
    section cannot grow past a few hundred tokens per identifier."""
    (tmp_path / "m.py").write_text(
        "def f():\n" + "".join("    y%d = victim\n" % i for i in range(40)))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/n.py b/n.py\n--- a/n.py\n+++ b/n.py\n"
            "@@ -1,1 +1,1 @@\n-victim = 1\n+other = 1\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["n.py"], diff)
    row = next(r for r in rows if r.entity == "victim")
    # rule-1 rows carry snippets too (injection I17: dropping them from
    # rule 1 alone left every test green)
    assert len(row.snippets) == 12
    assert row.snippets["m.py:2"].strip() == "y0 = victim"
    assert row.enclosing["m.py:2"] == "def f():"
    assert "(+28 more)" in row.dependents


# ---- review R2 (3dc47ce): two real gaps, one pin --------------------------

def test_typed_parameters_are_seen_when_dropped(tmp_path: Path):
    """_PARAM only matched `name=`, `name,`, `name)`; a typed parameter
    `name: int = 3` was invisible, so rule 2 never fired on annotated
    code, which is most code written after 2018."""
    (tmp_path / "a.py").write_text(
        "class A:\n    def __init__(self, x):\n        self.x = x\n"
        "    def go(self):\n        return self.typed_one\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -2,1 +2,1 @@ class A:\n"
            "-    def __init__(self, x, typed_one: int = 3):\n"
            "+    def __init__(self, x):\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["a.py"], diff)
    assert [r.entity for r in rows] == ["typed_one"]
    # and it is rule 2 that saw it (rule 1 also catches this fixture; the
    # rule-2 row is the one that names the signature)
    assert "parameter removed from signature" in rows[0].dependents
    from code_forge.context_sources import _dropped_parameters_by_file
    assert _dropped_parameters_by_file(diff) == {"a.py": {"typed_one"}}


def test_return_and_yield_lines_are_code(tmp_path: Path):
    """`return victim` has none of = ( . : [ and was classified as prose;
    the identifier on it was never grepped."""
    (tmp_path / "a.py").write_text("def g():\n    return 1\n")
    (tmp_path / "b.py").write_text("y = victim_name\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -2,1 +2,1 @@ def g():\n"
            "-    return victim_name\n+    return 1\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["a.py"], diff)
    assert [r.entity for r in rows] == ["victim_name"]


def test_deleted_file_is_not_attributed_to_dev_null(tmp_path: Path):
    """+++ /dev/null: the removed identifiers belong to the deleted file,
    which has no post-image readers to report. Pin: no row, no crash."""
    (tmp_path / "keep.py").write_text("z = gone_fn()\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=tmp_path, check=True)
    diff = ("diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-def gone_fn():\n-    return 1\n")
    rows = RemovedSymbolReaders(tmp_path).facts(["old.py"], diff)
    assert all(r.file != "/dev/null" for r in rows)


# ---- review R3 (8f3b741) ----------------------------------------------------

def test_fact_row_stays_hashable_with_snippets():
    """frozen=True generates __hash__ from every field; two plain dict
    fields made every FactRow unhashable, including graph_triage rows
    that never carry snippets (flagged three rounds running)."""
    r = FactRow(entity="e", file="f", downstream="1", dependents="d", source="s",
                snippets={"f:1": "x = 1"}, enclosing={"f:1": "def g():"})
    assert hash(r) == hash(FactRow(entity="e", file="f", downstream="1",
                                   dependents="d", source="s",
                                   snippets={"f:1": "x = 1"},
                                   enclosing={"f:1": "def g():"}))
    assert len({r, r}) == 1
    assert r.snippets["f:1"] == "x = 1"  # still a mapping to callers


def test_git_failure_in_readers_is_raised_not_emptied(tmp_path: Path):
    """Module contract, docstring lines 13-14: a provider that raises is
    recorded as an error, not swallowed into an empty table. _readers
    returned [] on OSError, which reads as 'no readers' -- the one
    answer that must never be fabricated. tmp_path is not a git repo,
    so git grep fails; that must surface through gather() as an error."""
    (tmp_path / "a.py").write_text("x = victim_name\n")
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n-y = victim_name\n+y = 1\n")
    src = RemovedSymbolReaders(tmp_path)
    r = gather([src], ["a.py"], diff, head_sha=None, allow_unsnapshotted=True)
    assert r.errors, "git failure was swallowed into an empty table"
    assert "removed-symbol-readers" in r.errors[0]


# ---- review R4 (2d6d58f) ----------------------------------------------------

def test_deleted_file_keyed_by_its_real_path_not_dev_null():
    """`+++ /dev/null` is what git writes for a deleted file. Keying the
    removed identifiers under '/dev/null' means facts() skips them (the
    changed_files guard sees a path that was never changed) and a
    deleted module's readers are never reported -- the one case where
    every remaining reader is broken by construction."""
    diff = ("diff --git a/pkg/gone.py b/pkg/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/pkg/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-def helper_that_was_deleted():\n"
            "-    return 1\n")
    out = _removed_identifiers_by_file(diff)
    assert "/dev/null" not in out
    assert "helper_that_was_deleted" in out.get("pkg/gone.py", set())


def test_prose_heuristic_does_not_swallow_a_call_with_spaces(tmp_path):
    """`foo(a, b, c)` has three lowercase words separated by spaces on
    either side of the punctuation. The prose guard must key on the
    absence of code punctuation, not on the presence of word runs."""
    assert _looks_like_code("result = combine(alpha, beta, gamma)")
    assert _looks_like_code("    self.value = compute(one two three)")
    assert not _looks_like_code("    the quick brown fox jumps over")


# ---- review R5 (3095eab) ----------------------------------------------------

def test_grep_line_split_survives_colon_in_path(tmp_path):
    """git grep prints path:lineno:text. A path with ':' in it (Windows
    drive, or a POSIX file literally named 'a:b.py') must not shift the
    line number into the path. Split from the right of the lineno
    field, not the left of the path."""
    from code_forge.context_sources import _split_grep_line
    assert _split_grep_line("a:b.py:12:    x = 1") == ("a:b.py", 12, "    x = 1")
    assert _split_grep_line("C:/w/f.py:7:self.x = y") == ("C:/w/f.py", 7, "self.x = y")
    assert _split_grep_line("f.py:3:url = 'http://h:80'") == ("f.py", 3, "url = 'http://h:80'")


def test_statement_keyword_followed_by_prose_is_not_code():
    """`return the computed value` is a docstring sentence that starts
    with a keyword; `return value` is code."""
    assert _looks_like_code("return value")
    assert _looks_like_code("raise ValueError")
    assert not _looks_like_code("return the computed value here")
    assert not _looks_like_code("assert that the caller has closed")


def test_params_ignore_parens_inside_string_defaults():
    """A default like sep=')' must not close the signature early and
    drop the parameters after it."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ("+++ b/a.py\n@@ -1,4 +1,3 @@\n"
            "-def f(self, sep=')',\n"
            "-      victim_name=None):\n"
            "+def f(self, sep=')'):\n"
            "-    self.victim_name = victim_name\n")
    out = _dropped_parameters_by_file(diff)
    assert "victim_name" in out.get("a.py", set())


# ---- review R6 (ea5096c) ----------------------------------------------------

def test_default_values_are_not_parameters():
    """`def f(a=some_var)` -> `def f(a=other_var)` drops no parameter.
    _PARAM matched any name followed by `=`, so a default's VALUE was
    read as a parameter and its replacement reported as a drop."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ("+++ b/a.py\n@@ -1,2 +1,2 @@\n"
            "-def f(self, alpha=some_var):\n"
            "+def f(self, alpha=other_var):\n"
            "-    self.some_var = 1\n")
    assert _dropped_parameters_by_file(diff) == {}


def test_typed_param_with_default_is_a_parameter():
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ("+++ b/a.py\n@@ -1,2 +1,2 @@\n"
            "-def f(self, victim_name: str = 'x', keep: int = 3):\n"
            "+def f(self, keep: int = 3):\n"
            "-    self.victim_name = victim_name\n")
    assert _dropped_parameters_by_file(diff) == {"a.py": {"victim_name"}}


def test_signature_end_is_the_colon_not_a_balanced_line():
    """A multi-line signature whose first line balances its own parens
    (an annotation like `Dict[str, int]` or a default `f()`) has not
    ended; the parameters on the next line are still parameters."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ("+++ b/a.py\n@@ -1,4 +1,3 @@\n"
            "-def f(self, cb=noop(),\n"
            "-      victim_name=None):\n"
            "+def f(self, cb=noop()):\n"
            "-    self.victim_name = victim_name\n")
    assert _dropped_parameters_by_file(diff) == {"a.py": {"victim_name"}}


# ---- review R7 (a70881b fix) ------------------------------------------------

def test_lines_at_handles_path_with_spaces_and_colons(tmp_path: Path):
    """Paths containing spaces or colons must not lose their line number
    or fail snippet/enclosing extraction. Only the protocol suffix ' (test)'
    may be stripped."""
    d = tmp_path / "my tests" / "sub:dir"
    d.mkdir(parents=True)
    target = d / "test sample.py"
    target.write_text(
        "class OuterClass:\n"
        "    def enclosing_fn(self):\n"
        "        x = 1\n"
        "        victim_call()\n"
        "        return x\n"
    )
    readers = RemovedSymbolReaders(tmp_path)
    rel_path = "my tests/sub:dir/test sample.py"

    # With protocol ' (test)' suffix
    loc_test = f"{rel_path}:4 (test)"
    snip, encl = readers._lines_at([loc_test])
    assert snip.get(f"{rel_path}:4") == "        victim_call()"
    assert encl.get(f"{rel_path}:4") == "def enclosing_fn(self):"

    # Without ' (test)' suffix
    loc_prod = f"{rel_path}:4"
    snip2, encl2 = readers._lines_at([loc_prod])
    assert snip2.get(f"{rel_path}:4") == "        victim_call()"
    assert encl2.get(f"{rel_path}:4") == "def enclosing_fn(self):"


def test_dropped_parameters_survives_escaped_quotes_and_parens():
    """An escaped quote inside a string default like sep=\"escaped \\\" quote )\" must
    not end the string early or balance parens incorrectly."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ('+++ b/a.py\n@@ -1,3 +1,2 @@\n'
            '-def f(self, sep="escaped \\" quote )",\n'
            '-      victim_param=None):\n'
            '+def f(self, sep="escaped \\" quote )"):\n'
            '-    self.victim_param = victim_param\n')
    out = _dropped_parameters_by_file(diff)
    assert out == {"a.py": {"victim_param"}}


def test_dropped_parameters_survives_multiline_triple_quotes():
    """Multiline triple-quoted default string containing parens must not
    close signature depth early or misidentify words as parameters."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = ("+++ b/a.py\n@@ -1,4 +1,3 @@\n"
            "-def f(self, doc='''multi (\n"
            "-      line )''',\n"
            "-      victim_param=None):\n"
            "+def f(self, doc='''multi line'''):\n"
            "-    self.victim_param = victim_param\n")
    out = _dropped_parameters_by_file(diff)
    assert out == {"a.py": {"victim_param"}}


# ---- A4 closure: F-01 / F-02 ----------------------------------------------

def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "p"], cwd=root, check=True)


def test_readers_keep_deleted_file_when_changed_files_is_additions_only(
        tmp_path: Path):
    """get_changed_files lists only files with an added line. A mixed
    diff that edits a.py and deletes b.py therefore hands facts()
    ['a.py']. The deleted file's identifiers still have live readers
    and must not be dropped by that secondary filter."""
    from code_forge.diff import get_changed_files
    (tmp_path / "a.py").write_text("def keep():\n    return 1\n")
    (tmp_path / "caller.py").write_text("from b import deleted_fn\nx = deleted_fn()\n")
    _init_git(tmp_path)
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def keep():\n"
        "-    return 1\n"
        "+    return 2\n"
        "+\n"
        "diff --git a/b.py b/b.py\n"
        "deleted file mode 100644\n"
        "--- a/b.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def deleted_fn():\n"
        "-    return 1\n"
    )
    changed = get_changed_files(diff)
    assert changed == ["a.py"]
    rows = RemovedSymbolReaders(tmp_path).facts(changed, diff)
    by_entity = {r.entity: r for r in rows}
    assert "deleted_fn" in by_entity
    assert by_entity["deleted_fn"].file == "b.py"
    assert "caller.py:2" in by_entity["deleted_fn"].dependents


def test_readers_keep_pure_deletion_file_in_mixed_diff(tmp_path: Path):
    """A file that only loses lines (no + line) is also absent from
    get_changed_files. Mixed with an addition elsewhere, its removed
    identifiers must still become facts."""
    from code_forge.diff import get_changed_files
    (tmp_path / "a.py").write_text("def keep():\n    return 1\n")
    (tmp_path / "c.py").write_text(
        "def leftover():\n    return 1\n\ndef dropped_helper():\n    return 1\n"
    )
    (tmp_path / "caller.py").write_text("from c import dropped_helper\ny = dropped_helper()\n")
    _init_git(tmp_path)
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def keep():\n"
        "     return 1\n"
        "+\n"
        "diff --git a/c.py b/c.py\n"
        "--- a/c.py\n"
        "+++ b/c.py\n"
        "@@ -3,3 +3,0 @@ def leftover():\n"
        "-\n"
        "-def dropped_helper():\n"
        "-    return 1\n"
    )
    changed = get_changed_files(diff)
    assert changed == ["a.py"]
    rows = RemovedSymbolReaders(tmp_path).facts(changed, diff)
    by_entity = {r.entity: r for r in rows}
    assert "dropped_helper" in by_entity
    assert by_entity["dropped_helper"].file == "c.py"
    assert "caller.py:2" in by_entity["dropped_helper"].dependents


def test_readers_empty_changed_files_still_reports_deleted_symbol(tmp_path: Path):
    """The same mixed diff with an empty changed_files list already
    reports the deleted symbol. The additions-only list must not
    shrink that result."""
    (tmp_path / "a.py").write_text("def keep():\n    return 1\n")
    (tmp_path / "caller.py").write_text("from b import deleted_fn\nx = deleted_fn()\n")
    _init_git(tmp_path)
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def keep():\n"
        "-    return 1\n"
        "+    return 2\n"
        "+\n"
        "diff --git a/b.py b/b.py\n"
        "deleted file mode 100644\n"
        "--- a/b.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def deleted_fn():\n"
        "-    return 1\n"
    )
    empty = RemovedSymbolReaders(tmp_path).facts([], diff)
    assert any(r.entity == "deleted_fn" for r in empty)


def test_dropped_parameter_is_scoped_to_its_function():
    """A context line of func_b(victim=1) must not cancel a drop of
    victim from func_a in the same file. Whole-file set subtraction
    loses function identity."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,6 +1,6 @@\n"
        "-def func_a(victim=1):\n"
        "+def func_a():\n"
        "     return victim\n"
        "\n"
        " def func_b(victim=1):\n"
        "     return victim\n"
    )
    assert _dropped_parameters_by_file(diff) == {"a.py": {"victim"}}


def test_dropped_parameter_same_name_in_other_class_is_not_a_keep():
    """Same parameter name on a different class in the same file is
    still a different signature."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,8 +1,8 @@\n"
        " class A:\n"
        "-    def go(self, victim=1):\n"
        "+    def go(self):\n"
        "         return self.victim\n"
        "\n"
        " class B:\n"
        "     def go(self, victim=1):\n"
        "         return self.victim\n"
    )
    assert _dropped_parameters_by_file(diff) == {"a.py": {"victim"}}


def test_dropped_parameter_split_hunks_keep_function_identity():
    """A later hunk of context that contains another function with the
    same parameter name must not cancel the drop."""
    from code_forge.context_sources import _dropped_parameters_by_file
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def func_a(victim=1):\n"
        "+def func_a():\n"
        "@@ -20,3 +20,3 @@\n"
        " def func_b(victim=1):\n"
        "     return victim\n"
    )
    assert _dropped_parameters_by_file(diff) == {"a.py": {"victim"}}


def _source_diff(before: str, after: str) -> str:
    ast.parse(before)
    ast.parse(after)
    return "diff --git a/a.py b/a.py\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/a.py", tofile="b/a.py", n=3,
    ))


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("remove", [False, True])
def test_same_named_methods_keep_separate_signature_locations(visible, remove):
    from code_forge.context_sources import _dropped_parameters_by_file

    padding = "    # padding\n" * 8
    prefix = "" if visible else padding
    before = "".join(
        f"class {name}:\n{prefix}    def go(self, alpha):\n"
        f"        return 1\n{padding}\n" for name in ("A", "B")
    )
    after = before.replace("return 1", "return 2")
    if remove:
        after = after.replace("go(self, alpha)", "go(self)", 1)
    diff = _source_diff(before, after)
    assert sum(line.startswith("@@") for line in diff.splitlines()) == 2
    classes = [line[1:].strip() for line in diff.splitlines()
               if line[:1] in (" ", "+", "-") and line[1:].lstrip().startswith("class ")]
    assert classes == (["class A:", "class B:"] if visible else [])
    assert _dropped_parameters_by_file(diff) == ({"a.py": {"alpha"}} if remove else {})


def test_same_named_methods_without_class_context_real_git(tmp_path: Path):
    from code_forge.context_sources import _dropped_parameters_by_file

    padding = "    # padding\n" * 8
    before = "".join(
        f"class {name}:\n{padding}    def go(self, alpha):\n"
        f"        return self.alpha\n{padding}\n" for name in ("A", "B")
    )
    after = before.replace("go(self, alpha)", "go(self)", 1)
    after = after.replace("return self.alpha", "return self.alpha + 1")
    ast.parse(before)
    ast.parse(after)
    target = tmp_path / "a.py"
    target.write_text(before)
    _init_git(tmp_path)
    target.write_text(after)
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=3", "--", "a.py"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout
    assert not any(line[1:].lstrip().startswith("class ")
                   for line in diff.splitlines() if line[:1] in (" ", "+", "-"))
    assert _dropped_parameters_by_file(diff) == {"a.py": {"alpha"}}
    rows = RemovedSymbolReaders(tmp_path).facts(["a.py"], diff)
    assert [row.entity for row in rows] == ["alpha"]
    assert "parameter removed from signature" in rows[0].dependents


@pytest.mark.parametrize("remove", [False, True])
def test_one_signature_continues_across_hunks(remove):
    from code_forge.context_sources import _dropped_parameters_by_file

    padding = "    # padding\n" * 8
    before = (
        "def go(\n    alpha=None,\n" + padding
        + "    beta=None,\n):\n    return 1\n"
    )
    after = before.replace("    alpha=None,\n", "")
    replacement = "    beta=1,\n" if remove else "    beta=None,\n    alpha=None,\n"
    after = after.replace("    beta=None,\n", replacement)
    diff = _source_diff(before, after)
    assert sum(line.startswith("@@") for line in diff.splitlines()) == 2
    assert _dropped_parameters_by_file(diff) == ({"a.py": {"alpha"}} if remove else {})



@pytest.mark.parametrize("context", [3, 80])
@pytest.mark.parametrize("move,remove", [(True, False), (False, True), (True, True)])
def test_method_move_preserves_parameter_identity(tmp_path, context, move, remove):
    stable = "".join(f"    def stable_{i}(self):\n        return {i}\n\n" for i in range(8))
    target = ("    def configure(self, alpha=1, bravo=2):\n"
              "        self.alpha = alpha\n        self.bravo = bravo\n        return self\n\n")
    reader = "    def read(self):\n        return self.alpha, self.bravo\n"
    prefix = "class Box:\n" + "    # padding\n" * 8
    before = prefix + target + stable + reader
    changed = target.replace("alpha=1, ", "").replace("self.alpha = alpha", "self.alpha = 1") if remove else target
    after = prefix + (stable + changed if move else changed + stable) + reader
    _assert_parameter_diff(tmp_path, before, after, {"alpha"} if remove else set(), context)


@pytest.mark.parametrize("prefix", ["", "class Outer:\n", "class Outer:\n" + "    # padding\n" * 8])
@pytest.mark.parametrize("change", ["keep", "remove", "insert", "delete", "reorder", "reorder_remove"])
def test_nested_signatures_use_enclosing_names(tmp_path, prefix, change):
    base = "    " if prefix else ""

    def source(names, after):
        chunks = []
        for name in names:
            arg = "alpha=2" if after else "alpha=1"
            if after and name == "one" and change in ("remove", "reorder_remove"):
                arg = ""
            chunks.append(f"{base}def factory_{name}(self): # {'after' if after else 'before'}\n"
                          f"{base}    def go({arg}):\n"
                          f"{base}        return self.alpha{' + 0' if after else ''}\n"
                          f"{base}    return {'(go)' if after else 'go'}\n")
        return prefix + "".join(chunks)

    before_names = ["one", "two", "three"]
    after_names = {"insert": ["zero", *before_names], "delete": ["one", "three"],
                   "reorder": ["three", "one", "two"],
                   "reorder_remove": ["three", "one", "two"]}.get(change, before_names)
    _assert_parameter_diff(tmp_path, source(before_names, False), source(after_names, True),
                           {"alpha"} if change in ("remove", "reorder_remove") else set())


@pytest.mark.parametrize("known_scope", [False, True])
def test_unresolved_repeated_signatures_do_not_claim_removal(tmp_path, known_scope):
    prefix = ("class Box:\n" if known_scope else "def factory():\n") + "    # padding\n" * 8
    before = prefix + "".join(
        f"    def go(self, {arg}=1):\n        return self.alpha, self.bravo\n"
        for arg in ("alpha", "bravo", "alpha"))
    after = prefix + "".join(
        f"    def go(self, {arg}=2):\n        return self.alpha, self.bravo + 0\n"
        for arg in ("alpha",))
    _assert_parameter_diff(tmp_path, before, after, set())


def test_deleted_signature_is_not_parameter_removal(tmp_path):
    before = ("class Box:\n    def gone(self, alpha=1):\n        return self.alpha\n"
              "    def read(self):\n        return self.alpha\n")
    after = "class Box:\n    def read(self):\n        return self.alpha\n"
    _assert_parameter_diff(tmp_path, before, after, set())


def _assert_parameter_diff(root, before, after, expected, context=3):
    from code_forge.context_sources import _dropped_parameters_by_file

    ast.parse(before)
    ast.parse(after)
    target = root / "a.py"
    target.write_text(before)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "--", "a.py"], cwd=root, check=True)
    target.write_text(after)
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-textconv", f"--unified={context}", "--", "a.py"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    rows = RemovedSymbolReaders(root).facts(["a.py"], diff)
    parameter_rows = [row for row in rows if row.dependents.startswith("parameter removed from signature;")]
    assert {row.entity for row in parameter_rows} == expected
    assert len(parameter_rows) == len(expected)
    for row in parameter_rows:
        assert row.file == "a.py" and row.source == "removed-symbol-readers"
        assert row.snippets and row.enclosing
        for location, snippet in row.snippets.items():
            assert after.splitlines()[int(location.rsplit(":", 1)[1]) - 1] == snippet
            assert f"self.{row.entity}" in snippet
    assert _dropped_parameters_by_file(diff) == ({"a.py": expected} if expected else {})
    collected = gather([RemovedSymbolReaders(root)], ["a.py"], diff, head_sha=None)
    assert collected.errors == [] and collected.skipped == []
    assert collected.rows == rows


def test_facts_parses_the_diff_body_once_per_call(tree: Path, monkeypatch):
    """facts() feeds three readers from one diff body; each reader used to
    walk it again. The spy wraps the real parser -- its output is not
    mocked -- and counts the walks. One parse per facts() call, with hunk
    labels kept for the signature reader."""
    import code_forge.context_sources as sources

    calls = []
    real_parse = sources._diff_body_lines

    def spy(diff_text, *, include_hunks=False):
        calls.append(include_hunks)
        return real_parse(diff_text, include_hunks=include_hunks)

    monkeypatch.setattr(sources, "_diff_body_lines", spy)
    rows = RemovedSymbolReaders(tree).facts(["pkg/ridge.py"], DIFF)
    assert calls == [True]
    # The spy delegates, so these are the real rows, not a canned output.
    assert "store_cv_values" in {row.entity for row in rows}


def test_helpers_accept_a_shared_parse_without_changing_output():
    """A materialized parse handed to all three readers must give exactly
    what each reader's own standalone parse gives."""
    from code_forge.context_sources import (
        _diff_body_lines,
        _dropped_parameters_by_file,
        _removal_scope,
    )

    diff = ("--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,2 @@ class Box:\n"
            " class Box:\n"
            "-    def go(self, alpha=1):\n"
            "-        return alpha + helper_marker\n"
            "+    def go(self):\n"
            "+        return 0\n")
    shared = list(_diff_body_lines(diff, include_hunks=True))
    assert (_removal_scope(diff, ["a.py"], parsed_lines=shared)
            == _removal_scope(diff, ["a.py"]) == {"a.py"})
    assert (_removed_identifiers_by_file(diff, parsed_lines=shared)
            == _removed_identifiers_by_file(diff) == {"a.py": {"alpha"}})
    assert (_dropped_parameters_by_file(diff, parsed_lines=shared)
            == _dropped_parameters_by_file(diff) == {"a.py": {"alpha"}})


def test_parsed_lines_none_reparses_and_empty_list_stays_empty(monkeypatch):
    """None means "not supplied" and reparses; an empty list is a real,
    already-empty parse and must not trigger one."""
    import code_forge.context_sources as sources
    from code_forge.context_sources import (
        _dropped_parameters_by_file,
        _removal_scope,
    )

    def boom(diff_text, *, include_hunks=False):
        raise RuntimeError("parse attempted")

    monkeypatch.setattr(sources, "_diff_body_lines", boom)
    assert _removal_scope(DIFF, ["pkg/ridge.py"], parsed_lines=[]) == {"pkg/ridge.py"}
    assert _removed_identifiers_by_file(DIFF, parsed_lines=[]) == {}
    assert _dropped_parameters_by_file(DIFF, parsed_lines=[]) == {}
    for call in (lambda: _removal_scope(DIFF, ["pkg/ridge.py"]),
                 lambda: _removal_scope(DIFF, ["pkg/ridge.py"], parsed_lines=None),
                 lambda: _removed_identifiers_by_file(DIFF),
                 lambda: _removed_identifiers_by_file(DIFF, parsed_lines=None),
                 lambda: _dropped_parameters_by_file(DIFF),
                 lambda: _dropped_parameters_by_file(DIFF, parsed_lines=None)):
        with pytest.raises(RuntimeError, match="parse attempted"):
            call()


def test_scope_ignores_hunk_labels_in_a_shared_parse():
    """A standalone scope parse drops @@ lines; the shared list keeps them
    for the signature reader. Scope must not let a hunk-only file leak
    into the named set."""
    from code_forge.context_sources import _diff_body_lines, _removal_scope

    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@ class Box:\n"
    shared = list(_diff_body_lines(diff, include_hunks=True))
    assert shared  # the hunk label is present in the shared list
    assert _removal_scope(diff, ["unrelated.py"]) == {"unrelated.py"}
    assert _removal_scope(diff, ["unrelated.py"], parsed_lines=shared) == {"unrelated.py"}
