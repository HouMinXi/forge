"""Native inventory diagnostics preserve process facts without scoring mutants."""

import os
import subprocess
import sys
from dataclasses import asdict

import pytest

from code_forge.mutation_dispatch import _tool_version, invoke_tool, probe_note, run_note
from code_forge.mutation_engines.adapters import get_adapter


@pytest.mark.parametrize(
    ("adapter_id", "binary", "fixture", "content"),
    [
        ("go-gremlins", "gremlins", "probe_test.go", "package p\n"),
        ("rust-cargo-mutants", "cargo", "probe_test.rs", "fn t() {}\n"),
        ("js-stryker", "stryker", "vitest.config.ts", "export default {}\n"),
        ("ps-mutant", "pwsh", "probe.Tests.ps1", "Describe 'probe' {}\n"),
        ("c-mull", "mull-runner-22", "probe_test", ""),
    ],
)
def test_each_adapter_diagnostic_executes_the_scanned_root(
    tmp_path, monkeypatch, adapter_id, binary, fixture, content
):
    root = tmp_path / "project"
    root.mkdir()
    source = root / fixture
    source.write_text(content, encoding="utf-8")
    source.chmod(0o755)
    (root / "marker.txt").write_text(adapter_id, encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / binary
    executable.write_text(
        "#!" + sys.executable + "\n"
        "from pathlib import Path\n"
        "import sys\n"
        "print(Path('marker.txt').read_text())\n"
        "print('native diagnostic detail', file=sys.stderr)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.defpath)

    result = get_adapter(adapter_id).invoke(root)

    assert result.reason == "diagnostic complete"
    assert result.cwd == str(root.resolve())
    assert result.argv[0] == binary
    assert result.exit_code == 0
    assert result.stdout == adapter_id + "\n"
    assert result.stderr == "native diagnostic detail\n"
    assert result.timeout is False
    assert result.error is None
    assert result.outcomes == ()
    assert asdict(result)["exit_code"] == 0


@pytest.mark.parametrize("exit_code", [0, 2, 7, -9])
def test_native_exit_overrides_the_discovered_test_label(tmp_path, monkeypatch, exit_code):
    def run(argv, **kwargs):
        assert kwargs["cwd"] == str(tmp_path.resolve())
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(argv, exit_code, stdout="inventory\n", stderr="detail\n")

    monkeypatch.setattr(subprocess, "run", run)
    result = invoke_tool(["tool", "--list"], "ran", cwd=tmp_path)
    assert result.reason == (
        "diagnostic complete" if exit_code == 0 else "diagnostic failed (exit %d)" % exit_code
    )
    assert (result.exit_code, result.stdout, result.stderr) == (exit_code, "inventory\n", "detail\n")
    assert result.outcomes == ()


@pytest.mark.parametrize(
    "reason", ["no go test", "no cargo test", "no vitest", "no pester", "no c test binary"]
)
def test_successful_diagnostic_preserves_named_no_test_boundary(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    )
    assert invoke_tool(["tool"], reason, cwd=tmp_path).reason == reason


def test_real_nonzero_command_retains_process_output(tmp_path):
    result = invoke_tool(
        [
            sys.executable,
            "-c",
            "import sys; print('inventory'); print('refused', file=sys.stderr); sys.exit(7)",
        ],
        "no cargo test",
        cwd=tmp_path,
    )
    assert result.reason == "diagnostic failed (exit 7)"
    assert result.exit_code == 7
    assert result.stdout == "inventory\n"
    assert result.stderr == "refused\n"
    assert result.error is None
    assert result.timeout is False


def test_real_timeout_retains_emitted_output(tmp_path):
    result = invoke_tool(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys,time; print('partial'); print('waiting', file=sys.stderr); time.sleep(10)",
        ],
        "ran",
        cwd=tmp_path,
        timeout=0.15,
    )
    assert result.reason == "diagnostic timed out"
    assert result.exit_code is None
    assert result.stdout == "partial\n"
    assert result.stderr == "waiting\n"
    assert result.timeout is True
    assert "TimeoutExpired" in result.error


def test_missing_command_is_a_named_refusal(tmp_path):
    command = str(tmp_path / "missing-tool")
    result = invoke_tool([command], "ran", cwd=tmp_path)
    assert result.reason == "diagnostic unavailable"
    assert result.exit_code is None
    assert result.stdout == result.stderr == ""
    assert result.timeout is False
    assert "FileNotFoundError" in result.error
    assert command in result.error


def test_missing_target_root_does_not_fall_back_to_caller(tmp_path):
    missing = tmp_path / "missing-project"
    result = invoke_tool([sys.executable, "--version"], "ran", cwd=missing)
    assert result.reason == "diagnostic unavailable"
    assert result.cwd == str(missing.resolve())
    assert result.exit_code is None
    assert "FileNotFoundError" in result.error


@pytest.mark.parametrize("output", [b"partial\xff\n", "partial\n", None])
def test_timeout_transport_accepts_bytes_text_and_absence(tmp_path, monkeypatch, output):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=output, stderr=output)

    monkeypatch.setattr(subprocess, "run", run)
    result = invoke_tool(["tool"], "ran", cwd=tmp_path)
    expected = "partial\ufffd\n" if isinstance(output, bytes) else output or ""
    assert result.stdout == result.stderr == expected
    assert result.timeout is True


@pytest.mark.parametrize(
    "adapter_id,version",
    [("go-gremlins", "0.6.0"), ("rust-cargo-mutants", "27.1.0"), ("js-stryker", "10.0.0")],
)
def test_failed_version_cannot_admit_native_diagnostic(monkeypatch, tmp_path, adapter_id, version):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 7, stdout=version + "\n", stderr="refused\n")

    adapter = get_adapter(adapter_id)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("code_forge.mutation_dispatch.adapters_for_files", lambda paths: (adapter,))
    monkeypatch.setattr(
        type(adapter), "probe", lambda *args: pytest.fail("failed native probe bypassed")
    )
    monkeypatch.setattr(
        type(adapter), "invoke", lambda *args: pytest.fail("failed version invoked tool")
    )
    assert probe_note(["source"]) == "mutation probe: %s probe_failed" % adapter_id
    assert run_note(["source"], tmp_path) == "mutation run: "
    assert len(calls) == 2
    assert _tool_version(adapter_id, run) == "probe_failed"


@pytest.mark.parametrize("error", [OSError("cannot execute"), subprocess.TimeoutExpired(["tool"], 30)])
def test_version_launch_errors_refuse_dispatch(monkeypatch, error):
    def run(argv, **kwargs):
        raise error

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert _tool_version("go-gremlins", run) == "probe_failed"
