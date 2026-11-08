# SPDX-License-Identifier: Apache-2.0
"""Actual Git acquisition, static evaluation and command-line contract tests."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_forge.return_contract import check_return_contract


def git(repo, *args):
    return (
        subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    return repo


def commit(repo, source):
    (repo / "sample.py").write_bytes(source)
    git(repo, "add", "sample.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")


def test_real_source_contract_and_exact_type_verdicts(repository):
    source = b"def value():\n    pass\n"
    rev = commit(repository, source)
    bad = check_return_contract(repository, rev, "sample.py", "value", {}, 7)
    good = check_return_contract(repository, rev, "sample.py", "value", {}, None)
    assert (bad.status, good.status) == ("CONFIRMED", "DISMISSED")
    assert bad.source_sha256 == hashlib.sha256(source).hexdigest()
    assert bad.revision == rev and bad.blob == git(repository, "rev-parse", rev + ":sample.py")
    assert bad.actual_json == "null"
    assert bad.expected_json == "7" and good.expected_json == "null"
    boolean = commit(repository, b"def value():\n    return True\n")
    assert check_return_contract(repository, boolean, "sample.py", "value", {}, 1).status == "CONFIRMED"
    assert (
        check_return_contract(repository, boolean, "sample.py", "value", {}, True).status == "DISMISSED"
    )


def test_revision_goal_and_replacement_binding(repository, monkeypatch):
    old = commit(repository, b"def value():\n    pass\n")
    new = commit(repository, b"def value():\n    return 7\n")
    assert check_return_contract(repository, old, "sample.py", "value", {}, 7).status == "CONFIRMED"
    assert check_return_contract(repository, new, "sample.py", "value", {}, 7).status == "DISMISSED"
    (repository / "sample.py").write_text("def value():\n    return 999\n")
    assert check_return_contract(repository, new, "sample.py", "value", {}, 7).status == "DISMISSED"
    assert check_return_contract(repository, new, "sample.py", "other", {}, 7).status == "UNKNOWN"
    with pytest.raises(ValueError):
        check_return_contract(repository, new, "sample.py", "value", {"proof": {"complete": True}}, 7)
    git(repository, "replace", old, new)
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    monkeypatch.setenv("GIT_WORK_TREE", "/nonexistent")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/nonexistent")
    assert check_return_contract(repository, old, "sample.py", "value", {}, 7).status == "CONFIRMED"


def test_external_ambiguous_and_malformed_sources_unknown(repository):
    sources = [
        b"from library import value\n",
        b"from library import fetch\ndef value():\n    return fetch()\n",
        b"def value():\n    pass\nfrom library import value\n",
        b"@decorator\ndef value():\n    pass\n",
        b"class Cache:\n    def value(self):\n        pass\n",
        b"def value():\n    return unknown()\n",
        b"def value():\n    return None\n    unknown()\n",
        b"def value(:\n",
    ]
    for source in sources:
        rev = commit(repository, source)
        for expected in (None, 7):
            assert (
                check_return_contract(repository, rev, "sample.py", "value", {}, expected).status
                == "UNKNOWN"
            )


def test_cli_outcomes_and_invalid_contracts(repository):
    rev = commit(repository, b"def value(x):\n    return x\n")
    base = [
        sys.executable,
        "-B",
        "-m",
        "code_forge.return_contract",
        "--repository",
        str(repository),
        "--revision",
        rev,
        "--path",
        "sample.py",
        "--function",
        "value",
        "--arguments",
        '{"x":7}',
    ]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    for expected, code, status in (("7", 0, "DISMISSED"), ("null", 1, "CONFIRMED")):
        result = subprocess.run(base + ["--expected", expected], capture_output=True, text=True, env=env)
        assert result.returncode == code, result.stderr
        assert json.loads(result.stdout)["status"] == status
    for invalid_json in ('{"x":1,"x":7}', "[" * 1200 + "0" + "]" * 1200, '"' + "x" * 65536 + '"'):
        result = subprocess.run(
            base[:-1] + [invalid_json, "--expected", "7"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2 and not result.stdout
    assert (
        check_return_contract(repository, "0" * 40, "sample.py", "value", {}, None).status
        == "CHECK_FAILED"
    )
    assert check_return_contract(repository, rev, "missing.py", "value", {}, None).status == "UNKNOWN"
    for path in ("../sample.py", "/sample.py", "sample.py/../sample.py"):
        with pytest.raises(ValueError):
            check_return_contract(repository, rev, path, "value", {}, None)


def test_snapshot_path_type_root_and_budget(repository):
    rev = commit(repository, b"def value():\n    return 7\n")
    subdir = repository / "subdir"
    subdir.mkdir()
    assert (
        check_return_contract(subdir, rev, "sample.py", "value", {}, 7).reason
        == "repository-root-mismatch"
    )
    assert check_return_contract(repository, rev, "*.py", "value", {}, 7).status == "UNKNOWN"
    (repository / "link.py").symlink_to("sample.py")
    git(repository, "add", "link.py")
    git(repository, "-c", "commit.gpgsign=false", "commit", "-qm", "link")
    linkrev = git(repository, "rev-parse", "HEAD")
    assert (
        check_return_contract(repository, linkrev, "link.py", "value", {}, 7).reason
        == "source-not-regular-file"
    )
    large = commit(repository, b"#" * 32769)
    assert (
        check_return_contract(repository, large, "sample.py", "value", {}, 7).reason == "source-budget"
    )
    blob = git(repository, "rev-parse", rev + ":sample.py")
    assert (
        check_return_contract(repository, blob, "sample.py", "value", {}, 7).reason
        == "revision-not-commit"
    )


def test_snapshot_policy_failure_and_argument_freeze(repository, monkeypatch):
    from code_forge import return_contract as host

    rev = commit(repository, b"def value(x):\n    return x\n")
    git(repository, "config", "protocol.file.allow", "always")
    original = host.subprocess.run
    calls = []
    arguments = {"x": 7}

    def observe(argv, **kwargs):
        calls.append(argv)
        assert "--no-replace-objects" in argv and "--literal-pathspecs" in argv
        assert "protocol.allow=never" in argv
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
        assert "GIT_DIR" not in kwargs["env"] and kwargs["stdin"] == subprocess.DEVNULL
        arguments["x"] = 999
        return original(argv, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", observe)
    result = check_return_contract(repository, rev, "sample.py", "value", arguments, 7)
    assert result.status == "DISMISSED" and result.arguments_json == '{"x": 7}'
    assert len(calls) == 5

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(host.subprocess, "run", timeout)
    assert check_return_contract(repository, rev, "sample.py", "value", {}, 7).status == "CHECK_FAILED"
