# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Deleted Git entries remain semantic scope without executable post-images."""

from pathlib import Path
import hashlib
import os
import stat
import sys
import shutil
import subprocess

import pytest

from code_forge._fixval_transaction import FixvalTransaction, TransactionError
from code_forge.baseline import ResolvedReview
from code_forge.cli import _assemble_post_image, _build_parser, _run_mutation_check
from code_forge.detect import JS_TOOL_REGISTRY, PYTHON_TOOL_REGISTRY
from code_forge.diff import get_changed_files, get_removed_files
from code_forge.fixval import FixvalCandidate, FixvalStatus, run_fixval
from code_forge.machine import StateMachine
from code_forge.registry import ToolConfig
from code_forge.state import Mode


def _git(repo, *args):
    return subprocess.check_output(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def _base(repo, files):
    _git(repo, "init", "-q", "-b", "main")
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", "base")


def _recovery_paths(root):
    namespace = hashlib.sha256(os.fsencode(root.absolute())).hexdigest()[:12]
    return list(root.parent.glob(".fixval-recovery-%s-*" % namespace))


def _machine(repo, diff, files, **kwargs):
    return StateMachine(
        mode=Mode.CI,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        resolved_review=ResolvedReview(
            source_files=files,
            baseline_content=None,
            git_diff=diff,
            mode_hint="git",
        ),
        source_hash="fixture",
        baseline_spec_repr="HEAD..INDEX",
        cwd=repo,
        registry=kwargs.pop("registry", {}),
        **kwargs,
    )


@pytest.mark.parametrize("contents", [b"tracked = 1\n", b"", b"\x00tracked"])
def test_removed_index_entry_never_reads_retained_local_contents(tmp_path, monkeypatch, contents):
    _base(tmp_path, {"config.py": contents, "live.py": b"before = 1\n"})
    (tmp_path / ".gitignore").write_text("config.py\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "ignore local file")
    _git(tmp_path, "rm", "--cached", "config.py")
    sentinel = "LOCAL_ONLY_SENTINEL_NOT_IN_DIFF"
    (tmp_path / "config.py").write_text(sentinel + "\n", encoding="utf-8")
    (tmp_path / "live.py").write_bytes(b"after = 2\n")
    _git(tmp_path, "add", "live.py")
    diff = _git(tmp_path, "diff", "--cached")
    assert _git(tmp_path, "check-ignore", "config.py").strip() == "config.py"
    assert sentinel not in diff
    assert get_changed_files(diff) == ["config.py", "live.py"]
    original_stat = Path.stat

    def guarded_stat(path, *args, **kwargs):
        assert path != tmp_path / "config.py", "removed local content must not be inspected"
        return original_stat(path, *args, **kwargs)

    # The conventions digest scans repository-wide names independently.
    # This assertion covers only the changed-file post-image reader.
    monkeypatch.setattr("code_forge.conventions.get_digest", lambda _: "")
    monkeypatch.setattr(Path, "stat", guarded_stat)
    post_image, _ = _assemble_post_image(tmp_path, diff)
    assert "after = 2" in post_image
    assert "config.py" not in post_image and sentinel not in post_image


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_l0_excludes_only_git_removed_entries(tmp_path, retained, absolute):
    _base(tmp_path, {"gone.js": b"old();\n", "live.js": b"before();\n"})
    _git(tmp_path, "rm", "--cached" if retained else "-f", "gone.js")
    if retained:
        (tmp_path / "gone.js").write_text("LOCAL_ONLY_SENTINEL\n", encoding="utf-8")
    (tmp_path / "live.js").write_text("after();\n", encoding="utf-8")
    _git(tmp_path, "add", "live.js")
    diff = _git(tmp_path, "diff", "--cached")
    paths = [Path(p) for p in [*get_changed_files(diff), "missing.js"]]
    files = [tmp_path / p for p in paths] if absolute else paths
    captured = []

    def run_l0(registry, inputs):
        captured.extend(inputs)
        return [], ["permission denied: missing.js"]

    machine = _machine(tmp_path, diff, files, l0_runner=run_l0)
    assert machine._run_l0_phase() == []
    assert captured == [p for p in files if p.name != "gone.js"]
    assert machine.resolved_review.source_files == files
    assert machine._state.infra_errors == ["permission denied: missing.js"]


def test_empty_live_file_still_reaches_l0_and_post_image(tmp_path):
    _base(tmp_path, {"emptied.py": b"before = 1\n"})
    (tmp_path / "emptied.py").write_text("", encoding="utf-8")
    _git(tmp_path, "add", "--all")
    diff = _git(tmp_path, "diff", "--cached")
    captured = []
    machine = _machine(
        tmp_path,
        diff,
        [tmp_path / "emptied.py"],
        l0_runner=lambda _, files: (captured.extend(files) or [], []),
    )
    machine._run_l0_phase()
    assert captured == [tmp_path / "emptied.py"]
    post_image, _ = _assemble_post_image(tmp_path, diff)
    assert post_image == "## File: emptied.py\n```\n\n```"


@pytest.mark.parametrize("operation", ["rename", "copy"])
def test_metadata_destination_stays_in_l0_and_post_image(tmp_path, operation):
    data = b"kept = 1\n" * 20
    _base(tmp_path, {"old.py": data})
    (tmp_path / "new name.py").write_bytes(data)
    if operation == "rename":
        (tmp_path / "old.py").unlink()
    _git(tmp_path, "add", "--all")
    diff = _git(tmp_path, "diff", "--cached", "-M", "-C", "--find-copies-harder")
    assert operation + " to new name.py" in diff
    captured = []
    machine = _machine(
        tmp_path,
        diff,
        [tmp_path / "new name.py"],
        l0_runner=lambda _, files: (captured.extend(files) or [], []),
    )
    machine._run_l0_phase()
    assert captured == [tmp_path / "new name.py"]
    post_image, _ = _assemble_post_image(tmp_path, diff)
    assert post_image == "## File: new name.py\n```\n%s\n```" % data.decode("utf-8")


def test_non_git_scope_is_preserved(tmp_path):
    files = [tmp_path / "missing.js"]
    captured = []
    machine = _machine(
        tmp_path,
        None,
        files,
        l0_runner=lambda _, inputs: (captured.extend(inputs) or [], ["real tool failure"]),
    )
    machine._run_l0_phase()
    assert captured == files
    assert "real tool failure" in machine._state.infra_errors


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("retained", [False, True])
def test_real_eslint_keeps_live_findings_and_missing_path_errors(
    tmp_path, monkeypatch, missing, retained
):
    if shutil.which("eslint") is None:
        pytest.skip("real eslint executable unavailable")
    _base(
        tmp_path,
        {
            "eslint.config.mjs": b'export default [{files:["**/*.js"],rules:{"no-undef":"error"}}];\n',
            "gone.js": b"const y = 1;\n",
            "live.js": b"const x = 1;\n",
        },
    )
    if retained:
        (tmp_path / ".gitignore").write_text("gone.js\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore")
        _git(tmp_path, "commit", "-q", "-m", "ignore removed local entry")
    _git(tmp_path, "rm", "--cached" if retained else "-f", "gone.js")
    (tmp_path / "live.js").write_text("missingFunction();\n", encoding="utf-8")
    _git(tmp_path, "add", "live.js")
    diff = _git(tmp_path, "diff", "--cached")
    assert get_removed_files(diff) == ["gone.js"]
    files = [tmp_path / p for p in get_changed_files(diff)]
    if missing:
        files.append(tmp_path / "missing.js")
    registry = {
        "eslint": ToolConfig(name="eslint", args=[], **JS_TOOL_REGISTRY["eslint"]["tools_yaml_entry"])
    }
    machine = _machine(tmp_path, diff, files, registry=registry, coverage_l1_active=False)
    monkeypatch.chdir(tmp_path)
    findings = machine._run_l0_phase()
    assert machine.resolved_review.source_files == files
    if missing:
        assert any("missing.js" in err and "exited 2" in err for err in machine._state.infra_errors)
    else:
        assert machine._state.infra_errors == []
        assert len(findings) == 1
        assert findings[0].file == str(tmp_path / "live.js")
        assert "missingFunction" in findings[0].description
        assert findings[0].disposition.value == "CONFIRMED"


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("l1_active", [False, True])
@pytest.mark.parametrize("exempt", [False, True])
def test_removed_path_needs_semantic_coverage(tmp_path, retained, l1_active, exempt):
    _base(tmp_path, {"gone.js": b"old();\n", "live.js": b"before();\n"})
    _git(tmp_path, "rm", "--cached" if retained else "-f", "gone.js")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / "gone.js", tmp_path / "live.js"]
    captured = []
    registry = {
        "eslint": ToolConfig(name="eslint", args=[], **JS_TOOL_REGISTRY["eslint"]["tools_yaml_entry"])
    }
    machine = _machine(
        tmp_path,
        diff,
        files,
        registry=registry,
        coverage_l1_active=l1_active,
        coverage_exempt_patterns=["*/gone.js"] if exempt else [],
        l0_runner=lambda _, inputs: (captured.extend(inputs) or [], []),
    )
    machine._run_l0_phase()
    assert captured == [tmp_path / "live.js"]
    gaps = machine._run_coverage_phase()
    assert machine.resolved_review.source_files == files
    assert [gap.file for gap in gaps] == ([] if l1_active or exempt else [str(tmp_path / "gone.js")])
    assert all(gap.source == "COVERAGE" and gap.disposition.value == "UNCERTAIN" for gap in gaps)
    assert all("no post-image for L0" in gap.description for gap in gaps)


def test_removed_symlink_does_not_exclude_live_target(tmp_path):
    _base(tmp_path, {"live.py": b"value = 1\n"})
    (tmp_path / "alias.py").symlink_to("live.py")
    _git(tmp_path, "add", "alias.py")
    _git(tmp_path, "commit", "-q", "-m", "alias")
    _git(tmp_path, "rm", "--cached", "alias.py")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / "alias.py", tmp_path / "live.py"]
    captured = []
    machine = _machine(
        tmp_path,
        diff,
        files,
        l0_runner=lambda _, inputs: (captured.extend(inputs) or [], []),
    )
    machine._run_l0_phase()
    assert captured == [tmp_path / "live.py"]
    assert machine.resolved_review.source_files == files


def test_real_ruff_preserves_live_undefined_name(tmp_path, monkeypatch):
    if shutil.which("ruff") is None:
        pytest.skip("real ruff executable unavailable")
    _base(tmp_path, {"gone.py": b"value = 1\n", "live.py": b"value = 2\n"})
    _git(tmp_path, "rm", "-f", "gone.py")
    (tmp_path / "live.py").write_text("missingFunction()\n", encoding="utf-8")
    _git(tmp_path, "add", "live.py")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / p for p in get_changed_files(diff)]
    registry = {
        "ruff": ToolConfig(name="ruff", args=[], **PYTHON_TOOL_REGISTRY["ruff"]["tools_yaml_entry"])
    }
    machine = _machine(tmp_path, diff, files, registry=registry)
    monkeypatch.chdir(tmp_path)
    findings = machine._run_l0_phase()
    assert machine.resolved_review.source_files == files
    assert machine._state.infra_errors == []
    assert len(findings) == 1
    assert findings[0].file == str(tmp_path / "live.py")
    assert "missingFunction" in findings[0].description
    assert findings[0].disposition.value == "CONFIRMED"


@pytest.mark.parametrize("snapshot", ["staged", "committed"])
@pytest.mark.parametrize("binary_patch", [False, True])
@pytest.mark.parametrize("contents", [b"text\n", b"", b"\x00binary"])
def test_git_removal_identity_for_metadata_and_binary(tmp_path, contents, binary_patch, snapshot):
    path = 'a/quoted "\u96ea name.py'
    _base(tmp_path, {path: contents})
    _git(tmp_path, "rm", "--cached", path)
    refs = ["--cached"]
    if snapshot == "committed":
        _git(tmp_path, "commit", "-q", "-m", "remove entry")
        refs = ["HEAD^", "HEAD"]
    diff = _git(tmp_path, "diff", *refs, *(["--binary"] if binary_patch else []))
    assert (tmp_path / path).read_bytes() == contents
    assert get_changed_files(diff) == [path]
    assert get_removed_files(diff) == [path]
    assert _assemble_post_image(tmp_path, diff)[0] == ""


@pytest.mark.parametrize("diff", ["", "   \n", "--- a/f.py\n+++ b/f.py\n@@ malformed @@\n"])
def test_unusable_removal_diff_does_not_hide_inputs(tmp_path, diff):
    files = [tmp_path / "missing.py"]
    captured = []
    assert get_removed_files(diff) == []
    machine = _machine(
        tmp_path,
        diff,
        files,
        l0_runner=lambda _, inputs: (captured.extend(inputs) or [], ["real missing path"]),
    )
    machine._run_l0_phase()
    assert captured == files
    assert "real missing path" in machine._state.infra_errors


@pytest.mark.parametrize("snapshot", ["staged", "committed"])
@pytest.mark.parametrize("replacement", ["regular", "symlink"])
def test_same_path_type_replacement_has_live_post_image(tmp_path, monkeypatch, snapshot, replacement):
    _base(tmp_path, {"target.py": b"value = 1\n"})
    path = tmp_path / "changed name.py"
    if replacement == "regular":
        path.symlink_to("target.py")
    else:
        path.write_text("old = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-q", "-m", "original type")
    path.unlink()
    if replacement == "regular":
        path.write_text("missingFunction()\n", encoding="utf-8")
    else:
        path.symlink_to("target.py")
    _git(tmp_path, "add", "--all")
    refs = ["--cached"]
    if snapshot == "committed":
        _git(tmp_path, "commit", "-q", "-m", "replace type")
        refs = ["HEAD^", "HEAD"]
    diff = _git(tmp_path, "diff", *refs)
    assert diff.count("diff --git") == 2
    assert get_changed_files(diff) == [path.name]
    assert get_removed_files(diff) == []
    seen = []
    machine = _machine(
        tmp_path,
        diff,
        [path],
        l0_runner=lambda _, files: (seen.extend(files) or [], []),
    )
    machine._run_l0_phase()
    assert seen == [path]
    monkeypatch.setattr("code_forge.conventions.get_digest", lambda _: "")
    post_image, _ = _assemble_post_image(tmp_path, diff)
    assert "## File: " + path.name in post_image
    assert path.read_text(encoding="utf-8") in post_image


def _retained_deletion_with_live(repo, suffix):
    _base(repo, {"gone." + suffix: b"old = 1\n", "live." + suffix: b"value = 1\n"})
    _git(repo, "rm", "--cached", "gone." + suffix)
    (repo / ("gone." + suffix)).write_text("LOCAL_ONLY_SENTINEL\n", encoding="utf-8")
    (repo / ("live." + suffix)).write_text("value = 2\n", encoding="utf-8")
    _git(repo, "add", "live." + suffix)
    diff = _git(repo, "diff", "--cached")
    assert "LOCAL_ONLY_SENTINEL" not in diff
    gate = repo / ".code-forge" / "gate.yaml"
    gate.parent.mkdir(exist_ok=True)
    gate.write_text(
        "test:\n  command: [echo, ok]\nrulepacks: [vercel-react]\n"
        "rulepacks_blocking: [no-document-write]\n",
        encoding="utf-8",
    )
    return diff, [Path(p) for p in get_changed_files(diff)]


@pytest.mark.parametrize("absolute", [False, True])
def test_local_mutation_dispatch_excludes_retained_deletion(tmp_path, absolute):
    diff, paths = _retained_deletion_with_live(tmp_path, "py")
    files = [tmp_path / p for p in paths] if absolute else paths
    seen = []

    def mutation(inputs, command, **kwargs):
        seen.append((list(inputs), command, kwargs))
        return [], ["mutation infrastructure preserved"]

    machine = _machine(tmp_path, diff, files, l2_runner=mutation)
    machine._run_l2_phase()
    assert seen[0][0] == [str(f) for f in files if f.name == "live.py"]
    assert seen[0][1] == ["echo", "ok"]
    assert machine._source_files() == files
    assert machine._state.infra_errors == ["mutation infrastructure preserved"]


@pytest.mark.parametrize("absolute", [False, True])
def test_ci_mutation_dispatch_excludes_retained_deletion(tmp_path, monkeypatch, absolute):
    diff, paths = _retained_deletion_with_live(tmp_path, "py")
    files = [tmp_path / p for p in paths] if absolute else paths
    machine = _machine(tmp_path, diff, files)
    seen = []
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    monkeypatch.setattr(StateMachine, "_write_ci_ledger_rows", lambda self: None)
    monkeypatch.setattr(shutil, "which", lambda _: "/fixture/mutmut")

    def launch(inputs, command, cwd, *args, **kwargs):
        seen.append((list(inputs), command, cwd))
        return True

    monkeypatch.setattr("code_forge.machine.launch_detached_mutation", launch)
    machine._run_ci()
    assert seen == [([str(f) for f in files if f.name == "live.py"], ["echo", "ok"], tmp_path)]
    assert machine._source_files() == files
    assert machine._state.infra_errors == []


def test_blocking_rulepack_dispatch_excludes_retained_deletion(tmp_path, monkeypatch):
    import json

    from code_forge.disposition import Disposition

    diff, files = _retained_deletion_with_live(tmp_path, "js")
    seen = []
    monkeypatch.setattr(shutil, "which", lambda _: "/fixture/semgrep")

    def scanner(argv, **kwargs):
        selected = argv[argv.index("--jobs") + 2 :]
        seen.append((selected, kwargs["cwd"]))
        results = [
            {
                "ruleId": "no-document-write",
                "level": "warning",
                "message": {"text": "Avoid document.write"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": name},
                            "region": {"startLine": 1, "endLine": 1},
                        }
                    }
                ],
            }
            for name in selected
        ]
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "version": "2.1.0",
                    "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": results}],
                }
            ),
            "",
        )

    monkeypatch.setattr("code_forge.rulepack.subprocess.run", scanner)
    machine = _machine(tmp_path, diff, files)
    findings = machine._run_rulepack_blocking_phase()
    assert seen == [(["live.js"], str(tmp_path))]
    assert [(f.file, f.disposition) for f in findings] == [("live.js", Disposition.CONFIRMED)]
    assert machine._source_files() == files
    assert machine._state.infra_errors == []


@pytest.mark.parametrize("missing", [False, True])
def test_native_eslint_uses_source_root_with_private_state(tmp_path, monkeypatch, missing):
    if shutil.which("eslint") is None:
        pytest.skip("real eslint executable unavailable")
    from code_forge import runner

    repo = tmp_path / "project"
    repo.mkdir()
    _base(
        repo,
        {
            "eslint.config.mjs": b'export default [{files:["**/*.js"],rules:{"no-undef":"error"}}];\n',
            "gone.js": b"const old = 1;\n",
            "live.js": b"const value = 1;\n",
        },
    )
    _git(repo, "rm", "--cached", "gone.js")
    (repo / "gone.js").write_text("LOCAL_ONLY_SENTINEL\n", encoding="utf-8")
    (repo / "live.js").write_text("missingFunction();\n", encoding="utf-8")
    _git(repo, "add", "live.js")
    diff = _git(repo, "diff", "--cached")
    state = tmp_path / "state"
    state.mkdir()
    files = [repo / p for p in get_changed_files(diff)]
    if missing:
        files.append(repo / "missing.js")
    calls = []
    real_run = runner.subprocess.run

    def capture(argv, **kwargs):
        result = real_run(argv, **kwargs)
        calls.append((argv, kwargs.get("cwd"), result.returncode))
        return result

    monkeypatch.setattr(runner.subprocess, "run", capture)
    registry = {
        "eslint": ToolConfig(name="eslint", args=[], **JS_TOOL_REGISTRY["eslint"]["tools_yaml_entry"])
    }
    machine = _machine(state, diff, files, source_root=repo, registry=registry)
    findings = machine._run_l0_phase()
    assert calls and all(cwd == repo for _, cwd, _ in calls)
    assert all(str(repo / "gone.js") not in argv for argv, _, _ in calls)
    if missing:
        assert any(code == 2 for _, _, code in calls)
        assert machine._state.infra_errors
    else:
        assert machine._state.infra_errors == []
        assert [(f.file, f.disposition.value) for f in findings] == [
            (str(repo / "live.js"), "CONFIRMED")
        ]
        assert "missingFunction" in findings[0].description
        assert machine._preexisting_buf == []
    assert machine._source_files() == files


def test_runner_executes_relative_tool_and_version_in_source_root(tmp_path):
    import json

    from code_forge.runner import run_tools

    tool = tmp_path / "scripts" / "inspect.py"
    tool.parent.mkdir()
    tool.write_text(
        "#!/usr/bin/python3\nimport json, os, sys\n"
        "if '--version' in sys.argv: print(os.getcwd())\n"
        "else: print(json.dumps({'cwd': os.getcwd(), 'files': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    config = ToolConfig(
        name="inspect",
        command="scripts/inspect.py",
        args=[],
        output_format="eslint_json",
        file_patterns=["*.js"],
    )
    results, versions, skipped, infra = run_tools({"inspect": config}, ["live.js"], cwd=tmp_path)
    assert skipped == []
    assert infra == []
    assert versions == {"inspect": str(tmp_path)}
    stdout, code, stderr = results["inspect"]
    assert code == 0 and stderr == ""
    assert json.loads(stdout) == {"cwd": str(tmp_path), "files": ["live.js"]}


def _terminal_fixture(repo, state, diff, files, monkeypatch):
    import json

    (state / ".code-forge").mkdir(exist_ok=True)
    (state / ".code-forge" / "gate.yaml").write_text(
        "test:\n  command: "
        + json.dumps(
            [
                "/usr/bin/python3",
                "-B",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    machine = _machine(state, diff, files, source_root=repo)
    # Only receipt/ledger prerequisites are bounded here; FIXVAL and its
    # baseline, Git revert, scoped pytest and terminal dispatch remain real.
    monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: [])
    monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", lambda: [])
    monkeypatch.setattr(machine, "_write_ledger_rows", lambda: None)
    monkeypatch.setattr(machine, "_persist_state", lambda: None)
    return machine


@pytest.mark.parametrize("hollow", [False, True])
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("private_state", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_real_fixval_projects_deleted_tests_and_uses_source_root(
    tmp_path,
    monkeypatch,
    hollow,
    retained,
    private_state,
    absolute,
):
    from code_forge.state import Verdict

    repo = tmp_path / "project"
    repo.mkdir()
    _base(
        repo, {"src/model.py": b"value = 1\n", "tests/test_removed.py": b"def test_old(): assert True\n"}
    )
    _git(repo, "rm", "--cached" if retained else "-f", "tests/test_removed.py")
    if retained:
        (repo / "tests/test_removed.py").write_text("LOCAL_ONLY_SENTINEL\n", encoding="utf-8")
    (repo / "src/model.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests/test_live.py").write_text(
        "def test_live():\n    assert True\n"
        if hollow
        else "from model import value\ndef test_live():\n    assert value == 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "src/model.py", "tests/test_live.py")
    diff = _git(repo, "diff", "--cached")
    state = tmp_path / "state" if private_state else repo
    state.mkdir(exist_ok=True)
    files = [repo / p if absolute else Path(p) for p in get_changed_files(diff)]
    machine = _terminal_fixture(repo, state, diff, files, monkeypatch)
    real_run = subprocess.run
    calls = []
    images = []

    def capture(argv, **kwargs):
        if argv[:3] == ["git", "apply", "-R"]:
            supplied = Path(kwargs["cwd"])
            assert supplied.parent == Path("/proc/%s/fd" % os.getpid())
            assert supplied.name.isdecimal()
            bound = os.fstat(int(supplied.name))
            image = supplied.resolve()
            observed = image.stat()
            assert (bound.st_dev, bound.st_ino) == (observed.st_dev, observed.st_ino)
            assert image.name.startswith(".fixval-image-")
            assert image.is_dir() and not image.is_symlink()
            assert image.stat().st_mode & 0o777 == 0o700
            assert image.parent.name.startswith(".fixval-recovery-")
            assert image.parent.parent == tmp_path
            images.append(image)
        result = real_run(argv, **kwargs)
        calls.append((argv, kwargs.get("cwd"), result.returncode, result.stdout))
        return result

    monkeypatch.setattr(subprocess, "run", capture)
    original = (repo / "src/model.py").read_bytes()
    machine._finalize_local_terminal()
    assert machine._state.verdict == (Verdict.FAIL if hollow else Verdict.PASS)
    tests = [c for c in calls if "pytest" in c[0]]
    assert len(tests) >= 4
    assert [c[2] for c in tests[:4]] == [0, 0, 0, 0 if hollow else 1]
    assert all(not any(arg.endswith("tests/test_removed.py") for arg in c[0]) for c in tests)
    source_calls = [c for c in calls if c[0][:3] != ["git", "apply", "-R"]]
    private_calls = [c for c in calls if c[0][:3] == ["git", "apply", "-R"]]
    assert source_calls and all(Path(c[1]) == repo for c in source_calls)
    assert len(private_calls) == len(images) == 1
    assert (repo / "src/model.py").read_bytes() == original
    assert _git(repo, "diff", "--cached") == diff
    assert machine._source_files() == files
    assert not any(f.id == "MUTATION_SKIPPED" for f in machine._state.findings)
    if hollow:
        assert any(f.id == "FIXVAL_HOLLOW" for f in machine._state.findings)


@pytest.mark.parametrize("gone_path", ["src/gone.py", "src/deep/gone.py"])
def test_real_fixval_keeps_deleted_production_revert_fact(tmp_path, monkeypatch, gone_path):
    from code_forge.state import Verdict

    _base(
        tmp_path, {gone_path: b"old = True\n", "tests/test_removed.py": b"def test_old(): assert True\n"}
    )
    _git(tmp_path, "rm", gone_path, "tests/test_removed.py")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_live.py").write_text(
        "from pathlib import Path\ndef test_live():\n    assert not Path(%r).exists()\n" % gone_path,
        encoding="utf-8",
    )
    _git(tmp_path, "add", "tests/test_live.py")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / p for p in get_changed_files(diff)]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.PASS
    assert machine._source_files() == files
    assert not (tmp_path / gone_path).exists()
    assert not any(f.id in {"FIXVAL_SKIPPED", "MUTATION_SKIPPED"} for f in machine._state.findings)
    assert _git(tmp_path, "diff", "--cached") == diff


def test_deleted_tests_without_current_test_skip_before_pytest(tmp_path, monkeypatch):
    from code_forge.state import Verdict

    _base(
        tmp_path,
        {"src/model.py": b"value = 1\n", "tests/test_removed.py": b"def test_old(): assert True\n"},
    )
    _git(tmp_path, "rm", "tests/test_removed.py")
    (tmp_path / "src/model.py").write_text("value = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/model.py")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / p for p in get_changed_files(diff)]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    monkeypatch.setattr(
        "code_forge.fixval.run_fixval", lambda *a: pytest.fail("no executable test may run")
    )
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.PASS
    (finding,) = machine._state.findings
    assert finding.id == "FIXVAL_SKIPPED"
    assert finding.description == "FIXVAL skipped: no executable test file in diff"
    assert machine._source_files() == files


def test_non_deleted_missing_test_remains_real_baseline_error(tmp_path, monkeypatch):
    _base(tmp_path, {"src/model.py": b"value = 1\n"})
    (tmp_path / "src/model.py").write_text("value = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/model.py")
    diff = _git(tmp_path, "diff", "--cached")
    files = [tmp_path / "src/model.py", tmp_path / "tests/test_missing.py"]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    calls = []
    real_run = subprocess.run

    def capture(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if "pytest" in argv:
            calls.append((argv, result.returncode, result.stderr))
        return result

    monkeypatch.setattr(subprocess, "run", capture)
    machine._finalize_local_terminal()
    assert calls and calls[0][1] == 4
    assert str(files[1]) in calls[0][0]
    assert "test_missing.py" in calls[0][2]
    assert any(f.id == "MUTATION_SKIPPED" for f in machine._state.findings)
    assert machine._source_files() == files


def test_overfit_uses_projected_live_production_and_source_root(tmp_path, monkeypatch):
    from code_forge import fixval

    repo = tmp_path / "project"
    repo.mkdir()
    _base(repo, {"src/gone.py": b"old = True\n", "src/model.py": b"value = 1\n"})
    _git(repo, "rm", "--cached", "src/gone.py")
    sentinel = b"LOCAL_ONLY_SENTINEL\n"
    (repo / "src/gone.py").write_bytes(sentinel)
    (repo / "src/model.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/test_live.py").write_text("def test_live(): assert True\n", encoding="utf-8")
    _git(repo, "add", "src/model.py", "tests/test_live.py")
    diff = _git(repo, "diff", "--cached")
    state = tmp_path / "state"
    state.mkdir()
    files = [repo / p for p in get_changed_files(diff)]
    machine = _terminal_fixture(repo, state, diff, files, monkeypatch)
    candidates = []

    def accepted(candidate, *args):
        candidates.append(candidate)
        return fixval.FixvalResult(fixval.FixvalStatus.PASS, [], [])

    monkeypatch.setattr(fixval, "run_fixval", accepted)
    real_guard = fixval.run_overfit_guard
    guards = []

    def guard(candidate, command, cwd):
        guards.append((candidate, cwd))
        return real_guard(candidate, command, cwd)

    monkeypatch.setattr(fixval, "run_overfit_guard", guard)
    real_read = Path.read_bytes

    def checked_read(path):
        assert path != repo / "src/gone.py", "retained Git deletion is not a transform input"
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", checked_read)
    machine._finalize_local_terminal()
    assert str(repo / "src/gone.py") in candidates[0].non_test_files
    candidate, cwd = guards[0]
    assert candidate.non_test_files == [str(repo / "src/model.py")]
    assert candidate.test_files == [str(repo / "tests/test_live.py")]
    assert cwd == repo
    assert real_read(repo / "src/gone.py") == sentinel
    assert real_read(repo / "src/model.py") == b"value = 2\n"
    assert machine._advisories == []


@pytest.mark.parametrize("fallback", [False, True])
def test_fixval_commit_message_uses_source_git_root(tmp_path, fallback):
    repo = tmp_path / "project"
    repo.mkdir()
    _base(repo, {"src/model.py": b"value = 1\n"})
    message = "base\n\nFixval-Waiver: fixture reason\n"
    if fallback:
        _git(repo, "commit", "--amend", "-q", "-m", message)
        (repo / ".git/COMMIT_EDITMSG").unlink()
    else:
        (repo / ".git/COMMIT_EDITMSG").write_text(message, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    machine = _machine(state, "", [], source_root=repo)
    assert machine._get_commit_message() == message.strip()


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_captured_rename_preserved_when_deriving_paths(tmp_path, monkeypatch, explicit):
    from code_forge import cli
    from code_forge.state import Verdict

    _base(tmp_path, {"old.py": b"value = 1\n" * 20})
    _git(tmp_path, "mv", "old.py", "new.py")
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge/tools.yaml").write_text("tools: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_load_gate_backends", lambda _: ({}, {}))
    monkeypatch.setattr(cli, "_merge_user_into", lambda configs, data: configs)
    monkeypatch.setattr("code_forge.user_config.load_user_retry", lambda: {})
    monkeypatch.setattr("code_forge.outlet_resolver.resolve_outlet", lambda *a, **kw: "subprocess")
    captured = []
    resolutions = []
    real_resolve = cli.resolve_baseline

    def resolve(*args):
        result = real_resolve(*args)
        resolutions.append(result)
        return result

    def dispatch(outlet, warn, contract, backend, resolved, *args, **kwargs):
        captured.append(resolved)
        return Verdict.PASS

    monkeypatch.setattr(cli, "resolve_baseline", resolve)
    monkeypatch.setattr(cli, "_dispatch_subagent", dispatch)
    args = cli._build_parser().parse_args(
        [
            "review",
            "--baseline",
            "HEAD",
            "--head",
            "INDEX",
            "--allow-main",
            "--registry",
            str(tmp_path / ".code-forge/tools.yaml"),
            "--backend-url",
            "https://example.invalid",
            "--backend-format",
            "openai",
            "--backend-key-env",
            "FIXTURE_KEY",
            "--backend-model",
            "fixture",
            *(["new.py"] if explicit else []),
        ]
    )
    assert cli._run(args, {"FIXTURE_KEY": "fixture"}, tmp_path) == Verdict.PASS
    (resolved,) = captured
    assert resolved.source_files == [Path("new.py")]
    assert len(resolutions) == 1
    assert resolved.git_diff == resolutions[0].git_diff
    assert resolved.base_sha == resolutions[0].base_sha
    assert resolved.head_sha == resolutions[0].head_sha
    if explicit:
        assert "rename from old.py" not in resolved.git_diff
    else:
        assert "rename from old.py" in resolved.git_diff
        assert "rename to new.py" in resolved.git_diff


def _retained_identity(path):
    import os
    import stat

    info = path.lstat()
    value = os.readlink(path) if stat.S_ISLNK(info.st_mode) else path.read_bytes()
    return info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), value


@pytest.mark.parametrize("hollow", [False, True])
@pytest.mark.parametrize("retained_kind", ["text", "empty", "binary", "symlink"])
def test_real_fixval_retained_production_cannot_skip_reversal(
    tmp_path,
    monkeypatch,
    hollow,
    retained_kind,
):
    from code_forge.state import Verdict

    _base(tmp_path, {"src/gone.py": b"previous = True\n", "src/model.py": b"value = 1\n"})
    _git(tmp_path, "rm", "--cached", "src/gone.py")
    retained = tmp_path / "src/gone.py"
    target = tmp_path / "symlink-target"
    target.write_bytes(b"DO_NOT_TOUCH\n")
    if retained_kind == "symlink":
        retained.unlink()
        retained.symlink_to("../symlink-target")
    else:
        retained.write_bytes(
            {"text": b"LOCAL_ONLY_SENTINEL\n", "empty": b"", "binary": b"\x00payload\n"}[retained_kind]
        )
        retained.chmod(0o751)
    (tmp_path / "src/model.py").write_bytes(b"value = 2\n")
    (tmp_path / "src/model.py").chmod(0o754)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_live.py").write_text(
        "def test_live(): assert True\n"
        if hollow
        else "from model import value\ndef test_live(): assert value == 2\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "src/model.py", "tests/test_live.py")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    files = [tmp_path / name for name in get_changed_files(diff)]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    before = _retained_identity(retained)
    target_before = _retained_identity(target)
    index_before = (tmp_path / ".git/index").read_bytes()
    calls = []
    real_run = subprocess.run

    def capture(argv, **kwargs):
        result = real_run(argv, **kwargs)
        calls.append((argv, result.returncode, result.stderr))
        return result

    monkeypatch.setattr(subprocess, "run", capture)
    machine._finalize_local_terminal()
    assert not any(f.id in {"FIXVAL_SKIPPED", "MUTATION_SKIPPED"} for f in machine._state.findings)
    assert machine._state.verdict == (Verdict.FAIL if hollow else Verdict.PASS)
    tests = [call for call in calls if "pytest" in call[0]]
    assert len(tests) >= 4
    assert [call[1] for call in tests[:4]] == [0, 0, 0, 0 if hollow else 1]
    assert _retained_identity(retained) == before
    assert _retained_identity(target) == target_before
    assert (tmp_path / "src/model.py").read_bytes() == b"value = 2\n"
    assert (tmp_path / "src/model.py").stat().st_mode & 0o777 == 0o754
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert machine._source_files() == files


@pytest.mark.parametrize("retained", [False, True])
def test_advisory_legacy_uses_same_executable_projection(tmp_path, retained):
    from code_forge.legacy import LegacyRunner

    _base(tmp_path, {"gone.js": b"old();\n", "live.js": b"old();\n"})
    _git(tmp_path, "rm", "--cached" if retained else "-f", "gone.js")
    (tmp_path / "live.js").write_bytes(b"live();\n")
    _git(tmp_path, "add", "live.js")
    diff = _git(tmp_path, "diff", "--cached")
    files = [Path(name) for name in get_changed_files(diff)] + [Path("missing.js")]
    calls = []

    def l0(registry, selected):
        calls.append(list(selected))
        return [], []

    legacy = LegacyRunner(l0_runner=l0)
    machine = _machine(tmp_path, diff, files, l0_runner=l0, advisory_runners=[legacy])
    machine._run_l0_phase()
    machine._run_advisory_axes()
    assert calls == [[Path("live.js"), Path("missing.js")]] * 2
    assert legacy.source_files == [Path("live.js"), Path("missing.js")]
    assert machine.resolved_review.git_diff == diff
    assert machine._source_files() == files


def test_advisory_rulepack_cannot_rescan_retained_deletion(tmp_path, monkeypatch):
    import json

    from code_forge.rulepack import RuleMeta, RulepackManifest, RulepackRunner

    _base(tmp_path, {"gone.js": b"document.write('old');\n", "live.js": b"old();\n"})
    _git(tmp_path, "rm", "--cached", "gone.js")
    (tmp_path / "live.js").write_bytes(b"live();\n")
    _git(tmp_path, "add", "live.js")
    diff = _git(tmp_path, "diff", "--cached")
    files = [Path(name) for name in get_changed_files(diff)]
    rule = RuleMeta(
        id="no-document-write",
        title="test",
        category="test",
        impact_tier="P2",
        languages=["javascript"],
        source="fixture",
    )
    pack = RulepackManifest(
        name="fixture",
        rules=[rule],
        rules_yaml_path=tmp_path / "rules.yaml",
        meta_yaml_path=tmp_path / "meta.yaml",
    )
    advisory = RulepackRunner()
    machine = _machine(tmp_path, diff, files, advisory_runners=[advisory])
    monkeypatch.setattr(
        "code_forge.gate_check.load_gate_config",
        lambda _: {
            "rulepacks": ["fixture"],
            "rulepacks_blocking": ["no-document-write"],
        },
    )
    monkeypatch.setattr("code_forge.rulepack.resolve_active_packs", lambda *_: [pack])
    monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: "/fixture/semgrep")
    monkeypatch.setattr(RulepackRunner, "_print_summary", lambda _: None)
    calls = []

    def scanner(argv, **kwargs):
        selected = argv[argv.index("--jobs") + 2 :]
        calls.append(selected)
        rows = [
            {
                "ruleId": "no-document-write",
                "level": "warning",
                "message": {"text": "retained local bytes"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": name},
                            "region": {"startLine": 1},
                        }
                    }
                ],
            }
            for name in selected
            if name == "gone.js"
        ]
        sarif = {
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": rows}],
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(sarif), "")

    monkeypatch.setattr("code_forge.rulepack.subprocess.run", scanner)
    assert machine._run_rulepack_blocking_phase() == []
    matrix_path = tmp_path / ".code-forge/rulepack-matrix.json"
    initial = matrix_path.read_bytes()
    machine._run_advisory_axes()
    assert calls == [["live.js"], ["live.js"]]
    assert matrix_path.read_bytes() == initial
    assert machine._advisories == []
    assert machine.resolved_review.git_diff == diff
    assert machine._source_files() == files


def test_bare_relative_path_exec_and_version_keep_caller_identity(tmp_path):
    import json
    import os

    launcher = tmp_path / "launcher"
    repo = tmp_path / "project"
    for directory, marker in [(launcher, "caller"), (repo, "source")]:
        (directory / "bin").mkdir(parents=True)
        executable = directory / "bin/inspect"
        executable.write_text(
            "#!/usr/bin/python3\nimport json, os, sys\n"
            "if '--version' in sys.argv: print('" + marker + "')\n"
            "else: print(json.dumps({'marker':'"
            + marker
            + "','cwd':os.getcwd(),'files':sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
    source = Path(__file__).resolve().parents[1] / "src"
    program = f"""import json
from pathlib import Path
from code_forge.registry import ToolConfig
from code_forge.runner import run_tools
def config(command):
    return ToolConfig(name='inspect', command=command, args=[], output_format='eslint_json', file_patterns=['*.js'])
print(json.dumps([run_tools({{'inspect':config(cmd)}}, ['live.js'], cwd=cwd)
    for cmd, cwd in [('inspect',Path({str(repo)!r})), ('bin/inspect',Path({str(repo)!r})), ('inspect',None)]]))
"""
    result = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", program],
        cwd=launcher,
        env={**os.environ, "PATH": "bin", "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    outputs = json.loads(result.stdout)
    for (results, versions, skipped, infra), marker, cwd in zip(
        outputs,
        ["source", "source", "caller"],
        [repo, repo, launcher],
        strict=True,
    ):
        assert skipped == [] and infra == []
        assert versions == {"inspect": marker}
        stdout, code, stderr = results["inspect"]
        assert code == 0 and stderr == ""
        assert json.loads(stdout) == {"marker": marker, "cwd": str(cwd), "files": ["live.js"]}


def test_factory_binds_actual_mutation_baseline_cwd(tmp_path, monkeypatch):
    from code_forge import factories, mutation

    repo = tmp_path / "project"
    repo.mkdir()
    diff, paths = _retained_deletion_with_live(repo, "py")
    state = tmp_path / "state"
    state.mkdir()
    shutil.copytree(repo / ".code-forge", state / ".code-forge")
    files = [repo / p for p in paths]
    baseline_calls = []
    monkeypatch.setattr(factories.shutil, "which", lambda _: "/fixture/mutmut")

    def baseline(argv, **kwargs):
        baseline_calls.append((argv, kwargs.get("cwd")))
        return subprocess.CompletedProcess(argv, 1, "controlled baseline failure", "")

    monkeypatch.setattr(mutation, "run_owned_command", baseline)
    machine = _machine(
        state,
        diff,
        files,
        source_root=repo,
        l2_runner=factories.build_l2_runner(cwd=repo),
    )
    findings = machine._run_l2_phase()
    assert baseline_calls == [(["echo", "ok"], str(repo))]
    assert findings and findings[0].fingerprint == "mutation-flaky"
    assert machine._source_files() == files


def test_cli_binds_mutation_factory_to_review_repository(tmp_path, monkeypatch):
    from code_forge import cli, factories
    from code_forge.llm_invoke import Usage
    from code_forge.state import Verdict

    calls = []
    monkeypatch.setattr(factories.shutil, "which", lambda _: "/fixture/mutmut")
    monkeypatch.setattr(
        factories, "run_mutation", lambda files, command, **kw: (calls.append(kw) or [], [])
    )

    class ScopeMachine(StateMachine):
        def run(self):
            self.l2_runner(["live.py"], ["echo", "ok"], baseline_timeout=37)
            return Verdict.PASS

    monkeypatch.setattr(cli, "StateMachine", ScopeMachine)
    verdict = cli._run_hold_loop(
        mode=Mode.LOCAL,
        falsifier=None,
        autofixer=None,
        revert_fn=lambda _: None,
        l1_provider=lambda: ([], [], Usage(), 0.0),
        resolved=ResolvedReview([Path("live.py")], None, "", "git"),
        source_hash="fixture",
        baseline_repr="HEAD..INDEX",
        cwd=tmp_path,
        registry={},
        max_rounds=1,
        max_fix_attempts=1,
        state_path=tmp_path / "state.json",
    )
    assert verdict == Verdict.PASS
    assert calls == [{"cwd": tmp_path, "baseline_timeout": 37}]


def test_ci_execution_root_preserves_private_result_ownership(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    diff, paths = _retained_deletion_with_live(repo, "py")
    state = tmp_path / "state"
    state.mkdir()
    shutil.copytree(repo / ".code-forge", state / ".code-forge")
    files = [repo / p for p in paths]
    machine = _machine(state, diff, files, source_root=repo)
    calls = []
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    monkeypatch.setattr(StateMachine, "_write_ci_ledger_rows", lambda self: None)
    monkeypatch.setattr(shutil, "which", lambda _: "/fixture/mutmut")
    monkeypatch.setattr(
        "code_forge.machine.launch_detached_mutation",
        lambda files, command, cwd, result_path, *a, **kw: (
            calls.append((files, cwd, result_path)) or True
        ),
    )
    machine._run_ci()
    assert calls == [([str(repo / "live.py")], repo, state / ".code-forge" / "mutation-result.json")]
    assert (state / ".code-forge" / "state.json").is_file()
    assert not (repo / ".code-forge" / "state.json").exists()


def test_rulepack_scanner_root_preserves_private_config_and_matrix(tmp_path, monkeypatch):
    import json

    repo = tmp_path / "project"
    repo.mkdir()
    diff, paths = _retained_deletion_with_live(repo, "js")
    state = tmp_path / "state"
    state.mkdir()
    shutil.move(str(repo / ".code-forge"), str(state / ".code-forge"))
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _: "/fixture/semgrep")

    def scanner(argv, **kwargs):
        calls.append((argv[argv.index("--jobs") + 2 :], kwargs.get("cwd")))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "version": "2.1.0",
                    "runs": [{"tool": {"driver": {"name": "semgrep"}}, "results": []}],
                }
            ),
            "",
        )

    monkeypatch.setattr("code_forge.rulepack.subprocess.run", scanner)
    machine = _machine(state, diff, paths, source_root=repo)
    assert machine._run_rulepack_blocking_phase() == []
    assert calls == [(["live.js"], str(repo))]
    assert (state / ".code-forge" / "rulepack-matrix.json").is_file()
    assert not (repo / ".code-forge").exists()
    assert machine._state.infra_errors == []


def test_missing_mutation_adapter_note_uses_explicit_source_root(tmp_path, monkeypatch):
    from code_forge import factories

    seen = []
    monkeypatch.setattr(factories.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        "code_forge.mutation_dispatch.other_adapter_note",
        lambda files, root: seen.append(root) or "adapter unavailable",
    )
    findings, infra = factories.build_l2_runner(cwd=tmp_path)(["app.ts"], [])
    assert seen == [tmp_path]
    assert findings[0].description == "adapter unavailable"
    assert infra == []


def _retained_transaction_fixture(tmp_path, monkeypatch):
    _base(tmp_path, {"src/gone.py": b"previous = True\n", "src/model.py": b"value = 1\n"})
    _git(tmp_path, "rm", "--cached", "src/gone.py")
    retained = tmp_path / "src/gone.py"
    retained.write_bytes(b"LOCAL_ONLY_SENTINEL\n")
    retained.chmod(0o751)
    live = tmp_path / "src/model.py"
    live.write_bytes(b"value = 2\n")
    live.chmod(0o754)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_live.py").write_text(
        "from model import value\ndef test_live(): assert value == 2\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "src/model.py", "tests/test_live.py")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    files = [tmp_path / name for name in get_changed_files(diff)]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    return machine, diff, files, _retained_identity(retained), (tmp_path / ".git/index").read_bytes()


@pytest.mark.parametrize(
    "failure",
    [
        "reverse",
        "test_oserror",
        "test_timeout",
        "test_cancel",
        "forward",
        "forward_cancel",
        "private_reverse_omitted",
        "post_reverse_same_bytes",
        "post_reverse_foreign_bytes",
        "post_forward_same_bytes",
        "pre_forward_same_bytes",
        "foreign_source",
        "foreign_retained",
        "parent_swap",
        "cleanup_foreign",
        "directory_swap",
    ],
)
def test_real_fixval_transaction_error_never_passes_or_overwrites_foreign(
    tmp_path,
    monkeypatch,
    failure,
):
    from code_forge.state import Verdict

    machine, diff, files, retained_before, index_before = _retained_transaction_fixture(
        tmp_path, monkeypatch
    )
    real_run = subprocess.run
    calls = []
    test_calls = 0
    foreign = None
    recovery_original = None

    def exercise(argv, **kwargs):
        nonlocal test_calls, foreign, recovery_original
        calls.append(list(argv))
        is_test = "pytest" in argv
        if is_test:
            test_calls += 1
        if is_test and test_calls == 4 and failure in {"test_oserror", "test_timeout", "test_cancel"}:
            if failure == "test_oserror":
                raise OSError("injected test process failure")
            if failure == "test_timeout":
                raise subprocess.TimeoutExpired(argv, 600)
            raise KeyboardInterrupt("injected test cancellation")
        is_reverse = argv[:3] == ["git", "apply", "-R"]
        is_forward = argv[:2] == ["git", "apply"] and not is_reverse
        if is_reverse and failure == "reverse":
            patch = Path(argv[-1])
            patch.write_text(patch.read_text().replace("value = 2", "value = 999"))
        if is_forward and failure == "forward":
            patch = Path(argv[-1])
            patch.write_text(patch.read_text().replace("value = 1", "value = 999"))
        if is_forward and failure == "forward_cancel":
            raise KeyboardInterrupt("injected forward cancellation")
        if is_forward and failure == "pre_forward_same_bytes":
            path = tmp_path / "src/model.py"
            replacement = tmp_path / "same-byte-writer"
            replacement.write_bytes(b"value = 1\n")
            replacement.chmod(0o711)
            replacement.replace(path)
            foreign = path, _retained_identity(path)
        result = real_run(argv, **kwargs)
        if is_reverse and failure == "private_reverse_omitted":
            assert Path(kwargs["cwd"]).resolve().name.startswith(".fixval-image-")
            (Path(kwargs["cwd"]).resolve() / "src/model.py").unlink()
        if is_reverse and failure in {"post_reverse_same_bytes", "post_reverse_foreign_bytes"}:
            path = tmp_path / "src/model.py"
            replacement = tmp_path / "reverse-writer"
            replacement.write_bytes(
                b"value = 1\n" if failure == "post_reverse_same_bytes" else b"value = 99\n"
            )
            replacement.chmod(0o711)
            replacement.replace(path)
            foreign = path, _retained_identity(path)
        if is_forward and failure == "post_forward_same_bytes":
            path = tmp_path / "src/model.py"
            replacement = tmp_path / "same-byte-writer"
            replacement.write_bytes(b"value = 2\n")
            replacement.chmod(0o711)
            replacement.replace(path)
            foreign = path, _retained_identity(path)
        if is_test and test_calls == 4:
            assert result.returncode == 1, (
                "control must reach the real RED test before recovery injection"
            )
            if failure in {"foreign_source", "foreign_retained"}:
                path = tmp_path / ("src/model.py" if failure == "foreign_source" else "src/gone.py")
                path.unlink()
                path.write_bytes(b"NEW_FOREIGN_ENTRY\n")
                path.chmod(0o711)
                foreign = path, _retained_identity(path)
            elif failure == "parent_swap":
                (tmp_path / "src").rename(tmp_path / "detached-src")
                (tmp_path / "src").mkdir()
                for name in ["model.py", "gone.py"]:
                    (tmp_path / "src" / name).write_bytes(b"FOREIGN_DIRECTORY_ENTRY\n")
                foreign = [(path, _retained_identity(path)) for path in (tmp_path / "src").iterdir()]
            elif failure in {"cleanup_foreign", "directory_swap"}:
                (recovery,) = _recovery_paths(tmp_path)
                if failure == "directory_swap":
                    recovery_original = tmp_path / "moved-recovery"
                    recovery.rename(recovery_original)
                    recovery.mkdir()
                injected = recovery / "foreign"
                injected.write_bytes(b"FOREIGN_RECOVERY_ENTRY\n")
                foreign = injected, _retained_identity(injected)
        return result

    monkeypatch.setattr(subprocess, "run", exercise)
    if failure in {"test_cancel", "forward_cancel"}:
        with pytest.raises(KeyboardInterrupt, match="injected"):
            machine._finalize_local_terminal()
    else:
        machine._finalize_local_terminal()
        assert machine._state.verdict == Verdict.FAIL
        assert not machine._state.converged
        (finding,) = [f for f in machine._state.findings if f.id == "FIXVAL_TRANSACTION"]
        assert finding.error and "Recovery:" in finding.error
    assert any(argv[:3] == ["git", "apply", "-R"] for argv in calls)
    assert test_calls >= (
        3
        if failure
        in {
            "reverse",
            "private_reverse_omitted",
            "post_reverse_same_bytes",
            "post_reverse_foreign_bytes",
        }
        else 4
    )
    retained = tmp_path / ("detached-src/gone.py" if failure == "parent_swap" else "src/gone.py")
    live = tmp_path / ("detached-src/model.py" if failure == "parent_swap" else "src/model.py")
    if failure != "foreign_retained":
        assert _retained_identity(retained) == retained_before
    else:
        (recovery,) = _recovery_paths(tmp_path)
        assert any(_retained_identity(path) == retained_before for path in recovery.iterdir())
    if failure not in {
        "foreign_source",
        "post_forward_same_bytes",
        "pre_forward_same_bytes",
        "post_reverse_same_bytes",
        "post_reverse_foreign_bytes",
    }:
        assert live.read_bytes() == b"value = 2\n"
        assert live.stat().st_mode & 0o777 == 0o754
    if failure == "parent_swap":
        assert all(_retained_identity(path) == identity for path, identity in foreign)
    elif foreign is not None:
        assert _retained_identity(foreign[0]) == foreign[1]
    if recovery_original is not None:
        assert recovery_original.exists()
        assert str(recovery_original) in finding.error
    if failure == "test_cancel":
        assert not list(_recovery_paths(tmp_path))
    else:
        assert list(_recovery_paths(tmp_path))
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert machine._source_files() == files


@pytest.mark.parametrize(
    "kind", ["binary_delete", "binary_retained", "binary_edit", "rename", "mode", "type_change"]
)
@pytest.mark.parametrize("hollow", [False, True])
def test_real_fixval_preserves_production_metadata_and_binary_blocks(
    tmp_path, monkeypatch, kind, hollow
):
    from code_forge.state import Verdict

    _base(tmp_path, {"src/model.py": b"value = 1\n", "src/data.bin": b"\x00original\xff\n"})
    live = tmp_path / "src/model.py"
    live.write_bytes(b"value = 2\n")
    live.chmod(0o751)
    data = tmp_path / "src/data.bin"
    retained_before = None
    if kind in {"binary_delete", "binary_retained"}:
        _git(tmp_path, "rm", "--cached" if kind == "binary_retained" else "-f", "src/data.bin")
        if kind == "binary_retained":
            data.write_bytes(b"\x00retained-foreign\xff\n")
            data.chmod(0o713)
            retained_before = _retained_identity(data)
    elif kind == "binary_edit":
        data.write_bytes(b"\x00updated\xfe\n")
    elif kind == "rename":
        _git(tmp_path, "mv", "src/data.bin", "src/renamed.bin")
    elif kind == "mode":
        data.chmod(0o751)
    else:
        data.unlink()
        data.symlink_to("model.py")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_live.py").write_text(
        "def test_live(): assert True\n"
        if hollow
        else "from model import value\ndef test_live(): assert value == 2\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "--all")
    # Re-adding retained bytes would erase the semantic deletion under test.
    if kind == "binary_retained":
        _git(tmp_path, "rm", "--cached", "src/data.bin")
    diff = _git(tmp_path, "diff", "--cached", "--binary", "--find-renames")
    files = [tmp_path / name for name in get_changed_files(diff)]
    machine = _terminal_fixture(tmp_path, tmp_path, diff, files, monkeypatch)
    before = {
        path.name: (
            path.is_symlink(),
            path.readlink() if path.is_symlink() else path.read_bytes(),
            path.lstat().st_mode,
        )
        for path in (tmp_path / "src").iterdir()
    }
    index_before = (tmp_path / ".git/index").read_bytes()
    calls = []
    real_run = subprocess.run

    def capture(argv, **kwargs):
        if "pytest" in argv and len([call for call in calls if "pytest" in call[0]]) == 3:
            assert live.read_bytes() == b"value = 1\n"
            assert data.read_bytes() == b"\x00original\xff\n"
            assert not data.is_symlink()
            assert not (tmp_path / "src/renamed.bin").exists()
        result = real_run(argv, **kwargs)
        calls.append((argv, result.returncode))
        return result

    monkeypatch.setattr(subprocess, "run", capture)
    machine._finalize_local_terminal()
    assert machine._state.verdict == (Verdict.FAIL if hollow else Verdict.PASS)
    assert not any(
        f.id in {"FIXVAL_SKIPPED", "FIXVAL_TRANSACTION", "MUTATION_SKIPPED"}
        for f in machine._state.findings
    )
    assert [code for argv, code in calls if "pytest" in argv][:4] == [0, 0, 0, 0 if hollow else 1]
    after = {
        path.name: (
            path.is_symlink(),
            path.readlink() if path.is_symlink() else path.read_bytes(),
            path.lstat().st_mode,
        )
        for path in (tmp_path / "src").iterdir()
    }
    assert after == before
    if retained_before is not None:
        assert _retained_identity(data) == retained_before
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert _git(tmp_path, "diff", "--cached", "--binary", "--find-renames") == diff
    assert machine._source_files() == files
    assert not list(_recovery_paths(tmp_path))


@pytest.mark.parametrize(
    "test_name",
    [
        "test_real_eslint_keeps_live_findings_and_missing_path_errors",
        "test_real_ruff_preserves_live_undefined_name",
        "test_native_eslint_uses_source_root_with_private_state",
    ],
)
def test_optional_native_tool_unavailable_has_explicit_skip(tmp_path, monkeypatch, test_name):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    test = globals()[test_name]
    with pytest.raises(pytest.skip.Exception, match="executable unavailable"):
        if test_name == "test_real_eslint_keeps_live_findings_and_missing_path_errors":
            test(tmp_path, monkeypatch, missing=False, retained=False)
        elif test_name == "test_real_ruff_preserves_live_undefined_name":
            test(tmp_path, monkeypatch)
        else:
            test(tmp_path, monkeypatch, missing=False)
    assert not list(tmp_path.iterdir())


def _r1_identity(path):
    meta = path.lstat()
    return (
        meta.st_dev,
        meta.st_ino,
        meta.st_mode,
        str(path.readlink()) if path.is_symlink() else path.read_bytes(),
    )


def _r1_index(repo):
    return (repo / ".git/index").read_bytes()


def test_r1_real_added_directory_reverse_restore(tmp_path):
    _base(tmp_path, {"tests/test_new.py": b"pass\n"})
    model = tmp_path / "newpkg/model.py"
    model.parent.mkdir()
    model.parent.chmod(0o751)
    model.write_bytes(b"answer = 42\n")
    model.chmod(0o751)
    _git(tmp_path, "add", "newpkg/model.py")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    index = _r1_index(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    command = [
        sys.executable,
        "-B",
        "-c",
        "import pathlib,sys;sys.exit(0 if pathlib.Path('newpkg/model.py').exists() else 1)",
    ]
    result = run_fixval(
        FixvalCandidate(["tests/test_new.py"], ["newpkg/model.py"]),
        command,
        tmp_path,
        "new package",
        diff,
    )
    assert result.status == FixvalStatus.PASS, result.block_message
    assert model.read_bytes() == b"answer = 42\n"
    assert stat.S_IMODE(model.stat().st_mode) == 0o751
    assert stat.S_IMODE(model.parent.stat().st_mode) == 0o751
    assert _r1_index(tmp_path) == index
    assert _git(tmp_path, "rev-parse", "HEAD") == head
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert not list(_recovery_paths(tmp_path))


@pytest.mark.parametrize("prefix", ["old \t", "\u65e7 \u6587\u4ef6 \t"])
def test_r1_real_quoted_rename_rejects_same_byte_foreign_inode(tmp_path, prefix):
    old = "src/" + prefix + "name.py"
    new = "src/new \tname.py"
    _base(tmp_path, {old: b"answer = 42\n"})
    _git(tmp_path, "mv", old, new)
    diff = _git(tmp_path, "diff", "--cached", "--binary", "--find-renames")
    index = _r1_index(tmp_path)
    transaction = FixvalTransaction(tmp_path, diff)
    transaction.prepare()
    patch = tmp_path / "rename.patch"
    patch.write_text(diff)
    assert transaction.reverse(str(patch)).returncode == 0
    transaction.mark_reverted()
    source = tmp_path / old
    displaced = source.with_name("original-reverted")
    source.rename(displaced)
    source.write_bytes(displaced.read_bytes())
    foreign = _r1_identity(source)
    can_forward = transaction.can_apply_forward()
    assert not can_forward, "actual Git forward accepted an unbound foreign rename source"
    errors = transaction.restore(can_forward)
    transaction.recovery_needed = bool(errors) or not can_forward
    transaction.close()
    assert _r1_identity(source) == foreign
    assert displaced.read_bytes() == b"answer = 42\n"
    assert _r1_index(tmp_path) == index


@pytest.mark.parametrize("explicit", [False, True])
def test_r1_real_mutation_cli_excludes_retained_cached_deletion(tmp_path, monkeypatch, explicit):
    _base(tmp_path, {"src/gone.py": b"old = 1\n", "src/live.py": b"value = 1\n"})
    _git(tmp_path, "rm", "--cached", "src/gone.py")
    gone = tmp_path / "src/gone.py"
    gone.write_bytes(b"PRIVATE_FOREIGN = 999\n")
    retained = _r1_identity(gone)
    (tmp_path / "src/live.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "src/live.py")
    diff = _git(tmp_path, "diff", "--binary", "HEAD")
    assert get_changed_files(diff) == ["src/gone.py", "src/live.py"]
    patch = tmp_path / "change.diff"
    patch.write_text(diff)
    argv = ["mutation-check", "--diff", str(patch)] if explicit else ["mutation-check"]
    captured = []
    monkeypatch.setattr(
        "code_forge.mutation.run_mutation", lambda **kwargs: (captured.append(kwargs) or [], [])
    )
    index = _r1_index(tmp_path)
    assert _run_mutation_check(_build_parser().parse_args(argv), tmp_path) == 0
    assert captured[0]["diff_files"] == ["src/live.py"], "semantic deletion reached mutation execution"
    assert _r1_identity(gone) == retained
    assert _r1_index(tmp_path) == index


@pytest.mark.parametrize("parent", ["newpkg", "newpkg/nested"])
@pytest.mark.parametrize("hollow", [False, True])
@pytest.mark.parametrize("forward_failure", [False, True])
def test_r1_real_nested_added_directory_restores_all_modes(
    tmp_path, monkeypatch, parent, hollow, forward_failure
):
    _base(tmp_path, {"tests/test_new.py": b"pass\n"})
    model = tmp_path / parent / "model.py"
    model.parent.mkdir(parents=True)
    directories = [tmp_path / "newpkg"]
    if parent != "newpkg":
        directories.append(model.parent)
    for directory in directories:
        directory.chmod(0o751)
    model.write_bytes(b"answer = 42\n")
    model.chmod(0o711)
    _git(tmp_path, "add", "newpkg")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    index = _r1_index(tmp_path)
    real_run = subprocess.run
    calls = []

    def observe(argv, **kwargs):
        if argv[:3] == ["git", "apply", "--check"] and len(argv) == 4 and forward_failure:
            result = subprocess.CompletedProcess(argv, 1, "", "injected forward failure")
        else:
            result = real_run(argv, **kwargs)
        calls.append((argv, result.returncode))
        return result

    monkeypatch.setattr(subprocess, "run", observe)
    command = [
        sys.executable,
        "-B",
        "-c",
        "import pathlib,sys;sys.exit(0)"
        if hollow
        else "import pathlib,sys;sys.exit(0 if pathlib.Path(%r).exists() else 1)"
        % (parent + "/model.py"),
    ]
    result = run_fixval(
        FixvalCandidate(["tests/test_new.py"], [parent + "/model.py"]),
        command,
        tmp_path,
        "new nested package",
        diff,
    )
    assert result.status == (FixvalStatus.BLOCK if hollow or forward_failure else FixvalStatus.PASS)
    assert model.read_bytes() == b"answer = 42\n"
    assert stat.S_IMODE(model.stat().st_mode) == 0o711
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o751 for path in directories)
    assert _r1_index(tmp_path) == index
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert [code for argv, code in calls if argv[0] == sys.executable] == [0, 0, 0, 0 if hollow else 1]
    if forward_failure:
        assert any(f.id == "FIXVAL_TRANSACTION" for f in result.findings)
        assert list(_recovery_paths(tmp_path))
    else:
        assert not list(_recovery_paths(tmp_path))


@pytest.mark.parametrize("kind", ["regular", "symlink", "directory"])
@pytest.mark.parametrize("phase", ["before_mark", "before_forward", "publish_race", "publish_swap"])
def test_r1_removed_directory_rejects_foreign_namespace(tmp_path, monkeypatch, kind, phase):
    _base(tmp_path, {"tests/test_new.py": b"pass\n"})
    model = tmp_path / "newpkg/model.py"
    model.parent.mkdir()
    model.write_bytes(b"answer = 42\n")
    _git(tmp_path, "add", "newpkg")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    patch = tmp_path / "add.patch"
    patch.write_text(diff)
    index = _r1_index(tmp_path)
    transaction = FixvalTransaction(tmp_path, diff)
    transaction.prepare()
    assert transaction.reverse(str(patch)).returncode == 0
    target = tmp_path / "symlink-target"
    target.mkdir()
    (target / "sentinel").write_bytes(b"FOREIGN_TARGET\n")
    foreign = None

    def inject():
        nonlocal foreign
        path = model.parent
        if kind == "regular":
            path.write_bytes(b"FOREIGN_PARENT\n")
        elif kind == "symlink":
            path.symlink_to(target, target_is_directory=True)
        else:
            path.mkdir()
            (path / "sentinel").write_bytes(b"FOREIGN_DIRECTORY\n")
        meta = path.lstat()
        foreign = (
            meta.st_dev,
            meta.st_ino,
            meta.st_mode,
            str(path.readlink()) if path.is_symlink() else None,
        )

    if phase == "before_mark":
        inject()
        with pytest.raises((TransactionError, OSError)):
            transaction.mark_reverted()
    else:
        transaction.mark_reverted()
        if phase == "before_forward":
            inject()
        else:
            real_rename = transaction._rename_between

            def race(source_fd, source, target_fd, name):
                if phase == "publish_race" and source.startswith(".parent-") and foreign is None:
                    inject()
                real_rename(source_fd, source, target_fd, name)
                if phase == "publish_swap" and source.startswith(".parent-") and foreign is None:
                    model.parent.rename(tmp_path / "displaced-created")
                    inject()

            monkeypatch.setattr(transaction, "_rename_between", race)
        with pytest.raises((TransactionError, OSError)):
            transaction.can_apply_forward()
    errors = transaction.restore(False)
    assert errors
    transaction.recovery_needed = True
    transaction.close()
    meta = model.parent.lstat()
    assert (
        meta.st_dev,
        meta.st_ino,
        meta.st_mode,
        str(model.parent.readlink()) if model.parent.is_symlink() else None,
    ) == foreign
    if kind == "regular":
        assert model.parent.read_bytes() == b"FOREIGN_PARENT\n"
    elif kind == "directory":
        assert (model.parent / "sentinel").read_bytes() == b"FOREIGN_DIRECTORY\n"
        assert not model.exists()
    assert (target / "sentinel").read_bytes() == b"FOREIGN_TARGET\n"
    assert _r1_index(tmp_path) == index
    assert (transaction.directory / "0").read_bytes() == b"answer = 42\n"


@pytest.mark.parametrize("presentation", [None, "color.ui", "diff.mnemonicPrefix", "diff.noprefix"])
def test_mutation_cli_git_presentation_preserves_actual_source_scope(
    tmp_path, monkeypatch, presentation
):
    _base(tmp_path, {"value.py": b"value = 1\n", "gone.py": b"gone = True\n"})
    _git(tmp_path, "rm", "--cached", "gone.py")
    (tmp_path / "gone.py").write_bytes(b"FOREIGN_RETAINED = 999\n")
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    if presentation:
        _git(tmp_path, "config", presentation, "always" if presentation == "color.ui" else "true")
    protected = {name: (tmp_path / ".git" / name).read_bytes() for name in ("index", "HEAD", "config")}
    retained = _r1_identity(tmp_path / "gone.py")
    captured = []
    monkeypatch.setattr(
        "code_forge.mutation.run_mutation", lambda **kwargs: (captured.append(kwargs) or [], [])
    )
    real_run = subprocess.run

    def capture_packet(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv[:2] == ["git", "diff"]:
            (tmp_path / ".git/mutation-output.patch").write_text(result.stdout, encoding="utf-8")
        return result

    monkeypatch.setattr(subprocess, "run", capture_packet)
    assert _run_mutation_check(_build_parser().parse_args(["mutation-check"]), tmp_path) == 0
    assert len(captured) == 1
    assert captured[0]["diff_files"] == ["value.py"], "Git presentation changed mutation scope"
    assert _r1_identity(tmp_path / "gone.py") == retained
    assert {name: (tmp_path / ".git" / name).read_bytes() for name in protected} == protected


@pytest.mark.parametrize("producer", ["refs", "index", "working"])
@pytest.mark.parametrize("hollow", [False, True])
def test_diff_producer_binary_packet_reaches_real_fixval(tmp_path, monkeypatch, producer, hollow):
    from code_forge.fixval import classify_fixval_candidate
    from code_forge.git import cached_diff, git_diff, working_tree_diff

    original = b"\x00original\xff\n"
    updated = b"\x00updated\xfe\n"
    _base(tmp_path, {"src/data.bin": original})
    baseline = _git(tmp_path, "rev-parse", "HEAD").strip()
    (tmp_path / "src/data.bin").write_bytes(updated)
    test = tmp_path / "tests/test_live.py"
    test.parent.mkdir()
    test.write_text(
        "def test_live(): assert True\n"
        if hollow
        else "from pathlib import Path\ndef test_live(): assert Path('src/data.bin').read_bytes() == %r\n"
        % updated,
        encoding="utf-8",
    )
    if producer in {"refs", "index"}:
        _git(tmp_path, "add", "--all")
    else:
        _git(tmp_path, "add", "tests/test_live.py")
    if producer == "refs":
        _git(tmp_path, "commit", "-q", "-m", "binary change with test")
        packet = git_diff(baseline, "HEAD", [Path(".")], tmp_path)
    elif producer == "index":
        packet = cached_diff(baseline, [Path(".")], tmp_path)
    else:
        packet = working_tree_diff(baseline, [Path(".")], tmp_path)
    (tmp_path / ".git/fixval-input.patch").write_text(packet, encoding="utf-8")
    candidate = classify_fixval_candidate(get_changed_files(packet))
    assert isinstance(candidate, FixvalCandidate)
    assert candidate.non_test_files == ["src/data.bin"]
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    protected = {name: (tmp_path / ".git" / name).read_bytes() for name in ("index", "HEAD", "config")}
    before = {
        name: ((tmp_path / name).lstat().st_mode, (tmp_path / name).read_bytes())
        for name in ("src/data.bin", "tests/test_live.py")
    }
    result = run_fixval(
        candidate,
        ["/usr/bin/python3", "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        tmp_path,
        "",
        packet,
    )
    assert result.status == (FixvalStatus.BLOCK if hollow else FixvalStatus.PASS)
    assert not any(f.id in {"FIXVAL_SKIPPED", "FIXVAL_TRANSACTION"} for f in result.findings)
    assert {
        name: ((tmp_path / name).lstat().st_mode, (tmp_path / name).read_bytes()) for name in before
    } == before
    assert {name: (tmp_path / ".git" / name).read_bytes() for name in protected} == protected
    assert not list(_recovery_paths(tmp_path))


def test_r1_removed_directory_rejects_replaced_bound_ancestor(tmp_path):
    _base(tmp_path, {"src/existing.py": b"old\n"})
    model = tmp_path / "src/newpkg/model.py"
    model.parent.mkdir()
    model.write_bytes(b"answer = 42\n")
    _git(tmp_path, "add", "src/newpkg")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    patch = tmp_path / "add.patch"
    patch.write_text(diff)
    transaction = FixvalTransaction(tmp_path, diff)
    transaction.prepare()
    assert transaction.reverse(str(patch)).returncode == 0
    transaction.mark_reverted()
    (tmp_path / "src").rename(tmp_path / "detached-src")
    (tmp_path / "src").mkdir()
    sentinel = tmp_path / "src/foreign.py"
    sentinel.write_bytes(b"FOREIGN_ANCESTOR\n")
    foreign = _r1_identity(sentinel)
    with pytest.raises(TransactionError, match="source directory changed"):
        transaction.can_apply_forward()
    assert transaction.restore(False)
    transaction.recovery_needed = True
    transaction.close()
    assert _r1_identity(sentinel) == foreign
    assert not model.exists()
    assert (transaction.directory / "0").read_bytes() == b"answer = 42\n"


@pytest.mark.parametrize("prefix", ["old space", "old \t", "\u65e7 \u6587\u4ef6 \t"])
def test_r1_real_quoted_rename_positive_roundtrip(tmp_path, prefix):
    old = "src/" + prefix + "name.py"
    new = "src/new \tname.py"
    _base(tmp_path, {old: b"answer = 42\n"})
    _git(tmp_path, "mv", old, new)
    (tmp_path / new).chmod(0o751)
    _git(tmp_path, "add", new)
    diff = _git(tmp_path, "diff", "--cached", "--binary", "--find-renames")
    patch = tmp_path / "rename.patch"
    patch.write_text(diff)
    index = _r1_index(tmp_path)
    transaction = FixvalTransaction(tmp_path, diff)
    assert {entry.path for entry in transaction.entries} == {old, new}
    transaction.prepare()
    assert transaction.reverse(str(patch)).returncode == 0
    transaction.mark_reverted()
    assert transaction.can_apply_forward()
    _git(tmp_path, "apply", "--check", str(patch))
    assert transaction.restore(False) == []
    transaction.close()
    assert not (tmp_path / old).exists()
    assert (tmp_path / new).read_bytes() == b"answer = 42\n"
    assert stat.S_IMODE((tmp_path / new).stat().st_mode) == 0o751
    assert _r1_index(tmp_path) == index
    assert not list(_recovery_paths(tmp_path))


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_r1_real_quoted_rename_rejects_foreign_type(tmp_path, kind):
    old, new = "src/old \tname.py", "src/new \tname.py"
    _base(tmp_path, {old: b"answer = 42\n"})
    _git(tmp_path, "mv", old, new)
    diff = _git(tmp_path, "diff", "--cached", "--binary", "--find-renames")
    patch = tmp_path / "rename.patch"
    patch.write_text(diff)
    transaction = FixvalTransaction(tmp_path, diff)
    transaction.prepare()
    assert transaction.reverse(str(patch)).returncode == 0
    transaction.mark_reverted()
    source = tmp_path / old
    source.rename(source.with_name("displaced-original"))
    if kind == "symlink":
        source.symlink_to("displaced-original")
    else:
        source.mkdir()
        (source / "foreign").write_bytes(b"FOREIGN_RENAME\n")
    meta = source.lstat()
    if kind == "directory":
        with pytest.raises(TransactionError):
            transaction.can_apply_forward()
    else:
        assert not transaction.can_apply_forward(), "foreign rename source accepted"
    assert transaction.restore(False)
    transaction.recovery_needed = True
    transaction.close()
    assert (source.lstat().st_dev, source.lstat().st_ino, source.lstat().st_mode) == (
        meta.st_dev,
        meta.st_ino,
        meta.st_mode,
    )
    if kind == "symlink":
        assert source.readlink() == Path("displaced-original")
    else:
        assert (source / "foreign").read_bytes() == b"FOREIGN_RENAME\n"


@pytest.mark.parametrize("explicit", [False, True])
def test_r1_mutation_cli_retains_empty_positive_and_accounting(tmp_path, monkeypatch, capsys, explicit):
    from code_forge.disposition import Disposition
    from code_forge.exit_codes import EXIT_UNRELIABLE
    from code_forge.state import StateFinding

    _base(tmp_path, {"src/empty.py": b"old = 1\n", "src/gone.py": b"old = 1\n"})
    (tmp_path / "src/empty.py").write_bytes(b"")
    _git(tmp_path, "rm", "--cached", "src/gone.py")
    _git(tmp_path, "add", "src/empty.py")
    diff = _git(tmp_path, "diff", "--binary", "HEAD")
    patch = tmp_path / "change.diff"
    patch.write_text(diff)
    argv = ["mutation-check", "--paths", "src/*.py"]
    if explicit:
        argv.extend(["--diff", str(patch)])
    error = StateFinding(
        id="MUTATION_ERROR",
        fingerprint="mutation-invocation-error",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="src/empty.py",
        line_range=[],
        description="exact mutation error cause",
    )
    captured = []
    monkeypatch.setattr(
        "code_forge.mutation.run_mutation", lambda **kwargs: (captured.append(kwargs) or [error], [])
    )
    assert _run_mutation_check(_build_parser().parse_args(argv), tmp_path) == EXIT_UNRELIABLE
    assert captured[0]["diff_files"] == ["src/empty.py"]
    output = capsys.readouterr()
    assert "exact mutation error cause" in output.err
    assert "PASS" not in output.err + output.out


def test_r1_parent_publication_rechecks_late_ancestor_identity(tmp_path, monkeypatch):
    _base(tmp_path, {"src/existing.py": b"old\n"})
    model = tmp_path / "src/newpkg/model.py"
    model.parent.mkdir()
    model.write_bytes(b"answer = 42\n")
    _git(tmp_path, "add", "src/newpkg")
    diff = _git(tmp_path, "diff", "--cached", "--binary")
    patch = tmp_path / "add.patch"
    patch.write_text(diff)
    index = _r1_index(tmp_path)
    transaction = FixvalTransaction(tmp_path, diff)
    transaction.prepare()
    assert transaction.reverse(str(patch)).returncode == 0
    transaction.mark_reverted()
    real_rename = transaction._rename_between
    foreign = None

    def publish_then_replace(source_fd, source, target_fd, name):
        nonlocal foreign
        real_rename(source_fd, source, target_fd, name)
        if source.startswith(".parent-") and foreign is None:
            (tmp_path / "src").rename(tmp_path / "detached-src")
            (tmp_path / "src").mkdir()
            sentinel = tmp_path / "src/foreign.py"
            sentinel.write_bytes(b"FOREIGN_ANCESTOR\n")
            foreign = _r1_identity(sentinel)

    monkeypatch.setattr(transaction, "_rename_between", publish_then_replace)
    with pytest.raises(TransactionError, match="source directory changed"):
        transaction.can_apply_forward()
    assert transaction.restore(False)
    transaction.recovery_needed = True
    transaction.close()
    assert _r1_identity(tmp_path / "src/foreign.py") == foreign
    assert not model.exists(), "no forward patch may write into the foreign namespace"
    assert _r1_index(tmp_path) == index
    assert (transaction.directory / "0").read_bytes() == b"answer = 42\n"


def _relative_ruff(repo):
    installed = shutil.which("ruff")
    if installed is None:
        pytest.skip("real Ruff executable is unavailable")
    (repo / "scripts").mkdir()
    (repo / "scripts/quality").symlink_to(Path(installed).resolve())
    return "scripts/quality"


@pytest.mark.parametrize("flag", ["--fix", "--fix-only"])
def test_relative_ruff_alias_refuses_mutating_detection(tmp_path, flag):
    from code_forge.runner import run_tool

    command = _relative_ruff(tmp_path)
    source = tmp_path / "value.py"
    source.write_bytes(b"import os\n")
    before = _r1_identity(source)
    tool = ToolConfig("quality", command, ["check", "--isolated", "--no-cache", flag], "sarif", ["*.py"])
    assert run_tool(tool, ["value.py"], cwd=tmp_path) == (
        "",
        2,
        "Forge detection refuses Ruff mutating flags",
    )
    assert _r1_identity(source) == before


@pytest.mark.parametrize("setting", ["fix", "fix-only"])
def test_relative_ruff_alias_overrides_mutating_configuration(tmp_path, setting):
    import json
    from code_forge.runner import run_tool

    command = _relative_ruff(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f"[tool.ruff]\n{setting} = true\n", encoding="utf-8")
    source = tmp_path / "value.py"
    source.write_bytes(b"import os\n")
    before = _r1_identity(source)
    tool = ToolConfig(
        "quality",
        command,
        ["check", "--output-format=sarif", "--select=F401", "--no-cache"],
        "sarif",
        ["*.py"],
    )
    output, status, _stderr = run_tool(tool, ["value.py"], cwd=tmp_path)
    assert status == 1
    assert [item["ruleId"] for item in json.loads(output)["runs"][0]["results"]] == ["F401"]
    assert _r1_identity(source) == before


def test_relative_ruff_alias_keeps_machine_abnormal_status_policy(tmp_path, monkeypatch):
    import json
    from code_forge.machine import _default_l0_runner

    command = _relative_ruff(tmp_path)
    tool = ToolConfig("quality", command, ["check"], "sarif", ["*.py"])
    packet = json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "ruff"}},
                    "results": [
                        {
                            "ruleId": "F821",
                            "message": {"text": "undefined name"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "value.py"},
                                        "region": {"startLine": 1},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    def tools(registry, files, *, cwd):
        assert registry == {"quality": tool} and files == ["value.py"] and cwd == tmp_path
        return {"quality": (packet, 2, "abnormal Ruff status")}, {}, [], []

    # This supplied packet distinguishes parser wiring without launching a tool.
    monkeypatch.setattr("code_forge.runner.run_tools", tools)
    findings, errors = _default_l0_runner({"quality": tool}, [Path("value.py")], cwd=tmp_path)
    assert len(findings) == 1 and findings[0].file == "value.py"
    assert errors and all("quality" in error for error in errors)


@pytest.mark.parametrize("operation", ["rename", "copy"])
@pytest.mark.parametrize("test_side", ["source", "target"])
def test_quoted_test_metadata_is_excluded_from_fixval(tmp_path, operation, test_side):
    from code_forge.fixval import _filter_non_test_patch

    old = "tests/test_old \tname.py" if test_side == "source" else "src/old \tname.py"
    new = "src/new \tname.py" if test_side == "source" else "tests/test_new \tname.py"
    _base(tmp_path, {old: b"value = 1\n"})
    (tmp_path / new).parent.mkdir(parents=True, exist_ok=True)
    if operation == "rename":
        (tmp_path / old).rename(tmp_path / new)
    else:
        (tmp_path / new).write_bytes((tmp_path / old).read_bytes())
    _git(tmp_path, "add", "--all")
    patch = _git(
        tmp_path,
        "-c",
        "core.quotePath=true",
        "diff",
        "--cached",
        "-M",
        "-C",
        "--find-copies-harder",
        "--no-ext-diff",
        "--no-textconv",
    )
    assert operation + ' from "' in patch and "\\tname.py" in patch
    assert _filter_non_test_patch(patch) == "", "test metadata reached production reversal"


def test_quoted_renamed_test_cannot_supply_hollow_fixval_pass(tmp_path):
    from code_forge.fixval import classify_fixval_candidate

    old, new = "tests/test_old \tname.py", "tests/test_new \tname.py"
    _base(tmp_path, {"value.py": b"value = 1\n", old: b"def test_hollow():\n    assert True\n"})
    (tmp_path / old).rename(tmp_path / new)
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "--all")
    patch = _git(
        tmp_path, "-c", "core.quotePath=true", "diff", "--cached", "-M", "--no-ext-diff", "--no-textconv"
    )
    before = {
        name: ((tmp_path / name).lstat().st_mode, (tmp_path / name).read_bytes())
        for name in ("value.py", new)
    }
    test_identity = _r1_identity(tmp_path / new)
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        "from pathlib import Path;import sys;raise SystemExit(0 if all(Path(p).is_file() for p in sys.argv[1:]) else 1)",
    ]
    result = run_fixval(classify_fixval_candidate(["value.py", new]), command, tmp_path, "", patch)
    assert result.status == FixvalStatus.BLOCK
    assert {
        name: ((tmp_path / name).lstat().st_mode, (tmp_path / name).read_bytes()) for name in before
    } == before
    assert _r1_identity(tmp_path / new) == test_identity


@pytest.mark.parametrize("producer", ["refs", "index", "working", "untracked", "cli"])
@pytest.mark.parametrize("driver", ["external", "textconv"])
def test_machine_git_diff_ignores_scope_erasing_drivers(tmp_path, monkeypatch, producer, driver):
    from code_forge.git import cached_diff, git_diff, working_tree_diff

    _base(tmp_path, {"value.py": b"value = 1\n"})
    baseline = _git(tmp_path, "rev-parse", "HEAD").strip()
    script = tmp_path / ".git/converter.py"
    journal = tmp_path / ".git/converter-calls"
    script.write_text(
        f"from pathlib import Path\nPath({str(journal)!r}).write_text('called')\nprint('constant output')\n",
        encoding="utf-8",
    )
    if driver == "external":
        _git(tmp_path, "config", "diff.external", f"{sys.executable} -I -S {script}")
    else:
        (tmp_path / ".gitattributes").write_text("*.py diff=scope\n", encoding="utf-8")
        _git(tmp_path, "config", "diff.scope.textconv", f"{sys.executable} -I -S {script}")
        _git(tmp_path, "config", "diff.scope.cachetextconv", "false")
    source = "new.py" if producer == "untracked" else "value.py"
    (tmp_path / source).write_bytes(b"value = 2\n")
    if producer in ("refs", "index"):
        _git(tmp_path, "add", source)
    if producer == "refs":
        _git(tmp_path, "commit", "-qm", "updated fixture")
    before = _r1_identity(tmp_path / source)
    packets = []
    original_run = subprocess.run

    def bounded(argv, **kwargs):
        assert not kwargs.get("shell")
        kwargs.setdefault("timeout", 10)
        result = original_run(argv, **kwargs)
        if list(argv[:2]) == ["git", "diff"]:
            packets.append((list(argv), result.returncode, result.stdout))
        return result

    monkeypatch.setattr(subprocess, "run", bounded)
    if producer == "refs":
        packet = git_diff(baseline, "HEAD", [Path(source)], tmp_path)
    elif producer == "index":
        packet = cached_diff(baseline, [Path(source)], tmp_path)
    elif producer in ("working", "untracked"):
        packet = working_tree_diff(baseline, [Path(source)], tmp_path)
    else:
        captured = []
        monkeypatch.setattr(
            "code_forge.mutation.run_mutation",
            lambda **kwargs: (
                captured.append(kwargs["diff_files"]) or [],
                ["bounded inventory observation"],
            ),
        )
        assert _run_mutation_check(_build_parser().parse_args(["mutation-check"]), tmp_path) != 0
        assert captured == [[source]]
        packet = packets[-1][2]
    assert get_changed_files(packet) == [source]
    assert not journal.exists(), "machine diff invoked the configured driver"
    assert _r1_identity(tmp_path / source) == before
    relevant = packets[-1][0]
    assert "--no-ext-diff" in relevant and "--no-textconv" in relevant
    if producer == "untracked":
        assert "--no-index" in relevant and "/dev/null" in relevant
        assert "+value = 2" in packet and packets[-1][1] == 1


@pytest.mark.parametrize(
    "name",
    ["added.py", "added space.py", "added\tname.py", "added\nname.py", "-new.py", "-added space.py"],
)
@pytest.mark.parametrize("operation", ["addition", "renamed_untracked"])
def test_working_tree_diff_keeps_relative_added_file_context(tmp_path, monkeypatch, name, operation):
    from code_forge.git import working_tree_diff

    _base(tmp_path, {"old.py": b"value = 1\n"})
    if operation == "renamed_untracked":
        (tmp_path / "old.py").rename(tmp_path / name)
    else:
        (tmp_path / name).write_bytes(b"value = 1\n")
    before = _r1_identity(tmp_path / name)
    packet = working_tree_diff("HEAD", [Path(".")], tmp_path)
    expected = sorted((["old.py"] if operation == "renamed_untracked" else []) + [name])
    assert get_changed_files(packet) == expected
    # This independent convention scan does not participate in pathname resolution.
    monkeypatch.setattr("code_forge.conventions.get_digest", lambda _: "")
    post_image, _digest = _assemble_post_image(tmp_path, packet)
    assert "value = 1" in post_image and name in post_image
    assert _r1_identity(tmp_path / name) == before


def test_ci_missing_mutation_adapter_note_uses_explicit_source_root(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    diff, paths = _retained_deletion_with_live(repo, "ts")
    state = tmp_path / "state"
    state.mkdir()
    shutil.move(str(repo / ".code-forge"), str(state / ".code-forge"))
    seen = []
    monkeypatch.setattr(StateMachine, "_execute_round", lambda self, round_index: None)
    monkeypatch.setattr(StateMachine, "_write_ci_ledger_rows", lambda self: None)
    monkeypatch.setattr(
        "code_forge.mutation_dispatch.other_adapter_note",
        lambda files, root: seen.append((files, root)) or "adapter unavailable",
    )
    machine = _machine(state, diff, paths, source_root=repo)
    machine._run_ci()
    assert seen == [(["live.ts"], repo)]
    assert [f.description for f in machine._state.findings] == ["adapter unavailable"]
    assert machine._state.infra_errors == []


@pytest.mark.parametrize("identity", ["git", "plain_dot"])
def test_test_rename_sharing_deleted_destination_cannot_supply_fixval_pass(tmp_path, identity):
    from code_forge._fixval_transaction import TransactionError
    from code_forge.diff import split_diff_for_files
    from code_forge.fixval import _filter_non_test_patch, classify_fixval_candidate

    _base(tmp_path, {"tests/test_old.py": b"value = 1\n"})
    source = tmp_path / "src/helper.py"
    source.parent.mkdir()
    source.symlink_to("missing-target")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "symlink fixture")
    source.unlink()
    (tmp_path / "tests/test_old.py").rename(source)
    (tmp_path / "tests/test_live.py").write_bytes(b"pass\n")
    _git(tmp_path, "add", "--all")
    original = _git(tmp_path, "diff", "--cached", "--binary", "-M")
    deletion = "diff --git " + original.split("diff --git ", 2)[1]
    if identity != "git":
        deletion = deletion[deletion.index("--- ") :]
        alias = {
            "plain_dot": "./src/helper.py",
            "plain_repeat_dot": "././src/helper.py",
            "plain_internal_dot": "a/src/./helper.py",
        }[identity]
        deletion = deletion.replace("--- a/src/helper.py\n", "--- " + alias + "\n")
    packet = deletion + _git(tmp_path, "diff", "--cached", "--binary", "-B", "-M")
    if identity == "git":
        assert "deleted file mode 120000" in packet
    assert "rename from tests/test_old.py" in packet and "rename to src/helper.py" in packet
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=packet,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    if identity == "git":
        assert split_diff_for_files(packet, get_changed_files(packet)) == packet
    before = (source.read_bytes(), source.stat().st_mode, _r1_index(tmp_path))
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        "from pathlib import Path;Path('baseline-called').write_text('called');"
        "import sys;sys.path.insert(0, 'src');import helper",
    ]
    result = run_fixval(
        classify_fixval_candidate(["src/helper.py", "tests/test_live.py"]),
        command,
        tmp_path,
        "",
        packet,
    )
    assert result.status == FixvalStatus.BLOCK
    assert not (tmp_path / "baseline-called").exists()
    assert get_removed_files(packet) == []
    assert get_changed_files(packet) == ["src/helper.py", "tests/test_live.py"]
    with pytest.raises(
        TransactionError, match="production reversal overlaps excluded test paths: src/helper.py"
    ):
        _filter_non_test_patch(packet)
    assert (source.read_bytes(), source.stat().st_mode, _r1_index(tmp_path)) == before
    assert _git(tmp_path, "diff", "--cached", "--binary", "-M") == original


def test_unprefixed_plain_source_keeps_preimage_test_identity(tmp_path):
    import unidiff

    from code_forge.diff import patched_file_path
    from code_forge.fixval import _filter_non_test_patch

    _base(tmp_path, {"src/helper.py": b"value = 2\n"})
    packet = "--- ./tests/test_old.py\n+++ ./src/helper.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=packet,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    entry = unidiff.PatchSet(packet)[0]
    assert patched_file_path(entry, source=True) == "tests/test_old.py"
    assert patched_file_path(entry) == "src/helper.py"
    assert _filter_non_test_patch(packet) == ""


def test_local_mutation_skips_empty_executable_projection(tmp_path, monkeypatch):
    from code_forge import factories
    from code_forge.state import Disposition

    _base(tmp_path, {"gone.py": b"previous = True\n"})
    _git(tmp_path, "rm", "--cached", "gone.py")
    retained = tmp_path / "gone.py"
    retained.write_bytes(b"LOCAL_ONLY_SENTINEL\n")
    packet = _git(tmp_path, "diff", "--cached")
    monkeypatch.setattr(factories.shutil, "which", lambda _: None)
    runner = factories.build_l2_runner(cwd=tmp_path)
    calls = []

    def mutation(files, command, **kwargs):
        calls.append((files, command))
        return runner(files, command, **kwargs)

    # Calibrate the actual missing-adapter fallback before checking dispatch.
    findings, errors = mutation(["gone.py"], ["pytest"])
    assert [finding.id for finding in findings] == ["MUTATION_SKIPPED"]
    assert findings[0].disposition == Disposition.CONFIRMED and errors
    assert calls == [(["gone.py"], ["pytest"])]
    calls.clear()
    machine = _machine(tmp_path, packet, [Path("gone.py")], l2_runner=mutation)
    before = _r1_identity(retained)
    assert machine._run_l2_phase() == []
    assert calls == []
    assert machine._state.infra_errors == []
    assert _r1_identity(retained) == before


@pytest.mark.parametrize("production", ["addition", "deletion"])
@pytest.mark.parametrize("test_side", ["source", "target"])
def test_fixval_overlap_preserves_test_source_and_target_identity(production, test_side):
    from code_forge._fixval_transaction import TransactionError
    from code_forge.fixval import _filter_non_test_patch

    shared = "src/shared.py"
    test = "tests/test_old.py"
    old, new = (test, shared) if test_side == "source" else (shared, test)
    header = f"diff --git a/{shared} b/{shared}\n"
    if production == "addition":
        block = (
            header + f"new file mode 100644\n--- /dev/null\n+++ b/{shared}\n@@ -0,0 +1 @@\n+value = 1\n"
        )
    else:
        block = (
            header
            + f"deleted file mode 100644\n--- a/{shared}\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n"
        )
    excluded = f"diff --git a/{old} b/{new}\nsimilarity index 100%\nrename from {old}\nrename to {new}\n"
    with pytest.raises(
        TransactionError, match="production reversal overlaps excluded test paths: src/shared.py"
    ):
        _filter_non_test_patch(block + excluded)


def test_fixval_plain_patch_keeps_only_production_text(tmp_path):
    from code_forge.diff import iter_diff_sections
    from code_forge.fixval import _filter_non_test_patch, classify_fixval_candidate

    production = "--- a/value.py\n+++ b/value.py\n@@ -1,1 +1,1 @@\n-value = 1\n+value = 2\n"
    test = "--- a/tests/test_value.py\n+++ b/tests/test_value.py\n@@ -1,1 +1,1 @@\n-assert True\n+assert 1 == 1\n"
    assert _filter_non_test_patch(production + test) == production
    assert list(iter_diff_sections(production)) == [(None, production)]
    # An empty production projection is qualified before executing the test command.
    command = [sys.executable, "-I", "-S", "-c", 'raise AssertionError("must not execute")']
    result = run_fixval(
        classify_fixval_candidate(["value.py", "tests/test_value.py"]),
        command,
        tmp_path,
        "",
        test,
    )
    assert result.status == FixvalStatus.SKIPPED


@pytest.mark.parametrize("entrypoint", ["fixval", "local_terminal"])
@pytest.mark.parametrize("framing", ["plain_before_git", "git_before_plain"])
def test_fixval_keeps_leading_plain_production_in_mixed_packet(
    tmp_path, monkeypatch, entrypoint, framing
):
    from code_forge.fixval import _filter_non_test_patch, classify_fixval_candidate

    old, new = "tests/test_old.py", "tests/test_new.py"
    _base(tmp_path, {"value.py": b"value = 1\n", old: b"def test_hollow():\n    assert True\n"})
    if framing == "plain_before_git":
        (tmp_path / old).rename(tmp_path / new)
    else:
        (tmp_path / new).write_bytes(b"def test_hollow():\n    assert True\n")
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "--all")
    raw = _git(tmp_path, "diff", "--cached", "--binary", "-M")
    sections = ["diff --git " + section for section in raw.split("diff --git ")[1:]]
    production = next(section for section in sections if "+++ b/value.py\n" in section)
    other = next(section for section in sections if section != production)
    if framing == "plain_before_git":
        mixed = production[production.index("--- ") :] + other
        assert "rename from tests/test_old.py\n" in mixed
    else:
        mixed = production + other[other.index("--- ") :]
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=mixed,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    assert get_changed_files(mixed) == [new, "value.py"]
    before = ((tmp_path / "value.py").read_bytes(), _r1_identity(tmp_path / new), _r1_index(tmp_path))
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        "from pathlib import Path;import sys;"
        "p=Path('baseline-called');p.write_text(p.read_text()+'x' if p.exists() else 'x');"
        "raise SystemExit(0 if all(Path(p).is_file() for p in sys.argv[1:]) else 1)",
    ]
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    if entrypoint == "fixval":
        result = run_fixval(classify_fixval_candidate([new, "value.py"]), command, tmp_path, "", mixed)
        assert result.status == FixvalStatus.BLOCK
        assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    else:
        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=None,
            autofixer=None,
            revert_fn=lambda _: None,
            resolved_review=ResolvedReview([Path(new), Path("value.py")], None, mixed, "git"),
            source_hash="fixture",
            baseline_spec_repr="mixed-unified",
            cwd=tmp_path,
            registry={},
        )
        monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: [])
        monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", lambda: [])
        monkeypatch.setattr(machine, "_get_commit_message", lambda: "")
        monkeypatch.setattr(machine, "_write_ledger_rows", lambda: None)
        monkeypatch.setattr(machine, "_persist_state", lambda: None)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda _: {"test": {"command": command}}
        )
        machine._finalize_local_terminal()
        assert machine._state.verdict.value == "FAIL"
        assert not machine._state.converged
        assert [finding.id for finding in machine._state.findings] == ["FIXVAL_HOLLOW"]
    assert (tmp_path / "baseline-called").read_text() == "xxxx"
    assert "+++ b/value.py\n" in _filter_non_test_patch(mixed)
    assert "rename from tests/" not in _filter_non_test_patch(mixed)
    assert (
        (tmp_path / "value.py").read_bytes(),
        _r1_identity(tmp_path / new),
        _r1_index(tmp_path),
    ) == before
    assert _git(tmp_path, "diff", "--cached", "--binary", "-M") == raw


@pytest.mark.parametrize("framing", ["plain-Git-plain", "Git-plain-Git", "plain-plain-Git"])
@pytest.mark.parametrize("header_shaped_body", [False, True])
def test_mixed_framing_preserves_each_production_block(tmp_path, framing, header_shaped_body):
    from code_forge.fixval import _filter_non_test_patch

    old = b"-- a/fake.py\n" if header_shaped_body else b"value = 1\n"
    new = b"++ b/fake.py\n" if header_shaped_body else b"value = 2\n"
    names = ["a.py", "b.py", "c.py"]
    _base(tmp_path, dict.fromkeys(names, old))
    for name in names:
        (tmp_path / name).write_bytes(new)
    (tmp_path / "tests").mkdir()
    for name in ("test_middle.py", "test_tail.py"):
        (tmp_path / "tests" / name).write_bytes(b"def test_hollow():\n    assert True\n")
    _git(tmp_path, "add", "--all")
    raw = _git(tmp_path, "diff", "--cached", "--binary")
    sections = ["diff --git " + section for section in raw.split("diff --git ")[1:]]
    selected = []
    for name, style in zip(names, framing.split("-"), strict=True):
        block = next(section for section in sections if f"+++ b/{name}\n" in section)
        selected.append(block if style == "Git" else block[block.index("--- ") :])
    middle = next(section for section in sections if "+++ b/tests/test_middle.py\n" in section)
    tail = next(section for section in sections if "+++ b/tests/test_tail.py\n" in section)
    packet = selected[0] + middle[middle.index("--- ") :] + "".join(selected[1:]) + tail
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=packet,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    assert _filter_non_test_patch(packet) == "".join(selected)
    assert get_changed_files(_filter_non_test_patch(packet)) == names


@pytest.mark.parametrize("hollow", [False, True])
def test_mixed_binary_production_keeps_plain_test_excluded(tmp_path, monkeypatch, hollow):
    from code_forge.fixval import _filter_non_test_patch, classify_fixval_candidate

    old, new = b"\x00original\xff\n", b"\x00updated\xfe\n"
    _base(tmp_path, {"src/data.bin": old})
    (tmp_path / "src/data.bin").write_bytes(new)
    test = tmp_path / "tests/test_new.py"
    test.parent.mkdir()
    test.write_text(
        "def test_hollow(): assert True\n"
        if hollow
        else "from pathlib import Path\ndef test_changed(): assert Path('src/data.bin').read_bytes() == %r\n"
        % new
    )
    _git(tmp_path, "add", "--all")
    raw = _git(tmp_path, "diff", "--cached", "--binary")
    sections = ["diff --git " + section for section in raw.split("diff --git ")[1:]]
    production = next(section for section in sections if "GIT binary patch\n" in section)
    test_block = next(section for section in sections if "+++ b/tests/test_new.py\n" in section)
    mixed = production + test_block[test_block.index("--- ") :]
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=mixed,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    assert _filter_non_test_patch(mixed) == production
    before = ((tmp_path / "src/data.bin").read_bytes(), _r1_identity(test), _r1_index(tmp_path))
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    result = run_fixval(
        classify_fixval_candidate(["src/data.bin", "tests/test_new.py"]),
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        tmp_path,
        "",
        mixed,
    )
    assert result.status == (FixvalStatus.BLOCK if hollow else FixvalStatus.PASS)
    assert not any(f.id in {"FIXVAL_SKIPPED", "FIXVAL_TRANSACTION"} for f in result.findings)
    assert ((tmp_path / "src/data.bin").read_bytes(), _r1_identity(test), _r1_index(tmp_path)) == before
    assert _git(tmp_path, "diff", "--cached", "--binary") == raw


@pytest.mark.parametrize("entrypoint", ["fixval", "local_terminal"])
def test_mode_only_frame_excludes_following_plain_test(tmp_path, monkeypatch, entrypoint):
    from code_forge.fixval import _filter_non_test_patch, classify_fixval_candidate

    _base(tmp_path, {"value.py": b"value = 1\n"})
    production = tmp_path / "value.py"
    production.chmod(0o755)
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-q", "-m", "executable base")
    production.chmod(0o644)
    test = tmp_path / "tests/test_new.py"
    test.parent.mkdir()
    test.write_text("def test_hollow(): assert True\n")
    _git(tmp_path, "add", "--all")
    raw = _git(tmp_path, "diff", "--cached", "--binary")
    sections = ["diff --git " + section for section in raw.split("diff --git ")[1:]]
    mode = next(section for section in sections if "old mode 100755\n" in section)
    plain = next(section for section in sections if "+++ b/tests/test_new.py\n" in section)
    packet = mode + plain[plain.index("--- ") :]
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=packet,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    before = (
        production.stat().st_mode,
        production.read_bytes(),
        _r1_identity(test),
        _r1_index(tmp_path),
    )
    command = [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q"]
    monkeypatch.delenv("FIXVAL_WAIVER", raising=False)
    if entrypoint == "fixval":
        result = run_fixval(
            classify_fixval_candidate(["value.py", "tests/test_new.py"]),
            command,
            tmp_path,
            "",
            packet,
        )
        assert result.status == FixvalStatus.BLOCK
        assert [finding.id for finding in result.findings] == ["FIXVAL_HOLLOW"]
    else:
        machine = StateMachine(
            mode=Mode.LOCAL,
            falsifier=None,
            autofixer=None,
            revert_fn=lambda _: None,
            resolved_review=ResolvedReview(
                [Path("value.py"), Path("tests/test_new.py")], None, packet, "git"
            ),
            source_hash="fixture",
            baseline_spec_repr="mode-only-mixed-unified",
            cwd=tmp_path,
            registry={},
        )
        monkeypatch.setattr(machine, "_receipt_gate_round_errors", lambda: [])
        monkeypatch.setattr(machine, "_receipt_gate_terminal_errors", lambda: [])
        monkeypatch.setattr(machine, "_get_commit_message", lambda: "")
        monkeypatch.setattr(machine, "_write_ledger_rows", lambda: None)
        monkeypatch.setattr(machine, "_persist_state", lambda: None)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda _: {"test": {"command": command}}
        )
        machine._finalize_local_terminal()
        assert machine._state.verdict.value == "FAIL"
        assert not machine._state.converged
        assert [finding.id for finding in machine._state.findings] == ["FIXVAL_HOLLOW"]
    assert _filter_non_test_patch(packet) == mode
    assert (
        production.stat().st_mode,
        production.read_bytes(),
        _r1_identity(test),
        _r1_index(tmp_path),
    ) == before
    assert _git(tmp_path, "diff", "--cached", "--binary") == raw


@pytest.mark.parametrize("path_entry", ["bin", ".", "", "absolute", "unset"])
def test_tool_path_entries_use_source_root(tmp_path, monkeypatch, path_entry):
    import json
    import os

    from code_forge.runner import _resolve_command, run_tools

    if path_entry == "unset":
        monkeypatch.delenv("PATH", raising=False)
        native = subprocess.run(["true"], cwd="/usr", capture_output=True, timeout=5)
        assert native.returncode == 0
        resolved = _resolve_command("true", cwd=Path("/usr"))
        assert resolved and os.path.isabs(resolved)
        assert subprocess.run([resolved], cwd="/usr", capture_output=True, timeout=5).returncode == 0
        return
    source, host = tmp_path / "source", tmp_path / "host"
    subdir = "bin" if path_entry in {"bin", "absolute"} else ""
    for root, label in ((source, "source"), (host, "host")):
        tool = root / subdir / "scope-tool"
        tool.parent.mkdir(parents=True)
        tool.write_text(
            "#!/usr/bin/python3\nimport json, os, sys\n"
            + "if '--version' in sys.argv: print(%r)\n" % label
            + "else: print(json.dumps({'label': %r, 'cwd': os.getcwd(), 'files': sys.argv[1:]}))\n"
            % label
        )
        tool.chmod(0o755)
    monkeypatch.chdir(host)
    monkeypatch.setenv("PATH", str(source / subdir) if path_entry == "absolute" else path_entry)
    native = subprocess.run(
        ["scope-tool", "module.py"], cwd=source, capture_output=True, text=True, timeout=5
    )
    assert native.returncode == 0
    expected = {"label": "source", "cwd": str(source), "files": ["module.py"]}
    assert json.loads(native.stdout) == expected
    config = ToolConfig(
        name="scope", command="scope-tool", args=[], output_format="eslint_json", file_patterns=["*.py"]
    )
    results, versions, skipped, infra = run_tools({"scope": config}, ["module.py"], cwd=source)
    assert versions == {"scope": "source"}
    assert skipped == [] and infra == []
    stdout, code, stderr = results["scope"]
    assert code == 0 and stderr == ""
    assert json.loads(stdout) == expected


@pytest.mark.parametrize(
    ("test_change", "kind"),
    [
        ("add", "mode"),
        ("add", "mode_text"),
        ("delete", "mode_text"),
        ("add", "delete"),
        ("delete", "delete"),
    ],
)
def test_metadata_frame_and_own_headers_keep_file_identity(tmp_path, kind, test_change):
    from code_forge.diff import split_diff_for_files
    from code_forge.fixval import _filter_non_test_patch

    test_name = "tests/test_other.py"
    files = {"old.py": b"value = 1\n"}
    if test_change == "delete":
        files[test_name] = b"def test_hollow(): assert True\n"
    _base(tmp_path, files)
    original = tmp_path / "old.py"
    if kind.startswith("mode"):
        original.chmod(0o755)
        _git(tmp_path, "add", "--all")
        _git(tmp_path, "commit", "-q", "-m", "executable base")
        original.chmod(0o644)
        if kind == "mode_text":
            original.write_bytes(b"value = 2\n")
    else:
        original.unlink()
    test = tmp_path / test_name
    if test_change == "delete":
        test.unlink()
    else:
        test.parent.mkdir(exist_ok=True)
        test.write_bytes(b"def test_hollow(): assert True\n")
    _git(tmp_path, "add", "--all")
    raw = _git(tmp_path, "diff", "--cached", "--binary", "-M", "-C", "--find-copies-harder")
    sections = ["diff --git " + section for section in raw.split("diff --git ")[1:]]
    test_block = next(section for section in sections if test_name in section)
    production = next(section for section in sections if section != test_block)
    packet = production + test_block[test_block.index("--- ") :]
    check = subprocess.run(
        ["git", "apply", "-R", "--check", "-"],
        input=packet,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    assert _filter_non_test_patch(packet) == production
    assert split_diff_for_files(packet, ["old.py"]) == production


@pytest.mark.parametrize("meaningful", [False, True])
def test_fixval_recovery_is_outside_native_test_scope(tmp_path, meaningful):
    _base(tmp_path, {"value.py": b"value = 1\n", "tests/test_clean.py": b"pass\n"})
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "value.py")
    patch = _git(tmp_path, "diff", "--cached", "--binary")
    index = _r1_index(tmp_path)
    program = (
        "import pathlib,subprocess; "
        "r=subprocess.run(['git','ls-files','--others','--exclude-standard'],"
        "capture_output=True,text=True,check=True); "
        "assert not r.stdout, r.stdout; "
        + ("assert pathlib.Path('value.py').read_text() == 'value = 2\\n'" if meaningful else "")
    )
    result = run_fixval(
        FixvalCandidate(["tests/test_clean.py"], ["value.py"]),
        [sys.executable, "-B", "-c", program],
        tmp_path,
        "",
        patch,
    )
    assert result.status == (FixvalStatus.PASS if meaningful else FixvalStatus.BLOCK)
    assert not result.findings or all(f.id != "FIXVAL_TRANSACTION" for f in result.findings)
    assert (tmp_path / "value.py").read_bytes() == b"value = 2\n"
    assert _r1_index(tmp_path) == index
    assert not _recovery_paths(tmp_path)
    assert not _git(tmp_path, "ls-files", "--others", "--exclude-standard")


@pytest.mark.parametrize("hollow", [False, True])
def test_terminal_forwards_external_recovery_parent_to_real_fixval(tmp_path, monkeypatch, hollow):
    from code_forge.state import Verdict

    repo = tmp_path / "primary/sibling"
    repo.mkdir(parents=True)
    _base(repo, {"src/model.py": b"value = 1\n"})
    (repo / "src/model.py").write_bytes(b"value = 2\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_live.py").write_text(
        "def test_live(): assert True\n"
        if hollow
        else "from model import value\ndef test_live(): assert value == 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "src/model.py", "tests/test_live.py")
    diff = _git(repo, "diff", "--cached")
    index = _r1_index(repo)
    state = tmp_path / "state"
    state.mkdir()
    machine = _terminal_fixture(
        repo, state, diff, [repo / p for p in get_changed_files(diff)], monkeypatch
    )
    machine.recovery_parent = tmp_path
    original = (repo / "src/model.py").read_bytes(), (repo / "src/model.py").stat().st_mode
    descriptors = set(os.listdir("/proc/self/fd"))
    prepare = FixvalTransaction.prepare
    parents = []

    def capture_parent(transaction):
        prepare(transaction)
        parents.append(transaction.directory.parent)

    monkeypatch.setattr(FixvalTransaction, "prepare", capture_parent)
    machine._finalize_local_terminal()
    assert ((repo / "src/model.py").read_bytes(), (repo / "src/model.py").stat().st_mode) == original
    assert _r1_index(repo) == index
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert machine._state.verdict == (Verdict.FAIL if hollow else Verdict.PASS)
    assert parents == [tmp_path]


@pytest.mark.parametrize("kind", ["root", "child", "inside_alias", "missing", "file", "outside_alias"])
def test_explicit_recovery_parent_refusal_leaves_source_and_descriptors_intact(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "value.py"
    source.write_bytes(b"new\n")
    source.chmod(0o751)
    child = repo / "child"
    child.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo if kind == "inside_alias" else external, target_is_directory=True)
    leaf = tmp_path / "file"
    leaf.write_bytes(b"FOREIGN\n")
    parent = {
        "root": repo,
        "child": child,
        "inside_alias": alias / "child",
        "missing": tmp_path / "absent",
        "file": leaf,
        "outside_alias": alias,
    }[kind]
    before = source.read_bytes(), source.stat().st_ino, source.stat().st_mode
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises((TransactionError, OSError)):
        FixvalTransaction(repo, "", recovery_parent=parent).close()
    assert (source.read_bytes(), source.stat().st_ino, source.stat().st_mode) == before
    assert leaf.read_bytes() == b"FOREIGN\n"
    assert not list(external.iterdir())
    assert set(os.listdir("/proc/self/fd")) == descriptors


def test_explicit_recovery_parent_device_refusal_is_native_and_before_source_mutation(tmp_path):
    parent = Path("/dev/shm")
    if not parent.is_dir() or parent.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("no actual external different-device directory")
    source = tmp_path / "value.py"
    source.write_bytes(b"new\n")
    before = source.read_bytes(), source.stat().st_ino, source.stat().st_mode
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises(TransactionError, match="share the source filesystem"):
        FixvalTransaction(tmp_path, "", recovery_parent=parent).close()
    assert (source.read_bytes(), source.stat().st_ino, source.stat().st_mode) == before
    assert set(os.listdir("/proc/self/fd")) == descriptors


def test_explicit_recovery_parent_identity_refusal_preserves_foreign_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "value.py"
    source.write_bytes(b"new\n")
    external = tmp_path / "external"
    external.mkdir()
    before = source.read_bytes(), source.stat().st_ino, source.stat().st_mode
    descriptors = set(os.listdir("/proc/self/fd"))
    transaction = FixvalTransaction(repo, "", recovery_parent=external)
    displaced = tmp_path / "displaced"
    external.rename(displaced)
    external.mkdir()
    foreign = external / "sentinel"
    foreign.write_bytes(b"FOREIGN\n")
    inode = foreign.stat().st_ino
    try:
        with pytest.raises(TransactionError, match="recovery parent changed"):
            transaction.prepare()
    finally:
        transaction.close()
    assert foreign.read_bytes() == b"FOREIGN\n" and foreign.stat().st_ino == inode
    assert (source.read_bytes(), source.stat().st_ino, source.stat().st_mode) == before
    assert not list(displaced.iterdir())
    assert set(os.listdir("/proc/self/fd")) == descriptors


def test_fixval_recovery_parent_is_bound_and_closed_on_refusal(tmp_path):
    repo = tmp_path / "parent/repo"
    repo.mkdir(parents=True)
    _base(repo, {"value.py": b"value = 1\n"})
    (repo / "value.py").write_bytes(b"value = 2\n")
    _git(repo, "add", "value.py")
    patch = _git(repo, "diff", "--cached", "--binary")
    transaction = FixvalTransaction(repo, patch)
    descriptors = {transaction.root_fd, transaction.recovery_parent_fd}
    moved = tmp_path / "moved-parent"
    repo.parent.rename(moved)
    repo.parent.mkdir()
    foreign = repo.parent / "sentinel"
    foreign.write_bytes(b"FOREIGN\n")
    with pytest.raises(TransactionError, match="recovery parent changed"):
        transaction.prepare()
    transaction.close()
    assert (moved / "repo/value.py").read_bytes() == b"value = 2\n"
    assert foreign.read_bytes() == b"FOREIGN\n"
    assert not list(repo.parent.glob(".fixval-*"))
    assert not list(moved.glob(".fixval-*"))
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("refusal", ["different_device", "allocation_denied"])
def test_fixval_recovery_refusal_precedes_source_mutation(tmp_path, monkeypatch, refusal):
    _base(tmp_path, {"value.py": b"value = 1\n"})
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "value.py")
    patch = _git(tmp_path, "diff", "--cached", "--binary")
    before = (tmp_path / "value.py").read_bytes(), _r1_index(tmp_path)
    descriptors = set(os.listdir("/proc/self/fd"))
    if refusal == "different_device":
        native = os.fstat
        native_stat = os.stat

        def parent_stat(path, *args, **kwargs):
            info = native_stat(path, *args, **kwargs)
            if path == tmp_path.parent:
                fields = list(info)
                fields[2] += 1
                return os.stat_result(fields)
            return info

        def different_device(fd):
            info = native(fd)
            if Path(os.readlink("/proc/self/fd/%s" % fd)) == tmp_path.parent:
                fields = list(info)
                fields[2] += 1
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {parent_stat})
        monkeypatch.setattr(os, "stat", parent_stat)
        monkeypatch.setattr(os, "fstat", different_device)
        with pytest.raises(TransactionError, match="share the source filesystem"):
            FixvalTransaction(tmp_path, patch)
    else:
        transaction = FixvalTransaction(tmp_path, patch)

        parent_mode = stat.S_IMODE(tmp_path.parent.stat().st_mode)
        tmp_path.parent.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                transaction.prepare()
        finally:
            tmp_path.parent.chmod(parent_mode)
            transaction.close()
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert ((tmp_path / "value.py").read_bytes(), _r1_index(tmp_path)) == before
    assert not _recovery_paths(tmp_path)


@pytest.mark.parametrize("refusal", ["open", "identity", "parent", "displaced"])
def test_fixval_allocated_recovery_refusal_closes_descriptors(tmp_path, monkeypatch, refusal):
    _base(tmp_path, {"value.py": b"value = 1\n"})
    (tmp_path / "value.py").write_bytes(b"value = 2\n")
    _git(tmp_path, "add", "value.py")
    patch = _git(tmp_path, "diff", "--cached", "--binary")
    transaction = FixvalTransaction(tmp_path, patch)
    descriptors = set(os.listdir("/proc/self/fd"))
    native_open = os.open
    native_fstat = os.fstat
    native_check = transaction._check_recovery_parent
    calls = 0
    foreign = None

    def opening(path, flags, *args, **kwargs):
        nonlocal foreign
        if kwargs.get("dir_fd") == transaction.recovery_parent_fd and str(path).startswith(".fixval-"):
            if refusal == "open":
                raise PermissionError("injected recovery open refusal")
            fd = native_open(path, flags, *args, **kwargs)
            if refusal == "displaced":
                allocated = transaction.recovery_parent / path
                allocated.rename(allocated.with_name(allocated.name + "-displaced"))
                allocated.mkdir()
                foreign = allocated / "sentinel"
                foreign.write_bytes(b"FOREIGN\n")
            return fd
        return native_open(path, flags, *args, **kwargs)

    def identity(fd):
        nonlocal calls
        result = native_fstat(fd)
        if refusal == "identity" and ".fixval-recovery-" in os.readlink("/proc/self/fd/%s" % fd):
            calls += 1
            if calls == 1:
                fields = list(result)
                fields[1] += 1
                return os.stat_result(fields)
        return result

    def check_parent():
        nonlocal calls
        native_check()
        if refusal == "parent":
            calls += 1
            if calls == 3:
                raise TransactionError("injected recovery parent change")

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fstat", identity)
    monkeypatch.setattr(transaction, "_check_recovery_parent", check_parent)
    with pytest.raises((PermissionError, TransactionError)):
        transaction.prepare()
    if refusal == "displaced":
        with pytest.raises(TransactionError, match="recovery directory changed"):
            transaction.close()
        assert foreign.read_bytes() == b"FOREIGN\n"
    else:
        transaction.close()
        assert not _recovery_paths(tmp_path)
    assert (tmp_path / "value.py").read_bytes() == b"value = 2\n"
    for fd in {transaction.root_fd, transaction.recovery_parent_fd}:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert len(os.listdir("/proc/self/fd")) == len(descriptors) - 2


def test_real_fixval_surviving_empty_file_keeps_parent_and_mode(tmp_path, monkeypatch):
    from code_forge.state import Verdict

    _base(tmp_path, {"src/model.py": b"value = 1\n"})
    source = tmp_path / "src/model.py"
    source.write_bytes(b"")
    source.chmod(0o751)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_live.py").write_text(
        "from pathlib import Path\ndef test_live(): assert Path('src/model.py').read_bytes() == b''\n"
    )
    _git(tmp_path, "add", "--all")
    packet = _git(tmp_path, "diff", "--cached", "--binary")
    assert get_removed_files(packet) == []
    before = (source.read_bytes(), source.stat().st_mode, _r1_index(tmp_path))
    files = [tmp_path / name for name in get_changed_files(packet)]
    machine = _terminal_fixture(tmp_path, tmp_path, packet, files, monkeypatch)
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.PASS
    assert not any(f.id == "FIXVAL_TRANSACTION" for f in machine._state.findings)
    assert (source.read_bytes(), source.stat().st_mode, _r1_index(tmp_path)) == before
    assert _git(tmp_path, "diff", "--cached", "--binary") == packet
    assert not list(_recovery_paths(tmp_path))


def test_real_fixval_private_image_swap_during_git_preserves_writer(tmp_path, monkeypatch):
    import errno
    import threading
    import time

    from code_forge.state import Verdict

    machine, diff, files, retained_before, index_before = _retained_transaction_fixture(
        tmp_path, monkeypatch
    )
    native = subprocess.run
    foreign = []
    writers = []
    failures = []

    def interleave(argv, **kwargs):
        if argv[:3] != ["git", "apply", "-R"]:
            return native(argv, **kwargs)
        image = Path(kwargs["cwd"]).resolve()
        assert image.name.startswith(".fixval-image-")
        patch = Path(argv[-1])
        payload = patch.read_bytes()
        patch.unlink()
        os.mkfifo(patch, 0o600)

        def writer():
            fd = None
            try:
                deadline = time.monotonic() + 5
                while fd is None:
                    try:
                        fd = os.open(patch, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError as exc:
                        if exc.errno != errno.ENXIO or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.01)
                # The real Git reader has opened the FIFO and is running in its bound cwd.
                image.rename(tmp_path / "owned-image-moved")
                image.mkdir()
                sentinel = image / "writer-data"
                sentinel.write_bytes(b"UNRELATED_IMAGE_WRITER\n")
                sentinel.chmod(0o711)
                foreign.append((image, image.stat().st_ino, sentinel, _retained_identity(sentinel)))
                with os.fdopen(fd, "wb", buffering=0) as output:
                    fd = None
                    output.write(payload)
            except (OSError, AssertionError) as exc:
                failures.append(exc)
            finally:
                if fd is not None:
                    os.close(fd)

        thread = threading.Thread(target=writer)
        writers.append(thread)
        thread.start()
        try:
            return native(argv, **kwargs)
        finally:
            thread.join(6)
            assert not thread.is_alive() and not failures

    monkeypatch.setattr(subprocess, "run", interleave)
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.FAIL
    assert any(f.id == "FIXVAL_TRANSACTION" for f in machine._state.findings)
    assert len(foreign) == 1 and all(not thread.is_alive() for thread in writers)
    image, inode, sentinel, identity = foreign[0]
    assert image.is_dir() and image.stat().st_ino == inode
    assert _retained_identity(sentinel) == identity
    assert (tmp_path / "owned-image-moved").is_dir()
    assert (tmp_path / "owned-image-moved/src/model.py").read_bytes() == b"value = 1\n"
    assert _retained_identity(tmp_path / "src/gone.py") == retained_before
    assert (tmp_path / "src/model.py").read_bytes() == b"value = 2\n"
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert machine._source_files() == files
    assert list(_recovery_paths(tmp_path))


@pytest.mark.parametrize("inherited_routing", [False, True])
def test_real_fixval_private_git_ignores_enclosing_repository(tmp_path, monkeypatch, inherited_routing):
    from code_forge.state import Verdict

    _base(tmp_path, {"outer.py": b"outer = True\n"})
    outer_index = (tmp_path / ".git/index").read_bytes()
    repo = tmp_path / "reviewed"
    repo.mkdir()
    machine, diff, files, retained_before, index_before = _retained_transaction_fixture(
        repo, monkeypatch
    )
    routing = {
        "GIT_DIR": str(tmp_path / ".git"),
        "GIT_WORK_TREE": str(tmp_path),
        "GIT_INDEX_FILE": str(tmp_path / "foreign-index"),
        "GIT_COMMON_DIR": str(tmp_path / ".git"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / ".git/objects"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "apply.ignoreWhitespace",
        "GIT_CONFIG_VALUE_0": "change",
    }
    if inherited_routing:
        for key, value in routing.items():
            monkeypatch.setenv(key, value)
    machine._finalize_local_terminal()
    assert machine._state.verdict == Verdict.PASS
    assert not any(f.id in {"FIXVAL_TRANSACTION", "FIXVAL_HOLLOW"} for f in machine._state.findings)
    assert _retained_identity(repo / "src/gone.py") == retained_before
    assert (repo / "src/model.py").read_bytes() == b"value = 2\n"
    assert (repo / ".git/index").read_bytes() == index_before
    assert (tmp_path / ".git/index").read_bytes() == outer_index
    assert not (tmp_path / "foreign-index").exists()
    if inherited_routing:
        for key in routing:
            monkeypatch.delenv(key)
    assert _git(repo, "diff", "--cached", "--binary") == diff
    assert machine._source_files() == files
    assert not list(_recovery_paths(repo))


def test_real_fixval_recovery_allocation_refusal_closes_descriptors(tmp_path, monkeypatch):
    from code_forge.state import Verdict

    machine, diff, files, retained_before, index_before = _retained_transaction_fixture(
        tmp_path, monkeypatch
    )
    recovery = tmp_path.parent / (tmp_path.name + "-unwritable")
    recovery.mkdir(mode=0o500)
    machine.recovery_parent = recovery
    original_prepare = FixvalTransaction.prepare
    captured = []
    descriptor_before = set(os.listdir("/proc/self/fd"))

    def capture(transaction):
        captured.append(transaction)
        original_prepare(transaction)

    monkeypatch.setattr(FixvalTransaction, "prepare", capture)
    error = None
    leaked = []
    try:
        try:
            machine._finalize_local_terminal()
        except AttributeError as exc:
            error = exc
            leaked = sorted(set(os.listdir("/proc/self/fd")) - descriptor_before)
        assert error is None, f"allocation refusal escaped as {error!r}; leaked descriptors {leaked}"
        assert machine._state.verdict == Verdict.FAIL
        assert any(f.id == "FIXVAL_TRANSACTION" for f in machine._state.findings)
        assert set(os.listdir("/proc/self/fd")) == descriptor_before
    finally:
        recovery.chmod(0o700)
        # Retire only captured transaction descriptors after recording any failed close assertion.
        for transaction in captured:
            try:
                bound = os.fstat(transaction.root_fd)
            except OSError:
                continue
            expected = tmp_path.stat()
            if (bound.st_dev, bound.st_ino) == (expected.st_dev, expected.st_ino):
                transaction.close()
    assert _retained_identity(tmp_path / "src/gone.py") == retained_before
    assert (tmp_path / "src/model.py").read_bytes() == b"value = 2\n"
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert _git(tmp_path, "diff", "--cached", "--binary") == diff
    assert machine._source_files() == files
    assert not list(recovery.iterdir())
