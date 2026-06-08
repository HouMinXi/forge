# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for conventions_resolver.py (D12 SPEC Stage 1 + Stage 2 + caching).

Covers: resolve_sources (4 sources), extract_conventions (multi-language),
get_cross_repo_digest (caching), and get_digest integration (M-03).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from code_forge.conventions_resolver import (
    ResolvedSource,
    extract_conventions,
    get_cross_repo_digest,
    resolve_sources,
)
from code_forge.conventions import get_digest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _mock_git(stdout: str = "abc123\n", returncode: int = 0):
    """Context manager: mock subprocess.run for git rev-parse HEAD."""
    return patch(
        "code_forge.conventions_resolver.subprocess.run",
        return_value=_make_proc(stdout, returncode),
    )


# ---------------------------------------------------------------------------
# TestSourceResolver
# ---------------------------------------------------------------------------

class TestSourceResolver:

    def test_custom_mapping_highest_priority(self, tmp_path):
        sibling1 = tmp_path / "sibling1"
        sibling1.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n"
            "  - repo: " + str(sibling1) + "\n"
            "    target: command names\n"
            "    recipe: ovs_commands\n"
        )
        result = resolve_sources(tmp_path)
        assert len(result) >= 1
        assert result[0].priority == 1
        assert result[0].source_type == "custom"
        assert result[0].repo_path == sibling1

    def test_custom_mapping_symlink_guard(self, tmp_path):
        """Source 1 must reject paths outside cwd.parent (M-R2-02)."""
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: /etc/passwd\n"
        )
        result = resolve_sources(tmp_path)
        assert not any(str(s.repo_path) == "/etc/passwd" for s in result)

    def test_custom_mapping_default_target_recipe(self, tmp_path):
        """Entries without target/recipe must get sensible defaults (M-R2-06)."""
        sibling = tmp_path / "sibling_nofields"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        result = resolve_sources(tmp_path)
        assert len(result) >= 1
        assert result[0].target == "public names"
        assert result[0].recipe == "default"
        assert result[0].priority == 1          # L-R5-05
        assert result[0].source_type == "custom"  # L-R5-05

    def test_agents_md_yaml_frontmatter(self, tmp_path):
        sibling2 = tmp_path / "sibling2"
        sibling2.mkdir()
        (tmp_path / "AGENTS.md").write_text(
            "---\n"
            "related_repos:\n"
            "  - " + str(sibling2) + "\n"
            "---\n"
            "# Agent docs\n"
        )
        result = resolve_sources(tmp_path)
        assert any(
            s.source_type == "agents_md" and s.repo_path == sibling2
            for s in result
        )

    def test_agents_md_no_frontmatter_fallback(self, tmp_path):
        """AGENTS.md without '---\\n' header must fall back to regex (L-02)."""
        sibling3 = tmp_path / "sibling3"
        sibling3.mkdir()
        (tmp_path / "AGENTS.md").write_text(
            "# My Agents\nRelated repos:\n- `" + str(sibling3) + "`\n"
        )
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "agents_md" for s in result)

    def test_agents_md_frontmatter_without_marker(self, tmp_path):
        """Non-YAML AGENTS.md must not crash (graceful degradation)."""
        (tmp_path / "AGENTS.md").write_text("not yaml at all\njust text\n")
        result = resolve_sources(tmp_path)
        assert isinstance(result, list)  # does not crash

    def test_agent_context_files(self, tmp_path):
        """CLAUDE.md with absolute path must yield agent_context source (H-04)."""
        sibling4 = tmp_path / "sibling4"
        sibling4.mkdir()
        (tmp_path / "CLAUDE.md").write_text(
            "# Config\nSee " + str(sibling4) + " for details.\n"
        )
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "agent_context" for s in result)

    def test_agent_context_relative_path(self, tmp_path):
        """CLAUDE.md with ../ relative path must resolve to sibling (H-04)."""
        sibling5_rel = tmp_path.parent / "sibling5_rel"
        sibling5_rel.mkdir(exist_ok=True)
        (tmp_path / "CLAUDE.md").write_text(
            "See ../sibling5_rel for details.\n"
        )
        result = resolve_sources(tmp_path)
        resolved_paths = [s.repo_path for s in result if s.source_type == "agent_context"]
        assert any(str(p).endswith("sibling5_rel") for p in resolved_paths)

    def test_path_re_special_chars(self, tmp_path):
        """_PATH_RE must match paths containing + (L-R2-01 expanded char class)."""
        lib_plus = tmp_path / "lib+extra"
        lib_plus.mkdir()
        (tmp_path / "CLAUDE.md").write_text(
            "See " + str(lib_plus) + " for more.\n"
        )
        result = resolve_sources(tmp_path)
        assert any("lib+extra" in str(s.repo_path) for s in result)

    def test_gitmodules_dependency(self, tmp_path):
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        (tmp_path / ".gitmodules").write_text(
            '[submodule "lib"]\n'
            "    path = lib\n"
            "    url = https://example.com/lib.git\n"
        )
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "dependency" for s in result)

    def test_package_json_local_dep(self, tmp_path):
        sibling5 = tmp_path.parent / "sibling5"
        sibling5.mkdir(exist_ok=True)
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"local-pkg": "file:../sibling5"}})
        )
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "dependency" for s in result)

    def test_pyproject_toml_path_dep(self, tmp_path):
        """pyproject.toml path= regex must find local path deps (M-R2-05)."""
        pylib = tmp_path / "pylib"
        pylib.mkdir()
        (tmp_path / "pyproject.toml").write_text(
            "[tool.setuptools]\npath = \"" + str(pylib) + "\"\n"
        )
        result = resolve_sources(tmp_path)
        assert any(
            s.source_type == "dependency" and "pylib" in str(s.repo_path)
            for s in result
        )

    def test_deduplication_keeps_highest_priority(self, tmp_path):
        """Same sibling in both custom yaml and AGENTS.md -> only 1 entry, priority=1."""
        sibling = tmp_path / "shared"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        (tmp_path / "AGENTS.md").write_text(
            "---\nrelated_repos:\n  - " + str(sibling) + "\n---\n"
        )
        result = resolve_sources(tmp_path)
        matching = [s for s in result if s.repo_path == sibling]
        assert len(matching) == 1
        assert matching[0].priority == 1  # custom wins

    def test_dedup_two_mdc_files_same_path(self, tmp_path):
        """Two .mdc files referencing same path -> only one ResolvedSource (L-R4-04)."""
        shared_sibling = tmp_path / "shared_sibling"
        shared_sibling.mkdir()
        cursor_rules = tmp_path / ".cursor" / "rules"
        cursor_rules.mkdir(parents=True)
        (cursor_rules / "a.mdc").write_text(
            "See " + str(shared_sibling) + " for rules.\n"
        )
        (cursor_rules / "b.mdc").write_text(
            "Also check " + str(shared_sibling) + " always.\n"
        )
        result = resolve_sources(tmp_path)
        matching = [s for s in result if s.repo_path == shared_sibling]
        assert len(matching) == 1

    def test_missing_files_graceful(self, tmp_path):
        """Empty directory must return empty list (no crash)."""
        result = resolve_sources(tmp_path)
        assert result == []

    def test_malformed_yaml_graceful(self, tmp_path):
        """Invalid YAML in conventions.yaml must not raise."""
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("{{{{invalid yaml\n")
        result = resolve_sources(tmp_path)
        assert isinstance(result, list)  # does not crash


# ---------------------------------------------------------------------------
# TestExtraction
# ---------------------------------------------------------------------------

class TestExtraction:

    def test_python_public_names_via_shared_helper(self, tmp_path):
        """extract_conventions delegates to _extract_python_public_names (B-02)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "module.py").write_text("def serve(): pass\nclass Handler: pass\n")
        source = ResolvedSource(
            repo_path=tmp_path, priority=1, source_type="custom",
            target="public names", recipe="default",
        )
        result = extract_conventions(source)
        assert "serve" in result
        assert "Handler" in result

    def test_js_ts_named_exports(self, tmp_path):
        (tmp_path / "index.ts").write_text(
            "export function fetchData() {}\nexport class ApiClient {}\n"
        )
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "fetchData" in result
        assert "ApiClient" in result

    def test_js_ts_default_exports(self, tmp_path):
        """export default class must be caught by second regex pass (L-R2-02)."""
        (tmp_path / "main.ts").write_text("export default class Router {}\n")
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "Router" in result

    def test_go_extraction(self, tmp_path):
        (tmp_path / "main.go").write_text(
            "func ServeHTTP() {}\ntype Handler struct{}\n"
        )
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "ServeHTTP" in result
        assert "Handler" in result

    def test_rust_extraction(self, tmp_path):
        (tmp_path / "lib.rs").write_text(
            "pub fn process() {}\npub struct Config {}\n"
        )
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "process" in result
        assert "Config" in result

    def test_empty_repo(self, tmp_path):
        """Empty dir must return empty string."""
        source = ResolvedSource(
            repo_path=tmp_path, priority=1, source_type="custom", recipe="default",
        )
        result = extract_conventions(source)
        assert result == ""

    def test_nonexistent_path(self, tmp_path):
        """Nonexistent path must return empty string without exception."""
        source = ResolvedSource(
            repo_path=Path("/nonexistent/path"), priority=1, source_type="custom",
        )
        result = extract_conventions(source)
        assert result == ""

    def test_custom_recipe_uses_default_extraction(self, tmp_path):
        """Custom recipe name must appear in header; extraction still works (D17)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "module.py").write_text("def set_flow(): pass\nclass OvsCmd: pass\n")
        source = ResolvedSource(
            repo_path=tmp_path, priority=1, source_type="custom",
            target="command names", recipe="ovs_commands",
        )
        result = extract_conventions(source)
        assert "recipe: ovs_commands" in result
        # Default extraction still runs (D17)
        assert "set_flow" in result or "OvsCmd" in result

    def test_large_file_skipped(self, tmp_path):
        """Files >100KB must be skipped in all language extractors (M-06)."""
        big_content = "export function bigFn() {}\n" * 5000  # well over 100KB
        (tmp_path / "big.ts").write_text(big_content)
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        # bigFn would appear thousands of times -- file must be skipped
        assert result == "" or "bigFn" not in result

    def test_skips_venv_tox_dirs(self, tmp_path):
        """_SKIP_DIRS must exclude venv/ and similar dirs (L-R2-04)."""
        venv_dir = tmp_path / "venv"
        venv_dir.mkdir()
        (venv_dir / "mod.py").write_text("def venv_fn(): pass\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "good.py").write_text("def real_fn(): pass\n")
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "real_fn" in result
        assert "venv_fn" not in result


# ---------------------------------------------------------------------------
# TestCaching
# ---------------------------------------------------------------------------

class TestCaching:

    def test_cache_written_on_first_call(self, tmp_path):
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def helper(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            get_cross_repo_digest(tmp_path)
        cache_dir = tmp_path / ".code-forge" / "conventions-cache"
        assert cache_dir.is_dir()
        assert len(list(cache_dir.glob("*.json"))) >= 1

    def test_cache_key_uses_encode_hexdigest(self, tmp_path):
        """Cache filename prefix must match sha256(str(path).encode())[:12] (B-03)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def helper(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            get_cross_repo_digest(tmp_path)
        cache_dir = tmp_path / ".code-forge" / "conventions-cache"
        expected_hash = hashlib.sha256(
            str(sibling.resolve()).encode()
        ).hexdigest()[:12]
        cache_files = list(cache_dir.glob("*.json"))
        assert any(f.stem.startswith(expected_hash) for f in cache_files)

    def test_cache_hit_on_second_call(self, tmp_path):
        """Second call with same git commit must not re-run extract_conventions."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def helper(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            with patch(
                "code_forge.conventions_resolver.extract_conventions",
                wraps=extract_conventions,
            ) as mock_extract:
                get_cross_repo_digest(tmp_path)
                get_cross_repo_digest(tmp_path)
        # extract_conventions must be called exactly once (cache hit on 2nd call)
        assert mock_extract.call_count == 1

    def test_stale_cache_evicted(self, tmp_path):
        """Changing git commit must evict old cache file and create new one."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def helper(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            get_cross_repo_digest(tmp_path)
        cache_dir = tmp_path / ".code-forge" / "conventions-cache"
        old_files = list(cache_dir.glob("*abc123.json"))
        assert len(old_files) == 1

        with _mock_git("def456\n"):
            get_cross_repo_digest(tmp_path)
        assert not any(f.name.endswith("abc123.json") for f in cache_dir.glob("*.json"))
        assert any("def456" in f.name for f in cache_dir.glob("*.json"))

    def test_orphaned_cache_cleanup(self, tmp_path):
        """Cache files for removed siblings must be deleted (L-R2-03)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def helper(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        cache_dir = cfg / "conventions-cache"
        cache_dir.mkdir()
        stale_file = cache_dir / "deadbeef1234_oldcommit.json"
        stale_file.write_text(
            json.dumps({"digest": "old", "repo": "/old", "commit": "old"})
        )
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            get_cross_repo_digest(tmp_path)
        assert not stale_file.exists()
        assert len(list(cache_dir.glob("*.json"))) >= 1

    def test_no_git_fallback(self, tmp_path):
        """FileNotFoundError from subprocess must fall back to 'no-git' (H-05)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with patch(
            "code_forge.conventions_resolver.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = get_cross_repo_digest(tmp_path)
        assert isinstance(result, str)
        cache_dir = tmp_path / ".code-forge" / "conventions-cache"
        if cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*.json"))
            assert any("no-git" in f.name for f in cache_files)

    def test_git_timeout_fallback(self, tmp_path):
        """TimeoutExpired from subprocess must fall back to 'no-git' (H-05)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with patch(
            "code_forge.conventions_resolver.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5),
        ):
            result = get_cross_repo_digest(tmp_path)
        assert isinstance(result, str)
        cache_dir = tmp_path / ".code-forge" / "conventions-cache"
        if cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*.json"))
            assert any("no-git" in f.name for f in cache_files)

    def test_empty_digest_filtered(self, tmp_path):
        """Empty digests must not cause extra separators in result (L-R4-08, L-R5-06)."""
        sibling_code = tmp_path / "sibling_code"
        sibling_code.mkdir()
        (sibling_code / "api.py").write_text("def useful_fn(): pass\n")
        sibling_empty = tmp_path / "sibling_empty"
        sibling_empty.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n"
            "  - repo: " + str(sibling_code) + "\n"
            "  - repo: " + str(sibling_empty) + "\n"
        )
        with _mock_git("abc123\n"):
            result = get_cross_repo_digest(tmp_path)
        assert "useful_fn" in result
        assert "\n\n\n" not in result
        # Count non-empty digests; separators must be exactly (non_empty_count - 1)
        sources = resolve_sources(tmp_path)
        non_empty = [s for s in sources if extract_conventions(s)]
        if len(non_empty) > 1:
            assert result.count("\n\n") == len(non_empty) - 1


# ---------------------------------------------------------------------------
# TestIntegration
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_get_digest_includes_cross_repo(self, tmp_path):
        """get_digest must include both same-repo and cross-repo naming (M-03)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "local.py").write_text("def local_fn(): pass\n")
        sibling = tmp_path.parent / "sibling_integ"
        sibling.mkdir(exist_ok=True)
        sibling_src = sibling / "src"
        sibling_src.mkdir(exist_ok=True)
        (sibling_src / "remote.py").write_text("def remote_fn(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            result = get_digest(tmp_path)
        assert "local_fn" in result
        assert "remote_fn" in result
        assert "\n\n" in result

    def test_get_digest_cross_repo_only(self, tmp_path):
        """get_digest must include cross-repo names even when same-repo is empty."""
        sibling = tmp_path.parent / "sibling_only"
        sibling.mkdir(exist_ok=True)
        sibling_src = sibling / "src"
        sibling_src.mkdir(exist_ok=True)
        (sibling_src / "api.py").write_text("def only_remote(): pass\n")
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n"
        )
        with _mock_git("abc123\n"):
            result = get_digest(tmp_path)
        assert "only_remote" in result
        assert "Same-repo naming conventions" not in result
