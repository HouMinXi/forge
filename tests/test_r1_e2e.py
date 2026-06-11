# SPDX-License-Identifier: Apache-2.0
"""R1 end-to-end integration tests for install-hooks + carve-out."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

from code_forge.exit_codes import EXIT_PASS
from code_forge.install_hooks import run_install_hooks


def _make_stub(bin_dir: Path) -> None:
    """Create a stub code-forge: verify=0, gate-check=1."""
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "code-forge"
    stub.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  verify) exit 0;;\n'
        '  gate-check) echo "stub: gate-check blocked" >&2; exit 1;;\n'
        '  *) exit 0;;\n'
        'esac\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _init_repo(tmp_path: Path) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    seed = tmp_path / "seed.txt"
    seed.write_text("seed")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path,
                    capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
         "commit", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
    )


class TestR1EndToEnd:
    """R1 lifecycle: install, code triggers, non-code passes, no stale, pre-push."""

    def test_install_hooks_succeeds_clean_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        with patch("code_forge.install_hooks.resolve_forge_path",
                    return_value="code-forge gate-check"):
            result = run_install_hooks(
                args=None, env=os.environ.copy(), cwd=tmp_path,
            )
        assert result == EXIT_PASS
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        assert hook.exists()
        assert os.access(hook, os.X_OK)
        content = hook.read_text()
        assert "NON_CODE" in content
        assert "gate-check" in content

    def test_r1_code_commit_triggers_gate(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)

        with patch("code_forge.install_hooks.resolve_forge_path",
                    return_value="code-forge gate-check"):
            run_install_hooks(args=None, env=os.environ.copy(), cwd=tmp_path)

        (tmp_path / "app.py").write_text("pass")
        subprocess.run(["git", "add", "app.py"], cwd=tmp_path,
                        capture_output=True, check=True)

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "add code"],
            cwd=tmp_path, capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0

    def test_r1_noncode_commit_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)

        with patch("code_forge.install_hooks.resolve_forge_path",
                    return_value="code-forge gate-check"):
            run_install_hooks(args=None, env=os.environ.copy(), cwd=tmp_path)

        (tmp_path / "changelog.md").write_text("v1")
        subprocess.run(["git", "add", "changelog.md"], cwd=tmp_path,
                        capture_output=True, check=True)

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "docs update"],
            cwd=tmp_path, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"non-code commit should pass: {result.stderr}"

    def test_hooks_dir_no_stale_artifacts(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        with patch("code_forge.install_hooks.resolve_forge_path",
                    return_value="code-forge gate-check"):
            run_install_hooks(args=None, env=os.environ.copy(), cwd=tmp_path)

        hooks_dir = tmp_path / ".git" / "hooks"
        non_sample = [
            f.name for f in hooks_dir.iterdir()
            if f.is_file() and not f.name.endswith(".sample")
        ]
        assert non_sample == ["pre-commit"]

    def test_existing_prepush_survives_install(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)

        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        prepush = hooks_dir / "pre-push"
        prepush.write_text("#!/bin/sh\nexit 0\n")
        prepush.chmod(prepush.stat().st_mode | stat.S_IEXEC)

        with patch("code_forge.install_hooks.resolve_forge_path",
                    return_value="code-forge gate-check"):
            run_install_hooks(args=None, env=os.environ.copy(), cwd=tmp_path)

        assert prepush.exists()
        assert prepush.read_text() == "#!/bin/sh\nexit 0\n"
