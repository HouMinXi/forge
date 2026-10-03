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


@pytest.mark.parametrize(
    "change",
    [
        "newest-content",
        "same-time-content",
        "delete-older",
        "add-older",
        "rename",
        "nested",
        "delete-all",
    ],
)
@pytest.mark.asyncio
async def test_every_tool_call_refuses_changed_package_identity(tmp_path, monkeypatch, change):
    import os

    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    older, newest = pkg / "a.py", pkg / "b.py"
    older.write_text("x = 1\n")
    newest.write_text("y = 1\n")
    os.utime(older, ns=(100, 100))
    os.utime(newest, ns=(200, 200))
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    dispatched = []

    async def downstream(name, arguments, **kw):
        dispatched.append((name, dict(arguments), kw))
        return "unchanged"

    monkeypatch.setattr(srv, "_original_tc", downstream)
    assert await _null_coerce_call_tool("test_tool", {}) == "unchanged"
    if change == "newest-content":
        newest.write_text("y = 2\n")
        os.utime(newest, ns=(201, 201))
    elif change == "same-time-content":
        newest.write_text("y = 2\n")
        os.utime(newest, ns=(200, 200))
    elif change == "delete-older":
        older.unlink()
    elif change == "add-older":
        added = pkg / "c.py"
        added.write_text("z = 1\n")
        os.utime(added, ns=(150, 150))
    elif change == "rename":
        older.rename(pkg / "renamed.py")
    elif change == "nested":
        nested = pkg / "rules" / "r.py"
        nested.parent.mkdir()
        nested.write_text("r = 1\n")
        os.utime(nested, ns=(150, 150))
    else:
        older.unlink()
        newest.unlink()
    for _ in range(3):
        arguments = {"project_dir": None}
        with pytest.raises(ToolError, match="[Rr]eload"):
            await _null_coerce_call_tool("test_tool", arguments)
        assert arguments == {"project_dir": None}
    assert dispatched == [("test_tool", {}, {})]


@pytest.mark.asyncio
async def test_unchanged_identity_preserves_dispatch_and_null_coercion(tmp_path, monkeypatch):
    pkg = tmp_path / "code_forge"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: pkg)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    dispatched = []

    async def downstream(name, arguments, **kw):
        dispatched.append((name, dict(arguments), kw))
        return "unchanged"

    monkeypatch.setattr(srv, "_original_tc", downstream)
    (tmp_path / "notes.py").write_text("unrelated = 1\n")
    (pkg / "notes.txt").write_text("not a package module\n")
    for _ in range(3):
        assert (
            await _null_coerce_call_tool(
                "test_tool", {"project_dir": None, "flag": False, "count": 0}, context="kept"
            )
            == "unchanged"
        )
    assert (
        dispatched
        == [("test_tool", {"project_dir": "", "flag": False, "count": 0}, {"context": "kept"})] * 3
    )
