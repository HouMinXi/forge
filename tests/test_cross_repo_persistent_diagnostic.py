# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Host-owned durable diagnostics through the real primary execution path.

Only unrelated gate/advisory work is controlled. Native graph discovery,
SQLite, the host, the runner, publication and consumption run in production.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sys
import types
import uuid

import pytest

from code_forge.state import Verdict
from tests import test_cross_repo_immediate_diagnostic as immediate

DIFF, MESSAGE, edit_graph = immediate.DIFF, immediate.MESSAGE, immediate.edit_graph
graphs = immediate.graphs

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="durable diagnostics qualify Linux local filesystems"
)


AXIS = "CROSS-REPO-IMPACT"
ATTRIBUTED_MESSAGE = "[cross-repo] primary: ADVISORY: " + MESSAGE
PERSISTENCE_WARNING = (
    "[cross-repo] primary: ADVISORY: cross-repo-impact: persistent diagnostic unavailable"
)
FIELDS = {
    "schema_version",
    "execution_id",
    "repository_binding",
    "axis",
    "observation_sequence",
    "source_hash",
    "diff_sha256",
    "analysis_state",
    "diagnostics",
}


def _records(primary):
    return sorted((primary / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))


def _payload(path, primary, *, diff=DIFF, state="no_named_targets", sequence=1):
    raw = path.read_bytes()
    assert len(raw) <= 4096
    data = json.loads(raw)
    assert set(data) == FIELDS
    assert type(data["schema_version"]) is int and data["schema_version"] == 1
    assert data["axis"] == AXIS
    assert type(data["observation_sequence"]) is int
    assert data["observation_sequence"] == sequence
    assert data["analysis_state"] == state
    assert data["diff_sha256"] == hashlib.sha256(diff.encode("utf-8")).hexdigest()
    assert data["source_hash"]
    execution = uuid.UUID(data["execution_id"])
    binding = data["repository_binding"]
    assert set(binding) == {"kind", "id"}
    assert binding["kind"] == "invocation-repository"
    repository = uuid.UUID(binding["id"])
    assert execution.version == repository.version == 4
    assert str(execution) == data["execution_id"]
    assert str(repository) == binding["id"]
    assert execution != repository
    assert path.relative_to(primary / ".code-forge/advisory-diagnostics").parts == (
        str(execution),
        str(repository),
        AXIS,
        f"{sequence}.json",
    )
    assert data["diagnostics"] == (
        [{"code": "no-named-changed-symbols", "message": MESSAGE}] if state == "no_named_targets" else []
    )
    assert str(primary) not in raw.decode("utf-8")
    return data


@pytest.fixture
def persistent_cli(graphs, monkeypatch):
    """CLI dispatch → real host → real machine.run → native SQLite axis."""
    from code_forge import cli, cross_repo
    from code_forge.baseline import GitRefBaseline
    from code_forge.machine import StateMachine
    from code_forge.state import Mode

    # The historical fixture can run without the optional provider. When it is
    # installed, exercise its actual default locator rather than the path stub.
    from importlib.metadata import PackageNotFoundError, version

    try:
        version("code-review-graph")
    except PackageNotFoundError:
        pass
    else:
        monkeypatch.delitem(sys.modules, "code_review_graph.registry")
        monkeypatch.delitem(sys.modules, "code_review_graph")
        __import__("code_review_graph.registry")

    primary, sibling, _, _ = graphs
    gate = primary / ".code-forge/gate.yaml"
    gate.parent.mkdir()
    gate.write_text(
        json.dumps({"siblings": [{"repo": str(sibling), "ref": "main..feature", "label": "consumer"}]}),
        encoding="utf-8",
    )
    observed = types.SimpleNamespace(machines={}, before={}, diff=DIFF, verdict=Verdict.PASS)
    monkeypatch.setattr(cross_repo, "get_sibling_diff", lambda *args: observed.diff)
    for module, name in (
        ("taint", "TaintRunner"),
        ("runtime", "RuntimeRunner"),
        ("graph_triage", "GraphTriageRunner"),
        ("daemon_state", "DaemonStateRunner"),
        ("legacy", "LegacyRunner"),
        ("rulepack", "RulepackRunner"),
    ):
        imported = __import__("code_forge." + module, fromlist=[name])
        monkeypatch.setattr(getattr(imported, name), "run", lambda *args, **kwargs: [])

    def gate_result(machine):
        machine._state.verdict = observed.verdict
        observed.machines[machine.baseline_spec_repr] = machine
        observed.before[machine.baseline_spec_repr] = deepcopy(machine._state)
        return observed.verdict

    monkeypatch.setattr(StateMachine, "_run_ci", gate_result)
    original_host = cross_repo.run_cross_repo

    def run(*, verdict=Verdict.PASS, output=None, diff=DIFF):
        observed.verdict, observed.diff = verdict, diff
        observed.machines.clear()
        observed.before.clear()
        if output is not None:
            monkeypatch.setattr(
                cross_repo, "run_cross_repo", lambda **kwargs: original_host(**kwargs, output_fn=output)
            )
        else:
            monkeypatch.setattr(cross_repo, "run_cross_repo", original_host)
        return cli._cross_repo_verdict_or_none(
            gate_yaml_path=gate,
            cwd=primary,
            baseline_spec=GitRefBaseline("main"),
            head_spec=GitRefBaseline("feature"),
            mode=Mode.CI,
            engine_choice="stub",
            backend=None,
            max_rounds=1,
            max_fix=1,
            _clean_threshold=1,
            warn=pytest.fail,
        )

    observed.run = run
    observed.primary, observed.sibling = primary, sibling
    return observed


def _assert_isolated(observed):
    assert set(observed.machines) == {"primary", "consumer"}
    for label, machine in observed.machines.items():
        assert machine._state == observed.before[label]
        assert machine._advisories == []
        assert not machine.cwd.exists()
        assert not (machine._source_root() / ".code-forge/code-forge.lock").exists()
    assert observed.machines["primary"]._source_root() == observed.primary
    assert observed.machines["consumer"].advisory_runners == []
    assert not _records(observed.sibling)


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL, Verdict.PENDING])
def test_real_cli_publishes_before_output_and_survives_worker_cleanup(persistent_cli, verdict):
    observed = persistent_cli
    messages = []

    def output(message):
        if message == ATTRIBUTED_MESSAGE:
            paths = _records(observed.primary)
            assert len(paths) == 1  # Publication precedes even the immediate sink.
            _payload(paths[0], observed.primary)
        messages.append(message)

    actual = observed.run(verdict=verdict, output=output)
    assert actual is (Verdict.FAIL if verdict is Verdict.PENDING else verdict)
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    _assert_isolated(observed)
    paths = _records(observed.primary)
    assert len(paths) == 1
    _payload(paths[0], observed.primary)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_output_process_control_preserves_committed_file_and_exception_identity(
    persistent_cli, error_type
):
    observed = persistent_cli
    fault = error_type("owned diagnostic cancellation")

    def output(message):
        if message == ATTRIBUTED_MESSAGE:
            assert len(_records(observed.primary)) == 1
            raise fault

    with pytest.raises(error_type) as caught:
        observed.run(output=output)
    assert caught.value is fault
    _assert_isolated(observed)
    paths = _records(observed.primary)
    assert len(paths) == 1
    _payload(paths[0], observed.primary)


@pytest.mark.parametrize("case", ["blank", "targets", "missing"])
def test_actual_analysis_states_do_not_invent_no_target_warning(persistent_cli, case):
    observed = persistent_cli
    diff = DIFF
    if case == "blank":
        diff, state = " \t\n", "not_requested"
    elif case == "targets":
        edit_graph(
            observed.primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'"
        )
        state = "targets_resolved"
    else:
        (observed.primary / ".code-review-graph/graph.db").unlink()
        state = "unavailable"
    messages = []
    assert observed.run(output=messages.append, diff=diff) is Verdict.PASS
    assert not any(MESSAGE in message for message in messages)
    paths = _records(observed.primary)
    assert len(paths) == 1
    _payload(paths[0], observed.primary, diff=diff, state=state)
    if case != "missing":
        _assert_isolated(observed)
    else:
        assert observed.machines["primary"]._state.infra_errors
        assert not observed.machines["primary"].cwd.exists()


def test_new_host_execution_never_reuses_same_source_hash_identity(persistent_cli):
    observed = persistent_cli
    assert observed.run(output=lambda message: None) is Verdict.PASS
    first = _payload(_records(observed.primary)[0], observed.primary)
    assert observed.run(output=lambda message: None) is Verdict.PASS
    paths = _records(observed.primary)
    assert len(paths) == 2
    payloads = [_payload(path, observed.primary) for path in paths]
    assert {value["source_hash"] for value in payloads} == {first["source_hash"]}
    assert len({value["execution_id"] for value in payloads}) == 2
    assert len({value["repository_binding"]["id"] for value in payloads}) == 2
    _assert_isolated(observed)


@pytest.fixture
def storage_calls(monkeypatch):
    from code_forge import _advisory_diagnostics as storage

    calls = types.SimpleNamespace(publications=[], reads=[])
    publish, read = storage.publish_observation, storage.read_observation

    def observe_publish(binding, observation):
        result = publish(binding, observation)
        calls.publications.append((observation, result))
        return result

    def observe_read(publication, expected):
        result = read(publication, expected)
        calls.reads.append((publication, expected, result))
        return result

    monkeypatch.setattr(storage, "publish_observation", observe_publish)
    monkeypatch.setattr(storage, "read_observation", observe_read)
    return calls


def test_host_publication_strictly_reads_after_temporary_cwds_are_removed(persistent_cli, storage_calls):
    from code_forge import _advisory_diagnostics as storage

    observed = persistent_cli
    assert observed.run(output=lambda message: None) is Verdict.PASS
    _assert_isolated(observed)
    paths = _records(observed.primary)
    assert len(paths) == 1
    _payload(paths[0], observed.primary)
    assert len(storage_calls.publications) == len(storage_calls.reads) == 1
    observation, publication = storage_calls.publications[0]
    assert publication.status == "published"
    assert publication.locator is not None
    assert storage_calls.reads[0] == (publication, observation.context, observation)
    # This is a new real strict read after host cleanup, not cached JSON or an
    # open descriptor kept alive by the original consumer.
    assert storage.read_observation(publication, observation.context) == observation
    # A second genuine host issues the new expected context independently of
    # either file. Repeating the same source hash must not admit the old file.
    assert observed.run(output=lambda message: None) is Verdict.PASS
    _assert_isolated(observed)
    second_observation, second_publication = storage_calls.publications[1]
    new_execution = second_observation.context
    assert new_execution.source_hash == observation.context.source_hash
    assert new_execution.diff_sha256 == observation.context.diff_sha256
    assert new_execution.execution_id != observation.context.execution_id
    assert new_execution.repository_id != observation.context.repository_id
    assert storage.read_observation(second_publication, new_execution) == second_observation
    with pytest.raises(storage.DiagnosticError):
        storage.read_observation(publication, new_execution)


def test_actual_strict_reader_return_controls_cli_text(persistent_cli, monkeypatch):
    from code_forge import _advisory_diagnostics as storage

    read = storage.read_observation
    marker = "reader-return-presentation-marker"
    verified = []

    def marked_read(publication, expected):
        observation = read(publication, expected)
        assert observation.diagnostics[0].message == MESSAGE
        verified.append(observation)
        # The real file and its strict validation stay genuine. Only the
        # returned presentation changes, proving output consumes that return.
        return replace(
            observation,
            diagnostics=(replace(observation.diagnostics[0], message=marker),),
        )

    monkeypatch.setattr(storage, "read_observation", marked_read)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    assert len(verified) == 1
    assert messages.count("[cross-repo] primary: ADVISORY: " + marker) == 1
    assert not any(MESSAGE in message for message in messages)
    _payload(_records(persistent_cli.primary)[0], persistent_cli.primary)
    _assert_isolated(persistent_cli)


@pytest.mark.parametrize("stage", ["publish", "read", "malformed-read"])
@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_persistence_fault_retains_warning_without_state_or_gate_changes(
    persistent_cli, monkeypatch, stage, verdict
):
    from code_forge import _advisory_diagnostics as storage

    def fail(*args):
        raise OSError("private fault details must not escape")

    if stage == "publish":
        monkeypatch.setattr(storage, "publish_observation", fail)
    elif stage == "read":
        monkeypatch.setattr(storage, "read_observation", fail)
    else:
        monkeypatch.setattr(storage, "read_observation", lambda *args: object())
    messages = []
    assert persistent_cli.run(verdict=verdict, output=messages.append) is verdict
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    assert messages.count(PERSISTENCE_WARNING) == 1
    assert "private fault" not in "\n".join(messages)
    assert len(_records(persistent_cli.primary)) == (0 if stage == "publish" else 1)
    _assert_isolated(persistent_cli)


def test_ordinary_output_failure_is_contained_after_publication(persistent_cli):
    messages = []

    def output(message):
        messages.append(message)
        if message == ATTRIBUTED_MESSAGE:
            raise BrokenPipeError("owned unavailable consumer")

    assert persistent_cli.run(output=output) is Verdict.PASS
    _assert_isolated(persistent_cli)
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    assert messages.count(PERSISTENCE_WARNING) == 1
    _payload(_records(persistent_cli.primary)[0], persistent_cli.primary)
    _assert_isolated(persistent_cli)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_pre_axis_failure_has_no_fabricated_observation(persistent_cli, monkeypatch, error_type):
    from code_forge.machine import StateMachine
    from code_forge._cross_repo_diagnostics import DiagnosticCapture

    closed = []
    original_close = DiagnosticCapture.close

    def close(capture):
        original_close(capture)
        closed.append(capture)

    monkeypatch.setattr(DiagnosticCapture, "close", close)
    fault = error_type("owned failure before advisory dispatch")

    def failed_gate(machine):
        if machine.baseline_spec_repr == "primary":
            raise fault
        return Verdict.PASS

    monkeypatch.setattr(StateMachine, "_run_ci", failed_gate)
    with pytest.raises(error_type) as caught:
        persistent_cli.run(output=lambda message: None)
    assert caught.value is fault
    assert _records(persistent_cli.primary) == []
    assert len(closed) == 1
    assert closed[0].delivery_status == "not_attempted"
    assert closed[0].records == []
    assert not closed[0]._active
    assert not (persistent_cli.primary / ".code-forge/code-forge.lock").exists()


def test_normal_machine_return_without_axis_call_records_not_run(persistent_cli, monkeypatch):
    from code_forge.machine import StateMachine

    monkeypatch.setattr(StateMachine, "_run_advisory_axes", lambda machine: None)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    paths = _records(persistent_cli.primary)
    assert len(paths) == 1
    _payload(paths[0], persistent_cli.primary, state="not_run")
    assert not any(MESSAGE in message for message in messages)
    _assert_isolated(persistent_cli)


def test_uncached_branch_transitions_get_new_records_and_cache_reuses_publication(
    persistent_cli, storage_calls, monkeypatch
):
    from code_forge import cross_repo_impact as impact
    from code_forge.machine import StateMachine

    observed = persistent_cli
    native_dispatch = StateMachine._run_advisory_axes
    native_resolve = impact.resolve_changed_symbols
    queries = []

    def resolve(*args, **kwargs):
        queries.append(args)
        return native_resolve(*args, **kwargs)

    def repeated_dispatch(machine):
        native_dispatch(machine)
        if machine.baseline_spec_repr == "primary":
            native_dispatch(machine)  # Another genuine uncached zero-target call.
            edit_graph(
                observed.primary,
                "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
            )
            native_dispatch(machine)  # Genuine resolved-target call with no callers.
            native_dispatch(machine)  # Same runner's cache, no query/publication.

    monkeypatch.setattr(impact, "resolve_changed_symbols", resolve)
    monkeypatch.setattr(StateMachine, "_run_advisory_axes", repeated_dispatch)
    messages = []
    assert observed.run(output=messages.append) is Verdict.PASS
    assert messages.count(ATTRIBUTED_MESSAGE) == 2
    assert len(queries) == 3
    assert len(storage_calls.publications) == 3
    paths = _records(observed.primary)
    assert len(paths) == 3
    for sequence, state in enumerate(("no_named_targets", "no_named_targets", "targets_resolved"), 1):
        _payload(paths[sequence - 1], observed.primary, sequence=sequence, state=state)
    assert len(storage_calls.reads) == 4
    assert storage_calls.reads[-1][0] is storage_calls.reads[-2][0]
    assert storage_calls.reads[-1][1] is storage_calls.reads[-2][1]
    _assert_isolated(observed)


@pytest.fixture
def native_cli_subprocess(graphs, tmp_path, monkeypatch):
    """Real CLI subprocess inputs, isolated from any user/model configuration."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    from code_forge.trust import record_trust

    primary, sibling, _, graph_home = graphs
    home = tmp_path / "user-home"
    home.mkdir()
    config = home / ".config"
    control = tmp_path / "control"
    control.mkdir()
    executable = tmp_path / "bin/code-forge"
    executable.parent.mkdir()
    tests = Path(__file__).parent.resolve()
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(tests)!r})\n"
        "from cross_repo_persistence_cli_fixture import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    gate_data = {
        "outlet": "subprocess",
        "siblings": [{"repo": str(sibling), "ref": "main..feature", "label": "consumer"}],
        "backends": {
            "owned-test": {"type": "cli", "command": [sys.executable, "-c", "raise SystemExit(99)"]}
        },
    }
    gate = primary / ".code-forge/gate.yaml"
    gate.parent.mkdir()
    gate.write_text(json.dumps(gate_data), encoding="utf-8")
    tools = primary / ".code-forge/tools.yaml"
    tools.write_text(
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
    record_trust(gate, gate_data, config_dir=config / "code-forge")

    def git(repo, *args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout

    for repo in (primary, sibling):
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Owned fixture")
        git(repo, "config", "user.email", "fixture@example.invalid")
        (repo / "api.py").write_text("def api():\n    return 1\n", encoding="utf-8")
        git(repo, "add", "api.py")
        git(repo, "commit", "-m", "base")
        git(repo, "checkout", "-b", "feature")
        (repo / "api.py").write_text("def api():\n    return 2\n", encoding="utf-8")
        git(repo, "add", "api.py")
        git(repo, "commit", "-m", "head")
    # No inherited API keys, user backend selection, or user configuration.
    env = {
        "PATH": str(executable.parent) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(tests.parent / "src"),
        "FORGE_OUTLET": "subprocess",
        "FORGE_FALSIFICATION_ENGINE": "stub",
        "FORGE_MODE": "ci",
        "CRG_HOME": str(graph_home.parent),
        "CRG_REGISTRY_PATH": str(graphs[2]),
        "FORGE_FIXTURE_CONTROL": str(control),
        "FORGE_FIXTURE_VERDICT": "PASS",
    }
    return types.SimpleNamespace(
        primary=primary,
        sibling=sibling,
        env=env,
        control=control,
        diff=git(primary, "diff", "main..feature"),
    )


def _assert_child_has_no_errors(report):
    assert report["infra_errors_empty"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True], ids=["immediate", "background"])
@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
async def test_actual_mcp_cli_child_preserves_published_diagnostic(
    native_cli_subprocess, monkeypatch, background, verdict
):
    import asyncio
    import os
    from pathlib import Path

    pytest.importorskip("mcp")
    from code_forge import mcp_jobs, mcp_server

    fixture = native_cli_subprocess
    fixture.env["FORGE_FIXTURE_VERDICT"] = verdict
    # Exercise the public MCP handler with the same explicitly isolated child
    # environment used by its production allow_main handoff.
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    for name, value in fixture.env.items():
        monkeypatch.setenv(name, value)
    processes = []
    native_spawn = asyncio.create_subprocess_exec
    native_budgeted = mcp_server._run_cli_budgeted

    async def observe_spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        processes.append(process)
        return process

    async def use_owned_budget(*args, **kwargs):
        return await native_budgeted(*args, budget=0.01 if background else 15, **kwargs)

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", observe_spawn)
    monkeypatch.setattr(mcp_server, "_run_cli_budgeted", use_owned_budget)
    if not background:
        (fixture.control / "release").touch()
    job = None
    try:
        response = await asyncio.wait_for(
            mcp_server.forge_review(
                baseline="main",
                head="feature",
                backend="owned-test",
                allow_main=True,
                project_dir=str(fixture.primary),
                timeout_s=25,
            ),
            timeout=20,
        )
        if background:
            job_id = response.structuredContent["job_id"]
            job = mcp_jobs.get_job(job_id)
            assert job is not None and job["status"] == "running"
            assert len(processes) == 1 and job["proc"] is processes[0]
            deadline = asyncio.get_running_loop().time() + 15
            while not (fixture.control / "primary.json").exists():
                assert processes[0].returncode is None, "real CLI failed before its primary axis"
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            paths = _records(fixture.primary)
            assert len(paths) == 1
            before = _payload(paths[0], fixture.primary, diff=fixture.diff)
            (fixture.control / "release").touch()
            await asyncio.wait_for(job["wait_task"], timeout=10)
            response = await mcp_server.forge_job_status(job_id)
            result = response.structuredContent["result"]
            assert _payload(paths[0], fixture.primary, diff=fixture.diff) == before
        else:
            result = response.structuredContent
        assert len(processes) == 1
        assert processes[0].returncode == (0 if verdict == "PASS" else 1)
        assert result["verdict"] == verdict
        assert result["output"].splitlines().count(ATTRIBUTED_MESSAGE) == 1
        assert ATTRIBUTED_MESSAGE in response.content[0].text
        assert result["findings"] is None
        assert result["findings_count"] is None
        paths = _records(fixture.primary)
        assert len(paths) == 1
        _payload(paths[0], fixture.primary, diff=fixture.diff)
        for label, root in (("primary", fixture.primary), ("consumer", fixture.sibling)):
            report = json.loads((fixture.control / (label + ".json")).read_text())
            assert report["pid"] == processes[0].pid
            assert report["source_root"] == str(root)
            _assert_child_has_no_errors(report)
            assert not await asyncio.to_thread(Path(report["cwd"]).exists)
            assert not (root / ".code-forge/code-forge.lock").exists()
        assert not _records(fixture.sibling)
    finally:
        (fixture.control / "release").touch()
        for process in processes:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        if job is not None:
            await asyncio.wait_for(job["wait_task"], timeout=5)


@pytest.mark.parametrize("fault", ["missing", "corrupt", "new-execution"])
def test_real_strict_read_rejects_lost_corrupt_or_wrong_execution_before_output(
    persistent_cli, monkeypatch, fault
):
    from code_forge import _advisory_diagnostics as storage

    native_read = storage.read_observation
    rejected = []

    def damaged_read(publication, expected):
        paths = _records(persistent_cli.primary)
        assert len(paths) == 1
        if fault == "missing":
            paths[0].unlink()
        elif fault == "corrupt":
            paths[0].write_bytes(b'{"schema_version":999}')
        else:
            expected = replace(expected, execution_id=str(uuid.uuid4()))
        try:
            return native_read(publication, expected)
        except storage.DiagnosticError as error:
            rejected.append(error)
            raise

    monkeypatch.setattr(storage, "read_observation", damaged_read)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    assert len(rejected) == 1
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    assert messages.count(PERSISTENCE_WARNING) == 1
    _assert_isolated(persistent_cli)


@pytest.mark.parametrize("case", ["blank", "targets", "not-run"])
def test_persistence_failure_never_fabricates_no_target_state(persistent_cli, monkeypatch, case):
    from code_forge import _advisory_diagnostics as storage
    from code_forge.machine import StateMachine

    diff = DIFF
    if case == "blank":
        diff = ""
    elif case == "targets":
        edit_graph(
            persistent_cli.primary,
            "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
        )
    else:
        monkeypatch.setattr(StateMachine, "_run_advisory_axes", lambda machine: None)

    def failed_read(*args):
        raise OSError("owned strict read failure")

    monkeypatch.setattr(storage, "read_observation", failed_read)
    messages = []
    assert persistent_cli.run(output=messages.append, diff=diff) is Verdict.PASS
    assert not any(MESSAGE in message for message in messages)
    assert messages.count(PERSISTENCE_WARNING) == 1
    _assert_isolated(persistent_cli)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_read_process_control_after_commit_preserves_identity(persistent_cli, monkeypatch, error_type):
    from code_forge import _advisory_diagnostics as storage

    fault = error_type("owned strict-read cancellation")

    def interrupted_read(*args):
        assert len(_records(persistent_cli.primary)) == 1
        raise fault

    monkeypatch.setattr(storage, "read_observation", interrupted_read)
    with pytest.raises(error_type) as caught:
        persistent_cli.run(output=lambda message: None)
    assert caught.value is fault
    _payload(_records(persistent_cli.primary)[0], persistent_cli.primary)
    _assert_isolated(persistent_cli)


def test_observation_limit_bounds_files_and_attempts_but_keeps_native_warnings(
    persistent_cli, storage_calls, monkeypatch
):
    from code_forge.machine import StateMachine

    native_dispatch = StateMachine._run_advisory_axes

    def repeated_dispatch(machine):
        for _ in range(66 if machine.baseline_spec_repr == "primary" else 1):
            native_dispatch(machine)

    monkeypatch.setattr(StateMachine, "_run_advisory_axes", repeated_dispatch)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    assert messages.count(ATTRIBUTED_MESSAGE) == 66
    assert messages.count(PERSISTENCE_WARNING) == 1
    assert len(storage_calls.publications) == 64
    paths = _records(persistent_cli.primary)
    assert len(paths) == 64
    for path in paths:
        _payload(path, persistent_cli.primary, sequence=int(path.stem))
    _assert_isolated(persistent_cli)


@pytest.mark.parametrize("named", [False, True], ids=["unconsumed", "consumed"])
def test_relative_sibling_binding_limits_only_consumed_persistence(
    persistent_cli, graphs, monkeypatch, named
):
    observed = persistent_cli
    monkeypatch.chdir(observed.primary.parent)
    graphs[2].write_text(
        json.dumps({"repos": [{"path": str(observed.primary)}, {"path": observed.sibling.name}]}),
        encoding="utf-8",
    )
    if named:
        edit_graph(
            observed.primary,
            "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
        )
    messages = []
    assert observed.run(output=messages.append) is Verdict.PASS
    _assert_isolated(observed)
    paths = _records(observed.primary)
    capture = observed.machines["primary"].advisory_runners[-1]._diagnostics
    assert len(capture.records) == 1
    if named:
        assert paths == []
        assert capture.records[0].observation.analysis_state == "targets_resolved"
        assert capture.records[0].publication.status == "not_published"
        assert messages.count(PERSISTENCE_WARNING) == 1
        assert not any(MESSAGE in message for message in messages)
    else:
        assert len(paths) == 1
        _payload(paths[0], observed.primary)
        assert messages.count(ATTRIBUTED_MESSAGE) == 1
        assert PERSISTENCE_WARNING not in messages


def test_surrogate_registry_alias_preserves_native_error_without_snapshot_failure(
    persistent_cli, graphs
):
    from code_forge import cross_repo_impact as impact

    observed = persistent_cli
    alias = "bad\udcff"
    edit_graph(
        observed.primary,
        "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'",
    )
    (observed.sibling / ".code-review-graph/graph.db").unlink()
    graphs[2].write_text(
        json.dumps(
            {"repos": [{"path": str(observed.primary)}, {"path": str(observed.sibling), "alias": alias}]}
        ),
        encoding="utf-8",
    )
    # The registry is real UTF-8 JSON containing an escaped lone surrogate.
    # Native legacy analysis retains that alias in its existing error channel.
    assert b"\\udcff" in graphs[2].read_bytes()
    legacy = impact.CrossRepoImpactRunner()
    assert legacy.run(DIFF, observed.primary) == []
    expected_error = "cross-repo-impact: sibling '%s' graph.db missing" % alias
    assert legacy.infra_errors == [expected_error]

    messages = []
    assert observed.run(output=messages.append) is Verdict.PASS
    primary = observed.machines["primary"]
    expected_state = deepcopy(observed.before["primary"])
    expected_state.infra_errors.append(expected_error)
    assert primary._state == expected_state
    assert primary._state.infra_errors == legacy.infra_errors
    assert primary._advisories == []
    assert not primary.cwd.exists()
    assert not (observed.primary / ".code-forge/code-forge.lock").exists()
    assert observed.machines["consumer"]._state == observed.before["consumer"]
    assert not observed.machines["consumer"].cwd.exists()
    assert not (observed.sibling / ".code-forge/code-forge.lock").exists()
    assert _records(observed.primary) == []
    assert messages.count(PERSISTENCE_WARNING) == 1
    assert not any(MESSAGE in message for message in messages)


@pytest.mark.parametrize("case", ["many", "oversized-utf8", "surrogate", "empty", "none"])
def test_malformed_present_return_falls_back_once_without_state_changes(
    persistent_cli, monkeypatch, case
):
    from code_forge._cross_repo_diagnostics import DiagnosticCapture

    malformed = {
        "many": ("untrusted consumer message",) * 300,
        "oversized-utf8": ("é" * 129,),
        "surrogate": ("bad\udcff",),
        "empty": (),
        "none": None,
    }[case]
    monkeypatch.setattr(DiagnosticCapture, "present", lambda *args, **kwargs: malformed)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    _assert_isolated(persistent_cli)
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    assert messages.count(PERSISTENCE_WARNING) == 1
    assert [message for message in messages if ": ADVISORY: " in message] == [
        ATTRIBUTED_MESSAGE,
        PERSISTENCE_WARNING,
    ]
    paths = _records(persistent_cli.primary)
    assert len(paths) == 1
    _payload(paths[0], persistent_cli.primary)


@pytest.mark.parametrize("case", ["status", "oversized-failure", "surrogate-failure"])
def test_malformed_publication_result_is_not_retained_or_gating(persistent_cli, monkeypatch, case):
    from code_forge import _advisory_diagnostics as storage

    malformed = {
        "status": storage.PublicationResult("unrecognized-status"),
        "oversized-failure": storage.PublicationResult("not_published", failure="x" * 100_000),
        "surrogate-failure": storage.PublicationResult("not_published", failure="bad\udcff"),
    }[case]
    monkeypatch.setattr(storage, "publish_observation", lambda *args: malformed)
    messages = []
    assert persistent_cli.run(output=messages.append) is Verdict.PASS
    _assert_isolated(persistent_cli)
    assert messages.count(ATTRIBUTED_MESSAGE) == 1
    assert messages.count(PERSISTENCE_WARNING) == 1
    capture = persistent_cli.machines["primary"].advisory_runners[-1]._diagnostics
    assert len(capture.records) == 1
    retained = capture.records[0].publication
    assert retained is not malformed
    assert retained.status == "unknown"
    assert retained.locator is None
    assert type(retained.failure) is str
    assert len(retained.failure.encode("utf-8", errors="strict")) <= 256
    assert retained.cleanup == "not_needed"
    assert _records(persistent_cli.primary) == []


@pytest.mark.parametrize("case", ["empty", "secret", "multiline-private"])
def test_cli_completion_receipt_omits_error_contents_without_hiding_failures(tmp_path, case):
    from code_forge.state import State
    from tests.cross_repo_persistence_cli_fixture import _write_completion

    errors = {
        "empty": [],
        "secret": ["synthetic-secret-marker-9fd8"],
        "multiline-private": ["synthetic-private-marker-4ea1\nUnexpected exception details"],
    }[case]
    expected_errors = tuple(errors)
    state = State(infra_errors=errors)
    machine = types.SimpleNamespace(
        _state=state,
        cwd=tmp_path / "temporary-worker",
        _source_root=lambda: tmp_path / "source-repository",
        baseline_spec_repr="primary",
    )
    _write_completion(tmp_path, machine)
    raw = (tmp_path / "primary.json").read_bytes()
    report = json.loads(raw)
    for error in expected_errors:
        assert error.splitlines()[0].encode("utf-8") not in raw
    assert set(report) == {"pid", "cwd", "source_root", "infra_errors_empty"}
    assert state.infra_errors is errors
    assert tuple(state.infra_errors) == expected_errors  # Never clear the genuine state.
    assert report["infra_errors_empty"] is (not expected_errors)
    if expected_errors:
        # This is the identical semantic oracle used by the actual CLI/MCP test.
        with pytest.raises(AssertionError):
            _assert_child_has_no_errors(report)
    else:
        _assert_child_has_no_errors(report)


@pytest.fixture
def delivery_trace(monkeypatch):
    """Observe real owner issuance/presentation without changing their results."""
    from code_forge import _advisory_diagnostics as storage
    from code_forge import _cross_repo_diagnostics as capture_module

    captures, presented, reads, attempts = [], [], [], []
    native_init = capture_module.DiagnosticCapture.__init__
    native_present = capture_module.DiagnosticCapture.present
    native_read = storage.read_observation

    def initialize(owner, **kwargs):
        native_init(owner, **kwargs)
        # Independent expected inputs are held before any payload is written/read.
        expected = storage.ExpectedContext(
            kwargs["execution_id"],
            owner.repository_id,
            kwargs["source_hash"],
            hashlib.sha256(kwargs["diff_text"].encode("utf-8")).hexdigest(),
            1,
        )
        captures.append((owner, expected, kwargs["repo_root"]))

    def present(owner, record, **kwargs):
        result = native_present(owner, record, **kwargs)
        presented.append((owner, record, result, owner.delivery_status))
        return result

    def read(publication, expected):
        # Record inputs and success without assertions inside a best-effort catch.
        # All expectations are checked outside the production consumer.
        succeeded = False
        try:
            result = native_read(publication, expected)
            reads.append((publication, expected, result))
            succeeded = True
            return result
        finally:
            attempts.append((publication, expected, succeeded))

    read.__wrapped__ = native_read
    monkeypatch.setattr(capture_module.DiagnosticCapture, "__init__", initialize)
    monkeypatch.setattr(capture_module.DiagnosticCapture, "present", present)
    monkeypatch.setattr(storage, "read_observation", read)
    return captures, presented, reads, attempts


@pytest.mark.parametrize("sink_fails", [False, True], ids=["sink-ok", "sink-fails"])
@pytest.mark.parametrize("fault", ["none", "bind", "publish", "read", "missing"])
def test_host_not_run_empty_success_and_unavailable_statuses(
    persistent_cli, delivery_trace, monkeypatch, fault, sink_fails
):
    from code_forge import _advisory_diagnostics as storage
    from code_forge import _cross_repo_diagnostics as capture_module
    from code_forge.machine import StateMachine
    from code_forge.source import compute_source_hash

    # Consumer-boundary control: _run_ci is supplied and dispatch is omitted.
    # This does not establish natural not_run reachability.
    # Existing repository coverage seam. We do not substitute finish/present or
    # synthesize a nonempty not_run observation to suggest a reachable bug.
    monkeypatch.setattr(StateMachine, "_run_advisory_axes", lambda machine: None)
    captures, presented, reads, read_attempts = delivery_trace
    native_read = storage.read_observation
    calls = []

    def fail(*args, **kwargs):
        raise OSError("owned diagnostic persistence failure")

    if fault == "bind":
        monkeypatch.setattr(storage, "bind_storage", fail)
    elif fault == "publish":
        monkeypatch.setattr(storage, "publish_observation", fail)
    elif fault == "read":
        monkeypatch.setattr(storage, "read_observation", fail)
    elif fault == "missing":

        def remove_then_read(publication, expected):
            paths = _records(persistent_cli.primary)
            assert len(paths) == 1
            paths[0].unlink()
            return native_read(publication, expected)

        monkeypatch.setattr(storage, "read_observation", remove_then_read)

    def output(message):
        if ": ADVISORY: " in message:
            calls.append(message)
            if sink_fails:
                raise BrokenPipeError("owned diagnostic unavailable sink")

    assert persistent_cli.run(output=output) is Verdict.PASS
    _assert_isolated(persistent_cli)
    assert len(captures) == len(presented) == 1
    owner, independent, root = captures[0]
    record = owner.records[0]
    assert root == persistent_cli.primary
    assert independent.source_hash == compute_source_hash(git_diff=DIFF)
    assert owner._active is False
    assert record.analysis_state == record.observation.analysis_state == "not_run"
    assert record.observation.diagnostics == ()
    assert record.observation.context == independent
    assert owner.last_delivery_origin == "actual"
    assert presented[0][1] is record
    assert read_attempts == (
        [] if fault == "read" else [(record.publication, independent, fault == "none")]
    )
    expected_publication = {"bind": "not_published", "publish": "unknown"}.get(fault, "published")
    assert record.publication.status == expected_publication
    assert (record.publication.locator is not None) is (expected_publication == "published")
    if fault == "none":
        assert presented[0][2:] == ((), "read")
        assert owner.delivery_status == "delivered"
        assert calls == []  # No output exists to lose, even with a failing sink.
        assert len(reads) == 1
        assert reads[0] == (record.publication, independent, record.observation)
        assert len(_records(root)) == 1
        _payload(_records(root)[0], root, state="not_run")
        assert storage.read_observation(record.publication, independent) == record.observation
    else:
        assert presented[0][2:] == ((capture_module.PERSISTENCE_WARNING,), "not_delivered")
        assert owner.delivery_status == "not_delivered"
        assert calls == [PERSISTENCE_WARNING]
        assert reads == []
        assert not any(MESSAGE in message for message in calls)
        if fault in {"bind", "publish", "missing"}:
            assert _records(root) == []
        else:
            assert len(_records(root)) == 1
            _payload(_records(root)[0], root, state="not_run")
            assert native_read.__wrapped__(record.publication, independent) == record.observation
