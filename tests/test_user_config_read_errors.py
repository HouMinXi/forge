# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""A loader bug is not the same as an unreadable user config."""
from __future__ import annotations

from pathlib import Path

import pytest

# Capture the real readers before the global backend-isolation fixture runs.
from code_forge.user_config import load_user_backends, load_user_retry


def test_readers_load_the_isolated_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "backends:\n  isolated:\n    model: fixture-model\n"
        "retry:\n  max_attempts: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("code_forge.user_config.user_config_path", lambda: path)
    assert load_user_backends() == {"isolated": {"model": "fixture-model"}}
    assert load_user_retry() == {"max_attempts": 2}


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
        load_user_backends()


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
        load_user_retry()
