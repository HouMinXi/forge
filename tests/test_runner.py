# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for the tool runner module.

All subprocess calls are mocked -- no real external tools are invoked.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from code_forge.registry import ToolConfig
from code_forge.runner import (
    _resolve_command,
    capture_tool_version,
    run_tool,
    run_tools,
)


def _make_tool(
    name="shellcheck",
    command="shellcheck",
    args=None,
    output_format="shellcheck_json",
    file_patterns=None,
    required=False,
    timeout=30,
    working_dir=None,
    enabled=True,
):
    """Helper to build a ToolConfig with sane defaults."""
    return ToolConfig(
        name=name,
        command=command,
        args=args or ["--format=json"],
        output_format=output_format,
        file_patterns=file_patterns or ["*.sh"],
        required=required,
        timeout=timeout,
        working_dir=working_dir,
        enabled=enabled,
    )


class TestResolveCommand:
    """Tests for _resolve_command -- PATH-based and relative path."""

    @patch("code_forge.runner.os.access", return_value=True)
    @patch("code_forge.runner.os.path.isfile", return_value=False)
    @patch("code_forge.runner.shutil.which", return_value="/usr/bin/shellcheck")
    def test_path_based(self, mock_which, _isfile, _access):
        assert _resolve_command("shellcheck") == "/usr/bin/shellcheck"
        mock_which.assert_called_once_with("shellcheck")

    @patch("code_forge.runner.os.access", return_value=True)
    @patch("code_forge.runner.os.path.isfile", return_value=True)
    @patch("code_forge.runner.shutil.which", return_value=None)
    def test_relative_path(self, mock_which, mock_isfile, mock_access):
        result = _resolve_command("scripts/checkpatch.pl")
        assert result == "scripts/checkpatch.pl"
        mock_which.assert_called_once_with("scripts/checkpatch.pl")
        mock_isfile.assert_called_once_with("scripts/checkpatch.pl")
        mock_access.assert_called()

    @patch("code_forge.runner.os.access", return_value=False)
    @patch("code_forge.runner.os.path.isfile", return_value=False)
    @patch("code_forge.runner.shutil.which", return_value=None)
    def test_not_found(self, _which, _isfile, _access):
        assert _resolve_command("nonexistent") is None

    @patch("code_forge.runner.os.access", return_value=False)
    @patch("code_forge.runner.os.path.isfile", return_value=True)
    @patch("code_forge.runner.shutil.which", return_value=None)
    def test_relative_not_executable(self, _which, _isfile, _access):
        """File exists but is not executable -- should return None."""
        result = _resolve_command("scripts/not_exec.pl")
        assert result is None

    @patch("code_forge.runner.shutil.which", return_value="/usr/bin/ruff")
    def test_no_separator_skips_file_check(self, mock_which):
        """Command without os.sep should not check isfile."""
        result = _resolve_command("ruff")
        assert result == "/usr/bin/ruff"


class TestCaptureToolVersion:
    """Tests for capture_tool_version -- Consensus #3, GATE-02."""

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_returns_version_string(self, _resolve, mock_run):
        mock_run.return_value = MagicMock(
            stdout="ShellCheck - shell script analysis tool version: 0.10.0\n",
            stderr="",
            returncode=0,
        )
        result = capture_tool_version("shellcheck")
        assert "0.10.0" in result
        # Must not use shell=True
        _call_args = mock_run.call_args
        assert _call_args[1].get("shell") is not True

    @patch(
        "code_forge.runner._resolve_command",
        return_value=None,
    )
    def test_not_installed(self, _resolve):
        result = capture_tool_version("nonexistent")
        assert result == "not_installed"

    @patch("code_forge.runner.subprocess.run", side_effect=OSError("no such file"))
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/broken",
    )
    def test_oserror_returns_unknown(self, _resolve, _run):
        result = capture_tool_version("broken")
        assert result == "unknown"

    @patch(
        "code_forge.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=5),
    )
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/slow",
    )
    def test_timeout_returns_unknown(self, _resolve, _run):
        result = capture_tool_version("slow")
        assert result == "unknown"


class TestRunTool:
    """Tests for run_tool -- subprocess orchestration."""

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_returns_3tuple(self, _resolve, mock_run):
        mock_run.return_value = MagicMock(
            stdout='[{"level":"error"}]',
            returncode=1,
            stderr="",
        )
        result = run_tool(_make_tool(), ["test.sh"])
        assert result is not None
        stdout, returncode, stderr = result
        assert stdout == '[{"level":"error"}]'
        assert returncode == 1
        assert stderr == ""

    @patch(
        "code_forge.runner._resolve_command",
        return_value=None,
    )
    def test_missing_optional_returns_none(self, _resolve):
        tool = _make_tool(required=False)
        result = run_tool(tool, ["test.sh"])
        assert result is None

    @patch(
        "code_forge.runner._resolve_command",
        return_value=None,
    )
    def test_missing_required_raises(self, _resolve):
        tool = _make_tool(required=True)
        with pytest.raises(RuntimeError, match="Required tool not found"):
            run_tool(tool, ["test.sh"])

    @patch(
        "code_forge.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=30),
    )
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_timeout_returns_none(self, _resolve, _run):
        tool = _make_tool(timeout=30)
        result = run_tool(tool, ["test.sh"])
        assert result is None

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_never_uses_shell_true(self, _resolve, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", returncode=0, stderr=""
        )
        run_tool(_make_tool(), ["test.sh"])
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("shell") is not True

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_command_is_list(self, _resolve, mock_run):
        """subprocess.run must receive a list, never a string."""
        mock_run.return_value = MagicMock(
            stdout="", returncode=0, stderr=""
        )
        run_tool(_make_tool(), ["test.sh"])
        cmd_arg = mock_run.call_args[0][0]
        assert isinstance(cmd_arg, list)

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_captures_stderr(self, _resolve, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", returncode=2, stderr="parse error at line 5"
        )
        result = run_tool(_make_tool(), ["test.sh"])
        assert result is not None
        _, _, stderr = result
        assert stderr == "parse error at line 5"

    @patch(
        "code_forge.runner.subprocess.run",
        side_effect=OSError("file not found"),
    )
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/broken",
    )
    def test_oserror_returns_none(self, _resolve, _run):
        result = run_tool(_make_tool(), ["test.sh"])
        assert result is None

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/cargo",
    )
    def test_cargo_root_skips_file_args(self, _resolve, mock_run):
        """working_dir='cargo_root' should not append files to command."""
        mock_run.return_value = MagicMock(
            stdout="", returncode=0, stderr=""
        )
        tool = _make_tool(
            name="clippy",
            command="cargo",
            args=["clippy", "--message-format=json"],
            working_dir="cargo_root",
        )
        run_tool(tool, ["src/main.rs"])
        cmd_arg = mock_run.call_args[0][0]
        assert "src/main.rs" not in cmd_arg

    @patch("code_forge.runner.subprocess.run")
    @patch(
        "code_forge.runner._resolve_command",
        return_value="/usr/bin/shellcheck",
    )
    def test_respects_timeout(self, _resolve, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", returncode=0, stderr=""
        )
        tool = _make_tool(timeout=60)
        run_tool(tool, ["test.sh"])
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 60


class TestRunTools:
    """Tests for run_tools -- orchestrating multiple tools."""

    @patch("code_forge.runner.capture_tool_version", return_value="shellcheck 0.10.0")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_returns_4tuple(self, mock_match, mock_run_tool, mock_version):
        mock_match.return_value = {"shellcheck": ["test.sh"]}
        mock_run_tool.return_value = ('{"output":true}', 0, "")
        registry = {"shellcheck": _make_tool()}
        results, versions, skipped, infra = run_tools(registry, ["test.sh"])
        assert isinstance(results, dict)
        assert isinstance(versions, dict)
        assert isinstance(skipped, list)
        assert isinstance(infra, list)

    @patch("code_forge.runner.capture_tool_version", return_value="shellcheck 0.10.0")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_populates_tool_versions(self, mock_match, mock_run_tool, mock_ver):
        mock_match.return_value = {"shellcheck": ["test.sh"]}
        mock_run_tool.return_value = ('{"output":true}', 0, "")
        registry = {"shellcheck": _make_tool()}
        _, versions, _, _ = run_tools(registry, ["test.sh"])
        assert "shellcheck" in versions
        assert versions["shellcheck"] == "shellcheck 0.10.0"

    @patch("code_forge.runner.capture_tool_version", return_value="ruff 0.4.0")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_skips_no_matching_files(self, mock_match, mock_run_tool, mock_ver):
        mock_match.return_value = {"ruff": []}
        registry = {"ruff": _make_tool(name="ruff", command="ruff")}
        results, _, skipped, _ = run_tools(registry, ["test.sh"])
        assert "ruff" not in results
        assert "ruff" in skipped
        mock_run_tool.assert_not_called()

    @patch("code_forge.runner.capture_tool_version", return_value="shellcheck 0.10.0")
    @patch("code_forge.runner.run_tool", return_value=None)
    @patch("code_forge.runner.match_tools")
    def test_run_tool_none_adds_to_skipped(
        self, mock_match, mock_run_tool, mock_ver
    ):
        mock_match.return_value = {"shellcheck": ["test.sh"]}
        registry = {"shellcheck": _make_tool()}
        _, _, skipped, infra = run_tools(registry, ["test.sh"])
        assert "shellcheck" in skipped
        assert any("shellcheck" in e for e in infra)

    @patch("code_forge.runner.capture_tool_version")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_sorted_iteration_order(
        self, mock_match, mock_run_tool, mock_ver
    ):
        """GATE-02: iteration order must be deterministic (sorted)."""
        mock_match.return_value = {
            "zzz_tool": ["a.sh"],
            "aaa_tool": ["a.sh"],
            "mmm_tool": ["a.sh"],
        }
        mock_run_tool.return_value = ("out", 0, "")
        mock_ver.return_value = "1.0"
        registry = {
            "zzz_tool": _make_tool(name="zzz_tool"),
            "aaa_tool": _make_tool(name="aaa_tool"),
            "mmm_tool": _make_tool(name="mmm_tool"),
        }
        run_tools(registry, ["a.sh"])
        # Check that run_tool was called in sorted order
        call_names = [
            c[0][0].name for c in mock_run_tool.call_args_list
        ]
        assert call_names == ["aaa_tool", "mmm_tool", "zzz_tool"]

    @patch("code_forge.runner.capture_tool_version", return_value="1.0")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_calls_match_tools_once(
        self, mock_match, mock_run_tool, mock_ver
    ):
        """Mimo F-04: match_tools called once, not per tool."""
        mock_match.return_value = {
            "a": ["test.sh"],
            "b": ["test.sh"],
        }
        mock_run_tool.return_value = ("out", 0, "")
        registry = {
            "a": _make_tool(name="a"),
            "b": _make_tool(name="b"),
        }
        run_tools(registry, ["test.sh"])
        mock_match.assert_called_once()

    @patch("code_forge.runner.capture_tool_version", return_value="1.0")
    @patch("code_forge.runner.run_tool")
    @patch("code_forge.runner.match_tools")
    def test_results_keyed_by_tool_name(
        self, mock_match, mock_run_tool, mock_ver
    ):
        mock_match.return_value = {"shellcheck": ["test.sh"]}
        mock_run_tool.return_value = ("output", 1, "err")
        registry = {"shellcheck": _make_tool()}
        results, _, _, _ = run_tools(registry, ["test.sh"])
        assert "shellcheck" in results
        stdout, rc, stderr = results["shellcheck"]
        assert stdout == "output"
        assert rc == 1
        assert stderr == "err"
