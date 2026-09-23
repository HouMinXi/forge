# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""A loader bug is not the same as an unreadable user config."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_user_config_reader_bug_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import code_forge.user_config as mod

    path = tmp_path / "config.yaml"
    path.write_text("backends: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "user_config_path", lambda: path)

    real_open = open

    def boom(target, *args, **kwargs):
        if str(target) == str(path):
            raise RuntimeError("reader bug")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(RuntimeError, match="reader bug"):
        mod.load_user_backends()


def test_retry_reader_bug_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import code_forge.user_config as mod

    path = tmp_path / "config.yaml"
    path.write_text("retry: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "user_config_path", lambda: path)

    real_open = open

    def boom(target, *args, **kwargs):
        if str(target) == str(path):
            raise RuntimeError("reader bug")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(RuntimeError, match="reader bug"):
        mod.load_user_retry()
