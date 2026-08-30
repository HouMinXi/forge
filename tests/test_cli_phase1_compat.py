# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI backward compatibility tests for deprecated and preserved flags."""


import pytest

from code_forge.cli import _build_parser


class TestPreservedFlags:
    """--registry, --quiet, --version still work.

    Post-subparser: --registry and --quiet are on review subcommand.
    --version remains on root parser.
    """

    def test_registry_flag_accepted(self):
        parser = _build_parser()
        args = parser.parse_args(["review", "--registry", "custom.yaml"])
        assert args.registry == "custom.yaml"

    def test_quiet_flag_accepted(self):
        parser = _build_parser()
        args = parser.parse_args(["review", "--quiet"])
        assert args.quiet is True

    def test_version_exits_zero(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


    # TestStateDirDeprecation and TestStagedDeprecation removed:
    # --state-dir and --staged flags were deleted from the CLI.
