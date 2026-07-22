# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for MCP server tool handlers."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_forge.user_config import (
    load_user_backends,
    user_config_path,
)
from code_forge.mcp_server import (
    _check_backend,
    _job_cap_s,
    _make_result,
    _make_simple_result,
    _resolve_workspace,
    _run_cli_budgeted,
    _run_cli_simple,
    forge_gate_check,
    forge_init,
    forge_job_status,
    forge_resolve_outlet,
    forge_review,
    forge_trust,
    mcp,
)
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent


@pytest.fixture(autouse=True)
def _no_gate_yaml_outlet():
    """Prevent gate.yaml outlet: sampling from leaking into subprocess tests."""
    with patch("code_forge.outlet_resolver.load_outlet_from_gate", return_value=None):
        yield


# -- tool registration --


def test_tool_list_contains_six_tools():
    tools = mcp._tool_manager._tools
    assert len(tools) == 6
    assert set(tools.keys()) == {
        "forge_review",
        "forge_gate_check",
        "forge_init",
        "forge_trust",
        "forge_resolve_outlet",
        "forge_job_status",
    }


def test_tool_annotations_resolve_outlet_readonly():
    tool = mcp._tool_manager._tools["forge_resolve_outlet"]
    assert tool.annotations.readOnlyHint is True


def test_tool_annotations_init_idempotent():
    tool = mcp._tool_manager._tools["forge_init"]
    assert tool.annotations.idempotentHint is True


# -- pre-flight --


def test_preflight_empty_backends_raises_tool_error():
    with (
        patch.object(Path, "exists", return_value=True),
        patch("code_forge.cli._load_gate_backends", return_value=([], {})),
        patch("code_forge.user_config.load_user_backends", return_value={}),
    ):
        with pytest.raises(ToolError, match="No review backends configured in"):
            _check_backend(_resolve_workspace())


def test_preflight_empty_backends_names_workspace():
    """The zero-backend error must say WHICH workspace and gate.yaml it checked,
    so a wrong-workspace resolution is diagnosable."""
    import re

    with (
        patch.object(Path, "exists", return_value=True),
        patch("code_forge.cli._load_gate_backends", return_value=([], {})),
        patch("code_forge.user_config.load_user_backends", return_value={}),
    ):
        with pytest.raises(ToolError, match="FORGE_PROJECT_DIR"):
            _check_backend(_resolve_workspace())
        with pytest.raises(
            ToolError, match=re.escape(str(_resolve_workspace()))
        ):
            _check_backend(_resolve_workspace())


def test_preflight_nonempty_backends_passes():
    from code_forge.backend import BackendConfig

    cfg = BackendConfig(
        name="test", type="api", model="m", format="openai",
        base_url="http://x", api_key_env="TEST_KEY_123",
    )
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([cfg], {"backends": {"test": {}}}),
        ),
        patch("code_forge.user_config.load_user_backends", return_value={}),
        patch.dict("os.environ", {"TEST_KEY_123": "sk-fake"}),
    ):
        _check_backend(_resolve_workspace())  # no exception


def test_preflight_missing_api_key_env_raises():
    """N3: missing API key env must be caught early with actionable message."""
    from code_forge.backend import BackendConfig

    cfg = BackendConfig(
        name="mimo-pro", type="api", model="m", format="anthropic",
        base_url="http://x", api_key_env="MIMO_PRO_API_KEY",
    )
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([cfg], {}),
        ),
        patch("code_forge.user_config.load_user_backends", return_value={}),
        patch.dict("os.environ", {}, clear=False),
    ):
        # Ensure the key is NOT set
        import os
        os.environ.pop("MIMO_PRO_API_KEY", None)
        with pytest.raises(ToolError, match="MIMO_PRO_API_KEY"):
            _check_backend(_resolve_workspace())


def test_preflight_partial_keys_warns_but_passes():
    """Mixed key availability: one backend has key, one doesn't -> pass."""
    from code_forge.backend import BackendConfig

    cfg_ok = BackendConfig(
        name="ok", type="api", model="m", format="openai",
        base_url="http://x", api_key_env="PARTIAL_OK_KEY",
    )
    cfg_nokey = BackendConfig(
        name="nokey", type="api", model="m", format="anthropic",
        base_url="http://x", api_key_env="PARTIAL_MISSING_KEY",
    )
    env = {"PARTIAL_OK_KEY": "sk-fake", "PARTIAL_MISSING_KEY": ""}
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([cfg_ok, cfg_nokey], {}),
        ),
        patch("code_forge.user_config.load_user_backends", return_value={}),
        patch.dict("os.environ", env, clear=False),
    ):
        # Remove via patch.dict-tracked key so it restores on exit.
        del os.environ["PARTIAL_MISSING_KEY"]
        _check_backend(_resolve_workspace())  # should pass, not raise


def test_preflight_catches_cli_error():
    from code_forge.errors import CliError

    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=CliError("corrupt"),
        ),
    ):
        with pytest.raises(ToolError):
            _check_backend(_resolve_workspace())


def test_preflight_catches_value_error():
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=ValueError("bad yaml"),
        ),
    ):
        with pytest.raises(ToolError):
            _check_backend(_resolve_workspace())


def test_preflight_catches_os_error():
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=OSError("permission"),
        ),
    ):
        with pytest.raises(ToolError):
            _check_backend(_resolve_workspace())


def test_preflight_gate_yaml_missing_raises():
    with (
        patch.object(Path, "exists", return_value=False),
        patch("code_forge.cli._load_gate_backends") as mock_load,
        patch.dict(os.environ, {}, clear=False),
    ):
        with pytest.raises(ToolError, match="gate.yaml not found"):
            _check_backend(_resolve_workspace())
        mock_load.assert_not_called()


def test_preflight_gate_yaml_exists_proceeds():
    from code_forge.backend import BackendConfig

    cfg = BackendConfig(
        name="deepseek", type="api", model="m", format="openai",
        base_url="http://x", api_key_env="DEEPSEEK_API_KEY",
    )
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([cfg], {"backends": {"deepseek": {}}}),
        ),
        patch("code_forge.user_config.load_user_backends", return_value={}),
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-fake"}),
    ):
        _check_backend(_resolve_workspace())  # no exception


# -- CLI runners --


@pytest.mark.asyncio
async def test_run_cli_simple_assembles_args():
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
    mock_proc.returncode = 0
    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as mock_exec:
        stdout, stderr, code = await _run_cli_simple(
            "resolve-outlet", workspace=_resolve_workspace())
        mock_exec.assert_called_once_with(
            "code-forge",
            "resolve-outlet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_resolve_workspace()),
        )
        assert stdout == "ok\n"
        assert code == 0


@pytest.mark.asyncio
async def test_run_cli_budgeted_inline_returns_tuple():
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"findings", b""))
    mock_proc.returncode = 1
    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        result = await _run_cli_budgeted(
            "review", "--no-color", workspace=_resolve_workspace(), budget=5.0)
        assert isinstance(result, tuple)
        assert len(result) == 4
        stdout, exit_code, elapsed, stderr = result
        assert stdout == "findings"
        assert exit_code == 1
        assert isinstance(elapsed, float)
        assert stderr == ""


@pytest.mark.asyncio
async def test_run_cli_budgeted_captures_stderr():
    """N1: stderr captured from tempfile, not discarded."""
    stderr_msg = "CliError: no backend configured\n"
    mock_proc = MagicMock()
    # communicate returns (stdout, None) when stderr is a file
    mock_proc.communicate = AsyncMock(return_value=(b"", None))
    mock_proc.returncode = 2

    real_ntf = tempfile.NamedTemporaryFile

    def _fake_ntf(**kwargs):
        tf = real_ntf(**kwargs)
        tf.write(stderr_msg)
        tf.flush()
        return tf

    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ), patch("code_forge.mcp_server.tempfile.NamedTemporaryFile", side_effect=_fake_ntf):
        result = await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=5.0)
        stdout, exit_code, elapsed, stderr = result
        assert stdout == ""
        assert exit_code == 2
        assert stderr == stderr_msg


@pytest.mark.asyncio
async def test_run_cli_budgeted_timeout_returns_task_and_proc():
    mock_proc = MagicMock()

    async def _slow_comm():
        await asyncio.sleep(100)
        return (b"late", None)

    mock_proc.communicate = _slow_comm
    mock_proc.returncode = None

    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        result = await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=0.01)
        assert isinstance(result, tuple)
        assert len(result) == 3
        inner_task, proc, stderr_path = result
        assert isinstance(inner_task, asyncio.Task)
        assert proc is mock_proc
        assert stderr_path.endswith(".log")
        inner_task.cancel()
        try:
            await inner_task
        except (asyncio.CancelledError, Exception):
            pass
        # cleanup tempfile
        try:
            os.unlink(stderr_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_run_cli_budgeted_cancelled_kills_proc():
    """CancelledError triggers _kill_and_reap: terminate + cancel task."""
    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.terminate = MagicMock()
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock()

    async def _slow():
        await asyncio.sleep(100)
        return (b"", b"")

    mock_proc.communicate = _slow

    async def _run():
        with patch(
            "code_forge.mcp_server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            return await _run_cli_budgeted(
                "review", workspace=_resolve_workspace(), budget=100.0)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Unified kill sequence: terminate first, kill only on grace timeout
    mock_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_kill_and_reap_already_exited_skips_kill():
    """If proc already exited, _kill_and_reap is a no-op."""
    from code_forge.mcp_server import _kill_and_reap

    mock_proc = MagicMock()
    mock_proc.returncode = 0  # already exited
    mock_proc.kill = MagicMock()
    mock_task = MagicMock()

    await _kill_and_reap(mock_proc, mock_task)
    mock_task.cancel.assert_called_once()
    mock_proc.kill.assert_not_called()


# -- allow_main env threading (BLOCKER 1 fix) --


@pytest.mark.asyncio
async def test_allow_main_true_injects_env_without_polluting_os():
    """allow_main=True passes FORGE_ALLOW_MAIN=1 to the child process
    via the env kwarg without mutating os.environ."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0
    with (
        patch(
            "code_forge.mcp_server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("FORGE_ALLOW_MAIN", None)
        await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=5.0,
            env={**os.environ, "FORGE_ALLOW_MAIN": "1"},
        )
        call_env = mock_exec.call_args.kwargs.get("env")
        assert call_env is not None
        assert call_env["FORGE_ALLOW_MAIN"] == "1"
        assert "FORGE_ALLOW_MAIN" not in os.environ


@pytest.mark.asyncio
async def test_allow_main_false_passes_none_env():
    """allow_main=False passes env=None (inherits parent env)."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0
    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as mock_exec:
        await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=5.0,
            env=None,
        )
        assert mock_exec.call_args.kwargs.get("env") is None


@pytest.mark.asyncio
async def test_allow_main_preserves_preexisting_server_env():
    """If the server process already has FORGE_ALLOW_MAIN set,
    an allow_main=True call must not delete it afterward."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0
    with (
        patch(
            "code_forge.mcp_server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ),
        patch.dict(os.environ, {"FORGE_ALLOW_MAIN": "1"}, clear=False),
    ):
        await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=5.0,
            env={**os.environ, "FORGE_ALLOW_MAIN": "1"},
        )
        assert os.environ.get("FORGE_ALLOW_MAIN") == "1"


@pytest.mark.asyncio
async def test_empty_env_dict_raises_value_error():
    """Empty env dict must be rejected -- it would strip PATH and all
    environment variables, causing the subprocess to fail silently."""
    with pytest.raises(ValueError, match="non-empty dict"):
        await _run_cli_budgeted(
            "review", workspace=_resolve_workspace(), budget=5.0,
            env={},
        )


# -- result formatting --


def test_make_result_returns_call_tool_result():
    result = _make_result("findings here", 1, 5.0)
    assert isinstance(result, CallToolResult)
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "findings here"
    assert result.structuredContent["verdict"] == "FAIL"
    assert result.structuredContent["findings_count"] is None


def test_make_result_merges_stderr_on_nonzero_exit():
    """N1: non-zero exit must surface stderr so the caller can diagnose."""
    result = _make_result("", 2, 1.0, stderr="CliError: no backend\n")
    text = result.content[0].text
    assert "--- stderr ---" in text
    assert "CliError: no backend" in text
    assert result.structuredContent["output"] == text


def test_make_result_omits_stderr_on_zero_exit():
    """Zero exit = success; stderr (warnings) should not pollute output."""
    result = _make_result("all good", 0, 1.0, stderr="deprecation warning\n")
    assert "stderr" not in result.content[0].text
    assert result.structuredContent["output"] == "all good"


def test_make_simple_result_returns_call_tool_result():
    result = _make_simple_result("ok", 0)
    assert isinstance(result, CallToolResult)
    assert result.content[0].text == "ok"
    assert result.structuredContent["exit_code"] == 0


# -- handler tests --


@pytest.mark.asyncio
async def test_forge_review_calls_preflight_then_cli():
    with (
        patch("code_forge.mcp_server._check_backend") as mock_check,
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("output", 0, 1.5, ""),
        ) as mock_cli,
    ):
        mock_check.return_value = Path("/tmp/fake")
        result = await forge_review()
        mock_check.assert_called_once()
        args = mock_cli.call_args
        cli_args = args[0] if args[0] else args[1].get("args", [])
        assert "review" in cli_args
        assert isinstance(result, CallToolResult)


@pytest.mark.asyncio
async def test_forge_review_allow_main_passes_env_to_cli():
    """Integration: forge_review(allow_main=True) constructs child_env
    with FORGE_ALLOW_MAIN=1 and passes it to _run_cli_budgeted."""
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("output", 0, 1.5, ""),
        ) as mock_cli,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("FORGE_ALLOW_MAIN", None)
        await forge_review(allow_main=True)
        call_env = mock_cli.call_args.kwargs.get("env")
        assert call_env is not None
        assert call_env["FORGE_ALLOW_MAIN"] == "1"
        assert "FORGE_ALLOW_MAIN" not in os.environ


@pytest.mark.asyncio
async def test_forge_review_with_backend_param_validates():
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock,
              return_value=Path("/tmp/fake")),
        patch("code_forge.mcp_server._backend_names_for",
              return_value=["mimo-pro", "deepseek"]),
    ):
        with pytest.raises(ToolError, match="Unknown backend"):
            await forge_review(backend="invalid-backend")


@pytest.mark.asyncio
async def test_forge_review_with_valid_backend():
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock,
              return_value=Path("/tmp/fake")),
        patch("code_forge.mcp_server._backend_names_for",
              return_value=["mimo-pro"]),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("output", 0, 1.0, ""),
        ) as mock_cli,
    ):
        await forge_review(backend="mimo-pro")
        args = mock_cli.call_args[0]
        assert "--backend" in args
        assert "mimo-pro" in args


@pytest.mark.asyncio
async def test_forge_review_with_contract_writes_tempfile():
    written_path = None

    async def _fake_budgeted(*args, **kwargs):
        nonlocal written_path
        # Find the --contract arg
        for i, a in enumerate(args):
            if a == "--contract" and i + 1 < len(args):
                written_path = args[i + 1]
                break
        return ("output", 0, 1.0, "")

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            side_effect=_fake_budgeted,
        ),
    ):
        await forge_review(contract="check X")
        assert written_path is not None
        # Tempfile was deleted after inline completion
        # (may already be cleaned up by the handler)


@pytest.mark.asyncio
async def test_forge_review_timeout_returns_job_ref():
    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, "/tmp/fake.log"),
        ),
        patch(
            "code_forge.mcp_server._job_cap_s", return_value=900.0,
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ),
    ):
        result = await forge_review()
        assert isinstance(result, CallToolResult)
        assert result.structuredContent["job_id"] == "test-job-id"
        assert result.structuredContent["status"] == "running"
        assert result.structuredContent["poll_after_seconds"] == 10


@pytest.mark.asyncio
async def test_forge_review_timeout_passes_tempfile_to_start_job():
    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, "/tmp/fake-stderr.log"),
        ),
        patch(
            "code_forge.mcp_server._job_cap_s", return_value=900.0,
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ) as mock_start,
    ):
        await forge_review(contract="X")
        _, kwargs = mock_start.call_args
        assert kwargs.get("tempfile_path") is not None
        assert kwargs.get("stderr_log_path") == "/tmp/fake-stderr.log"
        assert kwargs.get("max_lifetime_s") == 900.0


@pytest.mark.asyncio
async def test_forge_gate_check_calls_preflight():
    with (
        patch("code_forge.mcp_server._check_backend") as mock_check,
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("ok", 0, 0.5, ""),
        ) as mock_cli,
    ):
        await forge_gate_check()
        mock_check.assert_called_once()
        args = mock_cli.call_args[0]
        assert "gate-check" in args


@pytest.mark.asyncio
async def test_forge_gate_check_with_baseline():
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("ok", 0, 0.5, ""),
        ) as mock_cli,
    ):
        await forge_gate_check(baseline="HEAD~3")
        args = mock_cli.call_args[0]
        assert "--baseline" in args
        assert "HEAD~3" in args


@pytest.mark.asyncio
async def test_forge_gate_check_timeout_returns_job_ref():
    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, "/tmp/fake.log"),
        ),
        patch(
            "code_forge.mcp_server._job_cap_s", return_value=900.0,
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ) as mock_start,
    ):
        result = await forge_gate_check()
        assert isinstance(result, CallToolResult)
        assert result.structuredContent["job_id"] == "test-job-id"
        assert result.structuredContent["status"] == "running"
        mock_start.assert_called_once_with(
            mock_task, mock_proc,
            tempfile_path=None,
            stderr_log_path="/tmp/fake.log",
            max_lifetime_s=900.0,
        )


@pytest.mark.asyncio
async def test_gate_check_start_job_cleans_up_on_raise(tmp_path):
    """Site C (forge_gate_check subprocess path): if start_job raises,
    the helper must unlink the stderr log. Bug-inject proof: delete the
    _dispatch_cli call at site C and replace with inline unguarded
    start_job -- this test must FAIL (stderr leaked)."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    stderr_log = tmp_path / "stderr.log"
    stderr_log.write_text("")

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, str(stderr_log)),
        ) as mock_run,
        patch("code_forge.mcp_server._job_cap_s", return_value=900.0),
        patch(
            "code_forge.mcp_server.start_job",
            side_effect=RuntimeError("start failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="start failed"):
            await forge_gate_check()

    # Verify the expected code path was exercised: _run_cli_budgeted
    # was called, confirming the subprocess dispatch route was taken.
    mock_run.assert_called_once()

    # If site C routes through _dispatch_cli, stderr is cleaned up.
    # If site C uses inline unguarded start_job, stderr leaks.
    assert not stderr_log.exists(), (
        "stderr log leaked -- site C may not route through _dispatch_cli"
    )


@pytest.mark.asyncio
async def test_forge_init_no_preflight():
    with (
        patch("code_forge.mcp_server._check_backend") as mock_check,
        patch(
            "code_forge.mcp_server._run_cli_simple",
            new_callable=AsyncMock,
            return_value=("initialized", "", 0),
        ) as mock_cli,
    ):
        await forge_init()
        mock_check.assert_not_called()
        args = mock_cli.call_args[0]
        assert "init" in args


@pytest.mark.asyncio
async def test_forge_init_force_flag():
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("initialized", "", 0),
    ) as mock_cli:
        await forge_init(force=True)
        args = mock_cli.call_args[0]
        assert "--force" in args


@pytest.mark.asyncio
async def test_forge_trust_calls_cli():
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("trusted", "", 0),
    ) as mock_cli:
        await forge_trust()
        args = mock_cli.call_args[0]
        assert "trust" in args


@pytest.mark.asyncio
async def test_forge_resolve_outlet_readonly():
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("outlet: inline", "", 0),
    ) as mock_cli:
        await forge_resolve_outlet()
        args = mock_cli.call_args[0]
        assert "resolve-outlet" in args


@pytest.mark.asyncio
async def test_forge_resolve_outlet_appends_workspace_context():
    """Diagnostic output names the workspace, gate.yaml, and backends so a
    wrong-workspace resolution is visible without further digging."""
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("subprocess", "", 0),
    ):
        result = await forge_resolve_outlet()
        text = result.structuredContent["output"]
        assert "subprocess" in text
        assert "workspace:" in text
        assert "gate.yaml:" in text
        assert "backends:" in text


# -- _dispatch_sampling fallback routing (by LLMInvokeError.kind) --


def _sampling_dispatch_patches(kind: str, backend_names: list):
    """Patches to drive _dispatch_sampling into its except-LLMInvokeError
    branch with a given failure kind, without a real session or machine."""
    from code_forge.llm_invoke import LLMInvokeError

    resolved = MagicMock(git_diff="diff --git a/f b/f", mode_hint="git")
    return (
        patch(
            "code_forge.mcp_server._build_review_context",
            return_value=(resolved, "hash", "repr"),
        ),
        patch("code_forge.machine.StateMachine", MagicMock()),
        patch(
            "code_forge.mcp_server.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=LLMInvokeError("sampling failed", kind=kind),
        ),
        patch(
            "code_forge.mcp_server._backend_names_for",
            return_value=backend_names,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["truncated", "empty", "stub_model", "no_json"]
)
async def test_dispatch_sampling_recoverable_kind_falls_back(kind):
    """Every recoverable failure kind routes to the subprocess fallback
    when a backend is configured (was truncation-only before)."""
    from code_forge.mcp_server import _dispatch_sampling

    p1, p2, p3, p4 = _sampling_dispatch_patches(kind, ["deepseek"])
    with p1, p2, p3, p4, patch(
        "code_forge.mcp_server._run_cli_budgeted",
        new_callable=AsyncMock,
        return_value=("fallback ran", 0, 1.0, ""),
    ) as mock_cli:
        result = await _dispatch_sampling(
            session=MagicMock(), committed=False,
            workspace=_resolve_workspace(),
        )
    args = mock_cli.call_args[0]
    assert "--outlet" in args and "subprocess" in args
    assert "--backend" in args and "deepseek" in args
    assert isinstance(result, CallToolResult)


@pytest.mark.asyncio
async def test_dispatch_sampling_recoverable_kind_no_backend_remediates():
    """Recoverable failure with NO backend raises ToolError that names
    the gate.yaml remediation instead of a bare error."""
    from code_forge.mcp_server import _dispatch_sampling

    p1, p2, p3, p4 = _sampling_dispatch_patches("empty", [])
    with p1, p2, p3, p4:
        with pytest.raises(ToolError, match="Configure an API backend"):
            await _dispatch_sampling(
                session=MagicMock(), committed=False,
                workspace=_resolve_workspace())


@pytest.mark.asyncio
async def test_dispatch_sampling_unknown_kind_never_falls_back():
    """An LLMInvokeError without a recoverable kind must NOT consume the
    subprocess fallback (kind routing, not message-text matching)."""
    from code_forge.mcp_server import _dispatch_sampling

    # Message contains the words "empty" and "truncated" -- under the old
    # substring matching this would have falsely triggered the fallback.
    from code_forge.llm_invoke import LLMInvokeError

    p1, p2, _, p4 = _sampling_dispatch_patches("", ["deepseek"])
    p3 = patch(
        "code_forge.mcp_server.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=LLMInvokeError(
            "backend returned an empty truncated response"
        ),
    )
    with p1, p2, p3, p4, patch(
        "code_forge.mcp_server._run_cli_budgeted",
        new_callable=AsyncMock,
    ) as mock_cli:
        with pytest.raises(ToolError, match="Sampling failed"):
            await _dispatch_sampling(
                session=MagicMock(), committed=False,
                workspace=_resolve_workspace())
    mock_cli.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_sampling_staged_gate_check_names_failure_kind():
    """Gate-check (staged) cannot use the review-only fallback; its error
    must name the actual failure kind, not claim truncation for all."""
    from code_forge.mcp_server import _dispatch_sampling

    p1, p2, p3, p4 = _sampling_dispatch_patches("stub_model", ["deepseek"])
    with p1, p2, p3, p4:
        with pytest.raises(ToolError, match="stub_model"):
            await _dispatch_sampling(
                session=MagicMock(), committed=False,
                workspace=_resolve_workspace(), staged=True,
            )


@pytest.mark.asyncio
async def test_forge_job_status_known_completed():
    with patch(
        "code_forge.mcp_server.get_job",
        return_value={
            "status": "completed",
            "result": {"stdout": "ok", "exit_code": 0, "verdict": "PASS"},
        },
    ):
        result = await forge_job_status(job_id="abc")
        assert isinstance(result, CallToolResult)
        assert result.structuredContent["status"] == "completed"


@pytest.mark.asyncio
async def test_forge_job_status_known_running():
    with patch(
        "code_forge.mcp_server.get_job",
        return_value={
            "status": "running",
            "result": None,
            "created_at": time.monotonic() - 30.0,
        },
    ):
        result = await forge_job_status(job_id="abc")
        assert result.structuredContent["status"] == "running"


@pytest.mark.asyncio
async def test_forge_job_status_unknown_raises():
    with patch("code_forge.mcp_server.get_job", return_value=None):
        with pytest.raises(ToolError, match="Unknown job_id.*may have restarted"):
            await forge_job_status(job_id="bad")


# -- no-stdout guard --


def test_no_print_in_production_modules():
    src_dir = Path(__file__).parent.parent / "src" / "code_forge"
    for name in ("mcp_server.py", "mcp_jobs.py"):
        path = src_dir / name
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "print(" not in stripped, (
                "print() found in %s line %d: %s" % (name, i, stripped)
            )


# -- Workspace resolution + user config tests --


class TestResolveWorkspace:
    """Tests for _resolve_workspace walkup and env override."""

    def test_walkup_from_subdirectory(self, tmp_path):
        """T1: walkup finds project root from a subdirectory."""
        root = tmp_path / "project"
        (root / ".code-forge").mkdir(parents=True)
        (root / ".code-forge" / "gate.yaml").write_text("test:\n  command: [true]\n")
        subdir = root / "src" / "deep"
        subdir.mkdir(parents=True)
        with patch("code_forge.mcp_server.Path.cwd", return_value=subdir):
            with patch("code_forge.mcp_server.Path.home", return_value=tmp_path / "fakehome"):
                result = _resolve_workspace()
        assert result == root

    def test_env_overrides_walkup(self, tmp_path):
        """T2: FORGE_PROJECT_DIR takes precedence over walkup."""
        env_root = tmp_path / "env-project"
        env_root.mkdir()
        walkup_root = tmp_path / "walkup-project"
        (walkup_root / ".code-forge").mkdir(parents=True)
        (walkup_root / ".code-forge" / "gate.yaml").write_text("test:\n  command: [true]\n")
        subdir = walkup_root / "src"
        subdir.mkdir()
        with patch.dict(os.environ, {"FORGE_PROJECT_DIR": str(env_root)}):
            with patch("code_forge.mcp_server.Path.cwd", return_value=subdir):
                result = _resolve_workspace()
        assert result == env_root.resolve()

    def test_no_marker_falls_through(self, tmp_path):
        """T3: no .code-forge/gate.yaml anywhere -> returns cwd as-is."""
        bare = tmp_path / "bare"
        bare.mkdir()
        with patch("code_forge.mcp_server.Path.cwd", return_value=bare):
            with patch("code_forge.mcp_server.Path.home", return_value=tmp_path / "fakehome"):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("FORGE_PROJECT_DIR", None)
                    result = _resolve_workspace()
        assert result == bare

    def test_home_skipped_by_walkup(self, tmp_path):
        """T4: $HOME with .code-forge/gate.yaml is skipped by walkup."""
        fake_home = tmp_path / "home"
        (fake_home / ".code-forge").mkdir(parents=True)
        (fake_home / ".code-forge" / "gate.yaml").write_text("test:\n  command: [true]\n")
        subdir = fake_home / "code" / "project"
        subdir.mkdir(parents=True)
        with patch("code_forge.mcp_server.Path.cwd", return_value=subdir):
            with patch("code_forge.mcp_server.Path.home", return_value=fake_home):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("FORGE_PROJECT_DIR", None)
                    result = _resolve_workspace()
        # Must NOT resolve to fake_home; falls through to cwd
        assert result == subdir


class TestUserConfig:
    """Tests for user-level config loading and backend merge."""

    def test_loader_returns_backends_dict(self, tmp_path):
        """T5a: _load_user_backends returns backends dict from config."""
        user_cfg = tmp_path / "config.yaml"
        user_cfg.write_text(
            "backends:\n"
            "  user-back:\n"
            "    model: user-model\n"
        )
        with patch("code_forge.user_config.user_config_path", return_value=user_cfg):
            result = load_user_backends()
        assert result == {"user-back": {"model": "user-model"}}

    def test_loader_no_config_returns_empty(self):
        """T5b-loader: _user_config_path None -> empty dict."""
        with patch("code_forge.user_config.user_config_path", return_value=None):
            result = load_user_backends()
        assert result == {}

    def test_backend_names_for_merges_project_first_user_appends(self, tmp_path):
        """T5c: _backend_names_for merges project first, user appends."""
        from code_forge.mcp_server import _backend_names_for

        user_cfg = tmp_path / "config.yaml"
        user_cfg.write_text(
            "backends:\n"
            "  shared:\n"
            "    model: user-model\n"
            "  user-only:\n"
            "    model: only-in-user\n"
        )
        project_gate = {
            "backends": {"shared": {"model": "project-model"}, "proj-only": {"model": "p"}}
        }
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        (gate_dir / "gate.yaml").write_text("backends:\n  shared:\n    model: pm\n")
        with (
            patch("code_forge.user_config.user_config_path", return_value=user_cfg),
            patch("code_forge.cli._load_gate_backends", return_value=([], project_gate)),
        ):
            names = _backend_names_for(tmp_path)
        # Project backends come first so fallback [0] is CLI-resolvable.
        assert set(names[:2]) == {"shared", "proj-only"}
        assert names[2] == "user-only"

    def test_lenient_loader_warns_on_backends_non_dict(self, tmp_path, caplog):
        """T5d: backends: is a list instead of dict -> warns, returns empty."""
        bad_cfg = tmp_path / "config.yaml"
        bad_cfg.write_text("backends:\n  - not-a-dict\n")
        import logging
        with caplog.at_level(logging.WARNING, logger="code_forge.user_config"):
            with patch("code_forge.user_config.user_config_path", return_value=bad_cfg):
                result = load_user_backends()
        assert result == {}
        assert "not a mapping" in caplog.text.lower()

    def test_lenient_loader_warns_on_malformed(self, tmp_path):
        """T6: malformed user config warns and returns empty, never crashes."""
        bad_cfg = tmp_path / "config.yaml"
        bad_cfg.write_text("not: [a, valid, {config")
        with patch("code_forge.user_config.user_config_path", return_value=bad_cfg):
            result = load_user_backends()
        assert result == {}

    def test_lenient_loader_warns_on_non_mapping(self, tmp_path):
        """T6b: non-mapping YAML warns and returns empty."""
        bad_cfg = tmp_path / "config.yaml"
        bad_cfg.write_text("- just\n- a\n- list\n")
        with patch("code_forge.user_config.user_config_path", return_value=bad_cfg):
            result = load_user_backends()
        assert result == {}

    def test_legacy_path_returns_with_warning(self, tmp_path, caplog):
        """T5b: legacy ~/.code-forge/gate.yaml found -> returns path + warns."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        legacy = fake_home / ".code-forge" / "gate.yaml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("backends:\n  old:\n    model: legacy\n")
        with patch("code_forge.mcp_server.Path.home", return_value=fake_home):
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(fake_home / ".config")}):
                import logging
                with caplog.at_level(logging.WARNING, logger="code_forge.user_config"):
                    result = user_config_path()
        assert result == legacy
        assert "legacy" in caplog.text.lower() or "move to" in caplog.text.lower()


    def test_forge_config_dir_env_overrides_xdg(self, tmp_path):
        """T8: FORGE_CONFIG_DIR takes precedence over XDG and legacy."""
        env_dir = tmp_path / "custom-config"
        env_dir.mkdir()
        (env_dir / "config.yaml").write_text(
            "backends:\n  env-backend:\n    model: from-env\n"
        )
        xdg_dir = tmp_path / ".config" / "code-forge"
        xdg_dir.mkdir(parents=True)
        (xdg_dir / "config.yaml").write_text(
            "backends:\n  xdg-backend:\n    model: from-xdg\n"
        )
        with patch.dict(os.environ, {
            "FORGE_CONFIG_DIR": str(env_dir),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        }):
            with patch("code_forge.mcp_server.Path.home", return_value=tmp_path):
                result = user_config_path()
        assert result == env_dir / "config.yaml"
        with patch("code_forge.user_config.user_config_path", return_value=result):
            backends = load_user_backends()
        assert "env-backend" in backends
        assert "xdg-backend" not in backends


    def test_forge_config_dir_env_absent_file(self, tmp_path, caplog):
        """T9: FORGE_CONFIG_DIR set but config.yaml absent -> None + warning."""
        empty_dir = tmp_path / "no-config"
        empty_dir.mkdir()
        with patch.dict(os.environ, {"FORGE_CONFIG_DIR": str(empty_dir)}):
            import logging
            with caplog.at_level(logging.WARNING, logger="code_forge.user_config"):
                result = user_config_path()
        assert result is None
        assert "not found" in caplog.text.lower()

    def test_xdg_config_home_empty_string_treated_as_unset(self, tmp_path):
        """T10: XDG_CONFIG_HOME='' falls back to ~/.config per XDG spec."""
        fake_home = tmp_path / "home"
        default_xdg = fake_home / ".config" / "code-forge"
        default_xdg.mkdir(parents=True)
        (default_xdg / "config.yaml").write_text(
            "backends:\n  default:\n    model: from-default\n"
        )
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}, clear=False):
            os.environ.pop("FORGE_CONFIG_DIR", None)
            with patch("code_forge.mcp_server.Path.home", return_value=fake_home):
                result = user_config_path()
        assert result == default_xdg / "config.yaml"


    def test_user_config_path_none_when_no_config_anywhere(self, tmp_path):
        """G1a: no XDG, no legacy, no env -> _user_config_path returns None."""
        fake_home = tmp_path / "empty_home"
        fake_home.mkdir()
        with (
            patch("code_forge.mcp_server.Path.home", return_value=fake_home),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(fake_home / ".config")}, clear=False),
        ):
            os.environ.pop("FORGE_CONFIG_DIR", None)
            result = user_config_path()
        assert result is None

    def test_loader_no_backends_key_returns_empty(self, tmp_path):
        """G1b: config.yaml exists but has no backends: key -> empty dict."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("outlet: sampling\n")
        with patch("code_forge.user_config.user_config_path", return_value=cfg):
            result = load_user_backends()
        assert result == {}

    def test_backend_names_for_non_dict_ignored(self, tmp_path):
        """G1c: project gate.yaml backends: is a list -> reset to empty."""
        from code_forge.mcp_server import _backend_names_for

        project_gate = {"backends": ["not", "a", "dict"]}
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        (gate_dir / "gate.yaml").write_text("backends: []\n")
        with (
            patch("code_forge.mcp_server.load_user_backends", return_value={}),
            patch("code_forge.cli._load_gate_backends", return_value=([], project_gate)),
        ):
            names = _backend_names_for(tmp_path)
        assert names == []

    def test_backend_names_for_load_error_falls_back_to_user(self, tmp_path):
        """G1d: project gate.yaml load throws -> user backends still returned."""
        from code_forge.mcp_server import _backend_names_for

        with (
            patch(
                "code_forge.mcp_server.load_user_backends",
                return_value={"user-back": {"model": "u"}},
            ),
            patch(
                "code_forge.cli._load_gate_backends",
                side_effect=OSError("permission denied"),
            ),
        ):
            names = _backend_names_for(tmp_path)
        assert names == ["user-back"]


class TestShutdownInfrastructure:
    """Tests for signal-driven shutdown (Finding 2 coverage)."""

    def test_schedule_shutdown_sets_flag_and_creates_task(self):
        import code_forge.mcp_server as mod

        orig = mod._shutting_down
        try:
            mod._shutting_down = False
            loop = MagicMock()
            loop.create_task.return_value = MagicMock()
            with patch.object(mod, "_do_shutdown", new=MagicMock(return_value=MagicMock())):
                mod._schedule_shutdown(15, loop)
            assert mod._shutting_down is True
            loop.create_task.assert_called_once()
        finally:
            mod._shutting_down = orig

    def test_schedule_shutdown_reentrant_noop(self):
        import code_forge.mcp_server as mod

        orig = mod._shutting_down
        try:
            mod._shutting_down = True
            loop = MagicMock()
            mod._schedule_shutdown(15, loop)
            loop.create_task.assert_not_called()
        finally:
            mod._shutting_down = orig

    @pytest.mark.asyncio
    async def test_lifespan_installs_signal_handlers(self):
        import code_forge.mcp_server as mod

        with (
            patch("code_forge.mcp_server.load_user_backends", return_value={}),
            patch(
                "code_forge.cli._load_gate_backends",
                return_value=([], {"backends": {}}),
            ),
            patch("asyncio.get_running_loop") as mock_get_loop,
        ):
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            async with mod.lifespan(mod.mcp):
                pass
            # Verify both SIGTERM and SIGINT handlers were installed
            calls = mock_loop.add_signal_handler.call_args_list
            sigs = [c[0][0] for c in calls]
            import signal
            assert signal.SIGTERM in sigs
            assert signal.SIGINT in sigs

    @pytest.mark.asyncio
    async def test_lifespan_survives_missing_signal_support(self):
        """Windows loops raise NotImplementedError; startup must not die.

        Reproduces the gpu-win deployment failure: ProactorEventLoop
        has no add_signal_handler, and the unguarded call killed the
        server (and everything downstream of it) at lifespan setup.
        The lifespan must reach its yield anyway and still run
        cleanup_all on exit.
        """
        import code_forge.mcp_server as mod

        with (
            patch("code_forge.mcp_server.load_user_backends", return_value={}),
            patch(
                "code_forge.cli._load_gate_backends",
                return_value=([], {"backends": {}}),
            ),
            patch("asyncio.get_running_loop") as mock_get_loop,
            patch("code_forge.mcp_server.cleanup_all") as mock_cleanup,
        ):
            mock_loop = MagicMock()
            mock_loop.add_signal_handler.side_effect = NotImplementedError
            mock_get_loop.return_value = mock_loop
            reached_body = False
            async with mod.lifespan(mod.mcp):
                reached_body = True
            assert reached_body
            mock_cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_propagates_unexpected_signal_errors(self):
        """Only NotImplementedError means "unsupported" -- anything else
        (wrong thread, closed loop) is a real bug and must surface, not
        be swallowed by the platform fallback.
        """
        import code_forge.mcp_server as mod

        with (
            patch("code_forge.mcp_server.load_user_backends", return_value={}),
            patch(
                "code_forge.cli._load_gate_backends",
                return_value=([], {"backends": {}}),
            ),
            patch("asyncio.get_running_loop") as mock_get_loop,
            patch("code_forge.mcp_server.cleanup_all") as mock_cleanup,
        ):
            mock_loop = MagicMock()
            mock_loop.add_signal_handler.side_effect = RuntimeError("boom")
            mock_get_loop.return_value = mock_loop
            with pytest.raises(RuntimeError, match="boom"):
                async with mod.lifespan(mod.mcp):
                    pass
            mock_cleanup.assert_not_awaited()

    def test_install_pdeathsig_calls_prctl_on_linux(self):
        import code_forge.mcp_server as mod

        mock_libc = MagicMock()
        mock_libc.prctl.return_value = 0
        with (
            patch("code_forge.mcp_server.sys") as mock_sys,
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("code_forge.mcp_server.os.getppid", return_value=12345),
        ):
            mock_sys.platform = "linux"
            mod._install_pdeathsig()
        mock_libc.prctl.assert_called_once()
        args = mock_libc.prctl.call_args[0]
        assert args[0] == 1  # PR_SET_PDEATHSIG
        import signal
        assert args[1] == signal.SIGTERM

    def test_install_pdeathsig_skips_non_linux(self):
        import code_forge.mcp_server as mod

        with patch("code_forge.mcp_server.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ctypes.CDLL") as mock_cdll:
                mod._install_pdeathsig()
            mock_cdll.assert_not_called()

    def test_install_pdeathsig_exits_on_parent_change(self):
        """Parent ppid changed between before/after prctl -> exit."""
        import code_forge.mcp_server as mod

        mock_libc = MagicMock()
        mock_libc.prctl.return_value = 0
        # First call returns 500 (original), second returns 1 (reparented)
        with (
            patch("code_forge.mcp_server.sys") as mock_sys,
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("code_forge.mcp_server.os.getppid", side_effect=[500, 1, 1]),
            patch("code_forge.mcp_server.os._exit") as mock_exit,
        ):
            mock_sys.platform = "linux"
            mod._install_pdeathsig()
        mock_exit.assert_called_once_with(1)

    def test_install_pdeathsig_warns_on_prctl_failure(self, caplog):
        import code_forge.mcp_server as mod
        import logging

        mock_libc = MagicMock()
        mock_libc.prctl.return_value = -1
        with (
            patch("code_forge.mcp_server.sys") as mock_sys,
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("ctypes.get_errno", return_value=22),
            patch("code_forge.mcp_server.os.getppid", return_value=12345),
            caplog.at_level(logging.WARNING, logger="code_forge.mcp_server"),
        ):
            mock_sys.platform = "linux"
            mod._install_pdeathsig()
        assert "prctl(PR_SET_PDEATHSIG) failed" in caplog.text
        assert "errno=22" in caplog.text

    def test_install_pdeathsig_cdll_oserror_warns(self, caplog):
        import code_forge.mcp_server as mod
        import logging

        with (
            patch("code_forge.mcp_server.sys") as mock_sys,
            patch("ctypes.CDLL", side_effect=OSError("libc not found")),
            patch("code_forge.mcp_server.os.getppid", return_value=12345),
            caplog.at_level(logging.WARNING, logger="code_forge.mcp_server"),
        ):
            mock_sys.platform = "linux"
            mod._install_pdeathsig()
        assert "PR_SET_PDEATHSIG unavailable" in caplog.text

    @pytest.mark.asyncio
    async def test_lifespan_calls_pdeathsig(self):
        import code_forge.mcp_server as mod

        with (
            patch("code_forge.mcp_server.load_user_backends", return_value={}),
            patch(
                "code_forge.cli._load_gate_backends",
                return_value=([], {"backends": {}}),
            ),
            patch("asyncio.get_running_loop") as mock_get_loop,
            patch.object(mod, "_install_pdeathsig") as mock_pdeathsig,
        ):
            mock_get_loop.return_value = MagicMock()
            async with mod.lifespan(mod.mcp):
                pass
            mock_pdeathsig.assert_called_once()


class TestForgeInitHomeGuard:
    """forge_init refuses $HOME as workspace root."""

    @pytest.mark.asyncio
    async def test_forge_init_refuses_home(self):
        """T7: forge_init at $HOME raises ToolError."""
        home = Path.home().resolve()
        with patch("code_forge.mcp_server._resolve_workspace", return_value=home):
            with pytest.raises(ToolError, match="Refusing to initialize forge at"):
                await forge_init()


class TestWorkspaceFor:
    """T1 bug-inject tests for _workspace_for resolver."""

    def _make_ctx(self, roots_capable=True, roots_result=None,
                  list_roots_exc=None):
        """Build a fake MCP context with controllable roots behavior."""
        ctx = MagicMock()
        caps = MagicMock()
        caps.roots = roots_capable
        ctx.session.client_params.capabilities = caps
        if list_roots_exc:
            ctx.session.list_roots = AsyncMock(side_effect=list_roots_exc)
        else:
            ctx.session.list_roots = AsyncMock(return_value=roots_result)
        return ctx

    @pytest.mark.asyncio
    async def test_roots_with_gate_yaml_wins(self, tmp_path):
        """T1a: root with gate.yaml becomes workspace, not cwd.

        Bug-inject: if the roots branch is skipped (roots_capable=False),
        _workspace_for falls back to _resolve_workspace which returns
        cwd -- the assertion on tmp_path fails, proving roots are
        honored.
        """
        import code_forge.mcp_server as mod
        from mcp.types import Root

        # Create a project dir with gate.yaml under tmp_path
        project = tmp_path / "my project"  # space in name
        gate_dir = project / ".code-forge"
        gate_dir.mkdir(parents=True)
        (gate_dir / "gate.yaml").write_text("outlet: subprocess\n")

        root = Root(uri="file://" + str(project).replace(" ", "%20"),
                    name="test")
        result = MagicMock()
        result.roots = [root]

        ctx = self._make_ctx(roots_capable=True, roots_result=result)

        # Clear cache from prior tests
        mod._cached_session_ref = None
        mod._cached_workspace = None

        ws = await mod._workspace_for(ctx)
        assert ws == project

    @pytest.mark.asyncio
    async def test_roots_skipped_falls_back(self, tmp_path):
        """T1a bug-inject: roots_capable=False -> fallback, not root.

        This is the injected-bug counterpart: when roots are not
        checked, workspace != the root directory.
        """
        import code_forge.mcp_server as mod

        project = tmp_path / "my project"
        gate_dir = project / ".code-forge"
        gate_dir.mkdir(parents=True)
        (gate_dir / "gate.yaml").write_text("outlet: subprocess\n")

        ctx = self._make_ctx(roots_capable=False)

        mod._cached_session_ref = None
        mod._cached_workspace = None

        with patch.object(mod, "_resolve_workspace",
                          return_value=Path("/fallback")):
            ws = await mod._workspace_for(ctx)
        # Proves: without roots, workspace is NOT the project dir
        assert ws != project
        assert ws == Path("/fallback")

    @pytest.mark.asyncio
    async def test_no_roots_capability_uses_fallback(self):
        """T1b: client without roots -> FORGE_PROJECT_DIR/walk-up."""
        import code_forge.mcp_server as mod

        ctx = self._make_ctx(roots_capable=False)

        mod._cached_session_ref = None
        mod._cached_workspace = None

        fallback = Path("/some/project")
        with patch.object(mod, "_resolve_workspace",
                          return_value=fallback):
            ws = await mod._workspace_for(ctx)
        assert ws == fallback

    @pytest.mark.asyncio
    async def test_ctx_none_uses_resolve_workspace(self):
        """C-2: ctx=None -> _resolve_workspace() directly."""
        import code_forge.mcp_server as mod

        fallback = Path("/direct")
        with patch.object(mod, "_resolve_workspace",
                          return_value=fallback):
            ws = await mod._workspace_for(None)
        assert ws == fallback

    @pytest.mark.asyncio
    async def test_cache_hit_skips_rpc(self):
        """Cached session returns workspace without list_roots call."""
        import code_forge.mcp_server as mod

        ctx = self._make_ctx(roots_capable=True)
        cached_ws = Path("/cached/project")

        mod._cached_session_ref = ctx.session
        mod._cached_workspace = cached_ws

        ws = await mod._workspace_for(ctx)
        assert ws == cached_ws
        # list_roots was never called
        ctx.session.list_roots.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rpc_failure_no_cache(self):
        """C-3: list_roots RPC failure -> no cache, returns fallback."""
        import code_forge.mcp_server as mod

        ctx = self._make_ctx(roots_capable=True,
                             list_roots_exc=RuntimeError("gone"))

        mod._cached_session_ref = None
        mod._cached_workspace = None

        fallback = Path("/rpc-fail-fallback")
        with patch.object(mod, "_resolve_workspace",
                          return_value=fallback):
            ws = await mod._workspace_for(ctx)
        assert ws == fallback
        # Cache must NOT be set after RPC failure
        assert mod._cached_session_ref is None

    @pytest.mark.asyncio
    async def test_first_root_without_gate_yaml(self, tmp_path):
        """Root without gate.yaml still used as candidate[0]."""
        import code_forge.mcp_server as mod
        from mcp.types import Root

        project = tmp_path / "no-gate"
        project.mkdir()
        # No gate.yaml here

        root = Root(uri="file://" + str(project), name="test")
        result = MagicMock()
        result.roots = [root]

        ctx = self._make_ctx(roots_capable=True, roots_result=result)

        mod._cached_session_ref = None
        mod._cached_workspace = None

        ws = await mod._workspace_for(ctx)
        assert ws == project

    @pytest.mark.asyncio
    async def test_empty_project_dir_falls_through(self):
        """project_dir="" must NOT resolve to cwd; must fall through.

        Bug-inject: reverting the guard to `if project_dir is not None:`
        makes project_dir="" hit Path("").resolve() == cwd, so the
        assertion ws == Path("/fallback") fails.
        """
        import code_forge.mcp_server as mod

        ctx = self._make_ctx(roots_capable=False)

        saved_ref = mod._cached_session_ref
        saved_ws = mod._cached_workspace
        mod._cached_session_ref = None
        mod._cached_workspace = None
        try:
            with patch.object(mod, "_resolve_workspace",
                              return_value=Path("/fallback")):
                ws = await mod._workspace_for(ctx, project_dir="")
            assert ws == Path("/fallback")
            assert ws != Path.cwd().resolve()
        finally:
            mod._cached_session_ref = saved_ref
            mod._cached_workspace = saved_ws

    @pytest.mark.asyncio
    async def test_explicit_project_dir_honored(self):
        """project_dir="/explicit/path" resolves to that path."""
        import code_forge.mcp_server as mod

        ctx = self._make_ctx(roots_capable=False)

        saved_ref = mod._cached_session_ref
        saved_ws = mod._cached_workspace
        mod._cached_session_ref = None
        mod._cached_workspace = None
        try:
            ws = await mod._workspace_for(ctx,
                                          project_dir="/explicit/path")
            assert ws == Path("/explicit/path").expanduser().resolve()
        finally:
            mod._cached_session_ref = saved_ref
            mod._cached_workspace = saved_ws


class TestProjectDirOverride:
    """project_dir param overrides all other resolution and leaves cache intact."""

    def _make_ctx(self):
        ctx = MagicMock()
        caps = MagicMock()
        caps.roots = False
        ctx.session.client_params.capabilities = caps
        return ctx

    @pytest.mark.asyncio
    async def test_project_dir_overrides_cwd_home(self, tmp_path):
        import code_forge.mcp_server as mod

        project = tmp_path / "real-repo"
        project.mkdir()

        mod._cached_session_ref = None
        mod._cached_workspace = None

        ctx = self._make_ctx()
        ws = await mod._workspace_for(ctx, project_dir=str(project))
        assert ws == project.resolve()

    @pytest.mark.asyncio
    async def test_project_dir_does_not_corrupt_cache(self, tmp_path):
        import code_forge.mcp_server as mod

        project = tmp_path / "override"
        project.mkdir()

        mod._cached_session_ref = None
        mod._cached_workspace = None

        ctx = self._make_ctx()
        await mod._workspace_for(ctx, project_dir=str(project))
        assert mod._cached_session_ref is None, (
            "project_dir must not set the session cache"
        )
        assert mod._cached_workspace is None, (
            "project_dir must not set the workspace cache"
        )


class TestInprocessResultFindings:
    """T3: _make_inprocess_result returns findings in structuredContent."""

    def test_findings_in_result(self):
        """Non-empty findings list appears in structured output."""
        import code_forge.mcp_server as mod
        from code_forge.state import Verdict

        findings = [{"file": "a.py", "line_range": [1, 5],
                      "source": "L1", "disposition": "CONFIRMED",
                      "description": "bug here"}]
        result = mod._make_inprocess_result(
            Verdict.FAIL, findings_count=1, elapsed=1.0,
            findings=findings,
        )
        data = result.structuredContent
        assert data["findings_count"] == 1
        assert data["findings"] == findings

    def test_no_findings_omitted(self):
        """findings_count=0 + no findings -> None."""
        import code_forge.mcp_server as mod
        from code_forge.state import Verdict

        result = mod._make_inprocess_result(
            Verdict.PASS, findings_count=0, elapsed=0.5,
        )
        data = result.structuredContent
        assert data["findings_count"] == 0
        assert data["findings"] is None


class TestTruncate:
    """_truncate helper."""

    def test_short_unchanged(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("hello", 200) == "hello"

    def test_long_truncated_with_ellipsis(self):
        import code_forge.mcp_server as mod
        text = "x" * 250
        result = mod._truncate(text, 200)
        assert len(result) == 200
        assert result.endswith("...")

    def test_exact_limit_unchanged(self):
        import code_forge.mcp_server as mod
        text = "y" * 200
        assert mod._truncate(text, 200) == text

    def test_small_limit_no_ellipsis(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("abcdef", 2) == "ab"
        assert mod._truncate("abcdef", 3) == "abc"

    def test_limit_four_gets_ellipsis(self):
        import code_forge.mcp_server as mod
        result = mod._truncate("abcdef", 4)
        assert result == "a..."
        assert len(result) == 4

    def test_limit_zero_returns_empty(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("abcdef", 0) == ""

    def test_negative_limit_returns_empty(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("abcdef", -1) == ""
        assert mod._truncate("abcdef", -999) == ""

    def test_limit_one_returns_single_char(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("abcdef", 1) == "a"

    def test_text_shorter_than_limit(self):
        import code_forge.mcp_server as mod
        assert mod._truncate("ab", 3) == "ab"


class TestActiveFindingsProperty:
    """StateMachine.active_findings filters dismissed."""

    def test_dismissed_excluded(self):
        from code_forge.machine import StateMachine
        from code_forge.disposition import Disposition
        from code_forge.state import StateFinding
        from unittest.mock import MagicMock

        sm = MagicMock(spec=StateMachine)
        sm._state = MagicMock()
        sm._state.findings = [
            StateFinding(
                id="f1", fingerprint="fp1", source="L1",
                disposition=Disposition.CONFIRMED,
                file="a.py", line_range=[1], description="bug",
            ),
            StateFinding(
                id="f2", fingerprint="fp2", source="L1",
                disposition=Disposition.DISMISSED,
                file="b.py", line_range=[2], description="false pos",
            ),
        ]
        # Call the real property on the mock
        result = StateMachine.active_findings.fget(sm)
        assert len(result) == 1
        assert result[0].id == "f1"


# -- T1: capability diagnostics in forge_resolve_outlet --


def _mock_ctx(sampling=None, roots=None):
    """Build a mock MCP context with specified capabilities."""
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = sampling
    ctx.session.client_params.capabilities.roots = roots
    return ctx


@pytest.mark.asyncio
async def test_resolve_outlet_capability_lines_with_sampling():
    """When ctx has sampling, output shows 'client sampling: yes'."""
    ctx = _mock_ctx(sampling=MagicMock(), roots=MagicMock())
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("subprocess", "", 0),
    ), patch(
        "code_forge.mcp_server._workspace_for",
        new_callable=AsyncMock,
        return_value=Path("/tmp/fake-ws"),
    ):
        result = await forge_resolve_outlet(ctx=ctx)
        text = result.structuredContent["output"]
        assert "client sampling: yes" in text
        assert "client roots:    yes" in text
        assert "MISCONFIG" not in text


@pytest.mark.asyncio
async def test_resolve_outlet_capability_lines_without_sampling():
    """When ctx lacks sampling, output shows 'client sampling: NO'."""
    ctx = _mock_ctx(sampling=None, roots=None)
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("subprocess", "", 0),
    ), patch(
        "code_forge.mcp_server._workspace_for",
        new_callable=AsyncMock,
        return_value=Path("/tmp/fake-ws"),
    ):
        result = await forge_resolve_outlet(ctx=ctx)
        text = result.structuredContent["output"]
        assert "client sampling: NO" in text
        assert "client roots:    NO" in text


@pytest.mark.asyncio
async def test_resolve_outlet_no_ctx_shows_unknown():
    """CLI path (ctx=None) shows 'client capabilities: unknown'."""
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("subprocess", "", 0),
    ):
        result = await forge_resolve_outlet(ctx=None)
        text = result.structuredContent["output"]
        assert "client capabilities: unknown" in text


@pytest.mark.asyncio
async def test_resolve_outlet_misconfig_gate_yaml_sampling():
    """MISCONFIG when gate.yaml says sampling but client lacks it."""
    ctx = _mock_ctx(sampling=None, roots=None)
    ws = Path(tempfile.mkdtemp())
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    gate_yaml = gate_dir / "gate.yaml"
    gate_yaml.write_text("outlet: sampling\n")
    try:
        with patch(
            "code_forge.mcp_server._run_cli_simple",
            new_callable=AsyncMock,
            return_value=("sampling", "", 0),
        ), patch(
            "code_forge.mcp_server._workspace_for",
            new_callable=AsyncMock,
            return_value=ws,
        ), patch(
            "code_forge.outlet_resolver.load_outlet_from_gate",
            return_value="sampling",
        ):
            result = await forge_resolve_outlet(ctx=ctx)
            text = result.structuredContent["output"]
            assert "MISCONFIG" in text
            assert "sampling" in text.lower()
            assert "Switch outlet" in text
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_resolve_outlet_misconfig_env_wins_over_gate(monkeypatch):
    """Rider: FORGE_OUTLET env takes priority over gate.yaml for MISCONFIG.

    env=sampling + gate=subprocess -> MISCONFIG fires (mirrors guard).
    """
    ctx = _mock_ctx(sampling=None, roots=None)
    monkeypatch.setenv("FORGE_OUTLET", "sampling")
    ws = Path(tempfile.mkdtemp())
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    (gate_dir / "gate.yaml").write_text("outlet: subprocess\n")
    try:
        with patch(
            "code_forge.mcp_server._run_cli_simple",
            new_callable=AsyncMock,
            return_value=("sampling", "", 0),
        ), patch(
            "code_forge.mcp_server._workspace_for",
            new_callable=AsyncMock,
            return_value=ws,
        ):
            result = await forge_resolve_outlet(ctx=ctx)
            text = result.structuredContent["output"]
            assert "MISCONFIG" in text, (
                "env=sampling should trigger MISCONFIG even when "
                "gate.yaml says subprocess"
            )
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_resolve_outlet_no_misconfig_when_capable():
    """Bug-inject T1a(ii): client HAS sampling -> no MISCONFIG."""
    ctx = _mock_ctx(sampling=MagicMock(), roots=None)
    ws = Path(tempfile.mkdtemp())
    gate_dir = ws / ".code-forge"
    gate_dir.mkdir(parents=True)
    (gate_dir / "gate.yaml").write_text("outlet: sampling\n")
    try:
        with patch(
            "code_forge.mcp_server._run_cli_simple",
            new_callable=AsyncMock,
            return_value=("sampling", "", 0),
        ), patch(
            "code_forge.mcp_server._workspace_for",
            new_callable=AsyncMock,
            return_value=ws,
        ), patch(
            "code_forge.outlet_resolver.load_outlet_from_gate",
            return_value="sampling",
        ):
            result = await forge_resolve_outlet(ctx=ctx)
            text = result.structuredContent["output"]
            assert "MISCONFIG" not in text
            assert "client sampling: yes" in text
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)


# -- T1c: guard ToolError includes remediation --


@pytest.mark.asyncio
async def test_review_guard_includes_remediation(monkeypatch):
    """ToolError from forge_review sampling guard includes remediation."""
    monkeypatch.setenv("FORGE_OUTLET", "sampling")
    ctx = _mock_ctx(sampling=None)
    with patch(
        "code_forge.mcp_server._workspace_for",
        new_callable=AsyncMock,
        return_value=Path("/tmp/fake-ws"),
    ), pytest.raises(ToolError, match="Switch outlet"):
        await forge_review(ctx=ctx)


@pytest.mark.asyncio
async def test_gate_check_guard_includes_remediation(monkeypatch):
    """ToolError from forge_gate_check sampling guard includes remediation."""
    monkeypatch.setenv("FORGE_OUTLET", "sampling")
    ctx = _mock_ctx(sampling=None)
    with patch(
        "code_forge.mcp_server._workspace_for",
        new_callable=AsyncMock,
        return_value=Path("/tmp/fake-ws"),
    ), pytest.raises(ToolError, match="Switch outlet"):
        await forge_gate_check(ctx=ctx)


@pytest.mark.asyncio
async def test_null_coercion_coerces_none_to_empty_string():
    """MCP clients sending null for optional string params must not crash."""
    import code_forge.mcp_server as mod

    received_args: dict = {}

    async def _spy(name, arguments, **kw):
        received_args.update(arguments)
        return "ok"

    # Temporarily replace the underlying call_tool so we can capture
    # the coerced arguments without triggering real tool logic.
    saved = mod._original_tc
    mod._original_tc = _spy
    try:
        await mod._null_coerce_call_tool(
            "test_tool", {"project_dir": None, "other": "val"}
        )
    finally:
        mod._original_tc = saved

    # After coercion, None must become "" while non-None values pass through.
    assert received_args.get("project_dir") == "", (
        "None was not coerced to empty string"
    )
    assert received_args.get("other") == "val", (
        "Non-None value was corrupted"
    )


# _job_cap_s direct tests


def _make_backend(backend_type="cli", timeout_s=0, name="test"):
    from code_forge.backend import BackendConfig
    return BackendConfig(name=name, type=backend_type, model="", timeout_s=timeout_s)


def test_job_cap_s_backend_explicit_timeout():
    """Backend with timeout_s=1800 -> 1800 + 600 = 2400.0."""
    be = _make_backend(timeout_s=1800)
    with (
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.backend.resolve_backend", return_value=be),
    ):
        assert _job_cap_s(Path("/tmp")) == 2400.0


def test_job_cap_s_api_backend_default_timeout():
    """API backend with no explicit timeout -> API cap 600 + 600 = 1200.0."""
    be = _make_backend(backend_type="api", timeout_s=0)
    with (
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.backend.resolve_backend", return_value=be),
    ):
        assert _job_cap_s(Path("/tmp")) == 1200.0


def test_job_cap_s_cli_backend_default_timeout():
    """CLI backend with no explicit timeout -> CLI cap 300 + 600 = 900.0."""
    be = _make_backend(backend_type="cli", timeout_s=0)
    with (
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.backend.resolve_backend", return_value=be),
    ):
        assert _job_cap_s(Path("/tmp")) == 900.0


def test_job_cap_s_env_override_wins():
    """FORGE_MCP_JOB_TIMEOUT_S=50 -> 50.0, ignoring derived value."""
    with patch.dict(os.environ, {"FORGE_MCP_JOB_TIMEOUT_S": "50"}):
        assert _job_cap_s(Path("/tmp")) == 50.0


def test_job_cap_s_env_junk_falls_back(caplog):
    """FORGE_MCP_JOB_TIMEOUT_S=junk -> derived CLI 900.0 + warning."""
    be = _make_backend(backend_type="cli", timeout_s=0)
    with (
        patch.dict(os.environ, {"FORGE_MCP_JOB_TIMEOUT_S": "junk"}),
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.backend.resolve_backend", return_value=be),
    ):
        import logging
        with caplog.at_level(logging.WARNING, logger="code_forge.mcp_server"):
            result = _job_cap_s(Path("/tmp"))
    assert result == 900.0
    assert any("not an int" in r.message for r in caplog.records)


def test_job_cap_s_env_negative_falls_back(caplog):
    """FORGE_MCP_JOB_TIMEOUT_S=-1 -> derived CLI 900.0 + warning."""
    be = _make_backend(backend_type="cli", timeout_s=0)
    with (
        patch.dict(os.environ, {"FORGE_MCP_JOB_TIMEOUT_S": "-1"}),
        patch("code_forge.cli._load_gate_backends", return_value=({}, {})),
        patch("code_forge.backend.resolve_backend", return_value=be),
    ):
        import logging
        with caplog.at_level(logging.WARNING, logger="code_forge.mcp_server"):
            result = _job_cap_s(Path("/tmp"))
    assert result == 900.0
    assert any("not positive" in r.message for r in caplog.records)


def test_job_cap_s_resolution_failure_falls_back(caplog):
    """Broken gate.yaml -> DEFAULT_BACKEND (CLI, no timeout) -> 900.0 + warning."""
    import logging
    with (
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=RuntimeError("broken yaml"),
        ),
        caplog.at_level(logging.WARNING, logger="code_forge.mcp_server"),
    ):
        result = _job_cap_s(Path("/tmp"))
    assert result == 900.0
    assert any("falling back" in r.message for r in caplog.records)


# -- Phase 41: contract_spec wiring in sampling path --


def test_sampling_builder_receives_contract():
    """Direct builder test: contract_spec is accepted and flows into the
    prompt construction. Full prompt verification is in the e2e test."""
    from code_forge.factories import build_sampling_l1_provider

    loop = asyncio.new_event_loop()
    try:
        provider = build_sampling_l1_provider(
            session=MagicMock(),
            loop=loop,
            resolved=MagicMock(git_diff="diff", mode_hint="git"),
            contract_spec="Test contract body",
        )
        assert callable(provider)
    finally:
        loop.close()


def test_sampling_builder_contract_header_behavioral():
    """Behavioral: empty contract_spec must NOT emit '## Contract Reference'
    in the prompt; non-empty contract_spec MUST emit it with the text.

    Exercises the runtime prompt-building path by capturing what
    invoke_sampling receives, rather than inspecting source text."""
    import asyncio
    from code_forge.factories import build_sampling_l1_provider

    resolved = MagicMock(git_diff="diff --git a/f b/f\n+line", mode_hint="git")
    loop = asyncio.new_event_loop()
    captured_prompts = []

    async def _fake_invoke(session, prompt, **kwargs):
        captured_prompts.append(prompt)
        result = MagicMock()
        result.duration_s = 0.1
        result.content = '{"findings": [], "code_excerpts": []}'
        return result

    class _FakeFuture:
        def __init__(self, result):
            self._result = result
        def result(self, timeout=None):
            return self._result
        def cancel(self):
            pass

    def _fake_rcts(coro, loop):
        return _FakeFuture(loop.run_until_complete(coro))

    try:
        # EMPTY contract_spec: header must NOT appear
        captured_prompts.clear()
        with (
            patch("code_forge.llm_invoke.invoke_sampling", side_effect=_fake_invoke),
            patch("asyncio.run_coroutine_threadsafe", side_effect=_fake_rcts),
        ):
            provider = build_sampling_l1_provider(
                session=MagicMock(), loop=loop, resolved=resolved,
                contract_spec="",
            )
            provider()
        for p in captured_prompts:
            assert "## Contract Reference" not in p, (
                "empty contract_spec must not emit Contract Reference header"
            )

        # NON-EMPTY contract_spec: header MUST appear with the text
        captured_prompts.clear()
        with (
            patch("code_forge.llm_invoke.invoke_sampling", side_effect=_fake_invoke),
            patch("asyncio.run_coroutine_threadsafe", side_effect=_fake_rcts),
        ):
            provider = build_sampling_l1_provider(
                session=MagicMock(), loop=loop, resolved=resolved,
                contract_spec="My contract rules",
            )
            provider()
        found = any("## Contract Reference" in p and "My contract rules" in p
                     for p in captured_prompts)
        assert found, "non-empty contract_spec must emit Contract Reference header"
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_sampling_e2e_contract_in_prompt():
    """End-to-end: forge_review(contract=...) passes contract_spec into
    build_sampling_l1_provider via _dispatch_sampling. Bug-inject proof:
    remove contract_spec=contract at the forge_review call site -> this
    test FAILS; restore -> PASSES."""
    from code_forge.mcp_server import forge_review

    builder_kwargs = {}

    def capture_build(session, loop, resolved, **kwargs):
        builder_kwargs.update(kwargs)
        def _provider():
            return ([], [], MagicMock(), 0.0)
        return _provider

    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = MagicMock()

    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for",
              new_callable=AsyncMock, return_value=_resolve_workspace()),
        patch("code_forge.mcp_server._build_review_context",
              return_value=(MagicMock(git_diff="d", mode_hint="git"), "h", "r")),
        patch("code_forge.factories.build_sampling_l1_provider",
              side_effect=capture_build),
        patch("code_forge.factories.build_revert_fn", return_value=lambda f: None),
        patch("code_forge.machine.StateMachine") as mock_sm,
        patch("code_forge.mcp_server.asyncio.to_thread",
              new_callable=AsyncMock, return_value=MagicMock(value="PASS")),
    ):
        mock_sm.return_value.active_findings = []
        result = await forge_review(
            contract="test contract",
            ctx=ctx,
        )

    assert "test contract" in builder_kwargs.get("contract_spec", "")
    assert isinstance(result, CallToolResult)


@pytest.mark.asyncio
async def test_gate_check_no_contract():
    """forge_gate_check with sampling outlet passes no contract_spec
    and does not load contracts.yaml (staged=True skips digest)."""
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = MagicMock()

    builder_kwargs = {}

    def capture_build(session, loop, resolved, **kwargs):
        builder_kwargs.update(kwargs)
        def _provider():
            return ([], [], MagicMock(), 0.0)
        return _provider

    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._build_review_context",
              return_value=(MagicMock(git_diff="d", mode_hint="git"), "h", "r")),
        patch("code_forge.factories.build_sampling_l1_provider",
              side_effect=capture_build),
        patch("code_forge.factories.build_revert_fn", return_value=lambda f: None),
        patch("code_forge.machine.StateMachine") as mock_sm,
        patch("code_forge.mcp_server.asyncio.to_thread",
              new_callable=AsyncMock, return_value=MagicMock(value="PASS")),
        patch("code_forge.cli._safe_load_contract_digest") as mock_load,
    ):
        mock_sm.return_value.active_findings = []
        await forge_gate_check(ctx=ctx)

    mock_load.assert_not_called()
    # contract_spec stays at default "" -- omitted from call
    assert builder_kwargs.get("contract_spec", "") == ""


@pytest.mark.asyncio
async def test_sampling_fallback_preserves_contract():
    """Fallback on LLMInvokeError writes raw contract to tmpfile."""
    from code_forge.mcp_server import _dispatch_sampling

    p1, p2, p3, p4 = _sampling_dispatch_patches("truncated", ["deepseek"])
    captured_content = []

    def capture_cli(*args, **kwargs):
        # Read tmpfile content before the function cleans it up on success
        if "--contract" in args:
            idx = args.index("--contract") + 1
            with open(args[idx]) as f:
                captured_content.append(f.read())
        return ("fallback ran", 0, 1.0, "")

    with p1, p2, p3, p4, patch(
        "code_forge.mcp_server._run_cli_budgeted",
        new_callable=AsyncMock,
        side_effect=capture_cli,
    ):
        result = await _dispatch_sampling(
            session=MagicMock(), committed=False,
            workspace=_resolve_workspace(),
            contract_spec="raw contract text",
        )

    assert len(captured_content) == 1
    assert captured_content[0] == "raw contract text"
    assert isinstance(result, CallToolResult)


@pytest.mark.asyncio
async def test_sampling_digest_loaded_from_workspace(tmp_path):
    """contracts.yaml digest appears in the sampling prompt via the
    _safe_load_contract_digest -> _merge_contract_spec path."""
    from code_forge.mcp_server import _dispatch_sampling

    contracts_dir = tmp_path / ".code-forge"
    contracts_dir.mkdir()
    (contracts_dir / "contracts.yaml").write_text("repos: {}")

    builder_kwargs = {}

    def capture_build(session, loop, resolved, **kwargs):
        builder_kwargs.update(kwargs)
        def _provider():
            return ([], [], MagicMock(), 0.0)
        return _provider

    with (
        patch("code_forge.mcp_server._build_review_context",
              return_value=(MagicMock(git_diff="d", mode_hint="git"), "h", "r")),
        patch("code_forge.factories.build_sampling_l1_provider",
              side_effect=capture_build),
        patch("code_forge.factories.build_revert_fn", return_value=lambda f: None),
        patch("code_forge.machine.StateMachine") as mock_sm,
        patch("code_forge.mcp_server.asyncio.to_thread",
              new_callable=AsyncMock, return_value=MagicMock(value="PASS")),
        patch("code_forge.cli._safe_load_contract_digest",
              return_value="digest from yaml"),
    ):
        mock_sm.return_value.active_findings = []
        await _dispatch_sampling(
            session=MagicMock(), committed=False,
            workspace=tmp_path,
        )

    assert "digest from yaml" in builder_kwargs.get("contract_spec", "")


def test_merge_contract_spec_warns_on_large_no_backend():
    """_merge_contract_spec with backend=None + >4KB emits warning,
    content not truncated."""
    from code_forge.cli import _merge_contract_spec

    warn_mock = MagicMock()
    big = "x" * 5000
    result = _merge_contract_spec("", big, backend=None, warn_fn=warn_mock)
    warn_mock.assert_called_once()
    assert "no backend available" in warn_mock.call_args[0][0]
    assert result.startswith("x" * 5000)


@pytest.mark.asyncio
async def test_sampling_memory_error_propagates(tmp_path):
    """MemoryError from _safe_load_contract_digest propagates --
    no fallback, no green review."""
    from code_forge.mcp_server import _dispatch_sampling

    contracts_dir = tmp_path / ".code-forge"
    contracts_dir.mkdir()
    (contracts_dir / "contracts.yaml").write_text("dummy")

    with (
        patch("code_forge.mcp_server._build_review_context",
              return_value=(MagicMock(git_diff="d", mode_hint="git"), "h", "r")),
        patch("code_forge.cli._safe_load_contract_digest",
              side_effect=MemoryError("oom")),
    ):
        with pytest.raises(MemoryError):
            await _dispatch_sampling(
                session=MagicMock(), committed=False,
                workspace=tmp_path,
            )


# -- _dispatch_cli: contract tmpfile lifecycle --


@pytest.mark.asyncio
async def test_dispatch_cli_job_success_keeps_contract():
    """Job branch transfers contract ownership -- must NOT unlink."""
    from code_forge.mcp_server import _dispatch_cli

    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    with (
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, "/tmp/stderr.log"),
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="job-123",
        ) as mock_start,
    ):
        result = await _dispatch_cli(
            ["review"], Path("/tmp"), cap=900.0, contract="my spec",
        )
        assert result.structuredContent["job_id"] == "job-123"
        # start_job received a tempfile_path (not None)
        tmp_arg = mock_start.call_args.kwargs.get("tempfile_path")
        assert tmp_arg is not None
        # tmpfile still exists on disk (ownership transferred)
        assert os.path.exists(tmp_arg)
        os.unlink(tmp_arg)


@pytest.mark.asyncio
async def test_dispatch_cli_run_raises_unlinks_contract():
    """If _run_cli_budgeted raises, contract tmpfile is cleaned up."""
    from code_forge.mcp_server import _dispatch_cli

    captured_tmp_path = None

    def _capture_run(*args, **kwargs):
        nonlocal captured_tmp_path
        # Find --contract arg to discover the tmpfile path
        for i, a in enumerate(args):
            if a == "--contract" and i + 1 < len(args):
                captured_tmp_path = args[i + 1]
                break
        raise RuntimeError("cli crashed")

    with patch(
        "code_forge.mcp_server._run_cli_budgeted",
        new_callable=AsyncMock,
        side_effect=_capture_run,
    ):
        with pytest.raises(RuntimeError, match="cli crashed"):
            await _dispatch_cli(
                ["review"], Path("/tmp"), cap=900.0, contract="doomed spec",
            )
        # Contract tmpfile was created then cleaned up
        assert captured_tmp_path is not None
        assert not os.path.exists(captured_tmp_path)


@pytest.mark.asyncio
async def test_dispatch_cli_run_raises_cancelled_error_unlinks_contract():
    """asyncio.CancelledError is a BaseException (Python 3.8+), not
    an Exception -- the except clause around _run_cli_budgeted must
    widen to BaseException or this leaks the contract tmpfile on
    cancellation. Bug-inject proof: narrow the except back to
    `except Exception:` -- this test must FAIL (tmpfile still
    exists) because CancelledError is not an Exception subclass."""
    from code_forge.mcp_server import _dispatch_cli

    captured_tmp_path = None

    def _capture_run(*args, **kwargs):
        nonlocal captured_tmp_path
        for i, a in enumerate(args):
            if a == "--contract" and i + 1 < len(args):
                captured_tmp_path = args[i + 1]
                break
        raise asyncio.CancelledError()

    with patch(
        "code_forge.mcp_server._run_cli_budgeted",
        new_callable=AsyncMock,
        side_effect=_capture_run,
    ):
        with pytest.raises(asyncio.CancelledError):
            await _dispatch_cli(
                ["review"], Path("/tmp"), cap=900.0,
                contract="doomed spec",
            )
        assert captured_tmp_path is not None
        assert not os.path.exists(captured_tmp_path)


@pytest.mark.asyncio
async def test_dispatch_cli_start_job_raises_unlinks_both():
    """If start_job raises, both contract tmpfile and stderr log are unlinked."""
    from code_forge.mcp_server import _dispatch_cli

    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    # Create a real stderr tmpfile to verify cleanup
    stderr_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False,
    )
    stderr_tmp.close()

    captured_tmp_path = None

    def _capture_start(*args, **kwargs):
        nonlocal captured_tmp_path
        captured_tmp_path = kwargs.get("tempfile_path")
        raise RuntimeError("start failed")

    with (
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, stderr_tmp.name),
        ),
        patch(
            "code_forge.mcp_server.start_job",
            side_effect=_capture_start,
        ),
    ):
        with pytest.raises(RuntimeError, match="start failed"):
            await _dispatch_cli(
                ["review"], Path("/tmp"), cap=900.0,
                contract="test contract",
            )
        # Contract tmpfile was created then cleaned up
        assert captured_tmp_path is not None
        assert not os.path.exists(captured_tmp_path)
        # Stderr log also cleaned up
        assert not os.path.exists(stderr_tmp.name)


@pytest.mark.asyncio
async def test_dispatch_cli_no_contract_no_tmpfile():
    """No contract = no tmpfile created; contract_tmp passed as None."""
    from code_forge.mcp_server import _dispatch_cli

    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()

    with (
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, "/tmp/stderr.log"),
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="job-456",
        ) as mock_start,
    ):
        result = await _dispatch_cli(
            ["gate-check"], Path("/tmp"), cap=900.0,
        )
        assert result.structuredContent["job_id"] == "job-456"
        tmp_arg = mock_start.call_args.kwargs.get("tempfile_path")
        assert tmp_arg is None


# -- site-C integration: start_job failure routes through helper --


@pytest.mark.asyncio
async def test_gate_check_start_job_raises_cleans_stderr():
    """Site C (forge_gate_check) must route through _dispatch_cli so
    start_job failures get stderr cleanup."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_proc = MagicMock()
    stderr_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False,
    )
    stderr_tmp.close()

    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=(mock_task, mock_proc, stderr_tmp.name),
        ),
        patch(
            "code_forge.mcp_server._job_cap_s", return_value=900.0,
        ),
        patch(
            "code_forge.mcp_server.start_job",
            side_effect=RuntimeError("job init failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="job init failed"):
            await forge_gate_check()
        assert not os.path.exists(stderr_tmp.name)
