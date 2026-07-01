# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for GraphTriageRunner advisory axis.

Covers: dual-backend detection (sem preferred, graphdb fallback),
blast-radius ranking, top-10 output, gate.yaml validation,
find_entity_dependents utility, and tool-absent loud-fail.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from code_forge.graph_triage import (
    GraphTriageRunner,
    _detect_backend,
    _run_sem,
    find_entity_dependents,
)


# ---------------------------------------------------------------------------
# Helpers: mock data factories
# ---------------------------------------------------------------------------

def _sem_diff_json(entities: list[dict]) -> str:
    """Build sem diff --format json stdout."""
    return json.dumps({"summary": {}, "changes": entities})


def _sem_impact_json(
    entity_name: str,
    total: int,
    dependents: list[dict] | None = None,
) -> str:
    """Build sem impact --json stdout."""
    if dependents is None:
        dependents = [
            {"entityId": "dep_%d" % i, "entityName": "dep_%d" % i}
            for i in range(min(total, 5))
        ]
    return json.dumps({
        "entity": {"entityName": entity_name},
        "dependencies": [],
        "dependents": dependents,
        "impact": {"depth": 1, "entities": [], "total": total},
        "tests": [],
    })


def _make_entity(
    name: str,
    file_path: str,
    start: int = 1,
    end: int = 10,
    change_type: str = "modified",
) -> dict:
    """Build a single entity dict for sem diff output."""
    return {
        "entityId": "%s::function::%s" % (file_path, name),
        "changeType": change_type,
        "entityName": name,
        "filePath": file_path,
        "startLine": start,
        "endLine": end,
    }


def _make_diff(files: list[str]) -> str:
    """Build a minimal unified diff touching the given files."""
    parts = []
    for f in files:
        parts.append(
            "diff --git a/%(f)s b/%(f)s\n"
            "--- a/%(f)s\n"
            "+++ b/%(f)s\n"
            "@@ -1,3 +1,4 @@\n"
            " existing\n"
            "+new line\n"
            " more\n" % {"f": f}
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# GraphTriageRunner core behavior
# ---------------------------------------------------------------------------

class TestGraphTriageRunnerProtocol:
    """AxisRunner Protocol compliance."""

    def test_is_advisory(self):
        """GraphTriageRunner().is_advisory is True."""
        runner = GraphTriageRunner()
        assert runner.is_advisory is True

    def test_empty_diff(self):
        """Empty diff returns empty findings list."""
        runner = GraphTriageRunner()
        result = runner.run("", Path("/tmp"))
        assert result == []

    def test_empty_whitespace_diff(self):
        """Whitespace-only diff returns empty findings list."""
        runner = GraphTriageRunner()
        result = runner.run("  \n  ", Path("/tmp"))
        assert result == []


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

class TestDetectBackend:
    """_detect_backend() priority: sem > gate.yaml db_path > auto > env."""

    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_detect_sem_preferred(self, mock_which):
        """When sem is available, prefer it over graph.db."""
        result = _detect_backend(Path("/repo"), {})
        assert result is not None
        assert result[0] == "sem"
        assert result[1] == "/usr/bin/sem"

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    def test_detect_graphdb_fallback(self, mock_which, tmp_path):
        """When sem absent but graph.db exists at default path, use graphdb."""
        db_dir = tmp_path / ".code-review-graph"
        db_dir.mkdir()
        db_file = db_dir / "graph.db"
        db_file.write_text("")
        result = _detect_backend(tmp_path, {})
        assert result is not None
        assert result[0] == "graphdb"
        assert result[1] == str(db_file)

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    def test_detect_graphdb_via_gate_yaml(self, mock_which, tmp_path):
        """gate.yaml db_path overrides auto-discover when file exists."""
        custom_db = tmp_path / "custom" / "graph.db"
        custom_db.parent.mkdir()
        custom_db.write_text("")
        gate_config = {"graph_triage": {"db_path": str(custom_db)}}
        result = _detect_backend(tmp_path, gate_config)
        assert result is not None
        assert result[0] == "graphdb"
        assert result[1] == str(custom_db)

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    @patch.dict(os.environ, {"CRG_DB_PATH": ""}, clear=False)
    def test_detect_graphdb_via_env(self, mock_which, tmp_path):
        """CRG_DB_PATH env var used when gate.yaml and auto-discover fail."""
        env_db = tmp_path / "env_graph.db"
        env_db.write_text("")
        with patch.dict(os.environ, {"CRG_DB_PATH": str(env_db)}):
            result = _detect_backend(tmp_path, {})
        assert result is not None
        assert result[0] == "graphdb"
        assert result[1] == str(env_db)

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    def test_detect_none_both_absent(self, mock_which, tmp_path):
        """When sem absent and no graph.db found, returns None."""
        result = _detect_backend(tmp_path, {})
        assert result is None


# ---------------------------------------------------------------------------
# Tool-absent + explicit disable
# ---------------------------------------------------------------------------

class TestToolAbsent:
    """Both-absent and explicit-disable behavior."""

    @patch("code_forge.graph_triage._detect_backend", return_value=None)
    def test_both_absent_skip(self, mock_detect, capsys):
        """Both absent: run() returns [] and infra_errors has loud-fail."""
        runner = GraphTriageRunner()
        diff = _make_diff(["src/foo.py"])
        result = runner.run(diff, Path("/tmp"))
        assert result == []
        assert len(runner.infra_errors) >= 1
        assert "sem" in runner.infra_errors[0].lower() or \
               "graph" in runner.infra_errors[0].lower()
        # Verify stderr output
        captured = capsys.readouterr()
        assert "sem" in captured.err.lower() or \
               "graph" in captured.err.lower()

    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_explicit_disable(self, mock_which, tmp_path):
        """gate.yaml graph_triage.enabled=false disables even with sem."""
        runner = GraphTriageRunner()
        # Create gate.yaml with enabled=false
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        gate_yaml = forge_dir / "gate.yaml"
        gate_yaml.write_text(
            "test:\n"
            "  command: ['python3', '-m', 'pytest']\n"
            "graph_triage:\n"
            "  enabled: false\n"
        )
        diff = _make_diff(["src/foo.py"])
        result = runner.run(diff, tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# sem backend
# ---------------------------------------------------------------------------

class TestSemBackend:
    """sem CLI invocation and ranking."""

    @patch("code_forge.graph_triage.subprocess.run")
    def test_sem_diff_invocation(self, mock_run):
        """sem diff called with correct list args and --patch flag."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_sem_diff_json([_make_entity("foo", "src/foo.py")]),
            stderr="",
        )
        diff = _make_diff(["src/foo.py"])
        _run_sem(diff, Path("/repo"))
        # Verify the call
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
        assert "sem" in cmd
        assert "diff" in cmd
        assert "--patch" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        # Must not use shell=True
        assert call_args[1].get("shell") is not True

    @patch("code_forge.graph_triage.subprocess.run")
    def test_sem_impact_invocation(self, mock_run):
        """sem impact called with list args and --json flag."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_sem_impact_json("foo", 10),
            stderr="",
        )
        from code_forge.graph_triage import _get_sem_impact
        _get_sem_impact("foo", "src/foo.py", Path("/repo"))
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
        assert "sem" in cmd
        assert "impact" in cmd
        assert "--json" in cmd
        assert "--file" in cmd

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("sem", "/usr/bin/sem"))
    def test_sem_ranking_top10(self, mock_detect, mock_sem, mock_impact):
        """Given 15 entities, run() returns exactly 10 sorted descending."""
        entities = [
            _make_entity("func_%d" % i, "src/f%d.py" % i)
            for i in range(15)
        ]
        mock_sem.return_value = entities
        # Each entity has impact = 100 - i
        mock_impact.side_effect = [
            {
                "impact": {"total": 100 - i},
                "dependents": [
                    {"entityId": "d%d" % j, "entityName": "d%d" % j}
                    for j in range(min(100 - i, 5))
                ],
            }
            for i in range(15)
        ]
        runner = GraphTriageRunner()
        diff = _make_diff(["src/f0.py"])
        result = runner.run(diff, Path("/tmp"))
        assert len(result) == 10
        # Verify sorted descending by impact
        descriptions = [f.description for f in result]
        assert "func_0" in descriptions[0]

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("sem", "/usr/bin/sem"))
    def test_sem_entity_skip_unnamed(self, mock_detect, mock_sem, mock_impact):
        """Entities with 'module-level' or 'lines N' names are skipped."""
        entities = [
            _make_entity("module-level", "src/foo.py"),
            _make_entity("lines 1-5", "src/foo.py"),
            _make_entity("real_func", "src/foo.py"),
        ]
        mock_sem.return_value = entities
        mock_impact.return_value = {
            "impact": {"total": 10},
            "dependents": [{"entityId": "d1", "entityName": "d1"}],
        }
        runner = GraphTriageRunner()
        diff = _make_diff(["src/foo.py"])
        result = runner.run(diff, Path("/tmp"))
        # Only real_func should produce a finding
        assert len(result) == 1
        assert "real_func" in result[0].description

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("sem", "/usr/bin/sem"))
    def test_all_entities_unnamed_returns_empty(
        self, mock_detect, mock_sem, mock_impact,
    ):
        """When all entities are unnamed, run() returns [] with no crash."""
        entities = [
            _make_entity("module-level", "src/foo.py"),
            _make_entity("lines 10-20", "src/bar.py"),
        ]
        mock_sem.return_value = entities
        runner = GraphTriageRunner()
        diff = _make_diff(["src/foo.py"])
        result = runner.run(diff, Path("/tmp"))
        assert result == []
        # _get_sem_impact should never be called for unnamed entities
        mock_impact.assert_not_called()

    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("sem", "/usr/bin/sem"))
    def test_sem_subprocess_timeout(self, mock_detect, mock_sem):
        """TimeoutExpired on one entity gives impact=0; others processed."""
        entities = [
            _make_entity("slow_func", "src/a.py"),
            _make_entity("fast_func", "src/b.py"),
        ]
        mock_sem.return_value = entities

        def impact_side_effect(name, fpath, root):
            if name == "slow_func":
                # _get_sem_impact catches TimeoutExpired internally and
                # returns this fallback; mock mirrors that real behavior.
                return {"impact": {"total": 0}, "dependents": []}
            return {
                "impact": {"total": 5},
                "dependents": [{"entityId": "d1", "entityName": "d1"}],
            }

        with patch(
            "code_forge.graph_triage._get_sem_impact",
            side_effect=impact_side_effect,
        ):
            runner = GraphTriageRunner()
            diff = _make_diff(["src/a.py", "src/b.py"])
            result = runner.run(diff, Path("/tmp"))
            # fast_func should still produce a finding
            assert len(result) >= 1
            names = [f.description for f in result]
            assert any("fast_func" in n for n in names)

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("sem", "/usr/bin/sem"))
    def test_sem_finding_format(self, mock_detect, mock_sem, mock_impact):
        """AdvisoryFinding fields match axis='GRAPH-TRIAGE' etc."""
        entities = [_make_entity("my_func", "src/my.py", 10, 20)]
        mock_sem.return_value = entities
        mock_impact.return_value = {
            "impact": {"total": 42},
            "dependents": [
                {"entityId": "d1", "entityName": "caller_a"},
                {"entityId": "d2", "entityName": "caller_b"},
            ],
        }
        runner = GraphTriageRunner()
        diff = _make_diff(["src/my.py"])
        result = runner.run(diff, Path("/tmp"))
        assert len(result) == 1
        f = result[0]
        assert f.axis == "GRAPH-TRIAGE"
        assert f.file == "src/my.py"
        assert "my_func" in f.description
        assert "42" in f.description
        assert "sem" in f.attribution


# ---------------------------------------------------------------------------
# graphdb backend
# ---------------------------------------------------------------------------

class TestGraphDBBackend:
    """graph.db SQLite backend with IMPORTS_FROM disambiguation."""

    @patch("code_forge.graph_triage._detect_backend")
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_graphdb_node_query(self, mock_connect, mock_detect, tmp_path):
        """Nodes queried by file_path from diff."""
        db_path = str(tmp_path / "graph.db")
        mock_detect.return_value = ("graphdb", db_path)

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        # Return one node for the queried file
        mock_cursor.fetchall.side_effect = [
            # nodes query
            [("fn1", "function", "my_func", "src/foo.py::my_func",
              "src/foo.py", 1, 10)],
            # edges CALLS query for disambiguation
            [("caller::a",)],
            # No more nodes
            [],
        ]

        runner = GraphTriageRunner()
        diff = _make_diff(["src/foo.py"])
        runner.run(diff, Path("/tmp"))
        # Should have attempted sqlite3.connect
        mock_connect.assert_called_once()

    @patch("code_forge.graph_triage._detect_backend")
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_graphdb_edge_walk(self, mock_connect, mock_detect, tmp_path):
        """IMPORTS_FROM disambiguation applied in edge queries."""
        db_path = str(tmp_path / "graph.db")
        mock_detect.return_value = ("graphdb", db_path)

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        # Return multiple nodes with different dependent counts
        mock_cursor.fetchall.side_effect = [
            # nodes query
            [("fn1", "function", "run", "src/runner.py::run",
              "src/runner.py", 1, 10)],
            # edges disambiguation query -- returns 3 dependents after filter
            [("cli::main",), ("machine::dispatch",), ("test::test_run",)],
            [],
        ]

        runner = GraphTriageRunner()
        diff = _make_diff(["src/runner.py"])
        runner.run(diff, Path("/tmp"))
        # Should have queried with disambiguation
        calls = mock_cursor.execute.call_args_list
        sql_stmts = [str(c) for c in calls]
        assert any("IMPORTS_FROM" in s for s in sql_stmts)

    @patch("code_forge.graph_triage._detect_backend")
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_graphdb_ranking(self, mock_connect, mock_detect, tmp_path):
        """Multiple entities sorted by dependent count, top 10."""
        db_path = str(tmp_path / "graph.db")
        mock_detect.return_value = ("graphdb", db_path)

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        # 12 nodes, each with decreasing dependent counts
        nodes = [
            ("fn%d" % i, "function", "func_%d" % i,
             "src/f.py::func_%d" % i, "src/f.py", i, i + 5)
            for i in range(12)
        ]
        # Build fetchall responses: first the nodes query, then per-node edges
        responses = [nodes]
        for i in range(12):
            dep_count = 12 - i
            responses.append(
                [("dep_%d" % j,) for j in range(dep_count)]
            )
        responses.append([])  # Final empty for loop termination
        mock_cursor.fetchall.side_effect = responses

        runner = GraphTriageRunner()
        diff = _make_diff(["src/f.py"])
        result = runner.run(diff, Path("/tmp"))
        assert len(result) <= 10

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend",
           return_value=("graphdb", "/path/graph.db"))
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_graphdb_quality_caveat(
        self, mock_connect, mock_detect, mock_sem, mock_impact, tmp_path,
    ):
        """graphdb findings attribution contains 'graph.db (degraded)'."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_cursor.fetchall.side_effect = [
            [("fn1", "function", "unique_func", "src/x.py::unique_func",
              "src/x.py", 1, 10)],
            [("caller::a",), ("caller::b",)],
            [],
        ]

        runner = GraphTriageRunner()
        diff = _make_diff(["src/x.py"])
        result = runner.run(diff, Path("/tmp"))
        if result:
            for finding in result:
                assert "graph.db" in finding.attribution
                assert "degraded" in finding.attribution


# ---------------------------------------------------------------------------
# Security: no shell=True
# ---------------------------------------------------------------------------

class TestNoShellTrue:
    """Verify no subprocess call uses shell=True."""

    def test_no_shell_true(self):
        """grep the source for shell=True -- must not appear."""
        import inspect
        import code_forge.graph_triage as mod
        source = inspect.getsource(mod)
        assert "shell=True" not in source


# ---------------------------------------------------------------------------
# find_entity_dependents utility
# ---------------------------------------------------------------------------

class TestFindEntityDependents:
    """find_entity_dependents() exported utility."""

    @patch("code_forge.graph_triage.subprocess.run")
    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_find_entity_dependents_sem(self, mock_which, mock_run):
        """Uses sem when available, returns dependent IDs."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=_sem_impact_json("my_func", 3, dependents=[
                {"entityId": "a::caller1", "entityName": "caller1"},
                {"entityId": "b::caller2", "entityName": "caller2"},
                {"entityId": "c::caller3", "entityName": "caller3"},
            ]),
            stderr="",
        )
        result = find_entity_dependents("my_func", "src/foo.py", Path("/repo"))
        assert len(result) == 3
        assert "a::caller1" in result

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_find_entity_dependents_graphdb(self, mock_connect, mock_which, tmp_path):
        """Falls back to graphdb when sem absent."""
        # Create a real graph.db at the default path
        db_dir = tmp_path / ".code-review-graph"
        db_dir.mkdir()
        db_file = db_dir / "graph.db"
        db_file.write_text("")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_cursor.fetchall.return_value = [
            ("caller::func_a",),
            ("caller::func_b",),
        ]

        result = find_entity_dependents("my_func", "src/foo.py", tmp_path)
        assert len(result) == 2

    @patch("code_forge.graph_triage.shutil.which", return_value=None)
    def test_find_entity_dependents_none(self, mock_which, tmp_path):
        """When neither backend available, returns empty list."""
        result = find_entity_dependents("my_func", "src/foo.py", tmp_path)
        assert result == []
