"""Test helper for #140: CI never trusts an untracked in-tree gate.yaml.

Fixtures that write the gate into the reviewed tmp tree and run CI supply it
as an explicit config root outside that tree, the way a CI job passes
--test-config-root. The outside root links back to the fixture's
.code-forge directory so a test can still edit the gate after construction.
"""

from pathlib import Path


def outside_config_root(root) -> Path:
    root = Path(root)
    outside = root.parent / f"{root.name}-ci-config"
    outside.mkdir(exist_ok=True)
    link = outside / ".code-forge"
    if not link.is_symlink() and not link.exists():
        (root / ".code-forge").mkdir(exist_ok=True)
        link.symlink_to(root / ".code-forge", target_is_directory=True)
    return outside
