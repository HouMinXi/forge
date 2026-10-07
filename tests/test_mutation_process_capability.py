"""Unavailable ownership capabilities must fail before native execution."""

import os
from pathlib import Path
import sys

import pytest

from code_forge import _mutation_process as process


@pytest.mark.parametrize("failure", [FileNotFoundError(), PermissionError(), ValueError()])
def test_owner_rejects_unreadable_child_enumeration(monkeypatch, failure):
    tree = process._OwnedTree()
    observed = []

    def read_bytes(path):
        observed.append(path)
        raise failure

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    try:
        with pytest.raises(RuntimeError, match="native command was not launched") as caught:
            tree.verify_enumeration()
        assert caught.value.__cause__ is failure
        assert observed == [Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")]
    finally:
        tree.close()


def test_real_owner_checks_enumeration_before_native_launch(tmp_path):
    """Exercise the isolated owner, including the actual restricted procfs."""
    marker = tmp_path / "native-command-started"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    try:
        Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_bytes()
    except OSError:
        available = False
    else:
        available = True
    if available:
        result = process.run_owned_command(command, timeout=3)
        assert result.returncode == 0 and result.ownership["cleanup_complete"]
        assert marker.exists()
    else:
        with pytest.raises(process.MutationProcessError, match="ownership unavailable") as caught:
            process.run_owned_command(command, timeout=3)
        assert caught.value.cleanup_complete
        assert caught.value.report["cleanup_complete"]
        assert caught.value.report["owned"] == []
        assert "returncode" not in caught.value.report
        assert not marker.exists()
