"""Command and fail-closed checks, not substitutes for the real mount cases."""

from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from tests import _bind_mount as helper


def test_command_preserves_real_binds_and_child_arguments(tmp_path):
    source = tmp_path / "source with space"
    target = tmp_path / "alias"
    source.mkdir()
    target.mkdir()
    args = ["root-alias-via-symlink", source, "base-sha", tmp_path]
    command = helper.bind_mount_command("/usr/bin/bwrap", tmp_path, [(source, target)], "pass", args)
    assert command[:18] == [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--bind",
        str(tmp_path),
        str(tmp_path),
        "--bind",
        str(source),
        str(target),
        "--",
        sys.executable,
        "-B",
    ]
    assert command[18] == "-c"
    assert command[19].startswith(f"_fixture_binds = {[(str(source), str(target))]!r}\n")
    assert helper._VERIFY_MOUNTS in command[19]
    assert command[20:] == list(map(str, args))


def test_control_uses_same_namespace_without_alias(tmp_path):
    command = helper.bind_mount_command("/usr/bin/bwrap", tmp_path, [], "pass", [])
    assert command.count("--bind") == 1  # writable fixture workspace only
    assert "--unshare-all" in command
    assert "_fixture_binds = []" in command[-1]


@pytest.mark.parametrize("which", ["source", "target"])
def test_outside_workspace_bind_rejected(tmp_path, which):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    source, target = (outside, workspace) if which == "source" else (workspace, outside)
    with pytest.raises(ValueError, match="inside its workspace"):
        helper.bind_mount_command("bwrap", workspace, [(source, target)], "pass", [])


def test_symlink_escape_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = workspace / "link"
    alias.symlink_to(outside)
    with pytest.raises(ValueError, match="inside its workspace"):
        helper.bind_mount_command("bwrap", workspace, [(workspace, alias)], "pass", [])


def test_same_path_is_not_an_alias(tmp_path):
    with pytest.raises(ValueError, match="distinct alias"):
        helper.bind_mount_command("bwrap", tmp_path, [(tmp_path, tmp_path)], "pass", [])


def test_missing_bind_path_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        helper.bind_mount_command("bwrap", tmp_path, [(tmp_path, tmp_path / "missing")], "pass", [])


def test_missing_bwrap_is_explicit_skip(monkeypatch):
    monkeypatch.setattr(helper.shutil, "which", lambda _: None)
    with pytest.raises(pytest.skip.Exception, match="requires bubblewrap"):
        helper.require_bwrap()


@pytest.mark.parametrize("returncode", [0, 1])
def test_namespace_probe(monkeypatch, returncode):
    monkeypatch.setattr(helper.shutil, "which", lambda _: "/usr/bin/bwrap")
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, "", "namespace denied"))
    monkeypatch.setattr(helper.subprocess, "run", run)
    if returncode:
        with pytest.raises(
            pytest.skip.Exception, match="^bubblewrap user/mount namespaces unavailable$"
        ):
            helper.require_bwrap()
    else:
        assert helper.require_bwrap() == "/usr/bin/bwrap"
    command = run.call_args.args[0]
    assert command == [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--",
        sys.executable,
        "-B",
        "-c",
        "pass",
    ]
    assert run.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("failure", [OSError("launch failed"), subprocess.TimeoutExpired("bwrap", 30)])
def test_probe_launch_failure_is_not_a_skip(monkeypatch, failure):
    monkeypatch.setattr(helper.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(helper.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        helper.require_bwrap()


@pytest.mark.parametrize("returncode", [0, 1])
def test_child_failure_never_becomes_skip(tmp_path, monkeypatch, returncode):
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, "LOADED", "mount failed"))
    monkeypatch.setattr(helper.subprocess, "run", run)
    if returncode:
        with pytest.raises(AssertionError, match="bind-mount child failed.*mount failed"):
            helper.run_bind_mount_script("bwrap", tmp_path, [], "pass", [], pythonpath="src")
    else:
        result = helper.run_bind_mount_script("bwrap", tmp_path, [], "pass", [], pythonpath="src")
        assert result.stdout == "LOADED"
    assert run.call_args.kwargs["env"]["TMPDIR"] == str(tmp_path)
    assert run.call_args.kwargs["env"]["PYTHONPATH"] == "src"
    assert run.call_args.kwargs["timeout"] == 60


@pytest.mark.parametrize("mounted,samefile", [(False, True), (True, False), (True, True)])
def test_child_checks_mountinfo_and_inode_identity(monkeypatch, mounted, samefile):
    # Unit-test the verifier only. Product tests still require real bubblewrap mounts.
    mountinfo = "42 1 0:1 / /alias\\040space rw - tmpfs tmpfs rw\n" if mounted else ""
    monkeypatch.setattr(Path, "read_text", lambda _: mountinfo)
    monkeypatch.setattr(helper.os.path, "samefile", lambda *_: samefile)
    scope = {"_fixture_binds": [("/source", "/alias space")]}
    if mounted and samefile:
        exec(helper._VERIFY_MOUNTS, scope)  # noqa: S102 -- execute only the fixed fixture verifier
    else:
        with pytest.raises(RuntimeError, match="real bind-mount fixture was not established"):
            exec(helper._VERIFY_MOUNTS, scope)  # noqa: S102 -- execute only the fixed fixture verifier
