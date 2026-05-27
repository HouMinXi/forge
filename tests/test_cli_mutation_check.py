# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for mutation-check subcommand -- parser and dispatch."""

import sys
from unittest.mock import patch

import pytest

from code_forge.cli import _build_parser, main
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS


class TestMutationCheckParser:
    """Parser-level tests for mutation-check subcommand."""

    def test_subcommand_parses(self):
        """mutation-check subcommand is recognized."""
        parser = _build_parser()
        args = parser.parse_args(["mutation-check"])
        assert args.subcommand == "mutation-check"

    def test_defaults(self):
        """Default values are correct."""
        parser = _build_parser()
        args = parser.parse_args(["mutation-check"])
        assert args.diff is None
        assert args.timeout == 600
        assert args.paths is None

    def test_diff_flag(self):
        """--diff flag is captured."""
        parser = _build_parser()
        args = parser.parse_args(["mutation-check", "--diff", "/tmp/a.diff"])
        assert args.diff == "/tmp/a.diff"

    def test_timeout_flag(self):
        """--timeout flag is captured and stored as int."""
        parser = _build_parser()
        args = parser.parse_args(["mutation-check", "--timeout", "300"])
        assert args.timeout == 300

    def test_paths_flag(self):
        """--paths flag is captured."""
        parser = _build_parser()
        args = parser.parse_args(["mutation-check", "--paths", "src/**/*.py"])
        assert args.paths == "src/**/*.py"

    def test_all_flags_together(self):
        """All flags set simultaneously."""
        parser = _build_parser()
        args = parser.parse_args([
            "mutation-check",
            "--diff", "/tmp/x.diff",
            "--timeout", "120",
            "--paths", "*.py",
        ])
        assert args.subcommand == "mutation-check"
        assert args.diff == "/tmp/x.diff"
        assert args.timeout == 120
        assert args.paths == "*.py"

    def test_appears_in_help(self, capsys):
        """mutation-check appears in top-level --help."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "mutation-check" in captured.out

    def test_subcommand_help(self, capsys):
        """mutation-check --help exits 0 and mentions mutation."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["mutation-check", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "mutation" in captured.out.lower()


class TestMutationCheckDispatch:
    """Dispatch and exit-code tests for mutation-check subcommand."""

    def test_dispatch_pass_no_survivors(self, tmp_path, monkeypatch):
        """mutation-check returns EXIT_PASS when run_mutation returns no survivors."""
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
        dismissed = StateFinding(
            id="MUTATION_SKIPPED",
            fingerprint="mutation-no-python",
            source="MUTANT",
            disposition=Disposition.DISMISSED,
            file="",
            line_range=[],
            description="skipped",
        )
        with patch(
            "code_forge.mutation.run_mutation",
            return_value=([dismissed], []),
        ):
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "mutation-check", "--diff", str(diff_file)],
            )
            result = main()
        assert result == EXIT_PASS

    def test_dispatch_fail_survivors_found(self, tmp_path, monkeypatch):
        """mutation-check returns EXIT_FAIL when survivors are present."""
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
        survivor = StateFinding(
            id="mutant-foo__mutmut_1",
            fingerprint="mutant:foo__mutmut_1",
            source="MUTANT",
            disposition=Disposition.CONFIRMED,
            file="",
            line_range=[0, 0],
            description="mutant survived: foo__mutmut_1",
        )
        with patch(
            "code_forge.mutation.run_mutation",
            return_value=([survivor], []),
        ):
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "mutation-check", "--diff", str(diff_file)],
            )
            result = main()
        assert result == EXIT_FAIL

    def test_dispatch_cli_error_missing_diff(self, tmp_path, monkeypatch):
        """mutation-check returns EXIT_CLI_ERROR when --diff file not found."""
        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "mutation-check", "--diff", "/nonexistent/a.diff"],
        )
        result = main()
        assert result == EXIT_CLI_ERROR
