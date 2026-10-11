# SPDX-License-Identifier: Apache-2.0
"""URI representations and local SQLite parsing, not Windows UNC execution."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

import pytest

from code_forge._sqlite_uri import _readonly_uri_from_path_uri, readonly_uri


@pytest.mark.parametrize(
    ("generated_uri", "expected"),
    [
        ("file:///tmp/graph.db", "file:///tmp/graph.db?mode=ro"),
        ("file:////server/share/graph.db", "file:////server/share/graph.db?mode=ro"),
        ("file:///tmp/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db",
         "file:///tmp/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db?mode=ro"),
        ("file:///C:/graphs/graph.db", "file:///C:/graphs/graph.db?mode=ro"),
        ("file:///C:/graphs/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db",
         "file:///C:/graphs/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db?mode=ro"),
        ("file://Server/share/graph.db", "file:////Server/share/graph.db?mode=ro"),
        ("file://localhost/share/graph.db",
         "file:////localhost/share/graph.db?mode=ro"),
        ("file://LOCALHOST/share/graph.db",
         "file:////LOCALHOST/share/graph.db?mode=ro"),
        ("file://Server/share/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db",
         "file:////Server/share/graph%23%3F%2523%20x%E6%95%B0%E6%8D%AE.db?mode=ro"),
        ("file://Server/share%2523/graph%20%23.db",
         "file:////Server/share%2523/graph%20%23.db?mode=ro"),
        ("file://%3F/UNC/server/share/graph.db",
         "file:////%3F/UNC/server/share/graph.db?mode=ro"),
    ],
    ids=["posix", "posix-double-slash", "posix-escaped", "windows-drive",
         "windows-drive-escaped", "unc", "unc-localhost", "unc-localhost-case",
         "unc-escaped", "unc-share-escaped", "windows-extended-prefix"],
)
def test_absolute_path_representation(generated_uri, expected):
    # Explicit pathlib-generated representations keep other-platform
    # coverage without calling deprecated PurePath.as_uri() on this host.
    uri = _readonly_uri_from_path_uri(generated_uri)

    assert uri == expected
    parsed = urlsplit(uri)
    assert parsed.scheme == "file"
    assert parsed.netloc == ""
    assert parsed.query == "mode=ro"
    assert parsed.fragment == ""


@pytest.mark.parametrize("path", [Path("graph.db"), Path("graphs/../graph.db")])
def test_relative_paths_are_rejected(path):
    with pytest.raises(ValueError):
        readonly_uri(path)


@pytest.mark.parametrize("value", [
    "file://server/share/graph.db", "/tmp/graph.db",
    PurePosixPath("/tmp/graph.db"), PurePosixPath("graph.db"),
    PureWindowsPath("C:/graph.db"), PureWindowsPath("graph.db"),
    PureWindowsPath("C:graph.db"), PureWindowsPath("/graph.db"),
])
def test_public_helper_requires_concrete_path(value):
    with pytest.raises(TypeError, match="filesystem path"):
        readonly_uri(value)


@pytest.mark.parametrize("uri", [
    "https://server/graph.db", "file:relative.db", "file://server",
    "file:///tmp/graph.db?mode=rw", "file:///tmp/graph.db#fragment",
])
def test_private_formatter_requires_generated_absolute_file_uri(uri):
    with pytest.raises(ValueError, match="Path.as_uri"):
        _readonly_uri_from_path_uri(uri)


def test_public_helper_encodes_concrete_path(tmp_path):
    db = tmp_path / "graph%23 # \u6570\u636e.db"

    uri = readonly_uri(db)

    assert uri.endswith("/graph%2523%20%23%20%E6%95%B0%E6%8D%AE.db?mode=ro")
    assert urlsplit(uri).netloc == ""
    assert urlsplit(uri).fragment == ""


def _write_database(db, value):
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE witness (value TEXT)")
        con.execute("INSERT INTO witness VALUES (?)", (value,))
        con.commit()


@pytest.mark.skipif(os.name == "nt", reason="local POSIX parser fixture; no Windows UNC share")
def test_unc_representation_reads_owned_double_slash_path_read_only(tmp_path):
    db = tmp_path / "graph%23 #?.db"
    _write_database(db, "intended")
    # Build the UNC representation from concrete pathlib's encoded bytes.
    # On POSIX, //tmp/... names this owned file, not a network share.
    raw_uri = "file://" + db.as_uri().removeprefix("file:///")
    assert urlsplit(raw_uri).netloc == db.parts[1]
    before = db.read_bytes()
    with closing(sqlite3.connect(":memory:")) as con:
        authority_enabled = con.execute(
            "SELECT sqlite_compileoption_used('ALLOW_URI_AUTHORITY')"
        ).fetchone()[0]
    if not authority_enabled:
        with pytest.raises(sqlite3.OperationalError, match="invalid uri authority"):
            with closing(sqlite3.connect(raw_uri + "?mode=ro", uri=True)):
                pass

    con = sqlite3.connect(_readonly_uri_from_path_uri(raw_uri), uri=True)
    try:
        assert con.execute("SELECT value FROM witness").fetchone() == ("intended",)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("CREATE TABLE forbidden_write (value INTEGER)")
    finally:
        con.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        con.execute("SELECT 1")
    assert db.read_bytes() == before
    assert set(tmp_path.iterdir()) == {db}


@pytest.mark.skipif(os.name == "nt", reason="local POSIX parser fixture; no Windows UNC share")
def test_localhost_unc_must_not_select_local_database(tmp_path):
    local_decoy = tmp_path / "local-decoy.db"
    _write_database(local_decoy, "decoy")
    raw_uri = "file://localhost" + urlsplit(local_decoy.as_uri()).path
    intended = Path("//localhost" + local_decoy.as_posix())
    assert not intended.exists()
    before = local_decoy.read_bytes()

    # SQLite treats the raw URI's localhost authority as local, proving
    # that dropping the server would silently open a different database.
    with closing(sqlite3.connect(raw_uri + "?mode=ro", uri=True)) as con:
        assert con.execute("SELECT value FROM witness").fetchone() == ("decoy",)

    with pytest.raises(sqlite3.OperationalError, match="unable to open database"):
        with closing(sqlite3.connect(_readonly_uri_from_path_uri(raw_uri), uri=True)):
            pass

    assert not intended.exists()
    assert local_decoy.read_bytes() == before
    assert set(tmp_path.iterdir()) == {local_decoy}
