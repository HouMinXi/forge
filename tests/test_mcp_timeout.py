# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Per-call MCP deadlines preserve legacy handoff and process ownership."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
import anyio
from anyio import EndOfStream
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from code_forge import mcp_jobs, mcp_server as server


TOOLS = ("forge_review", "forge_gate_check")
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
INVALID = (
    False, True, "1", "", 0, -1, float("nan"), float("inf"), -float("inf"), [], {},
    "null", " null ", "\tnull\r\n", "true", "false", "[]", "{}",
)


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    workspace = AsyncMock(return_value=tmp_path)
    monkeypatch.setattr(server, "_workspace_for", workspace)
    monkeypatch.setattr(server, "_check_backend", Mock())
    monkeypatch.setattr(server, "_validate_backend", Mock())
    monkeypatch.setattr("code_forge.outlet_resolver.load_outlet_from_gate", lambda _: None)
    monkeypatch.delenv("FORGE_OUTLET", raising=False)
    return workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_registered_timeout_schema(name):
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    schema = tools[name].inputSchema
    field = schema["properties"]["timeout_s"]
    assert field["default"] is None
    numeric, null = field["anyOf"]
    assert numeric["type"] == "number" and numeric["exclusiveMinimum"] == 0
    assert "including background execution" in numeric["description"]
    assert null == {"type": "null"}
    assert "timeout_s" not in schema.get("required", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("sdk", [False, True])
async def test_invalid_timeout_precedes_side_effects(name, value, sdk, preflight, monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(server, "_dispatch_cli", dispatch)
    with pytest.raises(ToolError):
        if sdk:
            await server.mcp.call_tool(name, {"timeout_s": value})
        else:
            await getattr(server, name)(timeout_s=value)
    preflight.assert_not_called()
    server._check_backend.assert_not_called()
    server._validate_backend.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("arguments", [{}, {"timeout_s": None}, {"timeout_s": 3}, {"timeout_s": 0.5}])
async def test_sdk_timeout_override_and_omission(name, arguments, preflight, monkeypatch):
    monkeypatch.setenv("FORGE_MCP_JOB_TIMEOUT_S", "777")
    cap = Mock(return_value=777.0)
    dispatch = AsyncMock(return_value=server._make_result("ok", 0, 0.1))
    monkeypatch.setattr(server, "_job_cap_s", cap)
    monkeypatch.setattr(server, "_dispatch_cli", dispatch)
    await server.mcp.call_tool(name, dict(arguments))
    requested = arguments.get("timeout_s")
    assert dispatch.call_args.args[2] == (777.0 if requested is None else requested)
    if requested is None:
        cap.assert_called_once()
        assert "timeout_s" not in dispatch.call_args.kwargs
    else:
        cap.assert_not_called()
        assert dispatch.call_args.kwargs["timeout_s"] == requested
    assert os.environ["FORGE_MCP_JOB_TIMEOUT_S"] == "777"


@pytest.mark.asyncio
async def test_concurrent_timeout_isolation(preflight, monkeypatch):
    monkeypatch.setenv("FORGE_MCP_JOB_TIMEOUT_S", "444")
    monkeypatch.setattr(server, "_job_cap_s", Mock(return_value=444.0))
    seen = []

    async def dispatch(args, workspace, cap, **kwargs):
        await asyncio.sleep(0)
        seen.append((args[0], cap, kwargs.get("timeout_s")))
        return server._make_result("ok", 0, 0.1)

    monkeypatch.setattr(server, "_dispatch_cli", dispatch)
    await asyncio.gather(
        server.forge_review(timeout_s=0.3),
        server.forge_gate_check(timeout_s=31),
        server.forge_review(),
    )
    assert sorted(seen) == sorted([("review", 0.3, 0.3), ("gate-check", 31, 31), ("review", 444.0, None)])
    assert os.environ["FORGE_MCP_JOB_TIMEOUT_S"] == "444"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,elapsed,remaining", [(50.0, 20.0, 30.0), (0.5, 0.5, 0.0), (0.5, 0.7, 0.0)])
async def test_dispatch_handoff_uses_one_deadline(tmp_path, monkeypatch, limit, elapsed, remaining):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: clock.now))
    marker, proc = object(), object()

    async def run(*args, **kwargs):
        assert kwargs["deadline"] == 100.0 + limit
        assert "budget" not in kwargs
        clock.now += elapsed
        return marker, proc, "stderr.log"

    start = Mock(return_value="job")
    monkeypatch.setattr(server, "_run_cli_budgeted", run)
    monkeypatch.setattr(server, "start_job", start)
    result = await server._dispatch_cli(["review"], tmp_path, 999, timeout_s=limit)
    assert start.call_args.args == (marker, proc)
    assert start.call_args.kwargs["max_lifetime_s"] == pytest.approx(remaining)
    assert start.call_args.kwargs["deadline"] == 100.0 + limit
    assert result.structuredContent["poll_after_seconds"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining,expected", [(50.0, 20.0), (0.5, 0.5), (-0.1, 0.0)])
async def test_foreground_budget_subtracts_spawn_time(tmp_path, monkeypatch, remaining, expected):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: clock.now))
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"ok", None)))

    async def spawn(*args, **kwargs):
        clock.now = 105.0
        return proc

    actual = []
    real_wait = asyncio.wait_for

    async def wait(future, timeout):  # noqa: ASYNC109 -- wait_for spy preserves its keyword
        actual.append(timeout)
        return await real_wait(future, timeout=1)

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(server.asyncio, "wait_for", wait)
    result = await server._run_cli_budgeted("review", workspace=tmp_path, deadline=105.0 + remaining)
    assert actual == [pytest.approx(expected)]
    assert result[0] == "ok"


@pytest_asyncio.fixture
async def owned_cli(tmp_path, monkeypatch, preflight):
    if os.name != "posix":
        pytest.skip("POSIX executable and catchable SIGTERM fixture")
    executable = tmp_path / "code-forge"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os,pathlib,signal,sys,time\n"
        "root=pathlib.Path.cwd(); pid=os.getpid()\n"
        "def term(sig,frame):\n"
        " (root/('term-'+str(pid))).write_text(str(sig)); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,term)\n"
        "print('progress',file=sys.stderr,flush=True)\n"
        "ready=root/('pid-'+str(pid)); temporary=ready.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getppid())); os.replace(temporary,ready)\n"
        "if 'finish' in sys.argv: print('finished'); raise SystemExit(0)\n"
        "while True: time.sleep(.01)\n",
        encoding="ascii",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("FORGE_MCP_JOB_TIMEOUT_S", "999")
    processes, paths, handles = [], [], []
    ready_times = {}
    real_spawn = asyncio.create_subprocess_exec
    real_tempfile = server.tempfile.NamedTemporaryFile

    async def spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        ready = tmp_path / f"pid-{process.pid}"
        readiness_deadline = asyncio.get_running_loop().time() + 5
        while not await asyncio.to_thread(ready.exists):
            assert asyncio.get_running_loop().time() < readiness_deadline, "child readiness timed out"
            await asyncio.sleep(0.01)
        ready_times[process.pid] = asyncio.get_running_loop().time()
        return process

    def tempfile(**kwargs):
        handle = real_tempfile(dir=tmp_path, **kwargs)
        paths.append(Path(handle.name))
        handles.append(handle)
        return handle

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(server.tempfile, "NamedTemporaryFile", tempfile)
    try:
        yield tmp_path, processes, paths, handles, ready_times
    finally:
        await mcp_jobs.cleanup_all()
        for process in processes:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        for path in paths:
            path.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_real_sdk_deadline_reaps_and_cleans(owned_cli, name):
    root, processes, paths, handles, ready_times = owned_cli
    arguments = {"timeout_s": 0.3}
    if name == "forge_review":
        arguments.update(contract="scope", focus="focus")
    result = await server.mcp._tool_manager.call_tool(name, arguments)
    ref = result.structuredContent
    assert ref["status"] == "running" and ref["poll_after_seconds"] == 10
    job = mcp_jobs.get_job(ref["job_id"])
    assert job["max_lifetime_s"] == 0.0
    await asyncio.wait_for(asyncio.shield(job["wait_task"]), timeout=3)
    status = await server.forge_job_status(ref["job_id"])
    assert status.structuredContent["status"] == "failed"
    assert status.structuredContent["result"]["verdict"] == "TIMEOUT"
    assert "progress" in status.structuredContent["result"]["output"]
    assert "job exceeded" in status.structuredContent["result"]["output"]
    assert "job exceeded CLI deadline\nprogress\n" in status.structuredContent["result"]["output"]
    assert len(processes) == 1
    process = processes[0]
    assert asyncio.get_running_loop().time() - ready_times[process.pid] < 2
    assert (root / f"pid-{process.pid}").read_text() == str(os.getpid())
    assert (root / f"term-{process.pid}").exists()
    assert process.returncode is not None
    assert process.stdout.at_eof()
    if sys.platform.startswith("linux"):
        assert not await asyncio.to_thread(Path(f"/proc/{process.pid}").exists)
    assert all(handle.closed for handle in handles)
    assert len(paths) == (3 if name == "forge_review" else 1)
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_real_sdk_inline_with_timeout(owned_cli, name):
    _, processes, paths, _, _ = owned_cli
    result = await server.mcp._tool_manager.call_tool(name, {"timeout_s": 2, "baseline": "finish"})
    assert result.structuredContent["verdict"] == "PASS"
    assert result.structuredContent["output"] == "finished\n"
    assert "job_id" not in result.structuredContent
    assert processes[0].returncode == 0
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.asyncio
async def test_real_legacy_relative_cap_keeps_diagnostic(owned_cli):
    root, processes, paths, _, _ = owned_cli
    communication, process, stderr_path = await server._run_cli_budgeted(
        "review", workspace=root, budget=0.01,
    )
    job_id = mcp_jobs.start_job(communication, process, max_lifetime_s=1.2, stderr_log_path=stderr_path)
    job = mcp_jobs.get_job(job_id)
    await asyncio.wait_for(asyncio.shield(job["wait_task"]), timeout=3)
    status = await server.forge_job_status(job_id)
    assert job["deadline"] is None
    assert status.structuredContent["status"] == "failed"
    assert status.structuredContent["result"]["verdict"] == "TIMEOUT"
    assert job["result"]["stderr"] == "job exceeded 1s cap\nprogress\n"
    assert "job exceeded 1s cap\nprogress\n" in status.structuredContent["result"]["output"]
    assert processes[0].returncode is not None and process.stdout.at_eof()
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_real_delayed_watchdog_uses_absolute_deadline(owned_cli, monkeypatch, expired):
    root, processes, paths, _, _ = owned_cli
    release = asyncio.Event()
    captured = {}
    actual_waits = []
    original_run = server._run_cli_budgeted
    original_watchdog = mcp_jobs._wait_for_job
    original_wait = asyncio.wait_for
    loop = asyncio.get_running_loop()

    async def run(*args, **kwargs):
        captured["deadline"] = kwargs["deadline"]
        kwargs["budget"] = 0.01
        return await original_run(*args, **kwargs)

    async def delayed_watchdog(job_id):
        await release.wait()
        await original_watchdog(job_id)

    async def observe_wait(future, timeout):  # noqa: ASYNC109 -- preserve wait_for's API
        if future is captured.get("communication"):
            actual_waits.append((loop.time(), timeout))
        return await original_wait(future, timeout)

    monkeypatch.setattr(server, "_run_cli_budgeted", run)
    monkeypatch.setattr(mcp_jobs, "_wait_for_job", delayed_watchdog)
    monkeypatch.setattr(mcp_jobs.asyncio, "wait_for", observe_wait)
    result = await server._dispatch_cli(
        ["review"], root, 99, timeout_s=1, contract="scope", focus="focus"
    )
    job = mcp_jobs.get_job(result.structuredContent["job_id"])
    captured["communication"] = job["comm_task"]
    assert job["max_lifetime_s"] > 0
    if expired:
        await asyncio.sleep(max(0.0, captured["deadline"] - loop.time()) + 0.03)
    else:
        await asyncio.sleep(0.06)
    release.set()
    await original_wait(asyncio.shield(job["wait_task"]), timeout=3)
    assert len(actual_waits) == 1
    waited_at, allowance = actual_waits[0]
    assert allowance == pytest.approx(max(0.0, captured["deadline"] - waited_at), abs=0.01)
    assert job["deadline"] == captured["deadline"]
    assert (allowance == 0) if expired else (allowance > 0)
    assert job["status"] == "failed" and job["result"]["verdict"] == "TIMEOUT"
    assert processes[0].returncode is not None
    assert (root / f"term-{processes[0].pid}").exists()
    assert processes[0].stdout.at_eof()
    assert len(paths) == 3 and all(not path.exists() for path in paths)


@pytest_asyncio.fixture
async def wire_server(tmp_path):
    """Run actual SDK stdio with observed preflight and a diagnostic dispatcher."""
    observations = tmp_path / "workspace.jsonl"
    bootstrap = (
        "import json,os\nfrom pathlib import Path\n"
        "from code_forge import mcp_server as s\n"
        "root=Path(os.environ['FORGE_PROJECT_DIR'])\n"
        "s._resolve_workspace=lambda:root\n"
        "async def workspace(ctx,**kw):\n"
        " with (root/'workspace.jsonl').open('a') as f:f.write('called\\n')\n"
        " return root\n"
        "async def dispatch(args,workspace,cap,**kw):\n"
        " return s._make_result(json.dumps({'cap':cap,'timeout':kw.get('timeout_s')}),0,0)\n"
        "s._workspace_for=workspace;s._check_backend=lambda *_:None\n"
        "s._validate_backend=lambda *_:None;s._job_cap_s=lambda *_:777\n"
        "s._dispatch_cli=dispatch\n"
        "from code_forge import outlet_resolver\n"
        "outlet_resolver.load_outlet_from_gate=lambda _:None\n"
        "s.main()\n"
    )
    env = dict(os.environ)
    env.update(
        HOME=str(tmp_path / "home"), XDG_CONFIG_HOME=str(tmp_path / "config"),
        FORGE_PROJECT_DIR=str(tmp_path),
        PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT),
                                   *[p for p in sys.path if p and "site-packages" in p]]),
    )
    env.pop("FORGE_OUTLET", None)
    with (tmp_path / "server-stderr.txt").open("wb") as stderr:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", "-u", "-c", bootstrap, cwd=tmp_path, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr,
        )

        async def send(message):
            raw = message if isinstance(message, str) else json.dumps(message)
            process.stdin.write(raw.encode() + b"\n")
            await process.stdin.drain()

        async def request(message):
            await send(message)
            line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            assert line, (tmp_path / "server-stderr.txt").read_text()
            return json.loads(line)

        try:
            reply = await request({
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "timeout-wire", "version": "1"}},
            })
            assert reply["id"] == 0 and "result" in reply
            await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            yield request, observations
            process.stdin.close()
            await asyncio.wait_for(process.wait(), timeout=5)
            assert process.returncode == 0, (tmp_path / "server-stderr.txt").read_text()
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_stdio_rejects_nonfinite_before_sdk_dump(wire_server, name):
    request, observations = wire_server
    for request_id, token in enumerate(("1e309", "-1e309", "NaN", "Infinity", "-Infinity"), 1):
        reply = await request(
            '{"jsonrpc":"2.0","id":' + str(request_id) + ',"method":"tools/call",'
            '"params":{"name":' + json.dumps(name) + ',"arguments":{"timeout_s":' + token + '}}}'
        )
        assert reply["id"] == request_id
        assert reply.get("error", {}).get("code") == -32602, reply
        assert "finite positive" in reply["error"]["message"]
        assert not observations.exists(), "invalid timeout entered workspace preflight"

    for request_id, malformed_name in enumerate(([], {}), 10):
        reply = await request({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                               "params": {"name": malformed_name, "arguments": {"timeout_s": 1}}})
        assert reply["id"] == request_id and reply.get("error", {}).get("code") == -32602
        assert not observations.exists()

    for request_id, arguments in enumerate(({}, {"timeout_s": None}, {"timeout_s": 1}), 20):
        reply = await request({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}})
        assert reply["id"] == request_id and "error" not in reply
        assert not reply["result"].get("isError", False), reply
        result = reply["result"]["structuredContent"]
        value = json.loads(result["output"])
        expected = 1 if arguments.get("timeout_s") is not None else 777
        assert value == {"cap": expected, "timeout": arguments.get("timeout_s")}
    assert observations.read_text().splitlines() == ["called"] * 3
    reply = await request({"jsonrpc": "2.0", "id": 30, "method": "tools/list"})
    assert reply["id"] == 30 and any(tool["name"] == name for tool in reply["result"]["tools"])


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    None, {"name": []}, {"name": {}}, {"name": "forge_init", "arguments": {"timeout_s": float("inf")}},
    {"name": "forge_review"}, {"name": "forge_review", "arguments": []},
    {"name": "forge_review", "arguments": {}},
    {"name": "forge_gate_check", "arguments": {"timeout_s": None}},
    {"name": "forge_review", "arguments": {"timeout_s": 2}},
])
async def test_timeout_stream_preserves_request_and_metadata(params):
    message = SessionMessage(
        message=JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id="same-id", method="tools/call", params=params)),
        metadata=object(),
    )
    attributes = {object(): lambda: "original attribute"}
    incoming = SimpleNamespace(receive=AsyncMock(return_value=message), aclose=AsyncMock(), extra_attributes=attributes)
    outgoing = SimpleNamespace(send=AsyncMock())
    stream = server._TimeoutReceiveStream(incoming, outgoing)
    assert stream.extra_attributes is attributes
    async with stream:
        assert await stream.receive() is message
    incoming.aclose.assert_awaited_once()
    outgoing.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("root", [
    JSONRPCNotification(jsonrpc="2.0", method="notifications/cancelled", params={"requestId": 8}),
    JSONRPCResponse(jsonrpc="2.0", id=8, result={}),
    JSONRPCRequest(jsonrpc="2.0", id=8, method="tools/list"),
    ValueError("original parser error"),
])
async def test_timeout_stream_preserves_other_messages(root):
    message = root if isinstance(root, Exception) else SessionMessage(message=JSONRPCMessage(root))
    incoming = SimpleNamespace(receive=AsyncMock(return_value=message))
    outgoing = SimpleNamespace(send=AsyncMock())
    assert await server._TimeoutReceiveStream(incoming, outgoing).receive() is message
    outgoing.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [asyncio.CancelledError, EndOfStream])
async def test_timeout_stream_propagates_receive_interruption(exception):
    incoming = SimpleNamespace(receive=AsyncMock(side_effect=exception))
    outgoing = SimpleNamespace(send=AsyncMock())
    with pytest.raises(exception):
        await server._TimeoutReceiveStream(incoming, outgoing).receive()
    outgoing.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TOOLS)
async def test_timeout_stream_consumes_rejection_then_returns_next_message(name):
    invalid = SessionMessage(message=JSONRPCMessage(JSONRPCRequest(
        jsonrpc="2.0", id="invalid-id", method="tools/call",
        params={"name": name, "arguments": {"timeout_s": float("inf")}},
    )))
    accepted = SessionMessage(message=JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=9, method="tools/list")))
    incoming = SimpleNamespace(receive=AsyncMock(side_effect=[invalid, accepted]))
    outgoing = SimpleNamespace(send=AsyncMock())
    assert await server._TimeoutReceiveStream(incoming, outgoing).receive() is accepted
    outgoing.send.assert_awaited_once()
    error = outgoing.send.call_args.args[0].message.root
    assert error.id == "invalid-id" and error.error.code == -32602
    assert "finite positive" in error.error.message


@pytest.mark.asyncio
async def test_stdio_adapter_runs_real_session_and_exits_on_eof(monkeypatch):
    """Exercise the real SDK session through the owned stdio override in-process."""
    incoming_send, incoming_receive = anyio.create_memory_object_stream(1)
    outgoing_send, outgoing_receive = anyio.create_memory_object_stream(1)

    @asynccontextmanager
    async def transport():
        yield incoming_receive, outgoing_send

    monkeypatch.setattr(server, "stdio_server", transport)
    instance = server._ForgeFastMCP("timeout-session-test")
    observed = []

    @instance.tool(name="forge_review")
    async def review(timeout_s: float | None = None) -> str:
        observed.append(timeout_s)
        return "accepted"

    async def exchange(raw):
        await incoming_send.send(SessionMessage(message=JSONRPCMessage.model_validate_json(raw)))
        return (await asyncio.wait_for(outgoing_receive.receive(), timeout=3)).message.root

    task = asyncio.create_task(instance.run_stdio_async())
    try:
        initialized = await exchange(json.dumps({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "timeout-test", "version": "1"}},
        }))
        assert initialized.id == 0
        await incoming_send.send(SessionMessage(message=JSONRPCMessage(JSONRPCNotification(
            jsonrpc="2.0", method="notifications/initialized",
        ))))
        rejected = await exchange('{"jsonrpc":"2.0","id":1,"method":"tools/call",'
                                  '"params":{"name":"forge_review","arguments":{"timeout_s":1e309}}}')
        assert rejected.id == 1 and rejected.error.code == -32602
        accepted = await exchange('{"jsonrpc":"2.0","id":2,"method":"tools/call",'
                                  '"params":{"name":"forge_review","arguments":{"timeout_s":null}}}')
        assert accepted.id == 2 and not accepted.result.get("isError", False)
        assert observed == [None]
        await incoming_send.aclose()
        await asyncio.wait_for(task, timeout=3)
        assert task.exception() is None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await incoming_send.aclose()
        await outgoing_receive.aclose()
