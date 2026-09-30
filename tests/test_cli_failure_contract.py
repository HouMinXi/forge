"""A CLI invocation reports why it failed before any model output exists.

_invoke_cli builds a command, runs it, and reads the result. The
failure it raises is the only record of which step broke, and the
suite never checked the command shape or the error text, so a mutant
that rewrote either one still passed.
"""

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig


def _backend(command="forge-test-cli", model="m"):
    return BackendConfig(name="cli-test", type="cli", command=command, model=model)


def _install_binary(monkeypatch, path):
    monkeypatch.setattr(invoke, "shutil", SimpleNamespace(which=lambda name: path))


def _shell_prompt(script, binary, path):
    """Run the built shell command and return the text it passed to -p.

    The binary is replaced by a recorder. The check runs in a fresh
    interpreter, because this process installs a signal handler at import
    that a forked shell would inherit.
    """
    recorder = Path(path).parent / "recorder.sh"
    out_path = Path(path).parent / "out.txt"
    recorder.write_text(
        "#!/bin/sh\nprintf '%%s' \"$2\" > %s\n" % shlex.quote(str(out_path)),
        encoding="utf-8",
    )
    recorder.chmod(0o700)
    target = Path(path)
    target.write_text("payload with spaces", encoding="utf-8")
    quoted_binary = shlex.quote(binary)
    assert script.startswith(quoted_binary), script
    swapped = shlex.quote(str(recorder)) + script[len(quoted_binary):]
    rc = os.spawnvp(os.P_WAIT, "sh", ["sh", "-c", swapped])  # noqa: S606 - quoted command, checks shell delivery
    assert rc == 0, rc
    return out_path.read_text(encoding="utf-8"), target.read_text(encoding="utf-8")


def test_missing_binary_names_the_command(monkeypatch):
    _install_binary(monkeypatch, None)

    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    assert str(caught.value) == "forge-test-cli binary not found on PATH"


def test_default_binary_name_is_claude(monkeypatch):
    seen = []

    def record(name):
        seen.append(name)

    monkeypatch.setattr(invoke.shutil, "which", record)

    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_cli("prompt", _backend(command=""), timeout_s=5)

    assert seen == ["claude"]


def test_a_large_prompt_is_read_from_a_temp_file(monkeypatch, tmp_path):
    """Over 1 MB the prompt is written to a file and the shell reads it back.

    The command has to quote the binary, the file, and the model, or a
    space in any of them splits the command.
    """
    written = {}

    def fake_mkstemp(suffix, prefix):
        target = tmp_path / "prompt file.txt"
        target.write_bytes(b"")
        written["path"] = str(target)
        return 7, str(target)

    def fake_write(fd, data):
        assert fd == 7
        written["data"] = data
        return len(data)

    closed = []
    removed = []
    popen = Mock(side_effect=OSError("boom"))

    def remember_close(fd):
        closed.append(fd)

    def remember_unlink(path):
        removed.append(path)

    monkeypatch.setattr(tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(invoke.os, "write", fake_write)
    monkeypatch.setattr(invoke.os, "close", remember_close)
    monkeypatch.setattr(invoke.os, "unlink", remember_unlink)
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    _install_binary(monkeypatch, "/opt/my bin/cli")

    prompt = "x" * 1_000_001
    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_cli(prompt, _backend(model="sonnet 4"), timeout_s=5)

    assert written["data"] == prompt.encode("utf-8")
    shell, flag, script = popen.call_args.args[0]
    assert (shell, flag) == ("sh", "-c")
    # Run the built command with the binary swapped for a recorder, so the
    # assertion checks what the shell actually delivers, not the text of
    # the command.
    delivered, expected = _shell_prompt(script, "/opt/my bin/cli", written["path"])
    assert delivered == expected
    assert closed == [7]
    assert removed == [written["path"]]


def test_a_large_prompt_without_a_model_omits_the_model_flag(monkeypatch, tmp_path):
    def fake_mkstemp(suffix, prefix):
        return 7, str(tmp_path / "prompt.txt")

    popen = Mock(side_effect=OSError("boom"))

    def discard_write(fd, data):
        return len(data)

    def discard(value):
        return None

    def no_model():
        return ""

    monkeypatch.setattr(tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(invoke.os, "write", discard_write)
    monkeypatch.setattr(invoke.os, "close", discard)
    monkeypatch.setattr(invoke.os, "unlink", discard)
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(invoke, "_resolve_model", no_model)
    _install_binary(monkeypatch, "/usr/bin/cli")

    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_cli("x" * 1_000_001, _backend(model=""), timeout_s=5)

    script = popen.call_args.args[0][2]
    assert "--model" not in script
    assert script.endswith("--output-format json")


def test_a_short_prompt_is_passed_as_an_argument(monkeypatch):
    popen = Mock(side_effect=OSError("boom"))
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    _install_binary(monkeypatch, "/usr/bin/cli")

    with pytest.raises(invoke.LLMInvokeError):
        invoke._invoke_cli("short prompt", _backend(), timeout_s=5)

    assert popen.call_args.args[0] == [
        "/usr/bin/cli", "-p", "short prompt", "--model", "m", "--output-format", "json",
    ]


def test_popen_failure_names_the_os_error(monkeypatch):
    cause = OSError("exec format error")
    popen = Mock(side_effect=cause)
    clock = Mock(side_effect=[10.0, 10.5])
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=clock))
    _install_binary(monkeypatch, "/usr/bin/cli")

    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    error = caught.value
    assert str(error) == "LLM subprocess failed: exec format error"
    assert error.exit_code == -1
    assert error.stderr == "exec format error"
    assert error.duration_s == 0.5
    assert error.__cause__ is cause


def test_nonzero_exit_names_the_code(monkeypatch):
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.return_value = ("", "permission denied")
    proc.returncode = 127
    popen = Mock(return_value=proc)
    clock = Mock(side_effect=[10.0, 12.0])
    monkeypatch.setattr(
        invoke, "subprocess",
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(invoke, "_active_proc", None)
    _install_binary(monkeypatch, "/usr/bin/cli")

    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    error = caught.value
    assert str(error) == "LLM subprocess exited with code 127"
    assert error.exit_code == 127
    assert error.stderr == "permission denied"
    assert error.duration_s == 2.0
    assert invoke._active_proc is None


def test_non_json_stdout_keeps_the_parse_error(monkeypatch):
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.return_value = ("this is not json", "")
    proc.returncode = 0
    popen = Mock(return_value=proc)
    monkeypatch.setattr(
        invoke, "subprocess",
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(invoke, "_active_proc", None)
    _install_binary(monkeypatch, "/usr/bin/cli")

    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    error = caught.value
    assert error.exit_code == 0
    assert str(error).startswith("LLM subprocess returned non-JSON stdout -- JSONDecodeError:")
    assert "stdout[:500]: 'this is not json'" in str(error)
    assert error.__cause__ is not None


def test_a_stream_of_events_uses_the_last_result(monkeypatch):
    """Current CLI versions emit an event array. The last result event wins."""
    events = [
        {"type": "result", "result": '{"findings": []}', "usage": {"input_tokens": 1}},
        {"type": "progress"},
        {"type": "result", "result": '{"findings": [1]}', "usage": {"input_tokens": 9}},
    ]
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.return_value = (json.dumps(events), "")
    proc.returncode = 0
    popen = Mock(return_value=proc)
    monkeypatch.setattr(
        invoke, "subprocess",
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(invoke, "_active_proc", None)
    _install_binary(monkeypatch, "/usr/bin/cli")

    result = invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    assert result.content == {"findings": [1]}
    assert result.usage.input_tokens == 9
