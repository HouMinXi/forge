# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""CLI backward compatibility tests for deprecated and preserved flags."""

import subprocess
import sys

import pytest

from forge.cli import _build_parser, main


class TestPreservedFlags:
    """--registry, --quiet, --version still work.

    Post-subparser: --registry and --quiet are on review subcommand.
    --version remains on root parser.
    """

    def test_registry_flag_accepted(self):
        parser = _build_parser()
        args = parser.parse_args(["review", "--registry", "custom.yaml"])
        assert args.registry == "custom.yaml"

    def test_quiet_flag_accepted(self):
        parser = _build_parser()
        args = parser.parse_args(["review", "--quiet"])
        assert args.quiet is True

    def test_version_exits_zero(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


class TestStateDirDeprecation:
    """--state-dir accepted but ignored (deprecated)."""

    def test_state_dir_emits_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """--state-dir with non-default value emits warning."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text("# initial\n")
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
            ["forge", "--mode", "ci",
             "--state-dir", "/tmp/custom", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        main()
        captured = capsys.readouterr()
        assert "deprecated" in captured.err.lower()
        # State.json written to cwd/.forge regardless.
        assert (repo / ".forge" / "state.json").exists()


class TestStagedDeprecation:
    """--staged emits deprecation warning."""

    def test_staged_warns(
        self, tmp_path, monkeypatch, capsys
    ):
        """--staged emits deprecation warning."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")
        subprocess.run(
            ["git", "add", "a.py"],
            cwd=str(repo), capture_output=True, check=True,
        )

        monkeypatch.setattr(
            sys, "argv",
            ["forge", "--mode", "ci", "--staged", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        main()
        captured = capsys.readouterr()
        assert "deprecated" in captured.err.lower()
        assert "--head INDEX" in captured.err

    def test_staged_quiet_suppresses_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """--staged + --quiet -> warning suppressed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text("# initial\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo), capture_output=True, check=True,
        )
        (repo / "a.py").write_text("# modified\n")
        subprocess.run(
            ["git", "add", "a.py"],
            cwd=str(repo), capture_output=True, check=True,
        )

        monkeypatch.setattr(
            sys, "argv",
            ["forge", "--mode", "ci", "--staged",
             "--quiet", "a.py"],
        )
        monkeypatch.chdir(str(repo))
        main()
        captured = capsys.readouterr()
        # Warning should be suppressed by --quiet.
        assert "--staged" not in captured.err
