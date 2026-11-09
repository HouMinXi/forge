# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Bound registry FIFO checks in child processes; use real filesystem objects."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
@pytest.mark.parametrize("alias", [False, True], ids=["direct", "symlink"])
def test_fifo_registry_is_unavailable_without_waiting(tmp_path, alias):
    path = tmp_path / "registry.json"
    os.mkfifo(path)
    if alias:
        link = tmp_path / "alias.json"
        link.symlink_to(path)
        path = link
    code = r"""
import sys
import types
from pathlib import Path
from code_forge.cross_repo_impact import _read_registry, GraphDiscoveryError, CrossRepoImpactRunner
path = Path(sys.argv[1])
try:
    _read_registry(path)
except GraphDiscoveryError as exc:
    assert str(exc) == 'registry unreadable'
else:
    raise AssertionError('nonregular registry accepted')
# Stub only the optional provider's pure locator, not registry I/O.
provider = types.ModuleType('code_review_graph')
registry = types.ModuleType('code_review_graph.registry')
registry.default_registry_path = lambda: path
sys.modules['code_review_graph'] = provider
sys.modules['code_review_graph.registry'] = registry
runner = CrossRepoImpactRunner()
assert runner.run('+++ b/source.py\n', path.parent) == []
assert runner.infra_errors == ['cross-repo-impact: graph discovery unavailable: registry unreadable']
print('registry unavailable')
"""
    # subprocess.run kills and reaps the direct child if the old open blocks.
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "registry unavailable\n"


@pytest.mark.parametrize("alias", [False, True], ids=["direct", "symlink"])
def test_regular_registry_keeps_utf8_and_symlink_behavior(tmp_path: Path, alias):
    from code_forge.cross_repo_impact import _read_registry

    path = tmp_path / "registry.json"
    repos = [{"path": "/repo", "alias": "仓库"}]
    payload = json.dumps({"repos": repos}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(payload)
    if alias:
        link = tmp_path / "alias.json"
        link.symlink_to(path)
        path = link
    assert _read_registry(path) == repos
    assert path.read_bytes() == payload
