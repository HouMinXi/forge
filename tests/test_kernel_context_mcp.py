"""Sampling rejects enabled kernel context without changing capability priority."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from code_forge import cli, mcp_server, trust
from code_forge.errors import CliError


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
    return SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        capabilities=SimpleNamespace(sampling=object() if sampling else None))))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_sampling_enabled_rejected(workspace, monkeypatch, name):
    dispatch = AsyncMock(side_effect=AssertionError("must reject before dispatch"))
    monkeypatch.setattr(mcp_server, "_dispatch_sampling", dispatch)
    with pytest.raises(ToolError) as caught:
        await getattr(mcp_server, name)(ctx=context())
    assert str(caught.value) == "kernel-context: MCP sampling path is not supported; run the CLI subprocess path"
    dispatch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_sampling_capability_error_first(workspace, name):
    with pytest.raises(ToolError, match="Client does not support sampling capability"):
        await getattr(mcp_server, name)(ctx=context(False))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_untrusted_kernel_section_filtered(workspace, monkeypatch, name):
    trust.revoke_trust(workspace)
    dispatch = AsyncMock(return_value="existing sampling")
    monkeypatch.setattr(mcp_server, "_dispatch_sampling", dispatch)
    assert await getattr(mcp_server, name)(ctx=context()) == "existing sampling"
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
@pytest.mark.parametrize("remediation", [None, "", "repair yaml"])
async def test_loader_error_preserves_remediation(workspace, monkeypatch, name, remediation):
    def fail(*args):
        raise CliError("parse failed", remediation=remediation)
    monkeypatch.setattr(cli, "_load_gate_backends", fail)
    with pytest.raises(ToolError) as caught:
        await getattr(mcp_server, name)(ctx=context())
    assert str(caught.value) == "parse failed" + ("\nrepair yaml" if remediation else "")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["forge_review", "forge_gate_check"])
async def test_real_invalid_yaml_preserves_loader_error(workspace, name):
    workspace.write_text("broken: [\n")
    with pytest.raises(CliError) as expected:
        cli._load_gate_backends(workspace)
    with pytest.raises(ToolError) as caught:
        await getattr(mcp_server, name)(ctx=context())
    exc = expected.value
    assert str(caught.value) == str(exc) + ("\n" + exc.remediation if exc.remediation else "")
