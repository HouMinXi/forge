# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Native docs-only CI through public MCP handlers and a genuine CLI child.

The deterministic local backend supplies review input, never a Verdict. This
qualifies the in-worker consumer, not MCP wire transport or post-return recovery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import types

import pytest

from code_forge import _advisory_diagnostics as storage
from code_forge.cross_repo import get_sibling_diff
from code_forge.source import compute_source_hash
from code_forge.trust import record_trust
from tests.test_cross_repo_diagnostic_gate import native_ci_input as native_ci_input

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="native diagnostic chain qualifies Linux"
)
MESSAGE = "[cross-repo] primary: ADVISORY: " + storage.MESSAGE
WARNING = "[cross-repo] primary: ADVISORY: cross-repo-impact: persistent diagnostic unavailable"


@pytest.fixture
def native_chain(native_ci_input, tmp_path):
    primary, sibling, gate, backend, env = native_ci_input
    control = tmp_path / "control"
    control.mkdir()
    backend_path = Path(backend.command)
    backend_text = backend_path.read_text()
    # The owned deterministic backend creates no descendants. Its own alarm
    # survives CLI-group termination because native invocation detaches it.
    backend_guard = (
        "import atexit, pathlib, signal\n"
        "signal.alarm(5)\n"
        "pid = os.getpid()\n"
        "birth = pathlib.Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19]\n"
        f"control = pathlib.Path({str(control)!r})\n"
        "token = str(pid) + '-' + birth\n"
        "(control / ('backend-start-' + token)).touch()\n"
        "atexit.register(lambda: (control / ('backend-exit-' + token)).touch())\n"
    )
    backend_path.write_text(
        backend_text.replace("prompt = sys.argv", backend_guard + "prompt = sys.argv")
    )
    executable = tmp_path / "bin/code-forge"
    executable.parent.mkdir()
    tests = Path(__file__).parent.resolve()
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(tests)!r})\n"
        "from cross_repo_native_consumer_launcher import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    data = json.loads(gate.read_text())
    data.update(
        outlet="subprocess",
        backends={"owned-local": {"type": "cli", "command": backend.command, "timeout_s": 10}},
    )
    gate.write_text(json.dumps(data), encoding="utf-8")
    record_trust(gate, data, config_dir=Path(env["XDG_CONFIG_HOME"]) / "code-forge")
    (primary / ".code-forge/tools.yaml").write_text(
        json.dumps(
            {
                "tools": {
                    "owned": {
                        "command": sys.executable,
                        "output_format": "sarif",
                        "file_patterns": ["*.py"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env.update(
        PATH=str(executable.parent) + os.pathsep + env["PATH"],
        PYTHONPATH=str(tests.parent / "src"),
        FORGE_OUTLET="subprocess",
        FORGE_FALSIFICATION_ENGINE="real",
        FORGE_MODE="ci",
        FORGE_CLEAN_ROUND_THRESHOLD="1",
        FORGE_MAX_TOTAL_ROUNDS="1",
        FORGE_FIXTURE_CONTROL=str(control),
    )
    diff = get_sibling_diff(primary, "main..feature")
    return types.SimpleNamespace(primary=primary, sibling=sibling, control=control, env=env, diff=diff)


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True], ids=["immediate", "background"])
@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
async def test_native_mcp_cli_diagnostic(native_chain, monkeypatch, background, verdict):
    await _assert_chain(native_chain, monkeypatch, background, verdict, read_loss=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
async def test_native_mcp_cli_read_loss_is_non_gating(native_chain, monkeypatch, verdict):
    await _assert_chain(native_chain, monkeypatch, False, verdict, read_loss=True)


def _assert_observations(fixture, pid, verdict, read_loss):
    capture = json.loads((fixture.control / "capture.json").read_text())
    assert len(capture["expected"]) == 1
    expected = capture["expected"][0]
    assert expected["source_hash"] == compute_source_hash(git_diff=fixture.diff)
    assert expected["diff_sha256"] == hashlib.sha256(fixture.diff.encode("utf-8")).hexdigest()
    assert expected["observation_sequence"] == 1
    assert capture["publications"] == [{"issued_first": True}]
    assert len(capture["reads"]) == 1
    read = capture["reads"][0]
    assert read["expected"] == expected
    assert read["succeeded"] is (not read_loss)
    assert read["rejected"] is read_loss
    if not read_loss:
        assert read["context"] == expected
        assert read["state"] == "no_named_targets"
        assert read["diagnostic_count"] == 1
        assert read["message_matches"] is True
    assert capture["presentations"] == [
        {
            "owned": True,
            "state": "no_named_targets",
            "publication": "published",
            "context": expected,
            "message_count": 2 if read_loss else 1,
            "status": "not_delivered" if read_loss else "read",
        }
    ]
    assert capture["closed"] is True
    assert capture["delivery_origin"] == "actual"
    assert capture["delivery_status"] == ("not_delivered" if read_loss else "delivered")
    records = list((fixture.primary / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))
    assert len(records) == int(not read_loss)
    if records:
        payload = json.loads(records[0].read_bytes())
        assert payload["execution_id"] == expected["execution_id"]
        assert payload["repository_binding"] == {
            "kind": "invocation-repository",
            "id": expected["repository_id"],
        }
        assert payload["source_hash"] == expected["source_hash"]
        assert payload["diff_sha256"] == expected["diff_sha256"]
    reports = json.loads((fixture.control / "machines.json").read_text())
    assert set(reports) == {"primary", "consumer"}
    for label, root in (("primary", fixture.primary), ("consumer", fixture.sibling)):
        report = reports[label]
        assert report["pid"] == pid
        assert report["source_root"] == str(root)
        assert report["ci_returns"] == [verdict if label == "primary" else "FAIL"]
        assert report["verdict"] == report["ci_returns"][0]
        assert report["advisory_calls"] == report["advisory_returns"] == 1
        assert report["state_unchanged"] is True
        assert report["gate_infra_empty"] is (label == "primary")
        assert report["coverage_gap_count"] == int(label == "consumer")
        assert report["coverage_l1_active"] is (label == "primary")
        assert report["coverage_note_only"] is (label == "consumer")
        assert report["diagnostic_not_in_gate"] is True
        assert report["receipts_unchanged"] is True
        assert not Path(report["cwd"]).exists()
        assert not (root / ".code-forge/code-forge.lock").exists()
    assert reports["consumer"]["confirmed_count"] == 0
    assert len(reports["consumer"]["receipts"]) == 3
    assert all(r["excerpt_count"] == 0 for r in reports["consumer"]["receipts"].values())
    primary = reports["primary"]
    assert primary["converged"] is (verdict == "PASS")
    assert primary["confirmed_count"] == (3 if verdict == "FAIL" else 0)
    assert primary["confirmed_all_l1"] is True
    assert primary["receipt_gates"] == [
        ["_receipt_gate_round_errors", True],
        ["_receipt_gate_terminal_errors", True],
    ]
    assert len(primary["receipts"]) == 3
    for name, receipt in primary["receipts"].items():
        raw = (fixture.primary / ".code-forge" / ("primary-" + name)).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]
        assert receipt["pass_status"] == "completed"
        assert receipt["findings_count"] == int(verdict == "FAIL")
        assert receipt["excerpt_count"] == 2
    assert not list((fixture.sibling / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))


async def _assert_chain(fixture, monkeypatch, background, verdict, read_loss):
    pytest.importorskip("mcp")
    from code_forge import mcp_jobs, mcp_server

    fixture.env.update(FORGE_FIXTURE_GATE=verdict, FORGE_FIXTURE_READ_LOSS=str(int(read_loss)))
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    for name, value in fixture.env.items():
        monkeypatch.setenv(name, value)
    processes, communication_tasks = [], []
    native_spawn = asyncio.create_subprocess_exec
    native_budgeted = mcp_server._run_cli_budgeted

    async def observe_spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        processes.append(process)
        native_communicate = process.communicate

        async def communicate(*args, **kwargs):
            communication_tasks.append(asyncio.current_task())
            return await native_communicate(*args, **kwargs)

        process.communicate = communicate
        return process

    async def budgeted(*args, **kwargs):
        return await native_budgeted(*args, budget=0.01 if background else 40, **kwargs)

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", observe_spawn)
    monkeypatch.setattr(mcp_server, "_run_cli_budgeted", budgeted)
    if not background:
        (fixture.control / "release").touch()
    job = None
    try:
        response = await asyncio.wait_for(
            mcp_server.forge_review(
                baseline="main",
                head="feature",
                backend="owned-local",
                allow_main=True,
                project_dir=str(fixture.primary),
                timeout_s=45,
            ),
            timeout=45,
        )
        job_id = response.structuredContent.get("job_id")
        if job_id is not None:
            job = mcp_jobs.get_job(job_id)
        assert (job is not None) is background
        if background:
            assert job["status"] == "running"
            assert len(processes) == 1 and job["proc"] is processes[0]
            deadline = asyncio.get_running_loop().time() + 25
            while not (fixture.control / "primary-ready").exists():
                assert processes[0].returncode is None, "native CLI exited before its CI/advisory run"
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            (fixture.control / "release").touch()
            await asyncio.wait_for(asyncio.shield(job["wait_task"]), timeout=15)
            response = await mcp_server.forge_job_status(response.structuredContent["job_id"])
            result = response.structuredContent["result"]
            assert job["comm_task"].done() and job["wait_task"].done()
            assert communication_tasks == [job["comm_task"]]
        else:
            result = response.structuredContent
        assert len(processes) == len(communication_tasks) == 1
        assert communication_tasks[0].done()
        assert processes[0].returncode == (0 if verdict == "PASS" else 1), result
        assert result["verdict"] == verdict
        assert result["output"].splitlines().count(MESSAGE) == 1
        assert result["output"].splitlines().count(WARNING) == int(read_loss)
        assert MESSAGE in response.content[0].text
        assert (
            result["output"]
            .splitlines()
            .count(
                "[cross-repo] WARNING: sibling(s) ['consumer'] have findings -- see per-repo receipts"
            )
            == 1
        )
        _assert_observations(fixture, processes[0].pid, verdict, read_loss)
        starts = list(fixture.control.glob("backend-start-*"))
        assert starts, "native CI must invoke the owned backend"
        assert all(
            (fixture.control / path.name.replace("backend-start-", "backend-exit-")).exists()
            for path in starts
        )
    finally:
        active_error = sys.exc_info()[1]
        cleanup_errors = []
        try:
            (fixture.control / "release").touch()
        except BaseException as error:  # noqa: BLE001 -- Release failure cannot bypass owned cleanup.
            cleanup_errors.append(error)
        for process in processes:
            try:
                if process.returncode is None:
                    # This covers the CLI group, not detached backend sessions.
                    await asyncio.wait_for(mcp_server._terminate_and_reap(process), timeout=10)
                await asyncio.wait_for(process.wait(), timeout=5)
            except BaseException as error:  # noqa: BLE001 -- Settle remaining owned resources first.
                cleanup_errors.append(error)
        tasks = communication_tasks + ([job["wait_task"]] if job is not None else [])
        for task in tasks:
            try:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                finally:
                    if not task.done():
                        task.cancel()
                        settled, _ = await asyncio.wait([task], timeout=5)
                        await asyncio.gather(*settled, return_exceptions=True)
            except BaseException as error:  # noqa: BLE001 -- One failed task must not strand the next.
                cleanup_errors.append(error)
        try:
            # Independent of CLI groups, including when the CLI exited first.
            await _await_owned_backend_exit(fixture.control)
        except BaseException as error:  # noqa: BLE001 -- Preserve the original test failure below.
            cleanup_errors.append(error)
        unfinished = [task for task in tasks if not task.done()]
        if unfinished:
            for task in unfinished:
                task.cancel()
            settled, pending = await asyncio.wait(unfinished, timeout=5)
            await asyncio.gather(*settled, return_exceptions=True)
            if pending:
                cleanup_errors.append(TimeoutError("owned tasks remain unfinished"))
        if cleanup_errors:
            # Never credit a semantic negative whose owned-resource cleanup
            # failed. Preserve the original assertion as the explicit cause;
            # serialize only this bounded marker/count, not error contents.
            raise AssertionError("OWNED_RESOURCE_CLEANUP_FAILED: %d" % len(cleanup_errors)) from (
                active_error if active_error is not None else cleanup_errors[0]
            )


def _backend_alive(marker):
    pid, birth = marker.name.removeprefix("backend-start-").split("-")
    try:
        current = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    except FileNotFoundError:
        return False
    # A reaped/reused PID is not this backend. Even zombie residue fails cleanup.
    return current[19] == birth


async def _await_owned_backend_exit(control):
    deadline = asyncio.get_running_loop().time() + 7
    while any(_backend_alive(path) for path in control.glob("backend-start-*")):
        assert asyncio.get_running_loop().time() < deadline, "owned backend exceeded self-watchdog"
        await asyncio.sleep(0.01)
