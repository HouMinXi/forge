import os
import subprocess
import textwrap
from io import StringIO

import yaml

from code_forge import gate_check as gate_check_module
from code_forge.exit_codes import EXIT_PASS
from code_forge.gate_check import run_gate_check


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True)


def _make_repo(root, marker):
    """A tiny package whose test asserts a marker only this copy has."""
    src = root / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "core.py").write_text(f'MARKER = "{marker}"\n')
    tests = root / "tests"
    tests.mkdir()
    return src, tests


def test_test_gate_imports_the_repo_under_check(tmp_path, monkeypatch):
    """A stale copy earlier on sys.path must not satisfy the gate.

    The gate runs the project's own test command. If it leaves the
    import root to the ambient interpreter, an editable install of the
    same package shadows the tree being checked, and the gate reports
    on code that is not staged.
    """
    stale = tmp_path / "stale"
    _make_repo(stale, "stale")

    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")
    (tests / "test_marker.py").write_text(textwrap.dedent("""
        from demo.core import MARKER

        def test_marker():
            assert MARKER == "fresh"
    """))

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))

    # A real baseline with no recorded failures: any failure counts as new.
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")

    # The stale copy wins on PYTHONPATH unless the gate pins its own.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(stale / "src")
    env["FORGE_ALLOW_MAIN"] = "1"

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc == EXIT_PASS


def test_staged_files_come_from_the_repo_under_check(tmp_path, monkeypatch):
    """The staged list must be read from cwd, not the caller's directory.

    run_gate_check takes the repository as an argument, so it cannot
    assume the process already sits inside it. Reading `git diff
    --cached` without a working directory silently reports on whatever
    repository the caller happened to be in.
    """
    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")
    (tests / "test_marker.py").write_text(
        "def test_marker():\n    assert True\n"
    )

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")

    # A second repository with nothing staged. Running from here must
    # not make the gate believe the target repo has no source changes.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git(elsewhere, "init", "-q")
    monkeypatch.chdir(elsewhere)

    env = dict(os.environ)
    env["FORGE_ALLOW_MAIN"] = "1"

    # The gate must actually invoke the runner. A skipped run also
    # returns PASS, so the return code alone cannot tell them apart.
    seen = []
    real_run = subprocess.run

    def record(cmd, **kwargs):
        if "pytest" in " ".join(str(c) for c in cmd):
            seen.append(cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(gate_check_module.subprocess, "run", record)

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc == EXIT_PASS
    assert seen, "gate reported PASS without running the test command"


def test_existing_pythonpath_is_kept_behind_the_repo_source(tmp_path,
                                                            monkeypatch):
    """A caller's PYTHONPATH still applies, but cannot win over the repo.

    Projects put real dependencies on PYTHONPATH, so dropping it breaks
    the run. It must survive, ordered after the repository's own src/.
    """
    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")

    # A dependency that only exists on the inherited PYTHONPATH.
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "sidecar.py").write_text("VALUE = 'from-pythonpath'\n")

    (tests / "test_marker.py").write_text(textwrap.dedent("""
        from demo.core import MARKER
        from sidecar import VALUE

        def test_marker():
            assert MARKER == "fresh"
            assert VALUE == "from-pythonpath"
    """))

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)

    # A stale copy of the package sits alongside the real dependency, so
    # order matters as well as retention.
    stale = tmp_path / "stale"
    _make_repo(stale, "stale")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(extra), str(stale / "src")])
    env["FORGE_ALLOW_MAIN"] = "1"

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc == EXIT_PASS


def test_collection_errors_are_not_treated_as_an_interrupt(tmp_path,
                                                           monkeypatch):
    """pytest exit code 2 also covers a failed collection, not just Ctrl-C.

    Treating every 2 as an interrupt lets a suite that never ran pass
    the gate, which is the failure mode an import error produces.
    """
    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")
    (tests / "test_marker.py").write_text(
        "import a_module_that_does_not_exist  # noqa: F401\n"
        "\n"
        "def test_marker():\n"
        "    assert True\n"
    )

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)

    env = dict(os.environ)
    env["FORGE_ALLOW_MAIN"] = "1"

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc != EXIT_PASS


def test_a_real_interrupt_is_still_waved_through(tmp_path, monkeypatch):
    """Blocking on code 2 must not punish an operator pressing Ctrl-C.

    pytest prints an explicit interrupt banner in that case, which is
    what separates it from a suite that never started.
    """
    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")
    (tests / "test_marker.py").write_text(
        "def test_marker():\n    raise KeyboardInterrupt\n"
    )

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)

    env = dict(os.environ)
    env["FORGE_ALLOW_MAIN"] = "1"

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc == EXIT_PASS


def test_the_word_alone_does_not_wave_a_failed_collection_through(
    tmp_path, monkeypatch
):
    """Mentioning the exception is not the same as being interrupted.

    An import error naming a KeyboardInterrupt helper also exits 2 and
    puts that word in the output, so matching on the word alone hands
    the gate a bypass.
    """
    repo = tmp_path / "repo"
    _src, tests = _make_repo(repo, "fresh")
    (tests / "test_marker.py").write_text(
        "import KeyboardInterrupt_helper  # noqa: F401\n"
        "\n"
        "def test_marker():\n"
        "    assert True\n"
    )

    forge_dir = repo / ".code-forge"
    forge_dir.mkdir()
    (forge_dir / "gate.yaml").write_text(yaml.safe_dump({
        "test": {
            "command": ["python3", "-m", "pytest", "tests/", "-q"],
            "source_patterns": ["src/**/*.py"],
            "timeout_seconds": 120,
        },
    }))
    (forge_dir / "test_baseline.json").write_text(
        '{"schema_version": 1, "test_results": {}}'
    )

    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "add", "-A")
    monkeypatch.chdir(repo)

    env = dict(os.environ)
    env["FORGE_ALLOW_MAIN"] = "1"

    rc = run_gate_check(args=None, env=env, cwd=repo,
                        stdout=StringIO(), stderr=StringIO())
    assert rc != EXIT_PASS
