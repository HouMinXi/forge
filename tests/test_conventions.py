# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for conventions.py -- D11 conventions-digest slot (Phase 15a).

Covers:
  - get_same_repo_digest / get_digest
  - _extract_python_public_names: async, top-level only, large file skip,
    venv/_SKIP_DIRS pruning, symlink prefix-collision rejection
"""
import os
from pathlib import Path


from code_forge.conventions import (
    _extract_python_public_names,
    get_digest,
    get_same_repo_digest,
)


class TestGetSameRepoDigest:
    def test_empty_on_no_python_files(self, tmp_path):
        assert get_same_repo_digest(tmp_path) == ""

    def test_extracts_public_names(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "example.py").write_text(
            "def hello(): pass\nclass MyClass: pass\ndef _private(): pass\n"
        )
        digest = get_same_repo_digest(tmp_path)
        assert "hello" in digest
        assert "MyClass" in digest
        assert "_private" not in digest

    def test_syntax_error_file_skipped(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "bad.py").write_text("def (\n")
        # must not raise
        result = get_same_repo_digest(tmp_path)
        assert result == ""

    def test_digest_empty_no_sources(self, tmp_path):
        assert get_same_repo_digest(tmp_path) == ""


class TestGetDigest:
    def test_get_digest_uses_parts_list(self, tmp_path):
        """get_digest must build via parts list and match get_same_repo_digest."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "example.py").write_text(
            "def hello(): pass\nclass MyClass: pass\n"
        )
        assert get_digest(tmp_path) == get_same_repo_digest(tmp_path)

    def test_get_digest_returns_empty_for_nonexistent(self):
        result = get_digest(Path("/nonexistent_repo_path_xyz"))
        assert result == ""


class TestExtractPythonPublicNames:
    def test_extracts_async_functions(self, tmp_path):
        """M-04: async def must be collected same as def."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "async_mod.py").write_text(
            "async def fetch_data(): pass\ndef sync_fn(): pass\n"
        )
        funcs, classes = _extract_python_public_names(tmp_path)
        assert "fetch_data" in funcs
        assert "sync_fn" in funcs

    def test_top_level_only_not_nested(self, tmp_path):
        """M-05: tree.body iteration must NOT pick up nested functions."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "nested.py").write_text(
            "def outer():\n    def inner(): pass\n"
        )
        funcs, _ = _extract_python_public_names(tmp_path)
        assert "outer" in funcs
        assert "inner" not in funcs

    def test_skips_large_files(self, tmp_path):
        """M-06: files >100KB must be skipped entirely."""
        src = tmp_path / "src"
        src.mkdir()
        # ~120KB
        (src / "big.py").write_text("x = 1\n" * 20000)
        funcs, classes = _extract_python_public_names(tmp_path)
        assert funcs == []
        assert classes == []

    def test_skips_venv_and_tox_dirs(self, tmp_path):
        """L-R2-04: _SKIP_DIRS must be pruned inside src/ scan root.

        Skip dirs are created INSIDE src/ (M-R3-01) so that they are under
        the actual scan root. Since src/ exists (from real.py), the scanner
        uses src/ as its root. Skip dirs at the tmp_path level would be
        never visited and the test would pass vacuously.
        """
        src = tmp_path / "src"
        src.mkdir()
        # Real module -- must be found
        (src / "real.py").write_text("def real_func(): pass\n")
        # Skip dirs inside src/ (M-R3-01)
        venv_dir = src / "venv"
        venv_dir.mkdir()
        (venv_dir / "bad.py").write_text("def venv_func(): pass\n")
        tox_dir = src / ".tox" / "env"
        tox_dir.mkdir(parents=True)
        (tox_dir / "toxmod.py").write_text("def tox_func(): pass\n")
        build_dir = src / "build"
        build_dir.mkdir()
        (build_dir / "out.py").write_text("def build_func(): pass\n")

        funcs, _ = _extract_python_public_names(tmp_path)
        assert "real_func" in funcs
        assert "venv_func" not in funcs
        assert "tox_func" not in funcs
        assert "build_func" not in funcs

    def test_symlink_prefix_collision_rejected(self, tmp_path):
        """M-R5-01 / H-R5-01: Path.parents must reject prefix-collision paths.

        Regression test for the str.startswith path traversal bug where
        "/tmp/repo_evil/x.py".startswith("/tmp/repo") incorrectly returns True.

        A symlink inside repo/src/ pointing to repo_evil/src/mod.py must be
        rejected by the Path.parents containment check, NOT accepted by a
        str.startswith check.
        """
        repo = tmp_path / "repo"
        repo_evil = tmp_path / "repo_evil"
        (repo / "src").mkdir(parents=True)
        (repo_evil / "src").mkdir(parents=True)

        # evil module in the sibling repo
        evil_mod = repo_evil / "src" / "mod.py"
        evil_mod.write_text("def evil_func(): pass\n")

        # symlink inside the legitimate repo pointing to evil file
        symlink_path = repo / "src" / "linked.py"
        os.symlink(str(evil_mod), str(symlink_path))

        funcs, _ = _extract_python_public_names(repo)
        # The symlink traversal guard must reject this file
        assert "evil_func" not in funcs
