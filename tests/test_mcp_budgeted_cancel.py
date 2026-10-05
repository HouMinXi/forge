# SPDX-License-Identifier: Apache-2.0
"""Foreground budgeted cancellation owns its real child until cleanup finishes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from unittest.mock import Mock

import anyio
import pytest
import yaml

from code_forge import mcp_jobs, mcp_server
from code_forge.trust import record_trust

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"),
    reason="real Linux process ownership and pidfd controls",
)


@pytest.fixture
def owned_cli(tmp_path, monkeypatch):
    executable = tmp_path / "code-forge"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,signal,sys,time\n"
        "root=pathlib.Path.cwd()\n"
        "raw=pathlib.Path('/proc',str(os.getpid()),'stat').read_text()\n"
        "row={'pid':os.getpid(),'parent':os.getppid(),'start':raw[raw.rfind(')')+2:].split()[19]}\n"
        "def term(sig,frame):\n"
        " (root/'term').write_text(str(sig))\n"
        " if sys.argv[1]!='ignore-term':raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,term)\n"
        "(root/'ready.tmp').write_text(json.dumps(row));os.replace(root/'ready.tmp',root/'ready')\n"
        "if sys.argv[1] in ('normal','success'):\n"
        " print('stdout');print('stderr',file=sys.stderr);raise SystemExit(7 if sys.argv[1]=='normal' else 0)\n"
        "while not (root/'release').exists():time.sleep(.01)\n"
        "print('finished');print('completed log',file=sys.stderr)\n",
        encoding="ascii",
    )
    executable.chmod(0o700)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    processes, files, handles = [], [], []
    communications = {}
    returned = asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec
    real_tempfile = mcp_server.tempfile.NamedTemporaryFile

    async def observe_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        original_communicate = process.communicate

        async def communicate(*args, **kwargs):
            communications[process.pid] = asyncio.current_task()
            return await original_communicate(*args, **kwargs)

        process.communicate = communicate
        returned.set()
        return process

    def owned_tempfile(**kwargs):
        handle = real_tempfile(dir=tmp_path, **kwargs)
        files.append(Path(handle.name))
        handles.append(handle)
        return handle

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", observe_spawn)
    monkeypatch.setattr(mcp_server.tempfile, "NamedTemporaryFile", owned_tempfile)
    return tmp_path, env, processes, returned, files, handles, communications


async def ready_child(fixture):
    root, _, processes, returned, _, _, _ = fixture
    await asyncio.wait_for(returned.wait(), timeout=5)
    deadline = time.monotonic() + 5
    while not (root / "ready").exists():
        assert time.monotonic() < deadline, "child readiness missing"
        await asyncio.sleep(0.01)
    ready = json.loads((root / "ready").read_text())
    assert len(processes) == 1 and ready["pid"] == processes[0].pid
    assert ready["parent"] == os.getpid()
    assert os.getpgid(ready["pid"]) == os.getsid(ready["pid"]) == ready["pid"]
    fd = os.pidfd_open(ready["pid"])
    try:
        raw = await asyncio.to_thread(Path("/proc", str(ready["pid"]), "stat").read_text)
        assert raw[raw.rfind(")") + 2 :].split()[19] == ready["start"]
    except BaseException:
        os.close(fd)
        raise
    return fd


async def emergency_reap(fixture, fd):
    # Immediate parent-spawn observation owns these exact Process objects,
    # independently of ready parsing or the assertion under test.
    primary_error = sys.exc_info()[1]
    try:
        for process in fixture[2]:
            if process.returncode is None:
                process.kill()
            communication = fixture[6].get(process.pid)
            if communication is not None:
                await asyncio.wait_for(asyncio.gather(communication, return_exceptions=True), timeout=5)
            if not process.stdout.at_eof():
                await asyncio.wait_for(process.communicate(), timeout=5)
            await asyncio.wait_for(process.wait(), timeout=5)
    except BaseException as error:
        if primary_error is None:
            raise
        primary_error.add_note("Owned fixture cleanup also failed: " + str(error))
    finally:
        if fd is not None:
            os.close(fd)
        for path in fixture[4]:
            path.unlink(missing_ok=True)


def assert_disposed(fixture, fd):
    _, _, processes, _, files, handles, _ = fixture
    assert select.select([fd], [], [], 0)[0], "cancelled helper returned with a live child"
    assert processes[0].returncode is not None, "cancelled helper did not reap its child"
    assert not Path("/proc", str(processes[0].pid)).exists()
    assert processes[0].stdout.at_eof(), "stdout pipe remains open"
    assert all(handle.closed for handle in handles)
    assert files and all(not path.exists() for path in files), "owned tempfile survived cancellation"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["wait", "ignore-term"])
@pytest.mark.parametrize("cancellation", ["anyio", "repeated"])
async def test_budgeted_cancel_reaps_before_propagating(owned_cli, command, cancellation, caplog):
    root, env, _, _, _, _, _ = owned_cli
    fd = None
    task = None
    caught = []

    async def run_command():
        try:
            return await mcp_server._run_cli_budgeted(command, workspace=root, env=env)
        except asyncio.CancelledError as error:
            caught.append(error)
            raise

    try:
        if cancellation == "anyio":
            async with anyio.create_task_group() as group:
                group.start_soon(run_command)
                fd = await ready_child(owned_cli)
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(run_command())
            fd = await ready_child(owned_cli)
            task.cancel("first budgeted cancellation")
            for _ in range(3):
                await asyncio.sleep(0.01)
                if not task.done():
                    task.cancel("later cancellation")
            with pytest.raises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, timeout=12)
            assert raised.value is caught[0]
            assert raised.value.args == ("first budgeted cancellation",)
        assert len(caught) == 1
        assert_disposed(owned_cli, fd)
        assert (root / "term").read_text() == str(signal.SIGTERM)
        assert not any("budgeted CLI child" in record.getMessage() for record in caplog.records)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await emergency_reap(owned_cli, fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("disposed", [False, True])
async def test_cleanup_fault_preserves_primary_and_owned_handle(
    owned_cli, monkeypatch, caplog, disposed
):
    root, env, processes, _, _, _, _ = owned_cli
    original = mcp_server._kill_and_reap
    seen = []

    async def failed_cleanup(process, communication):
        seen.append((process, communication))
        if disposed:
            await original(process, communication)
            raise asyncio.CancelledError("cleanup cancellation")
        raise RuntimeError("controlled cleanup fault")

    monkeypatch.setattr(mcp_server, "_kill_and_reap", failed_cleanup)
    task = asyncio.create_task(mcp_server._run_cli_budgeted("wait", workspace=root, env=env))
    fd = None
    try:
        fd = await ready_child(owned_cli)
        task.cancel("original request cancellation")
        with pytest.raises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(task, timeout=12)
        assert raised.value.args == ("original request cancellation",)
        assert len(seen) == 1 and seen[0][0] is processes[0]
        assert isinstance(seen[0][1], asyncio.Task)
        failed = [
            r
            for r in caplog.records
            if r.getMessage() == "failed to terminate and reap budgeted CLI child"
        ]
        assert len(failed) == 1 and failed[0].exc_info is not None
        still_live = [
            r
            for r in caplog.records
            if r.getMessage() == "budgeted CLI child is still live after cleanup attempt"
        ]
        if disposed:
            assert not still_live
            assert_disposed(owned_cli, fd)
        else:
            assert len(still_live) == 1
            assert processes[0].returncode is None and not select.select([fd], [], [], 0)[0]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for _, communication in seen:
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
        await emergency_reap(owned_cli, fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("command,exit_code", [("normal", 7), ("success", 0)])
async def test_budgeted_inline_real_result_unchanged(owned_cli, command, exit_code):
    root, env, processes, _, files, handles, _ = owned_cli
    try:
        result = await mcp_server._run_cli_budgeted(command, workspace=root, env=env)
        assert len(result) == 4
        stdout, code, elapsed, stderr = result
        assert (stdout, code, stderr) == ("stdout\n", exit_code, "stderr\n")
        assert elapsed >= 0
        assert processes[0].returncode == exit_code and processes[0].stdout.at_eof()
        assert files and not any(path.exists() for path in files)
        assert all(handle.closed for handle in handles)
    finally:
        await emergency_reap(owned_cli, None)


@pytest.mark.asyncio
async def test_budget_expiry_preserves_background_owner(owned_cli):
    root, env, processes, _, files, _, _ = owned_cli
    fd = None
    job_id = None
    try:
        result = await mcp_server._run_cli_budgeted("wait", workspace=root, env=env, budget=0.05)
        assert len(result) == 3
        task, process, stderr_path = result
        fd = await ready_child(owned_cli)
        assert process is processes[0] and not task.done() and process.returncode is None
        assert Path(stderr_path) == files[0] and files[0].exists()
        job_id = mcp_jobs.start_job(task, process, stderr_log_path=stderr_path, max_lifetime_s=10)
        entry = mcp_jobs.get_job(job_id)
        assert entry["proc"] is process and entry["comm_task"] is task
        (root / "release").write_text("release", encoding="ascii")
        await asyncio.wait_for(entry["wait_task"], timeout=5)
        assert entry["status"] == "completed"
        assert entry["result"]["stdout"] == "finished\n"
        assert entry["result"]["stderr"] == "completed log\n"
        assert_disposed(owned_cli, fd)
        assert not (root / "term").exists(), "foreground timeout killed the handed-off child"
    finally:
        await emergency_reap(owned_cli, fd)
        if job_id is not None:
            mcp_jobs._jobs.pop(job_id, None)


def test_real_sdk_budgeted_request_cancellation(owned_cli, tmp_path):
    root, _, _, _, _, _, _ = owned_cli
    # The public gate handler passes these arguments to the real fake CLI.
    child_script = (root / "code-forge").read_text()
    child_script = child_script.replace("sys.argv[1]!='ignore-term'", "sys.argv[1]!='gate-check'")
    (root / "code-forge").write_text(child_script, encoding="ascii")
    home, tmp = tmp_path / "home", tmp_path / "temp"
    home.mkdir()
    tmp.mkdir()
    gate = root / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    data = {"backends": {"local": {"type": "cli", "command": "/bin/true", "model": "fixture"}}}
    gate.write_text(yaml.safe_dump(data), encoding="ascii")
    record_trust(gate, data, config_dir=home / ".config" / "code-forge")
    marker = root / "communicating.json"
    bootstrap = (
        "import asyncio,json,os,runpy\nfrom pathlib import Path\n"
        "spawn=asyncio.create_subprocess_exec;communicate=asyncio.subprocess.Process.communicate\nowned=set()\n"
        "def start(pid):\n raw=Path('/proc',str(pid),'stat').read_text();return raw[raw.rfind(')')+2:].split()[19]\n"
        "async def capture(*args,**kw):\n p=await spawn(*args,**kw);owned.add(p.pid);return p\n"
        "async def observe(self,*args,**kw):\n"
        " if self.pid in owned:\n"
        "  row={'server':os.getpid(),'server_start':start(os.getpid()),'child':self.pid,'child_start':start(self.pid)}\n"
        "  p=Path(os.environ['FORGE_TEST_HANDOFF']);q=p.with_suffix('.tmp');q.write_text(json.dumps(row));os.replace(q,p);owned.remove(self.pid)\n"
        " return await communicate(self,*args,**kw)\n"
        "asyncio.create_subprocess_exec=capture;asyncio.subprocess.Process.communicate=observe\n"
        "runpy.run_module('code_forge.mcp_server',run_name='__main__')\n"
    )
    repo = Path(__file__).resolve().parents[1]
    site_paths = [p for p in sys.path if p and "site-packages" in p]
    env = {
        "PATH": f"{root}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(tmp),
        "PYTHONPATH": os.pathsep.join([str(repo / "src"), *site_paths]),
        "FORGE_PROJECT_DIR": str(root),
        "FORGE_TEST_HANDOFF": str(marker),
    }
    server, child_fd, child_pid = None, None, None
    try:
        server = subprocess.Popen(
            [sys.executable, "-B", "-u", "-c", bootstrap],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
        raw = Path("/proc", str(server.pid), "stat").read_text()
        server_start = raw[raw.rfind(")") + 2 :].split()[19]

        def send(message):
            server.stdin.write((json.dumps(message) + "\n").encode())
            server.stdin.flush()

        def response():
            assert select.select([server.stdout], [], [], 10)[0], "SDK response timeout"
            line = server.stdout.readline()
            assert line, "SDK server closed output"
            return json.loads(line)

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "budget-cancel", "version": "1"},
                },
            }
        )
        assert response().get("id") == 1
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "forge_gate_check", "arguments": {"project_dir": str(root)}},
            }
        )
        deadline = time.monotonic() + 10
        while not (marker.exists() and (root / "ready").exists()):
            assert server.poll() is None, "SDK server exited before handoff"
            assert time.monotonic() < deadline, "public gate did not spawn a CLI child"
            time.sleep(0.01)
        event = json.loads(marker.read_text())
        ready = json.loads((root / "ready").read_text())
        child_pid = event["child"]
        assert event == {
            "server": server.pid,
            "server_start": server_start,
            "child": ready["pid"],
            "child_start": ready["start"],
        }
        assert ready["parent"] == server.pid
        fd = os.pidfd_open(child_pid)
        try:
            raw = Path("/proc", str(child_pid), "stat").read_text()
            assert raw[raw.rfind(")") + 2 :].split()[19] == event["child_start"]
        except BaseException:
            os.close(fd)
            raise
        child_fd = fd
        send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 2, "reason": "owned test cancellation"},
            }
        )
        assert select.select([child_fd], [], [], 12)[0], (
            "SDK request cancellation left budgeted CLI alive"
        )
        deadline = time.monotonic() + 5
        while Path("/proc", str(child_pid)).exists():
            assert time.monotonic() < deadline, "SDK did not reap the CLI child"
            time.sleep(0.01)
        assert (root / "term").read_text() == str(signal.SIGTERM)
        assert not list(tmp.glob("forge-stderr-*.log"))
        send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        replies = [response(), response()]
        assert {row.get("id") for row in replies} == {2, 3}
        cancelled = next(row for row in replies if row.get("id") == 2)
        assert cancelled.get("error", {}).get("message") == "Request cancelled"
        assert next(row for row in replies if row.get("id") == 3).get("result") == {}
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if child_fd is not None and not select.select([child_fd], [], [], 0)[0]:
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                assert select.select([child_fd], [], [], 5)[0]
        finally:
            try:
                if server is not None:
                    server.stdin.close()
                    try:
                        server.wait(timeout=15)
                    except subprocess.TimeoutExpired as error:
                        server.kill()
                        server.wait(timeout=5)
                        if primary_error is None:
                            raise
                        primary_error.add_note("Owned SDK server wait also failed: " + str(error))
            finally:
                if child_fd is not None:
                    os.close(child_fd)
                if server is not None:
                    for stream in (server.stdin, server.stdout, server.stderr):
                        stream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["cancel", "cleanup-fault"])
async def test_ready_identity_failure_disposes_known_fixture_without_pidfd_signal(
    owned_cli, monkeypatch, caplog, caller
):
    real_ready = ready_child
    real_open = os.pidfd_open
    captured_fds = []

    def capture_fd(pid):
        fd = real_open(pid)
        captured_fds.append(fd)
        return fd

    async def mismatch(fixture):
        root, _, _, returned, _, _, _ = fixture
        await asyncio.wait_for(returned.wait(), timeout=5)
        deadline = time.monotonic() + 5
        while not (root / "ready").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        row = json.loads((root / "ready").read_text())
        row["start"] = "0"
        (root / "ready").write_text(json.dumps(row), encoding="ascii")
        return await real_ready(fixture)

    monkeypatch.setattr(os, "pidfd_open", capture_fd)
    monkeypatch.setattr(sys.modules[__name__], "ready_child", mismatch)
    observe_signal = Mock(wraps=signal.pidfd_send_signal)
    monkeypatch.setattr(signal, "pidfd_send_signal", observe_signal)
    with pytest.raises(AssertionError):
        if caller == "cancel":
            await test_budgeted_cancel_reaps_before_propagating(owned_cli, "wait", "repeated", caplog)
        else:
            await test_cleanup_fault_preserves_primary_and_owned_handle(
                owned_cli, monkeypatch, caplog, False
            )
    # The real asyncio child watcher may also open a pidfd for this child.
    assert captured_fds
    observe_signal.assert_not_called()
    for fd in captured_fds:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == 9
    assert owned_cli[2][0].returncode is not None
    assert not await asyncio.to_thread(Path("/proc", str(owned_cli[2][0].pid)).exists)
    assert owned_cli[2][0].stdout.at_eof()


@pytest.mark.parametrize("fault", ["child", "wait", "both"])
def test_sdk_fixture_fault_closes_known_child_server_and_pipes(owned_cli, tmp_path, monkeypatch, fault):
    real_popen, real_select = subprocess.Popen, select.select
    servers, waits, child_fds = [], [], []
    wait_error = subprocess.TimeoutExpired("owned SDK server", 5)

    def observed_popen(*args, **kwargs):
        server = real_popen(*args, **kwargs)
        servers.append(server)
        original_wait = server.wait

        def wait(*wait_args, **wait_kwargs):
            waits.append(server)
            if fault in ("wait", "both") and len(waits) == 1:
                raise wait_error
            return original_wait(*wait_args, **wait_kwargs)

        server.wait = wait
        return server

    def controlled_select(readers, writers, errors, timeout):
        if timeout == 12 and isinstance(readers[0], int):
            child_fds.append(readers[0])
            if fault in ("child", "both"):
                raise AssertionError("owned known-child SDK control failure")
        return real_select(readers, writers, errors, timeout)

    monkeypatch.setattr(subprocess, "Popen", observed_popen)
    monkeypatch.setattr(select, "select", controlled_select)
    with pytest.raises(AssertionError if fault != "wait" else subprocess.TimeoutExpired) as raised:
        test_real_sdk_budgeted_request_cancellation(owned_cli, tmp_path)
    if fault == "wait":
        assert raised.value is wait_error
    elif fault == "both":
        assert "Owned SDK server wait also failed:" in raised.value.__notes__[0]
    assert len(servers) == 1 and all(server is servers[0] for server in waits)
    assert len(waits) == (1 if fault == "child" else 2)
    assert servers[0].returncode is not None and not Path("/proc", str(servers[0].pid)).exists()
    assert all(stream.closed for stream in (servers[0].stdin, servers[0].stdout, servers[0].stderr))
    assert len(child_fds) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(child_fds[0])
    assert closed.value.errno == 9
    child_pid = json.loads((tmp_path / "communicating.json").read_text())["child"]
    assert not Path("/proc", str(child_pid)).exists()


@pytest.mark.parametrize("fault", ["identity", "open"])
def test_sdk_setup_failure_preserves_owned_cleanup(owned_cli, tmp_path, monkeypatch, fault):
    real_popen, real_open, real_read = subprocess.Popen, os.pidfd_open, Path.read_text
    servers, waits, kills, fds = [], [], [], []
    open_error = OSError("controlled SDK pidfd open failure")

    def observed_popen(*args, **kwargs):
        server = real_popen(*args, **kwargs)
        servers.append(server)
        original_wait = server.wait

        def wait(*wait_args, **wait_kwargs):
            waits.append(wait_kwargs["timeout"])
            if fault == "identity":
                wait_kwargs["timeout"] = 15
            return original_wait(*wait_args, **wait_kwargs)

        def kill():
            # Record a premature kill request without stranding the owned child.
            kills.append(server.pid)
            original_wait(timeout=15)

        server.wait, server.kill = wait, kill
        return server

    def open_fd(pid):
        if fault == "open":
            raise open_error
        fd = real_open(pid)
        fds.append(fd)
        return fd

    def read(path, *args, **kwargs):
        raw = real_read(path, *args, **kwargs)
        if fault == "identity" and str(path).startswith("/proc/") and fds:
            fields = raw[raw.rfind(")") + 2 :].split()
            fields[19] = "0"
            return raw[: raw.rfind(")") + 2] + " ".join(fields)
        return raw

    observe_signal = Mock()
    monkeypatch.setattr(subprocess, "Popen", observed_popen)
    monkeypatch.setattr(os, "pidfd_open", open_fd)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(signal, "pidfd_send_signal", observe_signal)
    with pytest.raises(AssertionError if fault == "identity" else OSError) as raised:
        test_real_sdk_budgeted_request_cancellation(owned_cli, tmp_path)
    if fault == "open":
        assert raised.value is open_error
        assert not kills, "server was killed before its child cleanup finished"
    assert waits == [15]
    observe_signal.assert_not_called()
    assert len(servers) == 1 and servers[0].returncode == 0
    assert all(stream.closed for stream in (servers[0].stdin, servers[0].stdout, servers[0].stderr))
    row = json.loads(real_read(tmp_path / "communicating.json"))
    assert not Path("/proc", str(row["child"])).exists()
    assert not Path("/proc", str(servers[0].pid)).exists()
    for fd in fds:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == 9


@pytest.mark.asyncio
async def test_failed_handoff_settles_reader_and_closes_pidfd(owned_cli):
    root, env, _, _, _, _, _ = owned_cli
    task, process, fd = None, None, None
    try:
        task, process, _ = await mcp_server._run_cli_budgeted(
            "wait", workspace=root, env=env, budget=0.05
        )
        fd = await ready_child(owned_cli)
        primary = AssertionError("controlled failed handoff")
        with pytest.raises(AssertionError) as raised:
            try:
                raise primary
            finally:
                await emergency_reap(owned_cli, fd)
        assert raised.value is primary
        assert not getattr(primary, "__notes__", [])
        assert task.done() and process.returncode is not None
        assert process.stdout.at_eof()
        assert not await asyncio.to_thread(Path("/proc", str(process.pid)).exists)
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == 9
    finally:
        try:
            await emergency_reap(owned_cli, None)
        finally:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_failure", [False, True])
async def test_emergency_failure_closes_pidfd(owned_cli, monkeypatch, primary_failure):
    root, env, _, _, _, _, _ = owned_cli
    task, process, fd, original_kill = None, None, None, None
    try:
        task, process, _ = await mcp_server._run_cli_budgeted(
            "wait", workspace=root, env=env, budget=0.05
        )
        fd = await ready_child(owned_cli)
        original_kill = process.kill
        cleanup_error = RuntimeError("controlled owned fixture kill failure")
        primary_error = AssertionError("controlled original assertion")
        monkeypatch.setattr(process, "kill", Mock(side_effect=cleanup_error))
        if primary_failure:
            with pytest.raises(AssertionError) as raised:
                try:
                    raise primary_error
                finally:
                    await emergency_reap(owned_cli, fd)
            assert raised.value is primary_error
            assert primary_error.__notes__ == [
                "Owned fixture cleanup also failed: " + str(cleanup_error)
            ]
        else:
            with pytest.raises(RuntimeError) as raised:
                await emergency_reap(owned_cli, fd)
            assert raised.value is cleanup_error
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == 9
    finally:
        if original_kill is not None:
            monkeypatch.setattr(process, "kill", original_kill)
        try:
            await emergency_reap(owned_cli, None)
        finally:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
    assert process.returncode is not None and process.stdout.at_eof()
    assert not await asyncio.to_thread(Path("/proc", str(process.pid)).exists)


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["handoff", "emergency"])
@pytest.mark.parametrize("fault", ["ready", "open"])
async def test_cleanup_test_setup_failure_disposes_owned_child(owned_cli, monkeypatch, caller, fault):
    real_ready = ready_child
    setup_error = (
        asyncio.TimeoutError("controlled readiness failure")
        if fault == "ready"
        else OSError("controlled test pidfd open failure")
    )

    async def fail_ready(fixture):
        await asyncio.wait_for(fixture[3].wait(), timeout=5)
        if fault == "ready":
            raise setup_error
        with monkeypatch.context() as patch:

            def fail_open(pid):
                raise setup_error

            patch.setattr(os, "pidfd_open", fail_open)
            return await real_ready(fixture)

    monkeypatch.setattr(sys.modules[__name__], "ready_child", fail_ready)
    try:
        with pytest.raises(type(setup_error)) as raised:
            if caller == "handoff":
                await test_failed_handoff_settles_reader_and_closes_pidfd(owned_cli)
            else:
                await test_emergency_failure_closes_pidfd(owned_cli, monkeypatch, False)
        assert raised.value is setup_error
        assert len(owned_cli[2]) == 1
        process = owned_cli[2][0]
        assert process.returncode is not None, "setup failure left an owned test child live"
        assert not await asyncio.to_thread(Path("/proc", str(process.pid)).exists)
        assert process.stdout.at_eof()
        assert all(task.done() for task in owned_cli[6].values())
        assert all(handle.closed for handle in owned_cli[5])
        assert owned_cli[4] and all(not path.exists() for path in owned_cli[4])
    finally:
        # Safety observer owns the exact fixture Process objects even when the
        # tested teardown is omitted. Assertions above precede this cleanup.
        await emergency_reap(owned_cli, None)
