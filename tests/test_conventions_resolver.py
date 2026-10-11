# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for conventions_resolver.py (D12 Stage 1 + Stage 2).

Covers: resolve_sources (4 sources), extract_conventions (multi-language),
get_cross_repo_digest (direct scans), and get_digest integration (M-03).
"""

from __future__ import annotations

import errno
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge import conventions_resolver as cr
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


@contextmanager
def _mock_git(stdout: str = "abc123\n", returncode: int = 0):
    """Mock a synthetic commit and an already-verified source roster."""
    def source_roster(repo_path, _commit_hash):
        return {
            path.relative_to(repo_path) for path in repo_path.rglob("*")
            if path.is_file() and cr._is_convention_file(path.name)
            and not any(part in cr._SKIP_DIRS for part in path.relative_to(repo_path).parts[:-1])
        }

    with patch(
        "code_forge.conventions_resolver.subprocess.run",
        return_value=_make_proc(stdout, returncode),
    ) as mock_run, patch(
        "code_forge.conventions_resolver._tracked_convention_files", side_effect=source_roster,
    ), patch(
        "code_forge.conventions_resolver._scan_matches_commit", return_value=True,
    ), patch(
        "code_forge.conventions_resolver._source_cache_eligible", return_value=True,
    ):
        yield mock_run


def _multilang_git_fixture(tmp_path):
    cwd = tmp_path / "repo"
    sibling = tmp_path / "sibling"
    outside = tmp_path / "outside"
    (cwd / ".code-forge").mkdir(parents=True)
    selected = sibling / "src" / "sub"
    selected.mkdir(parents=True)
    outside.mkdir()
    (sibling / "src" / "always.py").write_text("def Always(): pass\n")
    for filename, declaration in {
        "one.py": "def PySelected(): pass\n",
        "one.ts": "export function TsSelected() {}\n",
        "one.go": "func GoSelected() {}\n",
        "one.rs": "pub fn RustSelected() {}\n",
    }.items():
        (selected / filename).write_text(declaration)
    (outside / "outside.py").write_text("def OutsideOnly(): pass\n")
    (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
    for command in (
        ["git", "init", "-q", str(sibling)],
        ["git", "-C", str(sibling), "add", "-A"],
        ["git", "-C", str(sibling), "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, capture_output=True, text=True, check=True)
    return cwd, sibling, outside, selected


def _cache_ancestor_fixture(tmp_path):
    """Put a real source beside a consumer under one swappable ancestor."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cwd, sibling, _inside, _selected = _multilang_git_fixture(workspace)
    (cwd / "src").mkdir()
    (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
    external = tmp_path / "external"
    external_cache = external / "repo/.code-forge/conventions-cache"
    external_cache.mkdir(parents=True)
    return cwd, sibling, workspace, external, external_cache


def _assert_real_git_commit(repo, commit):
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-object-format"],
        capture_output=True, text=True, check=True,
    )
    assert len(commit) == {"sha1": 40, "sha256": 64}[result.stdout.strip()]


def _local_cli_diff():
    return (
        "diff --git a/src/local.py b/src/local.py\n"
        "--- a/src/local.py\n"
        "+++ b/src/local.py\n"
        "@@ -0,0 +1 @@\n"
        "+def LocalOnly(): pass\n"
    )


def _run_nonfaulted_probe(run, command, *args, **kwargs):
    """Exercise real Git without requiring the optional no-lazy-fetch switch."""
    if command[:2] == ["git", "--no-lazy-fetch"]:
        command = [command[0], *command[2:]]
    return run(command, *args, **kwargs)


_PYTHON312_LOOP_CLI = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from code_forge import conventions_resolver as cr
from code_forge.cli import _assemble_post_image

case = sys.argv[1]
assert sys.version_info[:2] == (3, 12)
with tempfile.TemporaryDirectory(prefix="forge-convention-loop-") as directory:
    root = Path(directory)
    cwd, sibling, good = root / "consumer", root / "sibling", root / "good"
    (cwd / "src").mkdir(parents=True)
    (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
    (cwd / ".code-forge").mkdir()
    sibling.mkdir()
    good.mkdir()
    (good / "api.py").write_text("def GoodSibling(): pass\n")
    (cwd / ".code-forge/conventions.yaml").write_text(
        "siblings: [../sibling, ../good]\n"
    )
    if case.startswith("static-"):
        (sibling / "other.js").write_text("export function UnrelatedJs() {}\n")
        (sibling / "src").symlink_to(
            "src" if case == "static-self" else "bridge",
            target_is_directory=True,
        )
        if case == "static-two-links":
            (sibling / "bridge").symlink_to("src", target_is_directory=True)
        post, digest = _assemble_post_image(cwd, "")
        assert "LocalOnly" in digest
        assert "GoodSibling" in digest and "UnrelatedJs" in digest
    else:
        filename, declaration = {
            "sibling-ts": ("api.ts", "export function Selected() {}\n"),
            "sibling-go": ("api.go", "func Selected() {}\n"),
            "sibling-rs": ("api.rs", "pub fn Selected() {}\n"),
        }.get(case, ("api.py", "def Selected(): pass\n"))
        selected = sibling / filename
        selected.write_text(declaration)
        if case == "same-python":
            selected = cwd / "src/local.py"
        elif case == "alias-final":
            (sibling / "node_modules").mkdir()
            selected.rename(sibling / "node_modules/api.py")
            selected.symlink_to("node_modules/api.py")
        elif case == "warm-src":
            (sibling / "src").mkdir()
            selected.rename(sibling / "src/api.py")
            selected = sibling / "src"
        elif case == "context-dedup":
            (cwd / ".code-forge/conventions.yaml").unlink()
            (cwd / "CLAUDE.md").write_text(
                "See ../sibling and ../good for names.\n"
            )
        if case.startswith("warm-"):
            for command in (
                ["git", "init", "-q", str(sibling)],
                ["git", "-C", str(sibling), "add", "-A"],
                ["git", "-C", str(sibling), "-c", "user.name=Test",
                 "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
                 "commit", "-qm", "fixture"],
            ):
                subprocess.run(command, capture_output=True, check=True)
            assert "Selected" in _assemble_post_image(cwd, "")[1]

        events = []
        def loop(path):
            path.rename(path.with_name(path.name + ".saved"))
            path.symlink_to(
                path.name,
                target_is_directory=path == sibling or case == "warm-src",
            )
            events.append(str(path))

        realpath = os.path.realpath
        def loop_before_realpath(path, *args, **kwargs):
            if Path(path) == selected and not events:
                loop(selected)
            return realpath(path, *args, **kwargs)

        if case == "alias-final":
            original = cr._scanned_aliases_still_current
            def inject(*args, **kwargs):
                if not events:
                    loop(selected)
                return original(*args, **kwargs)
            manager = patch.object(cr, "_scanned_aliases_still_current", side_effect=inject)
        elif case in ("warm-leaf", "warm-src"):
            original = cr._scan_snapshot_is_current
            def inject(*args, **kwargs):
                if not events:
                    loop(selected)
                return original(*args, **kwargs)
            manager = patch.object(cr, "_scan_snapshot_is_current", side_effect=inject)
        elif case == "resolver-guard":
            original = cr._symlink_guard_passes
            def inject(*args, **kwargs):
                if not events:
                    loop(sibling)
                return original(*args, **kwargs)
            manager = patch.object(cr, "_symlink_guard_passes", side_effect=inject)
        elif case == "resolver-dedup":
            original = cr._resolve_source_custom
            def inject(*args, **kwargs):
                value = original(*args, **kwargs)
                if not events:
                    loop(sibling)
                return value
            manager = patch.object(cr, "_resolve_source_custom", side_effect=inject)
        elif case == "context-dedup":
            original = cr._resolve_paths_as_sources
            def inject(*args, **kwargs):
                value = original(*args, **kwargs)
                if not events and kwargs.get("source_type") == "agent_context":
                    loop(sibling)
                return value
            manager = patch.object(cr, "_resolve_paths_as_sources", side_effect=inject)
        else:
            manager = patch.object(os.path, "realpath", side_effect=loop_before_realpath)
        diff = (
            "diff --git a/src/local.py b/src/local.py\n"
            "--- a/src/local.py\n+++ b/src/local.py\n"
            "@@ -0,0 +1 @@\n+def LocalOnly(): pass\n"
        )
        with manager:
            post, digest = _assemble_post_image(cwd, diff)
        assert len(events) == 1, (case, events)
        assert "LocalOnly" in post and "GoodSibling" in digest, (case, digest)
    print(json.dumps({"case": case, "result": "PASS"}), flush=True)
'''


_PYTHON312_METADATA_CLI = r'''
import json
import os
from pathlib import Path
import sys
import tempfile

from code_forge.cli import _assemble_post_image

case = sys.argv[1]
assert sys.version_info[:2] == (3, 12)
with tempfile.TemporaryDirectory(prefix="forge-optional-metadata-") as directory:
    root = Path(directory)
    cwd, good = root / "consumer", root / "good"
    (cwd / "src").mkdir(parents=True)
    good.mkdir()
    (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
    (good / "api.py").write_text("def GoodSibling(): pass\n")
    (cwd / "AGENTS.md").write_text("---\nrelated: [../good]\n---\n")
    component = {"config": ".code-forge", "context": ".github", "cursor": ".cursor"}[case]
    denied = cwd / component
    denied.mkdir()
    denied.chmod(0)
    try:
        _post, digest = _assemble_post_image(cwd, "")
    finally:
        denied.chmod(0o700)
    assert "LocalOnly" in digest and "GoodSibling" in digest, (case, digest)
    print(json.dumps({"case": case, "result": "PASS"}), flush=True)
'''


def _python312_interpreter():
    """Find an executable for the version-specific path-loop regressions."""
    if sys.version_info[:2] == (3, 12):
        return Path(os.path.abspath(sys.executable))
    candidates = (os.environ.get("FORGE_TEST_PYTHON312"), shutil.which("python3.12"))
    for candidate in candidates:
        if not candidate:
            continue
        executable = shutil.which(candidate) if not os.path.dirname(candidate) else candidate
        if not executable:
            continue
        executable = os.path.abspath(executable)
        try:
            version = subprocess.run(
                [executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if version.returncode == 0 and version.stdout.strip() == "3.12":
            return Path(executable)
    pytest.skip("Python 3.12 interpreter is unavailable")


def _run_python312_loop_cli(case, script=None):
    interpreter = _python312_interpreter()
    if script is None:
        script = _PYTHON312_LOOP_CLI
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(cr.__file__).parents[2] / "src"),
        str(Path(sys.modules["yaml"].__file__).parent.parent),
    ))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(interpreter), "-c", script, case], env=env,
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == "PASS"


def _warm_component_alias_fixture(tmp_path, *, two=False):
    cwd = tmp_path / "consumer"
    sibling = tmp_path / "sibling"
    (cwd / ".code-forge").mkdir(parents=True)
    (cwd / ".code-forge" / "conventions.yaml").write_text(
        "siblings: [../sibling]\n"
    )
    labels = (
        {"build": ("OldA", "NewA"), "dist": ("OldB", "NewB")}
        if two else {"build": ("OriginalName", "RevisedName")}
    )
    for component, (old, new) in labels.items():
        for label in (old, new):
            directory = sibling / "node_modules" / label
            directory.mkdir(parents=True)
            (directory / "api.py").write_text(f"def {label}(): pass\n")
        (sibling / component).symlink_to(
            "node_modules/" + old, target_is_directory=True,
        )
        alias = "alias" + component.title() + ".py" if two else "alias.py"
        (sibling / alias).symlink_to(component + "/api.py")
    (sibling / "other.js").write_text("export function UnrelatedJs() {}\n")
    for command in (
        ["git", "init", "-q", str(sibling)],
        ["git", "-C", str(sibling), "add", "-f", "-A"],
        ["git", "-C", str(sibling), "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "component aliases"],
    ):
        subprocess.run(command, capture_output=True, check=True)
    return cwd, sibling


def _warm_leaf_alias_fixture(tmp_path, *, variant):
    cwd = tmp_path / "consumer"
    sibling = tmp_path / "sibling"
    (cwd / ".code-forge").mkdir(parents=True)
    (cwd / ".code-forge" / "conventions.yaml").write_text(
        "siblings: [../sibling]\n"
    )
    (sibling / "node_modules").mkdir(parents=True)
    for letter in "AB":
        if variant == "leaf_chain":
            for prefix in ("Old", "New"):
                target = sibling / "node_modules" / (prefix + letter) / "api.py"
                target.parent.mkdir()
                target.write_text(f"def {prefix}{letter}(): pass\n")
            (sibling / ("bridge" + letter)).symlink_to(
                "node_modules/Old" + letter + "/api.py"
            )
            alias_target = "bridge" + letter
        else:
            target = sibling / "node_modules" / (letter + ".bin")
            target.write_text(f"def Old{letter}(): pass\n")
            alias_target = "node_modules/" + letter + ".bin"
        (sibling / ("alias" + letter + ".py")).symlink_to(alias_target)
    for command in (
        ["git", "init", "-q", str(sibling)],
        ["git", "-C", str(sibling), "add", "-f", "-A"],
        ["git", "-C", str(sibling), "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "leaf aliases"],
    ):
        subprocess.run(command, capture_output=True, check=True)
    return cwd, sibling


def _two_clean_heads_fixture(tmp_path):
    cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
    a = cr._get_git_commit(sibling)
    (selected / "one.py").write_text("def OnlyOnB(): pass\n")
    for command in (
        ["git", "-C", str(sibling), "add", "src/sub/one.py"],
        ["git", "-C", str(sibling), "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "B"],
    ):
        subprocess.run(command, capture_output=True, text=True, check=True)
    b = cr._get_git_commit(sibling)
    subprocess.run(
        ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
        capture_output=True, text=True, check=True,
    )
    return cwd, sibling, selected, a, b


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
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: /etc/passwd\n")
        result = resolve_sources(tmp_path)
        assert not any(str(s.repo_path) == "/etc/passwd" for s in result)

    def test_custom_mapping_default_target_recipe(self, tmp_path):
        """Entries without target/recipe must get sensible defaults (M-R2-06)."""
        sibling = tmp_path / "sibling_nofields"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
        result = resolve_sources(tmp_path)
        assert len(result) >= 1
        assert result[0].target == "public names"
        assert result[0].recipe == "default"
        assert result[0].priority == 1  # L-R5-05
        assert result[0].source_type == "custom"  # L-R5-05

    def test_agents_md_yaml_frontmatter(self, tmp_path):
        sibling2 = tmp_path / "sibling2"
        sibling2.mkdir()
        (tmp_path / "AGENTS.md").write_text(
            "---\nrelated_repos:\n  - " + str(sibling2) + "\n---\n# Agent docs\n"
        )
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "agents_md" and s.repo_path == sibling2 for s in result)

    def test_agents_md_no_frontmatter_fallback(self, tmp_path):
        """AGENTS.md without '---\\n' header must fall back to regex (L-02)."""
        sibling3 = tmp_path / "sibling3"
        sibling3.mkdir()
        (tmp_path / "AGENTS.md").write_text("# My Agents\nRelated repos:\n- `" + str(sibling3) + "`\n")
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
        (tmp_path / "CLAUDE.md").write_text("# Config\nSee " + str(sibling4) + " for details.\n")
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "agent_context" for s in result)

    def test_agent_context_relative_path(self, tmp_path):
        """CLAUDE.md with ../ relative path must resolve to sibling (H-04)."""
        sibling5_rel = tmp_path.parent / "sibling5_rel"
        sibling5_rel.mkdir(exist_ok=True)
        (tmp_path / "CLAUDE.md").write_text("See ../sibling5_rel for details.\n")
        result = resolve_sources(tmp_path)
        resolved_paths = [s.repo_path for s in result if s.source_type == "agent_context"]
        assert any(str(p).endswith("sibling5_rel") for p in resolved_paths)

    def test_path_re_special_chars(self, tmp_path):
        """_PATH_RE must match paths containing + (L-R2-01 expanded char class)."""
        lib_plus = tmp_path / "lib+extra"
        lib_plus.mkdir()
        (tmp_path / "CLAUDE.md").write_text("See " + str(lib_plus) + " for more.\n")
        result = resolve_sources(tmp_path)
        assert any("lib+extra" in str(s.repo_path) for s in result)

    def test_gitmodules_dependency(self, tmp_path):
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        (tmp_path / ".gitmodules").write_text(
            '[submodule "lib"]\n    path = lib\n    url = https://example.com/lib.git\n'
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
        (tmp_path / "pyproject.toml").write_text('[tool.setuptools]\npath = "' + str(pylib) + '"\n')
        result = resolve_sources(tmp_path)
        assert any(s.source_type == "dependency" and "pylib" in str(s.repo_path) for s in result)

    def test_deduplication_keeps_highest_priority(self, tmp_path):
        """Same sibling in both custom yaml and AGENTS.md -> only 1 entry, priority=1."""
        sibling = tmp_path / "shared"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
        (tmp_path / "AGENTS.md").write_text("---\nrelated_repos:\n  - " + str(sibling) + "\n---\n")
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
        (cursor_rules / "a.mdc").write_text("See " + str(shared_sibling) + " for rules.\n")
        (cursor_rules / "b.mdc").write_text("Also check " + str(shared_sibling) + " always.\n")
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

    def test_bad_utf8_custom_mapping_preserves_local_digest(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "local.py").write_text("def local_name(): pass\n")
        config = repo / ".code-forge"
        config.mkdir()
        (config / "conventions.yaml").write_bytes(b"siblings: \xff\n")

        assert "local_name" in get_digest(repo)

    def test_custom_numeric_repo_is_skipped(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def valid_sibling(): pass\n")
        config = repo / ".code-forge"
        config.mkdir()
        (config / "conventions.yaml").write_text(
            "siblings:\n  - repo: 42\n  - repo: " + str(sibling) + "\n"
        )

        sources = resolve_sources(repo)
        assert len(sources) == 1
        assert sources[0].repo_path == sibling
        with _mock_git():
            assert "valid_sibling" in get_digest(repo)

    @pytest.mark.parametrize("field", ["target", "recipe"])
    def test_custom_numeric_metadata_uses_default(self, tmp_path, field):
        repo = tmp_path / "repo"
        repo.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def valid_sibling(): pass\n")
        config = repo / ".code-forge"
        config.mkdir()
        (config / "conventions.yaml").write_text(
            "siblings:\n  - repo: " + str(sibling) + "\n    " + field + ": 42\n"
        )

        sources = resolve_sources(repo)
        assert len(sources) == 1
        assert sources[0].target == "public names"
        assert sources[0].recipe == "default"
        with _mock_git():
            assert "valid_sibling" in get_digest(repo)

    def test_agents_numeric_repo_is_skipped(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (repo / "AGENTS.md").write_text(
            "---\nrelated:\n  - repo: 42\n  - repo: " + str(sibling) + "\n---\n"
        )

        sources = resolve_sources(repo)
        assert len(sources) == 1
        assert sources[0].repo_path == sibling
        assert sources[0].source_type == "agents_md"

    def test_custom_loader_bug_is_not_swallowed(self, tmp_path, monkeypatch):
        """A bug inside the YAML loader is not turned into an empty source list."""
        import yaml

        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("siblings: []\n")

        def boom(*_args, **_kwargs):
            raise RuntimeError("loader bug")

        monkeypatch.setattr(yaml, "safe_load", boom)
        with pytest.raises(RuntimeError, match="loader bug"):
            resolve_sources(tmp_path)


# ---------------------------------------------------------------------------
# TestExtraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_in_root_src_symlink_keeps_python_names(self, tmp_path):
        actual = tmp_path / "venv"
        actual.mkdir()
        (actual / "api.py").write_text("def linked_src_name(): pass\n")
        (tmp_path / "src").symlink_to(actual, target_is_directory=True)
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        assert "linked_src_name" in extract_conventions(source)

    def test_python_public_names_via_shared_helper(self, tmp_path):
        """extract_conventions delegates to _extract_python_public_names (B-02)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "module.py").write_text("def serve(): pass\nclass Handler: pass\n")
        source = ResolvedSource(
            repo_path=tmp_path,
            priority=1,
            source_type="custom",
            target="public names",
            recipe="default",
        )
        result = extract_conventions(source)
        assert "serve" in result
        assert "Handler" in result

    def test_js_ts_named_exports(self, tmp_path):
        (tmp_path / "index.ts").write_text("export function fetchData() {}\nexport class ApiClient {}\n")
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
        (tmp_path / "main.go").write_text("func ServeHTTP() {}\ntype Handler struct{}\n")
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "ServeHTTP" in result
        assert "Handler" in result

    def test_rust_extraction(self, tmp_path):
        (tmp_path / "lib.rs").write_text("pub fn process() {}\npub struct Config {}\n")
        source = ResolvedSource(repo_path=tmp_path, priority=1, source_type="custom")
        result = extract_conventions(source)
        assert "process" in result
        assert "Config" in result

    def test_empty_repo(self, tmp_path):
        """Empty dir must return empty string."""
        source = ResolvedSource(
            repo_path=tmp_path,
            priority=1,
            source_type="custom",
            recipe="default",
        )
        result = extract_conventions(source)
        assert result == ""

    def test_nonexistent_path(self, tmp_path):
        """Nonexistent path must return empty string without exception."""
        source = ResolvedSource(
            repo_path=Path("/nonexistent/path"),
            priority=1,
            source_type="custom",
        )
        result = extract_conventions(source)
        assert result == ""

    def test_nonexistent_source_marks_scan_incomplete(self, tmp_path):
        from code_forge.conventions import _ScanStatus

        source = ResolvedSource(repo_path=tmp_path / "missing", priority=1, source_type="custom")
        status = _ScanStatus()
        assert extract_conventions(source, scan_status=status) == ""
        assert status.complete is False

    def test_custom_recipe_uses_default_extraction(self, tmp_path):
        """Custom recipe name must appear in header; extraction still works (D17)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "module.py").write_text("def set_flow(): pass\nclass OvsCmd: pass\n")
        source = ResolvedSource(
            repo_path=tmp_path,
            priority=1,
            source_type="custom",
            target="command names",
            recipe="ovs_commands",
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
    @pytest.mark.skipif(not hasattr(os, "O_PATH"), reason="requires Linux rooted size open")
    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def selected_python(): pass\n", "selected_python"),
            (".ts", "export function selected_typescript() {}\n", "selected_typescript"),
            (".go", "func SelectedGo() {}\n", "SelectedGo"),
            (".rs", "pub fn selected_rust() {}\n", "selected_rust"),
        ],
    )
    def test_late_outside_link_at_size_open_retries_same_head(
        self, tmp_path, monkeypatch, extension, declaration, name
    ):
        from code_forge import conventions

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        selected = sibling / ("selected" + extension)
        selected.write_text(declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)

        outside = tmp_path / ("outside" + extension)
        outside.write_text(declaration + " " * 100_001)
        backup = selected.with_suffix(selected.suffix + ".saved")
        real_open = conventions._open_scan_file
        swaps = []

        def replace_at_size_open(path, root, flags, *, scan_root_fd=None, scan_status=None):
            if path == selected and flags & os.O_PATH and not swaps:
                selected.rename(backup)
                selected.symlink_to(outside)
                swaps.append(str(path))
            return real_open(
                path, root, flags, scan_root_fd=scan_root_fd, scan_status=scan_status,
            )

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(conventions, "_open_scan_file", replace_at_size_open)
                partial = get_cross_repo_digest(cwd)
        finally:
            if selected.is_symlink():
                selected.unlink()
            if backup.exists():
                backup.rename(selected)

        assert swaps == [str(selected)]
        assert "ordinary_sibling" in partial and name not in partial
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert name in recovered and cr._get_git_commit(sibling) == commit

    @pytest.mark.parametrize(
        ("extension", "declaration", "name", "walk_index"),
        [
            (".py", "def inside_python(): pass\n", "inside_python", 1),
            (".ts", "export function inside_typescript() {}\n", "inside_typescript", 1),
            (".go", "func InsideGo() {}\n", "InsideGo", 2),
            (".rs", "pub fn inside_rust() {}\n", "inside_rust", 3),
        ],
    )
    def test_transient_root_replacement_during_walk_retries_same_head(
        self, tmp_path, monkeypatch, extension, declaration, name, walk_index,
    ):
        from code_forge import conventions

        work = tmp_path / "work"
        cwd = work / "repo"
        sibling = work / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        outside.mkdir()
        (sibling / ("api" + extension)).write_text(declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        committed_head = cr._get_git_commit(sibling)
        saved = work / "sibling.saved"
        module = conventions if extension == ".py" else cr
        real_walk = module._walk_source
        visits = []
        swaps = []

        def walk_during_replacement(root, scan_root, scan_root_fd, scan_status):
            visits.append(str(root))
            if len(visits) != walk_index:
                yield from real_walk(root, scan_root, scan_root_fd, scan_status)
                return
            sibling.rename(saved)
            outside.rename(sibling)
            try:
                yield from real_walk(root, scan_root, scan_root_fd, scan_status)
            finally:
                sibling.rename(outside)
                saved.rename(sibling)
            swaps.append(str(root))

        with monkeypatch.context() as patcher:
            patcher.setattr(module, "_walk_source", walk_during_replacement)
            first = get_cross_repo_digest(cwd)

        assert len(swaps) == 1
        assert name not in first
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert name in recovered
        assert cr._get_git_commit(sibling) == committed_head

    @pytest.mark.parametrize(
        ("extension", "name"),
        [
            (".py", "PySelected"),
            (".ts", "TsSelected"),
            (".go", "GoSelected"),
            (".rs", "RustSelected"),
        ],
    )
    def test_admitted_child_moved_outside_before_leaf_open_is_rejected(
        self, tmp_path, monkeypatch, extension, name,
    ):
        from code_forge import conventions

        cwd, sibling, outside, selected_dir = _multilang_git_fixture(tmp_path)
        selected = selected_dir / ("one" + extension)
        moved = outside / "moved-sub"
        module = conventions if extension == ".py" else cr
        real_read = module._read_regular_text
        real_open = os.open
        swaps = []

        def move_child_before_leaf(path, flags, *args, **kwargs):
            if path == selected.name and kwargs.get("dir_fd") is not None and not swaps:
                selected_dir.rename(moved)
                swaps.append(1)
            return real_open(path, flags, *args, **kwargs)

        def read_after_child_move(path, *args, **kwargs):
            if path != selected or swaps:
                return real_read(path, *args, **kwargs)
            try:
                with monkeypatch.context() as patcher:
                    patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
                    patcher.setattr(conventions, "_SYSCALL", None)
                    patcher.setattr(os, "open", move_child_before_leaf)
                    return real_read(path, *args, **kwargs)
            finally:
                if moved.exists():
                    moved.rename(selected_dir)

        with monkeypatch.context() as patcher:
            patcher.setattr(module, "_read_regular_text", read_after_child_move)
            first = get_cross_repo_digest(cwd)

        assert swaps == [1]
        assert name not in first
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        assert name in get_cross_repo_digest(cwd)
        assert selected.is_file() and sibling.is_dir()

    @pytest.mark.parametrize(
        ("extension", "inside_declaration", "outside_declaration", "inside_name", "outside_name"),
        [
            (
                ".py", "def inside_python(): pass\n", "def outside_python(): pass\n",
                "inside_python", "outside_python",
            ),
            (
                ".ts", "export function inside_typescript() {}\n",
                "export function outside_typescript() {}\n", "inside_typescript", "outside_typescript",
            ),
            (".go", "func InsideGo() {}\n", "func OutsideGo() {}\n", "InsideGo", "OutsideGo"),
            (
                ".rs", "pub fn inside_rust() {}\n", "pub fn outside_rust() {}\n",
                "inside_rust", "outside_rust",
            ),
        ],
    )
    @pytest.mark.parametrize("mutation", ["replace", "rewrite_restore"])
    def test_moved_root_requires_the_exact_admitted_file_stamp(
        self, tmp_path, monkeypatch, extension, inside_declaration, outside_declaration,
        inside_name, outside_name, mutation,
    ):
        from code_forge import conventions

        work = tmp_path / "work"
        cwd = work / "repo"
        sibling = work / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        outside.mkdir()
        selected = sibling / ("api" + extension)
        selected.write_text(inside_declaration)
        (outside / selected.name).write_text(outside_declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        committed_head = cr._get_git_commit(sibling)
        before = selected.stat()
        saved = work / "sibling.saved"
        module = conventions if extension == ".py" else cr
        real_read = module._read_regular_text
        swaps = []

        def read_after_mutation(path, *args, **kwargs):
            if path != selected or swaps:
                return real_read(path, *args, **kwargs)
            sibling.rename(saved)
            outside.rename(sibling)
            moved_file = saved / selected.name
            original_file = saved / (selected.name + ".original")
            try:
                if mutation == "replace":
                    moved_file.rename(original_file)
                    moved_file.write_text(outside_declaration)
                else:
                    time.sleep(0.002)
                    moved_file.write_text(outside_declaration)
                    moved_file.write_text(inside_declaration)
                    os.utime(moved_file, ns=(before.st_atime_ns, before.st_mtime_ns))
                    assert moved_file.stat().st_ctime_ns != before.st_ctime_ns
                return real_read(path, *args, **kwargs)
            finally:
                if mutation == "replace":
                    moved_file.unlink()
                    original_file.rename(moved_file)
                sibling.rename(outside)
                saved.rename(sibling)
                swaps.append(1)

        with monkeypatch.context() as patcher:
            patcher.setattr(module, "_read_regular_text", read_after_mutation)
            digest = get_cross_repo_digest(cwd)

        assert swaps == [1]
        assert inside_name not in digest and outside_name not in digest
        assert cr._get_git_commit(sibling) == committed_head
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        assert inside_name in get_cross_repo_digest(cwd)

    @pytest.mark.parametrize("failure", ["stat", "realpath", "walk"])
    @pytest.mark.parametrize(
        ("extension", "declaration", "name", "walk_index"),
        [
            (".ts", "export function blocked_sibling() {}\n", "blocked_sibling", 2),
            (".go", "func BlockedSibling() {}\n", "BlockedSibling", 3),
            (".rs", "pub fn blocked_sibling() {}\n", "blocked_sibling", 4),
        ],
    )
    def test_nonpython_discovery_failure_is_not_cached(
        self, tmp_path, monkeypatch, failure, extension, declaration, name, walk_index
    ):
        import os

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        blocked = sibling / ("blocked" + extension)
        blocked.write_text(declaration)
        (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")

        with monkeypatch.context() as patcher, _mock_git("abc123\n"):
            if failure == "stat":
                real_stat = Path.stat

                def fail_stat(path, *args, **kwargs):
                    if path == blocked:
                        raise OSError(errno.EAGAIN, "temporary stat failure")
                    return real_stat(path, *args, **kwargs)

                patcher.setattr(Path, "stat", fail_stat)
            elif failure == "realpath":
                real_realpath = os.path.realpath

                def fail_realpath(path, *args, **kwargs):
                    if str(path) == str(blocked):
                        raise OSError(errno.EAGAIN, "temporary path resolution failure")
                    return real_realpath(path, *args, **kwargs)

                patcher.setattr(os.path, "realpath", fail_realpath)
            else:
                real_fwalk = os.fwalk
                visits = []

                def fail_second_walk(path, *args, **kwargs):
                    visits.append(path)
                    if len(visits) == walk_index:
                        raise OSError(errno.EAGAIN, "temporary directory failure")
                    return real_fwalk(path, *args, **kwargs)

                patcher.setattr(os, "fwalk", fail_second_walk)

            cold = get_cross_repo_digest(cwd)

        assert "ordinary_sibling" in cold
        assert name not in cold
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        with _mock_git("abc123\n"):
            recovered = get_cross_repo_digest(cwd)
        assert "ordinary_sibling" in recovered
        assert name in recovered

    def test_direct_extract_includes_nested_names_without_status(self, tmp_path):
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        result = extract_conventions(resolve_sources(cwd)[0])
        assert all(name in result for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))

    def test_untracked_selected_file_keeps_its_names(self, tmp_path):
        cwd, _sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        (selected / "extra.ts").write_text("export function UntrackedName() {}\n")
        digest = get_cross_repo_digest(cwd)
        assert "UntrackedName" in digest
        assert "PySelected" in digest

    def test_ignored_untracked_source_directory_bypasses_cache(self, tmp_path):
        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (sibling / ".gitignore").write_text("generated/\n")
        subprocess.run(["git", "-C", str(sibling), "add", ".gitignore"], check=True)
        subprocess.run(
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "ignore generated"], check=True,
        )
        first = get_cross_repo_digest(cwd)
        generated = sibling / "generated"
        generated.mkdir()
        (generated / "extra.js").write_text("export function IgnoredName() {}\n")
        assert "IgnoredName" in extract_conventions(resolve_sources(cwd)[0])
        second = get_cross_repo_digest(cwd)
        assert "IgnoredName" in second
        assert "IgnoredName" not in first

    def test_untracked_file_added_after_scan_prevents_cache_write(
        self, tmp_path, monkeypatch
    ):
        cwd, _sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        extra = selected / "extra.js"
        original_roster = cr._tracked_convention_files
        calls = []

        def add_after_roster(*args, **kwargs):
            roster = original_roster(*args, **kwargs)
            extra.write_text("export function LateName() {}\n")
            calls.append(1)
            return roster

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "_tracked_convention_files", add_after_roster)
            first = get_cross_repo_digest(cwd)
        assert calls == [1]
        assert "LateName" not in first
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert "LateName" in get_cross_repo_digest(cwd)

    def test_transient_untracked_scan_names_do_not_poison_clean_cache(
        self, tmp_path, monkeypatch
    ):
        cwd, _sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        extra = selected / "extra.js"
        original_extract = cr.extract_conventions
        calls = []

        def scan_while_extra_exists(*args, **kwargs):
            extra.write_text("export function TransientName() {}\n")
            try:
                result = original_extract(*args, **kwargs)
            finally:
                extra.unlink()
            calls.append(1)
            return result

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "extract_conventions", scan_while_extra_exists)
            first = get_cross_repo_digest(cwd)
        assert calls == [1]
        assert first == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert "TransientName" not in get_cross_repo_digest(cwd)

    def test_clean_head_switch_back_during_scan_cannot_cache_other_content(
        self, tmp_path, monkeypatch
    ):
        cwd, sibling, _selected, a, b = _two_clean_heads_fixture(tmp_path)
        original_extract = cr.extract_conventions
        switches = []

        def scan_other_commit(*args, **kwargs):
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", b],
                capture_output=True, text=True, check=True,
            )
            try:
                result = original_extract(*args, **kwargs)
            finally:
                subprocess.run(
                    ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
                    capture_output=True, text=True, check=True,
                )
            switches.append(1)
            return result

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "extract_conventions", scan_other_commit)
            raced = get_cross_repo_digest(cwd)
        assert switches == [1]
        assert cr._get_git_commit(sibling) == a
        assert raced == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert "PySelected" in recovered and "OnlyOnB" not in recovered

    @pytest.mark.parametrize(
        ("filename", "declaration", "original_name"),
        [
            ("one.py", "def OnlyOnB(): pass\n", "PySelected"),
            ("one.ts", "export function OnlyOnB() {}\n", "TsSelected"),
            ("one.go", "func OnlyOnB() {}\n", "GoSelected"),
            ("one.rs", "pub fn OnlyOnB() {}\n", "RustSelected"),
        ],
    )
    def test_clean_head_switch_back_after_final_walk_cannot_cache_other_content(
        self, tmp_path, monkeypatch, filename, declaration, original_name
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        a = cr._get_git_commit(sibling)
        (selected / filename).write_text(declaration)
        for command in (
            ["git", "-C", str(sibling), "add", "src/sub/" + filename],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "B"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        b = cr._get_git_commit(sibling)
        subprocess.run(
            ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
            capture_output=True, text=True, check=True,
        )
        original_extract = cr.extract_conventions
        original_roster = cr._tracked_convention_files
        switches = []

        def scan_other_commit(*args, **kwargs):
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", b],
                capture_output=True, text=True, check=True,
            )
            switches.append("scan-b")
            return original_extract(*args, **kwargs)

        def restore_after_roster(*args, **kwargs):
            roster = original_roster(*args, **kwargs)
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
                capture_output=True, text=True, check=True,
            )
            switches.append("restore-a")
            return roster

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "extract_conventions", scan_other_commit)
            patcher.setattr(cr, "_tracked_convention_files", restore_after_roster)
            raced = get_cross_repo_digest(cwd)
        assert switches == ["scan-b", "restore-a"]
        assert raced == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert original_name in recovered and "OnlyOnB" not in recovered
        assert "OnlyOnB" not in get_digest(cwd)

    def test_git_ident_worktree_returns_direct_digest_without_cache(self, tmp_path):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        (cwd / ".code-forge" / "conventions.yaml").write_text(
            "siblings: [../sibling]\n"
        )
        sibling.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(sibling)],
            capture_output=True, text=True, check=True,
        )
        (sibling / ".gitattributes").write_text("*.py ident\n")
        source = sibling / "api.py"
        source.write_text("# $Id$\ndef FilteredName(): pass\n")
        for command in (
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "A"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        source.unlink()
        subprocess.run(
            ["git", "-C", str(sibling), "checkout", "-q", "--", "api.py"],
            capture_output=True, text=True, check=True,
        )
        assert "$Id:" in source.read_text()
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout == ""
        first = get_cross_repo_digest(cwd)
        assert "FilteredName" in first
        assert get_cross_repo_digest(cwd) == first
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

        a = cr._get_git_commit(sibling)
        source.write_text("# $Id$\ndef OtherFilteredName(): pass\n")
        for command in (
            ["git", "-C", str(sibling), "add", "api.py"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "B"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        b = cr._get_git_commit(sibling)
        subprocess.run(
            ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
            capture_output=True, text=True, check=True,
        )
        original_extract = cr.extract_conventions
        original_current_reads = cr._scanned_reads_still_current
        switches = []

        def scan_b(*args, **kwargs):
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", b],
                capture_output=True, text=True, check=True,
            )
            switches.append("scan-b")
            return original_extract(*args, **kwargs)

        def restore_after_read(*args, **kwargs):
            current = original_current_reads(*args, **kwargs)
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
                capture_output=True, text=True, check=True,
            )
            switches.append("restore-a-after-read")
            return current

        with patch.object(cr, "extract_conventions", side_effect=scan_b), patch.object(
            cr, "_scanned_reads_still_current", side_effect=restore_after_read,
        ):
            raced = get_cross_repo_digest(cwd)
        assert switches == ["scan-b", "restore-a-after-read"]
        assert raced == ""
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert "FilteredName" in recovered and "OtherFilteredName" not in recovered

    def test_portable_posix_open_rejects_outside_link_without_cache(self, tmp_path, monkeypatch):
        from code_forge import conventions

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        outside = tmp_path / "outside.py"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "inside.py").write_text("def InsideName(): pass\n")
        outside.write_text("def OutsideOnly(): pass\n")
        (sibling / "linked.py").symlink_to(outside)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        head = cr._get_git_commit(sibling)
        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            partial = get_cross_repo_digest(cwd)
            assert "InsideName" in partial
            assert "OutsideOnly" not in partial
            assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
            (sibling / "linked.py").unlink()
            recovered = get_cross_repo_digest(cwd)
            assert "InsideName" in recovered
            # The tracked link is missing at this HEAD, so this scan cannot be cached.
            assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        assert cr._get_git_commit(sibling) == head

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="requires fd-path probe")
    def test_unbound_handle_path_scan_extracts_but_does_not_cache(self, tmp_path, monkeypatch):
        from code_forge import conventions

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "api.py").write_text("def portable_sibling(): pass\n")
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_open_scan_root", lambda _root: None)
            patcher.setattr(cr, "_open_scan_root", lambda _root: None)
            patcher.setattr(conventions, "_path_fallback_supported", lambda: True)
            patcher.setattr(cr, "_path_fallback_supported", lambda: True)
            patcher.setattr(
                conventions, "_opened_file_path",
                lambda fd: Path(os.readlink(f"/proc/self/fd/{fd}")),
            )
            assert "portable_sibling" in get_cross_repo_digest(cwd)
            assert "portable_sibling" in get_cross_repo_digest(cwd)
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("variant", ["leaf_chain", "target_bytes"])
    def test_committed_leaf_alias_is_read_cold_on_repeated_call(self, tmp_path, variant):
        cwd, _sibling = _warm_leaf_alias_fixture(tmp_path, variant=variant)
        direct = extract_conventions(resolve_sources(cwd)[0])
        assert "OldA" in direct and "OldB" in direct
        assert get_cross_repo_digest(cwd) == direct
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == direct
        assert scan.call_count == 1

    def test_component_alias_cold_midread_change_is_not_returned(self, tmp_path):
        from code_forge import conventions

        cwd, sibling = _warm_component_alias_fixture(tmp_path)
        original_read = conventions._read_regular_text
        retargeted = []

        def change_component(path, *args, **kwargs):
            if path == sibling / "node_modules" / "OriginalName" / "api.py" and not retargeted:
                (sibling / "build").unlink()
                (sibling / "build").symlink_to(
                    "node_modules/RevisedName", target_is_directory=True,
                )
                retargeted.append(True)
            return original_read(path, *args, **kwargs)

        with patch.object(conventions, "_read_regular_text", side_effect=change_component):
            raced = get_cross_repo_digest(cwd)
        assert retargeted == [True]
        assert "OriginalName" not in raced
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_component_alias_cold_midread_outside_change_is_not_returned(self, tmp_path):
        from code_forge import conventions

        cwd, sibling = _warm_component_alias_fixture(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "api.py").write_text("def OutsideOnly(): pass\n")
        original_read = conventions._read_regular_text
        retargeted = []

        def change_component(path, *args, **kwargs):
            result = original_read(path, *args, **kwargs)
            if path == sibling / "node_modules" / "OriginalName" / "api.py" and not retargeted:
                (sibling / "build").unlink()
                (sibling / "build").symlink_to(outside, target_is_directory=True)
                retargeted.append(True)
            return result

        with patch.object(conventions, "_read_regular_text", side_effect=change_component):
            raced = get_cross_repo_digest(cwd)
        assert retargeted == [True]
        assert raced == ""
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_component_alias_changed_after_commit_proof_is_not_cached(self, tmp_path):
        cwd, sibling = _warm_component_alias_fixture(tmp_path)
        original_proof = cr._scan_matches_commit
        retargeted = []

        def change_after_proof(repo_path, commit_hash, status):
            proof = original_proof(repo_path, commit_hash, status)
            if proof is True and not retargeted:
                (sibling / "build").unlink()
                (sibling / "build").symlink_to(
                    "node_modules/RevisedName", target_is_directory=True,
                )
                retargeted.append(True)
            return proof

        with patch.object(cr, "_scan_matches_commit", side_effect=change_after_proof):
            raced = get_cross_repo_digest(cwd)
        assert retargeted == [True]
        assert "OriginalName" not in raced
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize(
        "target", ["../outside.py", "missing/api.py", "alias.py", "a/" * 1025 + "api.py"],
    )
    def test_committed_alias_proof_rejects_unbounded_or_missing_path(
        self, tmp_path, target,
    ):
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        (sibling / "api.py").write_text("def SafeName(): pass\n")
        (sibling / "alias.py").symlink_to(target)
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "unsafe committed alias"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        status = cr._ScanStatus()
        status.python_walk_root = Path(".")
        status.candidates[Path("alias.py")] = Path("api.py")
        status.seen_files.add(Path("alias.py"))
        assert cr._scan_matches_commit(sibling, cr._get_git_commit(sibling), status) is None

    def test_committed_src_alias_is_marked_uncacheable_for_warm_reads(self, tmp_path):
        sibling = tmp_path / "sibling"
        (sibling / "package").mkdir(parents=True)
        (sibling / "package" / "api.py").write_text("def SafeName(): pass\n")
        (sibling / "src").symlink_to("package", target_is_directory=True)
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "source alias"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        status = cr._ScanStatus()
        status.python_walk_root = Path("package")
        assert cr._scan_matches_commit(sibling, cr._get_git_commit(sibling), status) is True
        assert status.committed_alias is True

    def test_dangling_committed_src_alias_does_not_drop_unrelated_js(self, tmp_path):
        cwd = tmp_path / "consumer"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        (cwd / ".code-forge" / "conventions.yaml").write_text(
            "siblings: [../sibling]\n"
        )
        sibling.mkdir()
        (sibling / "other.js").write_text("export function UnrelatedJs() {}\n")
        (sibling / "src").symlink_to("missing-directory", target_is_directory=True)
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-f", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "dangling src alias"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        (sibling / "src").unlink()
        (sibling / "src").mkdir()
        digest = get_cross_repo_digest(cwd)
        assert "UnrelatedJs" in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("corruption", ["empty_link", "invalid_intermediate_mode"])
    def test_committed_alias_proof_rejects_malformed_git_object(
        self, tmp_path, corruption,
    ):
        cwd = tmp_path / "consumer"
        (cwd / ".code-forge").mkdir(parents=True)
        (cwd / ".code-forge/conventions.yaml").write_text(
            "siblings: [../sibling]\n"
        )
        sibling = tmp_path / "sibling"
        (sibling / "weird").mkdir(parents=True)
        (sibling / "weird" / "api.py").write_text("def SafeName(): pass\n")
        (sibling / "alias.py").symlink_to("weird/api.py")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "alias proof"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        status = cr._ScanStatus()
        direct = extract_conventions(resolve_sources(cwd)[0], scan_status=status)
        assert status.complete and "SafeName" in direct
        assert status.candidates[Path("alias.py")] == Path("weird/api.py")
        commit = cr._get_git_commit(sibling)
        assert cr._scan_matches_commit(sibling, commit, status) is True
        original_run = subprocess.run
        injected = []

        def malformed_git_object(command, *args, **kwargs):
            if "cat-file" in command and corruption == "empty_link":
                injected.append(corruption)
                return subprocess.CompletedProcess(command, 0, b"", b"")
            result = original_run(command, *args, **kwargs)
            if "ls-tree" in command and corruption == "invalid_intermediate_mode":
                injected.append(corruption)
                blob = original_run(
                    ["git", "-C", str(sibling), "rev-parse", "HEAD:weird/api.py"],
                    capture_output=True, check=True,
                ).stdout.strip()
                result = subprocess.CompletedProcess(
                    command, 0,
                    result.stdout + b"040000 blob " + blob + b" 1\tweird\0",
                    result.stderr,
                )
            return result

        with patch.object(cr.subprocess, "run", side_effect=malformed_git_object):
            assert cr._scan_matches_commit(sibling, commit, status) is None
        assert injected == [corruption]

    @pytest.mark.parametrize(
        "state", [
            "untracked_target", "missing_target", "retargeted_component", "outside_escape",
        ],
    )
    def test_component_symlink_does_not_certify_unsafe_source(
        self, tmp_path, state,
    ):
        cwd = tmp_path / "consumer"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        (sibling / "dist").mkdir(parents=True)
        (cwd / ".code-forge" / "conventions.yaml").write_text(
            "siblings: [../sibling]\n"
        )
        (sibling / "dist" / "anchor.txt").write_text("committed\n")
        (sibling / "api.js").write_text("export function UnrelatedJs() {}\n")
        (sibling / "build").symlink_to("dist", target_is_directory=True)
        if state == "retargeted_component":
            (sibling / "dist" / "api.py").write_text("def CommittedName(): pass\n")
            (sibling / "node_modules").mkdir()
            (sibling / "node_modules" / "api.py").write_text(
                "def ChangedName(): pass\n"
            )
        target = "build/../../outside.py" if state == "outside_escape" else "build/api.py"
        (sibling / "alias.py").symlink_to(target)
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "add", "-f", "dist/anchor.txt"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "alias control"],
        ):
            subprocess.run(command, check=True)
        if state == "untracked_target":
            (sibling / "dist" / "api.py").write_text("def UntrackedName(): pass\n")
        elif state == "retargeted_component":
            (sibling / "build").unlink()
            (sibling / "build").symlink_to("node_modules", target_is_directory=True)
        elif state == "outside_escape":
            (tmp_path / "outside.py").write_text("def OutsideOnly(): pass\n")

        source = cr.resolve_sources(cwd)[0]
        status = cr._ScanStatus()
        direct = extract_conventions(source, scan_status=status)
        commit = cr._get_git_commit(sibling)
        proof = cr._scan_matches_commit(sibling, commit, status)
        digest = get_cross_repo_digest(cwd)
        assert "OutsideOnly" not in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        if state == "untracked_target":
            assert status.complete and "UntrackedName" in direct
            assert proof is None
            assert digest == direct
        elif state == "retargeted_component":
            assert status.complete and "ChangedName" in direct
            assert proof is None
            assert digest == direct
        else:
            assert not status.complete
            assert "UnrelatedJs" in digest
            assert "UntrackedName" not in digest

    @pytest.mark.parametrize("unsafe", ["loop", "escape"])
    def test_unsafe_committed_symlink_chain_keeps_only_direct_names(
        self, tmp_path, unsafe
    ):
        cwd, sibling, outside, selected = _multilang_git_fixture(tmp_path)
        alias = selected / "alias.py"
        alias2 = selected / "alias2.py"
        if unsafe == "loop":
            alias.symlink_to(alias2.name)
            alias2.symlink_to(alias.name)
        else:
            alias.symlink_to("one.py")
            (outside / "outside.py").write_text("def OutsideOnly(): pass\n")
            alias2.symlink_to(outside / "outside.py")
        subprocess.run(["git", "-C", str(sibling), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "unsafe chain"],
            check=True,
        )
        digest = get_cross_repo_digest(cwd)
        assert "PySelected" in digest and "OutsideOnly" not in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_no_git_fallback(self, tmp_path):
        """FileNotFoundError from subprocess must fall back to 'no-git' (H-05)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
        with patch(
            "code_forge.conventions_resolver.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = get_cross_repo_digest(tmp_path)
        assert isinstance(result, str)
        cache_dir = tmp_path / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_git_timeout_fallback(self, tmp_path):
        """TimeoutExpired from subprocess must fall back to 'no-git' (H-05)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        cfg = tmp_path / ".code-forge"
        cfg.mkdir()
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
        with patch(
            "code_forge.conventions_resolver.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5),
        ):
            result = get_cross_repo_digest(tmp_path)
        assert isinstance(result, str)
        cache_dir = tmp_path / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

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
            "siblings:\n  - repo: " + str(sibling_code) + "\n  - repo: " + str(sibling_empty) + "\n"
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


    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def selected_python(): pass\n", "selected_python"),
            (".ts", "export function selected_typescript() {}\n", "selected_typescript"),
            (".go", "func SelectedGo() {}\n", "SelectedGo"),
            (".rs", "pub fn selected_rust() {}\n", "selected_rust"),
        ],
    )
    def test_oversized_outside_symlink_retries_at_same_head(
        self, tmp_path, extension, declaration, name
    ):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        selected = sibling / ("selected" + extension)
        selected.write_text(declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)

        outside_name = "OutsideGo" if extension == ".go" else "outside_name"
        outside = tmp_path / ("outside" + extension)
        outside.write_text(declaration.replace(name, outside_name) + " " * 100_001)
        assert outside.stat().st_size > 100_000
        backup = selected.with_suffix(selected.suffix + ".saved")
        selected.rename(backup)
        selected.symlink_to(outside)
        try:
            partial = get_cross_repo_digest(cwd)
        finally:
            selected.unlink()
            backup.rename(selected)

        assert "ordinary_sibling" in partial
        assert name not in partial and outside_name not in partial
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        recovered = get_cross_repo_digest(cwd)
        assert name in recovered and outside_name not in recovered
        assert cr._get_git_commit(sibling) == commit
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

    @pytest.mark.parametrize(
        ("extension", "inside_declaration", "outside_declaration", "inside_name", "outside_name"),
        [
            (
                ".py", "def inside_python(): pass\n", "def outside_python(): pass\n",
                "inside_python", "outside_python",
            ),
            (
                ".ts", "export function inside_typescript() {}\n",
                "export function outside_typescript() {}\n", "inside_typescript", "outside_typescript",
            ),
            (".go", "func InsideGo() {}\n", "func OutsideGo() {}\n", "InsideGo", "OutsideGo"),
            (
                ".rs", "pub fn inside_rust() {}\n", "pub fn outside_rust() {}\n",
                "inside_rust", "outside_rust",
            ),
        ],
    )
    def test_root_replacement_during_read_is_uncacheable_and_recovers(
        self, tmp_path, monkeypatch, extension, inside_declaration, outside_declaration,
        inside_name, outside_name,
    ):
        from code_forge import conventions

        work = tmp_path / "work"
        cwd = work / "repo"
        sibling = work / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        outside.mkdir()
        selected = sibling / ("api" + extension)
        selected.write_text(inside_declaration)
        (outside / selected.name).write_text(outside_declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        committed_head = cr._get_git_commit(sibling)
        saved = work / "sibling.saved"
        module = conventions if extension == ".py" else cr
        real_read = module._read_regular_text
        real_extract = cr.extract_conventions
        swaps = []
        statuses = []

        def extract_with_status(source, *, scan_status=None):
            statuses.append(scan_status)
            return real_extract(source, scan_status=scan_status)

        def read_during_replacement(path, *args, **kwargs):
            if path == selected and not swaps:
                sibling.rename(saved)
                outside.rename(sibling)
                try:
                    content = real_read(path, *args, **kwargs)
                finally:
                    sibling.rename(outside)
                    saved.rename(sibling)
                swaps.append(str(path))
                return content
            return real_read(path, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(module, "_read_regular_text", read_during_replacement)
            patcher.setattr(cr, "extract_conventions", extract_with_status)
            digest = get_cross_repo_digest(cwd)

        assert swaps == [str(selected)]
        assert len(statuses) == 1 and statuses[0].complete is False
        # A final snapshot may reject the renamed root after its last pass.
        assert not digest or inside_name in digest
        assert outside_name not in digest
        assert cr._get_git_commit(sibling) == committed_head
        cache_files = list((cwd / ".code-forge").glob("conventions-cache-*.json"))
        assert cache_files == []
        recovered = get_cross_repo_digest(cwd)
        assert inside_name in recovered and outside_name not in recovered
        assert recovered == extract_conventions(resolve_sources(cwd)[0])
        cache_files = list((cwd / ".code-forge").glob("conventions-cache-*.json"))
        assert cache_files == []
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == recovered
        assert scan.call_count == 1

    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def inside_python(): pass\n", "inside_python"),
            (".ts", "export function inside_typescript() {}\n", "inside_typescript"),
            (".go", "func InsideGo() {}\n", "InsideGo"),
            (".rs", "pub fn inside_rust() {}\n", "inside_rust"),
        ],
    )
    def test_transient_root_replacement_during_stat_retries_same_head(
        self, tmp_path, monkeypatch, extension, declaration, name,
    ):
        work = tmp_path / "work"
        cwd = work / "repo"
        sibling = work / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        outside.mkdir()
        selected = sibling / ("api" + extension)
        selected.write_text(declaration)
        (outside / selected.name).write_bytes(b"x" * 100_001)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        committed_head = cr._get_git_commit(sibling)
        saved = work / "sibling.saved"
        real_stat = Path.stat
        swaps = []

        def stat_during_replacement(path, *args, **kwargs):
            if path == selected and not swaps:
                sibling.rename(saved)
                outside.rename(sibling)
                try:
                    info = real_stat(path, *args, **kwargs)
                finally:
                    sibling.rename(outside)
                    saved.rename(sibling)
                swaps.append(info.st_ino)
                return info
            return real_stat(path, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(Path, "stat", stat_during_replacement)
            first = get_cross_repo_digest(cwd)
        assert len(swaps) == 1
        assert name not in first
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        recovered = get_cross_repo_digest(cwd)
        assert name in recovered
        assert cr._get_git_commit(sibling) == committed_head
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

    @pytest.mark.parametrize("replacement", ["symlink", "directory"])
    @pytest.mark.parametrize(
        ("extension", "inside_declaration", "outside_declaration", "inside_name", "outside_name"),
        [
            (
                ".py", "def inside_python(): pass\n", "def outside_python(): pass\n",
                "inside_python", "outside_python",
            ),
            (
                ".ts", "export function inside_typescript() {}\n",
                "export function outside_typescript() {}\n", "inside_typescript", "outside_typescript",
            ),
            (".go", "func InsideGo() {}\n", "func OutsideGo() {}\n", "InsideGo", "OutsideGo"),
            (
                ".rs", "pub fn inside_rust() {}\n", "pub fn outside_rust() {}\n",
                "inside_rust", "outside_rust",
            ),
        ],
    )
    def test_source_root_swap_after_head_retries_without_outside_names(
        self, tmp_path, monkeypatch, extension, inside_declaration, outside_declaration,
        inside_name, outside_name, replacement,
    ):
        work = tmp_path / "work"
        cwd = work / "repo"
        sibling = work / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        outside.mkdir()
        (sibling / ("api" + extension)).write_text(inside_declaration)
        (outside / ("api" + extension)).write_text(outside_declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        committed_head = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, committed_head)
        assert work not in outside.parents

        saved = work / "sibling.saved"
        real_get_commit = cr._get_git_commit
        swaps = []

        def swap_after_head(path):
            head = real_get_commit(path)
            if path == sibling and not swaps:
                sibling.rename(saved)
                if replacement == "symlink":
                    sibling.symlink_to(outside, target_is_directory=True)
                else:
                    outside.rename(sibling)
                swaps.append(head)
            return head

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(cr, "_get_git_commit", swap_after_head)
                first = get_cross_repo_digest(cwd)
        finally:
            if replacement == "symlink":
                if sibling.is_symlink():
                    sibling.unlink()
            elif sibling.exists():
                sibling.rename(outside)
            if saved.exists():
                saved.rename(sibling)

        assert swaps == [committed_head]
        assert outside_name not in first
        assert inside_name not in first
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        recovered = get_cross_repo_digest(cwd)
        assert inside_name in recovered
        assert outside_name not in recovered
        assert cr._get_git_commit(sibling) == committed_head
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

        cached_swaps = []

        def swap_cached_after_head(path):
            head = real_get_commit(path)
            if path == sibling and not cached_swaps:
                sibling.rename(saved)
                if replacement == "symlink":
                    sibling.symlink_to(outside, target_is_directory=True)
                else:
                    outside.rename(sibling)
                cached_swaps.append(head)
            return head

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(cr, "_get_git_commit", swap_cached_after_head)
                cached_during_rebind = get_cross_repo_digest(cwd)
        finally:
            if replacement == "symlink":
                if sibling.is_symlink():
                    sibling.unlink()
            elif sibling.exists():
                sibling.rename(outside)
            if saved.exists():
                saved.rename(sibling)
        assert cached_swaps == [committed_head]
        assert cached_during_rebind == ""

        pinned_source = resolve_sources(cwd)[0]
        sibling.rename(saved)
        outside.rename(sibling)
        try:
            status = cr._ScanStatus()
            direct = extract_conventions(pinned_source, scan_status=status)
        finally:
            sibling.rename(outside)
            saved.rename(sibling)
        assert direct == ""
        assert status.complete is False

        # A direct, deliberately selected source outside the sibling parent
        # remains valid for extraction; only a rebind of an admitted source fails.
        outside_source = ResolvedSource(repo_path=outside, priority=1, source_type="custom")
        assert outside_name in extract_conventions(outside_source)

    @pytest.mark.parametrize("partial", [False, True])
    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def blocked_sibling(): pass\n", "blocked_sibling"),
            (".ts", "export function blocked_sibling() {}\n", "blocked_sibling"),
            (".go", "func BlockedSibling() {}\n", "BlockedSibling"),
            (".rs", "pub fn blocked_sibling() {}\n", "blocked_sibling"),
        ],
    )
    def test_transient_source_open_retries_same_commit(
        self, tmp_path, monkeypatch, partial, extension, declaration, name
    ):
        from code_forge import conventions

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        blocked = sibling / ("blocked" + extension)
        blocked.write_text(declaration)
        if partial:
            (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)

        real_open = conventions._open_beneath
        failures = []

        def fail_leaf_once(directory_fd, relative, flags):
            if Path(relative).name == blocked.name and not failures:
                failures.append(str(relative))
                raise OSError(errno.EAGAIN, "transient root resolution race")
            return real_open(directory_fd, relative, flags)

        monkeypatch.setattr(conventions, "_open_beneath", fail_leaf_once)
        cold = get_cross_repo_digest(cwd)
        assert failures == [blocked.name]
        assert name not in cold
        if partial:
            assert "ordinary_sibling" in cold
        else:
            assert cold == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        monkeypatch.setattr(conventions, "_open_beneath", real_open)
        recovered = get_cross_repo_digest(cwd)
        assert name in recovered
        assert cr._get_git_commit(sibling) == commit
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def blocked_sibling(): pass\n", "blocked_sibling"),
            (".ts", "export function blocked_sibling() {}\n", "blocked_sibling"),
            (".go", "func BlockedSibling() {}\n", "BlockedSibling"),
            (".rs", "pub fn blocked_sibling() {}\n", "blocked_sibling"),
        ],
    )
    def test_outside_symlink_swap_retries_same_commit(
        self, tmp_path, monkeypatch, extension, declaration, name
    ):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        blocked = sibling / ("blocked" + extension)
        blocked.write_text(declaration)
        outside_name = "OutsideOnly" if extension == ".go" else "outside_only"
        outside = tmp_path / ("outside" + extension)
        outside.write_text(declaration.replace(name, outside_name))
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)

        original_realpath = os.path.realpath
        backup = blocked.with_suffix(blocked.suffix + ".saved")
        swaps = []

        def swap_at_resolution(path, *args, **kwargs):
            if str(path) == str(blocked) and not swaps:
                blocked.rename(backup)
                blocked.symlink_to(outside)
                swaps.append(str(path))
            return original_realpath(path, *args, **kwargs)

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os.path, "realpath", swap_at_resolution)
                partial = get_cross_repo_digest(cwd)
        finally:
            if blocked.is_symlink():
                blocked.unlink()
            if backup.exists():
                backup.rename(blocked)

        assert swaps == [str(blocked)]
        assert blocked.read_text() == declaration
        assert "ordinary_sibling" in partial
        assert name not in partial
        assert outside_name not in partial
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        recovered = get_cross_repo_digest(cwd)
        assert name in recovered
        assert outside_name not in recovered
        assert cr._get_git_commit(sibling) == commit
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

    @pytest.mark.parametrize("late_skip", [
        pytest.param("nonregular", marks=pytest.mark.skipif(
            not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")),
        "fstat_oversize",
        "read_oversize",
    ])
    @pytest.mark.parametrize(
        ("extension", "declaration", "name"),
        [
            (".py", "def restored_name(): pass\n", "restored_name"),
            (".ts", "export function restored_name() {}\n", "restored_name"),
            (".go", "func RestoredName() {}\n", "RestoredName"),
            (".rs", "pub fn restored_name() {}\n", "restored_name"),
        ],
    )
    def test_late_source_omission_retries_same_commit(
        self, tmp_path, monkeypatch, extension, declaration, name, late_skip
    ):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (sibling / "ordinary.py").write_text("def ordinary_sibling(): pass\n")
        blocked = sibling / ("blocked" + extension)
        blocked.write_text(declaration)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)

        swaps = []
        backup = blocked.with_suffix(blocked.suffix + ".saved")
        original_realpath = os.path.realpath
        original_fstat = os.fstat
        target_inode = blocked.stat().st_ino

        def replace_at_resolution(path, *args, **kwargs):
            if str(path) == str(blocked) and not swaps:
                if late_skip == "nonregular":
                    blocked.rename(backup)
                    os.mkfifo(blocked)
                else:
                    blocked.write_bytes(b"x" * 100_001)
                swaps.append(str(path))
            return original_realpath(path, *args, **kwargs)

        def grow_after_fstat(fd):
            info = original_fstat(fd)
            if info.st_ino == target_inode and not swaps:
                blocked.write_bytes(b"x" * 100_001)
                swaps.append(str(blocked))
            return info

        try:
            with monkeypatch.context() as patcher:
                if late_skip == "read_oversize":
                    patcher.setattr(os, "fstat", grow_after_fstat)
                else:
                    patcher.setattr(os.path, "realpath", replace_at_resolution)
                partial = get_cross_repo_digest(cwd)
        finally:
            if backup.exists():
                blocked.unlink()
                backup.rename(blocked)
            else:
                blocked.write_text(declaration)

        assert swaps == [str(blocked)]
        assert blocked.read_text() == declaration
        assert "ordinary_sibling" in partial
        assert name not in partial
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

        recovered = get_cross_repo_digest(cwd)
        assert name in recovered
        assert cr._get_git_commit(sibling) == commit
        cache_files = list(cache_dir.glob("conventions-cache-*.json"))
        assert cache_files == []

    def test_child_swapped_after_fwalk_listing_retries_same_head(self, tmp_path, monkeypatch):
        cwd, sibling, outside, selected = _multilang_git_fixture(tmp_path)
        saved = tmp_path / "sub.saved"
        original_fwalk = os.fwalk
        swaps = []

        def swap_after_listing(*args, **kwargs):
            for dirpath, dirs, files, fd in original_fwalk(*args, **kwargs):
                if Path(dirpath).name == "src" and "sub" in dirs and not swaps:
                    selected.rename(saved)
                    selected.symlink_to(outside, target_is_directory=True)
                    swaps.append(dirpath)
                yield dirpath, dirs, files, fd

        head = cr._get_git_commit(sibling)
        real_extract = cr.extract_conventions
        statuses = []

        def extract_with_status(source, *, scan_status=None):
            statuses.append(scan_status)
            return real_extract(source, scan_status=scan_status)

        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(os, "fwalk", swap_after_listing)
                patcher.setattr(cr, "extract_conventions", extract_with_status)
                partial = get_cross_repo_digest(cwd)
        finally:
            if selected.is_symlink():
                selected.unlink()
            if saved.exists():
                saved.rename(selected)
        assert len(swaps) == 1
        assert len(statuses) == 1 and statuses[0].complete is False
        # The changed parent cannot prove even the unaffected child's row.
        assert partial == ""
        assert not any(name in partial for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        assert "Always" in recovered and "OutsideOnly" not in recovered
        assert recovered == extract_conventions(resolve_sources(cwd)[0])
        assert cr._get_git_commit(sibling) == head
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == recovered
        assert scan.call_count == 1

    def test_directory_removed_between_language_passes_retries_same_head(
        self, tmp_path, monkeypatch
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        saved = tmp_path / "sub.saved"
        original_js = cr._extract_js_ts_names
        moves = []

        def hide_after_python(*args, **kwargs):
            if not moves:
                selected.rename(saved)
                moves.append(1)
            return original_js(*args, **kwargs)

        head = cr._get_git_commit(sibling)
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(cr, "_extract_js_ts_names", hide_after_python)
                partial = get_cross_repo_digest(cwd)
        finally:
            if saved.exists():
                saved.rename(selected)
        assert moves == [1]
        assert "PySelected" in partial
        assert not any(name in partial for name in (
            "TsSelected", "GoSelected", "RustSelected",
        ))
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        assert cr._get_git_commit(sibling) == head
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_broken_observed_src_symlink_retries_same_head(self, tmp_path, monkeypatch):
        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        src = sibling / "src"
        saved = tmp_path / "src.saved"
        original_python = cr._extract_python_public_names
        moves = []

        def break_src_after_root_open(*args, **kwargs):
            if not moves:
                src.rename(saved)
                src.symlink_to(tmp_path / "missing", target_is_directory=True)
                moves.append(1)
            return original_python(*args, **kwargs)

        head = cr._get_git_commit(sibling)
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(cr, "_extract_python_public_names", break_src_after_root_open)
                partial = get_cross_repo_digest(cwd)
        finally:
            if src.is_symlink():
                src.unlink()
            if saved.exists():
                saved.rename(src)
        assert moves == [1]
        assert partial == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        assert cr._get_git_commit(sibling) == head
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("restore_before_js", [True, False])
    def test_tracked_file_omission_retries_same_head(
        self, tmp_path, monkeypatch, restore_before_js
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        python_file = selected / "one.py"
        hidden = tmp_path / "hidden-one.py"
        original_python = cr._extract_python_public_names
        original_js = cr._extract_js_ts_names
        moves = []

        def hide_before_python(*args, **kwargs):
            python_file.rename(hidden)
            moves.append("hidden")
            return original_python(*args, **kwargs)

        def restore_for_js(*args, **kwargs):
            hidden.rename(python_file)
            moves.append("restored")
            return original_js(*args, **kwargs)

        head = cr._get_git_commit(sibling)
        try:
            with monkeypatch.context() as patcher:
                patcher.setattr(cr, "_extract_python_public_names", hide_before_python)
                if restore_before_js:
                    patcher.setattr(cr, "_extract_js_ts_names", restore_for_js)
                partial = get_cross_repo_digest(cwd)
        finally:
            if hidden.exists():
                hidden.rename(python_file)
        assert moves == (["hidden", "restored"] if restore_before_js else ["hidden"])
        assert "PySelected" not in partial
        assert "TsSelected" in partial
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        assert cr._get_git_commit(sibling) == head
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        ).stdout == ""
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("as_symlink", [False, True])
    def test_untracked_addition_bypasses_existing_cache_until_removed(
        self, tmp_path, as_symlink
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        first = get_cross_repo_digest(cwd)
        head = cr._get_git_commit(sibling)
        extra = selected / "extra.js"
        if as_symlink:
            target = selected / "extra.txt"
            target.write_text("export function NewDirect() {}\n")
            extra.symlink_to(target.name)
        else:
            extra.write_text("export function NewDirect() {}\n")

        assert "NewDirect" in extract_conventions(resolve_sources(cwd)[0])
        second = get_cross_repo_digest(cwd)
        assert "NewDirect" in second
        assert cr._get_git_commit(sibling) == head

        extra.unlink()
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == first
        assert scan.call_count == 1
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_untracked_file_on_cold_scan_is_returned_but_not_cached(self, tmp_path):
        cwd, _sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        extra = selected / "extra.ts"
        extra.write_text("export function UntrackedName() {}\n")
        first = get_cross_repo_digest(cwd)
        assert "UntrackedName" in first
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        extra.unlink()
        second = get_cross_repo_digest(cwd)
        assert "UntrackedName" not in second
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_untracked_outside_symlink_bypasses_cache_without_outside_names(
        self, tmp_path
    ):
        cwd, _sibling, outside, selected = _multilang_git_fixture(tmp_path)
        first = get_cross_repo_digest(cwd)
        outside_file = outside / "extra.js"
        outside_file.write_text("export function OutsideName() {}\n")
        link = selected / "extra.js"
        link.symlink_to(outside_file)
        with patch(
            "code_forge.conventions_resolver.extract_conventions",
            wraps=cr.extract_conventions,
        ) as scan:
            second = get_cross_repo_digest(cwd)
        assert scan.call_count == 1
        assert "OutsideName" not in second
        link.unlink()
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == first
        assert scan.call_count == 1
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_selected_tracked_edit_bypasses_existing_cache(self, tmp_path):
        cwd, _sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        first = get_cross_repo_digest(cwd)
        selected_file = selected / "one.py"
        selected_file.write_text("def ChangedPython(): pass\n")
        second = get_cross_repo_digest(cwd)
        assert "ChangedPython" in second
        assert "ChangedPython" not in first
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize(
        "failure", ["timeout", "nonzero", "text", "malformed", "oversized"]
    )
    def test_source_listing_failure_keeps_digest_uncached(
        self, tmp_path, monkeypatch, failure
    ):
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        original_run = subprocess.run
        failures = []

        def fail_listing(command, *args, **kwargs):
            if "ls-files" in command and "--others" in command:
                failures.append(1)
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 5)
                if failure == "nonzero":
                    return subprocess.CompletedProcess(command, 1, b"", b"error")
                if failure == "text":
                    return subprocess.CompletedProcess(command, 0, "api.py\0", b"")
                raw = (
                    b"api.py" if failure == "malformed"
                    else b"x" * (cr._MAX_SOURCE_STATUS_BYTES + 1)
                )
                return subprocess.CompletedProcess(command, 0, raw, b"")
            return original_run(command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", fail_listing)
            digest = get_cross_repo_digest(cwd)
        assert failures == [1]
        assert "PySelected" in digest
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert get_cross_repo_digest(cwd) == digest
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_clean_head_switch_after_key_cannot_cache_other_commit(
        self, tmp_path, monkeypatch
    ):
        cwd, sibling, _selected, a, b = _two_clean_heads_fixture(tmp_path)
        original_get = cr._get_git_commit
        switches = []

        def switch_after_key(path):
            key = original_get(path)
            if not switches:
                subprocess.run(
                    ["git", "-C", str(sibling), "checkout", "-q", "--detach", b],
                    capture_output=True, text=True, check=True,
                )
                switches.append(key)
            return key

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "_get_git_commit", switch_after_key)
            raced = get_cross_repo_digest(cwd)
        assert switches == [a]
        assert raced == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert cr._get_git_commit(sibling) == b
        subprocess.run(
            ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
            capture_output=True, text=True, check=True,
        )
        recovered = get_cross_repo_digest(cwd)
        assert "PySelected" in recovered and "OnlyOnB" not in recovered
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_clean_head_switch_after_scan_cannot_cache_old_key(
        self, tmp_path, monkeypatch
    ):
        cwd, sibling, _selected, a, b = _two_clean_heads_fixture(tmp_path)
        original_roster = cr._tracked_convention_files
        switches = []

        def switch_after_roster(*args, **kwargs):
            roster = original_roster(*args, **kwargs)
            subprocess.run(
                ["git", "-C", str(sibling), "checkout", "-q", "--detach", b],
                capture_output=True, text=True, check=True,
            )
            switches.append(1)
            return roster

        with monkeypatch.context() as patcher:
            patcher.setattr(cr, "_tracked_convention_files", switch_after_roster)
            raced = get_cross_repo_digest(cwd)
        assert switches == [1]
        assert raced == ""
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert cr._get_git_commit(sibling) == b
        subprocess.run(
            ["git", "-C", str(sibling), "checkout", "-q", "--detach", a],
            capture_output=True, text=True, check=True,
        )
        recovered = get_cross_repo_digest(cwd)
        assert "PySelected" in recovered and "OnlyOnB" not in recovered
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_clean_crlf_worktree_matches_normalized_git_blob(self, tmp_path):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        (cwd / ".code-forge" / "conventions.yaml").write_text(
            "siblings: [../sibling]\n"
        )
        sibling.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(sibling)],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(sibling), "config", "core.autocrlf", "true"],
            capture_output=True, text=True, check=True,
        )
        (sibling / "api.py").write_bytes(b"def NormalizedName(): pass\r\n")
        for command in (
            ["git", "-C", str(sibling), "add", "api.py"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "A"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout == ""
        first = get_cross_repo_digest(cwd)
        assert "NormalizedName" in first
        cache = list((cwd / ".code-forge").glob("conventions-cache-*.json"))
        assert cache == []
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == first
        assert scan.call_count == 1

    def test_tracked_roster_timeout_keeps_digest_but_skips_cache(self, tmp_path, monkeypatch):
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        original_run = subprocess.run
        calls = []

        def timeout_roster(command, *args, **kwargs):
            if "ls-tree" in command:
                calls.append(1)
                raise subprocess.TimeoutExpired(command, 5)
            return original_run(command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", timeout_roster)
            partial = get_cross_repo_digest(cwd)
        assert calls == [1]
        assert all(name in partial for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert get_cross_repo_digest(cwd) == partial
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_bounded_file_roster_skips_cache_without_losing_names(
        self, tmp_path, monkeypatch
    ):
        from code_forge import conventions

        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_MAX_ROSTER_FILES", 1)
            digest = get_cross_repo_digest(cwd)
        assert all(name in digest for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected",
        ))
        cache_dir = cwd / ".code-forge"
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        assert get_cross_repo_digest(cwd) == digest
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_silent_child_directory_swap_cannot_cache_partial_scan(self, tmp_path, monkeypatch):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        selected = sibling / "src" / "sub"
        selected.mkdir(parents=True)
        outside.mkdir()
        (sibling / "src" / "always.py").write_text("def Always(): pass\n")
        for filename, body in {
            "one.py": "def PySelected(): pass\n",
            "one.ts": "export function TsSelected() {}\n",
            "one.go": "func GoSelected() {}\n",
            "one.rs": "pub fn RustSelected() {}\n",
        }.items():
            (selected / filename).write_text(body)
        (outside / "outside.py").write_text("def OutsideOnly(): pass\n")
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        head = cr._get_git_commit(sibling)
        original_open = os.open
        backup = sibling / "src" / "sub.saved"
        swaps = []
        armed = False

        def swap_at_child_open(path, *args, **kwargs):
            if armed and path == "sub" and kwargs.get("dir_fd") is not None:
                selected.rename(backup)
                selected.symlink_to(outside, target_is_directory=True)
                try:
                    fd = original_open(path, *args, **kwargs)
                finally:
                    selected.unlink()
                    backup.rename(selected)
                swaps.append(1)
                return fd
            return original_open(path, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "open", swap_at_child_open)
            patcher.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swap_at_child_open})
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                baseline = get_cross_repo_digest(cwd)
                assert all(name in baseline for name in (
                    "PySelected", "TsSelected", "GoSelected", "RustSelected"
                ))
                assert get_cross_repo_digest(cwd) == baseline
                assert scan.call_count == 2
            cache_dir = cwd / ".code-forge"
            armed = True
            partial = get_cross_repo_digest(cwd)
        assert len(swaps) == 4
        assert "Always" in partial
        assert "OutsideOnly" not in partial
        assert not any(name in partial for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected"
        ))
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert recovered == baseline
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected"
        ))
        assert cr._get_git_commit(sibling) == head
        assert list(cache_dir.glob("conventions-cache-*.json")) == []

    def test_silent_selected_walk_root_swap_cannot_cache_empty_scan(self, tmp_path, monkeypatch):
        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        outside = tmp_path / "outside"
        (cwd / ".code-forge").mkdir(parents=True)
        selected = sibling / "src"
        selected.mkdir(parents=True)
        outside.mkdir()
        for filename, body in {
            "one.py": "def PySelected(): pass\n",
            "one.ts": "export function TsSelected() {}\n",
            "one.go": "func GoSelected() {}\n",
            "one.rs": "pub fn RustSelected() {}\n",
        }.items():
            (selected / filename).write_text(body)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        head = cr._get_git_commit(sibling)
        original_open = os.open
        backup = sibling / "src.saved"
        swaps = []
        armed = False

        def swap_at_root_open(path, *args, **kwargs):
            if armed and path == "src" and kwargs.get("dir_fd") is not None:
                selected.rename(backup)
                selected.symlink_to(outside, target_is_directory=True)
                try:
                    fd = original_open(path, *args, **kwargs)
                finally:
                    selected.unlink()
                    backup.rename(selected)
                swaps.append(1)
                return fd
            return original_open(path, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "open", swap_at_root_open)
            patcher.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swap_at_root_open})
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                baseline = get_cross_repo_digest(cwd)
                assert all(name in baseline for name in (
                    "PySelected", "TsSelected", "GoSelected", "RustSelected"
                ))
                assert get_cross_repo_digest(cwd) == baseline
                assert scan.call_count == 2
            cache_dir = cwd / ".code-forge"
            armed = True
            assert get_cross_repo_digest(cwd) == ""
        assert len(swaps) == 4
        assert list(cache_dir.glob("conventions-cache-*.json")) == []
        recovered = get_cross_repo_digest(cwd)
        assert recovered == baseline
        assert all(name in recovered for name in (
            "PySelected", "TsSelected", "GoSelected", "RustSelected"
        ))
        assert cr._get_git_commit(sibling) == head

    def test_portable_posix_open_preserves_all_languages_and_cache(self, tmp_path, monkeypatch):
        from code_forge import conventions

        cwd = tmp_path / "repo"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        for filename, body in {
            "one.py": "def PySelected(): pass\n",
            "one.ts": "export function TsSelected() {}\n",
            "one.go": "func GoSelected() {}\n",
            "one.rs": "pub fn RustSelected() {}\n",
        }.items():
            (sibling / filename).write_text(body)
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "fixture"],
        ):
            subprocess.run(command, capture_output=True, text=True, check=True)
        head = cr._get_git_commit(sibling)
        with monkeypatch.context() as patcher:
            patcher.setattr(conventions, "_OPENAT2_SYSCALL_NUMBER", None)
            patcher.setattr(conventions, "_SYSCALL", None)
            digest = get_cross_repo_digest(cwd)
            assert all(name in digest for name in (
                "PySelected", "TsSelected", "GoSelected", "RustSelected"
            ))
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                assert get_cross_repo_digest(cwd) == digest
            assert scan.call_count == 1
        assert cr._get_git_commit(sibling) == head
        cache_files = list((cwd / ".code-forge").glob("conventions-cache-*.json"))
        assert cache_files == []

    @pytest.mark.parametrize(
        ("flag", "tag"),
        [("--assume-unchanged", b"h"), ("--skip-worktree", b"S")],
    )
    @pytest.mark.parametrize(
        ("filename", "declaration"),
        [
            ("one.py", "def HiddenName(): pass\n"),
            ("one.ts", "export function HiddenName() {}\n"),
            ("one.go", "func HiddenName() {}\n"),
            ("one.rs", "pub fn HiddenName() {}\n"),
        ],
    )
    def test_hidden_git_index_flag_cannot_reuse_stale_cache(
        self, tmp_path, flag, tag, filename, declaration
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        baseline = get_cross_repo_digest(cwd)
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        source = selected / filename
        original = source.read_text()
        relative = "src/sub/" + filename
        subprocess.run(
            ["git", "-C", str(sibling), "update-index", flag, relative],
            capture_output=True, check=True,
        )
        source.write_text(original + declaration)
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain=v1"],
            capture_output=True, check=True,
        ).stdout == b""
        assert subprocess.run(
            ["git", "-C", str(sibling), "ls-files", "-v", "-z", "--", relative],
            capture_output=True, check=True,
        ).stdout.startswith(tag + b" ")

        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            current = get_cross_repo_digest(cwd)
        assert scan.call_count == 1
        assert "HiddenName" in current and "HiddenName" not in baseline

        source.write_text(original)
        subprocess.run(
            ["git", "-C", str(sibling), "update-index", "--no-" + flag[2:], relative],
            capture_output=True, check=True,
        )
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == baseline
        assert scan.call_count == 1

    @pytest.mark.parametrize(
        "failure", ["timeout", "nonzero", "text", "malformed", "oversized"]
    )
    def test_unverifiable_git_index_forces_direct_scan(
        self, tmp_path, monkeypatch, failure
    ):
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        baseline = get_cross_repo_digest(cwd)
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        original_run = subprocess.run
        inspections = []

        def bad_index_inspection(command, *args, **kwargs):
            if "ls-files" in command and "--cached" in command:
                inspections.append(1)
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 5)
                if failure == "nonzero":
                    return subprocess.CompletedProcess(command, 1, b"", b"")
                if failure == "text":
                    return subprocess.CompletedProcess(command, 0, "H file\0", b"")
                output = (
                    b"H src/sub/one.py" if failure == "malformed"
                    else b"H x\0" * 1_100_000
                )
                return subprocess.CompletedProcess(command, 0, output, b"")
            return original_run(command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", bad_index_inspection)
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                assert get_cross_repo_digest(cwd) == baseline
        assert inspections == [1]
        assert scan.call_count == 1

    @pytest.mark.parametrize(
        ("filename", "added"),
        [
            ("one.py", "def Added(): pass\n"),
            ("one.ts", "export function Added() {}\n"),
            ("one.go", "func Added() {}\n"),
            ("one.rs", "pub fn Added() {}\n"),
        ],
    )
    def test_clean_filter_cannot_hide_changed_source_from_cache(
        self, tmp_path, filename, added
    ):
        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        extension = Path(filename).suffix
        (sibling / ".gitattributes").write_text(
            "*" + extension + " filter=strip_added\n"
        )
        for command in (
            ["git", "-C", str(sibling), "add", ".gitattributes"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "filter"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        baseline = get_cross_repo_digest(cwd)
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        head = cr._get_git_commit(sibling)
        for command in (
            ["git", "-C", str(sibling), "config", "filter.strip_added.clean",
             "sed '/Added/d'"],
            ["git", "-C", str(sibling), "config", "filter.strip_added.smudge", "cat"],
        ):
            subprocess.run(command, capture_output=True, check=True)
        source = selected / filename
        original = source.read_text()
        source.write_text(original + added)
        subprocess.run(
            ["git", "-C", str(sibling), "add", "src/sub/" + filename],
            capture_output=True, check=True,
        )
        assert cr._get_git_commit(sibling) == head
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain=v1"],
            capture_output=True, check=True,
        ).stdout == b""
        assert subprocess.run(
            ["git", "-C", str(sibling), "ls-files", "-v", "--", "src/sub/" + filename],
            capture_output=True, check=True,
        ).stdout.startswith(b"H ")

        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            current = get_cross_repo_digest(cwd)
        assert scan.call_count == 1
        assert "Added" in current and "Added" not in baseline

        source.write_text(original)
        subprocess.run(
            ["git", "-C", str(sibling), "add", "src/sub/" + filename],
            capture_output=True, check=True,
        )
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == baseline
        assert scan.call_count == 1
        subprocess.run(
            ["git", "-C", str(sibling), "config", "--unset", "filter.strip_added.clean"],
            capture_output=True, check=True,
        )
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            assert get_cross_repo_digest(cwd) == baseline
        assert scan.call_count == 1


    def test_component_alias_stable_dirty_target_returns_direct(self, tmp_path):
        cwd, sibling = _warm_component_alias_fixture(tmp_path)
        baseline = get_cross_repo_digest(cwd)
        (sibling / "build").unlink()
        (sibling / "build").symlink_to(
            "node_modules/RevisedName", target_is_directory=True,
        )
        direct = extract_conventions(resolve_sources(cwd)[0])
        assert "OriginalName" in baseline and "RevisedName" in direct
        assert get_cross_repo_digest(cwd) == direct
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_empty_untracked_src_keeps_root_language_digest_uncached(self, tmp_path):
        cwd = tmp_path / "consumer"
        sibling = tmp_path / "sibling"
        (cwd / ".code-forge").mkdir(parents=True)
        sibling.mkdir()
        (cwd / ".code-forge" / "conventions.yaml").write_text("siblings: [../sibling]\n")
        (sibling / "api.js").write_text("export function StillHere() {}\n")
        (sibling / "api.go").write_text("func GoStill() {}\n")
        (sibling / "api.rs").write_text("pub fn RustStill() {}\n")
        for command in (
            ["git", "init", "-q", str(sibling)],
            ["git", "-C", str(sibling), "add", "-A"],
            ["git", "-C", str(sibling), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "root source"],
        ):
            subprocess.run(command, check=True)
        src = sibling / "src"
        src.mkdir()
        assert subprocess.run(
            ["git", "-C", str(sibling), "status", "--porcelain=v1"],
            capture_output=True, check=True,
        ).stdout == b""
        direct = extract_conventions(cr.resolve_sources(cwd)[0])
        assert "StillHere" in direct and "GoStill" in direct and "RustStill" in direct
        assert get_cross_repo_digest(cwd) == direct
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        src.rmdir()
        assert get_cross_repo_digest(cwd) == direct
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_committed_nonempty_src_keeps_python_and_root_js(self, tmp_path):
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        digest = get_cross_repo_digest(cwd)
        assert "PySelected" in digest and "TsSelected" in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []


def test_bad_utf8_package_json_is_skipped(tmp_path):
    repo = tmp_path / "pkg"
    repo.mkdir()
    (tmp_path / "package.json").write_bytes(b'{"dependencies": {"x": "file:pkg"}\xff')
    result = resolve_sources(tmp_path)
    assert result == []


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
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
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
        (cfg / "conventions.yaml").write_text("siblings:\n  - repo: " + str(sibling) + "\n")
        with _mock_git("abc123\n"):
            result = get_digest(tmp_path)
        assert "only_remote" in result
        assert "Same-repo naming conventions" not in result


class TestInputReadSafety:
    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("helper", ["fsmonitor", "clean-filter", "post-index-change"])
    def test_cli_sibling_git_callbacks_are_passive(
        self, tmp_path, helper, object_format
    ):
        from code_forge.cli import _assemble_post_image

        cwd, sibling = tmp_path / "consumer", tmp_path / "sibling"
        (cwd / "src").mkdir(parents=True)
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        (cwd / "AGENTS.md").write_text("---\nrelated: [../sibling]\n---\n")
        sibling.mkdir()
        selected = sibling / "api.py"
        selected.write_text("def OriginalName(): pass\n")
        if helper == "clean-filter":
            (sibling / ".gitattributes").write_text("*.py filter=probe\n")

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(sibling), *args], capture_output=True, check=True,
            ).stdout

        git("init", "-q", "--object-format=" + object_format)
        git("add", "-A")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "fixture")
        _assert_real_git_commit(sibling, cr._get_git_commit(sibling))
        marker = tmp_path / "callback-called"
        callback = "printf called\\n >> " + shlex.quote(str(marker))
        if helper == "fsmonitor":
            executable = tmp_path / "fsmonitor"
            executable.write_text("#!/bin/sh\n" + callback + "\nprintf '\\0'\n")
            executable.chmod(0o700)
            git("config", "core.fsmonitor", str(executable))
        elif helper == "clean-filter":
            git("config", "filter.probe.clean", callback + "; cat")
            selected.write_text("def ReplacedName(): pass\n")
        else:
            executable = sibling / ".git/hooks/post-index-change"
            executable.write_text("#!/bin/sh\n" + callback + "\n")
            executable.chmod(0o700)
            info = selected.stat()
            os.utime(selected, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
        assert not marker.exists()

        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            for _temperature in ("cold", "warm"):
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
                assert "LocalOnly" in digest
                assert ("ReplacedName" if helper == "clean-filter" else "OriginalName") in digest
                assert not marker.exists(), helper
        assert scan.call_count == 2
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize(
        ("scope", "kind"),
        [("local", "clean"), ("global", "clean"), ("includeIf", "clean"),
         ("global", "process"), ("includeIf", "process")],
    )
    def test_cli_effective_filter_configuration_forces_direct_digest(
        self, tmp_path, monkeypatch, scope, kind
    ):
        from code_forge.cli import _assemble_post_image

        cwd, sibling, _outside, selected_dir = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        selected = selected_dir / "one.py"
        (sibling / ".gitattributes").write_text("*.py filter=probe\n")
        marker = tmp_path / "filter-called"
        command = "printf called\\n >> " + shlex.quote(str(marker)) + "; cat"
        if scope == "local":
            subprocess.run(
                ["git", "-C", str(sibling), "config", "filter.probe." + kind, command],
                capture_output=True, check=True,
            )
        else:
            global_config = tmp_path / "global.gitconfig"
            if scope == "global":
                global_config.write_text(
                    '[filter "probe"]\n\t' + kind + " = " + command + "\n"
                )
            else:
                included = tmp_path / "included.gitconfig"
                included.write_text(
                    '[filter "probe"]\n\t' + kind + " = " + command + "\n"
                )
                global_config.write_text(
                    '[includeIf "gitdir:' + str(sibling / ".git") + '"]\n'
                    "\tpath = " + str(included) + "\n"
                )
            monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
        if kind == "clean" and scope != "local":
            selected.write_text("def ReplacedName(): pass\n")

        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            for _temperature in ("cold", "warm"):
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
                assert "LocalOnly" in digest
                assert ("ReplacedName" if kind == "clean" and scope != "local"
                        else "PySelected") in digest
                if kind == "clean" and scope != "local":
                    assert "PySelected" not in digest
        assert scan.call_count == 2
        assert not marker.exists()
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("failure", ["nonzero", "timeout", "text"])
    def test_cli_filter_config_inspection_failure_forces_direct_digest(
        self, tmp_path, monkeypatch, failure
    ):
        from code_forge.cli import _assemble_post_image

        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        original_run = subprocess.run
        inspections = []

        def fail_config(command, *args, **kwargs):
            if "config" in command and "--get-regexp" in command:
                inspections.append(1)
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 5)
                if failure == "text":
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(command, 128, b"", b"error")
            return original_run(command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", fail_config)
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert inspections == [1] and scan.call_count == 1
        assert "LocalOnly" in digest and "PySelected" in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_cli_missing_no_lazy_switch_keeps_direct_digest(self, tmp_path):
        from code_forge.cli import _assemble_post_image

        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        expected_digest = _assemble_post_image(cwd, _local_cli_diff())[1]
        original_run = subprocess.run
        rejected = []

        def reject_switch(command, *args, **kwargs):
            if command[:2] == ["git", "--no-lazy-fetch"]:
                assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
                rejected.append(command)
                return subprocess.CompletedProcess(
                    command, 129, b"", b"unknown option: --no-lazy-fetch\n"
                )
            return original_run(command, *args, **kwargs)

        with patch.object(subprocess, "run", side_effect=reject_switch):
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert len(rejected) == 1 and "ls-files" in rejected[0]
        assert scan.call_count == 1
        assert digest == expected_digest
        assert all(name in digest for name in (
            "LocalOnly", "PySelected", "TsSelected", "GoSelected", "RustSelected"
        ))
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("changed_source", [False, True])
    def test_cli_filter_config_race_cannot_run_callback(
        self, tmp_path, monkeypatch, changed_source
    ):
        from code_forge.cli import _assemble_post_image

        cwd, sibling, _outside, selected_dir = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        (sibling / ".gitattributes").write_text("*.py filter=probe\n")
        if changed_source:
            selected_dir.joinpath("one.py").write_text("def ReplacedName(): pass\n")
        marker = tmp_path / "late-filter-called"
        command = "printf called\\n >> " + shlex.quote(str(marker)) + "; cat"
        original_run = subprocess.run
        switches = []

        def install_after_probe(args, *extra, **kwargs):
            result = original_run(args, *extra, **kwargs)
            if "config" in args and "--get-regexp" in args and not switches:
                original_run(
                    ["git", "-C", str(sibling), "config", "filter.probe.clean", command],
                    capture_output=True, check=True,
                )
                switches.append(1)
            return result

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", install_after_probe)
            _post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert switches == [1] and not marker.exists()
        assert "LocalOnly" in digest
        assert ("ReplacedName" if changed_source else "PySelected") in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("inherited_no_lazy", [None, "0"])
    @pytest.mark.parametrize("object_filter", ["blob:none", "tree:0"])
    def test_cli_partial_clone_missing_object_never_runs_transport(
        self, tmp_path, monkeypatch, object_format, inherited_no_lazy, object_filter
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
        monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
        origin, sibling, cwd = (
            tmp_path / "origin", tmp_path / "sibling", tmp_path / "consumer"
        )
        origin.mkdir()
        cwd.mkdir()

        def git(repo, *args):
            return subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True,
                check=True, timeout=20,
            ).stdout.strip()

        git(origin, "init", "-q", "--object-format=" + object_format)
        (origin / "api.py").write_text("def SiblingOnly(): pass\n")
        (origin / "asset.bin").write_bytes(b"unneeded asset\n")
        git(origin, "add", "-A")
        git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "fixture")
        git(origin, "config", "uploadpack.allowFilter", "true")
        missing_oid = git(origin, "rev-parse", "HEAD:asset.bin" if object_filter == "blob:none"
                          else "HEAD^{tree}")
        api_oid = git(origin, "rev-parse", "HEAD:api.py")
        git(tmp_path, "clone", "-q", "--filter=" + object_filter, "--no-checkout",
            origin.as_uri(), str(sibling))
        if object_filter == "blob:none":
            git(sibling, "checkout", "HEAD", "--", "api.py")
        else:
            (sibling / "api.py").write_text("def SiblingOnly(): pass\n")
            git(sibling, "update-index", "--add", "--cacheinfo",
                "100644," + api_oid.decode() + ",api.py")
        present = git(
            sibling, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"
        )
        assert missing_oid not in present.splitlines()
        assert git(sibling, "config", "--get", "remote.origin.promisor") == b"true"
        _assert_real_git_commit(sibling, cr._get_git_commit(sibling))

        (cwd / "local.py").write_text("def LocalOnly(): pass\n")
        (cwd / "AGENTS.md").write_text("---\nrelated: [../sibling]\n---\n")
        marker = tmp_path / "transport-called"
        git(sibling, "config", "remote.origin.url", "ssh://not-used.invalid/repo")
        git(sibling, "config", "core.sshCommand",
            "printf called\\n >> " + shlex.quote(str(marker)) + "; exit 1")
        if inherited_no_lazy is None:
            monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
        else:
            monkeypatch.setenv("GIT_NO_LAZY_FETCH", inherited_no_lazy)

        for _temperature in ("cold", "second"):
            _post, digest = _assemble_post_image(cwd, "")
            assert "LocalOnly" in digest and "SiblingOnly" in digest
            assert not marker.exists()
            assert missing_oid not in git(
                sibling, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"
            ).splitlines()
            assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        assert os.environ.get("GIT_NO_LAZY_FETCH") == inherited_no_lazy

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("inherited_no_lazy", [None, "0"])
    def test_cli_sparse_index_missing_tree_never_runs_transport(
        self, tmp_path, monkeypatch, object_format, inherited_no_lazy
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
        monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
        origin, sibling, cwd = (
            tmp_path / "origin", tmp_path / "sibling", tmp_path / "consumer"
        )
        origin.mkdir()
        cwd.mkdir()

        def git(repo, *args):
            return subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True,
                check=True, timeout=20,
            ).stdout.strip()

        git(origin, "init", "-q", "--object-format=" + object_format)
        (origin / "src").mkdir()
        (origin / "src/one.py").write_text("def SiblingOnly(): pass\n")
        (origin / "outside/deep").mkdir(parents=True)
        (origin / "outside/deep/asset.bin").write_bytes(b"unused\n")
        git(origin, "add", "-A")
        git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "fixture")
        git(origin, "config", "uploadpack.allowFilter", "true")
        missing_tree = git(origin, "rev-parse", "HEAD:outside")
        git(tmp_path, "clone", "-q", "--filter=tree:0", "--no-checkout",
            origin.as_uri(), str(sibling))
        git(sibling, "sparse-checkout", "init", "--cone", "--sparse-index")
        git(sibling, "sparse-checkout", "set", "src")
        git(sibling, "checkout", "HEAD")
        assert git(sibling, "ls-files", "--sparse", "-v").splitlines() == [
            b"S outside/", b"H src/one.py",
        ]

        # Checkout materializes the promised tree. Keep every other object
        # locally and remove only that tree from this disposable partial clone.
        objects = git(
            sibling, "cat-file", "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ).splitlines()
        assert any(record.startswith(missing_tree + b" ") for record in objects)
        for record in objects:
            oid, kind = record.split()
            if oid == missing_tree:
                continue
            content = subprocess.run(
                ["git", "-C", str(sibling), "cat-file", kind.decode(), oid.decode()],
                capture_output=True, check=True, timeout=20,
            ).stdout
            written = subprocess.run(
                ["git", "-C", str(sibling), "hash-object", "-w", "--stdin",
                 "-t", kind.decode()],
                input=content, capture_output=True, check=True, timeout=20,
            ).stdout.strip()
            assert written == oid
        for pack_file in (sibling / ".git/objects/pack").iterdir():
            pack_file.unlink()

        def tree_is_missing():
            local_objects = git(
                sibling, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"
            )
            return missing_tree not in local_objects.splitlines()

        assert tree_is_missing()
        assert git(sibling, "config", "--get", "remote.origin.promisor") == b"true"
        (cwd / "local.py").write_text("def LocalOnly(): pass\n")
        (cwd / "AGENTS.md").write_text("---\nrelated: [../sibling]\n---\n")
        marker = tmp_path / "transport-called"
        git(sibling, "config", "remote.origin.url", "ssh://not-used.invalid/repo")
        git(sibling, "config", "core.sshCommand",
            "printf called\\n >> " + shlex.quote(str(marker)) + "; exit 1")
        if inherited_no_lazy is None:
            monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
        else:
            monkeypatch.setenv("GIT_NO_LAZY_FETCH", inherited_no_lazy)

        original_run = subprocess.run
        listings = []

        def watch_listing(command, *args, **kwargs):
            if "ls-files" in command:
                listings.append((command, kwargs.get("env")))
            return original_run(command, *args, **kwargs)

        with patch.object(subprocess, "run", side_effect=watch_listing):
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                for _temperature in ("cold", "second"):
                    _post, digest = _assemble_post_image(cwd, "")
                    assert "LocalOnly" in digest and "SiblingOnly" in digest
                    assert not marker.exists()
                    assert tree_is_missing()
                    assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []
        assert scan.call_count == 2
        assert any("--cached" in command for command, _env in listings)
        assert any("--ignored" in command for command, _env in listings)
        assert any("--others" in command and "--ignored" not in command
                   for command, _env in listings)
        assert all(command[:2] == ["git", "--no-lazy-fetch"]
                   and env["GIT_NO_LAZY_FETCH"] == "1"
                   for command, env in listings)
        assert os.environ.get("GIT_NO_LAZY_FETCH") == inherited_no_lazy

    @pytest.mark.parametrize("listing", ["others", "ignored", "cached"])
    def test_cli_unsupported_passive_listing_keeps_direct_digest(
        self, tmp_path, monkeypatch, listing
    ):
        from code_forge.cli import _assemble_post_image

        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        original_run = subprocess.run
        rejected = []

        def reject_listing(command, *args, **kwargs):
            if "ls-files" in command:
                assert command[:2] == ["git", "--no-lazy-fetch"]
                assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
                assert "core.fsmonitor=false" in command
                assert f"core.hooksPath={os.devnull}" in command
                kind = ("cached" if "--cached" in command else
                        "ignored" if "--ignored" in command else "others")
                if kind == listing:
                    rejected.append(kind)
                    return subprocess.CompletedProcess(
                        command, 129, b"", b"unsupported --no-lazy-fetch"
                    )
            return _run_nonfaulted_probe(original_run, command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", reject_listing)
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert rejected == [listing] and scan.call_count == 1
        assert "LocalOnly" in digest and "PySelected" in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("probe", ["roster", "sizes", "symlink"])
    def test_cli_unavailable_object_probe_keeps_direct_digest(
        self, tmp_path, monkeypatch, probe
    ):
        from code_forge.cli import _assemble_post_image

        cwd, sibling, _outside, selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        if probe == "symlink":
            (selected / "alias.py").symlink_to("one.py")
            for args in (
                ["git", "-C", str(sibling), "add", "src/sub/alias.py"],
                ["git", "-C", str(sibling), "-c", "user.name=Test",
                 "-c", "user.email=test@example.invalid",
                 "-c", "core.hooksPath=" + os.devnull,
                 "commit", "-qm", "alias"],
            ):
                subprocess.run(args, capture_output=True, check=True)
        original_run = subprocess.run
        failed = []

        def reject_object_probe(command, *args, **kwargs):
            if command[:2] == ["git", "--no-lazy-fetch"]:
                assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
                match = (
                    probe == "roster" and "ls-tree" in command and "--name-only" in command
                    or probe == "sizes" and "ls-tree" in command and "-l" in command
                    or probe == "symlink" and "cat-file" in command
                )
                if match:
                    failed.append(probe)
                    return subprocess.CompletedProcess(
                        command, 129, b"", b"unsupported --no-lazy-fetch"
                    )
            return _run_nonfaulted_probe(original_run, command, *args, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(subprocess, "run", reject_object_probe)
            with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
                _post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert failed == [probe] and scan.call_count == 1
        assert "LocalOnly" in digest and "PySelected" in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    def test_cli_symlink_blob_disappearing_after_tree_probe_stays_passive(
        self, tmp_path, monkeypatch, object_format
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
        monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
        monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
        origin, sibling, cwd = (
            tmp_path / "origin", tmp_path / "sibling", tmp_path / "consumer"
        )
        origin.mkdir()
        cwd.mkdir()

        def git(repo, *args, input=None):
            return subprocess.run(
                ["git", "-C", str(repo), *args], input=input, capture_output=True,
                check=True, timeout=20,
            ).stdout.strip()

        git(origin, "init", "-q", "--object-format=" + object_format)
        (origin / "api.py").write_text("def SiblingOnly(): pass\n")
        (origin / "alias.py").symlink_to("api.py")
        git(origin, "add", "-A")
        git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "core.hooksPath=" + os.devnull, "commit", "-qm", "fixture")
        git(origin, "config", "uploadpack.allowFilter", "true")
        alias_oid = git(origin, "rev-parse", "HEAD:alias.py")
        git(tmp_path, "clone", "-q", "--filter=blob:none", "--no-checkout",
            origin.as_uri(), str(sibling))
        git(sibling, "checkout", "HEAD", "--", "api.py")
        (sibling / "alias.py").symlink_to("api.py")
        git(sibling, "update-index", "--add", "--cacheinfo",
            "120000," + alias_oid.decode() + ",alias.py")
        present = git(sibling, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
        assert alias_oid not in present.splitlines()
        assert git(sibling, "hash-object", "-w", "--stdin", input=b"api.py") == alias_oid
        loose = sibling / ".git/objects" / alias_oid[:2].decode() / alias_oid[2:].decode()
        assert loose.is_file()

        (cwd / "local.py").write_text("def LocalOnly(): pass\n")
        (cwd / "AGENTS.md").write_text("---\nrelated: [../sibling]\n---\n")
        marker = tmp_path / "transport-called"
        git(sibling, "config", "remote.origin.url", "ssh://not-used.invalid/repo")
        git(sibling, "config", "core.sshCommand",
            "printf called\\n >> " + shlex.quote(str(marker)) + "; exit 1")
        original_run = subprocess.run
        evictions = []

        def evict_after_tree(command, *args, **kwargs):
            result = original_run(command, *args, **kwargs)
            if "ls-tree" in command and "-l" in command and not evictions:
                loose.unlink()
                evictions.append(1)
            return result

        with patch.object(subprocess, "run", side_effect=evict_after_tree):
            _post, digest = _assemble_post_image(cwd, "")
        assert evictions == [1] and not loose.exists()
        assert "LocalOnly" in digest and "SiblingOnly" in digest
        assert not marker.exists()
        assert alias_oid not in git(
            sibling, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"
        ).splitlines()
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    def test_cli_unavailable_root_alias_omits_cross_repo_metadata(self, tmp_path):
        from code_forge.cli import _assemble_post_image

        cwd, _sibling, _workspace, _external, _external_cache = (
            _cache_ancestor_fixture(tmp_path)
        )
        with patch.object(cr, "_consumer_root_alias", return_value=None):
            post, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert "LocalOnly" in post and "LocalOnly" in digest
        assert "PySelected" not in digest
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("stage", ["before_pin", "after_pin"])
    @pytest.mark.parametrize("metadata", ["config", "agents"])
    def test_cli_consumer_ancestor_cannot_switch_metadata(
        self, tmp_path, stage, metadata,
    ):
        from code_forge.cli import _assemble_post_image

        cwd, _sibling, workspace, external, external_cache = (
            _cache_ancestor_fixture(tmp_path)
        )
        poison = external / "poison/src"
        poison.mkdir(parents=True)
        (poison / "api.py").write_text("def OutsideOnly(): pass\n")
        external_repo = external / "repo"
        if metadata == "config":
            (external_repo / ".code-forge/conventions.yaml").write_text(
                "siblings: [../poison]\n",
            )
        else:
            (external_repo / "AGENTS.md").write_text(
                "---\nrelated: [../poison]\n---\n",
            )
        sentinel = external_cache / ("deadbeef1234_" + "a" * 40 + ".json")
        sentinel.write_text('{"belongs_to": "external"}\n')
        _post, warm_digest = _assemble_post_image(cwd, _local_cli_diff())
        assert "PySelected" in warm_digest
        cache_dir = cwd / ".code-forge"
        before = {
            path.name: path.read_bytes()
            for path in cache_dir.glob("conventions-cache-*.json")
        }
        assert before == {}
        original_pin = cr._pin_consumer_root
        saved = tmp_path / "saved-workspace"
        pin_results = []

        def swap_at_pin(root):
            pinned = original_pin(root) if stage == "after_pin" else None
            workspace.rename(saved)
            workspace.symlink_to(external, target_is_directory=True)
            if stage == "before_pin":
                pinned = original_pin(root)
            pin_results.append(pinned is None)
            return pinned

        try:
            with patch.object(cr, "_pin_consumer_root", side_effect=swap_at_pin):
                post, digest = _assemble_post_image(cwd, _local_cli_diff())
        finally:
            workspace.unlink()
            saved.rename(workspace)

        assert pin_results == [stage == "before_pin"]
        assert "LocalOnly" in post and "LocalOnly" in digest
        assert "OutsideOnly" not in digest
        assert sentinel.read_text() == '{"belongs_to": "external"}\n'
        assert {path.name for path in external_cache.iterdir()} == {sentinel.name}
        if stage == "before_pin":
            assert "PySelected" not in digest
            assert {
                path.name: path.read_bytes()
                for path in cache_dir.glob("conventions-cache-*.json")
            } == before
        else:
            assert "PySelected" in digest
            assert list(cache_dir.glob("conventions-cache-*.json")) == []

    @pytest.mark.skipif(os.name != "posix", reason="requires POSIX byte-name paths")
    @pytest.mark.parametrize("kind", [
        "custom-relative", "custom-absolute", "agents", "package-file", "parent-custom",
    ])
    def test_cli_byte_name_path_keeps_later_sibling(self, tmp_path, kind):
        from code_forge.cli import _assemble_post_image

        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        byte_parent = tmp_path / os.fsdecode(b"byte\xff")
        byte_repo = byte_parent / "repo" if kind == "parent-custom" else byte_parent
        byte_repo.mkdir(parents=True)
        (byte_repo / "api.py").write_text("def ByteName(): pass\n")
        for command in (
            ["git", "init", "-q", str(byte_repo)],
            ["git", "-C", str(byte_repo), "add", "-A"],
            ["git", "-C", str(byte_repo), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", "byte name"],
        ):
            subprocess.run(command, capture_output=True, check=True)

        bad = str(byte_repo) if kind == "custom-absolute" else "../" + str(
            byte_repo.relative_to(tmp_path)
        )
        if kind in {"custom-relative", "custom-absolute", "parent-custom"}:
            (cwd / ".code-forge/conventions.yaml").write_text(
                json.dumps({"siblings": [bad, "../sibling"]})
            )
        else:
            (cwd / ".code-forge/conventions.yaml").unlink()
            if kind == "agents":
                (cwd / "AGENTS.md").write_text(
                    "---\n" + json.dumps({"related": [bad, "../sibling"]}) + "\n---\n"
                )
            else:
                (cwd / "package.json").write_text(json.dumps({"dependencies": {
                    "byte": "file:" + bad, "later": "file:../sibling",
                }}))

        post_image, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert "LocalOnly" in post_image and "LocalOnly" in digest
        assert "ByteName" in digest and "PySelected" in digest
        assert _assemble_post_image(cwd, _local_cli_diff()) == (post_image, digest)
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.parametrize("kind", ["custom-self", "agents-two-link", "package-self"])
    def test_python312_cli_loop_keeps_later_sibling(self, tmp_path, kind):
        from code_forge import conventions_resolver

        interpreter = _python312_interpreter()
        cwd, _sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        loop = tmp_path / "loop"
        if kind == "agents-two-link":
            (tmp_path / "other-loop").symlink_to(loop.name)
            loop.symlink_to("other-loop")
        else:
            loop.symlink_to(loop.name)
        if kind == "custom-self":
            (cwd / ".code-forge/conventions.yaml").write_text(
                "siblings: [../loop, ../sibling]\n"
            )
        else:
            (cwd / ".code-forge/conventions.yaml").unlink()
            if kind == "agents-two-link":
                (cwd / "AGENTS.md").write_text("---\nrelated: [../loop, ../sibling]\n---\n")
            else:
                (cwd / "package.json").write_text(json.dumps({"dependencies": {
                    "loop": "file:../loop", "later": "file:../sibling",
                }}))
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((
            str(Path(conventions_resolver.__file__).parents[2] / "src"),
            str(Path(sys.modules["yaml"].__file__).parent.parent),
        ))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "from pathlib import Path; from code_forge.cli import _assemble_post_image; "
            "post,digest = _assemble_post_image(Path(__import__('sys').argv[1]), "
            "'diff --git a/src/local.py b/src/local.py\\n--- a/src/local.py\\n' "
            "+ '+++ b/src/local.py\\n@@ -0,0 +1 @@\\n+def LocalOnly(): pass\\n'); "
            "assert 'LocalOnly' in post and 'LocalOnly' in digest and 'PySelected' in digest"
        )
        result = subprocess.run(
            [str(interpreter), "-c", script, str(cwd)], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("case", ["static-self", "static-two-links"])
    def test_python312_cli_sibling_src_loop_keeps_other_languages(self, case):
        _run_python312_loop_cli(case)

    @pytest.mark.parametrize("case", [
        "same-python", "sibling-python", "sibling-ts", "sibling-go",
        "sibling-rs", "alias-final", "warm-leaf", "warm-src",
        "resolver-guard", "resolver-dedup", "context-dedup",
    ])
    def test_python312_cli_path_switch_keeps_later_sibling(self, case):
        _run_python312_loop_cli(case)

    def test_python312_cli_runs_without_path_is_file_probe(self, monkeypatch):
        _python312_interpreter()
        monkeypatch.setattr(Path, "is_file", lambda _path: False)
        try:
            _run_python312_loop_cli("static-self")
        except pytest.skip.Exception:
            pytest.fail("A usable Python 3.12 must not be skipped by a file probe")

    def test_python312_resolver_loop_runs_without_path_is_file_probe(
        self, tmp_path, monkeypatch,
    ):
        _python312_interpreter()
        monkeypatch.setattr(Path, "is_file", lambda _path: False)
        try:
            self.test_python312_cli_loop_keeps_later_sibling(tmp_path, "custom-self")
        except pytest.skip.Exception:
            pytest.fail("A usable Python 3.12 must not be skipped by a file probe")

    def test_python312_selector_prefers_running_interpreter(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 12))
        monkeypatch.setattr(sys, "executable", "/running/python")
        with patch.object(subprocess, "run", side_effect=AssertionError("unexpected probe")):
            assert _python312_interpreter() == Path("/running/python")

    def test_python312_selector_absolutizes_running_interpreter(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 12))
        monkeypatch.setattr(sys, "executable", "../python3.12")
        with patch.object(subprocess, "run", side_effect=AssertionError("unexpected probe")):
            assert _python312_interpreter() == Path(os.path.abspath("../python3.12"))

    def test_python312_selector_validates_config_then_path(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 14))
        monkeypatch.setenv("FORGE_TEST_PYTHON312", "/configured/python")
        with patch.object(shutil, "which", return_value="/path/python3.12"), patch.object(
            subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout="3.14\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="3.12\n", stderr=""),
            ],
        ) as probe:
            assert _python312_interpreter() == Path("/path/python3.12")
        assert [call.args[0][0] for call in probe.call_args_list] == [
            "/configured/python", "/path/python3.12",
        ]

    def test_python312_selector_missing_command_uses_path(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 14))
        monkeypatch.setenv("FORGE_TEST_PYTHON312", "missing-python")
        with patch.object(shutil, "which", side_effect=lambda name: (
            None if name == "missing-python" else "/path/python3.12"
        )), patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, stdout="3.12\n", stderr=""
        )) as probe:
            assert _python312_interpreter() == Path("/path/python3.12")
        assert probe.call_count == 1

    def test_python312_selector_skips_if_genuinely_unavailable(self, monkeypatch):
        monkeypatch.setattr(sys, "version_info", (3, 14))
        monkeypatch.delenv("FORGE_TEST_PYTHON312", raising=False)
        with patch.object(shutil, "which", return_value=None), pytest.raises(pytest.skip.Exception):
            _python312_interpreter()

    @pytest.mark.skipif(os.name != "posix", reason="requires a relative PATH symlink")
    @pytest.mark.parametrize("source", ["configured", "path", "configured-command"])
    def test_python312_relative_interpreter_survives_cli_cwd(
        self, tmp_path, monkeypatch, source
    ):
        actual = _python312_interpreter()
        if source == "configured":
            relative = os.path.relpath(actual, Path.cwd())
            monkeypatch.setenv("FORGE_TEST_PYTHON312", relative)
            expected = Path(os.path.abspath(relative))
        else:
            executable = tmp_path / "bin" / (
                "python3.12" if source == "path" else "custom-python312"
            )
            executable.parent.mkdir()
            executable.symlink_to(actual)
            relative_dir = os.path.relpath(executable.parent, Path.cwd())
            monkeypatch.setenv("PATH", relative_dir + os.pathsep + os.environ["PATH"])
            if source == "path":
                monkeypatch.delenv("FORGE_TEST_PYTHON312", raising=False)
            else:
                monkeypatch.setenv("FORGE_TEST_PYTHON312", executable.name)
            expected = executable
        with patch.object(sys, "version_info", (3, 14)):
            selected = _python312_interpreter()
        assert selected == expected and selected.is_absolute()
        with patch(f"{__name__}._python312_interpreter", return_value=selected):
            self.test_python312_cli_loop_keeps_later_sibling(tmp_path, "custom-self")

    @pytest.mark.parametrize("case", ["config", "context", "cursor"])
    def test_python312_cli_unreadable_optional_metadata_keeps_context(self, case):
        if not hasattr(os, "geteuid") or os.geteuid() == 0:
            pytest.skip("requires unprivileged POSIX permissions")
        _run_python312_loop_cli(case, _PYTHON312_METADATA_CLI)

    @pytest.mark.parametrize("kind", [
        "custom-nul", "custom-absolute-nul", "agents-nul",
        "package-file-nul", "package-relative-nul", "gitmodules-nul",
        "pyproject-nul", "gomod-nul", "cargo-nul",
        "custom-surrogate", "agents-surrogate", "package-file-surrogate",
    ])
    def test_invalid_repo_path_keeps_cli_conventions(self, tmp_path, kind):
        from code_forge.cli import _assemble_post_image

        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        local = cwd / "src" / "local.py"
        local.parent.mkdir()
        local.write_text("def LocalOnly(): pass\n")
        post_image, baseline = _assemble_post_image(cwd, _local_cli_diff())
        assert "LocalOnly" in post_image
        assert "LocalOnly" in baseline and "PySelected" in baseline

        bad = ("/bad" if kind == "custom-absolute-nul" else "../bad") + (
            "\ud800" if kind.endswith("surrogate") else "\0"
        )
        source = kind.split("-")[0]
        if source == "custom":
            target = cwd / ".code-forge" / "conventions.yaml"
            content = json.dumps({"siblings": [bad, "../sibling"]})
        else:
            (cwd / ".code-forge" / "conventions.yaml").unlink()
            if source == "agents":
                target = cwd / "AGENTS.md"
                content = "---\n" + json.dumps({"related": [bad, "../sibling"]}) + "\n---\n"
            elif source == "package":
                target = cwd / "package.json"
                prefix = "file:" if "file" in kind else ""
                content = json.dumps({"dependencies": {
                    "bad": prefix + bad, "good": prefix + "../sibling",
                }})
            elif source == "gitmodules":
                target = cwd / ".gitmodules"
                content = "path = " + bad + "\npath = ../sibling\n"
            elif source == "gomod":
                target = cwd / "go.mod"
                content = "replace bad => " + bad + "\nreplace good => ../sibling\n"
            else:
                target = cwd / ("pyproject.toml" if source == "pyproject" else "Cargo.toml")
                content = 'path = "' + bad + '"\npath = "../sibling"\n'
        target.write_text(content)
        assert target.stat().st_size < 1000

        post_image, digest = _assemble_post_image(cwd, _local_cli_diff())
        assert "LocalOnly" in post_image
        assert "LocalOnly" in digest and "PySelected" in digest

    @pytest.mark.parametrize("kind", ["package", "custom", "agents"])
    def test_oversized_integer_keeps_cli_conventions(self, tmp_path, kind):
        from code_forge.cli import _assemble_post_image

        limit = sys.get_int_max_str_digits()
        if limit == 0 or limit + 100 >= 4_000_000:
            pytest.skip("requires a bounded Python integer conversion limit")
        digits = "9" * (limit + 1)
        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        local = cwd / "src" / "local.py"
        local.parent.mkdir()
        local.write_text("def LocalOnly(): pass\n")
        diff = _local_cli_diff()
        post_image, baseline = _assemble_post_image(cwd, diff)
        assert "LocalOnly" in post_image
        assert "LocalOnly" in baseline and "PySelected" in baseline
        if kind == "package":
            target = cwd / "package.json"
            content = '{"ignored":' + digits + "}"
        elif kind == "custom":
            target = cwd / ".code-forge" / "conventions.yaml"
            content = "siblings: [../sibling]\nignored: " + digits + "\n"
        else:
            target = cwd / "AGENTS.md"
            content = "---\nignored: " + digits + "\n---\n"
        target.write_text(content)
        assert target.stat().st_size < 4_000_000

        post_image, digest = _assemble_post_image(cwd, diff)
        assert "LocalOnly" in post_image and "LocalOnly" in digest
        if kind == "custom":
            assert "PySelected" not in digest
        else:
            assert digest == baseline
        assert list((cwd / ".code-forge").glob("conventions-cache-*.json")) == []

    @pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="requires POSIX FIFO")
    @pytest.mark.parametrize("kind", ["custom", "agents", "context", "gitmodules",
                                     "package", "pyproject", "gomod", "cargo"])
    def test_regular_document_replaced_by_fifo(self, tmp_path, monkeypatch, kind):
        import os
        import stat
        from code_forge.conventions import get_digest

        cwd = tmp_path / "repo"
        (cwd / "src").mkdir(parents=True)
        (cwd / "src" / "ordinary.py").write_text("def ordinary_local(): pass\n")
        inputs = {
            "custom": (".code-forge/conventions.yaml", "siblings: []\n"),
            "agents": ("AGENTS.md", "No siblings here.\n"),
            "context": ("CLAUDE.md", "No siblings here.\n"),
            "gitmodules": (".gitmodules", "[submodule]\n"),
            "package": ("package.json", "{}"),
            "pyproject": ("pyproject.toml", "[project]\n"),
            "gomod": ("go.mod", "module local\n"),
            "cargo": ("Cargo.toml", "[package]\n"),
        }
        rel, contents = inputs[kind]
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
        regular_inode = target.stat().st_ino
        original = Path.is_file
        swapped = []

        def replace_after_check(path):
            answer = original(path)
            if answer and not swapped and path.resolve() == target:
                path.unlink()
                os.mkfifo(path)
                swapped.append(path.stat())
                print(json.dumps({"kind": kind, "fifo": str(path),
                                  "regular_inode": regular_inode,
                                  "fifo_inode": swapped[0].st_ino}), flush=True)
            return answer

        monkeypatch.setattr(Path, "is_file", replace_after_check)
        assert "ordinary_local" in get_digest(cwd)
        assert len(swapped) == 1
        current = target.stat()
        assert stat.S_ISFIFO(current.st_mode)
        assert current.st_ino == swapped[0].st_ino

    @pytest.mark.parametrize("kind", ["custom", "agents"])
    def test_deep_yaml_input_is_ignored(self, tmp_path, kind):
        depth = "[" * 2000 + "]" * 2000
        if kind == "custom":
            target = tmp_path / ".code-forge" / "conventions.yaml"
            target.parent.mkdir()
            target.write_text(depth)
            assert cr.resolve_sources(tmp_path) == []
        else:
            (tmp_path / "AGENTS.md").write_text("---\nrelated: " + depth + "\n---\n")
            assert cr.resolve_sources(tmp_path) == []

    def test_package_json_wrong_shape_is_ignored(self, tmp_path):
        (tmp_path / "package.json").write_text("[]")
        assert cr.resolve_sources(tmp_path) == []


    def test_python_json_decoder_recursion_is_ignored(self, tmp_path, monkeypatch):
        decoder = json.JSONDecoder()
        decoder.scan_once = json.scanner.py_make_scanner(decoder)
        monkeypatch.setattr(json, "_default_decoder", decoder)
        depth = "[" * 20_000 + "]" * 20_000
        with pytest.raises(RecursionError):
            decoder.decode(depth)
        target = tmp_path / "package.json"
        target.write_text(depth)
        assert cr.resolve_sources(tmp_path) == []

    def test_json_loader_bug_is_not_swallowed(self, tmp_path, monkeypatch):
        def broken_loader(*args, **kwargs):
            raise RuntimeError("unexpected JSON loader bug")

        target = tmp_path / "package.json"
        target.write_text("{}")
        monkeypatch.setattr(cr.json, "loads", broken_loader)
        with pytest.raises(RuntimeError, match="unexpected JSON loader bug"):
            cr.resolve_sources(tmp_path)


class TestDirectScanNoDiskCache:
    def test_unencodable_source_does_not_hide_later_valid_sibling(self, tmp_path):
        cwd = tmp_path / "consumer"
        good = tmp_path / "good"
        cwd.mkdir()
        good.mkdir()
        (good / "api.py").write_text("def GoodSibling(): pass\n")
        admitted = cr._directory_identity(good)
        sources = [
            ResolvedSource(Path("bad\ud800"), 1, "custom"),
            ResolvedSource(good, 2, "custom", root_identity=admitted),
        ]
        with patch.object(cr, "resolve_sources", return_value=sources), _mock_git():
            digest = get_cross_repo_digest(cwd)
        assert "GoodSibling" in digest

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    def test_cli_cold_and_repeat_leave_consumer_unmodified(
        self, tmp_path, monkeypatch, object_format,
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_DEFAULT_HASH", object_format)
        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)
        assert len(commit) == {"sha1": 40, "sha256": 64}[object_format]
        config = cwd / ".code-forge"
        before = {entry.name: entry.read_bytes() for entry in config.iterdir()}
        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            cold = _assemble_post_image(cwd, _local_cli_diff())
            repeat = _assemble_post_image(cwd, _local_cli_diff())
        assert cold == repeat
        assert "LocalOnly" in cold[0] and "LocalOnly" in cold[1]
        assert all(name in cold[1] for name in ("PySelected", "TsSelected", "GoSelected", "RustSelected"))
        assert scan.call_count == 2
        assert {entry.name: entry.read_bytes() for entry in config.iterdir()} == before

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("entry_kind", ["primary", "legacy"])
    def test_cli_ignores_forged_existing_cache_entry(
        self, tmp_path, monkeypatch, object_format, entry_kind,
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_DEFAULT_HASH", object_format)
        cwd, sibling, _outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        cold = _assemble_post_image(cwd, _local_cli_diff())
        assert "PySelected" in cold[1]

        config = cwd / ".code-forge"
        path_hash = cr._repo_path_hash(sibling)
        commit = cr._get_git_commit(sibling)
        primary = config / f"conventions-cache-{path_hash}_{commit}.json"
        # V43 creates a structurally valid entry on the cold call. Reuse that
        # payload for the RED control; V44 deliberately never creates one.
        payload = json.loads(primary.read_text()) if primary.exists() else {
            "digest": "", "repo": str(sibling), "commit": commit,
        }
        payload["digest"] = "FORGED_ONLY_FROM_CACHE"
        if entry_kind == "legacy":
            legacy = config / "conventions-cache"
            legacy.mkdir(exist_ok=True)
            target = legacy / f"{path_hash}_{commit}.json"
            primary.unlink(missing_ok=True)
        else:
            target = primary
        target.write_text(json.dumps(payload))
        before = target.read_bytes()

        with patch.object(cr, "extract_conventions", wraps=cr.extract_conventions) as scan:
            repeat = _assemble_post_image(cwd, _local_cli_diff())
        assert repeat == cold
        assert "FORGED_ONLY_FROM_CACHE" not in repeat[1]
        assert "PySelected" in repeat[1] and "LocalOnly" in repeat[1]
        assert scan.call_count == 1
        assert target.read_bytes() == before

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("mode", [
        "create", "write", "replace", "orphan-unlink", "stale-unlink",
    ])
    def test_cli_moved_cache_parent_cannot_mutate_external_entries(
        self, tmp_path, monkeypatch, object_format, mode,
    ):
        from code_forge.cli import _assemble_post_image

        monkeypatch.setenv("GIT_DEFAULT_HASH", object_format)
        cwd, sibling, outside, _selected = _multilang_git_fixture(tmp_path)
        (cwd / "src").mkdir()
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        config = cwd / ".code-forge"
        (config / "important.json").write_text('{"keep": true}\n')
        commit = cr._get_git_commit(sibling)
        _assert_real_git_commit(sibling, commit)
        if mode in ("orphan-unlink", "stale-unlink"):
            path_hash = "deadbeef1234" if mode == "orphan-unlink" else cr._repo_path_hash(sibling)
            victim = config / f"conventions-cache-{path_hash}_{'a' * len(commit)}.json"
            victim.write_text('{"keep": true}\n')
        before = {entry.name: entry.read_bytes() for entry in config.iterdir()}
        moved = outside / "moved-config"
        sites = {
            "create": ("_write_cache", 1892),
            "write": ("_write_cache", 1900),
            "replace": ("_write_cache", 1903),
            "orphan-unlink": ("_collect_cross_repo_digest", 1494),
            "stale-unlink": ("_write_cache", 1921),
        }
        historical_function, historical_line = sites[mode]
        events = []

        def relocate(stage):
            config.rename(moved)
            config.symlink_to(moved, target_is_directory=True)
            events.append(stage)

        def trace(frame, event, _arg):
            if frame.f_code.co_filename != cr.__file__ or events:
                return trace
            if (event == "line" and frame.f_code.co_name == historical_function
                    and frame.f_lineno == historical_line):
                relocate("v43-mutation-boundary")
            elif event == "return" and frame.f_code.co_name == "_collect_cross_repo_digest":
                # V44 has no cache mutation boundary. Move the directory at
                # completion so any later cache activity is also observable.
                relocate("direct-scan-complete")
            return trace

        previous_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            post, digest = _assemble_post_image(cwd, _local_cli_diff())
        finally:
            sys.settrace(previous_trace)
        assert len(events) == 1
        assert "LocalOnly" in post and "LocalOnly" in digest and "PySelected" in digest
        assert {entry.name: entry.read_bytes() for entry in moved.iterdir()} == before

    def test_cli_does_not_create_cache_directory(self, tmp_path):
        from code_forge.cli import _assemble_post_image

        cwd = tmp_path / "consumer"
        sibling = tmp_path / "sibling"
        (cwd / "src").mkdir(parents=True)
        (sibling / "src").mkdir(parents=True)
        (cwd / "src/local.py").write_text("def LocalOnly(): pass\n")
        (sibling / "src/api.py").write_text("def SiblingOnly(): pass\n")
        (cwd / "AGENTS.md").write_text("---\nrelated: [../sibling]\n---\n")
        before = _assemble_post_image(cwd, _local_cli_diff())
        repeat = _assemble_post_image(cwd, _local_cli_diff())
        assert before == repeat
        assert "LocalOnly" in before[1] and "SiblingOnly" in before[1]
        assert not (cwd / ".code-forge").exists()
