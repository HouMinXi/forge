"""Tests for contract_loader.py and trust.py contracts extensions.

Covers: trust extension for contracts (spec-content hashing), contract
config loading, env var expansion, spec file reading, stat-first size
gate, binary detection, LLM summarization with caching, containment
check, per-spec error isolation, digest assembly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ====================================================================
# Task 1: Trust extension tests (6 tests)
# ====================================================================


@pytest.fixture()
def trust_dir(tmp_path, monkeypatch):
    """Create an isolated trust store directory and redirect XDG_CONFIG_HOME."""
    config_home = tmp_path / "trust-config"
    config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    d = config_home / "code-forge"
    d.mkdir()
    return d


@pytest.fixture()
def contracts_yaml(tmp_path):
    """Create a minimal contracts.yaml on disk."""
    p = tmp_path / "contracts.yaml"
    p.write_text("repos:\n  kernel:\n    path: ../kernel\n    specs:\n      - path: spec.yaml\n")
    return p


def _make_resolved_contents(
    paths_and_texts: list[tuple[str, str]],
) -> list[tuple[str, bytes]]:
    """Helper: build resolved_contents from (path, text) pairs."""
    return [(p, t.encode("utf-8")) for p, t in paths_and_texts]


def test_trust_record_and_check(trust_dir, contracts_yaml):
    """record_trust_contracts then is_trusted_contracts returns True."""
    from code_forge.trust import is_trusted_contracts, record_trust_contracts

    contents = _make_resolved_contents([
        ("/repo/spec.yaml", "field: value\n"),
    ])
    record_trust_contracts(contracts_yaml, contents, config_dir=trust_dir)
    assert is_trusted_contracts(contracts_yaml, contents) is True


def test_untrusted_contracts_returns_false(trust_dir, contracts_yaml):
    """No record -> is_trusted_contracts returns False."""
    from code_forge.trust import is_trusted_contracts

    contents = _make_resolved_contents([
        ("/repo/spec.yaml", "field: value\n"),
    ])
    # Point XDG to our trust dir so _load_trust_store finds our (empty) store
    assert is_trusted_contracts(contracts_yaml, contents) is False


def test_trust_hash_changes_on_spec_content_change(trust_dir, contracts_yaml):
    """Modify spec content after record -> is_trusted_contracts returns False."""
    from code_forge.trust import is_trusted_contracts, record_trust_contracts

    original = _make_resolved_contents([
        ("/repo/spec.yaml", "field: original\n"),
    ])
    record_trust_contracts(contracts_yaml, original, config_dir=trust_dir)

    modified = _make_resolved_contents([
        ("/repo/spec.yaml", "field: MODIFIED\n"),
    ])
    assert is_trusted_contracts(contracts_yaml, modified) is False


def test_trust_hash_changes_on_spec_path_change(trust_dir, contracts_yaml):
    """Same content but different resolved path -> different hash."""
    from code_forge.trust import hash_contracts_content

    content_bytes = b"field: value\n"
    h1 = hash_contracts_content(contracts_yaml, [("/path/a.yaml", content_bytes)])
    h2 = hash_contracts_content(contracts_yaml, [("/path/b.yaml", content_bytes)])
    assert h1 != h2


def test_revoke_trust_contracts(trust_dir, contracts_yaml):
    """record then revoke -> is_trusted_contracts returns False."""
    from code_forge.trust import (
        is_trusted_contracts,
        record_trust_contracts,
        revoke_trust_contracts,
    )

    contents = _make_resolved_contents([
        ("/repo/spec.yaml", "field: value\n"),
    ])
    record_trust_contracts(contracts_yaml, contents, config_dir=trust_dir)
    assert is_trusted_contracts(contracts_yaml, contents) is True

    revoke_trust_contracts(contracts_yaml)
    assert is_trusted_contracts(contracts_yaml, contents) is False


def test_trust_status_contracts(trust_dir, contracts_yaml):
    """Check TrustStatus fields after record."""
    from code_forge.trust import (
        hash_contracts_content,
        record_trust_contracts,
        trust_status_contracts,
    )

    contents = _make_resolved_contents([
        ("/repo/spec.yaml", "field: value\n"),
    ])
    record_trust_contracts(contracts_yaml, contents, config_dir=trust_dir)

    status = trust_status_contracts(contracts_yaml, contents)
    assert status.trusted is True
    expected_hash = hash_contracts_content(contracts_yaml, contents)
    assert status.stored_hash == expected_hash
    assert status.current_hash == expected_hash
    assert status.gate_yaml_path == str(contracts_yaml.resolve())
