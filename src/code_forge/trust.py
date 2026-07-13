"""Trust gate for repo-supplied gate.yaml backends.

Implements a direnv-style allow/deny model: the user explicitly trusts
each repo's gate.yaml before its backends are used. Trust is stored in
~/.config/code-forge/trusted.json (honoring XDG_CONFIG_HOME), keyed by
the realpath of gate.yaml. A repo cannot carry its own trust record.

The hash covers ONLY the backends block, not the entire file.
Changes to outlet/test/detect sections do not require re-trusting.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# -- Dangerous fields -----------------------------------------------

DANGEROUS_FIELDS: frozenset[str] = frozenset({
    "base_url",          # controls where credentials are sent (CWE-522)
    "api_key_env",       # names the env var containing the credential
    "api_key_file",      # names the file containing the credential
    "shell",             # arbitrary shell execution (CWE-78)
    "command",           # arbitrary command execution
    "hook",              # lifecycle hook execution
    "credentials_path",  # vertex: service account JSON path
})


# -- TrustStatus dataclass -------------------------------------------------


@dataclass(frozen=True)
class TrustStatus:
    """Result of a trust check."""

    trusted: bool
    stored_hash: Optional[str]
    current_hash: str
    gate_yaml_path: str


# -- Internal helpers -------------------------------------------------------


def _config_dir() -> Path:
    """Return XDG_CONFIG_HOME/code-forge, matching backend.py XDG pattern."""
    base = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    return Path(base) / "code-forge"


def _trust_store_path(config_dir: Optional[Path] = None) -> Path:
    """Return the path to the trust store JSON file."""
    return (config_dir if config_dir is not None else _config_dir()) / "trusted.json"


def _load_trust_store(config_dir: Optional[Path] = None) -> dict:
    """Load trusted.json, returning {} on missing or corrupt file."""
    path = _trust_store_path(config_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_trust_store(store: dict, config_dir: Optional[Path] = None) -> None:
    """Write trust store atomically (tmp + replace, POSIX safe)."""
    path = _trust_store_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    tmp.replace(path)


# -- Public API -------------------------------------------------------------


def _hash_all_backends(gate_data: Optional[dict]) -> str:
    """Legacy hash: sha256 of the entire backends block (pre-v2.7).

    Kept for migration: if a stored hash matches this but not the new
    dangerous-fields-only hash, the trust record is silently upgraded.
    """
    if gate_data is None:
        gate_data = {}
    backends = gate_data.get("backends", {})
    canonical = json.dumps(backends, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_backends_block(gate_data: Optional[dict]) -> str:
    """Return sha256 hex of only the dangerous fields in each backend.

    Only fields that control credential routing or command execution
    (DANGEROUS_FIELDS) are included. Model, temperature, max_tokens
    etc. can change without invalidating trust.
    """
    if gate_data is None:
        gate_data = {}
    backends = gate_data.get("backends", {})
    # Extract only dangerous fields per backend, sorted for stability
    filtered: dict[str, dict[str, str]] = {}
    if isinstance(backends, dict):
        for bname, bcfg in sorted(backends.items()):
            if not isinstance(bcfg, dict):
                continue
            dangerous = {
                k: v for k, v in sorted(bcfg.items())
                if k in DANGEROUS_FIELDS and v is not None and v != ""
            }
            if dangerous:
                filtered[bname] = dangerous
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_trusted(gate_yaml_path: Path, gate_data: dict) -> bool:
    """Check if gate.yaml's backends block matches the stored trust hash.

    Migration: if the stored hash matches the legacy all-fields hash but
    not the new dangerous-fields-only hash, silently upgrade the record.
    """
    store = _load_trust_store()
    key = str(gate_yaml_path.resolve())
    entry = store.get(key)
    if entry is None:
        return False
    stored = entry.get("hash")
    current_hash = hash_backends_block(gate_data)
    if stored == current_hash:
        return True
    # Migration: check legacy all-fields hash
    legacy_hash = _hash_all_backends(gate_data)
    if stored == legacy_hash:
        # Silently upgrade to dangerous-fields-only hash
        entry["hash"] = current_hash
        _save_trust_store(store)
        return True
    import sys
    print(
        "code-forge: trust invalidated: credential-related fields changed "
        "in gate.yaml. Run 'code-forge trust' to re-authorize.",
        file=sys.stderr,
    )
    return False


def record_trust(
    gate_yaml_path: Path,
    gate_data: dict,
    config_dir: Optional[Path] = None,
) -> None:
    """Record trust for gate.yaml's current backends block.

    Args:
        config_dir: override the trust store directory (used by eval runner
            to isolate per-run trust state without mutating os.environ).
    """
    store = _load_trust_store(config_dir)
    key = str(gate_yaml_path.resolve())
    current_hash = hash_backends_block(gate_data)
    store[key] = {"hash": current_hash}
    _save_trust_store(store, config_dir)


def revoke_trust(gate_yaml_path: Path) -> None:
    """Remove the trust record for gate.yaml (no-op if not trusted).

    Operates on the trusted.json entry keyed by the current gate.yaml
    realpath. There is NO in-repo trust file to read or delete.
    """
    store = _load_trust_store()
    key = str(gate_yaml_path.resolve())
    store.pop(key, None)
    _save_trust_store(store)


def trust_status(gate_yaml_path: Path, gate_data: dict) -> TrustStatus:
    """Return detailed trust status for gate.yaml."""
    store = _load_trust_store()
    key = str(gate_yaml_path.resolve())
    entry = store.get(key)
    current_hash = hash_backends_block(gate_data)
    stored_hash = entry.get("hash") if entry else None
    trusted = False
    if stored_hash:
        if stored_hash == current_hash:
            trusted = True
        else:
            # Check legacy hash for migration
            legacy = _hash_all_backends(gate_data)
            if stored_hash == legacy:
                trusted = True
                # Silently migrate
                entry["hash"] = current_hash
                _save_trust_store(store)
                stored_hash = current_hash
    return TrustStatus(
        trusted=trusted,
        stored_hash=stored_hash,
        current_hash=current_hash,
        gate_yaml_path=key,
    )


def find_dangerous_fields(
    gate_data: dict,
) -> list[tuple[str, str, str]]:
    """Return list of (backend_name, field_name, field_value) for dangerous fields.

    Dangerous fields are those that control where credentials are sent,
    what commands are executed, or where secrets are read from.
    """
    dangers: list[tuple[str, str, str]] = []
    backends = gate_data.get("backends", {})
    if isinstance(backends, list):
        entries = [(b.get("name", "unnamed"), b) for b in backends if isinstance(b, dict)]
    elif isinstance(backends, dict):
        entries = list(backends.items())
    else:
        return dangers
    for bname, bconfig in entries:
        if not isinstance(bconfig, dict):
            continue
        for field_name in DANGEROUS_FIELDS:
            value = bconfig.get(field_name)
            if value is not None and value != "":
                dangers.append((bname, field_name, str(value)))
    return dangers


# -- Contracts trust (spec-content hashing) ------------------------------


def hash_contracts_content(
    contracts_yaml_path: Path,
    resolved_contents: list[tuple[str, bytes]],
) -> str:
    """Hash resolved spec file contents for contracts trust verification.

    Builds a canonical JSON array of [path, sha256(content)] pairs sorted
    by path, then sha256 of that JSON string. Covers both the resolved
    paths and the file contents so a post-trust spec edit (path or content)
    invalidates the trust record.

    Args:
        contracts_yaml_path: path to the contracts.yaml manifest (for API
            consistency with is_trusted_contracts; not used in the hash).
        resolved_contents: list of (resolved_path_str, file_content_bytes)
            tuples for all specs that were successfully resolved.

    Returns:
        sha256 hexdigest of the canonical JSON.
    """
    pairs = sorted(
        [path, hashlib.sha256(content).hexdigest()]
        for path, content in resolved_contents
    )
    canonical = json.dumps(pairs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_trusted_contracts(
    contracts_yaml_path: Path,
    resolved_contents: list[tuple[str, bytes]],
) -> bool:
    """Check if contracts.yaml's resolved specs match the stored trust hash.

    Uses "contracts_hash" key (not "hash") to avoid collision with
    gate.yaml trust entries that share the same trust store.
    """
    store = _load_trust_store()
    key = str(contracts_yaml_path.resolve())
    entry = store.get(key)
    if entry is None:
        return False
    current = hash_contracts_content(contracts_yaml_path, resolved_contents)
    return entry.get("contracts_hash") == current


def record_trust_contracts(
    contracts_yaml_path: Path,
    resolved_contents: list[tuple[str, bytes]],
    config_dir: Optional[Path] = None,
) -> None:
    """Record trust for contracts.yaml's current resolved spec contents.

    Args:
        config_dir: override the trust store directory for test isolation.
    """
    store = _load_trust_store(config_dir)
    key = str(contracts_yaml_path.resolve())
    current = hash_contracts_content(contracts_yaml_path, resolved_contents)
    store[key] = {"contracts_hash": current}
    _save_trust_store(store, config_dir)


def revoke_trust_contracts(contracts_yaml_path: Path) -> None:
    """Remove the trust record for contracts.yaml (no-op if not trusted)."""
    store = _load_trust_store()
    key = str(contracts_yaml_path.resolve())
    store.pop(key, None)
    _save_trust_store(store)


def trust_status_contracts(
    contracts_yaml_path: Path,
    resolved_contents: list[tuple[str, bytes]],
) -> TrustStatus:
    """Return detailed trust status for contracts.yaml.

    Uses "contracts_hash" field to disambiguate from gate.yaml entries.
    """
    store = _load_trust_store()
    key = str(contracts_yaml_path.resolve())
    entry = store.get(key)
    current = hash_contracts_content(contracts_yaml_path, resolved_contents)
    stored = entry.get("contracts_hash") if entry else None
    return TrustStatus(
        trusted=(stored == current) if stored else False,
        stored_hash=stored,
        current_hash=current,
        gate_yaml_path=key,
    )
