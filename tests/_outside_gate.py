"""Test helper (#160): CI never trusts an untracked in-tree gate.yaml, so CI
fixtures supply theirs through an outside config root (--test-config-root)."""

import shutil
from pathlib import Path


def outside_gate_root(root) -> Path:
    """Copy ``root/.code-forge/gate.yaml`` (if any) into a sibling directory
    outside the checkout and return that directory, for ``test_config_root``.
    Re-copies on every call, so a test that edits the in-tree gate and builds
    a new machine sees the edit."""
    root = Path(root)
    outside = root.parent / f"{root.name}-forge-config"
    (outside / ".code-forge").mkdir(parents=True, exist_ok=True)
    gate = root / ".code-forge" / "gate.yaml"
    target = outside / ".code-forge" / "gate.yaml"
    if gate.is_file():
        shutil.copyfile(gate, target)
    elif target.exists():
        target.unlink()
    return outside
