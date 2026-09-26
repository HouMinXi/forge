"""Dockerfile fixture support: refuse a host that cannot map identities.

buildah needs a user namespace to build and run a disposable image.
A session with NoNewPrivs set, or without subordinate uid ranges,
cannot write its uid map. The fixture must say so instead of skipping
silently or reporting a pass.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class BuilderUnavailable(Exception):
    """The host cannot give the builder its own user namespace."""


def identity_mapping_error(status_text: str | None = None, subuid_text: str | None = None) -> str | None:
    """None when this process can map subordinate identities.

    Anything else is the reason a build would be a lie. Tests pass the
    texts directly so they do not have to replace Path methods.
    """
    caller_supplied = status_text is not None or subuid_text is not None
    if status_text is None:
        status = Path("/proc/self/status")
        status_text = status.read_text() if status.is_file() else ""
    for line in status_text.splitlines():
        if line.startswith("NoNewPrivs:") and line.split()[-1] == "1":
                return "NoNewPrivs is set, so a user namespace cannot be mapped"
    user = os.environ.get("USER") or ""
    if subuid_text is None:
        subuid = Path("/etc/subuid")
        subuid_text = subuid.read_text() if user and subuid.is_file() else ""
    if user:
        owned = [
            line for line in subuid_text.splitlines() if line.startswith(user + ":")
        ]
        if not owned:
            return "no subordinate uid range for %s" % user
    if caller_supplied:
        return None
    probe = subprocess.run(
        ["unshare", "--user", "--map-root-user", "true"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        return "user namespace mapping failed: %s" % (detail[-1] if detail else "unshare exited %d" % probe.returncode)
    return None


def require_identity_mapping() -> None:
    reason = identity_mapping_error()
    if reason is not None:
        raise BuilderUnavailable(reason)
