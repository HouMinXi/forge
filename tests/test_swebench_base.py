"""Phase 57-4: reconstructing a base tree the patch can apply to.

`runner.py:742-768` seeds each replay from `base_files/<entry>/`, then
runs `git apply`. SWE-bench provides patch text and commit hashes, no
source files. Without a base tree every entry fails to apply, is marked
SKIPPED, and under the Phase 56 rules counts against recall -- a corpus
that reports zero without ever having run a review.

Cloning five hundred repositories at their base commits is the obvious
answer and the wrong one: gigabytes, network dependence, and repository
states that drift. The patch already carries what `git apply` checks --
its context and removed lines are the pre-image of every hunk it touches.
"""

import subprocess

import pytest

from code_forge.eval.swebench import reconstruct_base_files


def _git_apply_check(tmp_path, files, patch):
    """Write files into a real git repo and ask git whether patch applies.

    Real git, not a parser of our own: the thing being verified is
    acceptance by the tool the runner actually invokes.
    """
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        # --allow-empty: a patch that only creates files correctly yields no
        # base files at all, and git refuses an empty commit by default.
        # Without this the file-creation case fails in the harness rather
        # than in the code under test.
        ["git", "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "base", "--allow-empty"],
        cwd=tmp_path, check=True,
    )
    patch_file = tmp_path / "p.diff"
    patch_file.write_text(patch)
    return subprocess.run(
        ["git", "apply", "--check", str(patch_file)],
        cwd=tmp_path, capture_output=True, text=True,
    )


class TestSingleHunk:
    def test_reconstructed_file_accepts_its_patch(self, tmp_path):
        patch = (
            "diff --git a/pkg/mod.py b/pkg/mod.py\n"
            "--- a/pkg/mod.py\n"
            "+++ b/pkg/mod.py\n"
            "@@ -3,3 +3,3 @@ def f():\n"
            "     before = 1\n"
            "-    value = wrong\n"
            "+    value = right\n"
            "     after = 2\n"
        )
        files = reconstruct_base_files(patch)
        result = _git_apply_check(tmp_path, files, patch)
        assert result.returncode == 0, result.stderr

    def test_content_is_the_pre_image(self):
        patch = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n+++ b/m.py\n"
            "@@ -1,3 +1,3 @@\n"
            " keep\n"
            "-removed\n"
            "+added\n"
            " tail\n"
        )
        text = reconstruct_base_files(patch)["m.py"]
        assert "removed" in text
        assert "added" not in text

    def test_hunk_is_placed_at_its_start_line(self):
        patch = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n+++ b/m.py\n"
            "@@ -10,2 +10,2 @@\n"
            " ctx\n"
            "-old\n"
            "+new\n"
        )
        lines = reconstruct_base_files(patch)["m.py"].split("\n")
        assert lines[9] == "ctx"
        assert lines[10] == "old"


class TestMultiHunk:
    def test_separated_hunks_both_land(self, tmp_path):
        patch = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n+++ b/m.py\n"
            "@@ -2,3 +2,3 @@\n"
            " head\n"
            "-first bug\n"
            "+first fix\n"
            " tail\n"
            "@@ -40,3 +40,3 @@\n"
            " middle\n"
            "-second bug\n"
            "+second fix\n"
            " end\n"
        )
        files = reconstruct_base_files(patch)
        text = files["m.py"]
        assert "first bug" in text and "second bug" in text
        assert _git_apply_check(tmp_path, files, patch).returncode == 0


class TestMultiFile:
    def test_every_touched_file_is_produced(self, tmp_path):
        patch = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n ctx\n-one\n+ONE\n"
            "diff --git a/sub/b.py b/sub/b.py\n"
            "--- a/sub/b.py\n+++ b/sub/b.py\n"
            "@@ -5,2 +5,2 @@\n ctx\n-two\n+TWO\n"
        )
        files = reconstruct_base_files(patch)
        assert set(files) == {"a.py", "sub/b.py"}
        assert _git_apply_check(tmp_path, files, patch).returncode == 0


class TestFileCreation:
    def test_created_file_has_no_base(self, tmp_path):
        """A patch that creates a file must NOT get a base for it.

        git refuses to create a file that already exists, so emitting a
        stub here breaks the very patch it was meant to support -- the
        failure mode is inverted from the usual one.
        """
        patch = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line one\n"
            "+line two\n"
        )
        files = reconstruct_base_files(patch)
        assert "new.py" not in files
        assert _git_apply_check(tmp_path, files, patch).returncode == 0


class TestFileDeletion:
    def test_deleted_file_gets_its_full_content(self, tmp_path):
        patch = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line one\n"
            "-line two\n"
        )
        files = reconstruct_base_files(patch)
        assert files["gone.py"].startswith("line one")
        assert _git_apply_check(tmp_path, files, patch).returncode == 0


class TestReversedDirection:
    """The reversed patch is what the corpus actually reviews.

    A base built for the fix must also accept the reversal applied to the
    post-fix tree. This is the direction the eval harness runs, so a base
    that only works forwards is a base that never works.
    """

    def test_base_accepts_the_reversed_patch(self, tmp_path):
        from code_forge.eval.swebench import reverse_patch

        fix = (
            "diff --git a/m.py b/m.py\n"
            "--- a/m.py\n+++ b/m.py\n"
            "@@ -3,2 +3,2 @@\n"
            " ctx\n"
            "-buggy\n"
            "+fixed\n"
        )
        reversed_patch = reverse_patch(fix)
        # The reversed patch's pre-image is the FIXED tree, so its base is
        # reconstructed from the reversed patch itself.
        files = reconstruct_base_files(reversed_patch)
        assert "fixed" in files["m.py"]
        assert _git_apply_check(tmp_path, files, reversed_patch).returncode == 0


class TestDegenerate:
    def test_patch_with_no_hunks_yields_nothing(self):
        assert reconstruct_base_files("diff --git a/x b/x\n") == {}

    @pytest.mark.parametrize("patch", ["", "\n", "not a patch at all\n"])
    def test_garbage_does_not_raise(self, patch):
        assert reconstruct_base_files(patch) == {}
