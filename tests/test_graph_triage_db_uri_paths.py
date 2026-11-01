# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Exercise literal database paths and read-only access with real SQLite."""

from contextlib import closing
from pathlib import Path
import os
import sqlite3

import pytest

from code_forge import graph_triage


_CONNECT = sqlite3.connect
_FILE = "src/subject.py"
_READERS = ["triage", "dependents"]
_WINDOWS_INVALID = pytest.mark.skipif(os.name == "nt", reason="Windows filenames cannot contain '?'")


@pytest.fixture(autouse=True)
def _graphdb_only(monkeypatch):
    monkeypatch.setattr(graph_triage.shutil, "which", lambda _: None)
    monkeypatch.delenv("CRG_DB_PATH", raising=False)


def _write_graph(db, identity="intended", stored_file=_FILE):
    db.parent.mkdir(parents=True, exist_ok=True)
    caller_file = db.parent / (identity + "_caller.py")
    caller_file.write_text("def caller():\n    return target()\n", encoding="utf-8")
    caller = str(caller_file) + "::caller"
    with closing(_CONNECT(db)) as conn:
        conn.executescript(
            "CREATE TABLE nodes (id INTEGER, kind TEXT, name TEXT, qualified_name TEXT, "
            "file_path TEXT, line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT);"
        )
        conn.executemany(
            "INSERT INTO nodes VALUES (?, 'Function', ?, ?, ?, ?, ?)",
            [
                (1, "target", identity + "::target", stored_file, 3, 7),
                (2, "caller", caller, str(caller_file), 1, 2),
            ],
        )
        conn.executemany(
            "INSERT INTO edges VALUES (?, ?, ?)",
            [("CALLS", caller, "target"), ("IMPORTS_FROM", str(caller_file), "subject")],
        )
        conn.commit()
    return caller


def _query(reader, db, root, monkeypatch):
    if reader == "triage":
        return graph_triage._run_graphdb(str(db), [_FILE], root)
    monkeypatch.setenv("CRG_DB_PATH", str(db))
    return graph_triage.find_entity_dependents("target", _FILE, root)


def _assert_intended(reader, result, caller):
    if reader == "dependents":
        assert result == [caller]
        return
    assert result.status == "completed", result.diagnostic
    assert result.impact_complete is True
    assert result.entities == [
        {
            "name": "target",
            "file": _FILE,
            "qualified_name": "intended::target",
            "dependent_count": 1,
            "top_dependents": [caller],
            "start_line": 3,
            "end_line": 7,
        }
    ]


def _files_under(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize(
    "relative_db",
    [
        "graph.db",
        "space and \u6570\u636e/graph \u56fe.db",
        "graph#one.db",
        pytest.param("graph?one.db", marks=_WINDOWS_INVALID),
        "directory#one/graph.db",
        pytest.param("directory?one/graph.db", marks=_WINDOWS_INVALID),
    ],
    ids=["plain", "space-unicode", "hash-file", "query-file", "hash-directory", "query-directory"],
)
def test_literal_database_path(reader, relative_db, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    db = tmp_path / "databases" / relative_db
    caller = _write_graph(db)
    before = _files_under(tmp_path)

    result = _query(reader, db, root, monkeypatch)

    assert _files_under(tmp_path) == before
    _assert_intended(reader, result, caller)


@pytest.mark.parametrize("reader", _READERS)
def test_percent_escape_reads_intended_graph_not_valid_decoy(reader, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    intended = tmp_path / "graph%23one.db"
    caller = _write_graph(intended)
    decoy = tmp_path / "graph#one.db"
    decoy_caller = _write_graph(decoy, identity="decoy")
    assert caller != decoy_caller
    before = _files_under(tmp_path)

    result = _query(reader, intended, root, monkeypatch)

    assert _files_under(tmp_path) == before
    _assert_intended(reader, result, caller)


@pytest.mark.parametrize("reader", _READERS)
def test_relative_database_uses_cwd_not_repository_root(reader, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    cwd = tmp_path / "elsewhere"
    db = cwd / "graphs" / "graph%23one.db"
    caller = _write_graph(db, stored_file=str(root / _FILE))
    _write_graph(root / "graphs" / "graph%23one.db", identity="decoy")
    before = _files_under(tmp_path)
    monkeypatch.chdir(cwd)

    result = _query(reader, Path("graphs/graph%23one.db"), root, monkeypatch)

    assert _files_under(tmp_path) == before
    _assert_intended(reader, result, caller)


@pytest.mark.parametrize("reader", _READERS)
def test_symlink_database_path(reader, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    target = tmp_path / "storage" / "graph.db"
    caller = _write_graph(target)
    alias = tmp_path / "linked%23graph.db"
    try:
        alias.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege")
        raise
    opened = []

    def connect(database, *args, **kwargs):
        assert database.endswith("/linked%2523graph.db?mode=ro")
        con = _CONNECT(database, *args, **kwargs)
        opened.append(con)
        return con

    monkeypatch.setattr(graph_triage.sqlite3, "connect", connect)
    before = _files_under(tmp_path)

    result = _query(reader, alias, root, monkeypatch)

    assert alias.is_symlink()
    assert _files_under(tmp_path) == before
    _assert_intended(reader, result, caller)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


@pytest.mark.parametrize("reader", _READERS)
def test_connections_are_read_only_and_closed(reader, tmp_path, monkeypatch):
    db = tmp_path / "graph.db"
    caller = _write_graph(db)
    opened = []
    helper_paths = []
    actual_uri = graph_triage.readonly_uri
    before = db.read_bytes()

    def observe_uri(path):
        helper_paths.append(path)
        return actual_uri(path)

    def connect(*args, **kwargs):
        conn = _CONNECT(*args, **kwargs)
        verified = False
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("CREATE TABLE forbidden_write (value INTEGER)")
            verified = True
        finally:
            if not verified:
                conn.close()
        opened.append(conn)
        return conn

    monkeypatch.setattr(graph_triage, "readonly_uri", observe_uri)
    monkeypatch.setattr(graph_triage.sqlite3, "connect", connect)
    result = _query(reader, db, tmp_path, monkeypatch)

    _assert_intended(reader, result, caller)
    assert helper_paths == [db.absolute()]
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    "filename",
    [
        "missing.db",
        "missing#graph.db",
        pytest.param("missing?graph.db", marks=_WINDOWS_INVALID),
        "missing%23graph.db",
    ],
)
def test_missing_database_is_execution_error_without_creation(filename, tmp_path):
    missing = tmp_path / filename

    result = graph_triage._run_graphdb(str(missing), [_FILE], tmp_path)

    assert result.status == "execution_error"
    assert result.entities == []
    assert result.impact_complete is False
    assert result.diagnostic
    assert _files_under(tmp_path) == {}


@pytest.mark.parametrize(
    "root_name",
    [
        "checkout#with%23characters",
        pytest.param("checkout#with?literal%23characters", marks=_WINDOWS_INVALID),
    ],
)
def test_dependents_default_database_precedes_environment(root_name, tmp_path, monkeypatch):
    root = tmp_path / root_name
    caller = _write_graph(root / ".code-review-graph" / "graph.db")
    env_db = tmp_path / "environment.db"
    _write_graph(env_db, identity="decoy")
    monkeypatch.setenv("CRG_DB_PATH", str(env_db))
    before = _files_under(tmp_path)

    result = graph_triage.find_entity_dependents("target", _FILE, root)

    assert _files_under(tmp_path) == before
    assert result == [caller]


@pytest.mark.parametrize("environment", ["unset", "missing", "invalid"])
def test_dependents_unavailable_database_returns_empty(environment, tmp_path, monkeypatch):
    db = tmp_path / "graph%23one.db"
    if environment != "unset":
        monkeypatch.setenv("CRG_DB_PATH", str(db))
    if environment == "invalid":
        db.write_bytes(b"not a SQLite database")
    before = _files_under(tmp_path)

    assert graph_triage.find_entity_dependents("target", _FILE, tmp_path) == []

    assert _files_under(tmp_path) == before


def test_dependents_database_removed_after_selection_is_not_recreated(tmp_path, monkeypatch):
    db = tmp_path / "graph.db"
    _write_graph(db)
    monkeypatch.setenv("CRG_DB_PATH", str(db))
    attempts = []

    def connect(*args, **kwargs):
        attempts.append(args[0])
        db.unlink()
        return _CONNECT(*args, **kwargs)

    monkeypatch.setattr(graph_triage.sqlite3, "connect", connect)

    assert graph_triage.find_entity_dependents("target", _FILE, tmp_path) == []

    assert len(attempts) == 1
    assert not db.exists()
