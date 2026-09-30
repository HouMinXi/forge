# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Selecting the sampling outlet is a config error, not a review."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_forge.mcp_server import forge_gate_check, forge_resolve_outlet, forge_review
from mcp.server.fastmcp.exceptions import ToolError

REMOVED = "sampling outlet was removed"


def _ctx():
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_review_rejects_sampling_from_env():
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=Path("/tmp")),
    ):
        with pytest.raises(ToolError, match=REMOVED):
            await forge_review(ctx=_ctx())


@pytest.mark.asyncio
async def test_review_rejects_sampling_from_gate_yaml(tmp_path):
    gate = tmp_path / ".code-forge"
    gate.mkdir()
    (gate / "gate.yaml").write_text("outlet: sampling\n")
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": ""}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=tmp_path),
        patch("code_forge.outlet_resolver.load_outlet_from_gate", return_value="sampling"),
    ):
        with pytest.raises(ToolError, match=REMOVED):
            await forge_review(ctx=_ctx())


@pytest.mark.asyncio
async def test_gate_check_rejects_sampling_from_env():
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=Path("/tmp")),
    ):
        with pytest.raises(ToolError, match=REMOVED):
            await forge_gate_check(ctx=_ctx())


@pytest.mark.asyncio
async def test_resolve_outlet_says_sampling_was_removed():
    ctx = _ctx()
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=Path("/tmp")),
        patch(
            "code_forge.mcp_server._run_cli_simple",
            new_callable=AsyncMock,
            return_value=("resolved\n", "", 0),
        ),
        patch("code_forge.mcp_server._backend_names_for", return_value=[]),
    ):
        result = await forge_resolve_outlet(ctx=ctx)
    assert REMOVED in result.content[0].text
