"""Independent authorization must not inherit automatic backend re-signing."""
import hashlib
import json

import pytest

from code_forge import trust
from code_forge.kernel_context import validate_kernel_context


def config(**kw):
    return validate_kernel_context({"enabled": True, "defconfig": "a//defconfig", **kw})


def test_hash_canonical_binds_root(tmp_path):
    canonical = json.dumps({"defconfig": "a/defconfig", "enabled": True,
                            "workspace_root": tmp_path.resolve().as_posix()},
                           sort_keys=True, separators=(",", ":"))
    assert trust.hash_kernel_context(tmp_path, config()) == hashlib.sha256(canonical.encode()).hexdigest()


def test_backend_resign_never_grants_kernel_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    gate = tmp_path / "gate.yaml"
    data = {"kernel_context": {"enabled": True, "defconfig": "defconfig"}}
    trust.record_trust(gate, data)
    assert trust.is_trusted(gate, data)
    assert not trust.is_trusted_kernel_context(gate, tmp_path, config())
    trust.record_kernel_context_trust(gate, tmp_path, config())
    assert trust.is_trusted_kernel_context(gate, tmp_path, config())
    changed = config(defconfig="other")
    trust.record_trust(gate, {"kernel_context": {"enabled": True, "defconfig": "other"}})
    assert not trust.is_trusted_kernel_context(gate, tmp_path, changed)
    assert trust.is_trusted_kernel_context(gate, tmp_path, config())


@pytest.mark.parametrize("change", ["root", "path", "enabled"])
def test_each_authorization_dimension_invalidates(tmp_path, monkeypatch, change):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    gate = tmp_path / "gate.yaml"
    trust.record_kernel_context_trust(gate, tmp_path, config())
    root = tmp_path / "second" if change == "root" else tmp_path
    cfg = config(defconfig="other") if change == "path" else config(enabled=False) if change == "enabled" else config()
    assert not trust.is_trusted_kernel_context(gate, root, cfg)


def test_shared_gate_symlink_does_not_share_read_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    gate = first / "gate.yaml"
    gate.write_text("kernel_context: {}\n")
    link = second / "gate.yaml"
    link.symlink_to(gate)
    trust.record_kernel_context_trust(gate, first, config())
    assert trust.is_trusted_kernel_context(gate, first, config())
    assert not trust.is_trusted_kernel_context(link, second, config())


def test_legacy_hash_is_not_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    gate = tmp_path / "gate.yaml"
    trust.record_trust(gate, {})
    store = trust._load_trust_store()
    store[str(gate.resolve())]["kernel_context_hash"] = hashlib.sha256(
        b'{"defconfig":"a/defconfig","enabled":true}'
    ).hexdigest()
    trust._save_trust_store(store)
    assert not trust.is_trusted_kernel_context(gate, tmp_path, config())
    assert trust._load_trust_store() == store
