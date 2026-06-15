# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Phase 24.1-01: Verdict.DELEGATED wiring for inline outlet."""

import argparse
from unittest.mock import patch

from code_forge.exit_codes import EXIT_DELEGATED, verdict_to_exit
from code_forge.state import Verdict


def _minimal_args(**overrides):
    """Return minimal Namespace for _run() that reaches the outlet check."""
    defaults = dict(
        quiet=True,
        backend=None,
        backend_url=None,
        backend_format=None,
        backend_key_env=None,
        backend_model=None,
        outlet=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestInlineDelegatedVerdict:
    """Inline outlet now returns DELEGATED, not PASS (Phase 24.1 D4)."""

    def test_inline_returns_delegated(self, tmp_path, monkeypatch):
        """_run with outlet=inline returns Verdict.DELEGATED (not PASS)."""
        from code_forge.cli import _run

        monkeypatch.setenv("FORGE_SKIP_WORKTREE_CHECK", "1")
        args = _minimal_args()

        with patch("code_forge.outlet_resolver.resolve_outlet", return_value="inline"), \
             patch("code_forge.cli._load_gate_backends", return_value=[]):
            result = _run(
                args,
                env={"FORGE_SKIP_WORKTREE_CHECK": "1"},
                cwd=tmp_path,
            )

        assert result == Verdict.DELEGATED
        assert result != Verdict.PASS

    def test_inline_stderr_message(self, tmp_path, monkeypatch, capsys):
        """_run with outlet=inline writes DELEGATED notice to stderr."""
        from code_forge.cli import _run

        monkeypatch.setenv("FORGE_SKIP_WORKTREE_CHECK", "1")
        args = _minimal_args()

        with patch("code_forge.outlet_resolver.resolve_outlet", return_value="inline"), \
             patch("code_forge.cli._load_gate_backends", return_value=[]):
            _run(
                args,
                env={"FORGE_SKIP_WORKTREE_CHECK": "1"},
                cwd=tmp_path,
            )

        captured = capsys.readouterr()
        assert "DELEGATED" in captured.err

    def test_inline_exit_code(self):
        """Unit: verdict_to_exit(Verdict.DELEGATED) == 5 (no argparse)."""
        assert verdict_to_exit(Verdict.DELEGATED) == 5
        assert EXIT_DELEGATED == 5
