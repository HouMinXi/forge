"""Tests for baseline-interpreter mutmut resolution and diff-scoped config.

Regression coverage for the gxwiki failure mode: forge invoked a bare
PATH mutmut whose interpreter lacked the project dependencies, so every
review collected a CONFIRMED 'mutmut run failed (exit 1)' infra finding.
The mutmut subprocess must run under the SAME interpreter that owns the
baseline test command, and mutations must be scoped to the diff while
the whole source tree is mirrored for importability.
"""

import os
import subprocess
from unittest.mock import patch

from code_forge.disposition import Disposition
from code_forge.mutation import (
    _baseline_test_selection,
    _build_mutmut_config,
    _resolve_mutmut_invocation,
    _source_roots,
    run_mutation,
)


class TestSourceRoots:
    """Pure derivation of mirror roots from diff-scoped python files."""

    def test_src_layout_file_maps_to_top_dir(self):
        assert _source_roots(["src/pkg/mod.py"]) == ["src"]

    def test_flat_layout_file_maps_to_itself(self):
        assert _source_roots(["module.py"]) == ["module.py"]

    def test_windows_backslash_maps_to_top_dir(self):
        assert _source_roots([r"src\\pkg\\mod.py"]) == ["src"]

    def test_test_files_excluded(self):
        assert _source_roots(["tests/test_mod.py", "src/pkg/mod.py"]) == ["src"]

    def test_only_test_files_returns_empty(self):
        assert _source_roots(["tests/test_mod.py"]) == []

    def test_multiple_source_dirs_deduped_sorted(self):
        roots = _source_roots(["src/b.py", "lib/c.py", "src/a.py"])
        assert roots == ["lib", "src"]


class TestBuildMutmutConfig:
    """The generated setup.cfg mirrors the tree but mutates only the diff."""

    def test_config_scopes_mutation_to_diff_files(self):
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            ["src/pkg/mod.py"], ["pytest", "tests/test_mod.py", "-q"]
        )
        assert "source_paths=src" in cfg
        assert "only_mutate=src/pkg/mod.py" in cfg
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "pytest_add_cli_args_test_selection")
        assert [x for x in raw.split("\n") if x] == [
            "tests/test_mod.py", "-q", "-m", "not integration and not source_scan",
        ]

    def test_config_multiple_diff_files(self):
        cfg = _build_mutmut_config(
            ["src/a.py", "src/b.py"], ["pytest", "tests/", "-q"]
        )
        from configparser import ConfigParser

        parser = ConfigParser()
        parser.read_string(cfg)
        assert parser.get("mutmut", "only_mutate").splitlines() == [
            "src/a.py", "src/b.py",
        ]

    def test_config_excludes_test_files_from_only_mutate(self):
        """Mutating the test suite poisons stats collection.

        Observed on the SSE review: only_mutate listed
        tests/test_llm_invoke.py, mutmut rewrote header names in the
        assertion, and pytest -x aborted before any production mutant
        ran. Mirror roots already skip tests/; only_mutate must too.
        """
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            [
                "src/code_forge/llm_invoke.py",
                "tests/test_llm_invoke.py",
                r"tests\\test_win.py",
            ],
            ["pytest", "tests/test_llm_invoke.py", "-q"],
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        mutated = parser.get("mutmut", "only_mutate").splitlines()
        assert mutated == ["src/code_forge/llm_invoke.py"]
        assert "tests/test_llm_invoke.py" not in mutated
        assert r"tests\\test_win.py" not in mutated

    def test_config_excludes_absolute_test_paths_from_only_mutate(self):
        """Review source_files can be absolute Paths stringified.

        startswith('tests/') misses /repo/tests/foo.py, so mutmut
        still rewrites the test suite.
        """
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            [
                "/repo/src/code_forge/llm_invoke.py",
                "/repo/tests/test_llm_invoke.py",
            ],
            ["pytest", "tests/test_llm_invoke.py", "-q"],
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        mutated = parser.get("mutmut", "only_mutate").splitlines()
        assert mutated == ["/repo/src/code_forge/llm_invoke.py"]
        assert not any("tests/" in p for p in mutated)

    def test_config_refuses_empty_only_mutate(self):
        """A bare only_mutate= is not 'mutate nothing'.

        mutmut may treat an empty value as 'mutate everything under
        source_paths'. The helper must refuse that shape so the
        tests-only skip in run_mutation stays the only no-op path.
        """
        import pytest

        with pytest.raises(ValueError, match="no production files"):
            _build_mutmut_config(
                ["tests/test_mod.py", r"tests\\test_win.py"],
                ["pytest", "tests/", "-q"],
            )

    def test_config_flat_layout(self):
        cfg = _build_mutmut_config(["module.py"], ["pytest"])
        assert "source_paths=module.py" in cfg
        assert "only_mutate=module.py" in cfg

    def test_config_drops_blank_also_copy_entries(self):
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            ["src/pkg/mod.py"],
            ["pytest", "tests/"],
            also_copy=["docs/", "  ", "", "deploy/"],
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "also_copy")
        assert raw.split("\n") == ["docs/", "deploy/"]

    def test_config_includes_also_copy_when_given(self):
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            ["src/pkg/mod.py"], ["pytest", "tests/"], also_copy=["docs/", "deploy/"]
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "also_copy")
        # Empty "also_copy=" plus indented continuations parses, but
        # leaves a leading newline. First path belongs on the key line.
        assert not raw.startswith("\n")
        assert [x for x in raw.split("\n") if x] == ["docs/", "deploy/"]

    def test_config_skips_empty_also_copy_entries(self):
        from configparser import ConfigParser

        cfg = _build_mutmut_config(
            ["src/pkg/mod.py"], ["pytest", "tests/"], also_copy=["", "docs/", ""]
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "also_copy")
        assert not raw.startswith("\n")
        assert [x for x in raw.split("\n") if x] == ["docs/"]

    def test_config_strips_interpreter_prefix_from_selection(self):
        # [python3, -m, pytest, tests/]: only "tests/" is a pytest
        # argument; "-m pytest" leaking into selection would be
        # parsed as a marker expression.
        cfg = _build_mutmut_config(
            ["src/add.py"], ["python3", "-m", "pytest", "tests/"]
        )
        selection = cfg.split("test_selection=")[1]
        assert selection.startswith("tests/")
        # The interpreter prefix "-m pytest" must not leak into the
        # selection; the only "-m" allowed is the trailing marker
        # exclusion the resource guard appends.
        from configparser import ConfigParser

        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "pytest_add_cli_args_test_selection")
        assert [x for x in raw.split("\n") if x] == [
            "tests/", "-m", "not integration and not source_scan",
        ]


class TestBaselineTestSelection:
    """Interpreter flags must not leak into pytest_add_cli_args_test_selection."""

    def test_direct_pytest_keeps_args(self):
        assert _baseline_test_selection(["pytest", "tests/", "-q"]) == ["tests/", "-q"]

    def test_venv_pytest_keeps_args(self):
        assert _baseline_test_selection(
            ["/proj/.venv/bin/pytest", "tests/"]
        ) == ["tests/"]

    def test_python_dash_m_pytest_strips_prefix(self):
        assert _baseline_test_selection(
            ["python3", "-m", "pytest", "tests/"]
        ) == ["tests/"]

    def test_non_pytest_runner_returns_empty(self):
        # Fallback used to return baseline_cmd[1:], leaking "-m unittest"
        # into mutmut's pytest_add_cli_args_test_selection.
        assert _baseline_test_selection(["python", "-m", "unittest"]) == []

    def test_empty_command_returns_empty(self):
        assert _baseline_test_selection([]) == []

    def test_windows_pytest_exe_keeps_args(self):
        assert _baseline_test_selection(
            [r"C:\\proj\\.venv\\Scripts\\pytest.exe", "tests/", "-q"]
        ) == ["tests/", "-q"]

    def test_windows_forward_slash_pytest_exe_keeps_args(self):
        assert _baseline_test_selection(
            ["C:/proj/.venv/Scripts/pytest.exe", "tests/"]
        ) == ["tests/"]


class TestResolveMutmutInvocation:
    """The interpreter that owns baseline_cmd must own the mutmut subprocess."""

    @patch("code_forge.mutation.subprocess.run")
    def test_venv_baseline_uses_sibling_python(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        cmd = _resolve_mutmut_invocation(["/proj/.venv/bin/pytest", "tests/", "-q"])
        assert cmd == ["/proj/.venv/bin/python", "-m", "mutmut"]
        probe = mock_run.call_args_list[0][0][0]
        assert probe[:2] == ["/proj/.venv/bin/python", "-c"]

    @patch("code_forge.mutation.subprocess.run")
    def test_venv_without_mutmut_returns_none(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No module named mutmut"
        )
        cmd = _resolve_mutmut_invocation(["/proj/.venv/bin/pytest", "tests/"])
        assert cmd is None

    @patch("code_forge.mutation.subprocess.run")
    def test_venv_probe_timeout_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["python", "-c"], timeout=30)
        try:
            _resolve_mutmut_invocation(["/proj/.venv/bin/pytest", "tests/"])
        except subprocess.TimeoutExpired:
            return
        raise AssertionError("probe timeout must not look like mutmut missing")

    @patch("code_forge.mutation.subprocess.run")
    def test_windows_pytest_exe_uses_python_exe(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        cmd = _resolve_mutmut_invocation(
            ["/proj/.venv/Scripts/pytest.exe", "tests/"]
        )
        assert cmd == ["/proj/.venv/Scripts/python.exe", "-m", "mutmut"]

    @patch("code_forge.mutation.subprocess.run")
    def test_trailing_separator_keeps_dirpart(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        cmd = _resolve_mutmut_invocation(["/proj/.venv/bin/", "tests/"])
        assert cmd == ["/proj/.venv/bin/python", "-m", "mutmut"]

    @patch("code_forge.mutation.subprocess.run")
    def test_windows_python3_exe_keeps_python3_exe(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        cmd = _resolve_mutmut_invocation(
            [r"C:\\proj\\.venv\\Scripts\\python3.exe", "-m", "pytest", "tests/"]
        )
        assert cmd == [
            r"C:\\proj\\.venv\\Scripts\\python3.exe", "-m", "mutmut"
        ]

    @patch("code_forge.mutation.shutil.which", return_value="/usr/bin/mutmut")
    def test_bare_pytest_keeps_path_resolution(self, mock_which):
        cmd = _resolve_mutmut_invocation(["pytest", "tests/"])
        assert cmd == ["mutmut"]

    @patch("code_forge.mutation.shutil.which", return_value=None)
    def test_bare_pytest_without_mutmut_returns_none(self, mock_which):
        assert _resolve_mutmut_invocation(["pytest"]) is None


class TestRunMutationVenvBaseline:
    """End-to-end: venv baseline drives the interpreter, config, and env."""

    @patch("code_forge.mutation.subprocess.run")
    def test_venv_probe_timeout_skips_as_timeout_not_missing(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and cmd[1:2] == ["-c"]:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, _infra = run_mutation(
            ["src/pkg/mod.py"], ["/proj/.venv/bin/pytest", "tests/", "-q"]
        )
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].fingerprint == "mutation-probe-timeout"
        assert "timed out" in findings[0].description
        assert findings[0].fingerprint != "mutation-unavailable"

    @patch("code_forge.mutation.subprocess.run")
    def test_venv_without_mutmut_skips_dismissed_not_confirmed(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and cmd[1:2] == ["-c"]:
                return subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="No module named mutmut"
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        findings, _infra = run_mutation(
            ["src/pkg/mod.py"], ["/proj/.venv/bin/pytest", "tests/", "-q"]
        )
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "baseline" in findings[0].description
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert not (isinstance(cmd, list) and "-m" in cmd and "mutmut" in cmd)

    @patch("code_forge.mutation.subprocess.run")
    def test_mutmut_run_inherits_baseline_pythonpath(self, mock_run, tmp_path):
        # mutmut 3.x rewrites sys.path after it has built mutants/;
        # forging PYTHONPATH=mutants/src races a directory that
        # does not yet exist and breaks collection. Pin cwd: this
        # test itself runs from mutants/ during a mutation stats
        # pass, and Path.cwd()/src then contains "mutants/" even
        # though the constructed path is still <repo>/src.
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, list) and "results" in cmd:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        run_mutation(
            ["src/pkg/mod.py"],
            ["/proj/.venv/bin/pytest", "tests/", "-q"],
            cwd=tmp_path,
        )

        mutmut_calls = [
            c
            for c in mock_run.call_args_list
            if isinstance(c[0][0], list) and c[0][0][1:4] == ["-m", "mutmut", "run"]
        ]
        assert len(mutmut_calls) == 1
        pythonpath = mutmut_calls[0][1]["env"]["PYTHONPATH"]
        posix = pythonpath.replace("\\", "/")
        assert posix == str(tmp_path / "src").replace("\\", "/")
        assert "mutants/" not in posix

    def test_tests_only_diff_skips(self):
        findings, infra = run_mutation(
            ["tests/test_cli.py"], ["pytest", "tests/test_cli.py"]
        )
        assert len(findings) == 1
        assert findings[0].id == "MUTATION_SKIPPED"
        assert findings[0].disposition == Disposition.DISMISSED
        assert "tests-only" in findings[0].description
        assert infra == []


class TestTestSelectionSurvivesConfigRoundTrip:
    """mutmut splits the selection on newlines, not spaces.

    A space-joined value arrives as ONE argv token ("-q --ignore=x"),
    which pytest rejects with exit 4 and mutmut turns into
    BadTestExecutionCommandsException -- the whole gate dies before
    measuring a single mutant.
    """

    def test_multi_arg_selection_round_trips_as_separate_tokens(self):
        from configparser import ConfigParser

        from code_forge.mutation import _build_mutmut_config

        cfg = _build_mutmut_config(
            ["src/pkg/mod.py"],
            ["pytest", "-q", "--ignore=tests/test_slow.py"],
        )
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "pytest_add_cli_args_test_selection")
        # Read back exactly as mutmut's configuration.py does. The
        # resource guard's marker exclusion rides along as two more
        # tokens.
        assert [x for x in raw.split("\n") if x] == [
            "-q",
            "--ignore=tests/test_slow.py",
            "-m",
            "not integration and not source_scan",
        ]

    def test_single_arg_selection_stays_on_the_key_line(self):
        from configparser import ConfigParser

        from code_forge.mutation import _build_mutmut_config

        cfg = _build_mutmut_config(["src/pkg/mod.py"], ["pytest", "tests/"])
        parser = ConfigParser()
        parser.read_string(cfg)
        raw = parser.get("mutmut", "pytest_add_cli_args_test_selection")
        assert not raw.startswith("\n")
        assert [x for x in raw.split("\n") if x] == [
            "tests/", "-m", "not integration and not source_scan",
        ]


class TestAlsoCopyConfigValidation:
    """A mistyped also_copy must fail loudly, not silently do nothing."""

    def _cfg(self, tmp_path, also_copy_literal):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "test:\n"
            "  command: [pytest, -q]\n"
            f"  also_copy: {also_copy_literal}\n",
            encoding="utf-8",
        )
        return p

    def test_non_list_also_copy_is_rejected(self, tmp_path):
        import pytest

        from code_forge.gate_check import load_gate_config

        with pytest.raises(ValueError, match="also_copy"):
            load_gate_config(self._cfg(tmp_path, "scripts/"))

    def test_non_string_entry_is_rejected(self, tmp_path):
        import pytest

        from code_forge.gate_check import load_gate_config

        with pytest.raises(ValueError, match="also_copy"):
            load_gate_config(self._cfg(tmp_path, "[scripts/, 7]"))

    def test_list_of_strings_is_accepted(self, tmp_path):
        from code_forge.gate_check import load_gate_config

        config = load_gate_config(self._cfg(tmp_path, "[scripts/, hooks/]"))
        assert config["test"]["also_copy"] == ["scripts/", "hooks/"]


class TestMutationResourceGuardConfigValidation:
    """The mutmut resource-guard knobs must fail loudly on bad gate.yaml."""

    def _cfg(self, tmp_path, extra):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "test:\n"
            "  command: [pytest, -q]\n"
            f"  {extra}\n",
            encoding="utf-8",
        )
        return p

    def test_zero_max_children_is_rejected(self, tmp_path):
        import pytest

        from code_forge.gate_check import load_gate_config

        with pytest.raises(ValueError, match="mutation_max_children"):
            load_gate_config(self._cfg(tmp_path, "mutation_max_children: 0"))

    def test_string_memory_limit_is_rejected(self, tmp_path):
        import pytest

        from code_forge.gate_check import load_gate_config

        with pytest.raises(ValueError, match="mutation_memory_limit_mb"):
            load_gate_config(
                self._cfg(tmp_path, 'mutation_memory_limit_mb: "8GiB"')
            )

    def test_valid_guards_are_accepted(self, tmp_path):
        from code_forge.gate_check import load_gate_config

        config = load_gate_config(
            self._cfg(
                tmp_path,
                "mutation_max_children: 2\n  mutation_memory_limit_mb: 4096",
            )
        )
        assert config["test"]["mutation_max_children"] == 2
        assert config["test"]["mutation_memory_limit_mb"] == 4096



class TestMutatedImportSurvivesAnEmptyCwd:
    """A trampoline import must not guess source_paths from an empty cwd.

    mutmut injects ``from mutmut.mutation.trampoline import ...`` into
    every mutated file. Importing that module calls Config.get(), which
    walks cwd for setup.cfg / src / lib. A CLI subprocess started from
    a scratch git repo has none of those, so the import raises
    FileNotFoundError before the command runs.

    The helper that plants a marked setup.cfg in the scratch cwd is
    what stops this. Without it the CLI subprocess dies on import.
    """

    def test_empty_cwd_crashes_without_a_planted_cfg(self, tmp_path):
        from mutmut.configuration import _guess_source_paths

        old = os.getcwd()
        os.chdir(tmp_path)
        try:
            try:
                _guess_source_paths()
            except FileNotFoundError as exc:
                assert "source_paths" in str(exc)
            else:
                raise AssertionError(
                    "empty cwd must make mutmut guess source_paths and fail"
                )
        finally:
            os.chdir(old)

    def test_planted_cfg_lets_guess_succeed(self, tmp_path):
        from mutmut.configuration import _load_config, Config

        marker = "# managed-by-code-forge-mutation"
        (tmp_path / "setup.cfg").write_text(
            f"{marker}\n[mutmut]\nsource_paths=src\n",
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        old = os.getcwd()
        os.chdir(tmp_path)
        try:
            Config._config = None
            cfg = _load_config()
            assert [str(p) for p in cfg.source_paths] == ["src"]
        finally:
            os.chdir(old)
            Config._config = None
