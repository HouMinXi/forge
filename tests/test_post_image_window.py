"""Post-image is narrowed to the neighbourhood of each hunk.

Whole files dominated the reviewer prompt and grew with file size rather
than with the size of the change.
"""
from code_forge.cli import _assemble_post_image, _window_file_text


def _hunk(start, end):
    return {"start": start, "end": end, "added_lines": [], "is_deletion_only": False}


class TestWindowFileText:
    """The windowing primitive, independent of the filesystem."""

    def _text(self, n):
        return "\n".join("line%d" % i for i in range(1, n + 1))

    def test_no_hunks_returns_text_unchanged(self):
        """A file with no hunks is exempt -- there is nothing to window to."""
        text = self._text(100)
        out, windowed = _window_file_text(text, [], 10)
        assert out == text
        assert windowed is False

    def test_lines_outside_the_window_are_dropped(self):
        out, windowed = _window_file_text(self._text(200), [_hunk(100, 101)], 5)
        assert windowed is True
        assert "line100" in out and "line101" in out
        assert "line95" in out and "line106" in out
        assert "line94" not in out
        assert "line1\n" not in out

    def test_kept_lines_carry_their_numbers(self):
        """The result has gaps, so the reviewer cannot count its way in."""
        out, _ = _window_file_text(self._text(50), [_hunk(20, 20)], 2)
        assert "20: line20" in out
        assert "18: line18" in out

    def test_gap_is_marked_with_a_count(self):
        out, _ = _window_file_text(self._text(100), [_hunk(50, 50)], 3)
        assert "[46 lines omitted]" in out, out
        assert "[47 lines omitted]" in out, out

    def test_overlapping_windows_do_not_repeat_lines(self):
        """Two hunks close together share context; emit it once.

        Windows 15-25 and 19-29 overlap at 19-25. Without merging, those
        seven lines are written twice and the reviewer sees a file that
        appears to repeat itself.
        """
        out, _ = _window_file_text(
            self._text(100), [_hunk(20, 20), _hunk(24, 24)], 5)
        body = [x for x in out.splitlines() if ": line" in x]
        assert len(body) == len(set(body)), (
            "a line was emitted more than once: %s" % out
        )
        assert "20: line20" in out and "24: line24" in out

    def test_touching_windows_leave_no_empty_gap(self):
        """Windows that meet exactly must not report a zero-line gap."""
        out, _ = _window_file_text(
            self._text(100), [_hunk(20, 20), _hunk(31, 31)], 5)
        assert "0 lines omitted" not in out, out
        assert "25: line25\n26: line26" in out, out

    def test_hunk_order_does_not_change_the_result(self):
        """The merge only compares against the previous region.

        git emits hunks ascending, so this holds today by luck of the
        caller rather than by anything this function does.
        """
        asc, _ = _window_file_text(
            self._text(100), [_hunk(20, 20), _hunk(60, 60)], 5)
        desc, _ = _window_file_text(
            self._text(100), [_hunk(60, 60), _hunk(20, 20)], 5)
        assert asc == desc, "reordering the hunks changed the output"

    def test_separated_windows_stay_separate(self):
        """The counterpart: a real gap must still be reported.

        Without this the merge could swallow every gap and the test above
        would still pass.
        """
        out, _ = _window_file_text(
            self._text(100), [_hunk(20, 20), _hunk(60, 60)], 5)
        assert out.count("lines omitted") == 3, out
        # 15-25 and 55-65 kept, so 26-54 is the gap between them.
        assert "[29 lines omitted]" in out, out

    def test_a_file_changed_throughout_comes_back_whole(self):
        """When the windows cover everything, do not renumber for nothing."""
        text = self._text(20)
        out, windowed = _window_file_text(
            text, [_hunk(1, 5), _hunk(10, 15)], 40)
        assert out == text
        assert windowed is False

    def test_context_zero_keeps_only_the_hunk(self):
        out, _ = _window_file_text(self._text(50), [_hunk(25, 26)], 0)
        assert "25: line25" in out and "26: line26" in out
        assert "line24" not in out and "line27" not in out

    def test_hunk_at_file_start_does_not_underflow(self):
        out, _ = _window_file_text(self._text(50), [_hunk(1, 2)], 10)
        assert "1: line1" in out
        assert "lines omitted]" in out
        assert out.startswith("1: line1")

    def test_hunk_at_file_end_does_not_overflow(self):
        out, _ = _window_file_text(self._text(50), [_hunk(49, 50)], 10)
        assert "50: line50" in out
        assert out.rstrip().endswith("50: line50")

    def test_hunk_end_past_eof_is_clamped(self):
        """parse_diff_hunks reports target-side numbers; a truncated read
        can leave the file shorter than the hunk claims."""
        out, _ = _window_file_text(self._text(10), [_hunk(8, 40)], 2)
        assert "10: line10" in out
        assert "line11" not in out

    def test_hunk_beyond_eof_falls_back_to_whole_text(self):
        """A 50KB-truncated read leaves every hunk past the cut. Windowing
        there would emit omission markers with no code under them, so the
        file goes out whole instead."""
        text = self._text(10)
        out, windowed = _window_file_text(text, [_hunk(500, 510)], 40)
        assert windowed is False
        assert out == text

    def test_out_of_range_hunk_does_not_corrupt_an_in_range_one(self):
        out, windowed = _window_file_text(
            self._text(100), [_hunk(10, 12), _hunk(500, 510)], 5,
        )
        assert windowed is True
        assert "10: line10" in out
        # The out-of-range hunk contributes no bare omission run of its own:
        # the only large gap is the file tail after line 17.
        assert "... [83 lines omitted]" in out


class TestAssemblePostImage:
    """The whole assembly, against real files and a real diff."""

    def _repo(self, tmp_path, nlines=400):
        f = tmp_path / "big.py"
        f.write_text("\n".join("row%d" % i for i in range(1, nlines + 1)) + "\n")
        return f

    def _diff(self, changed_line=200):
        return (
            "diff --git a/big.py b/big.py\n"
            "--- a/big.py\n"
            "+++ b/big.py\n"
            "@@ -%d,2 +%d,2 @@\n"
            " row%d\n"
            "-old\n"
            "+row%d\n"
        ) % (changed_line, changed_line, changed_line, changed_line + 1)

    def test_narrowed_output_is_much_smaller(self, tmp_path):
        self._repo(tmp_path)
        whole, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=10**6)
        narrow, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=20)
        assert len(narrow) < len(whole) / 3, (
            "windowing saved almost nothing: %d vs %d" % (len(narrow), len(whole))
        )

    def test_the_changed_lines_survive(self, tmp_path):
        self._repo(tmp_path)
        out, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=20)
        assert "row200" in out and "row201" in out

    def test_distant_lines_are_gone(self, tmp_path):
        self._repo(tmp_path)
        out, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=20)
        assert "row5\n" not in out and "row399" not in out

    def test_narrowing_is_announced_in_the_header(self, tmp_path):
        """The reviewer must not read a gap as the end of the file."""
        self._repo(tmp_path)
        out, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=20)
        assert "around the changes" in out, out[:200]

    def test_whole_file_keeps_a_plain_header(self, tmp_path):
        """A short file the windows cover entirely is not relabelled."""
        self._repo(tmp_path, nlines=5)
        diff = (
            "diff --git a/big.py b/big.py\n"
            "--- a/big.py\n"
            "+++ b/big.py\n"
            "@@ -1,5 +1,5 @@\n"
            " row1\n row2\n row3\n row4\n"
            "-old\n"
            "+row5\n"
        )
        out, _ = _assemble_post_image(tmp_path, diff, context_lines=40)
        assert "## File: big.py\n" in out, out[:120]
        assert "around the changes" not in out

    def test_rename_only_never_reaches_the_post_image(self, tmp_path):
        """Not a windowing decision -- it was already true.

        get_changed_files lists only files with an added line, and a pure
        rename has none, so it is absent from the post-image with or
        without narrowing. Pinned because the windowing docstring first
        claimed the opposite.
        """
        (tmp_path / "new.py").write_text("kept line\n" * 50)
        diff = (
            "diff --git a/old.py b/new.py\n"
            "similarity index 100%\n"
            "rename from old.py\n"
            "rename to new.py\n"
        )
        for ctx in (5, 10**6):
            out, _ = _assemble_post_image(tmp_path, diff, context_lines=ctx)
            assert out == "", "context_lines=%s produced %r" % (ctx, out[:80])

    def test_missing_file_is_skipped_not_fatal(self, tmp_path):
        out, _ = _assemble_post_image(tmp_path, self._diff(), context_lines=20)
        assert out == ""
