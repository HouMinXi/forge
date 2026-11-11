# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Immediate occurrence counts through native discovery, SQLite and CLI/MCP."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_capture as capture_tests
from tests import test_cross_repo_immediate_diagnostic as immediate
from tests.test_cross_repo_diagnostic_gate import native_ci_input as native_ci_input
from tests import test_cross_repo_native_consumer_chain as native_consumer
from tests import test_cross_repo_persistent_diagnostic as persistent
from code_forge._sqlite_uri import readonly_uri
from code_forge.source import compute_source_hash

DIFF = immediate.DIFF
graphs = immediate.graphs
native_chain = native_consumer.native_chain
PERSISTENCE = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="persistent capture qualifies Linux local filesystems"
)
BOUND_CASES = [False, pytest.param(True, marks=PERSISTENCE)]
SUMMARY = (
    "cross-repo-impact: direct-caller queries completed for %d of %d registered sibling entries "
    "(%d attempted; %d unavailable). Entries may repeat repositories. "
    "Repositories outside this registered scope were not searched for direct callers. "
    "Graph freshness and full impact coverage are not established."
)
EMPTY = (
    "cross-repo-impact: no sibling registry entries; direct-caller impact not evaluated. "
    "Repositories outside this registered scope were not searched for direct callers."
)
UNAVAILABLE = (
    "cross-repo-impact: registry scope unavailable; direct-caller impact not evaluated. "
    "Registered query scope was not established."
)


def named(primary):
    immediate.edit_graph(
        primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'"
    )


@pytest.mark.parametrize("bound", BOUND_CASES)
@pytest.mark.parametrize("case", ["zero", "callers", "missing", "corrupt", "mixed", "duplicates"])
def test_native_occurrences_counts_and_silent_cache(graphs, monkeypatch, bound, case):
    primary, sibling, registry, _ = graphs
    named(primary)
    if case in {"callers", "duplicates"}:
        capture_tests.add_named_target_and_caller(primary, sibling)
    if case == "missing":
        (sibling / ".code-review-graph/graph.db").unlink()
    elif case == "corrupt":
        (sibling / ".code-review-graph/graph.db").write_bytes(b"private error value")
    entries = [{"path": str(primary)}, {"path": str(sibling), "alias": "private-alias"}]
    if case == "mixed":
        entries.append({"path": str(primary.parent / "missing-private-path"), "alias": "private-alias"})
    if case == "duplicates":
        entries += [entries[1].copy()] * 11
    registry.write_text(json.dumps({"repos": entries}))
    queries = []
    sql = []
    native_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = native_connect(*args, **kwargs)
        connection.set_trace_callback(sql.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    original_query = impact._find_cross_repo_caller_groups

    def query(*args):
        queries.append(args)
        return original_query(*args)

    monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", query)
    messages = []
    owner = capture_tests.capture(primary) if bound else None
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append, _diagnostics=owner)
    result = runner.run(DIFF, primary)
    expected = {
        "zero": (1, 1, 1, 0),
        "callers": (1, 1, 1, 0),
        "missing": (0, 1, 0, 1),
        "corrupt": (0, 1, 1, 1),
        "mixed": (1, 2, 1, 1),
        "duplicates": (12, 12, 12, 0),
    }[case]
    assert messages == [SUMMARY % expected]
    assert sum("WHERE c.kind = 'CALLS'" in statement for statement in sql) == expected[0]
    assert len(queries) == expected[2]
    assert len(result) == (10 if case == "duplicates" else int(case == "callers"))
    assert len(runner.infra_errors) == expected[3]
    errors = list(runner.infra_errors)
    if bound:
        assert owner.delivery_status == "delivered"
        assert len(owner.records) == 1
        assert owner.records[0].observation.diagnostics == ()
    assert runner.run(DIFF, primary) == result
    assert messages == [SUMMARY % expected]
    assert sum("WHERE c.kind = 'CALLS'" in statement for statement in sql) == expected[0]
    assert len(queries) == expected[2]
    assert runner.infra_errors == errors
    for private in (str(primary), str(sibling), "private-alias", "private error value"):
        assert private not in messages[0]


@pytest.mark.parametrize("interrupt_second", [False, True], ids=["complete", "second-interrupted"])
def test_two_symbol_query_summary_counts_complete_sibling_occurrences(
    graphs, monkeypatch, interrupt_second
):
    """Synthetic graph inputs establish per-occurrence, not per-symbol, counts."""
    primary, sibling, _, _ = graphs
    capture_tests.add_named_target_and_caller(primary, sibling)
    immediate.edit_graph(
        primary,
        "INSERT INTO nodes VALUES (2,'function','second','api::second','api.py',1,2)",
    )
    immediate.edit_graph(sibling, "INSERT INTO edges VALUES ('CALLS', 'client::use', 'second')")
    native_connect = sqlite3.connect
    statements, calls = [], []
    sibling_uri = readonly_uri(sibling / ".code-review-graph/graph.db")

    def connect(database, *args, **kwargs):
        connection = native_connect(database, *args, **kwargs)
        if database == sibling_uri:

            def trace(statement):
                statements.append(statement)
                if "WHERE c.kind = 'CALLS'" in statement:
                    calls.append(statement)
                    if interrupt_second and len(calls) == 2:
                        # Real SQLite cancellation after the first symbol was
                        # materialized, not a replacement helper result.
                        connection.set_progress_handler(lambda: 1, 1)

            connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    result = runner.run(DIFF, primary)
    assert len(calls) == 2
    assert "c.target_qualified = 'api'" in calls[0]
    assert "c.target_qualified = 'second'" in calls[1]
    first_caller = "SELECT file_path, line_start FROM nodes WHERE qualified_name = 'client::use'"
    assert statements.index(calls[0]) < statements.index(first_caller) < statements.index(calls[1])
    assert messages == [SUMMARY % (int(not interrupt_second), 1, 1, int(interrupt_second))]
    assert len(runner.infra_errors) == int(interrupt_second)
    if interrupt_second:
        assert "interrupted" in runner.infra_errors[0]
        assert result == []  # The successfully read first symbol must not leak partial hits.
    else:
        assert len(result) == 2
        assert [finding.description.split(" used by ")[0] for finding in result] == ["api", "second"]
    assert runner.run(DIFF, primary) == result
    assert len(calls) == 2
    assert len(messages) == 1


@pytest.mark.parametrize("case", ["empty", "primary-only", "malformed", "root"])
def test_scope_early_returns_are_uncached_and_never_query(graphs, monkeypatch, case):
    primary, sibling, registry, _ = graphs
    named(primary)
    if case == "malformed":
        registry.write_text("{broken-private-registry")
    elif case != "root":
        registry.write_text(json.dumps({"repos": [] if case == "empty" else [{"path": str(primary)}]}))
    else:
        original = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == sibling:
                raise OSError("private root error")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", immediate.forbid)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    expected = EMPTY if case in {"empty", "primary-only"} else UNAVAILABLE
    for _ in range(2):
        assert runner.run(DIFF, primary) == []
        assert len(runner.infra_errors) == 1
        assert runner._cached is None
    assert messages == [expected, expected]


def test_default_placement_override_enumeration_and_discovery_failure(graphs, monkeypatch):
    primary, sibling, registry, default = graphs
    named(primary)
    external = primary.parent / "private-external"
    external.mkdir()
    (sibling / ".code-review-graph/graph.db").rename(external / "graph.db")
    default.parent.mkdir()
    default.write_text(json.dumps({"repos": [{"path": str(sibling), "data_dir": str(external)}]}))
    registry.write_text(json.dumps({"repos": [{"path": str(sibling), "data_dir": "/ignored"}]}))
    messages = []
    assert impact.CrossRepoImpactRunner(output_fn=messages.append).run(DIFF, primary) == []
    assert messages == [SUMMARY % (1, 1, 1, 0)]
    original = impact._readonly_db_path

    def path(root, entries):
        if root == sibling:
            raise ValueError("private discovery error")
        return original(root, entries)

    monkeypatch.setattr(impact, "_readonly_db_path", path)
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    assert runner.run(DIFF, primary) == []
    assert messages[-1] == SUMMARY % (0, 1, 0, 1)
    assert len(runner.infra_errors) == 1


@pytest.mark.parametrize("bound", BOUND_CASES)
@pytest.mark.parametrize("error", [RuntimeError, OSError, KeyboardInterrupt, SystemExit])
def test_summary_sink_preserves_native_output_failure_semantics(graphs, error, bound):
    primary, _, _, _ = graphs
    named(primary)
    fault = error("private sink error")
    messages = []

    def output(message):
        messages.append(message)
        raise fault

    owner = capture_tests.capture(primary) if bound else None
    runner = impact.CrossRepoImpactRunner(output_fn=output, _diagnostics=owner)
    if issubclass(error, Exception):
        assert runner.run(DIFF, primary) == []
        assert runner._cached == []
    else:
        with pytest.raises(error) as caught:
            runner.run(DIFF, primary)
        assert caught.value is fault
        assert runner._cached is None
    assert runner.infra_errors == []
    assert messages == [SUMMARY % (1, 1, 1, 0)]
    if bound:
        assert owner.delivery_status == "delivered"
        assert len(owner.records) == 1
        assert owner.records[0].analysis_state == "targets_resolved"
        assert owner.records[0].observation.diagnostics == ()


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_unexpected_query_failure_never_emits_completion(graphs, monkeypatch, error):
    primary, _, _, _ = graphs
    named(primary)
    fault = error("private native error")

    def query(*args):
        raise fault

    monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", query)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    with pytest.raises(error) as caught:
        runner.run(DIFF, primary)
    assert caught.value is fault
    assert messages == []
    assert runner._cached is None


@PERSISTENCE
def test_unavailable_capture_does_not_suppress_actual_summary(graphs):
    primary, _, _, _ = graphs
    named(primary)
    owner = capture_tests.capture(primary)
    owner._disable("capture_limit")
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append, _diagnostics=owner)
    assert runner.run(DIFF, primary) == []
    assert messages == [SUMMARY % (1, 1, 1, 0), capture_tests.capture_module.PERSISTENCE_WARNING]
    assert runner.infra_errors == []


def _prepare_native_summary(fixture):
    named(fixture.primary)
    assert impact._parse_diff_files(fixture.diff) == ["README.md"]
    graphs_before = {}
    for root in (fixture.primary, fixture.sibling):
        path = root / ".code-review-graph/graph.db"
        with closing(sqlite3.connect(readonly_uri(path), uri=True)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM edges").fetchone() == (0,)
            if root == fixture.primary:
                assert connection.execute(
                    "SELECT kind, name, qualified_name, file_path FROM nodes"
                ).fetchall() == [("function", "api", "api::api", "README.md")]
        graphs_before[path] = path.read_bytes()
    assert json.loads(Path(fixture.env["CRG_REGISTRY_PATH"]).read_text()) == {
        "repos": [{"path": str(fixture.primary)}, {"path": str(fixture.sibling)}]
    }
    _observe_native_queries(fixture)
    return graphs_before


def _observe_native_queries(fixture):
    """Observe real child SQLite calls without replacing query results or CI."""
    import os

    executable = Path(fixture.env["PATH"].split(os.pathsep)[0]) / "code-forge"
    entrypoint = executable.read_text()
    insertion = "from cross_repo_native_consumer_launcher import main\n"
    assert entrypoint.count(insertion) == 1
    observer = (
        "import atexit, json, sqlite3\n"
        "from pathlib import Path\n"
        "queries = []\n"
        "native_connect = sqlite3.connect\n"
        "def observe_connect(database, *args, **kwargs):\n"
        "    connection = native_connect(database, *args, **kwargs)\n"
        "    def trace(statement):\n"
        "        if \"WHERE c.kind = 'CALLS'\" in statement:\n"
        "            queries.append({'database': str(database), 'uri': kwargs.get('uri'), "
        "'statement': statement})\n"
        "    connection.set_trace_callback(trace)\n"
        "    return connection\n"
        "sqlite3.connect = observe_connect\n"
        f"trace_path = Path({str(fixture.control / 'queries.json')!r})\n"
        "atexit.register(lambda: trace_path.write_text(json.dumps(queries), encoding='utf-8'))\n"
    )
    executable.write_text(entrypoint.replace(insertion, observer + insertion), encoding="utf-8")


def _assert_native_summary_observations(fixture, pid, verdict, graphs_before):
    queries = [
        query
        for query in json.loads((fixture.control / "queries.json").read_text())
        if query["database"] == readonly_uri(fixture.sibling / ".code-review-graph/graph.db")
    ]
    assert len(queries) == 1
    query = queries[0]
    assert query["database"] == readonly_uri(fixture.sibling / ".code-review-graph/graph.db")
    assert query["uri"] is True
    assert "WHERE c.kind = 'CALLS'" in query["statement"]
    assert "c.target_qualified = 'api'" in query["statement"]
    assert "|| 'README' ||" in query["statement"]
    for path, raw in graphs_before.items():
        assert path.read_bytes() == raw

    capture = json.loads((fixture.control / "capture.json").read_text())
    assert len(capture["expected"]) == 1
    expected = capture["expected"][0]
    assert expected["source_hash"] == compute_source_hash(git_diff=fixture.diff)
    assert expected["diff_sha256"] == hashlib.sha256(fixture.diff.encode("utf-8")).hexdigest()
    assert expected["observation_sequence"] == 1
    assert capture["publications"] == [{"issued_first": True}]
    assert capture["reads"] == [
        {
            "expected": expected,
            "succeeded": True,
            "rejected": False,
            "context": expected,
            "state": "targets_resolved",
            "diagnostic_count": 0,
            "message_matches": False,
        }
    ]
    assert capture["presentations"] == [
        {
            "owned": True,
            "state": "targets_resolved",
            "publication": "published",
            "context": expected,
            "message_count": 0,
            "status": "read",
        }
    ]
    assert capture["closed"] is True
    assert capture["delivery_origin"] == "actual"
    assert capture["delivery_status"] == "delivered"
    paths = persistent._records(fixture.primary)
    assert len(paths) == 1
    payload = persistent._payload(paths[0], fixture.primary, diff=fixture.diff, state="targets_resolved")
    assert payload["execution_id"] == expected["execution_id"]
    assert payload["repository_binding"]["id"] == expected["repository_id"]
    assert payload["source_hash"] == expected["source_hash"]
    assert payload["diagnostics"] == []

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
    assert all(receipt["excerpt_count"] == 0 for receipt in reports["consumer"]["receipts"].values())
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
        assert b"cross-repo-impact" not in raw
    assert not persistent._records(fixture.sibling)


@PERSISTENCE
@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True], ids=["immediate", "background"])
@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
async def test_actual_cli_mcp_child_transports_query_summary(
    native_chain, monkeypatch, background, verdict
):
    """Natural CI/L1/falsification → CLI child → immediate/background MCP handlers.

    README-only changes plus a synthetic named README graph node reach the
    genuine zero-caller SQLite branch. This is not code-change impact, graph
    freshness, mutation, HOLD/recovery, or MCP wire-transport proof.
    """
    import asyncio
    import os

    pytest.importorskip("mcp")
    from code_forge import mcp_jobs, mcp_server

    fixture = native_chain
    graphs_before = await asyncio.to_thread(_prepare_native_summary, fixture)
    fixture.env.update(FORGE_FIXTURE_GATE=verdict, FORGE_FIXTURE_READ_LOSS="0")
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    for name, value in fixture.env.items():
        monkeypatch.setenv(name, value)
    native_budgeted = mcp_server._run_cli_budgeted
    processes, communication_tasks = [], []
    native_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        processes.append(process)
        native_communicate = process.communicate

        async def communicate(*args, **kwargs):
            communication_tasks.append(asyncio.current_task())
            return await native_communicate(*args, **kwargs)

        process.communicate = communicate
        return process

    async def budget(*args, **kwargs):
        return await native_budgeted(*args, budget=0.01 if background else 40, **kwargs)

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(mcp_server, "_run_cli_budgeted", budget)
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
            response = await mcp_server.forge_job_status(job_id)
            result = response.structuredContent["result"]
            assert job["comm_task"].done() and job["wait_task"].done()
            assert communication_tasks == [job["comm_task"]]
        else:
            result = response.structuredContent
        assert len(processes) == len(communication_tasks) == 1
        assert communication_tasks[0].done()
        assert processes[0].returncode == (0 if verdict == "PASS" else 1), result
        assert result["verdict"] == verdict
        assert result["findings"] is None and result["findings_count"] is None
        _assert_native_summary_observations(fixture, processes[0].pid, verdict, graphs_before)
        expected = "[cross-repo] primary: ADVISORY: " + SUMMARY % (1, 1, 1, 0)
        assert result["output"].splitlines().count(expected) == 1
        assert response.content[0].text.splitlines().count(expected) == 1
        assert immediate.MESSAGE not in result["output"]
        assert immediate.MESSAGE not in response.content[0].text
        assert persistent.PERSISTENCE_WARNING not in result["output"]
        assert [line for line in result["output"].splitlines() if ": ADVISORY: " in line] == [expected]
        assert (
            result["output"]
            .splitlines()
            .count(
                "[cross-repo] WARNING: sibling(s) ['consumer'] have findings -- see per-repo receipts"
            )
            == 1
        )
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
            # The reused helper accounts for detached backend sessions too.
            await native_consumer._await_owned_backend_exit(fixture.control)
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
            # Cleanup failures invalidate a semantic-negative run.
            raise AssertionError("OWNED_RESOURCE_CLEANUP_FAILED: %d" % len(cleanup_errors)) from (
                active_error if active_error is not None else cleanup_errors[0]
            )


@PERSISTENCE
@pytest.mark.parametrize("verdict_name", ["PASS", "FAIL"])
@pytest.mark.parametrize("broken_sink", [False, True])
def test_named_target_summary_preserves_natural_gate_and_receipt_bytes(
    native_ci_input, monkeypatch, verdict_name, broken_sink
):
    """Real CI/L1/falsification and receipts, with a deterministic local backend.

    The reviewed diff is docs-only; the deliberately named graph node makes the
    genuine direct-caller branch reachable. This is not mutation or HOLD proof.
    """
    from contextlib import redirect_stderr, redirect_stdout
    from copy import deepcopy
    from dataclasses import replace
    import io
    import os
    import threading
    from unittest.mock import patch

    from code_forge import cli, cross_repo
    from code_forge.baseline import GitRefBaseline
    from code_forge.disposition import Disposition
    from code_forge.machine import StateMachine
    from code_forge.state import Mode, Verdict

    primary, sibling, gate, backend, env = native_ci_input
    named(primary)
    verdict = Verdict[verdict_name]
    env["FORGE_FIXTURE_GATE"] = verdict_name
    before, after, receipts_before, receipts_after, ci_returns = {}, {}, {}, {}, {}
    captures, cwds, decisions, attempts = [], [], [], []
    expected = "[cross-repo] primary: ADVISORY: " + SUMMARY % (1, 1, 1, 0)

    def output_sink(message):
        attempts.append(message)
        if broken_sink and message == expected:
            raise BrokenPipeError("owned summary output failure")
        print(message)

    native_host = cross_repo.run_cross_repo
    monkeypatch.setattr(
        cross_repo, "run_cross_repo", lambda **kw: native_host(**kw, output_fn=output_sink)
    )
    prior_profile = threading.getprofile()

    def profile(frame, event, value):
        if prior_profile is not None:
            prior_profile(frame, event, value)
        if frame.f_code is StateMachine._run_ci.__code__ and event == "return":
            ci_returns[frame.f_locals["self"].baseline_spec_repr] = value
        if frame.f_code is StateMachine._run_advisory_axes.__code__ and event in {"call", "return"}:
            machine = frame.f_locals["self"]
            snapshots = before if event == "call" else after
            receipts = receipts_before if event == "call" else receipts_after
            snapshots[machine.baseline_spec_repr] = deepcopy(machine._state)
            receipts[machine.baseline_spec_repr] = {
                path.name: path.read_bytes()
                for path in (machine.cwd / ".code-forge/receipts").glob("receipt-*.json")
            }
            if event == "return":
                cwds.append(machine.cwd)
                captures.extend(
                    runner._diagnostics
                    for runner in machine.advisory_runners
                    if hasattr(runner, "_diagnostics")
                )
        if (
            frame.f_code
            in (
                StateMachine._receipt_gate_round_errors.__code__,
                StateMachine._receipt_gate_terminal_errors.__code__,
            )
            and event == "return"
            and frame.f_locals["self"].baseline_spec_repr == "primary"
        ):
            decisions.append((frame.f_code.co_name, not value))

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
        threading.setprofile(profile)
        try:
            actual = cli._cross_repo_verdict_or_none(
                gate_yaml_path=gate,
                cwd=primary,
                baseline_spec=GitRefBaseline("main"),
                head_spec=GitRefBaseline("feature"),
                mode=Mode.CI,
                engine_choice="real",
                backend=backend,
                max_rounds=1,
                max_fix=1,
                _clean_threshold=1,
                warn=pytest.fail,
            )
        finally:
            threading.setprofile(prior_profile)
    assert threading.getprofile() is prior_profile
    assert actual is ci_returns["primary"] is verdict
    assert before["primary"].converged is (verdict is Verdict.PASS)
    confirmed = [f for f in before["primary"].findings if f.disposition is Disposition.CONFIRMED]
    assert len(confirmed) == (3 if verdict is Verdict.FAIL else 0)
    assert all(f.source == "L1" for f in confirmed)
    assert replace(after["primary"], infra_errors=before["primary"].infra_errors) == before["primary"]
    assert before["primary"].infra_errors == []
    assert all("cross-repo-impact" not in item for item in after["primary"].infra_errors)
    assert decisions == [("_receipt_gate_round_errors", True), ("_receipt_gate_terminal_errors", True)]
    assert receipts_before == receipts_after
    assert len(receipts_before["primary"]) == 3
    for name, raw in receipts_before["primary"].items():
        assert (primary / ".code-forge" / ("primary-" + name)).read_bytes() == raw
        data = json.loads(raw)
        assert data["pass_status"] == "completed"
        assert data["findings_count"] == int(verdict is Verdict.FAIL)
        assert len(data["code_excerpts"]) == 2
        assert b"cross-repo-impact" not in raw
    assert attempts.count(expected) == 1
    assert stdout.getvalue().splitlines().count(expected) == int(not broken_sink)
    assert [message for message in attempts if ": ADVISORY: " in message] == [expected]
    assert len(captures) == 1 and len(captures[0].records) == 1
    owner = captures[0]
    assert not owner._active
    assert owner.delivery_status == "delivered"
    assert owner.records[0].analysis_state == "targets_resolved"
    assert owner.records[0].observation.diagnostics == ()
    assert owner.records[0].publication.status == "published"
    assert len(cwds) == 2 and all(not cwd.exists() for cwd in cwds)
    assert not (primary / ".code-forge/code-forge.lock").exists()
    assert not (sibling / ".code-forge/code-forge.lock").exists()
