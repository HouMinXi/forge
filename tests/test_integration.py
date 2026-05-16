# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""End-to-end integration tests for the forge CLI.

Tests the full pipeline: git diff -> tool execution -> parsing ->
delta filter -> verdict -> state persistence.

Addresses:
- Consensus #5: correct git diff invocation (tracked files only)
- Round 3 C-5: shellcheck availability guard
- Round 3 H-4: pytest.raises(SystemExit) for sys.exit testing
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from forge import EXIT_PASS, EXIT_FAIL

pytestmark = pytest.mark.skipif(
    not shutil.which("shellcheck"),
    reason="shellcheck not installed",
)


def _write_tools_yaml(repo_dir):
    """Write a minimal tools.yaml with only shellcheck."""
    forge_dir = os.path.join(repo_dir, ".forge")
    os.makedirs(forge_dir, exist_ok=True)
    tools_yaml = os.path.join(forge_dir, "tools.yaml")
    with open(tools_yaml, "w", encoding="utf-8") as f:
        f.write(
            "tools:\n"
            "  shellcheck:\n"
            '    command: shellcheck\n'
            '    args: ["-f", "json"]\n'
            "    output_format: shellcheck_json\n"
            '    file_patterns: ["*.sh"]\n'
            "    required: false\n"
            "    timeout: 30\n"
            "    enabled: true\n"
        )
    return tools_yaml


def _git_init(repo_dir):
    """Initialize a git repo with user config."""
    subprocess.run(
        ["git", "init"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )


def _git_add_commit(repo_dir, message="commit"):
    """Stage all and commit."""
    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )


class TestIntegrationFail:
    """Test: new violations produce FAIL."""

    def test_fail_on_shellcheck_violation(
        self, tmp_path, capsys, monkeypatch
    ):
        """Modify tracked file with shellcheck violation -> FAIL."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        # Create clean initial file and commit
        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        # Modify with violation (unquoted variable)
        script.write_text(
            "#!/bin/bash\necho hello\necho $unquoted_var\n"
        )

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(sys, "argv", ["forge"])
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == EXIT_FAIL

        captured = capsys.readouterr()
        assert "forge: FAIL" in captured.out
        # SC2154 (referenced but not assigned) or SC2086 (double quote)
        assert "SC2154" in captured.out or "SC2086" in captured.out


class TestIntegrationPass:
    """Test: clean code produces PASS."""

    def test_pass_on_clean_code(self, tmp_path, capsys, monkeypatch):
        """Modify tracked file with clean code -> PASS."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        # Create initial file and commit
        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        # Modify with clean code
        script.write_text("#!/bin/bash\necho hello\necho world\n")

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(sys, "argv", ["forge"])
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == EXIT_PASS

        captured = capsys.readouterr()
        assert "forge: PASS" in captured.out


class TestIntegrationBaseline:
    """Test: pre-existing violations in committed code not shown."""

    def test_baseline_preexisting_not_shown(
        self, tmp_path, capsys, monkeypatch
    ):
        """Pre-existing violations in committed file NOT shown."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        # Initial commit with violations in file A
        bad_script = repo / "bad.sh"
        bad_script.write_text("#!/bin/bash\necho $unquoted\n")
        _git_add_commit(repo_str, "initial with violations")

        # Now add file B with clean code and stage it
        clean_script = repo / "clean.sh"
        clean_script.write_text("#!/bin/bash\necho clean\n")
        subprocess.run(
            ["git", "add", "clean.sh"],
            cwd=repo_str,
            capture_output=True,
            check=True,
        )

        _write_tools_yaml(repo_str)

        # Use --staged since the new file is staged
        monkeypatch.setattr(sys, "argv", ["forge", "--staged"])
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == EXIT_PASS

        captured = capsys.readouterr()
        # Pre-existing violations in bad.sh should NOT appear
        assert "bad.sh" not in captured.out
        assert "forge: PASS" in captured.out


class TestIntegrationState:
    """Test: state.json written with tool_versions (Consensus #3)."""

    def test_state_json_written_with_versions(
        self, tmp_path, monkeypatch
    ):
        """Verify .forge/state.json has tool_versions populated."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        # Create clean script and commit
        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        # Modify to trigger tool run
        script.write_text("#!/bin/bash\necho hello\necho world\n")

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(sys, "argv", ["forge"])
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        with pytest.raises(SystemExit):
            main()

        state_path = os.path.join(repo_str, ".forge", "state.json")
        assert os.path.isfile(state_path), "state.json not written"

        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)

        assert "tool_versions" in state
        assert isinstance(state["tool_versions"], dict)
        # shellcheck should have a version
        assert "shellcheck" in state["tool_versions"]
        assert state["tool_versions"]["shellcheck"] != "not_installed"
        assert state["tool_versions"]["shellcheck"] != ""

        assert "verdict" in state
        assert "diff_spec" in state
        assert "tools_run" in state
