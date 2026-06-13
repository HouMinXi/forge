# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for LegacyRunner advisory axis (REVIEW-LEGACY-01 + REVIEW-INTENT-01)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.disposition import Disposition
from code_forge.legacy import LegacyRunner
from code_forge.state import StateFinding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal unified diff: file foo.py, line 5 changed (added).
DIFF_FOO_LINE5 = """\
--- a/foo.py
+++ b/foo.py
@@ -4,3 +4,4 @@
 existing line 4
+new line 5
 existing line 6
 existing line 7
"""

# Minimal unified diff: file bar.py, line 10 changed.
DIFF_BAR_LINE10 = """\
--- a/bar.py
+++ b/bar.py
@@ -9,3 +9,4 @@
 existing line 9
+new line 10
 existing line 11
 existing line 12
"""

# Diff with foo.py and bar.py both changed.
DIFF_FOO_AND_BAR = DIFF_FOO_LINE5 + DIFF_BAR_LINE10


def _make_finding(
    file: str = "foo.py",
    line: int = 20,
    description: str = "some issue",
    finding_id: str = "fp1",
) -> StateFinding:
    """Build a minimal StateFinding for test fixtures."""
    return StateFinding(
        id=finding_id,
        fingerprint=finding_id,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=[line, line],
        description=description,
        error=None,
        anchor=None,
        evidence_files=[],
    )


def _fake_runner(findings, infra_errors=None):
    """Return a callable that mimics _default_l0_runner."""
    def runner(registry, files):
        return (findings, infra_errors or [])
    return runner


# Full 40-char hex SHA for test fixtures.
SHA_40 = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
SHA_STAGED = "0" * 40


# ---------------------------------------------------------------------------
# REVIEW-LEGACY-01 tests
# ---------------------------------------------------------------------------


class TestPreExistingDetection:
    """Tests for pre-existing finding detection (REVIEW-LEGACY-01)."""

    @patch("code_forge.legacy.git_blame")
    def test_pre_existing_finding_emitted(self, mock_blame, tmp_path):
        """L0 finding on unchanged line 20 -> emitted as AdvisoryFinding."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "init commit"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        # Write a source file so _classify_intent can read it.
        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert isinstance(result[0], AdvisoryFinding)
        assert result[0].axis == "legacy"
        assert result[0].file == "foo.py"
        assert result[0].id.startswith("legacy:")

    @patch("code_forge.legacy.git_blame")
    def test_delta_finding_not_pre_existing(self, mock_blame, tmp_path):
        """L0 finding on changed line 5 -> NOT in advisory output."""
        mock_blame.return_value = {}
        finding = _make_finding(file="foo.py", line=5)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 10)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 0

    @patch("code_forge.legacy.git_blame")
    def test_d01_non_diff_file_excluded(self, mock_blame, tmp_path):
        """L0 finding on bar.py (not in diff) -> NOT in advisory output."""
        mock_blame.return_value = {}
        # Diff only touches foo.py, but finding is on bar.py.
        finding = _make_finding(file="bar.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py"), Path("bar.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text("line 1\n")
        (tmp_path / "bar.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 0

    @patch("code_forge.legacy.git_blame")
    def test_absolute_path_source_files_normalized(self, mock_blame, tmp_path):
        """source_files with absolute path; diff has relative path."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Bob", "subject": "setup"},
        }
        abs_path = str(tmp_path / "foo.py")
        finding = _make_finding(file=abs_path, line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [tmp_path / "foo.py"]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert result[0].file == abs_path

    def test_advisory_never_blocks(self):
        """LegacyRunner.is_advisory is True."""
        runner = LegacyRunner()
        assert runner.is_advisory is True

    def test_skipped_when_no_source_files(self, tmp_path):
        """source_files=None -> SKIPPED advisory finding."""
        runner = LegacyRunner(l0_runner=_fake_runner([]))
        runner.source_files = None
        runner.registry = {"tools": []}

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "SKIPPED" in result[0].description

    def test_skipped_when_no_registry(self, tmp_path):
        """registry=None -> SKIPPED advisory finding."""
        runner = LegacyRunner(l0_runner=_fake_runner([]))
        runner.source_files = [Path("foo.py")]
        runner.registry = None

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "SKIPPED" in result[0].description

    def test_empty_diff_returns_empty(self, tmp_path):
        """Empty diff_text -> returns []."""
        runner = LegacyRunner(l0_runner=_fake_runner([]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        result = runner.run("", tmp_path)

        assert result == []

    def test_empty_changed_lines_returns_empty(self, tmp_path):
        """Diff with no added lines -> returns []."""
        # A diff header only, no actual changes.
        diff_header_only = """\
--- a/foo.py
+++ b/foo.py
"""
        runner = LegacyRunner(l0_runner=_fake_runner([]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        result = runner.run(diff_header_only, tmp_path)

        assert result == []

    @patch("code_forge.legacy.git_blame")
    def test_git_blame_unavailable_produces_unavailable_attribution(
        self, mock_blame, tmp_path
    ):
        """git_blame returns {} -> attribution = 'git-blame: unavailable'."""
        mock_blame.return_value = {}
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert result[0].attribution == "git-blame: unavailable"

    @patch("code_forge.legacy.git_blame")
    def test_staged_line_attribution(self, mock_blame, tmp_path):
        """sha = 0*40 -> attribution = 'git-blame: uncommitted staged change'."""
        mock_blame.return_value = {
            20: {"sha": SHA_STAGED, "author": "Not Committed Yet", "subject": ""},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert result[0].attribution == "git-blame: uncommitted staged change"

    @patch("code_forge.legacy.git_blame")
    def test_attribution_format(self, mock_blame, tmp_path):
        """Verify D-04 attribution format: 'git-blame: {author} {sha[:8]} {subject}'."""
        sha_full = "abc12345" + "0" * 32
        mock_blame.return_value = {
            20: {"sha": sha_full, "author": "Alice", "subject": "fix: null"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert result[0].attribution == "git-blame: Alice abc12345 fix: null"


# ---------------------------------------------------------------------------
# REVIEW-INTENT-01 tests
# ---------------------------------------------------------------------------


class TestIntentClassification:
    """Tests for intent classification (REVIEW-INTENT-01)."""

    @patch("code_forge.legacy.git_blame")
    def test_intent_label_in_description(self, mock_blame, tmp_path):
        """Every AdvisoryFinding description has [pre-existing] ... [intent: ...]."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "init"},
        }
        finding = _make_finding(file="foo.py", line=20, description="some issue")
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        desc = result[0].description
        assert desc.startswith("[pre-existing]")
        assert "[intent: intended]" in desc or "[intent: unintended]" in desc

    @patch("code_forge.legacy.git_blame")
    def test_satd_surrounding_lines_intended(self, mock_blame, tmp_path):
        """Source line N-1 contains '# TODO: remove this' -> 'intended'."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "init"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        # Write source with SATD on line 19 (one line before finding at line 20).
        lines = ["line %d" % i for i in range(1, 25)]
        lines[18] = "# TODO: remove this"  # line 19 (0-indexed = 18)
        (tmp_path / "foo.py").write_text("\n".join(lines) + "\n")

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: intended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_commit_msg_signal_intended(self, mock_blame, tmp_path):
        """Blame subject contains 'workaround' -> 'intended'."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "workaround for bug"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: intended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_commit_msg_signal_hack_intended(self, mock_blame, tmp_path):
        """Blame subject 'hack: skip known issue' -> 'intended'."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "hack: skip known issue"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: intended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_default_classification_unintended(self, mock_blame, tmp_path):
        """No SATD, no commit signals -> 'unintended'."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "add feature"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: unintended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_satd_precision_acknowledged(self, mock_blame, tmp_path):
        """'xxx_default' in source -> 'xxx' matches -> 'intended' (D-03 tradeoff)."""
        mock_blame.return_value = {
            20: {"sha": SHA_40, "author": "Alice", "subject": "init"},
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        lines = ["line %d" % i for i in range(1, 25)]
        lines[19] = "xxx_default = 42"  # line 20 (0-indexed = 19)
        (tmp_path / "foo.py").write_text("\n".join(lines) + "\n")

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: intended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_intent_signal_temp_false_positive(self, mock_blame, tmp_path):
        """Subject 'attempt to fix regression' -> 'temp' in 'attempt' -> 'intended'."""
        mock_blame.return_value = {
            20: {
                "sha": SHA_40,
                "author": "Alice",
                "subject": "attempt to fix regression",
            },
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: intended]" in result[0].description

    @patch("code_forge.legacy.git_blame")
    def test_unintentional_not_classified_intended(self, mock_blame, tmp_path):
        """Subject 'this was unintentional' -> guard prevents -> 'unintended'."""
        mock_blame.return_value = {
            20: {
                "sha": SHA_40,
                "author": "Alice",
                "subject": "this was unintentional",
            },
        }
        finding = _make_finding(file="foo.py", line=20)
        runner = LegacyRunner(l0_runner=_fake_runner([finding]))
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        (tmp_path / "foo.py").write_text(
            "\n".join(["line %d" % i for i in range(1, 25)]) + "\n"
        )

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert "[intent: unintended]" in result[0].description

    def test_l0_runner_exception_returns_skipped(self, tmp_path):
        """l0_runner raises RuntimeError -> SKIPPED + infra_errors populated."""
        def bad_runner(registry, files):
            raise RuntimeError("L0 tools crashed")

        runner = LegacyRunner(l0_runner=bad_runner)
        runner.source_files = [Path("foo.py")]
        runner.registry = {"tools": []}

        result = runner.run(DIFF_FOO_LINE5, tmp_path)

        assert len(result) == 1
        assert result[0].id == "legacy-skipped"
        assert "SKIPPED" in result[0].description
        assert any("L0 tools crashed" in e for e in runner.infra_errors)
