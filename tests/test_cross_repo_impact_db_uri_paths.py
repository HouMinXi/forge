# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Real SQLite regression coverage for both public cross-repo readers.

Import directly: these readers do not require the optional graph provider.
All databases and live C sources are owned fixtures. No liveness, SQL, or
URI helper is stubbed. Source stays ASCII; filenames exercise Unicode.
"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote_from_bytes

import pytest

from code_forge import cross_repo_impact as impact


_CONNECT = sqlite3.connect
_READERS = ("primary", "sibling")
_FILE = "src/api.c"
_DIFF = "--- a/src/api.c\n+++ b/src/api.c\n@@ -1 +1 @@\n-old\n+new\n"
_CHANGED = [{"name": "api_target", "qualified_name": "api::api_target",
             "file_path": _FILE, "module": "api"}]
_WINDOWS_QUESTION = pytest.mark.skipif(
    os.name == "nt", reason="Windows filenames cannot contain '?'"
)
_DDL = (
    "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
    "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
    "CREATE TABLE edges(kind TEXT, source_qualified TEXT, target_qualified TEXT);"
)


def _make_db(path, reader, identity="intended", *, rows=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = []
    with closing(_CONNECT(path)) as conn:
        conn.executescript(_DDL)
        if rows and reader == "primary":
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (1, "function", identity, "api::" + identity, _FILE, 1, 3))
            expected = [{"name": identity, "qualified_name": "api::" + identity,
                         "file_path": _FILE, "module": "api"}]
        elif rows:
            source = path.parent / (identity + ".c")
            source.write_text("int " + identity + "(void) {\n    return api_target();\n}\n",
                              encoding="utf-8")
            qualified = identity + "::caller"
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (1, "function", identity, qualified, str(source), 2, 3))
            conn.executemany("INSERT INTO edges VALUES (?, ?, ?)",
                             [("CALLS", qualified, "api_target"),
                              ("IMPORTS_FROM", qualified, "api")])
            expected = [{"symbol": "api_target", "caller_qualified": qualified,
                         "caller_file": str(source), "caller_line": 2}]
        conn.commit()
    return expected


def _invoke(reader, path):
    if reader == "primary":
        return impact.resolve_changed_symbols(_DIFF, str(path))
    return impact.find_cross_repo_callers(str(path), _CHANGED)


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def _literal_uri(path):
    """Independent full lexical expectation, never using readonly_uri/as_uri."""
    lexical = path.absolute().as_posix()
    if os.name == "nt" and not lexical.startswith("/"):
        lexical = "/" + lexical
    safe = "/:" if os.name == "nt" else "/"
    return "file://" + quote_from_bytes(os.fsencode(lexical), safe=safe) + "?mode=ro"


@pytest.fixture
def observe_connect(monkeypatch):
    opened = []
    attempted = []

    def connect(database, *args, **kwargs):
        attempted.append((database, kwargs.copy()))
        conn = _CONNECT(database, *args, **kwargs)
        opened.append({"connection": conn, "database": database,
                       "uri": kwargs.get("uri"),
                       "actual": conn.execute("PRAGMA database_list").fetchone()[2]})
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("CREATE TABLE forbidden_write(value INTEGER)")
        except BaseException:
            conn.close()
            raise
        return conn

    monkeypatch.setattr(impact.sqlite3, "connect", connect)
    yield opened, attempted
    # Clean up even when a deliberately broken implementation fails an assertion.
    for record in opened:
        record["connection"].close()


def _assert_connection(observed, expected_path):
    opened, attempted = observed
    assert len(attempted) == len(opened) == 1
    record = opened[0]
    assert record["uri"] is True
    assert record["database"] == _literal_uri(expected_path)
    assert Path(record["actual"]).samefile(expected_path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        record["connection"].execute("SELECT 1")


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize("name", [
    "graph.db", "percent%/graph%.db", "space and \u6570\u636e/graph \u56fe.db",
    "graph#one.db", "directory#one/graph.db",
    pytest.param("graph?one.db", marks=_WINDOWS_QUESTION),
    pytest.param("directory?one/graph.db", marks=_WINDOWS_QUESTION),
], ids=["plain", "literal-percent", "space-unicode", "hash-file", "hash-directory",
        "question-file", "question-directory"])
def test_literal_database_paths(reader, name, tmp_path, observe_connect):
    db = tmp_path / name
    expected = _make_db(db, reader)
    before = _files(tmp_path)

    assert _invoke(reader, db) == expected

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize("literal,decoded", [
    ("%23root/graph.db", "#root/graph.db"),
    ("graph%23.db", "graph#.db"),
    pytest.param("%3Froot/graph.db", "?root/graph.db", marks=_WINDOWS_QUESTION),
    pytest.param("graph%3F.db", "graph?.db", marks=_WINDOWS_QUESTION),
    ("graph%25.db", "graph%.db"),
    ("graph%2523.db", "graph%23.db"),
], ids=["hash-directory", "hash-file", "question-directory", "question-file",
        "percent-file", "double-escape-file"])
def test_percent_escape_does_not_select_decoy(reader, literal, decoded, tmp_path, observe_connect):
    db = tmp_path / literal
    decoy = tmp_path / decoded
    expected = _make_db(db, reader)
    other = _make_db(decoy, reader, "decoded_decoy")
    assert expected != other
    before = _files(tmp_path)

    assert _invoke(reader, db) == expected

    _assert_connection(observe_connect, db)
    assert not Path(observe_connect[0][0]["actual"]).samefile(decoy)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reader", _READERS)
def test_relative_path_uses_cwd_with_root_and_decoded_decoys(reader, tmp_path,
                                                            monkeypatch, observe_connect):
    root = tmp_path / "repository"
    cwd = tmp_path / "working"
    relative = Path("graphs%23/graph.db")
    db = cwd / relative
    expected = _make_db(db, reader)
    root_rows = _make_db(root / relative, reader, "root_decoy")
    decoded_rows = _make_db(cwd / "graphs#" / "graph.db", reader, "decoded_decoy")
    assert expected != root_rows != decoded_rows
    before = _files(tmp_path)
    monkeypatch.chdir(cwd)

    assert _invoke(reader, relative) == expected

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


def _symlink(alias, target, *, directory=False):
    try:
        alias.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege")
        raise


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize("kind", ["final-component", "parent-directory"])
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_symlink_preserves_full_lexical_uri(reader, kind, relative, tmp_path,
                                           monkeypatch, observe_connect):
    target = tmp_path / "storage" / "target.db"
    expected = _make_db(target, reader, "target_sentinel")
    if kind == "final-component":
        alias = tmp_path / "linked%23 \u56fe.db"
        decoy = tmp_path / "linked# \u56fe.db"
        _symlink(alias, target)
        link = alias
    else:
        link = tmp_path / "linked%23 \u56fe"
        _symlink(link, target.parent, directory=True)
        alias = link / target.name
        decoy = tmp_path / "linked# \u56fe" / target.name
    decoy_rows = _make_db(decoy, reader, "alias_decoy_sentinel")
    assert expected != decoy_rows
    before = _files(tmp_path)
    monkeypatch.chdir(tmp_path)
    supplied = alias.relative_to(tmp_path) if relative else alias
    expected_uri = _literal_uri(alias)
    suffix = ("/linked%2523%20%E5%9B%BE.db?mode=ro" if kind == "final-component"
              else "/linked%2523%20%E5%9B%BE/target.db?mode=ro")
    observed_connect = impact.sqlite3.connect

    def check_lexical_uri(database, *args, **kwargs):
        assert database == expected_uri
        assert database.endswith(suffix)
        return observed_connect(database, *args, **kwargs)

    monkeypatch.setattr(impact.sqlite3, "connect", check_lexical_uri)

    assert _invoke(reader, supplied) == expected

    assert link.is_symlink()
    assert alias.samefile(target)
    # Check the actual connect argument, including its lexical parent and basename.
    _assert_connection(observe_connect, alias)
    assert _literal_uri(alias) != _literal_uri(target)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reader", _READERS)
def test_no_rows_still_closes_readonly_connection(reader, tmp_path, observe_connect):
    db = tmp_path / "empty%23.db"
    _make_db(db, reader, rows=False)
    before = _files(tmp_path)

    assert _invoke(reader, db) == []

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize("parent_missing", [False, True], ids=["missing-file", "missing-parent"])
def test_missing_database_does_not_create_anything(reader, parent_missing, tmp_path, observe_connect):
    db = tmp_path / "missing%23.db"
    if parent_missing:
        db = tmp_path / "absent%23" / db.name
    before = _files(tmp_path)

    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        _invoke(reader, db)

    opened, attempted = observe_connect
    assert opened == []
    assert attempted == [(_literal_uri(db), {"uri": True})]
    assert not db.exists()
    if parent_missing:
        assert not db.parent.exists()
    assert _files(tmp_path) == before


@pytest.mark.parametrize("reader", _READERS)
def test_query_error_closes_acquired_connection(reader, tmp_path, observe_connect):
    db = tmp_path / "missing-tables%23.db"
    with closing(_CONNECT(db)) as conn:
        # A valid database without the required tables, not a connect-time error.
        conn.execute("CREATE TABLE unrelated(value INTEGER)")
        conn.commit()
    before = _files(tmp_path)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _invoke(reader, db)

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("diff", ["", " \n", "no diff paths", "--- a/src/api.c\n+++ /dev/null\n"])
def test_primary_without_parsed_paths_never_connects(diff, tmp_path, observe_connect):
    missing = tmp_path / "absent" / "graph%23.db"

    assert impact.resolve_changed_symbols(diff, str(missing)) == []

    assert observe_connect == ([], [])
    assert not missing.parent.exists()


def test_sibling_empty_changed_still_opens_and_closes(tmp_path, observe_connect):
    db = tmp_path / "graph%23.db"
    _make_db(db, "sibling")
    before = _files(tmp_path)

    assert impact.find_cross_repo_callers(str(db), []) == []

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


def test_primary_keeps_suffix_matching_and_unnamed_filter(tmp_path, observe_connect):
    db = tmp_path / "graph%23.db"
    _make_db(db, "primary", rows=False)
    stored = (tmp_path / _FILE).as_posix()
    with closing(_CONNECT(db)) as conn:
        conn.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (1, "function", "named", "api::named", stored, 1, 3),
            (2, "module", "module-level", "api::module-level", stored, 1, 4),
            (3, "block", "lines 2-3", "api::lines", stored, 2, 3),
            (4, "function", "unrelated", "other::unrelated", "src/other.c", 1, 3),
        ])
        conn.commit()
    before = _files(tmp_path)

    assert impact.resolve_changed_symbols(_DIFF, str(db)) == [
        {"name": "named", "qualified_name": "api::named", "file_path": stored, "module": "api"}
    ]

    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before


def test_sibling_keeps_disambiguation_deduplication_and_real_c_liveness(tmp_path, observe_connect):
    db = tmp_path / "graph%23.db"
    _make_db(db, "sibling", rows=False)
    cases = [
        ("module_match", "api_target", "package.api.bridge", False),
        ("symbol_match", "api_target", "prefix_api_target_suffix", False),
        ("wrong_import", "api_target", "elsewhere", False),
        ("no_import", "api_target", None, False),
        ("wrong_target", "another_target", "api", False),
        ("dead", "api_target", "api", True),
    ]
    expected = []
    with closing(_CONNECT(db)) as conn:
        for index, (name, target, imported, dead) in enumerate(cases, 1):
            source = tmp_path / (name + ".c")
            prefix = "#if 0\n" if dead else ""
            source.write_text(prefix + "int caller(void) {\n    return api_target();\n}\n"
                              + ("#endif\n" if dead else ""), encoding="utf-8")
            line = 3 if dead else 2
            qualified = name + "::caller"
            conn.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (index, "function", name, qualified, str(source), line, line + 1))
            # Duplicate calls must still produce one row for this caller.
            conn.executemany("INSERT INTO edges VALUES (?, ?, ?)",
                             [("CALLS", qualified, target)] * 2)
            if imported is not None:
                conn.execute("INSERT INTO edges VALUES (?, ?, ?)",
                             ("IMPORTS_FROM", name + "::other_import_site", imported))
            if name in {"module_match", "symbol_match"}:
                expected.append({"symbol": "api_target", "caller_qualified": qualified,
                                 "caller_file": str(source), "caller_line": line})
        conn.commit()
    before = _files(tmp_path)

    result = impact.find_cross_repo_callers(str(db), _CHANGED)

    assert sorted(result, key=lambda row: row["caller_qualified"]) == expected
    _assert_connection(observe_connect, db)
    assert _files(tmp_path) == before
