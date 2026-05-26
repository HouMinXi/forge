# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""End-to-end integration tests for the forge CLI.

Updated for 02-05: main() returns int (no sys.exit).
Phase 1 pipeline tests adapted to 02-XX StateMachine-based CLI.
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
    """Test: new violations via StateMachine -> FAIL or PASS.

    02-05 main() returns int. The StateMachine decides the verdict.
    In CI mode a finding -> FAIL exit 1.
    """

    def test_fail_on_shellcheck_violation(
        self, tmp_path, monkeypatch
    ):
        """Modify tracked file with shellcheck violation -> FAIL."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        script.write_text(
            "#!/bin/bash\necho hello\necho $unquoted_var\n"
        )

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(
            sys, "argv", ["forge", "--mode", "ci", "hello.sh"]
        )
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        exit_code = main()
        assert exit_code == EXIT_FAIL

        state_path = os.path.join(repo_str, ".forge", "state.json")
        assert os.path.isfile(state_path)
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        assert state["verdict"] == "FAIL"


class TestIntegrationPass:
    """Test: clean code produces PASS."""

    def test_pass_on_clean_code(self, tmp_path, monkeypatch):
        """Modify tracked file with clean code -> PASS."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        script.write_text("#!/bin/bash\necho hello\necho world\n")

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(
            sys, "argv", ["forge", "--mode", "ci", "hello.sh"]
        )
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        exit_code = main()
        assert exit_code == EXIT_PASS

        state_path = os.path.join(repo_str, ".forge", "state.json")
        assert os.path.isfile(state_path)
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        assert state["verdict"] == "PASS"


class TestIntegrationBaseline:
    """Test: pre-existing violations in committed code not shown."""

    def test_baseline_preexisting_not_shown(
        self, tmp_path, monkeypatch
    ):
        """Pre-existing violations in committed file -> PASS.

        02-05: use --head INDEX instead of --staged.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        bad_script = repo / "bad.sh"
        bad_script.write_text("#!/bin/bash\necho $unquoted\n")
        _git_add_commit(repo_str, "initial with violations")

        clean_script = repo / "clean.sh"
        clean_script.write_text("#!/bin/bash\necho clean\n")
        subprocess.run(
            ["git", "add", "clean.sh"],
            cwd=repo_str,
            capture_output=True,
            check=True,
        )

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(
            sys, "argv",
            ["forge", "--mode", "ci", "--head", "INDEX",
             "clean.sh"],
        )
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        exit_code = main()
        assert exit_code == EXIT_PASS


class TestIntegrationState:
    """Test: state.json written with schema fields."""

    def test_state_json_written_with_versions(
        self, tmp_path, monkeypatch
    ):
        """Verify .forge/state.json has Phase 2 typed fields."""
        repo = tmp_path / "repo"
        repo.mkdir()
        repo_str = str(repo)

        _git_init(repo_str)

        script = repo / "hello.sh"
        script.write_text("#!/bin/bash\necho hello\n")
        _git_add_commit(repo_str, "initial")

        script.write_text("#!/bin/bash\necho hello\necho world\n")

        _write_tools_yaml(repo_str)

        monkeypatch.setattr(
            sys, "argv", ["forge", "--mode", "ci", "hello.sh"]
        )
        monkeypatch.chdir(repo_str)

        from forge.cli import main

        main()

        state_path = os.path.join(repo_str, ".forge", "state.json")
        assert os.path.isfile(state_path), "state.json not written"

        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)

        assert "schema_version" in state
        assert state["schema_version"] == 1
        assert "verdict" in state
        assert "disposition_protocol_version" in state
