# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Nonregular source files must not block advisory caller filtering."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


_FIFO_QUERY = r"""
import sqlite3
import sys
from contextlib import closing
from code_forge import dead_code

parsed = []
class Parser:
    def parse(self, source):
        parsed.append(source)
        raise AssertionError("nonregular input reached parser")

dead_code._PYTHON_PARSER = Parser()
dead_code._DETECTORS[".py"] = dead_code._is_dead_python
with closing(sqlite3.connect(":memory:")) as db:
    db.execute("CREATE TABLE nodes (qualified_name TEXT, file_path TEXT, line_start INTEGER)")
    db.execute("CREATE TABLE edges (kind TEXT, source_qualified TEXT, target_qualified TEXT)")
    db.execute("INSERT INTO nodes VALUES (?, ?, ?)", ("consumer::caller", sys.argv[1], 2))
    db.executemany("INSERT INTO edges VALUES (?, ?, ?)", [
        ("CALLS", "consumer::caller", "target_fn"),
        ("IMPORTS_FROM", "consumer::caller", "target_mod"),
    ])
    assert dead_code._live_callers(db.cursor(), "target_fn", "target_mod") == [
        dead_code.LiveCaller("consumer::caller", sys.argv[1], 2)
    ]
assert parsed == [], "nonregular source was parsed"
print("caller retained")
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
@pytest.mark.parametrize("suffix", [".c", ".h", ".py"], ids=["c", "h", "python"])
@pytest.mark.parametrize("alias", [False, True], ids=["direct", "symlink"])
def test_fifo_source_retains_caller_without_waiting(tmp_path: Path, suffix, alias):
    source = tmp_path / ("caller" + suffix)
    os.mkfifo(source)
    if alias:
        link = tmp_path / ("alias" + suffix)
        link.symlink_to(source)
        source = link
    # A direct, bounded child: subprocess.run kills and reaps it on timeout.
    # The parser is a tripwire; the FIFO must be rejected before parsing.
    result = subprocess.run(
        [sys.executable, "-B", "-c", _FIFO_QUERY, str(source)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "caller retained\n"


@pytest.mark.parametrize("alias", [False, True], ids=["direct", "symlink"])
def test_regular_source_keeps_decoding_and_symlink_behavior(tmp_path, alias):
    from code_forge import dead_code

    source = tmp_path / "caller.c"
    source.write_bytes(b"#if 0\r\nvoid caller() { target(); } // \xff\r\n#endif\r\n")
    if alias:
        link = tmp_path / "alias.c"
        link.symlink_to(source)
        source = link
    assert dead_code._is_dead_call_site(str(source), 2) is True
    with open(source, "rb", opener=dead_code._source_opener) as stream:
        assert stream.read() == source.read_bytes()


@pytest.mark.parametrize("fatal", [False, True], ids=["oserror", "interrupt"])
def test_source_opener_preserves_stat_error_and_closes_fd(tmp_path, monkeypatch, fatal):
    from types import SimpleNamespace

    from code_forge import dead_code

    source = tmp_path / "source.c"
    source.write_text("source")
    opened = []
    closed = []
    error = KeyboardInterrupt() if fatal else OSError("stat failed")

    def acquire(path, flags):
        fd = os.open(path, flags)
        opened.append(fd)
        return fd

    def inspect(fd):
        raise error

    def release(fd):
        closed.append(fd)
        os.close(fd)
        raise OSError("close failed after release")

    monkeypatch.setattr(
        dead_code,
        "os",
        SimpleNamespace(open=acquire, fstat=inspect, close=release, O_NONBLOCK=0),
    )
    with pytest.raises(type(error)) as caught:
        dead_code._source_opener(str(source), os.O_RDONLY)
    assert caught.value is error
    assert closed == opened and len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
def test_source_opener_checks_opened_object_not_path(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from code_forge import dead_code

    source = tmp_path / "source.c"
    source.write_text("regular path before open")
    replacement = tmp_path / "replacement.c"
    os.mkfifo(replacement)
    fd = os.open(replacement, os.O_RDONLY | os.O_NONBLOCK)

    def replaced(path, flags):
        assert path == str(source)
        assert flags & os.O_NONBLOCK
        return fd

    monkeypatch.setattr(
        dead_code,
        "os",
        SimpleNamespace(open=replaced, fstat=os.fstat, close=os.close, O_NONBLOCK=os.O_NONBLOCK),
    )
    with pytest.raises(OSError, match="not a regular file"):
        dead_code._source_opener(str(source), os.O_RDONLY)
    with pytest.raises(OSError):
        os.fstat(fd)
