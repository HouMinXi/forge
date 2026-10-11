# SPDX-License-Identifier: Apache-2.0
"""Real SQLite snapshot identity must match graph acquisition's literal path."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from code_forge import context_sources, graph_triage


_CONNECT = sqlite3.connect
_HEAD = "a" * 40
_STALE = "b" * 40
_WINDOWS_INVALID = pytest.mark.skipif(os.name == "nt", reason="Windows filenames cannot contain '?'")
_DIFF = (
    "diff --git a/subject.py b/subject.py\n"
    "--- a/subject.py\n+++ b/subject.py\n@@ -1,2 +1,2 @@\n"
    " def intended():\n-    return 0\n+    return 1\n"
)
_LITERAL_PATHS = [
    "graph.db",
    "space and \u6570\u636e/graph \u56fe.db",
    "graph#one.db",
    pytest.param("graph?one.db", marks=_WINDOWS_INVALID),
    "directory#one/graph.db",
    pytest.param("directory?one/graph.db", marks=_WINDOWS_INVALID),
    "graph%23one.db",
]
_PATH_IDS = [
    "plain", "space-unicode", "hash-file", "query-file", "hash-directory",
    "query-directory", "percent-file",
]


@pytest.fixture(autouse=True)
def _isolate_graphdb(monkeypatch):
    actual_which = graph_triage.shutil.which
    monkeypatch.setattr(
        graph_triage.shutil, "which",
        lambda name: None if name == "sem" else actual_which(name),
    )
    monkeypatch.delenv("CRG_DB_PATH", raising=False)


def _write_graph(db, snapshot=_HEAD, entity="intended"):
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(_CONNECT(db)) as con:
        con.executescript(
            "CREATE TABLE metadata (key TEXT, value TEXT);"
            "CREATE TABLE nodes (id INTEGER, kind TEXT, name TEXT, qualified_name TEXT, "
            "file_path TEXT, line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT);"
        )
        con.execute("INSERT INTO metadata VALUES ('git_head_sha', ?)", (snapshot,))
        con.execute(
            "INSERT INTO nodes VALUES (1, 'Function', ?, ?, 'subject.py', 1, 2)",
            (entity, entity + "::subject"),
        )
        con.commit()


def _files_under(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _gather(root, db, monkeypatch, head_sha=_HEAD, allow_unsnapshotted=False):
    monkeypatch.setenv("CRG_DB_PATH", str(db))
    source = context_sources.GraphTriageSource(root)
    result = context_sources.gather(
        [source], ["subject.py"], _DIFF, head_sha=head_sha,
        allow_unsnapshotted=allow_unsnapshotted,
    )
    return source, result


def _assert_admitted(source, result, snapshot=_HEAD):
    assert result.errors == []
    assert result.skipped == []
    assert result.skipped_sources == []
    assert result.refused_sources == []
    assert result.snapshot_shas == {"graph_triage": snapshot}
    assert result.rows == [context_sources.FactRow(
        entity="intended", file="subject.py", downstream="0", dependents="",
        source="graph_triage", origin_line=1,
    )]
    assert source.acquisition_outcome is not None and source.acquisition_outcome.completed
    assert len(source.findings_cache) == 1


def _assert_skipped(source, result, snapshot, head_sha):
    # Assert rows first: the published two-site repair wrongly admits stale facts.
    assert result.rows == []
    assert result.errors == []
    assert result.refused_sources == []
    assert result.snapshot_shas == {"graph_triage": snapshot}
    assert result.skipped_sources == ["graph_triage"]
    assert result.skipped == [
        "graph_triage: index at %s, review head %s"
        % (snapshot[:12], (head_sha or "unknown")[:12])
    ]
    assert source.acquisition_outcome is None
    assert source.findings_cache is None


@pytest.mark.parametrize("relative_db", _LITERAL_PATHS, ids=_PATH_IDS)
@pytest.mark.parametrize("review_head", [_HEAD, _STALE, None], ids=["matching", "stale", "unknown"])
def test_literal_snapshot_gates_real_graph_facts(relative_db, review_head, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    db = tmp_path / "databases" / relative_db
    _write_graph(db)
    before = _files_under(tmp_path)

    source, result = _gather(root, db, monkeypatch, head_sha=review_head)

    if review_head == _HEAD:
        _assert_admitted(source, result)
    else:
        _assert_skipped(source, result, _HEAD, review_head)
    assert _files_under(tmp_path) == before


@pytest.mark.parametrize(
    ("literal", "decoded"),
    [("graph%23one.db", "graph#one.db"),
     pytest.param("graph%3Fone.db", "graph?one.db", marks=_WINDOWS_INVALID),
     ("graph%2523one.db", "graph%23one.db")],
    ids=["hash-decoy", "query-decoy", "percent-decoy"],
)
@pytest.mark.parametrize("snapshot", [_HEAD, _STALE], ids=["matching-intended", "stale-intended"])
def test_snapshot_comes_from_literal_database_not_valid_decoy(
    literal, decoded, snapshot, tmp_path, monkeypatch,
):
    root = tmp_path / "checkout"
    root.mkdir()
    intended = tmp_path / literal
    _write_graph(intended, snapshot=snapshot)
    _write_graph(tmp_path / decoded, snapshot=_STALE if snapshot == _HEAD else _HEAD, entity="decoy")
    before = _files_under(tmp_path)

    source, result = _gather(root, intended, monkeypatch)

    if snapshot == _HEAD:
        _assert_admitted(source, result)
    else:
        _assert_skipped(source, result, _STALE, _HEAD)
    assert _files_under(tmp_path) == before


def test_relative_snapshot_and_acquisition_use_cwd_not_repo_root(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    cwd = tmp_path / "elsewhere"
    relative = Path("graphs/graph%23one.db")
    _write_graph(cwd / relative)
    _write_graph(root / relative, snapshot=_STALE, entity="decoy")
    before = _files_under(tmp_path)
    monkeypatch.chdir(cwd)

    source, result = _gather(root, relative, monkeypatch)

    _assert_admitted(source, result)
    assert _files_under(tmp_path) == before


def test_symlink_snapshot_keeps_literal_alias_and_closes_connections(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    target = tmp_path / "storage" / "graph.db"
    _write_graph(target)
    alias = tmp_path / "linked%23graph.db"
    try:
        alias.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege")
        raise
    opened = []

    def connect(database, *args, **kwargs):
        assert database == context_sources.readonly_uri(alias.absolute())
        assert database.endswith("/linked%2523graph.db?mode=ro")
        con = _CONNECT(database, *args, **kwargs)
        opened.append(con)
        return con

    before = _files_under(tmp_path)
    monkeypatch.setattr(context_sources.sqlite3, "connect", connect)

    source, result = _gather(root, alias, monkeypatch)

    _assert_admitted(source, result)
    assert alias.is_symlink()
    assert len(opened) == 2
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")
    assert _files_under(tmp_path) == before


@pytest.mark.parametrize("root_name", ["checkout#with%23characters",
                                     pytest.param("checkout#with?literal%23characters", marks=_WINDOWS_INVALID)])
def test_default_database_snapshot_precedes_environment(root_name, tmp_path, monkeypatch):
    root = tmp_path / root_name
    _write_graph(root / ".code-review-graph" / "graph.db")
    env_db = tmp_path / "environment.db"
    _write_graph(env_db, snapshot=_STALE, entity="decoy")
    before = _files_under(tmp_path)

    source, result = _gather(root, env_db, monkeypatch)

    _assert_admitted(source, result)
    assert _files_under(tmp_path) == before


def test_explicit_stale_override_keeps_true_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    db = tmp_path / "graph#one.db"
    _write_graph(db, snapshot=_STALE)
    before = _files_under(tmp_path)

    source, result = _gather(root, db, monkeypatch, allow_unsnapshotted=True)

    _assert_admitted(source, result, snapshot=_STALE)
    assert _files_under(tmp_path) == before


@pytest.mark.parametrize("relative_db", _LITERAL_PATHS, ids=_PATH_IDS)
def test_snapshot_connection_is_read_only_and_closed(relative_db, tmp_path, monkeypatch):
    db = tmp_path / relative_db
    _write_graph(db)
    before = _files_under(tmp_path)
    opened = []
    helper_paths = []
    actual_uri = context_sources.readonly_uri

    def observe_uri(path):
        helper_paths.append(path)
        return actual_uri(path)

    def connect(*args, **kwargs):
        con = _CONNECT(*args, **kwargs)
        verified = False
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                con.execute("CREATE TABLE forbidden_write (value INTEGER)")
            verified = True
        finally:
            if not verified:
                con.close()
        opened.append(con)
        return con

    monkeypatch.setattr(context_sources, "readonly_uri", observe_uri)
    monkeypatch.setattr(context_sources.sqlite3, "connect", connect)

    assert context_sources._graphdb_head_sha(db) == _HEAD

    assert helper_paths == [db.absolute()]
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    assert _files_under(tmp_path) == before


@pytest.mark.parametrize("filename", ["missing.db", "missing#graph.db",
                                     pytest.param("missing?graph.db", marks=_WINDOWS_INVALID),
                                     "missing%23graph.db"])
def test_missing_snapshot_returns_none_without_creating_files(filename, tmp_path):
    assert context_sources._graphdb_head_sha(tmp_path / filename) is None
    assert _files_under(tmp_path) == {}


@pytest.mark.parametrize("metadata", ["corrupt", "missing-table", "missing-column", "missing-key", "empty", "null"])
def test_unavailable_metadata_returns_none_and_closes_connection(metadata, tmp_path, monkeypatch):
    db = tmp_path / "graph%23one.db"
    if metadata == "corrupt":
        db.write_bytes(b"not a SQLite database")
    else:
        with closing(_CONNECT(db)) as con:
            if metadata == "missing-table":
                con.execute("CREATE TABLE unrelated (value TEXT)")
            elif metadata == "missing-column":
                con.execute("CREATE TABLE metadata (key TEXT, other TEXT)")
            else:
                con.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
                if metadata != "missing-key":
                    con.execute("INSERT INTO metadata VALUES ('git_head_sha', ?)",
                                ("" if metadata == "empty" else None,))
            con.commit()
    opened = []

    def connect(*args, **kwargs):
        con = _CONNECT(*args, **kwargs)
        opened.append(con)
        return con

    before = _files_under(tmp_path)
    monkeypatch.setattr(context_sources.sqlite3, "connect", connect)

    assert context_sources._graphdb_head_sha(db) is None

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    assert _files_under(tmp_path) == before
