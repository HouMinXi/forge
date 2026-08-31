"""Phase 57-2: turn a SWE-bench instance into the answer key for its entry.

One ExpectedFinding per HUNK, not per file. Per-file was the first design
and it was wrong three ways, each measured across the 500 instances:

- 44% of instances are multi-hunk, and scorer.py returns on line-range
  overlap alone when both sides carry a valid range -- it never falls back
  to description matching. A correct finding in the second hunk of a
  one-finding-per-file entry scores as a miss AND a false positive.
- 14.2% touch several files. One defect became N expected findings sharing
  a description, and Kuhn matching is 1:1, so catching it once scored
  recall 1/N.
- Per-hunk puts the answer key at the granularity the matcher works at.

The zero-count case is the sharp edge, though rarer than an earlier draft
of this docstring claimed. 1 hunk in 1220 has a zero header count; the 32%
figure that used to appear here counted reversed hunks with no + line in
their BODY, which is a different and harmless thing, because header counts
include context lines. One is still enough: the obvious range formula
yields (start, start - 1), which valid_line_range rejects, and load_corpus
raises before a single entry is scored.
"""

import pytest

from code_forge.eval.swebench import expected_findings_for


def _patch(*hunks, path="m.py"):
    head = "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n" % (path, path, path, path)
    return head + "".join(hunks)


class TestOneFindingPerHunk:
    def test_single_hunk_yields_one(self):
        p = _patch("@@ -2,3 +2,3 @@\n ctx\n-bad\n+good\n tail\n")
        found = expected_findings_for(p, "the defect title")
        assert len(found) == 1
        assert found[0].file == "m.py"

    def test_two_hunks_yield_two(self):
        p = _patch(
            "@@ -2,3 +2,3 @@\n ctx\n-bad\n+good\n tail\n",
            "@@ -40,3 +40,3 @@\n mid\n-worse\n+better\n end\n",
        )
        found = expected_findings_for(p, "the defect title")
        assert len(found) == 2

    def test_two_files_yield_one_each(self):
        p = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-one\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
            "@@ -5,2 +5,2 @@\n ctx\n-two\n"
        )
        found = expected_findings_for(p, "title")
        assert [f.file for f in found] == ["a.py", "b.py"]

    def test_each_hunk_carries_its_own_file(self):
        p = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-one\n"
            "@@ -9,2 +9,2 @@\n ctx\n-uno\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
            "@@ -5,2 +5,2 @@\n ctx\n-two\n"
        )
        found = expected_findings_for(p, "title")
        assert [f.file for f in found] == ["a.py", "a.py", "b.py"]


class TestLineRange:
    """The range comes from the REVERSED hunk's new side.

    The entry under review is the reversed patch, so the defect occupies
    the lines that patch adds -- its `+` side, which is the original
    fix's `-` side.
    """

    def test_range_brackets_the_reversed_new_side(self):
        # Fix removes 2 lines at old line 10; reversed, it adds them back
        # at that position.
        p = _patch("@@ -10,4 +10,2 @@\n ctx\n-gone one\n-gone two\n tail\n")
        found = expected_findings_for(p, "title")
        assert found[0].line_range == (10, 13)

    def test_zero_count_yields_none_not_an_invalid_range(self):
        # Pure insertion in the fix: nothing on the old side. Reversed,
        # the hunk deletes and adds nothing, so there is no range to
        # point at. (10, 9) would be rejected by valid_line_range and
        # would take load_corpus down with it.
        p = _patch("@@ -10,0 +10,2 @@\n+added one\n+added two\n")
        found = expected_findings_for(p, "title")
        assert len(found) == 1
        assert found[0].line_range is None

    def test_absent_count_means_one_line(self):
        # "@@ -5 +5 @@" is git's shorthand for a single line.
        p = _patch("@@ -5 +5 @@\n-bad\n+good\n")
        found = expected_findings_for(p, "title")
        assert found[0].line_range == (5, 5)


class TestDescription:
    """First line of problem_statement, not the whole thing.

    Measured median 62 characters against 1185 for the full text, which
    carries reproduction code. score_findings matches on shared terms, so
    a long description makes a hit trivial to satisfy and inflates recall
    without the tool getting any better.
    """

    def test_uses_the_first_line(self):
        stmt = "Parser drops the final row\n\nSteps to reproduce:\n  x = 1\n"
        found = expected_findings_for(_patch("@@ -1,2 +1,2 @@\n c\n-b\n"), stmt)
        assert found[0].description == "Parser drops the final row"

    def test_strips_markdown_heading_marks(self):
        found = expected_findings_for(
            _patch("@@ -1,2 +1,2 @@\n c\n-b\n"), "### Bug: rows vanish\nbody"
        )
        assert found[0].description == "Bug: rows vanish"

    def test_every_hunk_shares_the_one_description(self):
        p = _patch(
            "@@ -2,2 +2,2 @@\n c\n-b\n",
            "@@ -9,2 +9,2 @@\n c\n-b\n",
        )
        found = expected_findings_for(p, "one defect, two sites")
        assert {f.description for f in found} == {"one defect, two sites"}


class TestRejectsUnusableInput:
    def test_empty_statement_is_refused(self):
        with pytest.raises(ValueError):
            expected_findings_for(_patch("@@ -1,2 +1,2 @@\n c\n-b\n"), "   \n")

    def test_patch_without_hunks_is_refused(self):
        with pytest.raises(ValueError):
            expected_findings_for(
                "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n", "title"
            )


class TestLoadsThroughTheRealLoader:
    """The generated key must survive corpus.py, including the None range.

    Asserted against the real loader rather than the dataclass, because
    valid_line_range is where an invalid range would actually bite.
    """

    def test_manifest_with_a_none_range_loads(self, tmp_path):
        import yaml

        from code_forge.eval.corpus import load_corpus

        p = _patch("@@ -10,0 +10,2 @@\n+one\n+two\n")
        found = expected_findings_for(p, "title")
        assert found[0].line_range is None

        (tmp_path / "e.diff").write_text("diff --git a/m.py b/m.py\n")
        manifest = {
            "entries": [
                {
                    "name": "e",
                    "diff_file": "e.diff",
                    "expected_verdict": "HOLD",
                    "axis_tags": ["RUNTIME"],
                    "expected_findings": [
                        {"file": f.file, "description": f.description}
                        for f in found
                    ],
                }
            ]
        }
        mpath = tmp_path / "corpus.yaml"
        mpath.write_text(yaml.safe_dump(manifest))
        entries = load_corpus(mpath)
        assert entries[0].expected_findings[0].line_range is None


class TestDeletedFile:
    """A deleted source file must not inherit the previous file's path.

    Found in review (R1-F1). No instance in the current corpus deletes a
    source file, so the bug is invisible today and would corrupt the
    answer key the first time one did: the deleted file's hunks would be
    filed against whichever file preceded it in the patch, where nothing
    matches and the expectation can never be hit.
    """

    def test_hunks_after_a_deletion_are_not_attributed_to_the_previous_file(self):
        p = (
            "diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-bad\n"
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
            "--- a/gone.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-line one\n-line two\n"
        )
        found = expected_findings_for(p, "the defect title")
        assert [f.file for f in found] == ["keep.py"]

    def test_a_deletion_alone_yields_no_findings(self):
        p = (
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
            "--- a/gone.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-line one\n-line two\n"
        )
        with pytest.raises(ValueError):
            expected_findings_for(p, "the defect title")
