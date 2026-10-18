"""Test helper for #140: CI never trusts an untracked in-tree gate.yaml.

Fixtures that write the gate into the reviewed tmp tree and run CI supply it
as an explicit config root outside that tree, the way a CI job passes
--test-config-root. The loader refuses an outside root that links back into
the checkout, so this is a real copy; call it again after editing the gate.
"""

import shutil
from pathlib import Path


def outside_config_root(root) -> Path:
    root = Path(root)
    outside = root.parent / f"{root.name}-ci-config"
    (outside / ".code-forge").mkdir(parents=True, exist_ok=True)
    source = root / ".code-forge" / "gate.yaml"
    if source.is_file():
        shutil.copyfile(source, outside / ".code-forge" / "gate.yaml")
    return outside
