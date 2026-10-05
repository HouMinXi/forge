# SPDX-License-Identifier: Apache-2.0
"""Cancellation and communication failures must reap simple CLI children."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from code_forge import mcp_server

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process-group fixture")


@pytest.fixture(autouse=True)
def reset_shutdown_state():
    old_shutting_down = mcp_server._shutting_down
    old_calls_closing = mcp_server._simple_calls_closing
    old_cleanup_task = mcp_server._mcp_cleanup_task
    old_active_calls = mcp_server._active_simple_calls
    mcp_server._shutting_down = False
    mcp_server._simple_calls_closing = False
    mcp_server._mcp_cleanup_task = None
    mcp_server._active_simple_calls = set()
    yield
    assert not mcp_server._active_simple_calls
    mcp_server._shutting_down = old_shutting_down
    mcp_server._simple_calls_closing = old_calls_closing
    mcp_server._mcp_cleanup_task = old_cleanup_task
    mcp_server._active_simple_calls = old_active_calls


@pytest.fixture
def cli_child(tmp_path, monkeypatch):
    executable = tmp_path / "code-forge"
    ready = tmp_path / "ready.pid"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, sys, time\n"
        "if sys.argv[1] in ('signal-wait', 'init'):\n"
        "    def record_signal(sig, frame):\n"
        "        pathlib.Path('term.received').write_text(str(sig), encoding='ascii')\n"
        "        raise SystemExit(0)\n"
        "    signal.signal(signal.SIGTERM, record_signal)\n"
        "    signal.signal(signal.SIGINT, record_signal)\n"
        "if sys.argv[1] == 'ignore-term':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "ready_tmp = pathlib.Path('ready.pid.tmp')\n"
        "ready_tmp.write_text(str(os.getpid()), encoding='ascii')\n"
        "if sys.argv[1] == 'paused-publish':\n"
        "    pathlib.Path('publish.paused').write_text('paused', encoding='ascii')\n"
        "    while not pathlib.Path('publish.release').exists(): time.sleep(0.01)\n"
        "os.replace(ready_tmp, 'ready.pid')\n"
        "if sys.argv[1] == 'paused-publish': raise SystemExit(0)\n"
        "if sys.argv[1] == 'normal':\n"
        "    print('output')\n"
        "    print('diagnostic', file=sys.stderr)\n"
        "    raise SystemExit(7)\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return tmp_path, ready


async def wait_ready(path: Path, wait_seconds: float = 5.0) -> int:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            contents = await asyncio.to_thread(path.read_text, encoding="ascii")
        except FileNotFoundError:
            await asyncio.sleep(0.01)
            continue
        return int(contents)
    raise TimeoutError("CLI child did not publish readiness")


async def wait_for_file(path: Path, wait_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"CLI child did not create {path.name}")


def capture_spawn(
    monkeypatch,
    *,
    create_return_gate: asyncio.Event | None = None,
    return_gate: asyncio.Event | None = None,
):
    spawned: list[asyncio.subprocess.Process] = []
    emergency_handles: list[asyncio.subprocess.Process] = []
    spawn_captured = asyncio.Event()
    spawn_returned = asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec

    async def held_real_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        emergency_handles.append(proc)
        if create_return_gate is not None:
            await create_return_gate.wait()
        return proc

    async def observe_spawn(*args, **kwargs):
        proc = await held_real_spawn(*args, **kwargs)
        spawned.append(proc)
        spawn_captured.set()
        if return_gate is not None:
            await return_gate.wait()
        spawn_returned.set()
        return proc

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", observe_spawn)
    return spawned, spawn_captured, spawn_returned, emergency_handles


async def wait_for_spawn_capture(spawn_captured: asyncio.Event, wait_seconds: float = 5.0) -> None:
    await asyncio.wait_for(spawn_captured.wait(), timeout=wait_seconds)


async def wait_for_spawn_return(spawn_returned: asyncio.Event, wait_seconds: float = 5.0) -> None:
    await asyncio.wait_for(spawn_returned.wait(), timeout=wait_seconds)


async def reap_owned(spawned: list[asyncio.subprocess.Process]) -> None:
    for proc in spawned:
        if proc.returncode is None:
            proc.kill()
        await asyncio.wait_for(proc.communicate(), timeout=5.0)


def _start_ticks(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    return int(raw[raw.rfind(")") + 2 :].split()[19])


def _read_mcp_line(stream, timeout: float) -> bytes:
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError("MCP server did not answer on stdio")
    line = stream.readline()
    if not line:
        raise EOFError("MCP server closed stdio")
    return line


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _start_mcp_server(workspace: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-B", "-u", "-m", "code_forge.mcp_server"],
        cwd=workspace,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _install_communicate_probe(probe_dir: Path) -> None:
    """Add a fixture-only marker when the MCP server starts communicating with its CLI."""
    probe_dir.mkdir()
    (probe_dir / "sitecustomize.py").write_text(
        "import asyncio, json, os\n"
        "_marker = os.environ.get('FORGE_TEST_COMMUNICATE_MARKER')\n"
        "_owned = set()\n"
        "_real_create = asyncio.create_subprocess_exec\n"
        "async def _observe_create(*args, **kwargs):\n"
        "    proc = await _real_create(*args, **kwargs)\n"
        "    _owned.add(proc.pid)\n"
        "    return proc\n"
        "asyncio.create_subprocess_exec = _observe_create\n"
        "_real_communicate = asyncio.subprocess.Process.communicate\n"
        "async def _observe_communicate(self, *args, **kwargs):\n"
        "    if _marker and self.pid in _owned:\n"
        "        raw = open(f'/proc/{os.getpid()}/stat', encoding='ascii').read()\n"
        "        start = raw[raw.rfind(')') + 2:].split()[19]\n"
        "        row = json.dumps({'server_pid': os.getpid(), 'server_start_ticks': start, 'child_pid': self.pid}) + '\\n'\n"
        "        fd = os.open(_marker, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)\n"
        "        try: os.write(fd, row.encode('ascii'))\n"
        "        finally: os.close(fd)\n"
        "    return await _real_communicate(self, *args, **kwargs)\n"
        "asyncio.subprocess.Process.communicate = _observe_communicate\n",
        encoding="utf-8",
    )


async def wait_for_communicate_entry(
    marker: Path, server_pid: int, server_start: int, child_pid: int, wait_seconds: float = 5.0
) -> None:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            rows = await asyncio.to_thread(marker.read_text, encoding="ascii")
        except FileNotFoundError:
            rows = ""
        for row in rows.splitlines():
            event = json.loads(row)
            if (
                event.get("server_pid") == server_pid
                and event.get("server_start_ticks") == str(server_start)
                and event.get("child_pid") == child_pid
            ):
                return
        await asyncio.sleep(0.01)
    raise TimeoutError("MCP server did not enter communicate for the ready CLI child")


@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_all_simple_calls(cli_child, tmp_path, monkeypatch):
    workspace, ready = cli_child
    second_workspace = tmp_path / "second-workspace"
    second_workspace.mkdir()
    second_cli = second_workspace / "code-forge"
    second_cli.write_bytes((workspace / "code-forge").read_bytes())
    second_cli.chmod(0o700)
    second_ready = second_workspace / "ready.pid"
    monkeypatch.setenv("PATH", f"{workspace}:{second_workspace}{os.pathsep}{os.environ['PATH']}")
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)
    cleanup_calls = 0
    real_cleanup_all = mcp_server.cleanup_all

    async def count_cleanup_all():
        nonlocal cleanup_calls
        cleanup_calls += 1
        await real_cleanup_all()

    monkeypatch.setattr(mcp_server, "cleanup_all", count_cleanup_all)
    first = asyncio.create_task(mcp_server._run_cli_simple("wait", workspace=workspace))
    second = asyncio.create_task(mcp_server._run_cli_simple("wait", workspace=second_workspace))
    try:
        first_pid, second_pid = await asyncio.gather(wait_ready(ready), wait_ready(second_ready))
        await wait_for_spawn_return(spawn_returned)
        assert sorted(proc.pid for proc in spawned) == sorted([first_pid, second_pid])
        assert first in mcp_server._active_simple_calls
        assert second in mcp_server._active_simple_calls

        shutdown1 = asyncio.create_task(mcp_server._await_mcp_cleanup())
        shutdown2 = asyncio.create_task(mcp_server._await_mcp_cleanup())
        await asyncio.wait_for(asyncio.gather(shutdown1, shutdown2), timeout=12.0)
        assert all(task.cancelled() for task in (first, second))
        assert all(proc.returncode is not None for proc in spawned)
        assert not mcp_server._active_simple_calls
        assert mcp_server._simple_calls_closing
        assert cleanup_calls == 1, "overlapping shutdown callers ran cleanup_all more than once"

        with pytest.raises(asyncio.CancelledError):
            await mcp_server._run_cli_simple("normal", workspace=workspace)
        assert len(emergency_handles) == 2, "shutdown gate spawned a new simple CLI"
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
async def test_shielded_cleanup_propagates_waiter_cancellation_after_completion(monkeypatch):
    cleanup_started = asyncio.Event()
    helper_waiting = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    original_shield = asyncio.shield

    async def cleanup():
        cleanup_started.set()
        await release_cleanup.wait()
        cleanup_finished.set()

    def observe_shield(awaitable):
        helper_waiting.set()
        return original_shield(awaitable)

    monkeypatch.setattr(mcp_server.asyncio, "shield", observe_shield)
    cleanup_task = asyncio.create_task(cleanup())
    helper_task = asyncio.create_task(mcp_server._wait_for_shielded_task(cleanup_task))
    await cleanup_started.wait()
    await helper_waiting.wait()
    helper_task.cancel()
    await asyncio.sleep(0)
    assert not helper_task.done(), "waiter cancellation interrupted owned cleanup"
    assert not cleanup_finished.is_set()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(helper_task, timeout=2.0)
    assert cleanup_task.done()
    assert cleanup_finished.is_set()


@pytest.mark.asyncio
async def test_shielded_cleanup_failure_is_logged_before_propagating_cancellation(monkeypatch, caplog):
    cleanup_started = asyncio.Event()
    helper_waiting = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    original_shield = asyncio.shield

    async def cleanup():
        cleanup_started.set()
        await release_cleanup.wait()
        cleanup_finished.set()
        raise RuntimeError("controlled owned cleanup failure")

    def observe_shield(awaitable):
        helper_waiting.set()
        return original_shield(awaitable)

    monkeypatch.setattr(mcp_server.asyncio, "shield", observe_shield)
    cleanup_task = asyncio.create_task(cleanup())
    helper_task = asyncio.create_task(mcp_server._wait_for_shielded_task(cleanup_task))
    await cleanup_started.wait()
    await helper_waiting.wait()
    helper_task.cancel()
    await asyncio.sleep(0)
    assert not helper_task.done(), "waiter cancellation interrupted owned cleanup"

    with caplog.at_level(logging.ERROR, logger=mcp_server.__name__):
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(helper_task, timeout=2.0)

    assert cleanup_task.done()
    assert cleanup_finished.is_set()
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "owned cleanup failed while caller cancellation was pending"
    )
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert str(record.exc_info[1]) == "controlled owned cleanup failure"


@pytest.mark.asyncio
async def test_shutdown_logs_non_cancellation_request_failure(monkeypatch, caplog):
    request_started = asyncio.Event()
    cleanup_all = AsyncMock()

    async def request_fails_during_cancellation():
        request_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("controlled request cleanup failure") from exc

    request_task = asyncio.create_task(request_fails_during_cancellation())
    await request_started.wait()
    mcp_server._active_simple_calls.add(request_task)
    monkeypatch.setattr(mcp_server, "cleanup_all", cleanup_all)

    try:
        with caplog.at_level(logging.ERROR, logger=mcp_server.__name__):
            await mcp_server._cleanup_mcp_processes()
    finally:
        mcp_server._active_simple_calls.discard(request_task)

    assert request_task.done() and not request_task.cancelled()
    assert isinstance(request_task.exception(), RuntimeError)
    cleanup_all.assert_awaited_once()
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "simple MCP request failed during shutdown"
    )
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert str(record.exc_info[1]) == "controlled request cleanup failure"


@pytest.mark.asyncio
async def test_lifespan_exit_cancels_and_awaits_simple_call(cli_child, monkeypatch):
    workspace, ready = cli_child
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)
    loop = asyncio.get_running_loop()
    with (
        patch.object(mcp_server, "_install_pdeathsig"),
        patch.object(mcp_server, "_resolve_workspace", return_value=workspace),
        patch.object(mcp_server, "load_user_backends", return_value={}),
        patch("code_forge.cli._load_gate_backends", return_value=([], {"backends": {}})),
        patch.object(loop, "add_signal_handler"),
    ):
        task = None
        try:
            async with mcp_server.lifespan(mcp_server.mcp):
                task = asyncio.create_task(mcp_server._run_cli_simple("wait", workspace=workspace))
                child_pid = await wait_ready(ready)
                await wait_for_spawn_return(spawn_returned)
                assert spawned[0].pid == child_pid
                assert task in mcp_server._active_simple_calls
            assert task.cancelled()
            assert spawned[0].returncode is not None, "lifespan returned with a live simple CLI child"
            assert not mcp_server._active_simple_calls
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await reap_owned(emergency_handles)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signum", "expected_exit"),
    [(signal.SIGTERM, 128 + signal.SIGTERM), (signal.SIGINT, 128 + signal.SIGINT)],
)
@pytest.mark.skipif(
    not hasattr(os, "pidfd_open") or not sys.platform.startswith("linux"), reason="requires Linux pidfds"
)
async def test_real_mcp_group_signal_reaps_simple_cli_child(
    cli_child, tmp_path, monkeypatch, signum, expected_exit
):
    workspace, ready = cli_child
    server_home = tmp_path / "server-home"
    server_home.mkdir()
    config_home = server_home / ".config"
    config_home.mkdir()
    project_root = _project_root()
    site_paths = [path for path in sys.path if path and "site-packages" in path]
    probe_dir = tmp_path / "mcp-test-bootstrap"
    communicate_marker = tmp_path / "communicate-events.jsonl"
    _install_communicate_probe(probe_dir)
    env = {
        "HOME": str(server_home),
        "XDG_CONFIG_HOME": str(config_home),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": os.pathsep.join([str(probe_dir), str(project_root / "src"), *site_paths]),
        "FORGE_PROJECT_DIR": str(workspace),
        "FORGE_TEST_COMMUNICATE_MARKER": str(communicate_marker),
        "PYTHONUNBUFFERED": "1",
    }
    server = None
    child_fd = None
    child_pid = None
    child_start = None
    try:
        server = _start_mcp_server(workspace, env)
        assert os.getpgid(server.pid) == server.pid
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "shutdown-test", "version": "1"},
            },
        }
        server.stdin.write((json.dumps(initialize) + "\n").encode())
        server.stdin.flush()
        response = json.loads(await asyncio.to_thread(_read_mcp_line, server.stdout, 10.0))
        assert response.get("id") == 1 and "result" in response
        server.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        server.stdin.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "forge_init",
                            "arguments": {"project_dir": str(workspace)},
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        server.stdin.flush()
        child_pid = await wait_ready(ready)
        child_start = _start_ticks(child_pid)
        child_fd = os.pidfd_open(child_pid)
        assert _start_ticks(child_pid) == child_start, "simple CLI child identity changed during capture"
        assert os.getpgid(child_pid) != server.pid, "simple child was not isolated from server group"
        server_start = _start_ticks(server.pid)
        await wait_for_communicate_entry(communicate_marker, server.pid, server_start, child_pid)

        os.killpg(server.pid, signum)
        server_exit = await asyncio.wait_for(asyncio.to_thread(server.wait, 12.0), timeout=13.0)
        assert server_exit == expected_exit
        child_poll = select.poll()
        child_poll.register(child_fd, select.POLLIN)
        assert child_poll.poll(12000), "server exited while the simple CLI child remained alive"
        assert (workspace / "term.received").read_text(encoding="ascii") == str(signal.SIGTERM)
    finally:
        try:
            if server is not None and server.poll() is None:
                server.send_signal(signal.SIGKILL)
                await asyncio.to_thread(server.wait, 5.0)
        finally:
            try:
                if child_fd is None and child_pid is not None and child_start is not None:
                    try:
                        fallback_fd = os.pidfd_open(child_pid)
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                    else:
                        try:
                            if _start_ticks(child_pid) == child_start:
                                child_fd = fallback_fd
                        finally:
                            if child_fd != fallback_fd:
                                os.close(fallback_fd)
                if child_fd is not None:
                    poller = select.poll()
                    poller.register(child_fd, select.POLLIN)
                    if not poller.poll(0):
                        try:
                            if _start_ticks(child_pid) == child_start:
                                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                                poller.poll(5000)
                        except (FileNotFoundError, ProcessLookupError):
                            pass
            finally:
                if child_fd is not None:
                    os.close(child_fd)
                if server is not None:
                    for stream in (server.stdin, server.stdout, server.stderr):
                        if stream is not None:
                            stream.close()


@pytest.mark.asyncio
async def test_spawn_capture_waits_for_parent_handoff(cli_child, monkeypatch):
    workspace, ready = cli_child
    release_create_return = asyncio.Event()
    release_spawn_return = asyncio.Event()
    spawned, spawn_captured, spawn_returned, emergency_handles = capture_spawn(
        monkeypatch,
        create_return_gate=release_create_return,
        return_gate=release_spawn_return,
    )
    task = asyncio.create_task(mcp_server._run_cli_simple("wait", workspace=workspace))
    handoff_waiter = asyncio.create_task(wait_for_spawn_return(spawn_returned))
    try:
        child_pid = await wait_ready(ready)
        assert not spawn_captured.is_set(), "child readiness unexpectedly implied parent capture"
        assert not spawned, "observer captured process before create_subprocess_exec was released"
        release_create_return.set()
        await wait_for_spawn_capture(spawn_captured)
        assert len(spawned) == 1 and spawned[0].pid == child_pid
        assert not spawn_returned.is_set(), "handoff completed before the held spawn return"
        await asyncio.sleep(0)
        assert not handoff_waiter.done(), "handoff wait returned before parent spawn capture"

        release_spawn_return.set()
        await asyncio.wait_for(handoff_waiter, timeout=5.0)
        assert len(spawned) == 1 and spawned[0].pid == child_pid

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=12.0)
        assert spawned[0].returncode is not None
    finally:
        release_create_return.set()
        release_spawn_return.set()
        if not handoff_waiter.done():
            handoff_waiter.cancel()
        await asyncio.gather(handoff_waiter, return_exceptions=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wait", "ignore-term"])
async def test_task_cancel_reaps_real_cli_child(cli_child, monkeypatch, mode):
    workspace, ready = cli_child
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)
    task = asyncio.create_task(mcp_server._run_cli_simple(mode, workspace=workspace))
    try:
        child_pid = await wait_ready(ready)
        await wait_for_spawn_return(spawn_returned)
        assert len(spawned) == 1 and spawned[0].pid == child_pid
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=12.0)
        assert spawned[0].returncode is not None, "cancel returned with a live CLI child"
        assert await asyncio.wait_for(spawned[0].wait(), timeout=1.0) < 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
async def test_anyio_scope_cancel_reaps_real_cli_child(cli_child, monkeypatch):
    workspace, ready = cli_child
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)

    async def run_command():
        await mcp_server._run_cli_simple("wait", workspace=workspace)

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(run_command)
            child_pid = await wait_ready(ready)
            await wait_for_spawn_return(spawn_returned)
            assert len(spawned) == 1 and spawned[0].pid == child_pid
            group.cancel_scope.cancel()
        assert spawned[0].returncode is not None, "scope exited with a live CLI child"
        assert await asyncio.wait_for(spawned[0].wait(), timeout=1.0) < 0
    finally:
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
async def test_anyio_cancel_shield_waits_cooperatively_for_cleanup(cli_child, monkeypatch):
    workspace, ready = cli_child
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)
    cleanup_ready = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_cleanup = mcp_server._kill_and_reap
    original_shield = asyncio.shield
    shield_calls = 0

    async def gated_cleanup(proc, comm_task):
        cleanup_ready.set()
        await release_cleanup.wait()
        await original_cleanup(proc, comm_task)

    def count_shield(awaitable):
        nonlocal shield_calls
        shield_calls += 1
        return original_shield(awaitable)

    async def release_after_event_loop_turns():
        await cleanup_ready.wait()
        for _ in range(100):
            await asyncio.sleep(0)
        release_cleanup.set()

    async def run_command():
        await mcp_server._run_cli_simple("wait", workspace=workspace)

    monkeypatch.setattr(mcp_server, "_kill_and_reap", gated_cleanup)
    monkeypatch.setattr(mcp_server.asyncio, "shield", count_shield)
    releaser = asyncio.create_task(release_after_event_loop_turns())
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(run_command)
            child_pid = await wait_ready(ready)
            await wait_for_spawn_return(spawn_returned)
            assert len(spawned) == 1 and spawned[0].pid == child_pid
            group.cancel_scope.cancel()
        await asyncio.wait_for(releaser, timeout=3.0)
        assert spawned[0].returncode is not None, "scope exited with a live CLI child"
        assert shield_calls == 1, f"cancellation repeatedly interrupted cleanup: {shield_calls} waits"
    finally:
        releaser.cancel()
        await asyncio.gather(releaser, return_exceptions=True)
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
async def test_repeated_task_cancel_cannot_interrupt_reaping(cli_child, monkeypatch):
    workspace, ready = cli_child
    spawned, _, spawn_returned, emergency_handles = capture_spawn(monkeypatch)
    task = asyncio.create_task(mcp_server._run_cli_simple("ignore-term", workspace=workspace))
    cancel_handles = []
    try:
        child_pid = await wait_ready(ready)
        await wait_for_spawn_return(spawn_returned)
        assert len(spawned) == 1 and spawned[0].pid == child_pid
        task.cancel()
        loop = asyncio.get_running_loop()
        cancel_handles = [loop.call_later(delay, task.cancel) for delay in (0.05, 0.1, 0.2)]
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=12.0)
        assert spawned[0].returncode is not None, "repeated cancellation left a live CLI child"
    finally:
        for handle in cancel_handles:
            handle.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await reap_owned(emergency_handles)


@pytest.mark.asyncio
async def test_communication_exception_reaps_child_and_preserves_error(cli_child, monkeypatch):
    workspace, ready = cli_child
    spawned = []
    real_spawn = asyncio.create_subprocess_exec
    original_communicate = None

    async def fail_communication_after_ready(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        spawned.append(proc)

        async def raise_after_ready():
            await wait_ready(ready)
            raise RuntimeError("controlled communicate failure")

        nonlocal original_communicate
        original_communicate = proc.communicate
        proc.communicate = raise_after_ready
        return proc

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fail_communication_after_ready)
    try:
        with pytest.raises(RuntimeError, match="controlled communicate failure"):
            await asyncio.wait_for(mcp_server._run_cli_simple("wait", workspace=workspace), timeout=6.0)
        assert len(spawned) == 1 and spawned[0].returncode is not None
        assert await asyncio.wait_for(spawned[0].wait(), timeout=1.0) < 0
    finally:
        for proc in spawned:
            if original_communicate is not None:
                proc.communicate = original_communicate
        await reap_owned(spawned)


@pytest.mark.asyncio
async def test_cleanup_error_does_not_replace_communication_error(cli_child, monkeypatch, caplog):
    workspace, ready = cli_child
    spawned = []
    real_spawn = asyncio.create_subprocess_exec
    original_communicate = None

    async def fail_communication_after_ready(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        spawned.append(proc)

        async def raise_original_error():
            await wait_ready(ready)
            raise RuntimeError("original communication failure")

        nonlocal original_communicate
        original_communicate = proc.communicate
        proc.communicate = raise_original_error
        return proc

    async def fail_cleanup(_proc, _task):
        raise OSError("controlled cleanup failure")

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", fail_communication_after_ready)
    monkeypatch.setattr(mcp_server, "_kill_and_reap", fail_cleanup)
    try:
        with pytest.raises(RuntimeError, match="original communication failure"):
            await asyncio.wait_for(mcp_server._run_cli_simple("wait", workspace=workspace), timeout=6.0)
        assert "failed to terminate and reap simple CLI child" in caplog.text
        assert len(spawned) == 1 and spawned[0].returncode is None
    finally:
        for proc in spawned:
            if original_communicate is not None:
                proc.communicate = original_communicate
        await reap_owned(spawned)


@pytest.mark.asyncio
async def test_normal_result_preserves_output_and_exit_code(cli_child):
    workspace, _ready = cli_child
    stdout, stderr, code = await mcp_server._run_cli_simple("normal", workspace=workspace)
    assert (stdout, stderr, code) == ("output\n", "diagnostic\n", 7)


@pytest.mark.asyncio
async def test_paused_child_does_not_publish_partial_readiness(cli_child):
    workspace, ready = cli_child
    paused = workspace / "publish.paused"
    release = workspace / "publish.release"
    temporary_ready = workspace / "ready.pid.tmp"
    proc = await asyncio.create_subprocess_exec(
        str(workspace / "code-forge"),
        "paused-publish",
        cwd=workspace,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await wait_for_file(paused)
        assert not ready.exists(), "readiness appeared before the complete PID was published"
        assert temporary_ready.read_text(encoding="ascii") == str(proc.pid)

        release.touch()
        assert await asyncio.wait_for(proc.wait(), timeout=5.0) == 0
        assert ready.read_text(encoding="ascii") == str(proc.pid)
    finally:
        if proc.returncode is None:
            proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=5.0)
