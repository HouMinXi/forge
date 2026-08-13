# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI tests for the verify subcommand's --required-cycles flag.

The floor itself lives in run_verify and is tested there. What the CLI
owes is narrower: reject a value that is not a cycle count at all, hand
the rest through untouched, and tell a user who asked for less than the
repo demands that they are not getting it.

Patching targets are `code_forge.verify.*` rather than
`code_forge.cli.*` because the verify branch imports those names into
local scope on each call, so rebinding them on the cli module would have
no effect.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from code_forge.cli import _build_parser, main
from code_forge.errors import UnreadableGateError
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_PASS
from code_forge.verify import VerifyResult


def _run_verify_cli(monkeypatch, argv, gate_floor):
    """Invoke `code-forge verify [argv]` with git-diff and verify mocked out.

    gate_floor is what read_required_cycles returns, or an exception
    instance to raise. Returns (exit_code, run_verify_mock).
    """
    fake_diff = MagicMock(returncode=0, stdout="", stderr="")
    fake_result = VerifyResult(True, "ok", 3, 3)
    if isinstance(gate_floor, Exception):
        floor_patch = patch(
            "code_forge.verify.read_required_cycles", side_effect=gate_floor,
        )
    else:
        floor_patch = patch(
            "code_forge.verify.read_required_cycles", return_value=gate_floor,
        )
    with patch("subprocess.run", return_value=fake_diff), floor_patch, \
         patch(
             "code_forge.verify.run_verify", return_value=fake_result,
         ) as mock_run_verify:
        monkeypatch.setattr(sys, "argv", ["code-forge", "verify"] + argv)
        exit_code = main()
    return exit_code, mock_run_verify


class TestRequiredCyclesFlagParsing:
    def test_flag_parses_to_int(self):
        parser = _build_parser()
        args = parser.parse_args(["verify", "--required-cycles", "5"])
        assert args.subcommand == "verify"
        assert args.required_cycles == 5

    def test_flag_omitted_defaults_to_none(self):
        parser = _build_parser()
        args = parser.parse_args(["verify"])
        assert args.required_cycles is None

    def test_help_says_the_flag_can_only_tighten(self):
        """A flag that raises what you asked for has to say it does that."""
        parser = _build_parser()
        action = [
            a for a in parser._actions
            if getattr(a, "dest", None) == "subcommand"
        ][0]
        help_text = action.choices["verify"].format_help()
        assert "only tighten" in help_text, help_text


class TestTheCliHandsTheValueThrough:
    """The CLI does not decide the floor; it passes what it was given."""

    def test_flag_reaches_run_verify_unchanged(self, monkeypatch):
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--required-cycles", "5"], gate_floor=3,
        )
        assert exit_code == EXIT_PASS
        _, kwargs = mock_run_verify.call_args
        assert kwargs["required_cycles"] == 5

    def test_a_value_below_the_floor_still_reaches_run_verify(
        self, monkeypatch,
    ):
        """The CLI must not pre-empt the floor by nulling the argument.

        Swallowing it here would leave run_verify unable to tell "no
        preference" from "asked for less", and the floor would depend on
        which caller you came through.
        """
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--required-cycles", "1"], gate_floor=3,
        )
        assert exit_code == EXIT_PASS
        _, kwargs = mock_run_verify.call_args
        assert kwargs["required_cycles"] == 1

    def test_flag_omitted_passes_none(self, monkeypatch):
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, [], gate_floor=3,
        )
        assert exit_code == EXIT_PASS
        _, kwargs = mock_run_verify.call_args
        assert kwargs["required_cycles"] is None


class TestTheUserHearsAboutARaisedFloor:
    def test_asking_below_the_floor_warns(self, monkeypatch, capsys):
        _run_verify_cli(
            monkeypatch, ["--required-cycles", "1"], gate_floor=3,
        )
        err = capsys.readouterr().err
        assert "--required-cycles 1 is below the required 3" in err, err

    def test_quiet_suppresses_the_warning(self, monkeypatch, capsys):
        """--quiet is "exit code only, no output"; the hint must not leak.

        A pre-commit hook that runs verify --quiet is only reading the
        exit code, so a stderr line here would both break the contract
        and leak the repo's policy floor into CI logs.
        """
        _run_verify_cli(
            monkeypatch, ["--quiet", "--required-cycles", "1"], gate_floor=3,
        )
        err = capsys.readouterr().err
        assert err == "", err

    def test_quiet_still_passes_the_value_through(self, monkeypatch):
        """Quiet only silences output; it does not drop the argument."""
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--quiet", "--required-cycles", "1"], gate_floor=3,
        )
        assert exit_code == EXIT_PASS
        _, kwargs = mock_run_verify.call_args
        assert kwargs["required_cycles"] == 1

    def test_asking_above_the_floor_is_silent(self, monkeypatch, capsys):
        _run_verify_cli(
            monkeypatch, ["--required-cycles", "5"], gate_floor=3,
        )
        err = capsys.readouterr().err
        assert "below the" not in err, err

    def test_omitting_the_flag_is_silent(self, monkeypatch, capsys):
        _run_verify_cli(monkeypatch, [], gate_floor=3)
        err = capsys.readouterr().err
        assert "below the" not in err, err

    def test_an_unreadable_gate_does_not_crash_the_warning_path(
        self, monkeypatch,
    ):
        """run_verify reports the unreadable gate; the warning just steps aside."""
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--required-cycles", "1"],
            gate_floor=UnreadableGateError("boom"),
        )
        assert exit_code == EXIT_PASS
        _, kwargs = mock_run_verify.call_args
        assert kwargs["required_cycles"] == 1


class TestValuesThatAreNotACycleCount:
    def test_zero_is_rejected_before_run_verify(self, monkeypatch):
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--required-cycles", "0"], gate_floor=3,
        )
        assert exit_code == EXIT_CLI_ERROR
        mock_run_verify.assert_not_called()

    def test_negative_is_rejected(self, monkeypatch):
        exit_code, mock_run_verify = _run_verify_cli(
            monkeypatch, ["--required-cycles", "-1"], gate_floor=3,
        )
        assert exit_code == EXIT_CLI_ERROR
        mock_run_verify.assert_not_called()
