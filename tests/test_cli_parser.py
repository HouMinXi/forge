# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Argparse surface tests for forge CLI subcommands."""

import pytest

from forge.cli import _build_parser


class TestParserDefaults:
    """Bare invocation defaults."""

    def test_bare_invocation_defaults(self):
        """Bare forge (no subcommand) has subcommand=None."""
        parser = _build_parser()
        args = parser.parse_args([])
        # Subparser structure: no subcommand specified
        assert args.subcommand is None

    def test_no_subcommand_defaults_review(self):
        """Backward compat: bare forge maps to review in main()."""
        # This is tested in main() logic, not parser.
        # Parser returns subcommand=None; main() maps None -> 'review'.
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.subcommand is None
        # main() will set: if args.subcommand is None: args.subcommand = 'review'

    def test_review_subcommand_explicit(self):
        """Explicit 'forge review' sets subcommand='review'."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.subcommand == 'review'

    def test_review_subcommand_defaults(self):
        """Review subcommand defaults match old forge defaults."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.mode is None
        assert args.falsification_engine is None
        assert args.sandbox is False
        assert args.baseline is None
        assert args.head is None
        assert args.registry == ".forge/tools.yaml"
        assert args.state_dir is None
        assert args.max_total_rounds is None
        assert args.max_fix_attempts is None
        assert args.quiet is False
        assert args.staged is False
        assert args.paths == []


class TestParserAllFlags:
    """All flags set -> values propagate."""

    def test_all_flags_set(self):
        """All review flags populated."""
        parser = _build_parser()
        args = parser.parse_args([
            "review",  # explicit subcommand
            "--mode", "ci",
            "--falsification-engine", "stub",
            "--sandbox",
            "--baseline", "abc123",
            "--head", "WORKING",
            "--registry", "custom.yaml",
            "--state-dir", "/tmp/state",
            "--max-total-rounds", "50",
            "--max-fix-attempts", "10",
            "--quiet",
            "--staged",
            "a.py", "b.py",
        ])
        assert args.subcommand == "review"
        assert args.mode == "ci"
        assert args.falsification_engine == "stub"
        assert args.sandbox is True
        assert args.baseline == "abc123"
        assert args.head == "WORKING"
        assert args.registry == "custom.yaml"
        assert args.state_dir == "/tmp/state"
        assert args.max_total_rounds == 50
        assert args.max_fix_attempts == 10
        assert args.quiet is True
        assert args.staged is True
        assert args.paths == ["a.py", "b.py"]

    def test_review_flags_preserved(self):
        """Review subcommand preserves all existing flags."""
        parser = _build_parser()
        args = parser.parse_args([
            'review', '--mode', 'local', '--baseline', 'HEAD'
        ])
        assert args.subcommand == 'review'
        assert args.mode == 'local'
        assert args.baseline == 'HEAD'


class TestParserInvalidChoices:
    """Invalid choices -> argparse exit 2."""

    def test_invalid_mode_exits_2(self):
        """--mode invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["review", "--mode", "invalid"])
        assert exc_info.value.code == 2

    def test_invalid_engine_exits_2(self):
        """--falsification-engine invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([
                "review", "--falsification-engine", "invalid"
            ])
        assert exc_info.value.code == 2


class TestParserHelp:
    """--help includes Exit codes section."""

    def test_help_includes_exit_codes(self, capsys):
        """--help epilog lists exit codes 0/1/2/3/4."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Exit codes:" in captured.out
        assert "0  PASS" in captured.out
        assert "1  FAIL" in captured.out
        assert "2  CLI_ERROR" in captured.out
        assert "3  BUSY" in captured.out
        assert "4  ESCALATED" in captured.out


class TestParserVersion:
    """--version prints forge <version> + exits 0."""

    def test_version_exits_zero(self, capsys):
        """--version exits 0."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("forge ")


class TestSubcommands:
    """Subcommand routing tests."""

    def test_gate_check_subcommand(self):
        """gate-check subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['gate-check'])
        assert args.subcommand == 'gate-check'
        assert args.quiet is False

    def test_gate_check_quiet(self):
        """gate-check --quiet flag."""
        parser = _build_parser()
        args = parser.parse_args(['gate-check', '--quiet'])
        assert args.subcommand == 'gate-check'
        assert args.quiet is True

    def test_install_hooks_subcommand(self):
        """install-hooks subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['install-hooks'])
        assert args.subcommand == 'install-hooks'
        assert args.quiet is False

    def test_install_hooks_quiet(self):
        """install-hooks --quiet flag."""
        parser = _build_parser()
        args = parser.parse_args(['install-hooks', '--quiet'])
        assert args.subcommand == 'install-hooks'
        assert args.quiet is True
