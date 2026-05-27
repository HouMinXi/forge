# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for e2e-check subcommand -- parser and dispatch."""

import sys
from unittest.mock import patch

import pytest

from code_forge.cli import _build_parser, main
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS


class TestE2eCheckParser:
    """Parser-level tests for e2e-check subcommand."""

    def test_subcommand_parses(self):
        """e2e-check subcommand is recognized."""
        parser = _build_parser()
        args = parser.parse_args(["e2e-check"])
        assert args.subcommand == "e2e-check"

    def test_defaults(self):
        """Default values are correct."""
        parser = _build_parser()
        args = parser.parse_args(["e2e-check"])
        assert args.diff is None
        assert args.repo_root is None

    def test_diff_flag(self):
        """--diff flag is captured."""
        parser = _build_parser()
        args = parser.parse_args(["e2e-check", "--diff", "/tmp/b.diff"])
        assert args.diff == "/tmp/b.diff"

    def test_repo_root_flag(self):
        """--repo-root flag is captured."""
        parser = _build_parser()
        args = parser.parse_args(["e2e-check", "--repo-root", "/home/user/repo"])
        assert args.repo_root == "/home/user/repo"

    def test_all_flags_together(self):
        """All flags set simultaneously."""
        parser = _build_parser()
        args = parser.parse_args([
            "e2e-check",
            "--diff", "/tmp/y.diff",
            "--repo-root", "/home/user/repo",
        ])
        assert args.subcommand == "e2e-check"
        assert args.diff == "/tmp/y.diff"
        assert args.repo_root == "/home/user/repo"

    def test_appears_in_help(self, capsys):
        """e2e-check appears in top-level --help."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "e2e-check" in captured.out

    def test_subcommand_help(self, capsys):
        """e2e-check --help exits 0 and mentions e2e or coverage."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["e2e-check", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "e2e" in captured.out.lower() or "coverage" in captured.out.lower()


class TestE2eCheckDispatch:
    """Dispatch and exit-code tests for e2e-check subcommand."""

    def test_dispatch_pass_empty_diff(self, tmp_path, monkeypatch):
        """e2e-check returns EXIT_PASS when diff is empty."""
        diff_file = tmp_path / "empty.diff"
        diff_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "e2e-check", "--diff", str(diff_file),
             "--repo-root", str(tmp_path)],
        )
        result = main()
        assert result == EXIT_PASS

    def test_dispatch_pass_no_uncertain_findings(self, tmp_path, monkeypatch):
        """e2e-check returns EXIT_PASS when run_e2e_check returns no UNCERTAIN findings."""
        from code_forge.disposition import Disposition
        from code_forge.state import StateFinding

        diff_file = tmp_path / "test.diff"
        diff_file.write_text(
            "diff --git a/src/foo.py b/src/foo.py\n"
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "+def foo(): pass\n",
            encoding="utf-8",
        )
        advisory = StateFinding(
            id="e2e-layer1",
            fingerprint="e2e-l1:abc123",
            source="E2E_CHECK",
            disposition=Disposition.DISMISSED,
            file="",
            line_range=[],
            description="advisory finding",
        )
        with patch(
            "code_forge.e2e_check.run_e2e_check",
            return_value=([advisory], []),
        ):
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "e2e-check", "--diff", str(diff_file),
                 "--repo-root", str(tmp_path)],
            )
            result = main()
        assert result == EXIT_PASS

    def test_dispatch_fail_uncertain_findings(self, tmp_path, monkeypatch):
        """e2e-check returns EXIT_FAIL when UNCERTAIN findings are present."""
        from code_forge.disposition import Disposition
        from code_forge.state import StateFinding

        diff_file = tmp_path / "test.diff"
        diff_file.write_text(
            "diff --git a/src/foo.py b/src/foo.py\n"
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "+def foo(): pass\n",
            encoding="utf-8",
        )
        blocking = StateFinding(
            id="e2e-layer2",
            fingerprint="e2e-l2:deadbeef",
            source="E2E_CHECK",
            disposition=Disposition.UNCERTAIN,
            file="",
            line_range=[],
            description="cross-component change: no e2e artifact found",
        )
        with patch(
            "code_forge.e2e_check.run_e2e_check",
            return_value=([blocking], []),
        ):
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "e2e-check", "--diff", str(diff_file),
                 "--repo-root", str(tmp_path)],
            )
            result = main()
        assert result == EXIT_FAIL

    def test_dispatch_cli_error_missing_diff(self, tmp_path, monkeypatch):
        """e2e-check returns EXIT_CLI_ERROR when --diff file not found."""
        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "e2e-check", "--diff", "/nonexistent/b.diff"],
        )
        result = main()
        assert result == EXIT_CLI_ERROR
