# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI-01 argparse surface tests."""

import sys

import pytest

from forge.cli import _build_parser


class TestParserDefaults:
    """Bare invocation defaults."""

    def test_bare_invocation_defaults(self):
        """SC-2: all defaults match spec."""
        parser = _build_parser()
        args = parser.parse_args([])
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
        """SC-1(b): all flags populated."""
        parser = _build_parser()
        args = parser.parse_args([
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


class TestParserInvalidChoices:
    """Invalid choices -> argparse exit 2."""

    def test_invalid_mode_exits_2(self):
        """SC-1(c): --mode invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--mode", "invalid"])
        assert exc_info.value.code == 2

    def test_invalid_engine_exits_2(self):
        """SC-1(d): --falsification-engine invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(
                ["--falsification-engine", "invalid"]
            )
        assert exc_info.value.code == 2


class TestParserHelp:
    """--help includes Exit codes section."""

    def test_help_includes_exit_codes(self, capsys):
        """SC-1(e): --help epilog lists 0/1/2/3/4."""
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
        """SC-1(f): --version exits 0."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("forge ")
