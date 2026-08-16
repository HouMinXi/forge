# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Review startup banner: repo@sha, diff size, effective LLM timeout.

Pains 4 and 8 from the surflare usage report (2026-08-16): the
effective FORGE_LLM_TIMEOUT_S budget was only discoverable from a
failure message, and a wrong-cwd launch was silent until the first
deadline. One stderr line at review start fixes both.
"""

import subprocess
import sys
from unittest.mock import patch

from code_forge.cli import (
    _banner_timeout_note,
    _repo_display_name,
    _startup_banner_line,
    main,
)
from code_forge.exit_codes import EXIT_PASS
from code_forge.state import Verdict


class TestStartupBannerLine:
    def test_full_line_without_env_override(self):
        line = _startup_banner_line(
            repo_name="repo", sha="abc12345", diff_count=3,
            mode="local", backend_name="deepseek-nocache", timeout_s=2400,
        )
        assert line == (
            "code-forge: reviewing repo @ abc12345 (diff: 3 files); "
            "mode: local; backend: deepseek-nocache; LLM timeout: 2400s"
        )
        assert "FORGE_LLM_TIMEOUT_S" not in line

    def test_timeout_note_appended(self):
        line = _startup_banner_line(
            repo_name="repo", sha="abc12345", diff_count=3,
            mode="local", backend_name="deepseek-nocache", timeout_s=5400,
            timeout_note=" (from FORGE_LLM_TIMEOUT_S)",
        )
        assert "LLM timeout: 5400s (from FORGE_LLM_TIMEOUT_S)" in line

    def test_backend_name_control_chars_stripped(self):
        """A hostile backend name in config must not corrupt the line."""
        line = _startup_banner_line(
            repo_name="repo", sha="abc12345", diff_count=1,
            mode="ci", backend_name="evil\x1b[31mbackend", timeout_s=1800,
        )
        assert "\x1b" not in line
        assert "evil[31mbackend" in line

    def test_sha_control_chars_stripped(self):
        """A --head-supplied sha with escapes must not corrupt the line."""
        line = _startup_banner_line(
            repo_name="repo", sha="abc12\x1b345", diff_count=1,
            mode="ci", backend_name="b", timeout_s=1800,
        )
        assert "\x1b" not in line
        assert "abc12345" in line

    def test_singular_diff(self):
        line = _startup_banner_line(
            repo_name="repo", sha="abc12345", diff_count=1,
            mode="ci", backend_name="none", timeout_s=1800,
        )
        assert "(diff: 1 file)" in line
        assert "(diff: 1 files)" not in line

    def test_missing_sha_omits_at_sign(self):
        line = _startup_banner_line(
            repo_name="repo", sha="", diff_count=0,
            mode="ci", backend_name="none", timeout_s=1800,
        )
        assert "reviewing repo (diff:" in line
        assert "repo @ " not in line

    def test_unknown_values_render_n_a(self):
        line = _startup_banner_line(
            repo_name="repo", sha="abc12345", diff_count=None,
            mode="ci", backend_name="none", timeout_s=None,
        )
        assert "diff: n/a" in line
        assert "LLM timeout: n/a" in line
        assert "FORGE_LLM_TIMEOUT_S" not in line


class TestBannerTimeoutNote:
    def _backend(self, ts):
        class _B:
            name = "b"
            timeout_s = ts
            type = "api"
        return _B()

    def test_env_unset_returns_empty(self):
        assert _banner_timeout_note(self._backend(2400), None) == ""

    def test_backend_none_env_set(self):
        note = _banner_timeout_note(None, "5400")
        assert note == " (from FORGE_LLM_TIMEOUT_S)"

    def test_backend_timeout_unset_env_set(self):
        note = _banner_timeout_note(self._backend(0), "5400")
        assert note == " (from FORGE_LLM_TIMEOUT_S)"

    def test_backend_timeout_wins_says_ignored(self):
        note = _banner_timeout_note(self._backend(2400), "5400")
        assert "ignored" in note
        assert "5400" in note
        assert "backend timeout wins" in note

    def test_control_characters_never_reach_the_banner(self):
        """A value carrying escapes is not a valid integer, so no note
        is produced at all -- nothing hostile reaches the banner."""
        note = _banner_timeout_note(self._backend(2400), "5400\x1b[31m")
        assert note == ""
        assert "\x1b" not in note

    def test_empty_env_value_treated_as_unset(self):
        """An empty FORGE_LLM_TIMEOUT_S falls back to the default in the
        resolver; the banner must not claim the override."""
        assert _banner_timeout_note(self._backend(0), "") == ""
        assert _banner_timeout_note(self._backend(0), "   ") == ""

    def test_invalid_env_value_treated_as_unset(self):
        """Only a positive integer is honored by the resolver; anything
        else must not be claimed as the timeout source."""
        assert _banner_timeout_note(self._backend(0), "abc") == ""
        assert _banner_timeout_note(self._backend(0), "0") == ""
        assert _banner_timeout_note(self._backend(0), "-5") == ""
        assert _banner_timeout_note(
            self._backend(0), "5400"
        ) == " (from FORGE_LLM_TIMEOUT_S)"


class TestRepoDisplayName:
    def test_subdir_resolves_toplevel_name(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "sub").mkdir(parents=True)
        subprocess.run(
            ["git", "init"], cwd=str(repo), capture_output=True, check=True,
        )
        monkeypatch.chdir(str(repo / "sub"))
        assert _repo_display_name(repo / "sub") == "repo"

    def test_non_git_falls_back_to_dir_name(self, tmp_path, monkeypatch):
        d = tmp_path / "plain-dir"
        d.mkdir()
        monkeypatch.chdir(str(d))
        assert _repo_display_name(d) == "plain-dir"

    def test_missing_git_binary_falls_back(self, tmp_path, monkeypatch):
        """A git-less machine raises FileNotFoundError (an OSError, not
        a SubprocessError) from subprocess.run; the banner must fall
        back instead of crashing."""
        d = tmp_path / "plain-dir"
        d.mkdir()

        def _no_git(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr("code_forge.cli.subprocess.run", _no_git)
        assert _repo_display_name(d) == "plain-dir"

    def test_git_probe_carries_timeout(self, tmp_path, monkeypatch):
        """A hung git must not hang the startup banner."""
        d = tmp_path / "plain-dir"
        d.mkdir()
        seen = {}

        def _capture(*args, **kwargs):
            seen.update(kwargs)
            raise FileNotFoundError("git")

        monkeypatch.setattr("code_forge.cli.subprocess.run", _capture)
        _repo_display_name(d)
        assert seen.get("timeout") == 5

    def test_repo_name_control_chars_stripped(self, tmp_path, monkeypatch):
        """A directory name with escape sequences must not corrupt the
        banner line."""
        d = tmp_path / "evil\x1b[31mdir"
        d.mkdir()
        monkeypatch.chdir(str(d))
        name = _repo_display_name(d)
        assert "\x1b" not in name
        # Only the ESC control char is stripped; the printable
        # "[31m" remainder of the ANSI sequence stays.
        assert "evil[31mdir" in name


class TestBannerCallSite:
    """The banner must print in _run itself -- a helper that formats but
    is never called would leave the pain unfixed."""

    def test_review_prints_banner(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text('"""Clean module."""\nX = 1\n')
        subprocess.run(
            ["git", "add", "-A"], cwd=str(repo), capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        (repo / "a.py").write_text('"""Clean module."""\nX = 2\n')

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--falsification-engine", "stub",
             "--mode", "ci", "a.py"],
        )
        monkeypatch.setattr(
            "code_forge.outlet_resolver.resolve_outlet",
            lambda *a, **kw: "subprocess",
        )
        monkeypatch.chdir(str(repo))
        with patch("code_forge.cli._run_hold_loop") as mock_loop:
            mock_loop.return_value = Verdict.PASS
            exit_code = main()
        assert exit_code == EXIT_PASS
        err = capsys.readouterr().err
        assert "reviewing repo @" in err, (
            "banner missing from stderr: %r" % err
        )
        assert "LLM timeout" in err
        # Env var unset: the banner must NOT claim an override.
        assert "FORGE_LLM_TIMEOUT_S" not in err

    def test_banner_names_env_override_when_set(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The env_override wiring at the call site: with
        FORGE_LLM_TIMEOUT_S set, the banner says so."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text('"""Clean module."""\nX = 1\n')
        subprocess.run(
            ["git", "add", "-A"], cwd=str(repo), capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        (repo / "a.py").write_text('"""Clean module."""\nX = 2\n')

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--falsification-engine", "stub",
             "--mode", "ci", "a.py"],
        )
        monkeypatch.setattr(
            "code_forge.outlet_resolver.resolve_outlet",
            lambda *a, **kw: "subprocess",
        )
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "5400")
        monkeypatch.chdir(str(repo))
        with patch("code_forge.cli._run_hold_loop") as mock_loop:
            mock_loop.return_value = Verdict.PASS
            exit_code = main()
        assert exit_code == EXIT_PASS
        assert "from FORGE_LLM_TIMEOUT_S" in capsys.readouterr().err

    def test_banner_survives_timeout_resolver_error(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The banner is diagnostics: a raising timeout resolver must
        degrade to n/a, not crash the review."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True, check=True,
        )
        forge_dir = repo / ".code-forge"
        forge_dir.mkdir()
        (forge_dir / "tools.yaml").write_text("tools: {}\n")
        (repo / "a.py").write_text('"""Clean module."""\nX = 1\n')
        subprocess.run(
            ["git", "add", "-A"], cwd=str(repo), capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=str(repo),
            capture_output=True, check=True,
        )
        (repo / "a.py").write_text('"""Clean module."""\nX = 2\n')

        monkeypatch.setattr(
            sys, "argv",
            ["code-forge", "--falsification-engine", "stub",
             "--mode", "ci", "a.py"],
        )
        monkeypatch.setattr(
            "code_forge.outlet_resolver.resolve_outlet",
            lambda *a, **kw: "subprocess",
        )
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "5400")
        monkeypatch.chdir(str(repo))
        with patch(
            "code_forge.llm_invoke.effective_invoke_timeout_s",
            side_effect=RuntimeError("boom"),
        ), patch("code_forge.cli._run_hold_loop") as mock_loop:
            mock_loop.return_value = Verdict.PASS
            exit_code = main()
        assert exit_code == EXIT_PASS
        err = capsys.readouterr().err
        assert "reviewing repo @" in err
        assert "LLM timeout: n/a" in err
