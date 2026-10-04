"""Source selection uses effective applicability and owned deletion queries."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.mutation_result_fixture import write_inventory

from code_forge import mutation
from code_forge._mutation_process import MutationProcessError


@pytest.mark.parametrize("alias", ["relative", "dot", "absolute", "backslash"])
@pytest.mark.parametrize("custom", [False, True])
def test_excluded_link_aliases_do_not_block_applicable_sources(tmp_path, monkeypatch, alias, custom):
    source = tmp_path / "src/add.py"
    source.parent.mkdir()
    source.write_text("def add(a, b):\n    return a + b\n")
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    relative = "excluded/helper.py" if custom else "tests/helper.py"
    link = tmp_path / relative
    link.parent.mkdir()
    link.symlink_to(target)
    selected = {
        "relative": relative,
        "dot": "./" + relative,
        "absolute": str(link),
        "backslash": relative.replace("/", "\\"),
    }[alias]
    before = {path: (path.lstat(), os.readlink(path) if path.is_symlink() else path.read_bytes())
              for path in [source, target, link]}
    commands = []
    transport = ""

    def execute(argv, **kwargs):
        nonlocal transport
        commands.append(argv)
        if "run" in argv:
            config = (tmp_path / "setup.cfg").read_text()
            assert "src/add.py" in config and "helper.py" not in config
            transport = write_inventory(tmp_path, "src/add.py", {"x_add__mutmut_1": "killed"})
        return subprocess.CompletedProcess(argv, 0, transport if "results" in argv else "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    options = {"mutation_skip_globs": ["excluded/**"], "mutation_include_globs": []} if custom else {}
    findings, infra = mutation.run_mutation(
        ["src/add.py", selected], [sys.executable, "-m", "pytest"],
        cwd=tmp_path, _evidence=evidence, **options
    )
    assert not findings and not infra
    assert evidence["inventory"] == {"add.x_add__mutmut_1": "killed"}
    assert evidence["baseline_passed"] and evidence["completed_measurement"]
    assert len(commands) == 6
    for path, (original, raw) in before.items():
        current = path.lstat()
        assert (current.st_dev, current.st_ino, current.st_mode) == (
            original.st_dev, original.st_ino, original.st_mode
        )
        assert (os.readlink(path) if path.is_symlink() else path.read_bytes()) == raw


@pytest.mark.parametrize("alias", ["parent", "internal-parent", "absolute"])
def test_excluded_looking_escapes_fail_before_process_or_workspace(tmp_path, monkeypatch, alias):
    project = tmp_path / "project"
    source = project / "src/add.py"
    source.parent.mkdir(parents=True)
    source.write_text("def add(a, b):\n    return a + b\n")
    outside = tmp_path / "tests/helper.py"
    outside.parent.mkdir()
    outside.write_text("value = 1\n")
    selected = {"parent": "../tests/helper.py", "internal-parent": "src/../tests/helper.py",
                "absolute": str(outside)}[alias]
    monkeypatch.setattr(mutation, "run_owned_command", lambda *_a, **_k: pytest.fail("process launched"))
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/add.py", selected], ["unused"], cwd=project, _evidence=evidence
    )
    assert findings[0].id == "MUTATION_ERROR" and infra
    assert "outside the project root" in infra[0]
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]
    assert not (project / ".code-forge").exists()
    assert outside.read_text() == "value = 1\n"


@pytest.mark.parametrize("alias", ["src/add.py", "./src/add.py", "absolute", "src\\add.py"])
def test_source_aliases_preserve_one_native_identity(tmp_path, monkeypatch, alias):
    source = tmp_path / "src/add.py"
    source.parent.mkdir()
    source.write_text("def add(a, b):\n    return a + b\n")
    alias = str(source) if alias == "absolute" else alias
    commands = []
    transport = ""

    def execute(argv, **kwargs):
        nonlocal transport
        commands.append(argv)
        if "run" in argv:
            transport = write_inventory(tmp_path, "src/add.py", {"x_add__mutmut_1": "survived"})
        return subprocess.CompletedProcess(argv, 0, transport if "results" in argv else "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(
        ["src/add.py", alias], [sys.executable, "-m", "pytest"], cwd=tmp_path, _evidence=evidence
    )
    assert not infra and len(findings) == 1
    assert evidence["inventory"] == {"add.x_add__mutmut_1": "survived"}
    assert evidence["completed_measurement"] and len(commands) == 6


def test_canonical_selection_preserves_order_and_distinct_identity_collisions(tmp_path):
    names = ["src/pkg.py", "src/other.py", str(tmp_path / "src/pkg.py"), "./src/other.py"]
    assert mutation._relative_sources(names, tmp_path) == ["src/pkg.py", "src/other.py"]
    write_inventory(tmp_path, "src/pkg.py", {"x_add__mutmut_1": "survived"})
    write_inventory(tmp_path, "src/pkg/__init__.py", {"x_add__mutmut_1": "survived"})
    selected = mutation._relative_sources(["src/pkg.py", "src/pkg/__init__.py"], tmp_path)
    assert selected == ["src/pkg.py", "src/pkg/__init__.py"]
    with pytest.raises(ValueError, match="incomplete or unknown mutation result"):
        mutation._mutation_inventory(str(tmp_path), selected)


@pytest.mark.parametrize("alias", ["parent", "absolute"])
def test_direct_selection_retains_lexical_boundaries(tmp_path, alias):
    project = tmp_path / "project"
    project.mkdir()
    name = "../helper.py" if alias == "parent" else str(tmp_path / "helper.py")
    with pytest.raises(ValueError, match="outside the project root"):
        mutation._relative_sources([name], project)


@pytest.mark.parametrize("custom", [False, True])
def test_excluded_source_links_skip_without_a_process(tmp_path, monkeypatch, custom):
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    relative = "src/excluded.py" if custom else "tests/test_example.py"
    link = tmp_path / relative
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.setattr(mutation, "run_owned_command", lambda *_a, **_k: pytest.fail("process launched"))
    evidence = {}
    options = {"mutation_skip_globs": ["src/**"], "mutation_include_globs": []} if custom else {}
    findings, infra = mutation.run_mutation(
        [relative], ["unused"], cwd=tmp_path, _evidence=evidence, **options
    )
    assert not infra and findings[0].fingerprint == "mutation-tests-only"
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]
    assert not (tmp_path / ".code-forge").exists()


@pytest.mark.parametrize("alias", ["relative", "dot", "absolute", "backslash"])
@pytest.mark.parametrize("mirror", [False, True])
def test_include_override_keeps_link_validation_strict(tmp_path, monkeypatch, mirror, alias):
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    source = tmp_path / "tests/helper.py"
    source.parent.mkdir()
    if mirror:
        source.write_text("value = 1\n")
        link = tmp_path / "mutants/tests/helper.py"
        link.parent.mkdir(parents=True)
    else:
        link = source
    link.symlink_to(target)
    monkeypatch.setattr(mutation, "run_owned_command", lambda *_a, **_k: pytest.fail("process launched"))
    evidence = {}
    findings, infra = mutation.run_mutation(
        [{"relative": "tests/helper.py", "dot": "./tests/helper.py", "absolute": str(source),
          "backslash": "tests\\helper.py"}[alias]],
        ["unused"],
        cwd=tmp_path,
        mutation_include_globs=["tests/**"],
        _evidence=evidence,
    )
    assert findings[0].id == "MUTATION_ERROR" and infra
    assert "links are not supported" in infra[0]
    assert not evidence["completed_measurement"]


@pytest.mark.parametrize(
    "case", ["nonzero", "truncated", "missing-git", "timeout", "empty", "deleted", "reappeared"]
)
def test_deletion_query_retains_uncertain_sources_with_owned_executor(tmp_path, monkeypatch, case):
    (tmp_path / ".git").mkdir()
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv == [
            "git",
            "--no-optional-locks",
            "diff",
            "--relative",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            "--diff-filter=D",
            "HEAD",
            "--",
        ]
        assert kwargs == dict(
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=5,
        )
        if case == "missing-git":
            raise FileNotFoundError("missing git")
        if case == "timeout":
            error = subprocess.TimeoutExpired(argv, 5)
            error.ownership = {"cleanup_complete": True, "timed_out": True, "owned": []}
            raise error
        if case == "reappeared":
            (tmp_path / "gone.py").write_text("value = 1\n")
        output = "" if case == "empty" else "gone.py" if case == "truncated" else "gone.py\0"
        return subprocess.CompletedProcess(argv, 1 if case == "nonzero" else 0, output, "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    monkeypatch.setattr(mutation.subprocess, "run", lambda *_a, **_k: pytest.fail("unowned query"))
    selected = mutation._without_deleted_sources(["gone.py"], tmp_path)
    assert selected == ([] if case == "deleted" else ["gone.py"])
    assert len(calls) == 1


@pytest.mark.parametrize("cleanup_complete", [False, True])
def test_deletion_owner_failure_reaches_public_evidence(tmp_path, monkeypatch, cleanup_complete):
    (tmp_path / ".git").mkdir()
    report = {"cleanup_complete": cleanup_complete, "owned": [{"pid": 42, "start_ticks": 17}]}
    workspaces = []
    original = mutation.MutationWorkspace

    def workspace(root):
        value = original(root)
        workspaces.append(value)
        return value

    def execute(*_a, **_k):
        raise MutationProcessError(
            "deletion owner failed", cleanup_complete=cleanup_complete, report=report
        )

    monkeypatch.setattr(mutation, "MutationWorkspace", workspace)
    monkeypatch.setattr(mutation, "run_owned_command", execute)
    evidence = {}
    findings, infra = mutation.run_mutation(["gone.py"], ["unused"], cwd=tmp_path, _evidence=evidence)
    assert findings and findings[0].id == "MUTATION_ERROR" and infra == ["deletion owner failed"]
    assert evidence.get("process_failure") == report
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]
    assert workspaces[0].cleanup_complete is cleanup_complete
    assert not (tmp_path / ".code-forge").exists()


def test_real_git_deletion_hook_is_gone_after_timeout(tmp_path):
    # The isolated helper is a subreaper so even the faulty query's orphan
    # remains ours to close by its observed pidfd, including after assertion failure.
    helper = r"""
import ctypes, json, os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from code_forge import mutation
root = Path(sys.argv[2])
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return None
    return dict(pid=pid, start_ticks=int(fields[19]), ppid=int(fields[1]))
env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
os.environ.update(env)
template = root / "empty-template"
template.mkdir()
def git(*args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root,
                          env=env, capture_output=True, text=True, check=True, timeout=10)
git("init", "-q", f"--template={template}")
(root / "gone.py").write_text("value = 1\n")
git("add", "gone.py")
git("-c", "user.name=Owned Fixture", "-c", "user.email=fixture@example.invalid",
    "-c", "commit.gpgsign=false", "commit", "-q", "-m", "owned source")
(root / "gone.py").unlink()
record = root / "hook.json"
hook = root / "fsmonitor.py"
hook.write_text("#!/usr/bin/python3\nimport json,os,time\nfrom pathlib import Path\n"
                "f=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()\n"
                f"Path({str(record)!r}).write_text(json.dumps(dict(pid=os.getpid(),start_ticks=int(f[19]),ppid=int(f[1]))))\n"
                "os.close(1);os.close(2);time.sleep(20)\n")
hook.chmod(0o700)
git("config", "core.fsmonitor", str(hook))
reports = []
original = mutation.run_owned_command
def execute(*args, **kwargs):
    try:
        return original(*args, **kwargs)
    except subprocess.TimeoutExpired as exc:
        reports.append(exc.ownership)
        raise
mutation.run_owned_command = execute
result = {}
try:
    result["selected"] = mutation._without_deleted_sources(["gone.py"], root)
    observed = json.loads(record.read_text())
    current = identity(observed["pid"])
    result.update(hook=observed, live_after_return=current, ownership=reports,
                  source_origin=str(Path(mutation.__file__).resolve()))
finally:
    if record.exists():
        observed = json.loads(record.read_text())
        current = identity(observed["pid"])
        if current and current["start_ticks"] == observed["start_ticks"]:
            assert current["ppid"] == os.getpid(), current
            fd = os.pidfd_open(current["pid"])
            try:
                check = identity(current["pid"])
                assert check and check["start_ticks"] == observed["start_ticks"]
                signal.pidfd_send_signal(fd, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pid, status = os.waitpid(current["pid"], os.WNOHANG)
                    if pid:
                        result["finally_reaped"] = dict(pid=pid, status=status)
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError("owned orphan did not reap")
            finally:
                os.close(fd)
        result["hook_absent_finally"] = identity(observed["pid"]) is None
    (root / "result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
"""
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            helper,
            str(Path(mutation.__file__).resolve().parent.parent),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["selected"] == ["gone.py"]
    assert measured["hook_absent_finally"]
    assert measured["live_after_return"] is None
    assert measured["ownership"][0]["cleanup_complete"]
    assert measured["ownership"][0]["timed_out"]
    assert any(item["pid"] == measured["hook"]["pid"] for item in measured["ownership"][0]["owned"])
