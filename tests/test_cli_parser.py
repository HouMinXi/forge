# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Argparse surface tests for forge CLI subcommands."""

import pytest

from code_forge.cli import _build_parser


class TestParserDefaults:
    """Bare invocation defaults."""

    def test_bare_invocation_defaults(self):
        """Bare forge (no subcommand) has subcommand=None."""
        parser = _build_parser()
        args = parser.parse_args([])
        # Subparser structure: no subcommand specified
        assert args.subcommand is None

    def test_no_subcommand_defaults_review(self):
        """Backward compat: bare forge maps to review in main()."""
        # This is tested in main() logic, not parser.
        # Parser returns subcommand=None; main() maps None -> 'review'.
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.subcommand is None
        # main() will set: if args.subcommand is None: args.subcommand = 'review'

    def test_review_subcommand_explicit(self):
        """Explicit 'forge review' sets subcommand='review'."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.subcommand == 'review'

    def test_review_subcommand_defaults(self):
        """Review subcommand defaults match old forge defaults."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.mode is None
        assert args.falsification_engine is None
        assert args.sandbox is False
        assert args.baseline is None
        assert args.head is None
        assert args.registry == ".code-forge/tools.yaml"
        assert args.state_dir is None
        assert args.max_total_rounds is None
        assert args.max_fix_attempts is None
        assert args.quiet is False
        assert args.staged is False
        assert args.paths == []


class TestParserAllFlags:
    """All flags set -> values propagate."""

    def test_all_flags_set(self):
        """All review flags populated."""
        parser = _build_parser()
        args = parser.parse_args([
            "review",  # explicit subcommand
            "--mode", "ci",
            "--falsification-engine", "stub",
            "--sandbox",
            "--baseline", "abc123",
            "--head", "WORKING",
            "--registry", "custom.yaml",
            "--state-dir", "/tmp/state",
            "--max-total-rounds", "50",
            "--max-fix-attempts", "10",
            "--quiet",
            "--staged",
            "a.py", "b.py",
        ])
        assert args.subcommand == "review"
        assert args.mode == "ci"
        assert args.falsification_engine == "stub"
        assert args.sandbox is True
        assert args.baseline == "abc123"
        assert args.head == "WORKING"
        assert args.registry == "custom.yaml"
        assert args.state_dir == "/tmp/state"
        assert args.max_total_rounds == 50
        assert args.max_fix_attempts == 10
        assert args.quiet is True
        assert args.staged is True
        assert args.paths == ["a.py", "b.py"]

    def test_review_flags_preserved(self):
        """Review subcommand preserves all existing flags."""
        parser = _build_parser()
        args = parser.parse_args([
            'review', '--mode', 'local', '--baseline', 'HEAD'
        ])
        assert args.subcommand == 'review'
        assert args.mode == 'local'
        assert args.baseline == 'HEAD'


class TestParserInvalidChoices:
    """Invalid choices -> argparse exit 2."""

    def test_invalid_mode_exits_2(self):
        """--mode invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["review", "--mode", "invalid"])
        assert exc_info.value.code == 2

    def test_invalid_engine_exits_2(self):
        """--falsification-engine invalid -> exit 2."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([
                "review", "--falsification-engine", "invalid"
            ])
        assert exc_info.value.code == 2


class TestParserHelp:
    """--help includes Exit codes section."""

    def test_help_includes_exit_codes(self, capsys):
        """--help epilog lists exit codes 0/1/2/3/4."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Exit codes:" in captured.out
        assert "0  PASS" in captured.out
        assert "1  FAIL" in captured.out
        assert "2  CLI_ERROR" in captured.out
        assert "3  BUSY" in captured.out
        assert "4  ESCALATED" in captured.out


class TestParserVersion:
    """--version prints forge <version> + exits 0."""

    def test_version_exits_zero(self, capsys):
        """--version exits 0."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("code-forge ")


class TestSubcommands:
    """Subcommand routing tests."""

    def test_gate_check_subcommand(self):
        """gate-check subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['gate-check'])
        assert args.subcommand == 'gate-check'
        assert args.quiet is False

    def test_gate_check_quiet(self):
        """gate-check --quiet flag."""
        parser = _build_parser()
        args = parser.parse_args(['gate-check', '--quiet'])
        assert args.subcommand == 'gate-check'
        assert args.quiet is True

    def test_install_hooks_subcommand(self):
        """install-hooks subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['install-hooks'])
        assert args.subcommand == 'install-hooks'
        assert args.quiet is False

    def test_install_hooks_quiet(self):
        """install-hooks --quiet flag."""
        parser = _build_parser()
        args = parser.parse_args(['install-hooks', '--quiet'])
        assert args.subcommand == 'install-hooks'
        assert args.quiet is True

    def test_mutation_check_subcommand(self):
        """mutation-check subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['mutation-check'])
        assert args.subcommand == 'mutation-check'
        assert args.diff is None
        assert args.timeout == 600
        assert args.paths is None

    def test_e2e_check_subcommand(self):
        """e2e-check subcommand parses correctly."""
        parser = _build_parser()
        args = parser.parse_args(['e2e-check'])
        assert args.subcommand == 'e2e-check'
        assert args.diff is None
        assert args.repo_root is None

    def test_all_five_subcommands_in_help(self, capsys):
        """All 5 subcommands appear in top-level --help."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(['--help'])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        for cmd in ('review', 'gate-check', 'mutation-check',
                    'e2e-check', 'install-hooks'):
            assert cmd in captured.out, (
                "Expected %r in --help output" % cmd
            )


class TestOutletAndCommittedFlags:
    """Tests for --outlet and --committed flags (Phase 07-02)."""

    def test_outlet_flag_default(self):
        """--outlet not specified -> None."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.outlet is None

    def test_outlet_flag_subprocess(self):
        """--outlet subprocess -> 'subprocess' (canonical value)."""
        parser = _build_parser()
        args = parser.parse_args(['review', '--outlet', 'subprocess'])
        assert args.outlet == 'subprocess'

    def test_outlet_flag_cli(self):
        """--outlet cli -> 'cli' (deprecated alias; argparse accepts it)."""
        parser = _build_parser()
        args = parser.parse_args(['review', '--outlet', 'cli'])
        assert args.outlet == 'cli'

    def test_outlet_flag_inline(self):
        """--outlet inline -> 'inline'."""
        parser = _build_parser()
        args = parser.parse_args(['review', '--outlet', 'inline'])
        assert args.outlet == 'inline'

    def test_outlet_flag_invalid_rejected(self):
        """--outlet invalid raises SystemExit (argparse validation)."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['review', '--outlet', 'invalid'])

    def test_committed_flag_default(self):
        """--committed not specified -> False."""
        parser = _build_parser()
        args = parser.parse_args(['review'])
        assert args.committed is False

    def test_committed_flag_set(self):
        """--committed specified -> True."""
        parser = _build_parser()
        args = parser.parse_args(['review', '--committed'])
        assert args.committed is True

    def test_outlet_and_committed_together(self):
        """--outlet and --committed can be used together (parser allows)."""
        parser = _build_parser()
        args = parser.parse_args(['review', '--outlet', 'cli', '--committed'])
        assert args.outlet == 'cli'
        assert args.committed is True

    def test_committed_rejects_baseline_at_runtime(self, tmp_path):
        """--committed + --baseline raises CliError in _build_baseline_specs."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            committed=True, baseline="HEAD~2", head=None, staged=False
        )
        with pytest.raises(CliError, match="--committed cannot be combined with --baseline"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_committed_rejects_head_at_runtime(self, tmp_path):
        """--committed + --head raises CliError in _build_baseline_specs."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            committed=True, baseline=None, head="HEAD", staged=False
        )
        with pytest.raises(CliError, match="--committed cannot be combined with --head"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_committed_rejects_staged_at_runtime(self, tmp_path):
        """--committed + --staged raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            committed=True, baseline=None, head=None, staged=True
        )
        with pytest.raises(CliError, match="--committed and --staged are mutually exclusive"):
            _build_baseline_specs(args, cwd=tmp_path)


# -- TestWholeFileFlag -----------------------------------------------------


class TestWholeFileFlag:
    """Tests for --whole-file flag (alias for --baseline empty PATH)."""

    def test_whole_file_default_none(self):
        """--whole-file not specified -> None."""
        parser = _build_parser()
        args = parser.parse_args(["review"])
        assert args.whole_file is None

    def test_whole_file_maps_to_empty_baseline_non_git(self, tmp_path):
        """--whole-file in non-git dir -> EmptyBaseline + head=None."""
        from code_forge.baseline import EmptyBaseline
        from code_forge.cli import _build_baseline_specs
        import argparse
        args = argparse.Namespace(
            whole_file=["some/file.py"], baseline=None,
            head=None, committed=False, staged=False, paths=[],
        )
        baseline, head = _build_baseline_specs(args, cwd=tmp_path)
        assert isinstance(baseline, EmptyBaseline)
        assert head is None

    def test_whole_file_maps_to_working_head_in_git(self, tmp_path):
        """--whole-file in git repo -> EmptyBaseline + WORKING head."""
        from code_forge.baseline import EmptyBaseline, GitRefBaseline
        from code_forge.cli import _build_baseline_specs
        import argparse, subprocess
        subprocess.run(
            ["git", "init", str(tmp_path)],
            check=True, capture_output=True,
        )
        args = argparse.Namespace(
            whole_file=["some/file.py"], baseline=None,
            head=None, committed=False, staged=False, paths=[],
        )
        baseline, head = _build_baseline_specs(args, cwd=tmp_path)
        assert isinstance(baseline, EmptyBaseline)
        assert isinstance(head, GitRefBaseline)
        assert head.ref == "WORKING"

    def test_whole_file_rejects_baseline(self, tmp_path):
        """--whole-file + --baseline raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            whole_file=["f.py"], baseline="HEAD",
            head=None, committed=False, staged=False, paths=[],
        )
        with pytest.raises(CliError, match="--whole-file cannot be combined with --baseline"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_whole_file_rejects_committed(self, tmp_path):
        """--whole-file + --committed raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            whole_file=["f.py"], baseline=None,
            head=None, committed=True, staged=False, paths=[],
        )
        with pytest.raises(CliError, match="--whole-file cannot be combined with --committed"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_whole_file_rejects_head(self, tmp_path):
        """--whole-file + --head raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            whole_file=["f.py"], baseline=None,
            head="HEAD", committed=False, staged=False, paths=[],
        )
        with pytest.raises(CliError, match="--whole-file cannot be combined with --head"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_whole_file_rejects_positional_paths(self, tmp_path):
        """--whole-file + positional paths raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            whole_file=["f.py"], baseline=None,
            head=None, committed=False, staged=False,
            paths=["other.py"],
        )
        with pytest.raises(CliError, match="--whole-file cannot be combined with positional paths"):
            _build_baseline_specs(args, cwd=tmp_path)

    def test_whole_file_rejects_staged(self, tmp_path):
        """--whole-file + --staged raises CliError."""
        from code_forge.cli import _build_baseline_specs
        from code_forge.errors import CliError
        import argparse
        args = argparse.Namespace(
            whole_file=["f.py"], baseline=None,
            head=None, committed=False, staged=True, paths=[],
        )
        with pytest.raises(CliError, match="--whole-file cannot be combined with --staged"):
            _build_baseline_specs(args, cwd=tmp_path)
