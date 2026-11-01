# SPDX-License-Identifier: Apache-2.0
"""A long-lived MCP server must notice when its source tree changes.

An editable install keeps the imported modules in memory. After main
moves forward the files on disk are newer than the code the process
loaded, and the next lazy import raises ImportError on a symbol the old
module never had. The server should say the source changed and name the
reload, instead of letting that ImportError reach the caller.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

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


@pytest.fixture
def status_package(tmp_path, monkeypatch):
    package = tmp_path / "code_forge"
    package.mkdir()
    (package / "a.py").write_text("value = 1\n")
    monkeypatch.setattr(srv, "_package_dir", lambda: package)
    monkeypatch.setattr(srv, "_loaded_package_dir", package)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    return package


async def _status():
    result = await srv.mcp._tool_manager.call_tool("forge_server_status", {})
    assert result.isError is False
    wire = result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    assert json.loads(wire)["structuredContent"] == result.structuredContent
    assert json.loads(result.content[0].text) == result.structuredContent
    return result.structuredContent


@pytest.mark.asyncio
async def test_retargeted_import_symlink_keeps_loaded_identity_and_refuses_tools(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    for package, value in ((first, 1), (second, 2)):
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "later.py").write_text(f"value = {value}\n")
    alias = tmp_path / "active"
    alias.symlink_to(first, target_is_directory=True)
    monkeypatch.setattr(srv, "_package_import_file", alias / "__init__.py", raising=False)
    monkeypatch.setattr(srv, "_loaded_package_dir", first)
    monkeypatch.setattr(srv, "_loaded_digest", srv._source_digest())
    before = await _status()
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    after = await _status()
    assert after["source_state"] == "changed"
    assert after["reconnect_required"] is True
    assert after["current_digest"] != before["current_digest"]
    for field in (
        "loaded_digest",
        "package_path",
        "process_id",
        "server_instance_id",
        "module_loaded_at",
    ):
        assert after[field] == before[field]
    assert after["package_path"] == str(first)
    with pytest.raises(ToolError, match="source changed"):
        await _null_coerce_call_tool("forge_review", {})


@pytest.mark.asyncio
async def test_server_status_reports_stable_loaded_identity(status_package):
    first = await _status()
    assert set(first) == {
        "package_path",
        "loaded_digest",
        "current_digest",
        "source_state",
        "reconnect_required",
        "reason",
        "process_id",
        "server_instance_id",
        "module_loaded_at",
        "source_error_type",
        "package_path_is_escaped",
        "package_path_bytes_hex",
    }
    assert first["package_path"] == str(status_package)
    assert first["loaded_digest"] == first["current_digest"] == srv._source_digest()
    assert first["source_state"] == "unchanged"
    assert first["reconnect_required"] is False
    assert first["source_error_type"] is None
    assert first["reason"] and "reconnect" not in first["reason"].lower()
    assert first["process_id"] == os.getpid()
    assert UUID(first["server_instance_id"]).version == 4
    assert datetime.fromisoformat(first["module_loaded_at"]).utcoffset().total_seconds() == 0
    assert await _status() == first


@pytest.mark.asyncio
async def test_server_status_preserves_unicode_package_path(status_package, monkeypatch):
    package = status_package.rename(status_package.with_name("package-\u00e9"))
    monkeypatch.setattr(srv, "_package_dir", lambda: package)
    monkeypatch.setattr(srv, "_loaded_package_dir", package)
    result = await _status()
    assert result["package_path"] == str(package)
    assert result["package_path_is_escaped"] is False
    assert result["package_path_bytes_hex"] is None
    assert result["source_state"] == "unchanged"


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX byte filenames")
@pytest.mark.asyncio
async def test_server_status_serializes_byte_root_without_losing_identity(status_package, monkeypatch):
    package = status_package.rename(status_package.with_name(os.fsdecode(b"package-\xff")))
    monkeypatch.setattr(srv, "_package_dir", lambda: package)
    monkeypatch.setattr(srv, "_loaded_package_dir", package)
    result = await _status()
    literal = package.with_name(r"package-\udcff")
    assert result["package_path"] == str(literal)
    assert result["package_path_is_escaped"] is True
    assert bytes.fromhex(result["package_path_bytes_hex"]) == os.fsencode(package)
    assert os.fsdecode(bytes.fromhex(result["package_path_bytes_hex"])) == str(package)
    assert result["source_state"] == "unchanged"
    literal.mkdir()
    (literal / "a.py").write_bytes((package / "a.py").read_bytes())
    monkeypatch.setattr(srv, "_package_dir", lambda: literal)
    monkeypatch.setattr(srv, "_loaded_package_dir", literal)
    other = await _status()
    assert other["package_path"] == result["package_path"]
    assert other["package_path_is_escaped"] is False
    assert other["package_path_bytes_hex"] is None
    assert other["source_state"] == "unchanged"


@pytest.mark.parametrize("change", ["bytes", "added", "deleted", "renamed"])
@pytest.mark.asyncio
async def test_server_status_remains_available_after_drift(status_package, change):
    before = await _status()
    source = status_package / "a.py"
    if change == "bytes":
        source.write_text("value = 2\n")
    elif change == "added":
        (status_package / "b.py").write_text("new = True\n")
    elif change == "deleted":
        source.unlink()
    else:
        source.rename(status_package / "renamed.py")
    after = await _status()
    assert after["current_digest"] == srv._source_digest() != before["loaded_digest"]
    assert after["source_state"] == "changed"
    assert after["reconnect_required"] is True
    assert after["source_error_type"] is None
    assert "reconnect" in after["reason"].lower()
    for field in (
        "loaded_digest",
        "package_path",
        "process_id",
        "server_instance_id",
        "module_loaded_at",
    ):
        assert after[field] == before[field]
    assert await _status() == after


@pytest.mark.asyncio
async def test_server_status_reports_actual_digest_read_failure(status_package):
    before = await _status()
    (status_package / "unreadable.py").mkdir()
    after = await _status()
    assert after["source_state"] == "unreadable"
    assert after["current_digest"] is None
    assert after["reconnect_required"] is True
    assert after["source_error_type"] == "IsADirectoryError"
    assert after["loaded_digest"] == before["loaded_digest"]
    assert "reconnect" in after["reason"].lower()
    assert "unreadable.py" not in json.dumps(after)


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX byte filenames")
@pytest.mark.asyncio
async def test_server_status_reports_unencodable_source_name(status_package):
    (status_package / os.fsdecode(b"invalid-\xff.py")).write_text("value = 1\n")
    result = await _status()
    assert result["source_state"] == "unreadable"
    assert result["current_digest"] is None and result["reconnect_required"] is True
    assert result["source_error_type"] == "UnicodeEncodeError"
    assert "invalid-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_server_status_does_not_expose_read_error_payload(status_package, monkeypatch):
    original = Path.read_bytes

    def fail_owned_source(path):
        if path == status_package / "a.py":
            raise OSError("private diagnostic detail")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_owned_source)
    result = await _status()
    assert result["source_state"] == "unreadable"
    assert result["current_digest"] is None and result["reconnect_required"] is True
    assert result["source_error_type"] == "OSError"
    assert "private diagnostic detail" not in json.dumps(result)


@pytest.mark.parametrize("state", ["unchanged", "changed", "unreadable"])
@pytest.mark.asyncio
async def test_server_status_does_not_import_or_launch_work(status_package, monkeypatch, state):
    if state == "changed":
        (status_package / "a.py").write_text("changed = True\n")
    elif state == "unreadable":
        (status_package / "unreadable.py").mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("Status touched an execution or configuration helper")

    for name in (
        "_workspace_for",
        "_resolve_workspace",
        "_check_backend",
        "_backend_names_for",
        "_run_cli_simple",
        "_dispatch_cli",
        "start_job",
        "load_user_backends",
    ):
        monkeypatch.setattr(srv, name, forbidden)
    original_import = builtins.__import__

    def no_forge_import(name, *args, **kwargs):
        if name == "code_forge" or name.startswith("code_forge."):
            pytest.fail("Status attempted a Forge import")
        return original_import(name, *args, **kwargs)

    before = {name for name in sys.modules if name.startswith("code_forge.")}
    with monkeypatch.context() as imports:
        imports.setattr(builtins, "__import__", no_forge_import)
        result = await _status()
    assert result["source_state"] == state
    assert {name for name in sys.modules if name.startswith("code_forge.")} == before


@pytest.mark.parametrize(
    "name",
    [
        "forge_review",
        "forge_gate_check",
        "forge_init",
        "forge_trust",
        "forge_resolve_outlet",
        "forge_job_status",
        "unknown_tool",
        "forge_server_status_extra",
        "Forge_server_status",
        "forge_server_status ",
    ],
)
@pytest.mark.asyncio
async def test_only_exact_status_name_bypasses_source_guard(status_package, monkeypatch, name):
    (status_package / "a.py").write_text("changed = True\n")
    dispatched = []

    async def record(*args, **kwargs):
        dispatched.append((args, kwargs))

    monkeypatch.setattr(srv, "_original_tc", record)
    arguments = {"project_dir": None}
    with pytest.raises(ToolError, match="source changed"):
        await _null_coerce_call_tool(name, arguments)
    assert dispatched == []
    assert arguments == {"project_dir": None}


@pytest.mark.asyncio
async def test_healthy_unknown_tool_still_uses_normal_dispatch(status_package):
    with pytest.raises(ToolError, match="Unknown tool"):
        await srv.mcp._tool_manager.call_tool("unknown_tool", {})


@pytest.mark.asyncio
async def test_server_status_does_not_swallow_cancellation(status_package, monkeypatch):
    def interrupted():
        raise asyncio.CancelledError

    monkeypatch.setattr(srv, "_source_digest", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await srv.mcp._tool_manager.call_tool("forge_server_status", {})


def test_package_directory_is_captured_before_later_calls(monkeypatch):
    import code_forge

    original = srv._package_dir()
    monkeypatch.setattr(code_forge, "__file__", "/different/package/__init__.py")
    assert srv._package_dir() == original
