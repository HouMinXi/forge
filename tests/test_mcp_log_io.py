# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Bounded file-read and fake-process controls for MCP job ownership."""

import asyncio
import builtins
import os
import socket
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from code_forge import mcp_jobs as jobs


class FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode
        self.events = []

    async def wait(self):
        self.events.append("wait")
        self.returncode = -15
        return self.returncode


@pytest.fixture(autouse=True)
def boundaries(monkeypatch, record_property):
    jobs._jobs.clear()
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("external operation attempted")

    for owner, name in (
        (subprocess, "Popen"), (subprocess, "run"), (os, "system"),
        (os, "kill"), (os, "killpg"), (socket.socket, "connect"),
        (asyncio, "create_subprocess_exec"), (asyncio, "create_subprocess_shell"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(jobs, "group_of", lambda proc: None)
    monkeypatch.setattr(jobs, "terminate_group_or_child", lambda proc, group: proc.events.append("term"))
    monkeypatch.setattr(jobs, "kill_group_or_child", forbidden)
    yield attempts
    jobs._jobs.clear()
    record_property("external_operation_attempts", len(attempts))
    assert attempts == []


def register(branch, path, code=0):
    proc = FakeProc(None if branch == "live" else code)

    async def communicate():
        if branch == "complete":
            return b"output\xff", None
        await asyncio.Event().wait()

    comm = asyncio.create_task(communicate())
    jid = jobs.start_job(comm, proc, stderr_log_path=str(path),
                         max_lifetime_s=None if branch == "complete" else 0.01)
    return jid, jobs._jobs[jid], proc


class BlockedRead:
    """Independent controller always releases, even when the loop blocks."""

    def __init__(self, loop, callback):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.observed = threading.Event()
        self.progressed_before_release = False
        self.inputs = []

        def control():
            if self.entered.wait(2):
                loop.call_soon_threadsafe(callback, self.observed)
                self.progressed_before_release = self.observed.wait(0.3)
            self.release.set()

        self.controller = threading.Thread(target=control)
        self.controller.start()

    def wrap(self, reader):
        def blocked(*args, **kwargs):
            self.inputs.append(args)
            self.entered.set()
            try:
                assert self.release.wait(3), "bounded read release failed"
                return reader(*args, **kwargs)
            finally:
                self.finished.set()
        return blocked

    async def close(self):
        self.release.set()
        await asyncio.to_thread(self.controller.join, 3)
        assert not self.controller.is_alive()
        if self.entered.is_set():
            assert await asyncio.to_thread(self.finished.wait, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["complete", "live", "race"])
async def test_responsive_log_read(branch, tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    content = "A" * 4096 + "tail\ufffd"
    path.write_text(content)
    loop = asyncio.get_running_loop()
    block = BlockedRead(loop, lambda observed: observed.set())
    if branch == "complete":
        monkeypatch.setattr(Path, "read_text", block.wrap(Path.read_text))
    else:
        monkeypatch.setattr(jobs, "_read_stderr_tail", block.wrap(jobs._read_stderr_tail))
    _, entry, proc = register(branch, path)
    try:
        await asyncio.wait_for(entry["wait_task"], 4)
        assert block.progressed_before_release, "heartbeat did not run before read release"
        result = entry["result"]
        if branch == "complete":
            assert result["stderr"] == content
            assert result["stdout"] == "output\ufffd"
        else:
            assert block.inputs[0][0] is not entry, "worker received mutable job entry"
            assert block.inputs[0][0] == {"stderr_log_path": str(path)}
            assert result["stderr"].endswith(path_bytes_tail(content))
            prefix = ("job exceeded 0s cap\n" if branch == "live" else
                      "stdout lost: process exited at timeout boundary\n")
            assert result["stderr"] == prefix + path_bytes_tail(content)
        assert result["verdict"] == ("TIMEOUT" if branch == "live" else "PASS")
        assert entry["status"] == ("failed" if branch == "live" else "completed")
        assert proc.events == (["term", "wait"] if branch == "live" else [])
        assert not path.exists()
    finally:
        await block.close()


def path_bytes_tail(text):
    return text.encode()[-2048:].decode(errors="replace")


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["live", "race"])
@pytest.mark.parametrize("cleanup", [False, True])
async def test_tail_cancellation(branch, cleanup, tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    path.write_text("retained tail")
    loop = asyncio.get_running_loop()
    jid, entry, proc = register(branch, path)
    # Release only after terminal state is inspected, with a bounded fallback.
    entered = asyncio.Event()
    release = threading.Event()
    done = threading.Event()
    original = jobs._read_stderr_tail

    def read(snapshot):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(3)
            assert snapshot is not entry
            assert snapshot == {"stderr_log_path": str(path)}
            return "late tail"
        finally:
            done.set()

    monkeypatch.setattr(jobs, "_read_stderr_tail", read)
    real_unlink = os.unlink
    unlink_events = []

    def unlink(p, *args, **kwargs):
        if p == str(path):
            unlink_events.append(proc.returncode)
        return real_unlink(p, *args, **kwargs)

    monkeypatch.setattr(jobs.os, "unlink", unlink)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cleanup:
            await asyncio.wait_for(jobs.cleanup_all(), 2)
            assert jobs.get_job(jid) is None
        else:
            entry["wait_task"].cancel()
            await asyncio.wait_for(entry["wait_task"], 2)
        assert entry["status"] == "failed"
        assert entry["result"]["verdict"] == "UNKNOWN(-1)"
        assert entry["result"]["stderr"] == ""
        assert unlink_events == [proc.returncode]
        assert proc.returncode is not None
        assert proc.events == (["term", "wait"] if branch == "live" else [])
        frozen = dict(entry["result"])
        assert not done.is_set()
        release.set()
        assert await asyncio.to_thread(done.wait, 2)
        await asyncio.sleep(0)
        assert entry["result"] == frozen
        assert len(unlink_events) == 1
    finally:
        release.set()
        await asyncio.to_thread(done.wait, 3)
        if not entry["wait_task"].done():
            entry["wait_task"].cancel()
            await asyncio.gather(entry["wait_task"], return_exceptions=True)
        monkeypatch.setattr(jobs, "_read_stderr_tail", original)


def test_real_file_readers(tmp_path):
    path = tmp_path / "stderr"
    data = b"A" * 4100 + "\u4e2d".encode() * 40 + b"\xff"
    path.write_bytes(data)
    assert jobs._read_stderr_log(str(path)) == data.decode(errors="replace")
    entry = {"stderr_log_path": str(path)}
    assert jobs._read_stderr_tail(entry) == data[-2048:].decode(errors="replace")
    assert jobs._read_stderr_tail(entry, max_bytes=100) == data[-100:].decode(errors="replace")
    for unavailable in (None, "", str(tmp_path / "missing"), str(tmp_path)):
        assert jobs._read_stderr_log(unavailable) == ""
        assert jobs._read_stderr_tail({"stderr_log_path": unavailable}) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [0, 1, None])
@pytest.mark.parametrize("inline", [False, True])
async def test_completed_real_log_and_inline_bytes(code, inline, tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    data = b"A" * 4096 + b"\xff"
    path.write_bytes(data)
    proc = FakeProc(code)

    def forbidden_read(*args):
        pytest.fail("inline stderr must bypass the file reader")

    if inline:
        monkeypatch.setattr(jobs, "_read_stderr_log", forbidden_read)

    async def communicate():
        return b"out\xff", b"inline\xff" if inline else None

    jid = jobs.start_job(asyncio.create_task(communicate()), proc, stderr_log_path=str(path))
    entry = jobs._jobs[jid]
    await asyncio.wait_for(entry["wait_task"], 2)
    assert entry["status"] == "completed"
    assert entry["result"]["stdout"] == "out\ufffd"
    assert entry["result"]["stderr"] == (b"inline\xff" if inline else data).decode(errors="replace")
    assert entry["result"]["exit_code"] == (code if code is not None else -1)
    assert entry["result"]["verdict"] == jobs.exit_to_verdict(code if code is not None else -1)
    assert proc.events == []
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["complete", "live", "race"])
async def test_unexpected_read_error_policy(branch, tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    path.write_text("content")
    error = RuntimeError("reader programming failure")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path if branch == "complete" else jobs,
                        "read_text" if branch == "complete" else "_read_stderr_tail", fail)
    _, entry, proc = register(branch, path)
    if branch == "complete":
        await asyncio.wait_for(entry["wait_task"], 2)
        assert entry["status"] == "failed"
        assert entry["result"]["stderr"] == str(error)
        assert entry["result"]["verdict"] == "UNKNOWN(-1)"
    else:
        with pytest.raises(RuntimeError) as raised:
            await asyncio.wait_for(entry["wait_task"], 2)
        assert raised.value is error
        assert entry["status"] == "running"
        assert entry["result"] is None
    assert not path.exists()
    assert proc.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["complete", "live"])
@pytest.mark.parametrize("opened", [False, True])
async def test_cleanup_during_real_file_read(branch, opened, tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    data = b"file content\xff"
    path.write_bytes(data)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    outputs = []
    owner = Path if branch == "complete" else builtins
    original_open = owner.open

    @contextmanager
    def controlled_open(p, *args, **kwargs):
        if str(p) != str(path):
            with original_open(p, *args, **kwargs) as other:
                yield other
            return
        fh = original_open(p, *args, **kwargs) if opened else None
        try:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "bounded real-file read release failed"
            if fh is None:
                fh = original_open(p, *args, **kwargs)
            yield fh
        finally:
            if fh is not None:
                fh.close()

    reader_name = "_read_stderr_log" if branch == "complete" else "_read_stderr_tail"
    reader = getattr(jobs, reader_name)

    def capture(arg):
        try:
            value = reader(arg)
            outputs.append(value)
            return value
        finally:
            finished.set()

    monkeypatch.setattr(owner, "open", controlled_open)
    monkeypatch.setattr(jobs, reader_name, capture)
    jid, entry, proc = register(branch, path)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(jobs.cleanup_all(), 2)
        assert jobs.get_job(jid) is None
        assert entry["status"] == "failed"
        assert entry["result"]["verdict"] == "UNKNOWN(-1)"
        assert proc.returncode is not None
        assert not finished.is_set()
        frozen = dict(entry["result"])
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        assert outputs == ([data.decode(errors="replace")] if opened else [""])
        assert entry["result"] == frozen
        assert jobs._jobs == {}
        assert not path.exists()
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 3)
        if not entry["wait_task"].done():
            entry["wait_task"].cancel()
            await asyncio.gather(entry["wait_task"], return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_unlink_oserror_preserves_result(tmp_path, monkeypatch):
    path = tmp_path / "stderr"
    path.write_text("owned log")
    original = os.unlink

    def fail_owned(p, *args, **kwargs):
        if p == str(path):
            raise OSError("open file cannot be unlinked")
        return original(p, *args, **kwargs)

    monkeypatch.setattr(jobs.os, "unlink", fail_owned)
    _, entry, _ = register("complete", path)
    await asyncio.wait_for(entry["wait_task"], 2)
    assert entry["status"] == "completed"
    assert entry["result"]["stderr"] == "owned log"
    assert path.exists()
    original(path)


@pytest.mark.asyncio
async def test_original_failure_diagnostics_and_reap_warning(tmp_path, monkeypatch, caplog):
    path = tmp_path / "stderr"
    path.write_text("owned log")
    proc = FakeProc(None)
    error = ValueError("communication failed")
    reaped = []

    async def communicate():
        raise error

    async def failed_reap(child):
        reaped.append(child)
        raise RuntimeError("reap failed")

    monkeypatch.setattr(jobs, "_terminate_and_reap", failed_reap)
    jid = jobs.start_job(asyncio.create_task(communicate()), proc, stderr_log_path=str(path))
    entry = jobs._jobs[jid]
    await asyncio.wait_for(entry["wait_task"], 2)
    assert reaped == [proc]
    assert entry["status"] == "failed"
    assert entry["result"]["stdout"] == ""
    assert entry["result"]["stderr"] == str(error)
    assert entry["result"]["exit_code"] == -1
    assert entry["result"]["verdict"] == "UNKNOWN(-1)"
    assert entry["result"]["duration_s"] >= 0
    assert "reap after job failure raised" in caplog.text
    assert not path.exists()


@pytest.mark.asyncio
async def test_completed_without_path_avoids_worker(monkeypatch):
    async def communicate():
        return b"out", None

    def fail(*args, **kwargs):
        pytest.fail("absent completed log should not dispatch a worker")

    monkeypatch.setattr(jobs.asyncio, "to_thread", fail)
    jid = jobs.start_job(asyncio.create_task(communicate()), FakeProc(0))
    entry = jobs._jobs[jid]
    await asyncio.wait_for(entry["wait_task"], 2)
    assert entry["status"] == "completed"
    assert entry["result"]["stderr"] == ""
