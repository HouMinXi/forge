# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for MCP server tool handlers."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_forge.mcp_server import (
    _WORKSPACE,
    _backend_names,
    _check_backend,
    _make_job_ref,
    _make_result,
    _make_simple_result,
    _resolve_workspace,
    _run_cli_budgeted,
    _run_cli_simple,
    _validate_backend,
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
    ):
        with pytest.raises(ToolError, match="No trusted review backend"):
            _check_backend()


def test_preflight_nonempty_backends_passes():
    mock_cfg = MagicMock()
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([mock_cfg], {"backends": {"mimo-pro": {}}}),
        ),
    ):
        _check_backend()  # no exception


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
            _check_backend()


def test_preflight_catches_value_error():
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=ValueError("bad yaml"),
        ),
    ):
        with pytest.raises(ToolError):
            _check_backend()


def test_preflight_catches_os_error():
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            side_effect=OSError("permission"),
        ),
    ):
        with pytest.raises(ToolError):
            _check_backend()


def test_preflight_gate_yaml_missing_raises():
    with (
        patch.object(Path, "exists", return_value=False),
        patch("code_forge.cli._load_gate_backends") as mock_load,
    ):
        with pytest.raises(ToolError, match="gate.yaml not found"):
            _check_backend()
        mock_load.assert_not_called()


def test_preflight_gate_yaml_exists_proceeds():
    mock_cfg = MagicMock()
    with (
        patch.object(Path, "exists", return_value=True),
        patch(
            "code_forge.cli._load_gate_backends",
            return_value=([mock_cfg], {"backends": {"deepseek": {}}}),
        ),
    ):
        _check_backend()  # no exception


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
        stdout, stderr, code = await _run_cli_simple("resolve-outlet")
        mock_exec.assert_called_once_with(
            "code-forge",
            "resolve-outlet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_WORKSPACE),
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
        result = await _run_cli_budgeted("review", "--no-color", budget=5.0)
        assert isinstance(result, tuple)
        assert len(result) == 3
        stdout, exit_code, elapsed = result
        assert stdout == "findings"
        assert exit_code == 1
        assert isinstance(elapsed, float)


@pytest.mark.asyncio
async def test_run_cli_budgeted_timeout_returns_task_and_proc():
    mock_proc = MagicMock()

    async def _slow_comm():
        await asyncio.sleep(100)
        return (b"late", b"")

    mock_proc.communicate = _slow_comm
    mock_proc.returncode = None

    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        result = await _run_cli_budgeted("review", budget=0.01)
        assert isinstance(result, tuple)
        assert len(result) == 2
        inner_task, proc = result
        assert isinstance(inner_task, asyncio.Task)
        assert proc is mock_proc
        inner_task.cancel()
        try:
            await inner_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_run_cli_budgeted_cancelled_kills_proc():
    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.returncode = None

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
            return await _run_cli_budgeted("review", budget=100.0)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    mock_proc.kill.assert_called_once()


# -- result formatting --


def test_make_result_returns_call_tool_result():
    result = _make_result("findings here", 1, 5.0)
    assert isinstance(result, CallToolResult)
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "findings here"
    assert result.structuredContent["verdict"] == "FAIL"
    assert result.structuredContent["findings_count"] is None


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
            return_value=("output", 0, 1.5),
        ) as mock_cli,
    ):
        result = await forge_review()
        mock_check.assert_called_once()
        args = mock_cli.call_args
        cli_args = args[0] if args[0] else args[1].get("args", [])
        assert "review" in cli_args
        assert "--no-color" in cli_args
        assert isinstance(result, CallToolResult)


@pytest.mark.asyncio
async def test_forge_review_with_backend_param_validates():
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch(
            "code_forge.mcp_server._backend_names",
            ["mimo-pro", "deepseek"],
        ),
    ):
        with pytest.raises(ToolError, match="Unknown backend"):
            await forge_review(backend="invalid-backend")


@pytest.mark.asyncio
async def test_forge_review_with_valid_backend():
    with (
        patch("code_forge.mcp_server._check_backend"),
        patch("code_forge.mcp_server._backend_names", ["mimo-pro"]),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("output", 0, 1.0),
        ) as mock_cli,
    ):
        result = await forge_review(backend="mimo-pro")
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
        return ("output", 0, 1.0)

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
            return_value=(mock_task, mock_proc),
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ) as mock_start,
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
            return_value=(mock_task, mock_proc),
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ) as mock_start,
    ):
        await forge_review(contract="X")
        # start_job should be called with tempfile_path set
        _, kwargs = mock_start.call_args
        assert kwargs.get("tempfile_path") is not None


@pytest.mark.asyncio
async def test_forge_gate_check_calls_preflight():
    with (
        patch("code_forge.mcp_server._check_backend") as mock_check,
        patch("code_forge.mcp_server._validate_backend"),
        patch(
            "code_forge.mcp_server._run_cli_budgeted",
            new_callable=AsyncMock,
            return_value=("ok", 0, 0.5),
        ) as mock_cli,
    ):
        result = await forge_gate_check()
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
            return_value=("ok", 0, 0.5),
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
            return_value=(mock_task, mock_proc),
        ),
        patch(
            "code_forge.mcp_server.start_job", return_value="test-job-id"
        ) as mock_start,
    ):
        result = await forge_gate_check()
        assert isinstance(result, CallToolResult)
        assert result.structuredContent["job_id"] == "test-job-id"
        assert result.structuredContent["status"] == "running"
        mock_start.assert_called_once_with(mock_task, mock_proc)


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
        result = await forge_init()
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
        result = await forge_trust()
        args = mock_cli.call_args[0]
        assert "trust" in args


@pytest.mark.asyncio
async def test_forge_resolve_outlet_readonly():
    with patch(
        "code_forge.mcp_server._run_cli_simple",
        new_callable=AsyncMock,
        return_value=("outlet: inline", "", 0),
    ) as mock_cli:
        result = await forge_resolve_outlet()
        args = mock_cli.call_args[0]
        assert "resolve-outlet" in args


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
        return_value={"status": "running", "result": None},
    ):
        result = await forge_job_status(job_id="abc")
        assert result.structuredContent["status"] == "running"


@pytest.mark.asyncio
async def test_forge_job_status_unknown_raises():
    with patch("code_forge.mcp_server.get_job", return_value=None):
        with pytest.raises(ToolError, match="Unknown job_id"):
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


# -- workspace resolution (ADR-0006) --


def test_resolve_workspace_env_overrides_walkup(tmp_path, monkeypatch):
    """FORGE_PROJECT_DIR takes priority over cwd walkup."""
    # Create a gate.yaml under tmp_path so walkup could find it
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("backends: {}")

    # Create a different target via env var
    env_target = tmp_path / "env-project"
    env_gate = env_target / ".code-forge"
    env_gate.mkdir(parents=True)
    (env_gate / "gate.yaml").write_text("backends: {}")

    monkeypatch.setenv("FORGE_PROJECT_DIR", str(env_target))
    monkeypatch.chdir(tmp_path)
    result = _resolve_workspace()
    assert result == env_target.resolve()


def test_resolve_workspace_walkup_finds_ancestor(tmp_path, monkeypatch):
    """Walk up from a subdirectory to find .code-forge/gate.yaml."""
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("backends: {}")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)

    monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(sub)
    result = _resolve_workspace()
    assert result == tmp_path.resolve()


def test_resolve_workspace_no_marker_returns_cwd(tmp_path, monkeypatch):
    """No .code-forge anywhere: fall through to cwd as-is."""
    monkeypatch.delenv("FORGE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    result = _resolve_workspace()
    assert result == tmp_path.resolve()


def test_resolve_workspace_empty_env_falls_through(tmp_path, monkeypatch):
    """Empty or whitespace FORGE_PROJECT_DIR is treated as unset."""
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("backends: {}")

    monkeypatch.setenv("FORGE_PROJECT_DIR", "   ")
    monkeypatch.chdir(tmp_path)
    result = _resolve_workspace()
    assert result == tmp_path.resolve()


def test_resolve_workspace_env_expanduser(tmp_path, monkeypatch):
    """FORGE_PROJECT_DIR with ~ is expanded."""
    gate_dir = tmp_path / ".code-forge"
    gate_dir.mkdir()
    (gate_dir / "gate.yaml").write_text("backends: {}")

    monkeypatch.setenv("HOME", str(tmp_path.parent))
    rel = "~/" + tmp_path.name
    monkeypatch.setenv("FORGE_PROJECT_DIR", rel)
    result = _resolve_workspace()
    assert result == tmp_path.resolve()


@pytest.mark.asyncio
async def test_subprocess_receives_workspace_cwd(monkeypatch):
    """Subprocess exec calls include cwd=_WORKSPACE."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
    mock_proc.returncode = 0
    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as mock_exec:
        await _run_cli_simple("review")
        call_kwargs = mock_exec.call_args[1]
        assert "cwd" in call_kwargs
        assert call_kwargs["cwd"] == str(_WORKSPACE)
