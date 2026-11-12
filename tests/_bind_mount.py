"""Real bind-mount fixtures using the stock AppArmor-supported bwrap entry point.

Only the fixture workspace is writable. Namespace setup failures remain explicit
skips (and therefore fail the stock CI skip gate); failures after the probe are
errors, never skips. No host policy or capability changes are made.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def _namespace_command(bwrap):
    return [bwrap, "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]


def require_bwrap():
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        pytest.skip("real bind-mount fixture requires bubblewrap")
    probe = subprocess.run(
        [*_namespace_command(bwrap), "--", sys.executable, "-B", "-c", "pass"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode:
        pytest.skip("bubblewrap user/mount namespaces unavailable")
    return bwrap


# os.path.ismount cannot reliably identify same-filesystem bind mounts. Check
# mountinfo as well as inode identity, before executing any product assertion.
_VERIFY_MOUNTS = r"""
import os, re
from pathlib import Path
_mountpoints = {
    re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[4])
    for line in Path("/proc/self/mountinfo").read_text().splitlines()
}
for _source, _target in _fixture_binds:
    if _target not in _mountpoints or not os.path.samefile(_source, _target):
        raise RuntimeError("real bind-mount fixture was not established: " + _target)
"""


def bind_mount_command(bwrap, workspace, binds, script, args):
    workspace = Path(workspace).resolve(strict=True)
    command = [*_namespace_command(bwrap), "--bind", str(workspace), str(workspace)]
    verified = []
    for source, target in binds:
        source = Path(source).resolve(strict=True)
        target = Path(target).resolve(strict=True)
        if not source.is_relative_to(workspace) or not target.is_relative_to(workspace):
            raise ValueError("bind fixture paths must stay inside its workspace")
        if source == target:
            raise ValueError("bind fixture requires a distinct alias")
        command.extend(["--bind", str(source), str(target)])
        verified.append((str(source), str(target)))
    child = f"_fixture_binds = {verified!r}\n" + _VERIFY_MOUNTS + "\n" + script
    return [*command, "--", sys.executable, "-B", "-c", child, *map(str, args)]


def run_bind_mount_script(bwrap, workspace, binds, script, args, *, pythonpath):
    result = subprocess.run(
        bind_mount_command(bwrap, workspace, binds, script, args),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": str(pythonpath), "TMPDIR": str(workspace)},
    )
    assert result.returncode == 0, f"bind-mount child failed ({result.returncode}): {result.stderr}"
    return result
