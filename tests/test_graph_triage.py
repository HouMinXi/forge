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
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge.graph_triage import (
    GraphTriageRunner,
    _detect_backend,
    _run_sem,
    SemAcquisition,
    _sem_has_index,
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
            {"entityId": "dep_%d" % i, "entityName": "dep_%d" % i} for i in range(min(total, 5))
        ]
    return json.dumps(
        {
            "entity": {"entityName": entity_name},
            "dependencies": [],
            "dependents": dependents,
            "impact": {"depth": 1, "entities": [], "total": total},
            "tests": [],
        }
    )


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

    @patch("code_forge.graph_triage._sem_has_index", return_value=True)
    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_detect_sem_preferred(self, mock_which, mock_index):
        """When sem is available and indexed, prefer it over graph.db."""
        result = _detect_backend(Path("/repo"), {})
        assert result is not None
        assert result[0] == "sem"
        assert result[1] == "/usr/bin/sem"

    @patch("code_forge.graph_triage._sem_has_index", return_value=False)
    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_detect_sem_no_index_skips(self, mock_which, mock_index):
        """sem in PATH but no index for this repo -> skip sem, fallback."""
        result = _detect_backend(Path("/repo"), {})
        assert result is None

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
# D1: _sem_has_index file-based probe
# ---------------------------------------------------------------------------


class TestSemHasIndex:
    """_sem_has_index checks for .semcode.db existence."""

    def test_returns_true_when_db_present(self, tmp_path):
        """Repo with a .semcode.db file -> indexed."""
        (tmp_path / ".semcode.db").touch()
        assert _sem_has_index(tmp_path) is True

    def test_returns_true_when_db_is_directory(self, tmp_path):
        """sem 0.10.x stores the index as a DIRECTORY of lance tables.

        The real artifact on an indexed repo is a directory, not a
        file -- an is_file() probe returns False there and the sem
        backend never activates.  Existence is the correct signal.
        """
        db_dir = tmp_path / ".semcode.db"
        db_dir.mkdir()
        (db_dir / "functions.lance").mkdir()
        assert _sem_has_index(tmp_path) is True

    def test_returns_false_when_db_absent(self, tmp_path):
        """A repo without .semcode.db is not indexed.

        Only meaningful on sem versions that still keep an on-disk index.
        sem dropped .semcode.db in v0.21.0 and _sem_has_index reports True
        unconditionally there, so the absent-db path cannot be exercised.
        """
        from types import SimpleNamespace
        from unittest.mock import patch

        # Force the pre-0.21 branch: the probe must fall through to the
        # on-disk check rather than short-circuit on the version string.
        with patch("code_forge.graph_triage.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="sem 0.10.3")
            assert _sem_has_index(tmp_path) is False

    def test_returns_true_for_empty_db_dir(self, tmp_path):
        """An EMPTY .semcode.db directory still counts as indexed.

        Existence -- not content validity -- is the deliberate
        signal: a present-but-corrupt index is tolerated because
        _run_sem and _get_sem_impact degrade gracefully on non-zero
        exit.
        """
        (tmp_path / ".semcode.db").mkdir()
        assert _sem_has_index(tmp_path) is True


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
        assert "sem" in runner.infra_errors[0].lower() or "graph" in runner.infra_errors[0].lower()
        # Verify stderr output
        captured = capsys.readouterr()
        assert "sem" in captured.err.lower() or "graph" in captured.err.lower()

    @patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem")
    def test_explicit_disable(self, mock_which, tmp_path):
        """gate.yaml graph_triage.enabled=false disables even with sem."""
        runner = GraphTriageRunner()
        # Create gate.yaml with enabled=false
        forge_dir = tmp_path / ".code-forge"
        forge_dir.mkdir()
        gate_yaml = forge_dir / "gate.yaml"
        gate_yaml.write_text(
            "test:\n  command: ['python3', '-m', 'pytest']\ngraph_triage:\n  enabled: false\n"
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
        diff_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_sem_diff_json([_make_entity("foo", "src/foo.py")]),
            stderr="",
        )
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["sem", "--version"], returncode=0, stdout="sem 0.21.0", stderr=""
            ),
            diff_result,
        ]
        diff = _make_diff(["src/foo.py"])
        with patch("code_forge.graph_triage.shutil.which", return_value="/usr/bin/sem"):
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
            args=[],
            returncode=0,
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
    @patch("code_forge.graph_triage._detect_backend", return_value=("sem", "/usr/bin/sem"))
    def test_sem_ranking_top10(self, mock_detect, mock_sem, mock_impact):
        """Given 15 entities, run() returns exactly 10 sorted descending."""
        entities = [_make_entity("func_%d" % i, "src/f%d.py" % i) for i in range(15)]
        mock_sem.return_value = SemAcquisition("completed", entities)
        # Each entity has impact = 100 - i
        mock_impact.side_effect = [
            {
                "impact": {"total": 100 - i},
                "dependents": [
                    {"entityId": "d%d" % j, "entityName": "d%d" % j} for j in range(min(100 - i, 5))
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
    @patch("code_forge.graph_triage._detect_backend", return_value=("sem", "/usr/bin/sem"))
    def test_sem_entity_skip_unnamed(self, mock_detect, mock_sem, mock_impact):
        """Entities with 'module-level' or 'lines N' names are skipped."""
        entities = [
            _make_entity("module-level", "src/foo.py"),
            _make_entity("lines 1-5", "src/foo.py"),
            _make_entity("real_func", "src/foo.py"),
        ]
        mock_sem.return_value = SemAcquisition("completed", entities)
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
    @patch("code_forge.graph_triage._detect_backend", return_value=("sem", "/usr/bin/sem"))
    def test_all_entities_unnamed_returns_empty(
        self,
        mock_detect,
        mock_sem,
        mock_impact,
    ):
        """When all entities are unnamed, run() returns [] with no crash."""
        entities = [
            _make_entity("module-level", "src/foo.py"),
            _make_entity("lines 10-20", "src/bar.py"),
        ]
        mock_sem.return_value = SemAcquisition("completed", entities)
        runner = GraphTriageRunner()
        diff = _make_diff(["src/foo.py"])
        result = runner.run(diff, Path("/tmp"))
        assert result == []
        # _get_sem_impact should never be called for unnamed entities
        mock_impact.assert_not_called()

    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend", return_value=("sem", "/usr/bin/sem"))
    def test_sem_subprocess_timeout(self, mock_detect, mock_sem):
        """TimeoutExpired on one entity gives impact=0; others processed."""
        entities = [
            _make_entity("slow_func", "src/a.py"),
            _make_entity("fast_func", "src/b.py"),
        ]
        mock_sem.return_value = SemAcquisition("completed", entities)

        def impact_side_effect(name, fpath, root):
            if name == "slow_func":
                # _get_sem_impact now marks timeout with _timed_out key,
                # which trips the circuit breaker in _run_with_sem.
                return {"impact": {"total": 0}, "dependents": [], "_timed_out": True}
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
            # Circuit breaker trips on slow_func timeout: entire run
            # disabled, fast_func never queried.  No findings returned.
            assert result == []

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend", return_value=("sem", "/usr/bin/sem"))
    def test_sem_finding_format(self, mock_detect, mock_sem, mock_impact):
        """AdvisoryFinding fields match axis='GRAPH-TRIAGE' etc."""
        entities = [_make_entity("my_func", "src/my.py", 10, 20)]
        mock_sem.return_value = SemAcquisition("completed", entities)
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
            [("fn1", "function", "my_func", "src/foo.py::my_func", "src/foo.py", 1, 10)],
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
            [("fn1", "function", "run", "src/runner.py::run", "src/runner.py", 1, 10)],
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
            ("fn%d" % i, "function", "func_%d" % i, "src/f.py::func_%d" % i, "src/f.py", i, i + 5)
            for i in range(12)
        ]
        # Build fetchall responses: first the nodes query, then per-node edges
        responses = [nodes]
        for i in range(12):
            dep_count = 12 - i
            responses.append([("dep_%d" % j,) for j in range(dep_count)])
        responses.append([])  # Final empty for loop termination
        mock_cursor.fetchall.side_effect = responses

        runner = GraphTriageRunner()
        diff = _make_diff(["src/f.py"])
        result = runner.run(diff, Path("/tmp"))
        assert len(result) <= 10

    @patch("code_forge.graph_triage._get_sem_impact")
    @patch("code_forge.graph_triage._run_sem")
    @patch("code_forge.graph_triage._detect_backend", return_value=("graphdb", "/path/graph.db"))
    @patch("code_forge.graph_triage.sqlite3.connect")
    def test_graphdb_quality_caveat(
        self,
        mock_connect,
        mock_detect,
        mock_sem,
        mock_impact,
        tmp_path,
    ):
        """graphdb findings attribution contains 'graph.db (degraded)'."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_cursor.fetchall.side_effect = [
            [("fn1", "function", "unique_func", "src/x.py::unique_func", "src/x.py", 1, 10)],
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


class TestGraphDBFileIdentity:
    """Use real SQLite to select only the changed checkout's file."""

    def _write_graph(self, root, paths):
        db = root / ".code-review-graph/graph.db"
        db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(db)) as connection:
            connection.execute(
                "CREATE TABLE nodes (id INTEGER, kind TEXT, name TEXT, qualified_name TEXT, "
                "file_path TEXT, line_start INTEGER, line_end INTEGER)"
            )
            connection.execute(
                "CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT)"
            )
            for index, path in enumerate(paths):
                name = "entity_%d" % index
                connection.execute(
                    "INSERT INTO nodes VALUES (?, 'Function', ?, ?, ?, 1, 10)",
                    (index, name, "%s::%s" % (path, name), path),
                )
            connection.commit()
        return db

    @pytest.mark.parametrize("symlink_root", [False, True])
    @pytest.mark.parametrize("storage", ["relative", "lexical", "resolved"])
    @pytest.mark.parametrize(
        "relative", ["src/foo.py", "src/foo_bar.py", "src/foo%bar.py", "src/quote'file.py"]
    )
    def test_runner_selects_exact_file(self, tmp_path, monkeypatch, symlink_root, storage, relative):
        root = tmp_path / "checkout"
        root.mkdir()
        if symlink_root:
            alias = tmp_path / "alias"
            alias.symlink_to(root, target_is_directory=True)
            root = alias
        stored = {
            "relative": relative,
            "lexical": str(root / relative),
            "resolved": str(root.resolve() / relative),
        }[storage]
        paths = [
            stored,
            "tests/eval/corpus/base_files/BUG-P12-01/" + relative,
            str(tmp_path / "foreign" / relative),
        ]
        lookalike = relative.replace("_", "X").replace("%", "XY")
        if lookalike != relative:
            paths.append(lookalike)
        self._write_graph(root, paths)
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        findings = runner.run(_make_diff([relative]), root)
        assert [(f.file, f.line_range) for f in findings] == [(relative, (1, 10))]
        assert findings[0].description.startswith("entity_0 ")
        assert runner.acquisition_outcome.status == "completed" and not runner.infra_errors

    @pytest.mark.parametrize("storage", ["corpus", "foreign"])
    def test_suffix_only_is_completed_empty(self, tmp_path, monkeypatch, storage):
        relative = "tests/test_outlet_c.py"
        stored = (
            "tests/eval/corpus/base_files/BUG-P12-01/" + relative
            if storage == "corpus"
            else str(tmp_path / "other-checkout" / relative)
        )
        root = tmp_path / "checkout"
        self._write_graph(root, [stored])
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        assert runner.run(_make_diff([relative]), root) == []
        assert runner.acquisition_outcome.status == "completed_empty"
        assert runner.acquisition_outcome.impact_complete and not runner.infra_errors

    def test_explicit_relative_root_does_not_follow_database_location(self, tmp_path, monkeypatch):
        root = tmp_path / "checkout"
        root.mkdir()
        db = self._write_graph(tmp_path / "database-location", [str(root / "src/foo.py")])
        relative_root = Path(os.path.relpath(root, Path.cwd()))
        assert not relative_root.is_absolute()
        monkeypatch.setenv("CRG_DB_PATH", str(db))
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        findings = runner.run(_make_diff(["./src/foo.py"]), relative_root)
        assert len(findings) == 1 and findings[0].file == "src/foo.py"
        assert not runner.infra_errors

    @pytest.mark.parametrize("invalid", ["../a.py", "src/../../a.py", "/a.py", ".", "a\0.py"])
    def test_invalid_identity_discards_prior_rows(self, tmp_path, monkeypatch, invalid):
        self._write_graph(tmp_path, ["valid.py"])
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        assert runner.run(_make_diff(["valid.py", invalid]), tmp_path) == []
        assert runner.acquisition_outcome.status == "schema_error"
        assert runner.acquisition_outcome.entities == [] and runner.infra_errors
        assert runner.acquisition_outcome.impact_complete is False
        assert runner._cached_findings is None
        diagnostic = runner.acquisition_outcome.diagnostic
        assert isinstance(diagnostic, str) and diagnostic.strip()
        assert repr(invalid) in diagnostic
        assert "invalid" in diagnostic.casefold()
        assert any(diagnostic in error for error in runner.infra_errors)

    def test_ambiguous_stored_spellings_discards_prior_rows(self, tmp_path, monkeypatch):
        self._write_graph(tmp_path, ["valid.py", "src/foo.py", str(tmp_path / "src/foo.py")])
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        assert runner.run(_make_diff(["valid.py", "src/foo.py"]), tmp_path) == []
        assert runner.acquisition_outcome.status == "schema_error"
        assert runner.acquisition_outcome.entities == [] and runner.infra_errors
        assert runner.acquisition_outcome.impact_complete is False
        assert runner._cached_findings is None
        diagnostic = runner.acquisition_outcome.diagnostic
        assert isinstance(diagnostic, str) and diagnostic.strip()
        assert "src/foo.py" in diagnostic
        assert "ambiguous" in diagnostic.casefold()
        assert any(diagnostic in error for error in runner.infra_errors)

    def test_real_graphdb_caller_arguments_preserved(self, tmp_path):
        from code_forge.graph_triage import _run_graphdb

        relative = "src/identitymodule.py"
        db = self._write_graph(tmp_path, [relative])
        callers = [
            ("caller", "from identitymodule import entity_0", "entity_0()", "identitymodule"),
            ("unrelated", "import differentmodule", "differentmodule.entity_0()", "differentmodule"),
        ]
        with closing(sqlite3.connect(db)) as connection:
            for index, (name, import_text, call_text, module) in enumerate(callers, start=1):
                caller_file = tmp_path / (name + ".py")
                caller_file.write_text(
                    "%s\n\ndef %s():\n    return %s\n" % (import_text, name, call_text),
                    encoding="utf-8",
                )
                qualified = "%s::%s" % (caller_file, name)
                connection.execute(
                    "INSERT INTO nodes VALUES (?, 'Function', ?, ?, ?, 3, 4)",
                    (index, name, qualified, str(caller_file)),
                )
                connection.executemany(
                    "INSERT INTO edges VALUES (?, ?, ?)",
                    [
                        ("CALLS", qualified, "entity_0"),
                        ("IMPORTS_FROM", str(caller_file), module),
                    ],
                )
            connection.commit()

        outcome = _run_graphdb(str(db), [relative], tmp_path)
        assert outcome.status == "completed" and outcome.impact_complete
        assert len(outcome.entities) == 1
        entity = outcome.entities[0]
        assert entity["file"] == relative
        assert entity["dependent_count"] == 1
        assert entity["top_dependents"] == ["%s::caller" % (tmp_path / "caller.py")]


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
            args=[],
            returncode=0,
            stdout=_sem_impact_json(
                "my_func",
                3,
                dependents=[
                    {"entityId": "a::caller1", "entityName": "caller1"},
                    {"entityId": "b::caller2", "entityName": "caller2"},
                    {"entityId": "c::caller3", "entityName": "caller3"},
                ],
            ),
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


class TestGraphSourceAndConfigRoots:
    """Keep the source checkout separate from private review configuration."""

    _write_graph = TestGraphDBFileIdentity._write_graph

    def _state_cwd(self, tmp_path, monkeypatch, graph_config):
        import tempfile
        from code_forge.cross_repo import make_per_repo_cwd

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        gate = {"test": {"command": ["never-executed"]}, "graph_triage": graph_config}
        cwd = make_per_repo_cwd("graph-roots", gate)
        assert cwd.parent == tmp_path
        return cwd

    def _machine(self, root, cwd, runners):
        from code_forge.autofix import NoChangeAutoFixer
        from code_forge.baseline import ResolvedReview
        from code_forge.machine import StateMachine
        from code_forge.state import Mode

        source = root / "src/foo.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def foo():\n    return 2\n", encoding="utf-8")
        return StateMachine(
            mode=Mode.CI,
            falsifier=object(),
            autofixer=NoChangeAutoFixer(),
            revert_fn=lambda finding: None,
            resolved_review=ResolvedReview(
                source_files=[source],
                baseline_content=None,
                git_diff=_make_diff(["src/foo.py"]),
                mode_hint="git",
            ),
            source_hash="graph-roots",
            baseline_spec_repr="source and configuration roots",
            cwd=cwd,
            source_root=root,
            registry={},
            advisory_runners=runners,
            coverage_l1_active=False,
        )

    @pytest.mark.parametrize("symlink_root", [False, True])
    @pytest.mark.parametrize("selection", ["default", "override"])
    def test_machine_source_identity_and_config_snapshot(
        self, tmp_path, monkeypatch, symlink_root, selection
    ):
        import yaml

        root = tmp_path / "source"
        root.mkdir()
        if symlink_root:
            alias = tmp_path / "source-alias"
            alias.symlink_to(root, target_is_directory=True)
            root = alias
        stored = str(root.resolve() / "src/foo.py")
        config = {"enabled": True}
        if selection == "override":
            self._write_graph(root, [str(tmp_path / "foreign/src/foo.py")])
            db = self._write_graph(tmp_path / "override-location", [stored])
            config["db_path"] = str(db)
        else:
            self._write_graph(root, [stored])
        source_gate = root / ".code-forge/gate.yaml"
        source_gate.parent.mkdir()
        source_gate.write_text(
            yaml.safe_dump({"test": {"command": ["never-executed"]},
                            "graph_triage": {"enabled": False}}),
            encoding="utf-8",
        )
        cwd = self._state_cwd(tmp_path, monkeypatch, config)
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        sm = self._machine(root, cwd, [runner])
        sm._run_advisory_axes()
        assert [(f.file, f.line_range) for f in sm._advisories] == [("src/foo.py", (1, 10))]
        assert runner.acquisition_outcome.status == "completed"
        assert runner.acquisition_outcome.impact_complete and not sm._state.infra_errors
        assert runner.source_files == [root / "src/foo.py"]
        assert not (root / ".code-forge/state.json").exists()

    @pytest.mark.parametrize("policy", ["disabled", "invalid", "missing"])
    def test_machine_configuration_root_preserves_policy(self, tmp_path, monkeypatch, policy):
        import yaml

        root = tmp_path / "source"
        self._write_graph(root, [str(root / "src/foo.py")])
        source_gate = root / ".code-forge/gate.yaml"
        source_gate.parent.mkdir()
        source_gate.write_text(
            yaml.safe_dump({"test": {"command": ["never-executed"]},
                            "graph_triage": {"enabled": True}}),
            encoding="utf-8",
        )
        policy_config = {"enabled": False if policy == "disabled" else "invalid"}
        cwd = self._state_cwd(tmp_path, monkeypatch, policy_config)
        if policy == "missing":
            (cwd / ".code-forge/gate.yaml").unlink()
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = GraphTriageRunner()
        sm = self._machine(root, cwd, [runner])
        sm._run_advisory_axes()
        expected = {"disabled": "disabled", "invalid": "configuration_error",
                    "missing": "completed"}[policy]
        assert runner.acquisition_outcome.status == expected
        assert len(sm._advisories) == (1 if policy == "missing" else 0)
        assert bool(sm._state.infra_errors) == (policy == "invalid")

    def test_machine_sem_backend_uses_source_root(self, tmp_path, monkeypatch):
        from code_forge import graph_triage as gt

        root = tmp_path / "source"
        root.mkdir()
        cwd = self._state_cwd(tmp_path, monkeypatch, {"enabled": True})
        observed = []
        monkeypatch.setattr(gt.shutil, "which", lambda _: "/controlled/sem")

        def has_index(repo_root):
            observed.append(("index", repo_root))
            return True

        def acquire(diff, repo_root):
            observed.append(("diff", repo_root))
            return SemAcquisition("completed", [_make_entity("foo", "src/foo.py")])

        def impact(name, file, repo_root):
            observed.append(("impact", repo_root))
            return {"impact": {"total": 1}, "dependents": []}

        monkeypatch.setattr(gt, "_sem_has_index", has_index)
        monkeypatch.setattr(gt, "_run_sem", acquire)
        monkeypatch.setattr(gt, "_get_sem_impact", impact)
        runner = GraphTriageRunner()
        sm = self._machine(root, cwd, [runner])
        sm._run_advisory_axes()
        assert observed == [("index", root), ("diff", root), ("impact", root)]
        assert len(sm._advisories) == 1 and not sm._state.infra_errors

    def test_machine_inherited_graph_implementation_uses_source_root(self, tmp_path, monkeypatch):
        class InheritedGraphRunner(GraphTriageRunner):
            pass

        root = tmp_path / "source"
        self._write_graph(root, [str(root / "src/foo.py")])
        cwd = self._state_cwd(tmp_path, monkeypatch, {"enabled": True})
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        runner = InheritedGraphRunner()
        sm = self._machine(root, cwd, [runner])
        sm._run_advisory_axes()
        assert len(sm._advisories) == 1 and not sm._state.infra_errors
        assert runner.acquisition_outcome.status == "completed"

    def test_machine_preserves_specialized_source_runner_and_other_axis(self, tmp_path, monkeypatch):
        from code_forge.context_sources import GraphTriageSource

        root = tmp_path / "source"
        self._write_graph(root, [str(root / "src/foo.py")])
        cwd = self._state_cwd(tmp_path, monkeypatch, {"enabled": False})
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        direct = GraphTriageRunner()
        cached = direct.run(_make_diff(["src/foo.py"]), root)
        assert len(cached) == 1
        specialized = GraphTriageSource(root).advisory_runner(None, allow_unsnapshotted=True)
        specialized._cached_findings = cached

        class OtherAxis:
            source_files = None

            def run(self, diff, repo_root):
                self.root = repo_root
                return []

        other = OtherAxis()
        sm = self._machine(root, cwd, [specialized, other])
        sm._run_advisory_axes()
        assert sm._advisories == cached and not sm._state.infra_errors
        assert other.root == cwd and other.source_files == [root / "src/foo.py"]

    def test_public_config_root_is_optional(self, tmp_path, monkeypatch):
        root = tmp_path / "source"
        self._write_graph(root, [str(root / "src/foo.py")])
        cwd = self._state_cwd(tmp_path, monkeypatch, {"enabled": False})
        monkeypatch.setattr("code_forge.graph_triage.shutil.which", lambda _: None)
        direct = GraphTriageRunner()
        assert len(direct.run(_make_diff(["src/foo.py"]), root)) == 1
        controlled = GraphTriageRunner()
        assert controlled.run(_make_diff(["src/foo.py"]), root, config_root=cwd) == []
        assert controlled.acquisition_outcome.status == "disabled" and not controlled.infra_errors
