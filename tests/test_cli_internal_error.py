# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""BaseException leaks from the review pipeline must be visible.

A SystemExit raised inside _run (a forge internal bug, e.g. a library
calling sys.exit) used to propagate to the interpreter top level and
exit silently with code 1 -- indistinguishable from a FAIL verdict and
with zero receipts on disk (surflare pain report 2026-08-16, 2x repro
at ~230K tokens each). It must convert to a printed internal error
with a traceback and EXIT_CLI_ERROR (2). KeyboardInterrupt keeps its
conventional semantics (print, then re-raise).
"""

import pytest

from unittest.mock import patch

from code_forge.cli import main
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_PASS
from code_forge.state import Verdict


class TestSystemExitLeakVisibility:
    def test_system_exit_becomes_visible_cli_error(self, capsys):
        with patch("code_forge.cli._run", side_effect=SystemExit(1)), \
                patch("sys.argv", ["code-forge", "review"]):
            result = main()
        assert result == EXIT_CLI_ERROR
        err = capsys.readouterr().err
        assert "internal error" in err
        assert "SystemExit" in err

    def test_system_exit_traceback_names_this_file(self, capsys):
        """The traceback must show the raise origin, not just a summary."""
        def _raiser(*args, **kwargs):
            raise SystemExit(1)

        with patch("code_forge.cli._run", side_effect=_raiser), \
                patch("sys.argv", ["code-forge", "review"]):
            main()
        assert "test_cli_internal_error" in capsys.readouterr().err

    def test_bare_system_exit_reports_code_zero(self, capsys):
        """sys.exit() with no argument has code None; the message must
        report the effective interpreter code (0), not 'None'."""
        with patch("code_forge.cli._run", side_effect=SystemExit()), \
                patch("sys.argv", ["code-forge", "review"]):
            result = main()
        assert result == EXIT_CLI_ERROR
        err = capsys.readouterr().err
        assert "SystemExit(0)" in err
        assert "SystemExit(None)" not in err

    def test_normal_pass_untouched(self):
        """Sanity: a normal verdict still maps to its usual exit code."""
        with patch("code_forge.cli._run", return_value=Verdict.PASS), \
                patch("sys.argv", ["code-forge", "review"]):
            result = main()
        assert result == EXIT_PASS


class TestKeyboardInterrupt:
    def test_keyboard_interrupt_prints_and_exits_130(self, capsys):
        with patch("code_forge.cli._run", side_effect=KeyboardInterrupt()), \
                patch("sys.argv", ["code-forge", "review"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 130
        assert "interrupted" in capsys.readouterr().err
