# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for STATE-06 mode resolution (10 cases a-j)."""

import pytest

from forge.mode_resolver import resolve_mode
from forge.state import Mode


class TestCliArgWins:
    """(a) --mode=local wins over FORGE_MODE=ci."""

    def test_cli_local_overrides_env_ci(self):
        result = resolve_mode("local", {"FORGE_MODE": "ci"}, True)
        assert result == Mode.LOCAL

    """(b) --mode=ci wins over TTY=true."""

    def test_cli_ci_overrides_tty(self):
        result = resolve_mode("ci", {}, True)
        assert result == Mode.CI


class TestEnvWins:
    """(c) no flag + FORGE_MODE=ci wins over TTY=true."""

    def test_env_ci_overrides_tty(self):
        result = resolve_mode(None, {"FORGE_MODE": "ci"}, True)
        assert result == Mode.CI


class TestTtyDefault:
    """(d) no flag + no env + TTY=true -> LOCAL."""

    def test_tty_true_is_local(self):
        result = resolve_mode(None, {}, True)
        assert result == Mode.LOCAL

    """(e) no flag + no env + TTY=false -> CI."""

    def test_tty_false_is_ci(self):
        result = resolve_mode(None, {}, False)
        assert result == Mode.CI


class TestCaseInsensitive:
    """(f) FORGE_MODE case variants all work."""

    def test_upper(self):
        assert resolve_mode(None, {"FORGE_MODE": "LOCAL"}, False) == Mode.LOCAL

    def test_mixed(self):
        assert resolve_mode(None, {"FORGE_MODE": "Local"}, False) == Mode.LOCAL

    def test_lower(self):
        assert resolve_mode(None, {"FORGE_MODE": "local"}, False) == Mode.LOCAL


class TestInvalidValues:
    """(g) FORGE_MODE="invalid" raises ValueError."""

    def test_invalid_env(self):
        with pytest.raises(ValueError, match="invalid mode"):
            resolve_mode(None, {"FORGE_MODE": "invalid"}, True)

    """(h) --mode="invalid" raises ValueError."""

    def test_invalid_cli(self):
        with pytest.raises(ValueError, match="invalid mode"):
            resolve_mode("invalid", {}, True)


class TestEmptyEnv:
    """(i) FORGE_MODE="" falls through to TTY default."""

    def test_empty_env_falls_through(self):
        result = resolve_mode(None, {"FORGE_MODE": ""}, True)
        assert result == Mode.LOCAL

    def test_empty_env_falls_through_ci(self):
        result = resolve_mode(None, {"FORGE_MODE": ""}, False)
        assert result == Mode.CI


class TestWhitespaceEnv:
    """(j) FORGE_MODE="  " raises ValueError (not treated as empty)."""

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="invalid mode"):
            resolve_mode(None, {"FORGE_MODE": "  "}, True)
