# SPDX-License-Identifier: Apache-2.0
"""Filesystem worker boundaries preserve MCP outputs and ownership."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, ListRootsResult, Root
from pydantic import ValidationError

import code_forge.mcp_server as server


def context(roots=(), error=None, capable=True):
    session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=SimpleNamespace(roots=capable)),
        list_roots=AsyncMock(side_effect=error, return_value=ListRootsResult(roots=list(roots))),
    )
    return SimpleNamespace(session=session)


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr(server._workspace_cache, "value", None)


class WorkerBarrier:
    def __init__(self, loop, function):
        self.loop = loop
        self.function = function
        self.release = threading.Event()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.thread = None
        self.loop_progress = False

    def heartbeat(self):
        self.loop_progress = True
        self.release.set()

    def __call__(self, *args):
        self.thread = threading.get_ident()
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            assert self.release.wait(2), "filesystem call blocked the event loop"
            return self.function(*args)
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)

    async def close(self):
        self.release.set()
        await asyncio.wait_for(self.finished.wait(), 3)


def workspace_case(kind, tmp_path):
    project = str(tmp_path)
    ctx = context(capable=False)
    helper = "_resolve_workspace"
    if kind == "explicit":
        helper = "_explicit_workspace"
    elif kind == "none":
        ctx = None
        project = ""
    elif kind == "roots":
        helper = "_workspace_from_roots"
        ctx = context([Root(uri=tmp_path.as_uri())])
        project = ""
    elif kind == "rpc":
        ctx = context(error=RuntimeError("closed"))
        project = ""
    elif kind == "empty":
        ctx = context()
        project = ""
    else:
        project = ""
    return ctx, project, helper


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["explicit", "none", "roots", "rpc", "empty", "static"])
async def test_workspace_worker_allows_loop_progress(kind, tmp_path, monkeypatch):
    ctx, project, helper = workspace_case(kind, tmp_path)
    barrier = WorkerBarrier(asyncio.get_running_loop(), lambda *args: tmp_path)
    monkeypatch.setattr(server, helper, barrier)
    task = asyncio.create_task(server._workspace_for(ctx, project))
    try:
        await asyncio.wait_for(barrier.started.wait(), 3)
        barrier.heartbeat()
        assert await asyncio.wait_for(task, 3) == tmp_path
        assert barrier.thread != threading.get_ident()
        assert barrier.loop_progress
        if kind in ("explicit", "none", "rpc"):
            assert server._workspace_cache.value is None
        else:
            assert server._workspace_cache.value == (ctx.session, tmp_path)
    finally:
        await barrier.close()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["explicit", "none", "roots", "rpc", "empty", "static"])
async def test_workspace_cancellation_does_not_publish(kind, tmp_path, monkeypatch):
    ctx, project, helper = workspace_case(kind, tmp_path)
    sentinel = (object(), tmp_path / "cached")
    server._workspace_cache.value = sentinel
    barrier = WorkerBarrier(asyncio.get_running_loop(), lambda *args: tmp_path)
    monkeypatch.setattr(server, helper, barrier)
    task = asyncio.create_task(server._workspace_for(ctx, project))
    try:
        await asyncio.wait_for(barrier.started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert server._workspace_cache.value is sentinel
    finally:
        await barrier.close()
        await asyncio.gather(task, return_exceptions=True)
    assert server._workspace_cache.value is sentinel


@pytest.mark.asyncio
async def test_root_priority_cache_session_and_explicit_override(tmp_path):
    first, chosen, override = (tmp_path / name for name in ("first", "chosen", "override"))
    (chosen / ".code-forge").mkdir(parents=True)
    (chosen / ".code-forge" / "gate.yaml").write_text("outlet: subprocess\n")
    ctx = context([Root(uri=first.as_uri()), Root(uri=chosen.as_uri())])
    assert await server._workspace_for(ctx) == chosen
    assert await server._workspace_for(ctx) == chosen
    ctx.session.list_roots.assert_awaited_once()
    cached = server._workspace_cache.value
    assert await server._workspace_for(ctx, str(override)) == override
    assert server._workspace_cache.value is cached
    other = context([Root(uri=first.as_uri())])
    assert await server._workspace_for(other) == first
    assert server._workspace_cache.value == (other.session, first)


@pytest.mark.asyncio
async def test_cache_binds_session_captured_before_worker(tmp_path, monkeypatch):
    ctx = context([Root(uri=tmp_path.as_uri())])
    session = ctx.session
    barrier = WorkerBarrier(asyncio.get_running_loop(), lambda *args: tmp_path)
    monkeypatch.setattr(server, "_workspace_from_roots", barrier)
    task = asyncio.create_task(server._workspace_for(ctx))
    try:
        await asyncio.wait_for(barrier.started.wait(), 3)
        ctx.session = context(capable=False).session
        barrier.release.set()
        assert await task == tmp_path
        assert server._workspace_cache.value == (session, tmp_path)
    finally:
        await barrier.close()
        await asyncio.gather(task, return_exceptions=True)


def invalid_roots():
    try:
        ListRootsResult.model_validate({"roots": [None]})
    except ValidationError as error:
        return error
    raise AssertionError("fixture must fail validation")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        McpError(ErrorData(code=-32603, message="closed")),
        BrokenResourceError(),
        ClosedResourceError(),
        EndOfStream(),
        invalid_roots(),
        RuntimeError("adapter closed"),
    ],
)
async def test_expected_roots_error_retries_without_cache(error, tmp_path, monkeypatch, capsys):
    ctx = context(error=error)
    monkeypatch.setattr(server, "_resolve_workspace", lambda: tmp_path)
    for _ in range(2):
        assert await server._workspace_for(ctx) == tmp_path
        assert server._workspace_cache.value is None
    assert ctx.session.list_roots.await_count == 2
    assert capsys.readouterr().err.count("code-forge: list_roots failed:") == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ValueError("programming error"), TypeError("bad adapter"), asyncio.CancelledError()]
)
async def test_unexpected_roots_errors_propagate(error):
    ctx = context(error=error)
    with pytest.raises(type(error)):
        await server._workspace_for(ctx)
    assert server._workspace_cache.value is None


def fake_process():
    return SimpleNamespace(communicate=AsyncMock(return_value=(b"out\xff", None)), returncode=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_inline_stderr_worker_output_and_cancellation(cancel, tmp_path, monkeypatch):
    proc = fake_process()
    paths, handles = [], []
    original = server.tempfile.NamedTemporaryFile

    def logfile(**kwargs):
        handle = original(dir=tmp_path, **kwargs)
        paths.append(Path(handle.name))
        handles.append(handle)
        handle.write("progress\n")
        handle.flush()
        return handle

    monkeypatch.setattr(server.tempfile, "NamedTemporaryFile", logfile)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    reap = AsyncMock()
    monkeypatch.setattr(server, "_kill_and_reap", reap)
    barrier = WorkerBarrier(asyncio.get_running_loop(), server._read_and_unlink_stderr)
    monkeypatch.setattr(server, "_read_and_unlink_stderr", barrier)
    task = asyncio.create_task(server._run_cli_budgeted("review", workspace=tmp_path))
    try:
        await asyncio.wait_for(barrier.started.wait(), 3)
        assert handles[0].closed
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            reap.assert_awaited_once()
            assert reap.call_args.args[0] is proc
            assert reap.call_args.args[1].done()
        else:
            barrier.heartbeat()
            stdout, code, elapsed, stderr = await task
            assert (stdout, code, stderr) == ("out\ufffd", 2, "progress\n")
            assert elapsed >= 0
            assert barrier.loop_progress and barrier.thread != threading.get_ident()
            reap.assert_not_awaited()
        assert not paths[0].exists()
    finally:
        await barrier.close()
        await asyncio.gather(task, return_exceptions=True)
        for path in paths:
            path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_job_tail_worker_snapshot_and_cancellation(cancel, tmp_path, monkeypatch):
    log = tmp_path / "progress.log"
    log.write_bytes(b"discard" * 400 + b"tail\xff\n")
    entry = {"status": "running", "created_at": 10, "stderr_log_path": str(log)}
    monkeypatch.setattr(server, "get_job", lambda job_id: entry)
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 15))
    snapshots = []
    original = server._read_stderr_tail

    def tail(snapshot):
        snapshots.append(snapshot)
        return original(snapshot)

    barrier = WorkerBarrier(asyncio.get_running_loop(), tail)
    monkeypatch.setattr(server, "_read_stderr_tail", barrier)
    task = asyncio.create_task(server.forge_job_status("job"))
    try:
        await asyncio.wait_for(barrier.started.wait(), 3)
        entry.update(status="completed", stderr_log_path=None, result={"stdout": "done"})
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            barrier.heartbeat()
            result = await task
            text = result.content[0].text
            expected = log.read_bytes()[-2048:].decode(errors="replace").strip()
            assert text == "Job job: running (5s)\n--- progress ---\n" + expected
            assert result.structuredContent == {
                "job_id": "job",
                "status": "running",
                "poll_after_seconds": 10,
                "result": None,
            }
            assert barrier.loop_progress and barrier.thread != threading.get_ident()
    finally:
        await barrier.close()
        await asyncio.gather(task, return_exceptions=True)
    assert snapshots == [{"stderr_log_path": str(log)}]
    assert entry["status"] == "completed" and entry["stderr_log_path"] is None


@pytest.mark.asyncio
async def test_job_clock_stub_preserves_loop_deadline(monkeypatch):
    loop = asyncio.get_running_loop()
    started = loop.time()
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 15))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(loop.create_future(), 0.02)
    assert loop.time() > started
    assert server.time.monotonic() == 15


def test_real_log_read_helpers_empty_missing_and_invalid_utf8(tmp_path):
    path = tmp_path / "stderr.log"
    assert server._read_and_unlink_stderr(str(path)) == ""
    assert server._read_stderr_tail({"stderr_log_path": str(path)}) == ""
    assert server._read_stderr_tail({}) == ""
    path.write_bytes(b"\xff" + b"a" * 3000 + b"last\xfe\n")
    expected = path.read_bytes().decode(errors="replace")
    assert server._read_stderr_tail({"stderr_log_path": str(path)}) == path.read_bytes()[-2048:].decode(
        errors="replace"
    )
    assert server._read_and_unlink_stderr(str(path)) == expected
    assert not path.exists()
    path.write_bytes(b"")
    assert server._read_and_unlink_stderr(str(path)) == ""
    path.unlink(missing_ok=True)
    assert server._read_and_unlink_stderr(str(path)) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["rpc-error", "invalid-result", "closed-channel"])
async def test_real_sdk_roots_failures_keep_retry_contract(response_kind, tmp_path, monkeypatch):
    import anyio
    from mcp.server.models import InitializationOptions
    from mcp.server.session import ServerSession
    from mcp.shared.message import SessionMessage
    from mcp.types import (
        ClientCapabilities,
        Implementation,
        InitializeRequestParams,
        JSONRPCError,
        JSONRPCMessage,
        JSONRPCResponse,
        RootsCapability,
        ServerCapabilities,
    )

    to_server, server_reader = anyio.create_memory_object_stream(1)
    server_writer, from_server = anyio.create_memory_object_stream(1)
    options = InitializationOptions(
        server_name="test", server_version="1", capabilities=ServerCapabilities()
    )
    monkeypatch.setattr(server, "_resolve_workspace", lambda: tmp_path)
    async with to_server, server_reader, server_writer, from_server:
        async with ServerSession(server_reader, server_writer, options, stateless=True) as session:
            session._client_params = InitializeRequestParams(
                protocolVersion="2025-11-25",
                capabilities=ClientCapabilities(roots=RootsCapability()),
                clientInfo=Implementation(name="test", version="1"),
            )
            ctx = SimpleNamespace(session=session)
            if response_kind == "closed-channel":
                await from_server.aclose()
            for _ in range(2):
                task = asyncio.create_task(server._workspace_for(ctx))
                try:
                    if response_kind != "closed-channel":
                        request = (await from_server.receive()).message.root
                        assert request.method == "roots/list"
                        if response_kind == "rpc-error":
                            response = JSONRPCError(
                                jsonrpc="2.0",
                                id=request.id,
                                error=ErrorData(code=-32603, message="closed"),
                            )
                        else:
                            response = JSONRPCResponse(
                                jsonrpc="2.0", id=request.id, result={"roots": [None]}
                            )
                        await to_server.send(SessionMessage(message=JSONRPCMessage(response)))
                    assert await asyncio.wait_for(task, 3) == tmp_path
                    assert server._workspace_cache.value is None
                    assert not session._response_streams
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert session._request_id == 2


@pytest.mark.asyncio
async def test_cancelled_open_stderr_worker_closes_then_unlinks(tmp_path, monkeypatch):
    loop = asyncio.get_running_loop()
    opened, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    handles, sharing_errors = [], []
    real_open, real_unlink = Path.open, server.os.unlink
    original_worker = server._read_and_unlink_stderr
    real_tempfile = server.tempfile.NamedTemporaryFile
    paths = []

    def tempfile(**kwargs):
        handle = real_tempfile(dir=tmp_path, **kwargs)
        handle.write("owned progress\n")
        handle.flush()
        paths.append(Path(handle.name))
        return handle

    def open_file(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if path in paths and (args[0] if args else kwargs.get("mode", "r")) == "r":
            handles.append(handle)
            read = handle.read

            def blocked_read(*read_args):
                loop.call_soon_threadsafe(opened.set)
                assert release.wait(2), "test did not release the opened reader"
                return read(*read_args)

            handle.read = blocked_read
        return handle

    def unlink(path, *args, **kwargs):
        if Path(path) in paths and any(not handle.closed for handle in handles):
            sharing_errors.append(path)
            raise PermissionError("reader still owns file")
        return real_unlink(path, *args, **kwargs)

    def worker(path):
        try:
            return original_worker(path)
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(server.tempfile, "NamedTemporaryFile", tempfile)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(server.os, "unlink", unlink)
    monkeypatch.setattr(server, "_read_and_unlink_stderr", worker)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_process()))
    reap = AsyncMock()
    monkeypatch.setattr(server, "_kill_and_reap", reap)
    task = asyncio.create_task(server._run_cli_budgeted("review", workspace=tmp_path))
    try:
        await asyncio.wait_for(opened.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reap.assert_awaited_once()
        assert sharing_errors and paths[0].exists()
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 3)
        await asyncio.gather(task, return_exceptions=True)
    assert handles and all(handle.closed for handle in handles)
    assert not paths[0].exists()


@pytest.mark.asyncio
async def test_cache_compares_session_identity(tmp_path):
    class EqualSession(SimpleNamespace):
        __hash__ = None

        def __eq__(self, other):
            return True

    ctx = context([Root(uri=tmp_path.as_uri())])
    ctx.session = EqualSession(**vars(ctx.session))
    old = EqualSession(**vars(context(capable=False).session))
    server._workspace_cache.value = (old, tmp_path / "other")
    assert old == ctx.session and old is not ctx.session
    assert await server._workspace_for(ctx) == tmp_path
    ctx.session.list_roots.assert_awaited_once()
    assert server._workspace_cache.value[0] is ctx.session
