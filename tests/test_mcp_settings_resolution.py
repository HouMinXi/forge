# SPDX-License-Identifier: Apache-2.0
"""Resolve SDK settings before constructing the MCP server."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_server_import_with_warnings_as_errors():
    pytest.importorskip('mcp')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-W', 'error', '-c', (
            'import code_forge.mcp_server; '
            'from mcp.server.fastmcp.server import Settings; '
            'assert Settings.__pydantic_complete__; '
            'assert Settings.model_fields["lifespan"].annotation is not None'
        )],
        env={**os.environ, 'PYTHONPATH': str(root / 'src')},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
