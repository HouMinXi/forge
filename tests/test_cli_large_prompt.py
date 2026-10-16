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
RECORDER = r'''#!{python}
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
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    out = tmp_path / "seen.json"
    path = tmp_path / "fake-cli"
    path.write_text(RECORDER.format(python=sys.executable, out=str(out)))
    path.chmod(0o755)
    monkeypatch.setattr(invoke, "shutil", SimpleNamespace(which=lambda name: str(path)))
    return out


def _backend(**kw):
    return BackendConfig(name="cli-test", type="cli", command="fake-cli", model="m", **kw)


@pytest.mark.parametrize("size", [131_073, 200_000, 1_000_001])
def test_prompt_over_max_arg_strlen_succeeds(fake_cli, size):
    prompt = "H" * 10 + "é" * ((size - 20) // 2) + "T" * 10
    result = invoke._invoke_cli(prompt, _backend(), timeout_s=60)
    assert result.content == {"ok": True}
    seen = json.loads(fake_cli.read_text())
    assert seen["via"] == "stdin"
    assert seen["len"] == len(prompt.encode())
    assert seen["head"] == prompt[:20] and seen["tail"] == prompt[-20:]
    assert seen["argv"] == ["-p", "--model", "--output-format"]


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

    (cfg,) = load_backend_configs({"backends": {"c": {"type": "cli", "command": "x", "prompt_via": "stdin"}}})
    assert cfg.prompt_via == "stdin"
    (cfg,) = load_backend_configs({"backends": {"c": {"type": "cli", "command": "x"}}})
    assert cfg.prompt_via == "auto"
    with pytest.raises(CliError):
        load_backend_configs({"backends": {"c": {"type": "cli", "command": "x", "prompt_via": "file"}}})
