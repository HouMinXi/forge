# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Real orchestration/SQLite controls; review body is limited to advisory dispatch."""

import json
import sqlite3
import subprocess
from contextlib import closing

import pytest

pytest.importorskip("code_review_graph")

from code_forge.baseline import ResolvedReview
from code_forge.cross_repo import run_cross_repo
from code_forge.cross_repo_impact import CrossRepoImpactRunner
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict


def _git(repo, *args):
    return subprocess.check_output(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        text=True,
        timeout=10,
    )


def _graph(path, root, *, sibling=False, named=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE nodes (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
            "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT);"
        )
        conn.execute("INSERT INTO metadata VALUES ('repo_root', ?)", (str(root),))
        if sibling:
            conn.execute(
                "INSERT INTO nodes VALUES (1, 'function', 'consumer', 'consumer::consumer', "
                "'consumer.py', 1, 3)"
            )
            conn.executemany(
                "INSERT INTO edges VALUES (?, ?, ?)",
                [
                    ("CALLS", "consumer::consumer", "handler"),
                    ("IMPORTS_FROM", "consumer::consumer", "handler"),
                ],
            )
        elif named:
            conn.execute(
                "INSERT INTO nodes VALUES (1, 'function', 'handler', 'handler::handler', "
                "'handler.py', 1, 3)"
            )
        conn.commit()


@pytest.fixture
def graph_repos(tmp_path, monkeypatch):
    repos = {name: tmp_path / name for name in ("primary", "sibling")}
    for name, repo in repos.items():
        repo.mkdir()
        (repo / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1"\n')
        filename = "handler.py" if name == "primary" else "consumer.py"
        function = "handler" if name == "primary" else "consumer"
        (repo / filename).write_text(f"def {function}():\n    return 1\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / filename).write_text(f"def {function}():\n    return 2\n")
        _git(repo, "commit", "-qam", "change")
    home = tmp_path / "provider"
    home.mkdir()
    monkeypatch.setenv("CRG_HOME", str(home))
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)
    monkeypatch.delenv("CRG_REGISTRY_PATH", raising=False)
    return repos, home


@pytest.mark.parametrize("placement", ["registry", "environment"])
@pytest.mark.parametrize("graph_state", ["caller", "no-target", "foreign-root"])
def test_cross_repo_primary_advisory_uses_source_root(
    graph_repos, tmp_path, monkeypatch, placement, graph_state
):
    repos, home = graph_repos
    data = tmp_path / "graph-data"
    recorded_root = repos["sibling"] if graph_state == "foreign-root" else repos["primary"]
    _graph(data / "graph.db", recorded_root, named=graph_state != "no-target")
    _graph(repos["sibling"] / ".code-review-graph/graph.db", repos["sibling"], sibling=True)
    primary_entry = {"path": str(repos["primary"]), "alias": "primary"}
    if placement == "registry":
        primary_entry["data_dir"] = str(data)
    else:
        monkeypatch.setenv("CRG_DATA_DIR", str(data))
    # Sibling data_dir wins over the global override, preserving provider precedence.
    entries = [
        primary_entry,
        {
            "path": str(repos["sibling"]),
            "alias": "sibling",
            "data_dir": str(repos["sibling"] / ".code-review-graph"),
        },
    ]
    (home / "registry.json").write_text(json.dumps({"repos": entries}))
    observations = {}

    class AdvisoryMachine(StateMachine):
        def run(self):
            target = [r for r in self.advisory_runners if isinstance(r, CrossRepoImpactRunner)]
            assert len(target) == (1 if self.baseline_spec_repr == "primary" else 0)
            # Isolate this axis; retain the actual dispatcher and target runner.
            self.advisory_runners = target
            self._run_advisory_axes()
            observations[self.baseline_spec_repr] = (
                self.cwd,
                self._source_root(),
                list(self._advisories),
                list(self._state.infra_errors),
            )
            return Verdict.PASS  # Controlled review body, not a convergence assertion.

    monkeypatch.setattr("code_forge.machine.StateMachine", AdvisoryMachine)
    run_cross_repo(
        primary_path=repos["primary"],
        primary_ref="main..feature",
        primary_label="primary",
        siblings=[{"repo": str(repos["sibling"]), "ref": "main..feature", "label": "sibling"}],
        gate_config={"test": {"command": ["echo", "ok"]}},
        mode=Mode.LOCAL,
        engine_choice="stub",
        backend=None,
        max_rounds=1,
        max_fix_attempts=1,
        clean_round_threshold=1,
        output_fn=lambda _: None,
    )
    cwd, source, findings, errors = observations["primary"]
    assert source == repos["primary"] and cwd != source
    if graph_state == "caller":
        assert len(findings) == 1
        assert findings[0].file == "sibling:consumer.py"
        assert errors == []
    elif graph_state == "no-target":
        assert findings == [] and errors == []  # No #441 diagnostic protocol added.
    else:
        assert findings == []
        assert len(errors) == 1 and "different repository root" in errors[0]
    assert observations["sibling"][2:] == ([], [])
    assert all(not observation[0].exists() for observation in observations.values())


@pytest.mark.parametrize("explicit_root", [False, True])
def test_advisory_root_fallback_subclass_and_generic_cwd(tmp_path, explicit_root):
    seen = {}

    class InheritedImpact(CrossRepoImpactRunner):
        def run(self, diff_text, repo_root):
            seen["impact"] = repo_root
            return super().run(diff_text, repo_root)

    class GenericRunner:
        def run(self, diff_text, repo_root):
            seen["generic"] = repo_root
            return []

    cwd, root = tmp_path / "state", tmp_path / "source"
    cwd.mkdir()
    root.mkdir()
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        resolved_review=ResolvedReview(
            source_files=[], baseline_content=None, git_diff="", mode_hint="git"
        ),
        source_hash="controlled",
        baseline_spec_repr="controlled",
        cwd=cwd,
        registry={},
        source_root=root if explicit_root else None,
        advisory_runners=[InheritedImpact(), GenericRunner()],
    )
    machine._run_advisory_axes()
    assert seen == {"impact": root if explicit_root else cwd, "generic": cwd}
    assert machine._state.infra_errors == []
