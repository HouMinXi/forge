# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI tests for detect and resolve-outlet subcommands."""

from __future__ import annotations

import sys
from unittest.mock import patch


from code_forge.cli import _build_parser, main
from code_forge.errors import CliError
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeDetectionResult:
    """Minimal stand-in for DetectionResult."""

    def __init__(self, detected, missing, language):
        self.detected = detected
        self.missing = missing
        self.language = language


# ---------------------------------------------------------------------------
# detect subcommand
# ---------------------------------------------------------------------------


class TestDetectSubcommand:
    """Tests for code-forge detect subcommand."""

    def test_detect_parser_registered(self):
        """detect subcommand is registered in _build_parser."""
        parser = _build_parser()
        args = parser.parse_args(["detect"])
        assert args.subcommand == "detect"

    def test_detect_force_flag_parsed(self):
        """detect --force sets force=True."""
        parser = _build_parser()
        args = parser.parse_args(["detect", "--force"])
        assert args.subcommand == "detect"
        assert args.force is True

    def test_detect_no_force_default(self):
        """detect without --force defaults to force=False."""
        parser = _build_parser()
        args = parser.parse_args(["detect"])
        assert args.force is False

    def test_detect_subcommand_runs(self, monkeypatch, capsys):
        """detect in a Python project exits 0."""
        fake_result = _FakeDetectionResult(
            detected=["ruff", "pytest"],
            missing=["mypy"],
            language="python",
        )
        with patch(
            "code_forge.detect.detect_and_init",
            return_value=fake_result,
        ) as mock_dai:
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "detect"],
            )
            exit_code = main()

        assert exit_code == EXIT_PASS
        mock_dai.assert_called_once()

    def test_detect_force_flag_passed(self, monkeypatch, capsys):
        """detect --force passes force=True to detect_and_init."""
        fake_result = _FakeDetectionResult(
            detected=["ruff"],
            missing=[],
            language="python",
        )
        with patch(
            "code_forge.detect.detect_and_init",
            return_value=fake_result,
        ) as mock_dai:
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "detect", "--force"],
            )
            exit_code = main()

        assert exit_code == EXIT_PASS
        mock_dai.assert_called_once()

    def test_detect_no_python_exits_2(self, monkeypatch, capsys):
        """detect in empty project -> exits 2 (CliError)."""
        with patch(
            "code_forge.detect.detect_and_init",
            side_effect=CliError(
                "No toolchain detected. L0 has no static "
                "analysis tools."
            ),
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "detect"],
            )
            exit_code = main()

        assert exit_code == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "No toolchain detected" in captured.err

    def test_detect_in_known_subcommands(self):
        """detect is in known_subcommands set (backward compat)."""
        parser = _build_parser()
        args = parser.parse_args(["detect"])
        assert args.subcommand == "detect"


# ---------------------------------------------------------------------------
# resolve-outlet subcommand
# ---------------------------------------------------------------------------


class TestResolveOutletSubcommand:
    """Tests for code-forge resolve-outlet subcommand."""

    def test_resolve_outlet_parser_registered(self):
        """resolve-outlet subcommand is registered in _build_parser."""
        parser = _build_parser()
        args = parser.parse_args(["resolve-outlet"])
        assert args.subcommand == "resolve-outlet"

    def test_resolve_outlet_prints_cli(self, monkeypatch, capsys):
        """Mocked resolve_outlet returns 'cli' -> prints it to stdout, exits 0."""
        with patch(
            "code_forge.outlet_resolver.resolve_outlet",
            return_value="subprocess",
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "resolve-outlet"],
            )
            exit_code = main()

        assert exit_code == EXIT_PASS
        captured = capsys.readouterr()
        assert captured.out.strip() == "subprocess"

    def test_resolve_outlet_prints_inline(self, monkeypatch, capsys):
        """FORGE_OUTLET=inline -> prints 'inline' to stdout, exits 0."""
        with patch(
            "code_forge.outlet_resolver.resolve_outlet",
            return_value="inline",
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "resolve-outlet"],
            )
            exit_code = main()

        assert exit_code == EXIT_PASS
        captured = capsys.readouterr()
        assert captured.out.strip() == "inline"

    def test_resolve_outlet_backend_unreachable_exits_1(
        self, monkeypatch, capsys,
    ):
        """Backend unreachable -> stderr diagnostic, exits 1."""
        with patch(
            "code_forge.outlet_resolver.resolve_outlet",
            side_effect=CliError(
                "Configure a review backend or set "
                "FORGE_OUTLET=inline. Reachability: mock"
            ),
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "resolve-outlet"],
            )
            exit_code = main()

        assert exit_code == EXIT_FAIL
        captured = capsys.readouterr()
        assert "Configure a review backend" in captured.err

    def test_resolve_outlet_invalid_value_exits_2(
        self, monkeypatch, capsys,
    ):
        """Invalid FORGE_OUTLET value -> exits 2 (ValueError)."""
        with patch(
            "code_forge.outlet_resolver.resolve_outlet",
            side_effect=ValueError(
                "invalid outlet 'bogus' from FORGE_OUTLET env "
                "(expected: cli|inline)"
            ),
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "resolve-outlet"],
            )
            exit_code = main()

        assert exit_code == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "invalid outlet" in captured.err

    def test_resolve_outlet_in_known_subcommands(self):
        """resolve-outlet is in known_subcommands set."""
        parser = _build_parser()
        args = parser.parse_args(["resolve-outlet"])
        assert args.subcommand == "resolve-outlet"

    def test_resolve_outlet_stdout_not_stderr(self, monkeypatch, capsys):
        """resolve-outlet prints outlet value to stdout, not stderr."""
        with patch(
            "code_forge.outlet_resolver.resolve_outlet",
            return_value="subprocess",
        ):
            monkeypatch.setattr(
                sys, "argv", ["code-forge", "resolve-outlet"],
            )
            main()

        captured = capsys.readouterr()
        assert "subprocess" in captured.out
