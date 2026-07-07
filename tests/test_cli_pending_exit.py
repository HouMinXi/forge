# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""PENDING verdict must map to EXIT_BUSY (3), not EXIT_PASS (0)."""

from unittest.mock import patch

from code_forge.cli import main
from code_forge.exit_codes import EXIT_BUSY, EXIT_PASS
from code_forge.state import Verdict


class TestPendingExitCode:
    def test_pending_returns_busy(self):
        """Verdict.PENDING -> EXIT_BUSY (3), blocks pre-commit hooks."""
        with patch("code_forge.cli._run", return_value=Verdict.PENDING), \
             patch("sys.argv", ["code-forge", "review"]):
            result = main()
        assert result == EXIT_BUSY

    def test_pass_still_returns_zero(self):
        """Verdict.PASS -> EXIT_PASS (0), unchanged."""
        with patch("code_forge.cli._run", return_value=Verdict.PASS), \
             patch("sys.argv", ["code-forge", "review"]):
            result = main()
        assert result == EXIT_PASS
