"""Tests for the bubblewrap sandbox supervisor.

The real-isolation tests require a delegated cgroup v2 subtree with
memory+pids controllers, user namespaces and bwrap; they skip on hosts
without those facilities.  The unavailable-mapping tests run everywhere.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest

from code_forge.mutation_engines import isolate
from code_forge.mutation_engines.isolate import (
    IsolationUnavailable,
    SandboxSpec,
    Supervisor,
    SupervisorThread,
    verify_isolation_support,
)

DELEGATED_ROOT = "/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (
    os.getuid(),
    os.getuid(),
)


def _capable() -> bool:
    if shutil.which("bwrap") is None:
        return False
    try:
        verify_isolation_support(DELEGATED_ROOT)
    except IsolationUnavailable:
        return False
    return True


requires_isolation = pytest.mark.skipif(
    not _capable(), reason="host lacks delegated cgroup/bwrap isolation"
)


def _spec(command, workspace, **kw):
    args = dict(
        run_id="run-" + uuid.uuid4().hex[:12],
        command=command,
        cwd="/workspace",
        memory_mb=64,
        pids=32,
        workspace_mb=16,
        env=(("PATH", "/usr/bin:/bin"),),
        workspace_host=str(workspace),
    )
    args.update(kw)
    return SandboxSpec(**args)


def _run_sandbox(spec, cgroup_root=DELEGATED_ROOT):
    """Start a supervisor on its dedicated thread, wait, tear down."""
    sup = Supervisor(spec, cgroup_root)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        thread.wait_started(10)
        return sup.wait(timeout=30)
    finally:
        sup.teardown()
        thread.join(timeout=10)


# -- capability mapping (runs everywhere) --------------------------------------


def test_bogus_cgroup_root_maps_unavailable(tmp_path):
    with pytest.raises(IsolationUnavailable) as exc:
        verify_isolation_support(str(tmp_path / "nonexistent"))
    assert exc.value.reason == "isolation_unavailable"


def test_missing_bwrap_maps_unavailable(monkeypatch):
    monkeypatch.setattr(isolate.shutil, "which", lambda name: None)
    with pytest.raises(IsolationUnavailable) as exc:
        verify_isolation_support(DELEGATED_ROOT)
    assert exc.value.reason == "isolation_unavailable"


def test_supervisor_refuses_non_supervisor_thread(tmp_path):
    spec = _spec(["/bin/true"], tmp_path)
    sup = Supervisor(spec, DELEGATED_ROOT)
    with pytest.raises(isolate.IsolationError, match="supervisor thread"):
        sup.start()


def test_supervisor_refuses_short_lived_plain_thread(tmp_path):
    """A plain short-lived thread must not start the supervisor: the
    parent-death signal would bind to a dying thread (plan frozen detail)."""
    spec = _spec(["/bin/true"], tmp_path)
    sup = Supervisor(spec, DELEGATED_ROOT)
    outcome = {}

    def short_lived():
        try:
            sup.start()
        except isolate.IsolationError as exc:
            outcome["error"] = str(exc)

    t = threading.Thread(target=short_lived, name="short-lived-worker")
    t.start()
    t.join(timeout=10)
    assert "supervisor thread" in outcome.get("error", "")


# -- real isolation ------------------------------------------------------------


@requires_isolation
def test_real_run_executes_in_workspace(tmp_path):
    spec = _spec(
        ["/bin/sh", "-c", "echo hello > /workspace/marker.txt"],
        tmp_path,
    )
    code = _run_sandbox(spec)
    assert code == 0
    assert (tmp_path / "marker.txt").read_text().strip() == "hello"


@requires_isolation
def test_limits_are_applied_before_payload_and_read_back(tmp_path):
    spec = _spec(
        ["/bin/sh", "-c", "date +%s.%N > /workspace/start.txt"],
        tmp_path,
        memory_mb=48,
        pids=24,
    )
    sup = Supervisor(spec, DELEGATED_ROOT)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        thread.wait_started(10)
        assert sup.limits_readback == {
            "memory.max": str(48 * 1024 * 1024),
            "pids.max": "24",
            "memory.swap.max": "0",
        }
        gate_open_ns = sup.gate_opened_monotonic_ns
        assert gate_open_ns > 0
        assert sup.wait(timeout=30) == 0
        assert (tmp_path / "start.txt").read_text().strip()
        # The fork-cap test below is the behavioral anchor proving limits
        # were in force before payload execution: a payload that forks
        # past pids.max is capped only when the limit preceded it.
    finally:
        sup.teardown()
        thread.join(timeout=10)


@requires_isolation
def test_no_network_route_inside(tmp_path):
    spec = _spec(
        ["/bin/sh", "-c", "ip route > /workspace/route.txt 2>&1; "
         "echo exit=$? >> /workspace/route.txt"],
        tmp_path,
    )
    assert _run_sandbox(spec) == 0
    out = (tmp_path / "route.txt").read_text()
    assert "default" not in out


@requires_isolation
def test_pids_limit_caps_forking(tmp_path):
    spec = _spec(
        ["/usr/bin/python3", "-c",
         "import os\n"
         "ok = 0\n"
         "for _ in range(64):\n"
         "    try:\n"
         "        pid = os.fork()\n"
         "    except OSError:\n"
         "        break\n"
         "    if pid == 0:\n"
         "        os._exit(0)\n"
         "    ok += 1\n"
         "for _ in range(ok):\n"
         "    os.wait()\n"
         "open('/workspace/forked.txt', 'w').write(str(ok))\n"],
        tmp_path,
        pids=12,
    )
    assert _run_sandbox(spec) == 0
    forked = int((tmp_path / "forked.txt").read_text())
    assert forked < 64


@requires_isolation
def test_watchdog_kills_payload_when_supervisor_dies(tmp_path):
    """kill -9 the supervisor process; the sandbox payload must die too."""
    helper = (
        "import sys, threading, time\n"
        "sys.path.insert(0, %r)\n"
        "from code_forge.mutation_engines.isolate import (\n"
        "    SandboxSpec, Supervisor, SupervisorThread)\n"
        "spec = SandboxSpec(run_id=%r, command=['/bin/sleep', '60'],\n"
        "    cwd='/workspace', memory_mb=64, pids=16, workspace_mb=16,\n"
        "    env=(('PATH', '/usr/bin:/bin'),), workspace_host=%r)\n"
        "sup = Supervisor(spec, %r)\n"
        "t = SupervisorThread(sup)\n"
        "t.start(); t.wait_started(10)\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    src = os.path.abspath("src")
    run_id = "run-" + uuid.uuid4().hex[:12]
    proc = subprocess.Popen(
        [sys.executable, "-c",
         helper % (src, run_id, str(tmp_path), DELEGATED_ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "started"
    time.sleep(1.0)
    # find the payload sleep pid
    sleeps = subprocess.run(
        ["pgrep", "-f", "/bin/sleep 60"], capture_output=True, text=True
    ).stdout.split()
    assert sleeps, "payload sleep not running"
    proc.kill()
    proc.wait(timeout=10)
    deadline = time.time() + 10
    alive = True
    while time.time() < deadline:
        still = [p for p in sleeps if os.path.exists("/proc/" + p)]
        if not still:
            alive = False
            break
        time.sleep(0.3)
    assert not alive, "payload survived supervisor death"
    # helper's cgroup may linger; reclaim best-effort
    cg = os.path.join(DELEGATED_ROOT, "forge-" + run_id)
    if os.path.isdir(cg):
        try:
            with open(os.path.join(cg, "cgroup.kill"), "w") as fh:
                fh.write("1")
        except OSError:
            pass
        for _ in range(20):
            try:
                os.rmdir(cg)
                break
            except OSError:
                time.sleep(0.3)


@requires_isolation
def test_cleanup_removes_cgroup_and_payloads(tmp_path):
    spec = _spec(["/bin/sleep", "60"], tmp_path)
    sup = Supervisor(spec, DELEGATED_ROOT)
    thread = SupervisorThread(sup)
    thread.start()
    try:
        thread.wait_started(10)
        cg = sup.cgroup_path
        assert os.path.isdir(cg)
    finally:
        sup.teardown()
        thread.join(timeout=10)
    assert not os.path.isdir(cg)
    with pytest.raises(ProcessLookupError):
        os.kill(sup.payload_pid, 0)


def test_run_id_must_be_identifier(tmp_path):
    with pytest.raises(ValueError):
        SandboxSpec(
            run_id="../escape",
            command=("/bin/true",),
            cwd="/",
            memory_mb=16,
            pids=8,
            workspace_mb=8,
            workspace_host=str(tmp_path),
        )


def test_remove_cgroup_force_rewrites_kill(tmp_path, monkeypatch):
    import code_forge.mutation_engines.isolate as iso

    cgroup = tmp_path / "cg"
    cgroup.mkdir()
    (cgroup / "cgroup.kill").write_text("")
    (cgroup / "busy").write_text("x")  # rmdir always fails
    writes = []
    monkeypatch.setattr(iso, "_write", lambda path, value: writes.append(path))
    monkeypatch.setattr(iso.time, "sleep", lambda seconds: None)

    def fast_clock():
        fast_clock.now += 2.0
        return fast_clock.now

    sup = object.__new__(iso.Supervisor)
    sup.cgroup_path = str(cgroup)

    fast_clock.now = 0.0
    monkeypatch.setattr(iso.time, "monotonic", fast_clock)
    sup._remove_cgroup(force=True)
    force_writes = len(writes)

    writes.clear()
    fast_clock.now = 0.0
    sup._remove_cgroup(force=False)
    assert force_writes > len(writes) > 0
