# SPDX-License-Identifier: Apache-2.0
"""Tests for the non-code carve-out in generate_hook_content."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

from code_forge.install_hooks import generate_hook_content


def _make_stub(bin_dir: Path) -> None:
    """Create a stub code-forge script that passes verify, fails gate-check."""
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
    """Initialize a git repo in tmp_path with an initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    readme = tmp_path / "seed.txt"
    readme.write_text("seed")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
         "commit", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
    )


class TestCarveoutContent:
    """String-level tests on generated hook content."""

    def test_carveout_block_present(self, tmp_path, monkeypatch):
        """(a) generate_hook_content output contains NON_CODE."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        content = generate_hook_content("code-forge gate-check", None)
        assert "NON_CODE=" in content

    def test_carveout_before_attestation(self, tmp_path, monkeypatch):
        """(e) carve-out block appears BEFORE attestation in hook."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        content = generate_hook_content("code-forge gate-check", None)
        idx_carveout = content.index("NON_CODE")
        idx_attest = content.index("code-forge verify")
        assert idx_carveout < idx_attest

    def test_carveout_with_chain(self, tmp_path, monkeypatch):
        """(f) chain variant also includes the carve-out block."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        content = generate_hook_content("code-forge gate-check", Path("/tmp/old"))
        assert "NON_CODE=" in content
        assert "old" in content


class TestCarveoutExecution:
    """Run the generated hook in a real git repo with stub code-forge."""

    def test_docs_only_skips_verify(self, tmp_path, monkeypatch):
        """(b) docs-only commit exits 0 via carve-out."""
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
            content = generate_hook_content("code-forge gate-check", None)

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        (tmp_path / "docs.md").write_text("docs")
        subprocess.run(["git", "add", "docs.md"], cwd=tmp_path,
                        capture_output=True, check=True)

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "docs only"],
            cwd=tmp_path, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"expected 0, got {result.returncode}: {result.stderr}"

    def test_code_file_triggers_verify(self, tmp_path, monkeypatch):
        """(c) code file triggers gate-check (stub fails it)."""
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
            content = generate_hook_content("code-forge gate-check", None)

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

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

    def test_mixed_commit_triggers_verify(self, tmp_path, monkeypatch):
        """(d) mixed code+docs commit triggers gate-check."""
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
            content = generate_hook_content("code-forge gate-check", None)

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        (tmp_path / "readme.md").write_text("docs")
        (tmp_path / "main.py").write_text("pass")
        subprocess.run(["git", "add", "readme.md", "main.py"], cwd=tmp_path,
                        capture_output=True, check=True)

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "mixed"],
            cwd=tmp_path, capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0

    def test_unknown_extension_triggers_verify(self, tmp_path, monkeypatch):
        """(g) unknown extension file triggers gate-check."""
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
            content = generate_hook_content("code-forge gate-check", None)

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        (tmp_path / "data.xyz").write_text("unknown")
        subprocess.run(["git", "add", "data.xyz"], cwd=tmp_path,
                        capture_output=True, check=True)

        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "unknown ext"],
            cwd=tmp_path, capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0

    def test_noncode_commit_without_stub(self, tmp_path, monkeypatch):
        """(h) Anti-mock guard: nonexistent binary, no stub."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        _init_repo(tmp_path)

        content = generate_hook_content(
            "/nonexistent/code-forge gate-check", None,
        )

        hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(content)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        (tmp_path / "notes.md").write_text("notes")
        subprocess.run(["git", "add", "notes.md"], cwd=tmp_path,
                        capture_output=True, check=True)

        result = subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
             "commit", "-m", "docs no stub"],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"carve-out should exit 0 before hitting nonexistent binary: "
            f"{result.stderr}"
        )
