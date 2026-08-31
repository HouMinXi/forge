"""Phase 57-1: reversing a fix into the change that introduced the defect.

SWE-bench ships the patch that FIXES each bug. Reviewing it asks whether
correct code is correct. What forge must review is the reverse: the change
that introduces the defect, which is what a reviewer would have faced.

Four traps, each with a test here. Traps 1-3 were known when the plan was
written; trap 4 was found by measurement -- it broke 1 of the first 30 real
instances, and every file-creating patch after that.
"""

import pytest

from code_forge.eval.swebench import reverse_patch


class TestHeaderOrder:
    """Trap 1: --- must come first.

    A naive implementation rewrites '--- a/f' to '+++ a/f' in place and
    leaves the file headers in +++/--- order. git apply rejects that
    outright, so the diff is not merely ugly, it is unusable.
    """

    def test_minus_header_precedes_plus_header(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,3 @@\n"
            " ctx\n"
            "-old\n"
            "+new\n"
        )
        out = reverse_patch(fix).split("\n")
        minus = next(i for i, l in enumerate(out) if l.startswith("--- "))
        plus = next(i for i, l in enumerate(out) if l.startswith("+++ "))
        assert minus < plus

    def test_header_paths_swap_sides(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        out = reverse_patch(fix)
        assert "--- a/f.py" in out
        assert "+++ b/f.py" in out


class TestPrefixCollision:
    """Trap 2: +++ starts with + and --- starts with -.

    A prefix swap that does not exclude the headers turns '+++ b/f.py'
    into '-++ b/f.py'. The corruption is silent until git apply refuses.
    """

    def test_headers_survive_the_prefix_swap(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        out = reverse_patch(fix)
        assert "-++" not in out
        assert "+--" not in out

    def test_body_lines_do_swap(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-buggy\n"
            "+fixed\n"
        )
        out = reverse_patch(fix).split("\n")
        assert "+buggy" in out
        assert "-fixed" in out


class TestHunkArithmetic:
    """Trap 3: @@ -A,B +C,D @@ becomes @@ -C,D +A,B @@.

    The trailing text after the closing @@ is the enclosing function name,
    which git emits as context and which must survive verbatim.
    """

    def test_hunk_sides_swap(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -10,5 +20,7 @@\n"
            "-old\n"
            "+new\n"
        )
        assert "@@ -20,7 +10,5 @@" in reverse_patch(fix)

    def test_function_context_preserved(self):
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -10,5 +20,7 @@ def _cstack(left, right):\n"
            "-old\n"
            "+new\n"
        )
        assert "@@ -20,7 +10,5 @@ def _cstack(left, right):" in reverse_patch(fix)

    def test_single_line_hunk_without_count(self):
        # git omits ',1' for one-line ranges: '@@ -5 +5 @@'.
        fix = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -5 +7 @@\n"
            "-old\n"
            "+new\n"
        )
        assert "@@ -7 +5 @@" in reverse_patch(fix)


class TestFileCreationAndDeletion:
    """Trap 4: /dev/null and the file mode line.

    Found by measurement, not by reading: astropy__astropy-13398 failed
    with 'bad git-diff - expected /dev/null'. git validates the mode line
    against the null path, so a patch that creates a file must reverse
    into one that deletes it -- both halves, or neither.
    """

    def test_creation_reverses_to_deletion(self):
        fix = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line one\n"
            "+line two\n"
        )
        out = reverse_patch(fix)
        assert "deleted file mode 100644" in out
        assert "new file mode" not in out
        assert "--- a/new.py" in out
        assert "+++ /dev/null" in out

    def test_deletion_reverses_to_creation(self):
        fix = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line one\n"
            "-line two\n"
        )
        out = reverse_patch(fix)
        assert "new file mode 100644" in out
        assert "deleted file mode" not in out
        assert "--- /dev/null" in out
        assert "+++ b/gone.py" in out

    def test_dev_null_never_lands_on_the_minus_side_of_a_creation(self):
        # The precise shape git rejects: a 'new file mode' whose ---
        # header is not /dev/null.
        fix = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+x\n"
        )
        out = reverse_patch(fix).split("\n")
        mode = next(l for l in out if "file mode" in l)
        assert mode.startswith("deleted"), (
            "a reversed creation must declare a deletion; git validates "
            "the mode line against the /dev/null side"
        )


class TestRoundTrip:
    """Reversing twice returns the original.

    The strongest cheap check: it catches asymmetric handling of any trap
    without needing a git tree to apply against.
    """

    @pytest.mark.parametrize(
        "fix",
        [
            (
                "diff --git a/f.py b/f.py\n"
                "--- a/f.py\n+++ b/f.py\n"
                "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n"
            ),
            (
                "diff --git a/new.py b/new.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n+++ b/new.py\n"
                "@@ -0,0 +1,2 @@\n+a\n+b\n"
            ),
            (
                "diff --git a/gone.py b/gone.py\n"
                "deleted file mode 100644\n"
                "--- a/gone.py\n+++ /dev/null\n"
                "@@ -1,2 +0,0 @@\n-a\n-b\n"
            ),
            (
                "diff --git a/x.py b/x.py\n"
                "--- a/x.py\n+++ b/x.py\n"
                "@@ -1,2 +1,3 @@ def f():\n ctx\n-a\n+b\n+c\n"
                "diff --git a/y.py b/y.py\n"
                "--- a/y.py\n+++ b/y.py\n"
                "@@ -9,1 +9,1 @@\n-p\n+q\n"
            ),
        ],
    )
    def test_double_reversal_is_identity(self, fix):
        assert reverse_patch(reverse_patch(fix)) == fix


class TestMultiFile:
    def test_each_file_section_reversed(self):
        fix = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n-one\n+ONE\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n"
            "@@ -5,1 +5,1 @@\n-two\n+TWO\n"
        )
        out = reverse_patch(fix)
        assert "+one" in out and "-ONE" in out
        assert "+two" in out and "-TWO" in out
        assert out.count("diff --git") == 2
