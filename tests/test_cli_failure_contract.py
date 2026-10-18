"""A CLI invocation reports why it failed before any model output exists.

_invoke_cli builds a command, runs it, and reads the result. The
failure it raises is the only record of which step broke, and the
suite never checked the command shape or the error text, so a mutant
that rewrote either one still passed.
"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig


def _backend(command="forge-test-cli", model="m"):
    return BackendConfig(name="cli-test", type="cli", command=command, model=model)


def _install_binary(monkeypatch, path):
    monkeypatch.setattr(invoke, "shutil", SimpleNamespace(which=lambda name: path))


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


def test_a_large_prompt_is_sent_on_stdin(monkeypatch):
    # #108: no single argv string may exceed MAX_ARG_STRLEN, so a large
    # prompt goes on stdin with -p and no prompt argument.
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.return_value = (json.dumps({"type": "result", "result": "{}"}), "")
    proc.returncode = 0
    popen = Mock(return_value=proc)
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    _install_binary(monkeypatch, "/opt/my bin/cli")

    prompt = "x" * (invoke.CLI_ARGV_PROMPT_LIMIT + 1)
    invoke._invoke_cli(prompt, _backend(model="sonnet 4", command="claude"), timeout_s=5)

    assert popen.call_args.args[0] == [
        "/opt/my bin/cli", "-p", "--model", "sonnet 4", "--output-format", "json",
        "--input-format", "text",
    ]
    assert popen.call_args.kwargs["stdin"] == subprocess.PIPE
    assert proc.communicate.call_args.kwargs["input"] == prompt


def test_a_large_stdin_prompt_without_a_model_omits_the_model_flag(monkeypatch):
    proc = Mock(spec=subprocess.Popen)
    proc.communicate.return_value = (json.dumps({"type": "result", "result": "{}"}), "")
    proc.returncode = 0
    popen = Mock(return_value=proc)
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(invoke, "_resolve_model", lambda: "")
    _install_binary(monkeypatch, "/usr/bin/claude")

    prompt = "x" * 1_000_001
    invoke._invoke_cli(prompt, _backend(command="claude", model=""), timeout_s=5)

    assert popen.call_args.args[0] == [
        "/usr/bin/claude", "-p", "--output-format", "json", "--input-format", "text",
    ]
    assert popen.call_args.kwargs["stdin"] == subprocess.PIPE
    assert proc.communicate.call_args.kwargs["input"] == prompt


def test_a_short_prompt_is_passed_as_an_argument(monkeypatch):
    popen = Mock(side_effect=OSError("boom"))
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
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
    monkeypatch.setattr(invoke, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
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
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired),
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
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired),
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
        SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(invoke, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(invoke, "_active_proc", None)
    _install_binary(monkeypatch, "/usr/bin/cli")

    result = invoke._invoke_cli("prompt", _backend(), timeout_s=5)

    assert result.content == {"findings": [1]}
    assert result.usage.input_tokens == 9
