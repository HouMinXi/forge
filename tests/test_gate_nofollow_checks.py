"""#156 rebase onto #145/#147/#149: every check of _read_gate_nofollow fires."""

import hashlib
import os

import pytest

from code_forge.gate_check import UntrustedGateConfigError, _load_gate_nofollow, _read_gate_nofollow

GATE = "test:\n  command: [pytest]\n"


def _root(tmp_path):
    root = tmp_path / "cfg"
    (root / ".code-forge").mkdir(parents=True)
    (root / ".code-forge" / "gate.yaml").write_text(GATE, encoding="utf-8")
    return root


def test_hardlink_refusal_still_fires(tmp_path):
    root = _root(tmp_path)
    os.link(root / ".code-forge" / "gate.yaml", tmp_path / "second-link")
    with pytest.raises(UntrustedGateConfigError, match="hard links"):
        _read_gate_nofollow(root, refuse_hardlinks=True)
    assert _read_gate_nofollow(root) == GATE  # only refused when asked


def test_inode_refusal_still_fires(tmp_path):
    root = _root(tmp_path)
    info = os.stat(root / ".code-forge" / "gate.yaml")
    with pytest.raises(UntrustedGateConfigError, match="hardlink of a file in the reviewed checkout"):
        _read_gate_nofollow(root, refuse_inodes=frozenset({(info.st_dev, info.st_ino)}))


def test_sha256_pin_still_fires_and_matching_pin_parses(tmp_path):
    root = _root(tmp_path)
    good = hashlib.sha256(GATE.encode()).hexdigest()
    assert _load_gate_nofollow(root, expected_sha256=good)["test"]["command"] == ["pytest"]
    with pytest.raises(UntrustedGateConfigError, match="changed during this review"):
        _read_gate_nofollow(root, expected_sha256="0" * 64)


def test_checks_combine(tmp_path):
    root = _root(tmp_path)
    os.link(root / ".code-forge" / "gate.yaml", tmp_path / "second-link")
    good = hashlib.sha256(GATE.encode()).hexdigest()
    with pytest.raises(UntrustedGateConfigError, match="hard links"):
        _read_gate_nofollow(root, refuse_hardlinks=True, expected_sha256=good)
