"""Explicit startup authority, private historical projection and owned read lifetime."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

import pytest
from code_forge import _advisory_diagnostics as storage
from code_forge import _diagnostic_history as history
from code_forge import _mcp_diagnostic_history as bridge

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "repo"
    tickets = tmp_path / "tickets"
    root.mkdir(mode=0o700)
    tickets.mkdir(mode=0o700)
    context = storage.ExpectedContext(str(uuid4()), str(uuid4()), "private-source", "a" * 64, 1)
    pub = storage.publish_observation(
        storage.bind_storage(root), storage.make_observation(context, "no_named_targets")
    )
    path = history.export_ticket(tickets, pub, context)
    env = {"FORGE_DIAGNOSTIC_TICKET_DIR": str(tickets), "FORGE_DIAGNOSTIC_STORAGE_ROOT": str(root)}
    return root, tickets, context, pub, path, env


def test_default_off_and_two_explicit_directories(sample):
    *_, path, env = sample
    assert bridge.configure({}) is None
    for partial in ({k: v} for k, v in env.items()):
        with pytest.raises(history.TicketError, match="invalid_history_configuration"):
            bridge.configure(partial)
    with pytest.raises(history.TicketError):
        bridge.read(None, path.name)


@pytest.mark.parametrize(
    "state", ["not_requested", "not_run", "no_named_targets", "targets_resolved", "unavailable"]
)
def test_public_projection_all_states(sample, state):
    root, tickets, context, _, _, env = sample
    context = replace(context, observation_sequence=2)
    pub = storage.publish_observation(
        storage.bind_storage(root), storage.make_observation(context, state)
    )
    path = history.export_ticket(tickets, pub, context)
    result = bridge.read(bridge.configure(env), path.name)
    assert result["analysis_state"] == state and result["current_run_credit"] is False
    assert set(result) == {"kind", "current_run_credit", "analysis_state", "diagnostics"}
    assert (len(result["diagnostics"]) == 1) == (state == "no_named_targets")
    text = json.dumps(result)
    for private in (
        str(root),
        str(tickets),
        context.execution_id,
        context.repository_id,
        context.source_hash,
        context.diff_sha256,
    ):
        assert private not in text


@pytest.mark.parametrize(
    "name",
    [
        "",
        "..",
        "../secret",
        "/tmp/secret",
        "a/b",
        "a\\b",
        "ticket.json",
        "LATEST",
        "00000000-0000-0000-0000-000000000000.diagnostic-ticket.json\n",
        None,
    ],
)
def test_client_cannot_select_path(sample, name):
    with pytest.raises(history.TicketError, match="historical_diagnostic_unavailable"):
        bridge.read(bridge.configure(sample[-1]), name)


@pytest.mark.parametrize("which", ["tickets", "storage"])
@pytest.mark.parametrize("change", ["replace", "mode", "symlink"])
def test_startup_directory_authority_cannot_be_rebound(sample, which, change):
    root, tickets, _, _, path, env = sample
    access = bridge.configure(env)
    target = tickets if which == "tickets" else root
    if change == "mode":
        target.chmod(0o755)
    else:
        saved = target.with_name(target.name + "-original")
        target.rename(saved)
        if change == "replace":
            target.mkdir(mode=0o700)
        else:
            target.symlink_to(saved, target_is_directory=True)
    with pytest.raises(history.TicketError):
        bridge.read(access, path.name)


def test_ticket_cannot_authorize_another_storage_root(sample, tmp_path):
    _, tickets, context, _, _, env = sample
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    pub = storage.publish_observation(
        storage.bind_storage(foreign), storage.make_observation(context, "targets_resolved")
    )
    path = history.export_ticket(tickets, pub, context)
    with pytest.raises(history.TicketError):
        bridge.read(bridge.configure(env), path.name)


@pytest.mark.parametrize("change", ["corrupt", "symlink", "fifo", "mode", "missing", "record"])
def test_strict_reader_refuses_invalid_inputs(sample, change):
    _, _, _, pub, path, env = sample
    access = bridge.configure(env)
    if change == "corrupt":
        path.write_text("{}")
    elif change == "mode":
        path.chmod(0o644)
    elif change == "record":
        loc = pub.locator
        loc.binding.source_root.joinpath(*(n for n, _ in loc.directories), loc.filename).write_text("{}")
    else:
        old = path.with_suffix(".saved")
        path.rename(old)
        if change == "symlink":
            path.symlink_to(old)
        elif change == "fifo":
            os.mkfifo(path, 0o600)
    with pytest.raises(history.TicketError):
        bridge.read(access, path.name)


@pytest.mark.asyncio
async def test_sdk_tool_projection_and_schema(sample, monkeypatch):
    from code_forge import mcp_server as srv

    monkeypatch.setattr(srv, "_diagnostic_history_access", bridge.configure(sample[-1]))
    monkeypatch.setattr(srv, "_simple_calls_closing", False)
    monkeypatch.setattr(srv, "_shutting_down", False)
    result = await srv.mcp._tool_manager.call_tool(
        "forge_diagnostic_history", {"ticket": sample[-2].name}
    )
    assert result.structuredContent["current_run_credit"] is False
    tool = srv.mcp._tool_manager._tools["forge_diagnostic_history"]
    assert set(tool.parameters["properties"]) == {"ticket"}
    assert tool.annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_cancel_drains_owned_worker_before_propagating(sample, monkeypatch):
    from code_forge import mcp_server as srv

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = bridge.read

    def slow(*args):
        entered.set()
        assert release.wait(5)
        try:
            return original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(bridge, "read", slow)
    monkeypatch.setattr(srv, "_diagnostic_history_access", bridge.configure(sample[-1]))
    monkeypatch.setattr(srv, "_simple_calls_closing", False)
    monkeypatch.setattr(srv, "_shutting_down", False)
    task = asyncio.create_task(srv.forge_diagnostic_history(sample[-2].name))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and task in srv._active_simple_calls
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert finished.is_set() and task not in srv._active_simple_calls
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_stdio_restart_and_job_independence(sample):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, **sample[-1], "PYTHONPATH": _SOURCE}
    for _ in range(2):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "code_forge.mcp_server"], env=env
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("forge_diagnostic_history", {"ticket": sample[-2].name})
            assert not result.isError
            assert result.structuredContent["analysis_state"] == "no_named_targets"
            assert result.structuredContent["current_run_credit"] is False
            rejected = await session.call_tool("forge_diagnostic_history", {"ticket": str(sample[-2])})
            assert rejected.isError
            text = str(rejected)
            assert str(sample[0]) not in text and str(sample[1]) not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_anyio_cancel_scope_cannot_deliver_success(sample, monkeypatch, corrupt):
    import anyio
    from code_forge import mcp_server as srv

    monkeypatch.setattr(srv, "_diagnostic_history_access", bridge.configure(sample[-1]))
    monkeypatch.setattr(srv, "_simple_calls_closing", False)
    monkeypatch.setattr(srv, "_shutting_down", False)
    if corrupt:
        sample[-2].write_text("{}")
    delivered = False
    with anyio.CancelScope() as scope:
        scope.cancel()
        await srv.forge_diagnostic_history(sample[-2].name)
        delivered = True
    assert not delivered and not srv._active_simple_calls


@pytest.mark.asyncio
async def test_shutdown_drains_read_before_job_cleanup(sample, monkeypatch):
    from code_forge import mcp_server as srv

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = bridge.read

    def slow(*args):
        entered.set()
        assert release.wait(5)
        try:
            return original(*args)
        finally:
            finished.set()

    cleanup_seen = []

    async def cleanup_jobs():
        cleanup_seen.append(finished.is_set())

    monkeypatch.setattr(bridge, "read", slow)
    monkeypatch.setattr(srv, "cleanup_all", cleanup_jobs)
    monkeypatch.setattr(srv, "_diagnostic_history_access", bridge.configure(sample[-1]))
    monkeypatch.setattr(srv, "_simple_calls_closing", False)
    monkeypatch.setattr(srv, "_shutting_down", False)
    task = asyncio.create_task(srv.forge_diagnostic_history(sample[-2].name))
    cleanup = None
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        cleanup = asyncio.create_task(srv._cleanup_mcp_processes())
        await asyncio.sleep(0.03)
        assert not cleanup.done() and not cleanup_seen
        release.set()
        await asyncio.wait_for(cleanup, 5)
        assert task.cancelled() and cleanup_seen == [True]
        assert not srv._active_simple_calls
        with pytest.raises(asyncio.CancelledError):
            await srv.forge_diagnostic_history(sample[-2].name)
    finally:
        release.set()
        await asyncio.gather(task, *([cleanup] if cleanup else []), return_exceptions=True)


def test_directory_replacement_during_read_refuses_delivery(sample, monkeypatch):
    _, tickets, _, _, path, env = sample
    access = bridge.configure(env)
    original = storage.read_observation

    def replace_after_read(*args):
        value = original(*args)
        tickets.rename(tickets.with_name("old-tickets"))
        tickets.mkdir(mode=0o700)
        return value

    monkeypatch.setattr(storage, "read_observation", replace_after_read)
    with pytest.raises(history.TicketError):
        bridge.read(access, path.name)


@pytest.mark.asyncio
async def test_real_stdio_default_off(sample):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {k: v for k, v in os.environ.items() if k not in sample[-1]}
    env["PYTHONPATH"] = _SOURCE
    params = StdioServerParameters(command=sys.executable, args=["-m", "code_forge.mcp_server"], env=env)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        result = await session.call_tool("forge_diagnostic_history", {"ticket": sample[-2].name})
        assert result.isError
        assert "historical diagnostic unavailable" in str(result)
        assert str(sample[0]) not in str(result)


@pytest.mark.parametrize("change", ["missing", "alias", "relative", "empty"])
def test_startup_rejects_invalid_directory_without_creating_it(sample, change):
    _, tickets, _, _, _, env = sample
    if change == "missing":
        value = str(tickets / "missing")
    elif change == "alias":
        alias = tickets.with_name("alias")
        alias.symlink_to(tickets)
        value = str(alias)
    elif change == "relative":
        value = "tickets"
    else:
        value = ""
    with pytest.raises(history.TicketError):
        bridge.configure({**env, "FORGE_DIAGNOSTIC_TICKET_DIR": value})
    assert not (tickets / "missing").exists()
