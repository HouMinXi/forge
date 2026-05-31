# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""End-to-end CLI integration tests (no subprocess).

Tests exercise _run() with injected env/cwd to verify the full
pipeline without needing real git repos (except where noted).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS
from code_forge.cli import _run, main
from code_forge.errors import CliError


def _git_init_repo(repo_path):
    """Create a minimal git repo."""
    subprocess.run(
        ["git", "init"], cwd=str(repo_path),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=str(repo_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo_path), capture_output=True, check=True,
    )


def _write_tools_yaml(repo_path):
    """Write empty tools.yaml (no tools configured)."""
    forge_dir = repo_path / ".code-forge"
    forge_dir.mkdir(parents=True, exist_ok=True)
    tools_yaml = forge_dir / "tools.yaml"
    tools_yaml.write_text("tools: {}\n")


def _write_py_file(repo_path, name="a.py"):
    """Write a minimal Python file."""
    f = repo_path / name
    f.write_text("# clean\n")
    return f


class TestGitRepoPassCI:
    """SC-15: git repo + no findings + CI -> exit 0."""

    def test_clean_tree_pass(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _write_tools_yaml(repo)
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        # Modify file to create a diff.
        (repo / "a.py").write_text("# clean\nx = 1\n")

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        exit_code = main()
        assert exit_code == EXIT_PASS

        state_path = repo / ".code-forge" / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["verdict"] == "PASS"


class TestRegistryMissing:
    """SC-17: missing registry -> exit 2."""

    def test_registry_not_found(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        # No tools.yaml written.
        monkeypatch.setattr(
            sys, "argv", ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        exit_code = main()
        assert exit_code == EXIT_CLI_ERROR


class TestSandboxWarning:
    """SC-39 R3-1: --sandbox emits warning, no behavior change."""

    def test_sandbox_warning_emitted(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _write_tools_yaml(repo)
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--mode", "ci", "--sandbox", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        exit_code = main()
        captured = capsys.readouterr()
        assert "Phase 4 hook; ignored in v2.0" in captured.err
        assert exit_code == EXIT_PASS


class TestTopLevelExceptionCatch:
    """SC-46 R3-L2: unexpected exception -> exit FAIL + traceback."""

    def test_unexpected_exception_returns_fail(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            sys, "argv", ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(tmp_path))

        # Inject a crash in _run by mocking resolve_mode.
        with patch(
            "code_forge.cli.resolve_mode",
            side_effect=RuntimeError("test panic"),
        ):
            exit_code = main()

        assert exit_code == EXIT_FAIL
        captured = capsys.readouterr()
        assert "unexpected error: test panic" in captured.err


class TestMainReturnsInt:
    """02-05 main() returns int, not None."""

    def test_main_returns_int(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _write_tools_yaml(repo)
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        result = main()
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Review auto-detect integration (D-20)
# ---------------------------------------------------------------------------


class TestReviewAutoDetect:
    """D-20: review pipeline calls detect_and_init when tools.yaml missing."""

    def test_review_missing_default_tools_yaml_triggers_detect(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Missing default tools.yaml triggers detect_and_init(quiet=True)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        def fake_detect_and_init(project_root, quiet=False, **kwargs):
            forge_dir = project_root / ".code-forge"
            forge_dir.mkdir(parents=True, exist_ok=True)
            tools_yaml = forge_dir / "tools.yaml"
            tools_yaml.write_text(
                "tools:\n"
                "  ruff:\n"
                "    command: ruff check --output-format=sarif\n"
                "    output_format: sarif\n"
                "    file_patterns: ['*.py']\n"
            )
            from code_forge.detect import DetectionResult
            return DetectionResult(
                detected=["ruff"], missing=[], language="python",
            )

        with patch(
            "code_forge.detect.detect_and_init",
            side_effect=fake_detect_and_init,
        ) as mock_dai:
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "--mode", "ci", "a.py"],
            )
            monkeypatch.chdir(str(repo))
            exit_code = main()

        mock_dai.assert_called_once()
        call_kwargs = mock_dai.call_args
        assert call_kwargs[1].get("quiet") is True
        assert exit_code == EXIT_PASS

    def test_review_existing_nonempty_tools_yaml_skips_detect(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Existing non-empty tools.yaml skips detect_and_init."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        tools_yaml = forge_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  ruff:\n"
            "    command: ruff check --output-format=sarif\n"
            "    output_format: sarif\n"
            "    file_patterns: ['*.py']\n"
        )
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        with patch(
            "code_forge.detect.detect_and_init",
        ) as mock_dai:
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "--mode", "ci", "a.py"],
            )
            monkeypatch.chdir(str(repo))
            exit_code = main()

        mock_dai.assert_not_called()
        assert exit_code == EXIT_PASS

    def test_review_custom_registry_path_no_detect(
        self, tmp_path, monkeypatch, capsys,
    ):
        """--registry=custom.yaml missing -> CliError, no detect."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )

        with patch(
            "code_forge.detect.detect_and_init",
        ) as mock_dai:
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "--mode", "ci",
                 "--registry", "custom.yaml", "a.py"],
            )
            monkeypatch.chdir(str(repo))
            exit_code = main()

        mock_dai.assert_not_called()
        assert exit_code == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "custom.yaml" in captured.err

    def test_review_empty_tools_yaml_detect_fails_exits_cli_error(
        self, tmp_path, monkeypatch, capsys,
    ):
        """tools.yaml with tools: [] -> detect called -> CliError -> exit 2."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        tools_yaml = forge_dir / "tools.yaml"
        tools_yaml.write_text("tools: []\n")
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        with patch(
            "code_forge.detect.detect_and_init",
            side_effect=CliError(
                "No toolchain detected. L0 has no static "
                "analysis tools. Install tools or manually "
                "configure `.code-forge/tools.yaml`."
            ),
        ):
            monkeypatch.setattr(
                sys, "argv",
                ["code-forge", "--mode", "ci", "a.py"],
            )
            monkeypatch.chdir(str(repo))
            exit_code = main()

        assert exit_code == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "No toolchain detected" in captured.err

    def test_review_corrupted_tools_yaml_exits_cli_error(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Corrupted tools.yaml -> ValueError from load_registry -> exit 2."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        tools_yaml = forge_dir / "tools.yaml"
        tools_yaml.write_text(
            "tools:\n"
            "  ruff:\n"
            "    command: ruff\n"
            "    output_format: sarif\n"
            "    file_patterns: not_a_list\n"
        )
        _write_py_file(repo)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--mode", "ci", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        exit_code = main()

        assert exit_code == EXIT_CLI_ERROR
        captured = capsys.readouterr()
        assert "registry load failed" in captured.err
