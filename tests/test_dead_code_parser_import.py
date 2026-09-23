# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Parser-import failures stay separate from a missing optional pack."""
from __future__ import annotations

import pytest

from code_forge import dead_code


def test_missing_parser_pack_leaves_parser_none(monkeypatch) -> None:
    import builtins
    import runpy

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tree_sitter_language_pack":
            raise ImportError("no pack")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    ns = runpy.run_path(
        str(dead_code.__file__),
        run_name="dead_code_reload_missing",
    )
    assert ns["_PYTHON_PARSER"] is None


def test_parser_loader_bug_is_not_swallowed(monkeypatch) -> None:
    import builtins
    import runpy

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tree_sitter_language_pack":
            raise RuntimeError("loader bug")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match="loader bug"):
        runpy.run_path(
            str(dead_code.__file__),
            run_name="dead_code_reload_bug",
        )
