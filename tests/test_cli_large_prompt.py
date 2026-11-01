# SPDX-License-Identifier: Apache-2.0
"""Issue #108: a CLI prompt larger than Linux MAX_ARG_STRLEN (131072 bytes)
must reach the CLI instead of failing execve with E2BIG."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import code_forge.llm_invoke as invoke
from code_forge.backend import BackendConfig

# A fake CLI honouring the documented contract: prompt as the positional
# after -p, or on stdin when no prompt argument is given.
RECORDER = r"""#!{python}
import json, sys
args = sys.argv[1:]
prompt = None
if args and args[0] == "-p" and len(args) > 1 and not args[1].startswith("--"):
    prompt, via = args[1], "argv"
else:
    prompt, via = sys.stdin.read(), "stdin"
with open({out!r}, "w") as f:
    json.dump({{"via": via, "len": len(prompt.encode()), "head": prompt[:20], "tail": prompt[-20:], "argv": args[:1] + [a for a in args[1:] if a.startswith("--")]}}, f)
sys.stdout.write(json.dumps({{"type": "result", "result": json.dumps({{"ok": True}})}}))
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    out = tmp_path / "seen.json"
    path = tmp_path / "fake-cli"
    path.write_text(RECORDER.format(python=sys.executable, out=str(out)))
    path.chmod(0o755)
    monkeypatch.setattr(invoke, "shutil", SimpleNamespace(which=lambda name: str(path)))
    return out


def _backend(command="claude", **kw):
    # which() is patched, so "claude" resolves to the fake recorder; the name
    # is what auto mode uses to decide whether stdin is known to work.
    return BackendConfig(name="cli-test", type="cli", command=command, model="m", **kw)


@pytest.mark.parametrize("size", [131_073, 200_000, 1_000_001])
def test_prompt_over_max_arg_strlen_succeeds(fake_cli, size):
    prompt = "H" * 10 + "é" * ((size - 20) // 2) + "T" * 10
    result = invoke._invoke_cli(prompt, _backend(), timeout_s=60)
    assert result.content == {"ok": True}
    seen = json.loads(fake_cli.read_text())
    assert seen["via"] == "stdin"
    assert seen["len"] == len(prompt.encode())
    assert seen["head"] == prompt[:20] and seen["tail"] == prompt[-20:]
    assert seen["argv"] == ["-p", "--model", "--output-format", "--input-format"]


def test_small_prompt_keeps_argv(fake_cli):
    result = invoke._invoke_cli("short prompt", _backend(), timeout_s=60)
    assert result.content == {"ok": True}
    assert json.loads(fake_cli.read_text())["via"] == "argv"


def test_prompt_via_argv_forces_argv_small(fake_cli):
    invoke._invoke_cli("short", _backend(prompt_via="argv"), timeout_s=60)
    assert json.loads(fake_cli.read_text())["via"] == "argv"


def test_prompt_via_stdin_forces_stdin_small(fake_cli):
    invoke._invoke_cli("short", _backend(prompt_via="stdin"), timeout_s=60)
    seen = json.loads(fake_cli.read_text())
    assert seen["via"] == "stdin" and seen["len"] == 5


def test_threshold_boundary(fake_cli):
    invoke._invoke_cli("a" * invoke.CLI_ARGV_PROMPT_LIMIT, _backend(), timeout_s=60)
    assert json.loads(fake_cli.read_text())["via"] == "argv"
    invoke._invoke_cli("a" * (invoke.CLI_ARGV_PROMPT_LIMIT + 1), _backend(), timeout_s=60)
    assert json.loads(fake_cli.read_text())["via"] == "stdin"
    assert invoke.CLI_ARGV_PROMPT_LIMIT < 131072


def test_prompt_via_config():
    from code_forge.backend import load_backend_configs
    from code_forge.errors import CliError

    (cfg,) = load_backend_configs(
        {"backends": {"c": {"type": "cli", "command": "x", "prompt_via": "stdin"}}}
    )
    assert cfg.prompt_via == "stdin"
    (cfg,) = load_backend_configs({"backends": {"c": {"type": "cli", "command": "x"}}})
    assert cfg.prompt_via == "auto"
    with pytest.raises(CliError):
        load_backend_configs({"backends": {"c": {"type": "cli", "command": "x", "prompt_via": "file"}}})


def test_custom_proxy_auto_large_prompt_fails_loudly(fake_cli, monkeypatch):
    calls = []
    monkeypatch.setattr(
        invoke.subprocess, "Popen", lambda *a, **k: calls.append(a) or pytest.fail("spawned")
    )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_cli("x" * 200_000, _backend(command="/usr/local/bin/my-proxy"), timeout_s=60)
    assert "prompt_via: stdin" in str(caught.value)
    assert calls == []


def test_custom_proxy_opted_into_stdin_gets_prompt_without_input_format(fake_cli):
    prompt = "y" * 200_000
    invoke._invoke_cli(prompt, _backend(command="my-proxy", prompt_via="stdin"), timeout_s=60)
    seen = json.loads(fake_cli.read_text())
    assert seen["via"] == "stdin" and seen["len"] == len(prompt)
    assert seen["argv"] == ["-p", "--model", "--output-format"]


def test_custom_proxy_small_prompt_unchanged(fake_cli):
    invoke._invoke_cli("short", _backend(command="my-proxy"), timeout_s=60)
    assert json.loads(fake_cli.read_text())["via"] == "argv"


def test_windows_threshold(monkeypatch):
    assert invoke._argv_prompt_limit() == invoke.CLI_ARGV_PROMPT_LIMIT
    monkeypatch.setattr(invoke.os, "name", "nt")
    assert invoke._argv_prompt_limit() == invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS == 16384


def test_windows_mid_size_prompt_uses_stdin_for_claude(fake_cli, monkeypatch):
    monkeypatch.setattr(
        invoke, "_argv_prompt_limit", lambda binary="": invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS
    )
    invoke._invoke_cli("z" * 20_000, _backend(), timeout_s=60)
    assert json.loads(fake_cli.read_text())["via"] == "stdin"


@pytest.mark.parametrize("value", [["stdin"], {"a": 1}, 3, True])
def test_prompt_via_non_string_is_a_config_error(value):
    from code_forge.backend import load_backend_configs
    from code_forge.errors import CliError

    with pytest.raises(CliError, match="invalid prompt_via"):
        load_backend_configs({"backends": {"c": {"type": "cli", "command": "x", "prompt_via": value}}})


def test_prompt_via_rejected_on_api_backend():
    from code_forge.backend import load_backend_configs
    from code_forge.errors import CliError

    entry = {
        "type": "api",
        "format": "openai",
        "base_url": "https://example.invalid/v1",
        "api_key_env": "K",
        "model": "m",
        "prompt_via": "stdin",
    }
    with pytest.raises(CliError, match="only valid on cli"):
        load_backend_configs({"backends": {"a": entry}})


def test_windows_batch_threshold(monkeypatch):
    monkeypatch.setattr(invoke.os, "name", "nt")
    assert invoke._argv_prompt_limit(r"C:\npm\claude.cmd") == invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS_BATCH
    assert invoke._argv_prompt_limit(r"C:\tools\proxy.BAT") == invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS_BATCH
    assert invoke._argv_prompt_limit(r"C:\bin\claude.exe") == invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS
    assert invoke.CLI_ARGV_PROMPT_LIMIT_WINDOWS_BATCH < 8191


def test_claude_cmd_is_known_stdin_reader(fake_cli):
    prompt = "c" * 200_000
    invoke._invoke_cli(prompt, _backend(command="claude.cmd"), timeout_s=60)
    seen = json.loads(fake_cli.read_text())
    assert seen["via"] == "stdin" and seen["len"] == len(prompt)
    assert "--input-format" in seen["argv"]


def test_prompt_via_null_means_default_like_other_fields():
    # Same as other optional backend fields (e.g. timeout_s: null -> default).
    from code_forge.backend import load_backend_configs

    (cfg,) = load_backend_configs(
        {"backends": {"c": {"type": "cli", "command": "x", "prompt_via": None}}}
    )
    assert cfg.prompt_via == "auto"
    (other,) = load_backend_configs(
        {"backends": {"c": {"type": "cli", "command": "x", "timeout_s": None}}}
    )
    assert other.timeout_s == 0
