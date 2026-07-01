"""Tests for the trust gate module (trust.py).

Covers: hash stability, trust store CRUD, XDG config resolution,
dangerous field detection, corrupted store recovery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def trust_home(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so trust store lives under tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    return config_dir


@pytest.fixture()
def gate_yaml(tmp_path):
    """Create a minimal gate.yaml file on disk."""
    p = tmp_path / "gate.yaml"
    p.write_text("backends:\n  mimo:\n    type: api\n    base_url: https://x\n")
    return p


# -- hash_backends_block --------------------------------------------------


def test_hash_backends_block_deterministic():
    """Same gate_data in different dict orderings produces same hash."""
    from code_forge.trust import hash_backends_block

    data_a = {"backends": {"b": {"base_url": "https://b.com"}, "a": {"base_url": "https://a.com"}}}
    data_b = {"backends": {"a": {"base_url": "https://a.com"}, "b": {"base_url": "https://b.com"}}}
    assert hash_backends_block(data_a) == hash_backends_block(data_b)


def test_hash_backends_block_none_input():
    """gate_data=None treated as empty dict -> sha256 of '{}'."""
    from code_forge.trust import hash_backends_block

    h_none = hash_backends_block(None)
    h_empty = hash_backends_block({})
    assert h_none == h_empty
    assert isinstance(h_none, str)
    assert len(h_none) == 64  # sha256 hex length


def test_hash_backends_block_no_backends_key():
    """gate_data with no 'backends' key -> sha256 of '{}'."""
    from code_forge.trust import hash_backends_block

    h = hash_backends_block({"outlet": "cli"})
    h_empty = hash_backends_block({})
    assert h == h_empty


def test_hash_backends_block_changes_on_dangerous_content():
    """Different dangerous fields produce different hashes."""
    from code_forge.trust import hash_backends_block

    h1 = hash_backends_block({"backends": {"a": {"base_url": "https://a.com"}}})
    h2 = hash_backends_block({"backends": {"a": {"base_url": "https://b.com"}}})
    assert h1 != h2


def test_hash_backends_block_ignores_benign_fields():
    """Benign fields (model, temperature) do not affect hash."""
    from code_forge.trust import hash_backends_block

    base = {"backends": {"a": {"base_url": "https://a.com", "api_key_env": "K"}}}
    h1 = hash_backends_block(base)
    with_model = {"backends": {"a": {
        "base_url": "https://a.com", "api_key_env": "K", "model": "gpt-4",
    }}}
    h2 = hash_backends_block(with_model)
    assert h1 == h2


# -- _config_dir -----------------------------------------------------------


def test_config_dir_xdg(tmp_path, monkeypatch):
    """When XDG_CONFIG_HOME is set, _config_dir uses it."""
    from code_forge.trust import _config_dir

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = _config_dir()
    assert result == tmp_path / "xdg" / "code-forge"


def test_config_dir_default(monkeypatch):
    """When XDG_CONFIG_HOME is unset, _config_dir uses ~/.config."""
    from code_forge.trust import _config_dir

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    result = _config_dir()
    assert result == Path.home() / ".config" / "code-forge"


# -- record_trust + is_trusted --------------------------------------------


def test_record_and_check_trusted(trust_home, gate_yaml):
    """After record_trust, is_trusted returns True."""
    from code_forge.trust import is_trusted, record_trust

    gate_data = {"backends": {"x": {"base_url": "https://a.com"}}}
    record_trust(gate_yaml, gate_data)
    assert is_trusted(gate_yaml, gate_data) is True


def test_is_trusted_false_no_record(trust_home, gate_yaml):
    """is_trusted returns False when no record exists."""
    from code_forge.trust import is_trusted

    gate_data = {"backends": {"x": {"base_url": "https://a.com"}}}
    assert is_trusted(gate_yaml, gate_data) is False


def test_is_trusted_false_after_change(trust_home, gate_yaml):
    """is_trusted returns False when dangerous fields have changed."""
    from code_forge.trust import is_trusted, record_trust

    original = {"backends": {"x": {"base_url": "https://a.com"}}}
    record_trust(gate_yaml, original)
    modified = {"backends": {"x": {"base_url": "https://evil.com"}}}
    assert is_trusted(gate_yaml, modified) is False


# -- revoke_trust ----------------------------------------------------------


def test_revoke_trust_removes_entry(trust_home, gate_yaml):
    """revoke_trust removes the entry from the store."""
    from code_forge.trust import is_trusted, record_trust, revoke_trust

    gate_data = {"backends": {"x": {"type": "api"}}}
    record_trust(gate_yaml, gate_data)
    assert is_trusted(gate_yaml, gate_data) is True
    revoke_trust(gate_yaml)
    assert is_trusted(gate_yaml, gate_data) is False


def test_revoke_trust_noop_when_not_trusted(trust_home, gate_yaml):
    """revoke_trust on a non-trusted path is a no-op (no error)."""
    from code_forge.trust import revoke_trust

    # Must not raise
    revoke_trust(gate_yaml)


# -- trust_status ----------------------------------------------------------


def test_trust_status_untrusted(trust_home, gate_yaml):
    """trust_status for an untrusted path returns trusted=False."""
    from code_forge.trust import trust_status

    gate_data = {"backends": {"x": {"type": "api"}}}
    status = trust_status(gate_yaml, gate_data)
    assert status.trusted is False
    assert status.stored_hash is None
    assert isinstance(status.current_hash, str)
    assert status.gate_yaml_path == str(gate_yaml.resolve())


def test_trust_status_trusted(trust_home, gate_yaml):
    """trust_status for a trusted path returns trusted=True with matching hashes."""
    from code_forge.trust import record_trust, trust_status

    gate_data = {"backends": {"x": {"type": "api"}}}
    record_trust(gate_yaml, gate_data)
    status = trust_status(gate_yaml, gate_data)
    assert status.trusted is True
    assert status.stored_hash == status.current_hash


# -- find_dangerous_fields ------------------------------------------------


def test_find_dangerous_fields_detects_all():
    """find_dangerous_fields detects all DANGEROUS_FIELDS members."""
    from code_forge.trust import find_dangerous_fields

    gate_data = {
        "backends": {
            "evil": {
                "base_url": "https://evil.com",
                "api_key_env": "MY_KEY",
                "api_key_file": "/tmp/key",
                "shell": "bash",
                "command": "curl evil",
                "hook": "post_review",
                "credentials_path": "/sa.json",
            }
        }
    }
    dangers = find_dangerous_fields(gate_data)
    field_names = {f for _, f, _ in dangers}
    assert "base_url" in field_names
    assert "api_key_env" in field_names
    assert "api_key_file" in field_names
    assert "shell" in field_names
    assert "command" in field_names
    assert "hook" in field_names
    assert "credentials_path" in field_names
    assert len(dangers) == 7


def test_find_dangerous_fields_ignores_empty():
    """Empty/None values are not flagged."""
    from code_forge.trust import find_dangerous_fields

    gate_data = {
        "backends": {
            "safe": {
                "base_url": "",
                "api_key_env": None,
                "type": "api",
            }
        }
    }
    dangers = find_dangerous_fields(gate_data)
    assert len(dangers) == 0


def test_find_dangerous_fields_no_backends():
    """No backends key returns empty list."""
    from code_forge.trust import find_dangerous_fields

    assert find_dangerous_fields({}) == []
    assert find_dangerous_fields({"outlet": "cli"}) == []


def test_find_dangerous_fields_list_format():
    """find_dangerous_fields handles YAML list format (real gate.yaml)."""
    from code_forge.trust import find_dangerous_fields

    gate_data = {
        "backends": [
            {
                "name": "evil",
                "type": "api",
                "base_url": "https://evil.com",
                "api_key_env": "MY_KEY",
                "shell": "bash",
            }
        ]
    }
    dangers = find_dangerous_fields(gate_data)
    assert len(dangers) == 3
    names = {f for _, f, _ in dangers}
    assert names == {"base_url", "api_key_env", "shell"}
    assert all(bname == "evil" for bname, _, _ in dangers)


def test_find_dangerous_fields_list_format_unnamed():
    """List entry without name key uses 'unnamed' as backend name."""
    from code_forge.trust import find_dangerous_fields

    gate_data = {"backends": [{"type": "api", "base_url": "https://x.com"}]}
    dangers = find_dangerous_fields(gate_data)
    assert len(dangers) == 1
    assert dangers[0][0] == "unnamed"


# -- corrupted store -------------------------------------------------------


def test_corrupted_store_treated_as_empty(trust_home, gate_yaml):
    """Corrupted trusted.json (invalid JSON) treated as empty store."""
    from code_forge.trust import is_trusted

    store_path = trust_home / "code-forge" / "trusted.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("NOT VALID JSON {{{")

    gate_data = {"backends": {"x": {"type": "api"}}}
    # Must not raise, must return False (no trust)
    assert is_trusted(gate_yaml, gate_data) is False


# -- atomic write ----------------------------------------------------------


def test_save_uses_atomic_write(trust_home, gate_yaml):
    """record_trust uses tmp+replace pattern (no .tmp leftover)."""
    from code_forge.trust import record_trust

    gate_data = {"backends": {"x": {"type": "api"}}}
    record_trust(gate_yaml, gate_data)

    store_dir = trust_home / "code-forge"
    assert (store_dir / "trusted.json").exists()
    assert not (store_dir / "trusted.json.tmp").exists()


# -- no in-repo trust file -------------------------------------------------


def test_no_in_repo_trust_file():
    """trust.py must not reference .trusted or any in-repo trust file."""
    import inspect
    import code_forge.trust as trust_mod

    source = inspect.getsource(trust_mod)
    assert ".trusted" not in source


def test_record_trust_with_explicit_config_dir(tmp_path, gate_yaml, monkeypatch):
    """record_trust(config_dir=...) isolates writes from os.environ trust store."""
    from code_forge.trust import record_trust, _load_trust_store

    isolated_dir = tmp_path / "isolated-config" / "code-forge"
    isolated_dir.mkdir(parents=True)
    # Ensure os.environ does not point here
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "env-config"))

    gate_data = {"backends": {"x": {"type": "api"}}}
    record_trust(gate_yaml, gate_data, config_dir=isolated_dir)

    # Written to explicit dir
    assert (isolated_dir / "trusted.json").exists()
    # os.environ trust store untouched
    env_store = _load_trust_store()
    assert str(gate_yaml.resolve()) not in env_store
