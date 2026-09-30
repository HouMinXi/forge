# SPDX-License-Identifier: Apache-2.0
"""A long-lived MCP server must notice when its source tree changes.

An editable install keeps the imported modules in memory. After main
moves forward the files on disk are newer than the code the process
loaded, and the next lazy import raises ImportError on a symbol the old
module never had. The server should say the source changed and name the
reload, instead of letting that ImportError reach the caller.
"""

from __future__ import annotations

import pytest

import code_forge.mcp_server as srv
from code_forge.mcp_server import ToolError, _null_coerce_call_tool


def test_digest_changes_when_a_source_file_changes(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    (pkg / "b.py").write_text("y = 2\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)

    before = srv._source_digest()
    (pkg / "b.py").write_text("y = 3\n")

    assert srv._source_digest() != before


def test_digest_ignores_files_outside_the_package(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)

    before = srv._source_digest()
    (tmp_path / "notes.txt").write_text("unrelated\n")

    assert srv._source_digest() == before


def test_digest_covers_a_nested_module(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    sub = pkg / "rules"
    sub.mkdir(parents=True)
    (sub / "r.py").write_text("z = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)

    before = srv._source_digest()
    (sub / "r.py").write_text("z = 2\n")

    assert srv._source_digest() != before


@pytest.mark.asyncio
async def test_tool_call_refuses_when_source_moved(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    (pkg / "a.py").write_text("x = 2\n")

    with pytest.raises(ToolError, match="[Rr]eload"):
        await _null_coerce_call_tool("forge_review", {})


@pytest.mark.asyncio
async def test_tool_call_proceeds_when_source_is_unchanged(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    called = {}

    async def fake(name, arguments, **kw):
        called["name"] = name
        return "ok"

    monkeypatch.setattr(srv, "_original_tc", fake)

    assert await _null_coerce_call_tool("forge_review", {}) == "ok"
    assert called["name"] == "forge_review"
