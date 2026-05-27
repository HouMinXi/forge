# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for the install-skill subcommand."""

import types

from code_forge.cli import _run_install_skill
from code_forge.exit_codes import EXIT_CLI_ERROR, EXIT_PASS


def _make_args(
    target="claude",
    dest=None,
    skill=None,
    force=False,
    quiet=True,
):
    return types.SimpleNamespace(
        target=target,
        dest=dest,
        skill=skill,
        force=force,
        quiet=quiet,
    )


class TestTargetResolution:
    """Target flag selects the correct destination root."""

    def test_target_claude_uses_home_claude_skills(self, tmp_path, monkeypatch):
        """--target claude resolves to <home>/.claude/skills/."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        # Monkeypatch Path.home() via os.environ HOME
        result = _run_install_skill(
            _make_args(target="claude"), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert (tmp_path / ".claude" / "skills").is_dir()

    def test_target_vscode_uses_cwd_claude_skills(self, tmp_path, monkeypatch):
        """--target vscode resolves to <cwd>/.claude/skills/."""
        result = _run_install_skill(
            _make_args(target="vscode"), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert (tmp_path / ".claude" / "skills").is_dir()

    def test_target_universal_uses_cwd_agents_skills(self, tmp_path, monkeypatch):
        """--target universal resolves to <cwd>/.agents/skills/."""
        result = _run_install_skill(
            _make_args(target="universal"), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert (tmp_path / ".agents" / "skills").is_dir()


class TestDestOverride:
    """--dest overrides --target."""

    def test_dest_overrides_target(self, tmp_path):
        """--dest uses the explicit path regardless of --target."""
        custom = tmp_path / "custom" / "skills"
        result = _run_install_skill(
            _make_args(target="claude", dest=str(custom)), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert custom.is_dir()

    def test_dest_creates_parent_dirs(self, tmp_path):
        """--dest creates intermediate directories as needed."""
        deep = tmp_path / "a" / "b" / "c" / "skills"
        result = _run_install_skill(
            _make_args(dest=str(deep)), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert deep.is_dir()


class TestSkillCopy:
    """Skills are copied into <dest>/<skill>/SKILL.md."""

    def test_default_installs_all_bundled_skills(self, tmp_path):
        """Default (no --skill) installs all bundled skills."""
        result = _run_install_skill(
            _make_args(dest=str(tmp_path)), cwd=tmp_path
        )
        assert result == EXIT_PASS
        # All skills directory must contain SKILL.md
        skill_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(skill_dirs) >= 6  # 6 bundled skills
        for skill_dir in skill_dirs:
            assert (skill_dir / "SKILL.md").exists(), (
                "Missing SKILL.md in %s" % skill_dir
            )

    def test_named_skill_creates_skill_md(self, tmp_path):
        """--skill code-forge creates <dest>/code-forge/SKILL.md."""
        result = _run_install_skill(
            _make_args(dest=str(tmp_path), skill="code-forge"), cwd=tmp_path
        )
        assert result == EXIT_PASS
        assert (tmp_path / "code-forge" / "SKILL.md").exists()

    def test_named_skill_only_installs_that_skill(self, tmp_path):
        """--skill installs exactly one skill directory."""
        result = _run_install_skill(
            _make_args(dest=str(tmp_path), skill="qodo-review"), cwd=tmp_path
        )
        assert result == EXIT_PASS
        installed = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(installed) == 1
        assert installed[0].name == "qodo-review"


class TestForceFlag:
    """--force overwrites existing skill directories."""

    def test_existing_dest_without_force_is_skipped(self, tmp_path):
        """Existing <dest>/<skill> without --force is skipped, not overwritten."""
        # Pre-create the skill dir with a sentinel file
        skill_dir = tmp_path / "code-forge"
        skill_dir.mkdir()
        sentinel = skill_dir / "sentinel.txt"
        sentinel.write_text("original")

        result = _run_install_skill(
            _make_args(dest=str(tmp_path), skill="code-forge", force=False),
            cwd=tmp_path,
        )
        assert result == EXIT_PASS
        # Sentinel must still exist (SKIP)
        assert sentinel.exists()
        assert sentinel.read_text() == "original"

    def test_existing_dest_with_force_is_replaced(self, tmp_path):
        """Existing <dest>/<skill> with --force is removed and replaced."""
        skill_dir = tmp_path / "code-forge"
        skill_dir.mkdir()
        sentinel = skill_dir / "sentinel.txt"
        sentinel.write_text("original")

        result = _run_install_skill(
            _make_args(dest=str(tmp_path), skill="code-forge", force=True),
            cwd=tmp_path,
        )
        assert result == EXIT_PASS
        # Sentinel is gone; SKILL.md is in place
        assert not sentinel.exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_force_second_install_is_idempotent(self, tmp_path):
        """Two --force installs produce the same result."""
        for _ in range(2):
            result = _run_install_skill(
                _make_args(
                    dest=str(tmp_path), skill="code-forge", force=True
                ),
                cwd=tmp_path,
            )
        assert result == EXIT_PASS
        assert (tmp_path / "code-forge" / "SKILL.md").exists()


class TestUnknownSkill:
    """Unknown --skill name exits with 2 (CLI_ERROR)."""

    def test_unknown_skill_exits_2(self, tmp_path):
        """--skill nonexistent-skill-xyz returns EXIT_CLI_ERROR."""
        result = _run_install_skill(
            _make_args(dest=str(tmp_path), skill="nonexistent-skill-xyz"),
            cwd=tmp_path,
        )
        assert result == EXIT_CLI_ERROR


class TestBundledSkillsAccessible:
    """importlib.resources can locate bundled skills."""

    def test_importlib_resources_finds_code_forge_skill(self):
        """importlib.resources can find code-forge/SKILL.md in bundled skills."""
        from importlib.resources import files as _pkg_files

        src_root = _pkg_files("code_forge") / "skills"
        skill_file = src_root / "code-forge" / "SKILL.md"
        # Traversable.read_bytes() works for both installed and source layouts
        content = skill_file.read_bytes()
        assert len(content) > 0
        # SKILL.md must contain meaningful content
        assert b"SKILL.md" in content or b"skill" in content.lower()

    def test_importlib_resources_lists_all_six_skills(self):
        """All 6 skills are discoverable via importlib.resources."""
        from importlib.resources import files as _pkg_files

        src_root = _pkg_files("code_forge") / "skills"
        skill_names = sorted(
            entry.name for entry in src_root.iterdir() if entry.is_dir()
        )
        expected = {
            "adversarial-qe",
            "code-forge",
            "code-review-expert",
            "kernel-fp-verify",
            "qodo-review",
            "smoke-test",
        }
        assert expected.issubset(set(skill_names))
