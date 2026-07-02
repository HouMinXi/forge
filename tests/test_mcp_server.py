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
    ):
        with pytest.raises(ToolError, match="No review backends configured in"):
            _check_backend()


def test_preflight_empty_backends_names_workspace():
    """The zero-backend error must say WHICH gate.yaml/workspace it read,
    so a wrong-workspace resolution (stale ~/.code-forge) is diagnosable."""
    import re

    from code_forge import mcp_server

    with (
        patch.object(Path, "exists", return_value=True),
        patch("code_forge.cli._load_gate_backends", return_value=([], {})),
    ):
        with pytest.raises(ToolError, match="FORGE_PROJECT_DIR"):
            _check_backend()
        with pytest.raises(
            ToolError, match=re.escape(str(mcp_server._WORKSPACE))
        ):
            _check_backend()


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
        patch.dict("os.environ", {"TEST_KEY_123": "sk-fake"}),
    ):
        _check_backend()  # no exception


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
        patch.dict("os.environ", {}, clear=False),
    ):
        # Ensure the key is NOT set
        import os
        os.environ.pop("MIMO_PRO_API_KEY", None)
        with pytest.raises(ToolError, match="MIMO_PRO_API_KEY"):
            _check_backend()


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
        patch.dict(os.environ, {}, clear=False),
    ):
        with pytest.raises(ToolError, match="gate.yaml not found"):
            _check_backend()
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
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-fake"}),
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
        assert len(result) == 4
        stdout, exit_code, elapsed, stderr = result
        assert stdout == "findings"
        assert exit_code == 1
        assert isinstance(elapsed, float)
        assert stderr == ""


@pytest.mark.asyncio
async def test_run_cli_budgeted_captures_stderr():
    """N1: stderr must be captured, not discarded."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"CliError: no backend configured\n")
    )
    mock_proc.returncode = 2
    with patch(
        "code_forge.mcp_server.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        result = await _run_cli_budgeted("review", budget=5.0)
        stdout, exit_code, elapsed, stderr = result
        assert stdout == ""
        assert exit_code == 2
        assert stderr == "CliError: no backend configured\n"


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
    """CancelledError triggers _kill_and_reap: kill + cancel task."""
    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
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
            return await _run_cli_budgeted("review", budget=100.0)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    mock_proc.kill.assert_called_once()


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
            return_value=("output", 0, 1.0, ""),
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
            return_value=("ok", 0, 0.5, ""),
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
        patch.object(
            __import__("code_forge.mcp_server", fromlist=["x"]),
            "_backend_names",
            backend_names,
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
            session=MagicMock(), committed=False
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
            await _dispatch_sampling(session=MagicMock(), committed=False)


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
            await _dispatch_sampling(session=MagicMock(), committed=False)
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
                session=MagicMock(), committed=False, staged=True
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
