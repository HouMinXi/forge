# SPDX-License-Identifier: Apache-2.0
"""Explicitly opted-in real mutmut controls, outside the default CI roster."""

import json
import os
from pathlib import Path
import sys

import pytest

from code_forge._mutation_process import run_owned_command


@pytest.mark.skipif(
    os.environ.get("FORGE_RUN_NATIVE_MUTATION") != "1",
    reason="set FORGE_RUN_NATIVE_MUTATION=1 to run the bounded native fixture",
)
def test_real_mutmut_ci_ledger_controls(tmp_path):
    """The real owner also bounds and reaps the entire qualification worker."""
    worker = Path(__file__).with_name("worker.py")
    # Isolate before importing product modules in the worker, too.
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(tmp_path / "preflight-home"),
        "XDG_CONFIG_HOME": str(tmp_path / "preflight-home/.config"),
        "XDG_CACHE_HOME": str(tmp_path / "preflight-home/.cache"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    result = run_owned_command(
        [sys.executable, "-I", "-B", str(worker), str(tmp_path)],
        cwd=str(tmp_path),
        timeout=240,
        memory_limit_bytes=1024**3,
        env=environment,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.ownership["cleanup_complete"] is True
    assert result.ownership["timed_out"] is False
    assert result.ownership["cancelled"] is False
    assert not any(process.get("remaining") for process in result.ownership["owned"])
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["measurement_count"] == 6
    assert report["owned_command_count"] == 36
    assert [(case["name"], case["verdict"]) for case in report["cases"]] == [
        ("unruled", "FAIL"),
        ("exact", "PASS"),
        ("revoked", "FAIL"),
        ("new-source-review", "FAIL"),
        ("stale-source-review", "FAIL"),
    ]
    assert [case["eligible"] for case in report["cases"]] == [True, True, True, True, False]
    assert report["cleanup_complete"] is True
