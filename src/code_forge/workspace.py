"""Shared workspace resolution -- no MCP imports.

Extracted from mcp_server.py so doctor.py can reuse the same
walk-up logic without pulling in MCP dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional


SAMPLING_REMEDIATION = (
    "Switch outlet to 'subprocess' in .code-forge/gate.yaml, "
    "or use a client that advertises MCP sampling."
)


def resolve_workspace(
    cwd: Path,
    env: Mapping[str, str],
    home: Optional[Path] = None,
) -> Path:
    """Forge workspace root: explicit env, else walk up from cwd.

    Priority: FORGE_PROJECT_DIR > nearest ancestor with
    .code-forge/gate.yaml (skipping $HOME) > cwd as-is.

    $HOME is skipped because it is a configuration domain, not a
    project.  A stale .code-forge/ left there by a previous
    ``forge_init`` would act as a walkup magnet, binding every
    subdirectory to ~ as the workspace root (ADR-0006, ADR-0009).

    Args:
        cwd: current working directory
        env: environment mapping (os.environ or test-injected)
        home: home directory for skip check (defaults to Path.home())
    """
    explicit = env.get("FORGE_PROJECT_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if home is None:
        home = Path.home()
    home = home.resolve()
    start = cwd.resolve()
    for d in (start, *start.parents):
        if d == home:
            continue
        if (d / ".code-forge" / "gate.yaml").is_file():
            return d
    return start
