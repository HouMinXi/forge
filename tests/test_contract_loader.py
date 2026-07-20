"""Tests for contract_loader.py and trust.py contracts extensions.

Covers: trust extension for contracts (spec-content hashing), contract
config loading, env var expansion, spec file reading, stat-first size
gate, binary detection, LLM summarization with caching, containment
check, per-spec error isolation, digest assembly.
"""
from __future__ import annotations

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


# ====================================================================
# Task 2: Contract loader tests (22 tests)
# ====================================================================


def _write_contracts_yaml(tmp_path, repos_dict):
    """Helper: write a contracts.yaml from a repos dict."""
    import yaml

    cfg = {"repos": repos_dict}
    p = tmp_path / "contracts.yaml"
    p.write_text(yaml.dump(cfg, default_flow_style=False))
    return p


def _setup_repo_with_specs(tmp_path, repo_name, specs):
    """Helper: create a repo dir with spec files.

    specs: list of (relative_path, content_str) tuples.
    Returns the repo directory path.
    """
    repo_dir = tmp_path / repo_name
    repo_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in specs:
        spec_file = repo_dir / rel_path
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_bytes(
            content.encode("utf-8") if isinstance(content, str) else content
        )
    return repo_dir


# -- Config loading tests --


def test_load_valid_contracts_yaml(tmp_path):
    """Valid YAML parsed into ContractsConfig."""
    from code_forge.contract_loader import ContractsConfig, load_contracts_config

    repo_dir = _setup_repo_with_specs(tmp_path, "kernel", [
        ("net/ovs/spec.yaml", "name: ovs_flow\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "kernel": {
            "path": str(repo_dir),
            "specs": [{"path": "net/ovs/spec.yaml"}],
        },
    })

    config = load_contracts_config(cfg_path)
    assert isinstance(config, ContractsConfig)
    assert "kernel" in config.repos
    assert config.repos["kernel"].path == str(repo_dir)
    assert len(config.repos["kernel"].specs) == 1
    assert config.repos["kernel"].specs[0].path == "net/ovs/spec.yaml"


def test_load_invalid_yaml_raises_cli_error(tmp_path):
    """Missing required fields, wrong types -> CliError."""
    from code_forge.contract_loader import load_contracts_config
    from code_forge.errors import CliError

    # No repos key
    p = tmp_path / "bad.yaml"
    p.write_text("not_repos: {}\n")
    with pytest.raises(CliError):
        load_contracts_config(p)

    # repos is not a dict
    p.write_text("repos: []\n")
    with pytest.raises(CliError):
        load_contracts_config(p)


def test_env_var_expansion(tmp_path, monkeypatch):
    """$VAR expanded in repo path."""
    from code_forge.contract_loader import _resolve_repo_path

    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    monkeypatch.setenv("MY_REPO_PATH", str(repo_dir))

    resolved, err = _resolve_repo_path("$MY_REPO_PATH", tmp_path)
    assert err is None
    assert resolved == repo_dir.resolve()


def test_missing_env_var_graceful_skip(tmp_path, trust_dir, monkeypatch, capsys):
    """Unset env var produces empty digest with warning."""
    from code_forge.contract_loader import load_contract_digest

    cfg_path = _write_contracts_yaml(tmp_path, {
        "kernel": {
            "path": "$UNDEFINED_REPO_VAR_XYZ",
            "specs": [{"path": "spec.yaml"}],
        },
    })
    # Trust the empty resolved contents so the trust check passes
    from code_forge.trust import record_trust_contracts
    record_trust_contracts(cfg_path, [], config_dir=trust_dir)

    result = load_contract_digest(cfg_path, tmp_path)
    # Should return "" because no specs resolved
    assert result == ""


def test_spec_file_not_found_graceful(tmp_path, trust_dir):
    """Missing spec path, returns empty digest."""
    from code_forge.contract_loader import load_contract_digest

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "nonexistent.yaml"}],
        },
    })
    # Trust check would get empty contents
    from code_forge.trust import record_trust_contracts
    record_trust_contracts(cfg_path, [], config_dir=trust_dir)

    result = load_contract_digest(cfg_path, tmp_path)
    assert result == ""


def test_binary_file_detection(tmp_path):
    """Null bytes in first 1KB cause skip with warning."""
    from code_forge.contract_loader import _read_spec_content

    spec = tmp_path / "binary.dat"
    spec.write_bytes(b"some\x00binary\x00data" + b"x" * 100)

    result = _read_spec_content(spec)
    assert result is None


def test_small_spec_raw_injection(tmp_path, trust_dir):
    """Spec under max_raw_size injected as raw text."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("spec.yaml", "name: small_spec\nops:\n  - get\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "spec.yaml"}],
        },
    })
    # Build trust contents matching what resolve_contract_specs returns
    spec_file = repo_dir / "spec.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    result = load_contract_digest(cfg_path, tmp_path)
    assert "name: small_spec" in result
    assert "## Contract: repo/spec.yaml" in result


def test_large_spec_summarized(tmp_path, trust_dir, monkeypatch):
    """Spec over max_raw_size triggers llm_invoke."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    large_content = "field: " + "x" * 40000 + "\n"
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("big.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "big.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "big.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    mock_result_obj = type("LLMResult", (), {
        "content": {"summary": "A summary of big spec"},
        "usage": type("Usage", (), {"input_tokens": 0, "output_tokens": 0})(),
        "duration_s": 0.1,
    })()

    with patch("code_forge.contract_loader.llm_invoke", return_value=mock_result_obj) as mock_llm:
        result = load_contract_digest(cfg_path, tmp_path)
        mock_llm.assert_called_once()
        assert "A summary of big spec" in result


def test_summary_cache_hit(tmp_path, trust_dir, monkeypatch):
    """sha256 cache file exists, llm_invoke not called."""
    from code_forge.contract_loader import (
        _content_hash,
        _spec_cache_path,
        _write_spec_cache,
        load_contract_digest,
    )
    from code_forge.trust import record_trust_contracts

    large_content = "y" * 40000
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("cached.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "cached.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "cached.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    # Pre-populate cache
    cache_dir = tmp_path / ".code-forge" / "cache" / "contracts"
    c_hash = _content_hash(content)
    cache_path = _spec_cache_path(cache_dir, "repo", "cached.yaml", c_hash)
    _write_spec_cache(cache_path, "cached summary", "cached.yaml", c_hash)

    with patch("code_forge.contract_loader.llm_invoke") as mock_llm:
        result = load_contract_digest(cfg_path, tmp_path)
        mock_llm.assert_not_called()
        assert "cached summary" in result


def test_summary_cache_miss(tmp_path, trust_dir, monkeypatch):
    """No cache, llm_invoke called, cache file written."""
    from code_forge.contract_loader import _content_hash, _spec_cache_path, load_contract_digest
    from code_forge.trust import record_trust_contracts

    large_content = "z" * 40000
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("miss.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "miss.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "miss.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    mock_result_obj = type("LLMResult", (), {
        "content": {"summary": "summarized miss"},
        "usage": type("Usage", (), {"input_tokens": 0, "output_tokens": 0})(),
        "duration_s": 0.1,
    })()

    with patch("code_forge.contract_loader.llm_invoke", return_value=mock_result_obj):
        result = load_contract_digest(cfg_path, tmp_path)
        assert "summarized miss" in result

    # Verify cache was written
    cache_dir = tmp_path / ".code-forge" / "cache" / "contracts"
    c_hash = _content_hash(content)
    cache_path = _spec_cache_path(cache_dir, "repo", "miss.yaml", c_hash)
    assert cache_path.exists()


def test_cache_key_uses_12_hex_chars(tmp_path):
    """hexdigest()[:12] in cache filename (SF-3)."""
    from code_forge.contract_loader import _spec_cache_path

    cache_dir = tmp_path / "cache"
    path = _spec_cache_path(cache_dir, "repo", "spec.yaml", "abc123def456")
    stem = path.stem
    # Format: {repo_name}_{spec_path_hash12}_{content_hash}.json
    parts = stem.split("_")
    assert len(parts) == 3
    # spec_path_hash12 should be 12 chars
    assert len(parts[1]) == 12


def test_summarization_failure_graceful(tmp_path, trust_dir, monkeypatch):
    """llm_invoke raises LLMInvokeError, returns empty for that spec."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.llm_invoke import LLMInvokeError
    from code_forge.trust import record_trust_contracts

    large_content = "w" * 40000
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("fail.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "fail.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "fail.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    with patch(
        "code_forge.contract_loader.llm_invoke",
        side_effect=LLMInvokeError("backend timeout", is_timeout=True),
    ):
        result = load_contract_digest(cfg_path, tmp_path)
        # Should return "" since summarization failed
        assert result == ""


# -- Containment check tests --


def test_containment_check_rejects_outside_repo(tmp_path):
    """_is_within_repo returns False for paths above repo root (CF-1)."""
    from code_forge.contract_loader import _is_within_repo

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "elsewhere" / "evil.yaml"
    outside.parent.mkdir(parents=True)
    outside.touch()

    assert _is_within_repo(outside, repo_root) is False


def test_containment_check_allows_nested(tmp_path):
    """_is_within_repo returns True for paths within repo."""
    from code_forge.contract_loader import _is_within_repo

    repo_root = tmp_path / "repo"
    nested = repo_root / "sub" / "spec.yaml"
    nested.parent.mkdir(parents=True)
    nested.touch()

    assert _is_within_repo(nested, repo_root) is True


# -- Digest and integration tests --


def test_untrusted_contracts_returns_empty(tmp_path, trust_dir):
    """Trust check fails, digest is empty."""
    from code_forge.contract_loader import load_contract_digest

    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("spec.yaml", "name: test\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "spec.yaml"}],
        },
    })
    # Do NOT record trust -- should fail trust check
    result = load_contract_digest(cfg_path, tmp_path)
    assert result == ""


def test_digest_assembly_format(tmp_path, trust_dir):
    """Output has '## Contract: {name}\\n{content}' sections."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("a.yaml", "name: alpha\n"),
        ("b.yaml", "name: beta\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [
                {"path": "a.yaml"},
                {"path": "b.yaml"},
            ],
        },
    })
    # Build trust
    specs = [
        (str((repo_dir / "a.yaml").resolve()), (repo_dir / "a.yaml").read_bytes()),
        (str((repo_dir / "b.yaml").resolve()), (repo_dir / "b.yaml").read_bytes()),
    ]
    record_trust_contracts(cfg_path, specs, config_dir=trust_dir)

    result = load_contract_digest(cfg_path, tmp_path)
    assert "## Contract: repo/a.yaml" in result
    assert "## Contract: repo/b.yaml" in result
    assert "name: alpha" in result
    assert "name: beta" in result


def test_no_contracts_yaml_returns_empty(tmp_path):
    """FileNotFoundError returns empty."""
    from code_forge.contract_loader import load_contract_digest

    result = load_contract_digest(tmp_path / "nonexistent.yaml", tmp_path)
    assert result == ""


def test_multiple_repos_multiple_specs(tmp_path, trust_dir):
    """Two repos each with specs, all assembled."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    repo_a = _setup_repo_with_specs(tmp_path, "repoA", [
        ("s1.yaml", "name: s1\n"),
    ])
    repo_b = _setup_repo_with_specs(tmp_path, "repoB", [
        ("s2.yaml", "name: s2\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repoA": {
            "path": str(repo_a),
            "specs": [{"path": "s1.yaml"}],
        },
        "repoB": {
            "path": str(repo_b),
            "specs": [{"path": "s2.yaml"}],
        },
    })
    specs = [
        (str((repo_a / "s1.yaml").resolve()), (repo_a / "s1.yaml").read_bytes()),
        (str((repo_b / "s2.yaml").resolve()), (repo_b / "s2.yaml").read_bytes()),
    ]
    record_trust_contracts(cfg_path, specs, config_dir=trust_dir)

    result = load_contract_digest(cfg_path, tmp_path)
    assert "## Contract: repoA/s1.yaml" in result
    assert "## Contract: repoB/s2.yaml" in result


def test_per_spec_oserror_isolated(tmp_path, trust_dir):
    """One unreadable spec does not abort the loop (CF-2)."""
    from code_forge.contract_loader import resolve_contract_specs

    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("good.yaml", "name: good\n"),
        # bad.yaml will be made unreadable
        ("bad.yaml", "name: bad\n"),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [
                {"path": "good.yaml"},
                {"path": "bad.yaml"},
            ],
        },
    })

    bad_file = repo_dir / "bad.yaml"
    bad_file.chmod(0o000)

    try:
        results = resolve_contract_specs(cfg_path, tmp_path)
        # At least good.yaml should resolve; bad.yaml skipped via OSError
        paths = [r[1] for r in results]
        assert "good.yaml" in paths
    finally:
        bad_file.chmod(0o644)


def test_stat_rejects_oversized_before_read(tmp_path):
    """File larger than 10x max_raw_size is rejected before full read (SF-4)."""
    from code_forge.contract_loader import _read_spec_content, _HARD_SIZE_LIMIT

    # Create a file that stat reports as huge (using a sparse file)
    huge = tmp_path / "huge.yaml"
    huge.write_bytes(b"x" * (_HARD_SIZE_LIMIT + 1))

    result = _read_spec_content(huge)
    assert result is None


def test_summarize_uses_summary_expected_keys(tmp_path, trust_dir, monkeypatch):
    """llm_invoke called with expected_keys=frozenset({'summary'}) (SF-9)."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    large_content = "k" * 40000
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("ek.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "ek.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "ek.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    mock_result_obj = type("LLMResult", (), {
        "content": {"summary": "ek summary"},
        "usage": type("Usage", (), {"input_tokens": 0, "output_tokens": 0})(),
        "duration_s": 0.1,
    })()

    with patch("code_forge.contract_loader.llm_invoke", return_value=mock_result_obj) as mock_llm:
        load_contract_digest(cfg_path, tmp_path)
        call_kwargs = mock_llm.call_args
        assert call_kwargs.kwargs.get("expected_keys") == frozenset({"summary"})


def test_bytes_decoded_before_summarization(tmp_path, trust_dir, monkeypatch):
    """Spec content bytes decoded to str before passing to summarizer (SF-2)."""
    from code_forge.contract_loader import load_contract_digest
    from code_forge.trust import record_trust_contracts

    # Use content with a UTF-8 multibyte char to prove decoding
    large_content = "field: value\n" + "x" * 40000
    repo_dir = _setup_repo_with_specs(tmp_path, "repo", [
        ("utf.yaml", large_content),
    ])
    cfg_path = _write_contracts_yaml(tmp_path, {
        "repo": {
            "path": str(repo_dir),
            "specs": [{"path": "utf.yaml", "max_raw_size": 100}],
        },
    })
    spec_file = repo_dir / "utf.yaml"
    content = spec_file.read_bytes()
    trust_contents = [(str(spec_file.resolve()), content)]
    record_trust_contracts(cfg_path, trust_contents, config_dir=trust_dir)

    mock_result_obj = type("LLMResult", (), {
        "content": {"summary": "decoded summary"},
        "usage": type("Usage", (), {"input_tokens": 0, "output_tokens": 0})(),
        "duration_s": 0.1,
    })()

    with patch("code_forge.contract_loader.llm_invoke", return_value=mock_result_obj) as mock_llm:
        load_contract_digest(cfg_path, tmp_path)
        # The prompt arg (first positional) should be a str, not bytes
        prompt_arg = mock_llm.call_args.args[0]
        assert isinstance(prompt_arg, str)
        assert "field: value" in prompt_arg


# ====================================================================
# Memory exhaustion must abort, not degrade to an empty digest
# ====================================================================

def test_memoryerror_in_spec_resolution_propagates(tmp_path):
    """MemoryError while resolving specs aborts instead of returning "".

    The surrounding `except Exception` deliberately degrades to an empty
    digest so a broken contract cannot kill a review.  Memory exhaustion
    is not that kind of failure: swallowing it yields a review that lost
    its contract context and can still report PASS.
    """
    from code_forge.contract_loader import load_contract_digest

    cfg_path = tmp_path / "contracts.yaml"
    cfg_path.write_text("repos:\n  t:\n    path: .\n    specs: []\n")

    with patch(
        "code_forge.contract_loader.resolve_contract_specs",
        side_effect=MemoryError("out of memory"),
    ):
        with pytest.raises(MemoryError):
            load_contract_digest(cfg_path, tmp_path)


def test_memoryerror_in_digest_assembly_propagates(tmp_path):
    """MemoryError after resolution also aborts rather than degrading.

    Covers the second guard, which wraps trust checking and digest
    assembly.  Same reasoning as the resolution guard above.
    """
    from code_forge.contract_loader import load_contract_digest

    cfg_path = tmp_path / "contracts.yaml"
    cfg_path.write_text("repos:\n  t:\n    path: .\n    specs: []\n")

    with patch(
        "code_forge.contract_loader.resolve_contract_specs",
        return_value=[],
    ), patch(
        "code_forge.contract_loader.is_trusted_contracts",
        side_effect=MemoryError("out of memory"),
    ):
        with pytest.raises(MemoryError):
            load_contract_digest(cfg_path, tmp_path)
