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


def _make_stub_verify_fails(bin_dir: Path) -> None:
    """Stub where verify always fails; gate-check and review pass.

    Use this to prove that attestation is what blocked a commit: if the
    declared-class skip did not happen, the commit cannot succeed.
    """
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "code-forge"
    stub.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  verify) echo "stub: no receipts" >&2; exit 1;;\n'
        '  *) exit 0;;\n'
        'esac\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _make_stub_review_fails(bin_dir: Path) -> None:
    """Stub where review always fails; verify and gate-check pass.

    Use this to prove the review block runs in the full gate path and is
    skipped in the declared-class path: everything else lets the commit
    through, so the failure can only come from review.
    """
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "code-forge"
    stub.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  review) echo "stub: review unreachable for declared" >&2; exit 1;;\n'
        '  *) exit 0;;\n'
        'esac\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


_ENTRY_FAILING_SUBMIT = {
    "command": ["false"],
    "applies_to": "*",
    "on": "diff",
    "applies_to_grep": ".*",
}


def _install_hook(tmp_path: Path, presubmit_entries: list | None = None) -> None:
    """Write the generated hook into tmp_path's .git and make it executable."""
    content = generate_hook_content(
        "code-forge gate-check", None, presubmit_entries=presubmit_entries,
    )
    hook_path = tmp_path / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(content)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)


def _stage_and_commit(tmp_path: Path, bin_dir: Path, name: str, text: str,
                      env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Stage `name` with `text` in the tmp_path repo and attempt a commit."""
    (tmp_path / name).write_text(text)
    subprocess.run(["git", "add", name], cwd=tmp_path,
                    capture_output=True, check=True)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env.update(env_extra or {})
    return subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test",
         "commit", "-m", "test commit"],
        cwd=tmp_path, capture_output=True, text=True, env=env,
    )


class TestDeclaredClassCarveout:
    """FORGE_COMMIT_CLASS declares docs/config/chore/wip for code-file commits.

    The declared skip covers attestation, LLM review, and gate-check; the
    staged-diff text gates (non-ASCII, AI vocabulary) still apply.
    """

    def test_declared_block_between_carveout_and_attestation(self):
        """(i) declared-class block ordering in generated content."""
        content = generate_hook_content("code-forge gate-check", None)
        idx_class = content.index("FORGE_COMMIT_CLASS")
        idx_carveout = content.index("NON_CODE")
        idx_attest = content.index("code-forge verify")
        assert idx_carveout < idx_class < idx_attest

    def test_declared_chore_skips_attestation_for_code_commit(
            self, tmp_path, monkeypatch):
        """(j) .py commit with FORGE_COMMIT_CLASS=chore commits despite
        a stub whose verify always fails."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub_verify_fails(bin_dir)
        _install_hook(tmp_path)

        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 1\n",
            env_extra={"FORGE_COMMIT_CLASS": "chore"},
        )
        assert result.returncode == 0, (
            f"expected declared chore to pass, got {result.returncode}: "
            f"{result.stderr}"
        )
        assert "declared" in result.stderr

    def test_undeclared_code_commit_blocked_by_verify(
            self, tmp_path, monkeypatch):
        """(k) without the env class, the same failing stub still blocks."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub_verify_fails(bin_dir)
        _install_hook(tmp_path)

        result = _stage_and_commit(tmp_path, bin_dir, "app.py", "x = 1\n")
        assert result.returncode != 0
        assert "stub: no receipts" in result.stderr

    def test_declared_skips_gate_check(self, tmp_path, monkeypatch):
        """(l) declared class also skips gate-check: stub has verify passing
        and gate-check failing, yet the declared commit succeeds."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)
        _install_hook(tmp_path)

        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 1\n",
            env_extra={"FORGE_COMMIT_CLASS": "config"},
        )
        assert result.returncode == 0, (
            f"expected declared commit to skip gate-check: {result.stderr}"
        )

    def test_declared_commit_still_hits_nonascii_gate(
            self, tmp_path, monkeypatch):
        """(m) declared class does not bypass the staged-diff text gates:
        an em dash in the code file still blocks the commit."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)
        _install_hook(tmp_path)

        code = "x = 1  # plain comment \u2014 with dash\n"
        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", code,
            env_extra={"FORGE_COMMIT_CLASS": "chore"},
        )
        assert result.returncode != 0, (
            "em dash in a declared-chore commit must still fail the "
            f"non-ASCII gate; got rc={result.returncode}: {result.stderr}"
        )
        assert "non-ASCII" in result.stderr

    def test_invalid_class_falls_through_to_full_gate(
            self, tmp_path, monkeypatch):
        """(n) a mistyped class name is not a declaration: the full gate
        runs and the failing stub blocks the commit."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub_verify_fails(bin_dir)
        _install_hook(tmp_path)

        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 1\n",
            env_extra={"FORGE_COMMIT_CLASS": "chore-todo"},
        )
        assert result.returncode != 0

    def test_declared_commit_still_runs_presubmit(
            self, tmp_path, monkeypatch):
        """(o) declared class does not skip the presubmit linters: a
        configured linter whose command fails blocks a declared commit."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)
        _install_hook(tmp_path, presubmit_entries=[_ENTRY_FAILING_SUBMIT])

        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 1\n",
            env_extra={"FORGE_COMMIT_CLASS": "chore"},
        )
        assert result.returncode != 0, (
            "declared commit must still run presubmit linters; "
            f"got rc={result.returncode}: {result.stderr}"
        )

    def test_declared_commit_skips_review_block(
            self, tmp_path, monkeypatch):
        """(p) review is part of the full gate and skipped when declared:
        stub fails only review, so undeclared commits block and declared
        commits pass."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub_review_fails(bin_dir)
        _install_hook(tmp_path)

        undeclared = _stage_and_commit(tmp_path, bin_dir, "app.py", "x = 1\n")
        assert undeclared.returncode != 0
        assert "review unreachable" in undeclared.stderr

        declared = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 2\n",
            env_extra={"FORGE_COMMIT_CLASS": "chore"},
        )
        assert declared.returncode == 0, (
            f"declared commit must skip review: {declared.stderr}"
        )

    def test_declared_commit_still_hits_ai_vocab_gate(
            self, tmp_path, monkeypatch):
        """(q) declared class does not bypass the AI-vocabulary gate either;
        a banned word in the diff still blocks a declared commit."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub(bin_dir)
        _install_hook(tmp_path)

        code = "# moreover the fix is obvious\nx = 1\n"
        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", code,
            env_extra={"FORGE_COMMIT_CLASS": "chore"},
        )
        assert result.returncode != 0, (
            "AI vocabulary in a declared-chore commit must still fail "
            f"the staged-diff gate; got rc={result.returncode}"
        )
        assert "AI" in result.stderr

    def test_chain_variant_contains_declared_blocks(self):
        """(r) the chained-hook variant assembles the declared-class blocks
        as well as the plain variant."""
        content = generate_hook_content(
            "code-forge gate-check", Path("/tmp/old"),
        )
        assert "FORGE_COMMIT_CLASS" in content
        assert "_FORGE_DECLARED" in content
        assert "/tmp/old" in content

    def test_docs_class_value_also_declares(self, tmp_path, monkeypatch):
        """(s) classes other than chore/config follow the same path;
        docs declares just as well."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES", str(tmp_path.parent), prepend=os.pathsep,
        )
        _init_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        _make_stub_verify_fails(bin_dir)
        _install_hook(tmp_path)

        result = _stage_and_commit(
            tmp_path, bin_dir, "app.py", "x = 1\n",
            env_extra={"FORGE_COMMIT_CLASS": "docs"},
        )
        assert result.returncode == 0, (
            f"declared docs commit must pass: {result.stderr}"
        )
