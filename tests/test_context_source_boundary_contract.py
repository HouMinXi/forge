"""Keep context-source identifier policies and Git text decoding independent."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_forge.context_sources import (
    GitHistorySource,
    _removed_identifiers_by_file,
    query_terms,
)


def test_query_accepts_three_character_identifiers_without_relaxing_removals():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-abc = alpha\n+xyz = other\n"
    assert query_terms(["a.py"], diff) == "a.py xyz other"
    assert _removed_identifiers_by_file(diff) == {"a.py": {"alpha"}}


@pytest.mark.parametrize(
    ("output_encoding", "subject"),
    [
        ("utf-8", "\u6d4b\u8bd5\u63d0\u4ea4"),
        ("gbk", "\u6d4b\u8bd5\u63d0\u4ea4"),
        ("iso-8859-1", "Update caf\u00e9 r\u00e9sum\u00e9"),
    ],
)
def test_git_history_decodes_utf8_even_under_an_ascii_locale(tmp_path, output_encoding, subject):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "config", "i18n.logOutputEncoding", output_encoding], cwd=tmp_path, check=True
    )
    (tmp_path / "a.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            subject,
        ],
        cwd=tmp_path,
        check=True,
    )
    source = Path(__file__).resolve().parents[1] / "src"
    script = (
        "import locale, sys\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from pathlib import Path\n"
        "from code_forge.context_sources import GitHistorySource\n"
        "assert locale.getencoding() in ('ANSI_X3.4-1968', 'ascii', 'US-ASCII')\n"
        f"rows = GitHistorySource(Path({str(tmp_path)!r})).facts(['a.py'], '')\n"
        f"assert [row.entity for row in rows] == [{ascii(subject)}]\n"
        "print('UTF8_HISTORY_OK')\n"
    )
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8=0", "-c", script],
        env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert child.stdout.strip() == "UTF8_HISTORY_OK"


def test_git_history_subprocess_sets_a_fixed_decoding_policy(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="abc subject\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    rows = GitHistorySource(tmp_path).facts(["a.py"], "")
    assert [row.entity for row in rows] == ["subject"]
    assert len(calls) == 1
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"
    assert calls[0][0] == ["git", "log", "--encoding=utf-8", "-n", "5", "--format=%h %s", "--", "a.py"]
