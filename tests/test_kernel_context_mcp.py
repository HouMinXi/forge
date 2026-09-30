"""Selecting sampling is a config error; kernel-context checks never run."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from code_forge import mcp_server, trust


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FORGE_OUTLET", "sampling")
    gate = tmp_path / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    data = {"kernel_context": {"enabled": True, "defconfig": "defconfig"}}
    gate.write_text(yaml.safe_dump(data))
    trust.record_trust(gate, data)
    monkeypatch.setattr(mcp_server, "_workspace_for", AsyncMock(return_value=tmp_path))
    return gate


def context(sampling=True):
    return SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=SimpleNamespace(sampling=object() if sampling else None)
            )
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_sampling_rejected_before_any_kernel_check(workspace, monkeypatch, name):
    """The outlet refusal fires regardless of kernel-context config."""
    load = AsyncMock(side_effect=AssertionError("must not load gate backends"))
    monkeypatch.setattr(mcp_server, "_check_backend", load)
    with pytest.raises(ToolError, match="sampling outlet was removed"):
        await getattr(mcp_server, name)(ctx=context())
    load.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_sampling_rejected_without_client_capability(workspace, name):
    """Capability no longer matters; the outlet itself is gone."""
    with pytest.raises(ToolError, match="sampling outlet was removed"):
        await getattr(mcp_server, name)(ctx=context(False))
