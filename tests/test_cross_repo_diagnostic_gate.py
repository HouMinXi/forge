# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Diagnostic isolation after genuinely reachable, service-free CI verdicts.

The CLI dispatch helper, host, CI round, L1/falsification and advisory runners
are unchanged. A local executable supplies deterministic backend responses.
These docs-only cases do not claim mutation measurement, outer CLI parsing,
MCP transport, or HOLD/recovery coverage.
"""

from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from code_forge import _advisory_diagnostics as storage
from code_forge import cli
from code_forge.backend import BackendConfig
from code_forge.baseline import GitRefBaseline
from code_forge.cross_repo import get_sibling_diff
from code_forge.disposition import Disposition
from code_forge.machine import StateMachine
from code_forge.receipt_scope import repository_scope
from code_forge.state import Mode, Verdict
from tests.test_cross_repo import _make_repo

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="persistent diagnostics qualify Linux local filesystems"
)


@pytest.fixture
def native_ci_input(tmp_path, monkeypatch):
    primary, sibling = (
        _make_repo(
            tmp_path,
            monkeypatch,
            name,
            filename="README.md",
            content_v1="Old fixture documentation.\n",
            content_v2="New fixture documentation.\n",
        )
        for name in ("primary", "consumer")
    )
    for root in (primary, sibling):
        subprocess.run(["git", "checkout", "feature"], cwd=root, check=True, capture_output=True)
        # Toolchain detection needs a Python source; only README.md is reviewed.
        (root / "api.py").write_text("def api():\n    return 1\n", encoding="utf-8")
        (root / ".code-review-graph").mkdir()
        with closing(sqlite3.connect(root / ".code-review-graph/graph.db")) as connection, connection:
            connection.executescript(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);"
                "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
                "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
                "CREATE TABLE edges(kind TEXT, source_qualified TEXT, target_qualified TEXT);"
            )
            connection.execute("INSERT INTO metadata VALUES ('repo_root', ?)", (str(root),))
            connection.execute(
                "INSERT INTO nodes VALUES (1,'module','module-level','readme','README.md',1,1)"
            )
    diffs = {
        "primary": get_sibling_diff(primary, "main..feature"),
        "consumer": get_sibling_diff(sibling, "main..feature"),
    }
    _, manifest = repository_scope(diffs)
    excerpts = [
        {
            "file": f"{label}@{manifest[label]}/README.md",
            "start_line": 1,
            "end_line": 1,
            "content": "New fixture documentation.",
        }
        for label in diffs
    ]
    executable = tmp_path / "owned-backend"
    executable.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "prompt = sys.argv[sys.argv.index('-p') + 1]\n"
        f"excerpts = {excerpts!r}\n"
        "if prompt.startswith('You are a code review verifier.'):\n"
        "    result = {'verdict': 'CONFIRMED', 'reasoning': 'Owned fixture finding.'}\n"
        "elif 'Each diff hunk MUST have at least one code_excerpt.' in prompt:\n"
        "    findings = []\n"
        "    if os.environ['FORGE_FIXTURE_GATE'] == 'FAIL':\n"
        "        findings = [{'file': excerpts[0]['file'], 'line': 1, 'severity': 'P1',"
        " 'description': 'Owned fixture finding.', 'excerpt': excerpts[0]['content']}]\n"
        "    result = {'findings': findings, 'code_excerpts': excerpts}\n"
        "else:\n"
        "    result = {'surfaces': [], 'findings': []}\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    backend = BackendConfig(name="owned-local", type="cli", model="", command=str(executable))
    (primary / ".code-forge").mkdir()
    gate = primary / ".code-forge/gate.yaml"
    gate.write_text(
        json.dumps(
            {
                "siblings": [{"repo": str(sibling), "ref": "main..feature", "label": "consumer"}],
                "test": {"command": [sys.executable, "-c", "raise SystemExit(0)"]},
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"repos": [{"path": str(primary)}, {"path": str(sibling)}]}), encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "CRG_HOME": str(tmp_path),
        "CRG_REGISTRY_PATH": str(registry),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "FORGE_SKIP_WORKTREE_CHECK": "1",
        "FORGE_MUTATION_IDLE_MAX_WAIT": "0",
    }
    # Provider-free CI substitutes only its pure locator, not Forge discovery.
    try:
        import code_review_graph.registry  # noqa: F401
    except ImportError:
        import types

        package = types.ModuleType("code_review_graph")
        package.__path__ = []
        provider = types.ModuleType("code_review_graph.registry")
        provider.default_registry_path = lambda: registry
        package.registry = provider
        monkeypatch.setitem(sys.modules, package.__name__, package)
        monkeypatch.setitem(sys.modules, provider.__name__, provider)
    return primary, sibling, gate, backend, env


def test_native_ci_fixture_closes_graph_connections(request, monkeypatch):
    connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    try:
        request.getfixturevalue("native_ci_input")
        assert len(connections) == 2
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL], ids=["PASS", "FAIL"])
@pytest.mark.parametrize("publication_fault", [False, True], ids=["published", "publication-fault"])
def test_docs_only_native_ci_keeps_gate_state_with_diagnostic(
    native_ci_input, monkeypatch, verdict, publication_fault
):
    primary, sibling, gate, backend, env = native_ci_input
    env["FORGE_FIXTURE_GATE"] = verdict.value
    before, after, ci_returns, captures, reads, cwds = {}, {}, {}, [], [], []
    receipts_before, receipts_after, receipt_decisions = {}, {}, []

    def profile(frame, event, value):
        if prior_profile is not None:
            prior_profile(frame, event, value)
        if frame.f_code is StateMachine._run_ci.__code__ and event == "return":
            ci_returns[frame.f_locals["self"].baseline_spec_repr] = value
        if frame.f_code is StateMachine._run_advisory_axes.__code__:
            machine = frame.f_locals["self"]
            snapshots = before if event == "call" else after if event == "return" else None
            if snapshots is not None:
                snapshots[machine.baseline_spec_repr] = deepcopy(machine._state)
                receipts = receipts_before if event == "call" else receipts_after
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
        if frame.f_code is storage.read_observation.__code__ and event == "return" and value is not None:
            reads.append(value)

        if (
            frame.f_code
            in (
                StateMachine._receipt_gate_round_errors.__code__,
                StateMachine._receipt_gate_terminal_errors.__code__,
            )
            and event == "return"
        ):
            machine = frame.f_locals["self"]
            if machine.baseline_spec_repr == "primary":
                receipt_decisions.append((frame.f_code.co_name, not value))

    if publication_fault:

        def fail_publication(*args):
            raise OSError("owned diagnostic publication failure")

        monkeypatch.setattr(storage, "publish_observation", fail_publication)
    output, errors = io.StringIO(), io.StringIO()
    prior_profile = threading.getprofile()
    with patch.dict(os.environ, env, clear=True), redirect_stdout(output), redirect_stderr(errors):
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
    # Other native axes can append environment-dependent tool availability
    # notes. Compare every other State field, and reject diagnostic text directly.
    assert replace(after["primary"], infra_errors=before["primary"].infra_errors) == before["primary"]
    assert before["primary"].infra_errors == []
    assert storage.MESSAGE not in after["primary"].infra_errors
    assert "cross-repo-impact: persistent diagnostic unavailable" not in after["primary"].infra_errors
    assert all("cross-repo-impact" not in item for item in after["primary"].infra_errors)
    assert not any(storage.MESSAGE in finding.description for finding in after["primary"].findings)
    assert receipt_decisions == [
        ("_receipt_gate_round_errors", True),
        ("_receipt_gate_terminal_errors", True),
    ]
    assert receipts_before == receipts_after
    assert len(receipts_before["primary"]) == 3
    for name, raw in receipts_before["primary"].items():
        # No timestamp or decision field is normalized out: actual publication
        # bytes survive both the diagnostic axis and final host receipt copying.
        assert (primary / ".code-forge" / ("primary-" + name)).read_bytes() == raw
        receipt = json.loads(raw)
        assert receipt["pass_status"] == "completed"
        assert receipt["findings_count"] == int(verdict is Verdict.FAIL)
        assert len(receipt["code_excerpts"]) == 2
        assert storage.MESSAGE.encode() not in raw
    assert len(captures) == 1 and len(captures[0].records) == 1
    owner = captures[0]
    record = owner.records[0]
    assert not owner._active
    assert record.analysis_state == "no_named_targets"
    assert record.publication.status == ("unknown" if publication_fault else "published")
    assert owner.delivery_status == ("not_delivered" if publication_fault else "delivered")
    assert len(reads) == int(not publication_fault)
    if not publication_fault:
        assert reads[0] == record.observation
        assert storage.read_observation(record.publication, record.observation.context) == reads[0]
    else:
        assert record.publication.locator is None
    assert (
        output.getvalue().splitlines().count("[cross-repo] primary: ADVISORY: " + storage.MESSAGE) == 1
    )
    warning = "[cross-repo] primary: ADVISORY: cross-repo-impact: persistent diagnostic unavailable"
    assert output.getvalue().splitlines().count(warning) == int(publication_fault)
    assert len(list((primary / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))) == int(
        not publication_fault
    )
    assert not list((sibling / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))
    assert len(cwds) == 2 and all(not cwd.exists() for cwd in cwds)
    assert not (primary / ".code-forge/code-forge.lock").exists()
    assert not (sibling / ".code-forge/code-forge.lock").exists()
