# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Cross-repo contract context loader.

Loads contracts.yaml, resolves spec file paths with env var expansion,
reads specs with stat-first size gating and binary detection, caches
LLM summarizations keyed on content hash, and assembles a digest string
for injection into the review prompt.

Exported symbols:
  ContractSpec         -- frozen dataclass for a single spec entry
  ContractRepo         -- frozen dataclass for a repo with specs
  ContractsConfig      -- frozen dataclass for the full config
  resolve_contract_specs -- resolve config to (name, path, abs, content, max_size) tuples
  load_contract_digest -- orchestrate loading, trust, caching, digest assembly
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .errors import CliError
from .llm_invoke import LLMInvokeError, llm_invoke
from .trust import is_trusted_contracts


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractSpec:
    """A single spec file reference within a contract repo."""

    path: str
    max_raw_size: int = 32768


@dataclass(frozen=True)
class ContractRepo:
    """A repo containing contract spec files."""

    path: str
    specs: list[ContractSpec]


@dataclass(frozen=True)
class ContractsConfig:
    """Top-level contracts configuration."""

    repos: dict[str, ContractRepo]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HARD_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB (SF-4)
_MAX_SUMMARIZE_BYTES = 512 * 1024  # 512 KB


def _warn(msg: str) -> None:
    """Write a warning to stderr."""
    sys.stderr.write("code-forge: contract: %s\n" % msg)


def _is_within_repo(path: Path, repo_root: Path) -> bool:
    """Check if path is contained within repo_root (CF-1).

    Uses repo_root.resolve() in path.resolve().parents for a
    path-component-safe containment check. Dedicated to contract specs
    (the conventions_resolver containment helper checks cwd.parent,
    which inverts the boundary for cross-repo specs).
    """
    try:
        resolved_path = path.resolve()
        resolved_root = repo_root.resolve()
    except OSError:
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_contracts_config(config_path: Path) -> ContractsConfig:
    """Load and validate contracts.yaml into frozen dataclasses.

    Raises CliError on schema violations (missing keys, wrong types).
    """
    with open(str(config_path), encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict) or "repos" not in data:
        raise CliError(
            "contracts.yaml must have a top-level 'repos' dict"
        )

    raw_repos = data["repos"]
    if not isinstance(raw_repos, dict):
        raise CliError(
            "contracts.yaml 'repos' must be a dict, got %s" % type(raw_repos).__name__
        )

    repos: dict[str, ContractRepo] = {}
    for repo_name, repo_data in raw_repos.items():
        if not isinstance(repo_data, dict):
            raise CliError(
                "contracts.yaml repo '%s' must be a dict" % repo_name
            )
        repo_path = repo_data.get("path", "")
        if not isinstance(repo_path, str) or not repo_path:
            raise CliError(
                "contracts.yaml repo '%s' must have a 'path' string" % repo_name
            )

        raw_specs = repo_data.get("specs", [])
        if not isinstance(raw_specs, list):
            raise CliError(
                "contracts.yaml repo '%s' specs must be a list" % repo_name
            )

        specs: list[ContractSpec] = []
        for spec_entry in raw_specs:
            if isinstance(spec_entry, str):
                specs.append(ContractSpec(path=spec_entry))
            elif isinstance(spec_entry, dict):
                spec_path = spec_entry.get("path", "")
                if not spec_path:
                    raise CliError(
                        "contracts.yaml spec in '%s' must have a 'path'" % repo_name
                    )
                max_raw = spec_entry.get("max_raw_size", 32768)
                specs.append(ContractSpec(path=spec_path, max_raw_size=int(max_raw)))
            else:
                raise CliError(
                    "contracts.yaml spec in '%s' must be a string or dict" % repo_name
                )

        repos[repo_name] = ContractRepo(path=repo_path, specs=specs)

    return ContractsConfig(repos=repos)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_repo_path(
    raw_path: str, cwd: Path
) -> tuple[Optional[Path], Optional[str]]:
    """Expand env vars in a repo path and resolve it.

    Returns (resolved_path, None) on success or (None, error_message) on
    failure. Relative paths resolve against cwd (the reviewed repo root),
    not os.getcwd().
    """
    expanded = os.path.expandvars(raw_path)
    if "$" in expanded:
        return None, "env var not set in: %s" % raw_path

    p = Path(expanded)
    if not p.is_absolute():
        resolved = (cwd / p).resolve()
    else:
        resolved = p.resolve()

    return resolved, None


# ---------------------------------------------------------------------------
# Spec file reading
# ---------------------------------------------------------------------------


def _read_spec_content(spec_path: Path) -> Optional[bytes]:
    """Read a spec file with stat-first size gate and binary detection.

    Returns None if the file is oversized (>_HARD_SIZE_LIMIT), binary
    (null bytes in first 1KB), or unreadable.
    """
    try:
        st = spec_path.stat()
    except OSError as exc:
        _warn("cannot stat: %s (%s)" % (spec_path, exc))
        return None

    if st.st_size > _HARD_SIZE_LIMIT:
        _warn("spec too large (stat): %s (%d bytes)" % (spec_path, st.st_size))
        return None

    content = spec_path.read_bytes()

    # Binary detection: null bytes in first 1024 bytes
    if b"\x00" in content[:1024]:
        _warn("binary file detected, skipping: %s" % spec_path)
        return None

    return content


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _content_hash(content: bytes) -> str:
    """Return sha256 hex of content bytes."""
    return hashlib.sha256(content).hexdigest()


def _spec_cache_path(
    cache_dir: Path, repo_name: str, spec_path_str: str, content_hash: str
) -> Path:
    """Build cache file path: {repo}_{pathHash12}_{contentHash}.json (SF-3)."""
    path_hash = hashlib.sha256(spec_path_str.encode()).hexdigest()[:12]
    return cache_dir / ("%s_%s_%s.json" % (repo_name, path_hash, content_hash))


def _read_spec_cache(cache_path: Path) -> Optional[str]:
    """Read summary from cache. Returns None on miss or error."""
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("summary")
    except Exception:
        return None


def _write_spec_cache(
    cache_path: Path, summary: str, source: str, content_hash: str
) -> None:
    """Write summary to cache file. OSError is non-fatal."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "source": source,
            "content_hash": content_hash,
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=True), encoding="utf-8"
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------


def _summarize_spec(content_str: str, spec_name: str, backend) -> str:
    """Summarize a spec via LLM, returning the summary string.

    Uses expected_keys=frozenset({"summary"}) so the JSON extractor
    accepts only dicts containing "summary" (SF-9). The prompt requests
    JSON {"summary": "..."} output.
    """
    prompt = (
        "Summarize the following specification for a code reviewer. "
        "Preserve all field names, attribute types, operation names, "
        "and constraints. Return your response as a JSON object with "
        'a single key: {"summary": "your summary text here"}.\n\n'
        "Spec (%s):\n%s" % (spec_name, content_str)
    )
    result = llm_invoke(
        prompt,
        backend=backend,
        expected_keys=frozenset({"summary"}),
    )
    if isinstance(result.content, dict):
        return result.content.get("summary", "")
    return str(result.content)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_contract_specs(
    config_path: Path, cwd: Path
) -> list[tuple[str, str, str, bytes, int]]:
    """Resolve contract specs to 5-tuples.

    Returns list of (repo_name, spec_path, abs_path_str, content_bytes,
    max_raw_size). Single source of truth for spec resolution + reading.
    Both load_contract_digest and Plan 02's trust call use this to ensure
    the trust hash covers identical resolution.
    """
    config = load_contracts_config(config_path)
    results: list[tuple[str, str, str, bytes, int]] = []

    for repo_name, repo in config.repos.items():
        repo_path, err = _resolve_repo_path(repo.path, cwd)
        if err is not None:
            _warn(err)
            continue

        for spec in repo.specs:
            try:
                resolved = (repo_path / spec.path).resolve()

                # Containment check against the EXTERNAL repo root (CF-1)
                if not _is_within_repo(resolved, repo_path):
                    _warn(
                        "spec outside repo root: %s (root: %s)"
                        % (resolved, repo_path)
                    )
                    continue

                content = _read_spec_content(resolved)
                if content is None:
                    continue

                results.append((
                    repo_name,
                    spec.path,
                    str(resolved),
                    content,
                    spec.max_raw_size,
                ))
            except OSError as exc:
                _warn("spec error: %s/%s (%s)" % (repo_name, spec.path, exc))
                continue

    return results


def load_contract_digest(
    config_path: Path,
    cwd: Path,
    backend=None,
) -> str:
    """Orchestrate contract loading, trust check, and digest assembly.

    Returns "" on any error (missing file, untrusted, resolution failure,
    summarization error). Non-empty result contains '## Contract: ...'
    sections for each resolved spec.
    """
    if not config_path.is_file():
        return ""

    try:
        resolved_specs = resolve_contract_specs(config_path, cwd)
    # Memory exhaustion is not a contract problem.  Degrading to an empty
    # digest would hand back a review that quietly lost its contract
    # context and can still report PASS; fail loudly instead.
    except MemoryError:
        raise
    except Exception as exc:
        _warn("failed to resolve specs: %s" % exc)
        return ""

    try:
        # Build trust contents: (abs_path, content) pairs
        trust_contents = [
            (abs_path, content)
            for _, _, abs_path, content, _ in resolved_specs
        ]

        if not is_trusted_contracts(config_path, trust_contents):
            _warn("contracts not trusted: %s" % config_path)
            return ""

        cache_dir = cwd / ".code-forge" / "cache" / "contracts"
        sections: list[str] = []

        for repo_name, spec_path, abs_path, content_bytes, max_raw_size in resolved_specs:
            digest_text = ""

            if len(content_bytes) <= max_raw_size:
                # Small spec: inject raw
                digest_text = content_bytes.decode("utf-8", errors="replace")
            elif len(content_bytes) > _MAX_SUMMARIZE_BYTES:
                # Too large even for summarization
                _warn("spec too large for summarization: %s" % abs_path)
            else:
                # Summarizable: check cache, then LLM
                c_hash = _content_hash(content_bytes)
                content_str = content_bytes.decode("utf-8", errors="replace")

                cache_path = _spec_cache_path(
                    cache_dir, repo_name, spec_path, c_hash
                )
                cached = _read_spec_cache(cache_path)
                if cached is not None:
                    digest_text = cached
                else:
                    try:
                        summary = _summarize_spec(
                            content_str,
                            "%s/%s" % (repo_name, spec_path),
                            backend,
                        )
                        if summary:
                            _write_spec_cache(
                                cache_path, summary, spec_path, c_hash
                            )
                        digest_text = summary
                    except LLMInvokeError as exc:
                        _warn(
                            "summarization failed for %s/%s: %s"
                            % (repo_name, spec_path, exc)
                        )

            if digest_text:
                sections.append(
                    "## Contract: %s/%s\n%s" % (repo_name, spec_path, digest_text)
                )

        return "\n\n".join(sections) if sections else ""

    # Same reasoning as the resolve guard above: an out-of-memory failure
    # must not be laundered into "no contract context, carry on".
    except MemoryError:
        raise
    except Exception as exc:
        _warn("unexpected error: %s" % exc)
        return ""
