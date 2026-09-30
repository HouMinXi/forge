# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Selecting the sampling outlet must say it is deprecated."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_forge.mcp_server import (
    _make_simple_result,
    forge_gate_check,
    forge_resolve_outlet,
    forge_review,
)

NOTICE = "sampling outlet is deprecated"


def _ctx():
    ctx = MagicMock()
    ctx.session.client_params.capabilities.sampling = MagicMock()
    return ctx


def _text(result):
    return result.content[0].text


@pytest.mark.asyncio
async def test_review_warns_when_env_selects_sampling():
    ctx = _ctx()
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=Path("/tmp")),
        patch("code_forge.mcp_server._reject_kernel_sampling"),
        patch(
            "code_forge.mcp_server._dispatch_sampling",
            new_callable=AsyncMock,
            return_value=_make_simple_result("ok", 0),
        ),
    ):
        result = await forge_review(ctx=ctx)
    assert NOTICE in _text(result)


@pytest.mark.asyncio
async def test_review_warns_when_gate_yaml_selects_sampling(tmp_path):
    ctx = _ctx()
    gate = tmp_path / ".code-forge"
    gate.mkdir()
    (gate / "gate.yaml").write_text("outlet: sampling\n")
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": ""}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=tmp_path),
        patch("code_forge.outlet_resolver.load_outlet_from_gate", return_value="sampling"),
        patch("code_forge.mcp_server._reject_kernel_sampling"),
        patch(
            "code_forge.mcp_server._dispatch_sampling",
            new_callable=AsyncMock,
            return_value=_make_simple_result("ok", 0),
        ),
    ):
        result = await forge_review(ctx=ctx)
    assert NOTICE in _text(result)


@pytest.mark.asyncio
async def test_gate_check_warns_when_env_selects_sampling():
    ctx = _ctx()
    with (
        patch.dict(os.environ, {"FORGE_OUTLET": "sampling"}),
        patch("code_forge.mcp_server._workspace_for", new_callable=AsyncMock, return_value=Path("/tmp")),
        patch("code_forge.mcp_server._reject_kernel_sampling"),
        patch(
            "code_forge.mcp_server._dispatch_sampling",
            new_callable=AsyncMock,
            return_value=_make_simple_result("ok", 0),
        ),
    ):
        result = await forge_gate_check(ctx=ctx)
    assert NOTICE in _text(result)


@pytest.mark.asyncio
async def test_resolve_outlet_warns_when_env_selects_sampling():
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
    assert NOTICE in _text(result)
