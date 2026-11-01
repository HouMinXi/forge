# SPDX-License-Identifier: Apache-2.0
"""Temporary fixtures must not inherit an enclosing repository."""

import json
import os
import sys
import tempfile
from pathlib import Path

from code_forge._mutation_process import run_owned_command


_PROBE = """import json
import os
from pathlib import Path

from code_forge._mutation_process import run_owned_command


def test_git_discovery_stops_at_temporary_boundary(tmp_path):
    environment = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": os.environ["GIT_CEILING_DIRECTORIES"],
    }
    results = Path(os.environ["FORGE_GIT_CEILING_RESULTS"])

    def git(label, cwd, *args):
        result = run_owned_command(
            ["git", *args], timeout=10, cwd=str(cwd), env=environment,
            memory_limit_bytes=536870912, text=True,
        )
        with (results / (label + ".json")).open("x") as stream:
            json.dump({"argv": result.args, "returncode": result.returncode,
                       "stdout": result.stdout, "stderr": result.stderr,
                       "ownership": result.ownership}, stream)
        assert result.ownership["cleanup_complete"] is True
        return result

    own = tmp_path / "owned-repository"
    own.mkdir()
    assert git("own-init", own, "init", "--quiet", "--template=").returncode == 0
    positive = git("own-discovery", own, "rev-parse", "--show-toplevel")
    assert positive.returncode == 0 and Path(positive.stdout.strip()) == own

    scratch = tmp_path / "uninitialized"
    scratch.mkdir()
    negative = git("uninitialized-discovery", scratch, "rev-parse", "--show-toplevel")
    assert negative.returncode == 128 and negative.stdout == "", (
        "GIT_CEILING_ISOLATION: uninitialized fixture discovered an enclosing repository: "
        + negative.stdout
    )
"""


def test_explicit_basetemp_does_not_discover_enclosing_repository(tmp_path):
    source_root = Path(__file__).resolve().parents[1]
    results = tmp_path / "nested-git-results"
    results.mkdir()
    with tempfile.TemporaryDirectory(prefix="forge-git-ceiling-", dir="/tmp") as directory:
        scratch = Path(directory)
        enclosing = scratch / "enclosing"
        enclosing.mkdir()
        initialized = run_owned_command(
            ["git", "init", "--quiet", "--template="],
            timeout=10,
            cwd=str(enclosing),
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
            memory_limit_bytes=536870912,
            text=True,
        )
        with (results / "enclosing-init.json").open("x") as stream:
            json.dump(
                {
                    "argv": initialized.args,
                    "returncode": initialized.returncode,
                    "stdout": initialized.stdout,
                    "stderr": initialized.stderr,
                    "ownership": initialized.ownership,
                },
                stream,
            )
        assert initialized.ownership["cleanup_complete"] is True
        assert initialized.returncode == 0

        suite = scratch / "suite"
        tests = suite / "tests"
        tests.mkdir(parents=True)
        (tests / "conftest.py").write_bytes(Path(__file__).with_name("conftest.py").read_bytes())
        (tests / "test_probe.py").write_text(_PROBE, encoding="utf-8")
        nested = run_owned_command(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-q",
                "-c",
                os.devnull,
                "--rootdir=" + str(suite),
                "--confcutdir=" + str(suite),
                "--basetemp=" + str(enclosing / "pytest-base"),
                "--junitxml=" + str(tmp_path / "nested.xml"),
                str(tests / "test_probe.py"),
            ],
            timeout=60,
            cwd=str(suite),
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONPATH": str(source_root / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "FORGE_GIT_CEILING_RESULTS": str(results),
            },
            memory_limit_bytes=536870912,
            text=True,
        )
        for name, body in (("nested.stdout", nested.stdout), ("nested.stderr", nested.stderr)):
            with (tmp_path / name).open("x") as stream:
                stream.write(body)
        with (tmp_path / "nested-owner.json").open("x") as stream:
            json.dump(nested.ownership, stream)
        assert nested.ownership["cleanup_complete"] is True
        assert nested.returncode == 0, nested.stdout + nested.stderr
        assert "1 passed" in nested.stdout
