# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Immediate no-target output, not the persistent diagnostic contract in #441."""

from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import sys
import types

import pytest

from code_forge import cross_repo_impact as impact


MESSAGE = "cross-repo-impact: no named changed symbols resolved; direct-caller impact not evaluated"
DIFF = "diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.fixture
def graphs(tmp_path, monkeypatch):
    home = tmp_path / "graph-home"
    # CI does not install the optional provider. Isolate only its pure path
    # helper; Forge's registry discovery and graph queries still use real files.
    package = types.ModuleType("code_review_graph")
    package.__path__ = []
    provider = types.ModuleType("code_review_graph.registry")
    provider.default_registry_path = lambda: home / "registry.json"
    package.registry = provider
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, provider.__name__, provider)
    monkeypatch.setenv("CRG_HOME", str(home))
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)
    primary = tmp_path / "primary"
    sibling = tmp_path / "sibling"
    for repo in (primary, sibling):
        repo.mkdir()
        (repo / "api.py").write_text("def api():\n    return 1\n", encoding="utf-8")
        (repo / ".code-review-graph").mkdir()
        with closing(sqlite3.connect(repo / ".code-review-graph/graph.db")) as conn:
            conn.executescript(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);"
                "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
                "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
                "CREATE TABLE edges(kind TEXT, source_qualified TEXT, target_qualified TEXT);"
            )
            conn.execute("INSERT INTO metadata VALUES ('repo_root', ?)", (str(repo),))
            conn.execute("INSERT INTO nodes VALUES (1,'module','module-level','api','api.py',1,2)")
            conn.commit()
    registry = tmp_path / "enumeration.json"
    registry.write_text(
        json.dumps({"repos": [{"path": str(primary)}, {"path": str(sibling), "alias": "consumer"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CRG_REGISTRY_PATH", str(registry))
    return primary, sibling, registry, provider.default_registry_path()


def edit_graph(repo, statement):
    with closing(sqlite3.connect(repo / ".code-review-graph/graph.db")) as conn:
        conn.execute(statement)
        conn.commit()


def forbid(*args, **kwargs):
    pytest.fail("zero-target analysis must not enumerate or query siblings")


@pytest.mark.parametrize("case", ["unnamed", "no-matching-node", "no-path", "deleted"])
def test_zero_targets_emit_once_before_sibling_access(graphs, monkeypatch, case):
    primary, _, _, _ = graphs
    diff = DIFF
    if case == "no-matching-node":
        edit_graph(primary, "DELETE FROM nodes")
    elif case == "no-path":
        diff = "a nonempty diff without an added-side path\n"
    elif case == "deleted":
        diff = DIFF.replace("+++ b/api.py", "+++ /dev/null")
    monkeypatch.setattr(impact, "_registry_path", forbid)
    monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", forbid)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    assert runner.run(diff, primary) == []
    assert messages == [MESSAGE]
    assert runner.infra_errors == []
    assert runner._cached is None


@pytest.mark.parametrize("diff", ["", " \t\n"])
def test_empty_diff_has_no_diagnostic_or_discovery(graphs, monkeypatch, diff):
    monkeypatch.setattr(impact, "_read_registry", forbid)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    assert runner.run(diff, graphs[0]) == []
    assert runner.infra_errors == []
    assert messages == []


@pytest.mark.parametrize("case", ["provider", "registry", "missing", "corrupt", "identity"])
def test_prior_unavailability_does_not_claim_zero_targets(graphs, monkeypatch, case):
    primary, _, _, default_registry = graphs
    db = primary / ".code-review-graph/graph.db"
    if case == "provider":
        monkeypatch.setitem(sys.modules, "code_review_graph.registry", None)
    elif case == "registry":
        default_registry.parent.mkdir(parents=True)
        default_registry.write_text("{bad json", encoding="utf-8")
    elif case == "missing":
        db.unlink()
    elif case == "corrupt":
        db.write_bytes(b"not a SQLite graph")
    else:
        edit_graph(primary, "DELETE FROM metadata")
    control = impact.CrossRepoImpactRunner()
    assert control.run(DIFF, primary) == []
    assert control.infra_errors
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    assert runner.run(DIFF, primary) == []
    assert runner.infra_errors == control.infra_errors
    assert messages == []


@pytest.mark.parametrize("callers", [False, True])
def test_resolved_targets_keep_findings_and_cache_without_zero_target_output(graphs, callers):
    primary, sibling, _, _ = graphs
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    if callers:
        edit_graph(sibling, "UPDATE nodes SET kind='function', name='use', qualified_name='client::use'")
        edit_graph(sibling, "INSERT INTO edges VALUES ('CALLS', 'client::use', 'api')")
        edit_graph(sibling, "INSERT INTO edges VALUES ('IMPORTS_FROM', 'client::use', 'api')")
    control = impact.CrossRepoImpactRunner()
    expected = control.run(DIFF, primary)
    assert len(expected) == int(callers)
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    actual = runner.run(DIFF, primary)
    assert actual == expected
    assert runner.infra_errors == control.infra_errors == []
    assert runner.run(DIFF, primary) is actual
    assert messages == []


def test_zero_targets_remain_uncached_and_later_errors_or_targets_are_not_relabelled(graphs):
    primary, _, _, _ = graphs
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    assert runner.run(DIFF, primary) == []
    assert runner.run(DIFF, primary) == []
    assert messages == [MESSAGE, MESSAGE]
    edit_graph(primary, "DELETE FROM metadata")
    assert runner.run(DIFF, primary) == []
    assert runner.infra_errors
    assert messages == [MESSAGE, MESSAGE]
    with closing(sqlite3.connect(primary / ".code-review-graph/graph.db")) as conn:
        conn.execute("INSERT INTO metadata VALUES ('repo_root', ?)", (str(primary),))
        conn.execute("UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
        conn.commit()
    assert runner.run(DIFF, primary) == []
    assert runner.infra_errors == []
    assert runner._cached == []
    assert messages == [MESSAGE, MESSAGE]


def test_default_runner_is_silent(graphs, capsys):
    assert impact.CrossRepoImpactRunner().run(DIFF, graphs[0]) == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("error", [RuntimeError, OSError, BrokenPipeError])
def test_output_failure_does_not_become_infrastructure_failure(graphs, error):
    def output(message):
        assert message == MESSAGE
        raise error("unavailable output")

    runner = impact.CrossRepoImpactRunner(output_fn=output)
    assert runner.run(DIFF, graphs[0]) == []
    assert runner.infra_errors == []
    assert runner._cached is None


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_output_does_not_swallow_process_control(graphs, error):
    fault = error("stop")

    def output(message):
        raise fault

    runner = impact.CrossRepoImpactRunner(output_fn=output)
    with pytest.raises(error) as caught:
        runner.run(DIFF, graphs[0])
    assert caught.value is fault
    assert runner.infra_errors == []


@pytest.fixture
def primary_cli_output(graphs, tmp_path, monkeypatch):
    """Real CLI dispatch, cross-repo host, machine.run and advisory dispatch.

    Only the gate outcome and six unrelated advisory axes are controlled.
    Diff capture is stubbed; graph discovery and SQLite queries are real.
    """
    from code_forge import cli
    from code_forge.baseline import GitRefBaseline
    from code_forge.machine import StateMachine
    from code_forge.state import Mode, Verdict

    primary, sibling, _, _ = graphs
    gate = primary / ".code-forge/gate.yaml"
    gate.parent.mkdir()
    gate.write_text(
        json.dumps({"siblings": [{"repo": str(sibling), "ref": "main..feature", "label": "consumer"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("code_forge.cross_repo.get_sibling_diff", lambda *args: DIFF)
    axes = []
    for module, name in (
        ("taint", "TaintRunner"),
        ("runtime", "RuntimeRunner"),
        ("graph_triage", "GraphTriageRunner"),
        ("daemon_state", "DaemonStateRunner"),
        ("legacy", "LegacyRunner"),
        ("rulepack", "RulepackRunner"),
    ):
        imported = __import__("code_forge." + module, fromlist=[name])
        cls = getattr(imported, name)
        axes.append(cls)
        monkeypatch.setattr(cls, "run", lambda *args, **kwargs: [])
    states = {}
    machines = {}

    def gate_result(machine):
        machine._state.verdict = expected_verdict
        states[machine.baseline_spec_repr] = deepcopy(machine._state)
        machines[machine.baseline_spec_repr] = machine
        return expected_verdict

    monkeypatch.setattr(StateMachine, "_run_ci", gate_result)

    def run(verdict=Verdict.PASS, *, broken_output=False):
        nonlocal expected_verdict
        expected_verdict = verdict
        # A failing stdout sink must not become a machine infrastructure error.
        if broken_output:
            import builtins

            original_print = builtins.print

            def output(*args, **kwargs):
                if args and MESSAGE in str(args[0]):
                    raise OSError("diagnostic output unavailable")
                return original_print(*args, **kwargs)

            # run_cross_repo's default was bound at import time. Wrap the CLI
            # seam solely to inject this failing sink; keep the real host.
            from code_forge import cross_repo

            native_host = cross_repo.run_cross_repo

            def host(**kwargs):
                return native_host(**kwargs, output_fn=output)

            monkeypatch.setattr(cross_repo, "run_cross_repo", host)
        actual = cli._cross_repo_verdict_or_none(
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
        assert actual is verdict
        assert set(machines) == {"primary", "consumer"}
        assert [type(runner) for runner in machines["primary"].advisory_runners] == [
            *axes,
            impact.CrossRepoImpactRunner,
        ]
        assert machines["consumer"].advisory_runners == []
        for label, machine in machines.items():
            assert machine._state == states[label]
            assert machine._advisories == []
            assert not machine.cwd.exists()  # Host cleanup still runs.
            assert not (machine._source_root() / ".code-forge/code-forge.lock").exists()
        assert machines["primary"]._source_root() == primary
        assert machines["consumer"]._source_root() == sibling
        return actual

    expected_verdict = Verdict.PASS
    return run


@pytest.mark.parametrize("verdict_name", ["PASS", "FAIL"])
@pytest.mark.parametrize("broken_output", [False, True])
def test_primary_cli_output_is_attributed_without_state_or_gate_changes(
    primary_cli_output, capsys, verdict_name, broken_output
):
    from code_forge.state import Verdict

    primary_cli_output(Verdict[verdict_name], broken_output=broken_output)
    stdout = capsys.readouterr().out
    expected = "[cross-repo] primary: ADVISORY: " + MESSAGE
    assert stdout.splitlines().count(expected) == (0 if broken_output else 1)
    assert "consumer: ADVISORY:" not in stdout


@pytest.mark.parametrize("verdict_name,exit_code", [("PASS", 0), ("FAIL", 1)])
@pytest.mark.asyncio
async def test_existing_mcp_outputs_preserve_primary_cli_diagnostic(
    primary_cli_output, capsys, monkeypatch, verdict_name, exit_code
):
    pytest.importorskip("mcp")
    from code_forge import mcp_server
    from code_forge.state import Verdict

    primary_cli_output(Verdict[verdict_name])
    stdout = capsys.readouterr().out
    assert stdout.count(MESSAGE) == 1
    immediate = mcp_server._make_result(stdout, exit_code, 0.1)
    assert immediate.content[0].text == stdout
    assert immediate.structuredContent["output"] == stdout
    assert immediate.structuredContent["verdict"] == verdict_name
    assert immediate.structuredContent["findings"] is None
    assert immediate.structuredContent["findings_count"] is None
    monkeypatch.setattr(
        mcp_server,
        "get_job",
        lambda job_id: {
            "status": "completed",
            "result": {"stdout": stdout, "exit_code": exit_code, "verdict": verdict_name},
        },
    )
    background = await mcp_server.forge_job_status("fixture-job")
    result = background.structuredContent["result"]
    assert result["output"] == stdout
    assert stdout in background.content[0].text
    assert result["verdict"] == verdict_name
    assert result["findings"] is None
    assert result["findings_count"] is None
    assert set(result) == set(immediate.structuredContent)
