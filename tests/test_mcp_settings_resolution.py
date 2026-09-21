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
        cwd=root,
        env={**os.environ, 'PYTHONPATH': str(root / 'src')},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('remove_cwd', [
    False,
    pytest.param(
        True,
        marks=pytest.mark.skipif(
            os.name == 'nt', reason='Windows locks the current directory',
        ),
    ),
])
def test_import_probe_uses_its_source_root(tmp_path, monkeypatch, remove_cwd):
    original_run = subprocess.run
    seen = []

    def record_run(*args, **kwargs):
        seen.append(kwargs.get('cwd'))
        return original_run(*args, **kwargs)

    caller_dir = tmp_path / 'caller'
    caller_dir.mkdir()
    monkeypatch.chdir(caller_dir)
    if remove_cwd:
        caller_dir.rmdir()
    monkeypatch.setattr(subprocess, 'run', record_run)
    test_server_import_with_warnings_as_errors()
    assert seen == [Path(__file__).resolve().parents[1]]
