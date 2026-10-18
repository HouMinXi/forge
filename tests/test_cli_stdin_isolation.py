"""#111: CLI backend children and MCP-spawned code-forge never inherit stdin."""

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge import mcp_server
from code_forge.backend import BackendConfig
from code_forge.llm_invoke import llm_invoke

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _mock_proc():
    from unittest.mock import MagicMock

    proc = MagicMock()
    proc.communicate.return_value = ('{"ok": true}', "")
    proc.returncode = 0
    proc.pid = 4242
    return proc


def test_argv_prompt_cli_gets_devnull_stdin():
    backend = BackendConfig(name="t", type="cli", model="", command="")
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("code_forge.llm_invoke.subprocess.Popen", return_value=_mock_proc()) as popen,
    ):
        llm_invoke("small prompt", backend=backend)
    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert "small prompt" in popen.call_args.args[0]


def test_stdin_prompt_cli_still_gets_a_pipe():
    backend = BackendConfig(name="t", type="cli", model="", command="")
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("code_forge.llm_invoke.subprocess.Popen", return_value=_mock_proc()) as popen,
    ):
        llm_invoke("x" * 1_100_000, backend=backend)
    assert popen.call_args.kwargs["stdin"] is subprocess.PIPE


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shebang fixture")
def test_argv_cli_does_not_block_on_an_inherited_open_pipe(tmp_path):
    """forge's own stdin is a pipe that never reaches EOF (the MCP stdio pipe);
    a CLI that reads stdin must see EOF at once, not block until the timeout."""
    fake = tmp_path / "fake-cli"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "data = sys.stdin.read()\n"
        "print(json.dumps({'stdin_bytes': len(data)}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    driver = textwrap.dedent(f"""
        from code_forge.backend import BackendConfig
        from code_forge.llm_invoke import llm_invoke
        backend = BackendConfig(name="t", type="cli", model="", command={str(fake)!r}, prompt_via="argv")
        print(llm_invoke("hello", backend=backend, timeout_s=8).content)
    """)
    env = {**os.environ, "PYTHONPATH": SRC}
    read_end, write_end = os.pipe()  # we hold write_end open: stdin never reaches EOF
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", driver], stdin=read_end, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
        )
        os.close(read_end)
        try:
            out, err = child.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            pytest.fail("CLI child blocked reading an inherited stdin pipe")
    finally:
        os.close(write_end)
    assert child.returncode == 0, err
    assert "'stdin_bytes': 0" in out, out


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["simple", "budgeted"])
async def test_mcp_spawns_code_forge_with_devnull_stdin(monkeypatch, tmp_path, which):
    seen = {}

    class Stop(Exception):
        pass

    async def spawn(*args, **kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(Stop):
        if which == "simple":
            await mcp_server._run_cli_simple("--version", workspace=tmp_path)
        else:
            await mcp_server._run_cli_budgeted("--version", workspace=tmp_path)
    assert seen["stdin"] is asyncio.subprocess.DEVNULL
