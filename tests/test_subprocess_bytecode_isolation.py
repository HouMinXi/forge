# SPDX-License-Identifier: Apache-2.0
"""Subprocess fixtures must not write bytecode into the reviewed checkout (#37)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _caches(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("__pycache__"))


def test_suite_children_inherit_no_bytecode(monkeypatch):
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"


def test_inheriting_child_leaves_no_cache_in_checkout_copy(tmp_path):
    copy = tmp_path / "src"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert _caches(copy) == []
    env = dict(os.environ, PYTHONPATH=str(copy))
    subprocess.run(
        [sys.executable, "-c", "import code_forge.cli"],
        env=env,
        cwd=tmp_path,
        check=True,
    )
    assert _caches(copy) == []
