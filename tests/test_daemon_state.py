# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for DaemonStateRunner advisory axis.

Covers all DaemonStateRunner behaviors:
- AxisRunner Protocol conformance (is_advisory=True)
- Empty diff returns []
- Heuristic fallback when no gate.yaml daemon_state section
- Two-step LLM call (Q1 state enumeration -> grep -> Q2Q3 conflict analysis)
- Static conflict rule matching from gate.yaml
- Static rules injected into LLM Q2Q3 prompt
- grep sanitization (subprocess.run with list args, no shell=True)
- grep top_k limit
- grep empty keywords
- Q1 empty state skips grep
- Q1 malformed response -> SKIPPED finding
- LLM failure -> SKIPPED finding
- RuntimeRunner.last_surfaces integration
- Runtime fallback when runner absent
- Unconfigured: no full axis without opt-in
- conflicts_file missing warning
- Default keyword set verification
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from code_forge.advisory import AdvisoryFinding


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestDaemonStateProtocol:
    """DaemonStateRunner implements AxisRunner Protocol."""

    def test_is_advisory(self):
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        assert runner.is_advisory is True

    def test_run_returns_list(self, tmp_path):
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        result = runner.run("", tmp_path)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Empty diff
# ---------------------------------------------------------------------------


class TestDaemonStateEmptyDiff:
    """Empty diff returns []."""

    def test_empty_diff(self, tmp_path):
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        result = runner.run("", tmp_path)
        assert result == []

    def test_empty_whitespace_diff(self, tmp_path):
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        result = runner.run("  \n  ", tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------


class TestDaemonStateHeuristicFallback:
    """Heuristic fallback when no gate.yaml daemon_state section."""

    def test_heuristic_fallback_no_gate_yaml(self, tmp_path):
        """Diff with stateful keyword + no daemon_state config -> one advisory."""
        from code_forge.daemon_state import DaemonStateRunner

        # No .code-forge/gate.yaml at all
        runner = DaemonStateRunner()
        diff = "diff --git a/rules.sh b/rules.sh\n+nft add rule inet filter input drop"
        result = runner.run(diff, tmp_path)

        assert len(result) == 1
        finding = result[0]
        assert isinstance(finding, AdvisoryFinding)
        assert finding.axis == "DAEMON-STATE"
        assert "enable daemon_state in gate.yaml" in finding.description

    def test_heuristic_fallback_axis_name(self, tmp_path):
        """Heuristic advisory finding uses axis='DAEMON-STATE'."""
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        diff = "diff --git a/fw.sh b/fw.sh\n+iptables -A INPUT -j DROP"
        result = runner.run(diff, tmp_path)

        assert len(result) == 1
        assert result[0].axis == "DAEMON-STATE"

    def test_heuristic_no_match(self, tmp_path):
        """Diff with no stateful keywords + no gate.yaml -> []."""
        from code_forge.daemon_state import DaemonStateRunner

        runner = DaemonStateRunner()
        diff = "diff --git a/hello.py b/hello.py\n+print('hello world')"
        result = runner.run(diff, tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Two-step LLM call
# ---------------------------------------------------------------------------


class TestDaemonStateTwoStepLLM:
    """Two-step LLM: Q1 enumerates state, Q2Q3 analyzes conflicts."""

    def test_two_step_llm(self, tmp_path):
        """Mock llm_invoke twice: Q1 -> Q2Q3 with correct expected_keys."""
        from code_forge.daemon_state import DaemonStateRunner

        # Set up gate.yaml with daemon_state enabled
        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        # Q1 response
        q1_response = MagicMock()
        q1_response.content = {"external_state": ["nft mark 0xff"]}

        # Q2Q3 response
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": [
            {
                "subsystem": "killswitch",
                "mutates": "nft mark",
                "interferes_with": "health check probes",
                "scenario": "mark blocks outbound probes",
            }
        ]}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]) as mock_llm, \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            diff = "diff --git a/rules.sh b/rules.sh\n+nft add rule inet filter"
            runner.run(diff, tmp_path)

        assert mock_llm.call_count == 2

        # Q1 call uses frozenset({"external_state"})
        q1_call = mock_llm.call_args_list[0]
        assert q1_call.kwargs.get("expected_keys") == frozenset({"external_state"})

        # Q2Q3 call uses frozenset({"conflicts"})
        q2q3_call = mock_llm.call_args_list[1]
        assert q2q3_call.kwargs.get("expected_keys") == frozenset({"conflicts"})


# ---------------------------------------------------------------------------
# Static conflict rules
# ---------------------------------------------------------------------------


class TestDaemonStateStaticRules:
    """Static conflict rules from gate.yaml matched against diff."""

    def test_static_rules_matched(self, tmp_path):
        """gate.yaml triplet matching diff -> appears as AdvisoryFinding."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
                "conflicts": [
                    {
                        "subsystem": "killswitch",
                        "mutates": "nft mark",
                        "interferes_with": "health check outbound probes",
                    },
                ],
            },
        }))

        # Q1/Q2Q3 responses (static rules appear BEFORE LLM findings)
        q1_response = MagicMock()
        q1_response.content = {"external_state": []}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]), \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            diff = "diff --git a/ks.sh b/ks.sh\n+nft mark 0xff"
            result = runner.run(diff, tmp_path)

        # Static rule should produce at least one finding
        static_findings = [f for f in result if "killswitch" in f.description.lower()
                           or "nft mark" in f.description.lower()]
        assert len(static_findings) >= 1
        for f in static_findings:
            assert f.axis == "DAEMON-STATE"

    def test_static_rules_injected_into_llm_prompt(self, tmp_path):
        """When static rules match, the Q2Q3 prompt contains matched rule text."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
                "conflicts": [
                    {
                        "subsystem": "killswitch",
                        "mutates": "nft mark",
                        "interferes_with": "health check probes",
                    },
                ],
            },
        }))

        q1_response = MagicMock()
        q1_response.content = {"external_state": ["nft mark"]}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]) as mock_llm, \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            diff = "diff --git a/ks.sh b/ks.sh\n+nft mark 0xff"
            runner.run(diff, tmp_path)

        # Q2Q3 prompt (second call, first positional arg) should contain
        # the matched static rule text
        q2q3_prompt = mock_llm.call_args_list[1][0][0]
        assert "killswitch" in q2q3_prompt.lower() or "nft mark" in q2q3_prompt.lower()


# ---------------------------------------------------------------------------
# grep sanitization
# ---------------------------------------------------------------------------


class TestDaemonStateGrepSanitization:
    """_grep_repo uses subprocess.run with list args; no shell=True."""

    def test_grep_sanitization(self, tmp_path):
        """subprocess.run is called with list args, not shell=True."""
        from code_forge.daemon_state import _grep_repo

        with patch("code_forge.daemon_state.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", returncode=0
            )
            _grep_repo(["test_keyword"], tmp_path)

        # Every call to subprocess.run should use list args
        for c in mock_run.call_args_list:
            args = c[0][0] if c[0] else c.kwargs.get("args", [])
            assert isinstance(args, list), "subprocess.run must use list args"
            # shell=True must NOT be passed
            assert c.kwargs.get("shell") is not True, \
                "shell=True must NOT be used in grep calls"

    def test_grep_top_k(self, tmp_path):
        """_grep_repo with top_k=2 returns context from at most 2 files."""
        from code_forge.daemon_state import _grep_repo

        # Create 5 files with matching content
        for i in range(5):
            f = tmp_path / ("file%d.py" % i)
            f.write_text("nft mark 0xff\nnft add rule\nnft delete rule\n")

        result = _grep_repo(["nft"], tmp_path, top_k=2)

        # Count distinct file references in output
        file_refs = set()
        for line in result.splitlines():
            if line.startswith("--- ") and line.endswith(" ---"):
                file_refs.add(line)
        assert len(file_refs) <= 2

    def test_grep_empty_keywords(self, tmp_path):
        """_grep_repo([], repo_root) returns '' without crashing."""
        from code_forge.daemon_state import _grep_repo

        result = _grep_repo([], tmp_path)
        assert result == ""


# ---------------------------------------------------------------------------
# Q1 empty state
# ---------------------------------------------------------------------------


class TestDaemonStateQ1Empty:
    """When Q1 returns empty external_state, grep is skipped."""

    def test_q1_empty_state_skips_grep(self, tmp_path):
        """Q1 returns empty external_state -> _grep_repo not called with keywords."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        q1_response = MagicMock()
        q1_response.content = {"external_state": []}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]), \
             patch("code_forge.daemon_state._grep_repo",
                   return_value="") as mock_grep:
            runner = DaemonStateRunner()
            diff = "diff --git a/f b/f\n+nft add rule"
            runner.run(diff, tmp_path)

        # _grep_repo should be called with empty keywords or not called
        if mock_grep.called:
            keywords_arg = mock_grep.call_args[0][0]
            assert keywords_arg == [] or keywords_arg == ""


# ---------------------------------------------------------------------------
# Q1 malformed response
# ---------------------------------------------------------------------------


class TestDaemonStateQ1Malformed:
    """Q1 returns valid JSON without 'external_state' key -> SKIPPED."""

    def test_q1_malformed_response(self, tmp_path):
        """Valid JSON but missing external_state -> SKIPPED finding."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        # Q1 returns JSON without expected key
        q1_response = MagicMock()
        q1_response.content = {"other_key": ["something"]}

        with patch("code_forge.daemon_state.llm_invoke",
                   return_value=q1_response):
            runner = DaemonStateRunner()
            diff = "diff --git a/f b/f\n+nft add rule"
            result = runner.run(diff, tmp_path)

        skipped = [f for f in result if "skipped" in f.description.lower()
                   or "SKIPPED" in f.description]
        assert len(skipped) >= 1
        assert skipped[0].axis == "DAEMON-STATE"


# ---------------------------------------------------------------------------
# LLM failure -> SKIPPED
# ---------------------------------------------------------------------------


class TestDaemonStateLLMFailure:
    """LLM failure produces SKIPPED finding, never silent."""

    def test_llm_failure_skipped(self, tmp_path):
        """llm_invoke raises LLMInvokeError -> SKIPPED AdvisoryFinding."""
        from code_forge.daemon_state import DaemonStateRunner
        from code_forge.llm_invoke import LLMInvokeError

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=LLMInvokeError("connection timeout")):
            runner = DaemonStateRunner()
            diff = "diff --git a/f b/f\n+nft add rule"
            result = runner.run(diff, tmp_path)

        skipped = [f for f in result if "SKIPPED" in f.description]
        assert len(skipped) >= 1
        assert skipped[0].axis == "DAEMON-STATE"
        assert skipped[0].id == "daemon-state-skipped"


# ---------------------------------------------------------------------------
# RuntimeRunner.last_surfaces integration (cross-axis)
# ---------------------------------------------------------------------------


class TestDaemonStateRuntimeSurfaces:
    """DaemonStateRunner reads RuntimeRunner.last_surfaces."""

    def test_runtime_surfaces_used(self, tmp_path):
        """When _runtime_runner has last_surfaces, they appear in Q1 prompt."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        # Mock runtime runner with surfaces
        mock_runtime = MagicMock()
        mock_runtime.last_surfaces = ["nftables rules", "systemd units"]

        q1_response = MagicMock()
        q1_response.content = {"external_state": []}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]) as mock_llm, \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            runner._runtime_runner = mock_runtime
            diff = "diff --git a/f b/f\n+nft add rule"
            runner.run(diff, tmp_path)

        # Q1 prompt should contain runtime surfaces
        q1_prompt = mock_llm.call_args_list[0][0][0]
        assert "nftables rules" in q1_prompt

    def test_runtime_fallback(self, tmp_path):
        """When _runtime_runner is None, DaemonStateRunner still runs."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
            },
        }))

        q1_response = MagicMock()
        q1_response.content = {"external_state": []}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]), \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            # _runtime_runner is None by default
            diff = "diff --git a/f b/f\n+nft add rule"
            result = runner.run(diff, tmp_path)

        # Should not crash -- results may vary but must be a list
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Unconfigured: no full axis without opt-in
# ---------------------------------------------------------------------------


class TestDaemonStateUnconfigured:
    """Without daemon_state in gate.yaml, full LLM axis does NOT run."""

    def test_unconfigured_no_full_axis(self, tmp_path):
        """No daemon_state in gate.yaml AND heuristic matches -> advisory only,
        two-step LLM does NOT run."""
        from code_forge.daemon_state import DaemonStateRunner

        # No gate.yaml at all
        with patch("code_forge.daemon_state.llm_invoke") as mock_llm:
            runner = DaemonStateRunner()
            diff = "diff --git a/f b/f\n+nft add rule inet filter"
            result = runner.run(diff, tmp_path)

        # LLM should NOT have been called
        mock_llm.assert_not_called()

        # Should still emit advisory finding
        assert len(result) == 1
        assert "enable daemon_state in gate.yaml" in result[0].description


# ---------------------------------------------------------------------------
# conflicts_file missing warning
# ---------------------------------------------------------------------------


class TestDaemonStateConflictsFileMissing:
    """gate.yaml references conflicts_file that does not exist."""

    def test_conflicts_file_missing_warning(self, tmp_path):
        """Missing conflicts_file -> logs warning, continues with inline rules."""
        from code_forge.daemon_state import DaemonStateRunner

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        gate_yaml = gate_dir / "gate.yaml"
        gate_yaml.write_text(yaml.safe_dump({
            "test": {"command": ["python3", "-m", "pytest"]},
            "daemon_state": {
                "enabled": True,
                "subsystems": ["nftables"],
                "patterns": [],
                "conflicts_file": "nonexistent_conflicts.yaml",
            },
        }))

        q1_response = MagicMock()
        q1_response.content = {"external_state": []}
        q2q3_response = MagicMock()
        q2q3_response.content = {"conflicts": []}

        with patch("code_forge.daemon_state.llm_invoke",
                   side_effect=[q1_response, q2q3_response]), \
             patch("code_forge.daemon_state._grep_repo", return_value=""):
            runner = DaemonStateRunner()
            diff = "diff --git a/f b/f\n+nft add rule"
            runner.run(diff, tmp_path)

        # Should log warning to infra_errors
        assert len(runner.infra_errors) > 0
        assert any("conflicts_file" in e.lower() or "nonexistent" in e.lower()
                    for e in runner.infra_errors)


# ---------------------------------------------------------------------------
# Default keyword set
# ---------------------------------------------------------------------------


class TestDaemonStateDefaultKeywords:
    """Verify default keyword set."""

    def test_default_keywords(self):
        from code_forge.daemon_state import DEFAULT_DAEMON_KEYWORDS

        expected = frozenset({"nft", "iptables", "ip route",
                              "systemctl", "firewall-cmd", "tc"})
        assert DEFAULT_DAEMON_KEYWORDS == expected


# ---------------------------------------------------------------------------
# Constants exported
# ---------------------------------------------------------------------------


class TestDaemonStateConstants:
    """Verify DAEMON_STATE_Q1 and DAEMON_STATE_Q2Q3 are exported."""

    def test_q1_constant_exported(self):
        from code_forge.daemon_state import DAEMON_STATE_Q1

        assert isinstance(DAEMON_STATE_Q1, str)
        assert len(DAEMON_STATE_Q1.strip()) > 0

    def test_q2q3_constant_exported(self):
        from code_forge.daemon_state import DAEMON_STATE_Q2Q3

        assert isinstance(DAEMON_STATE_Q2Q3, str)
        assert len(DAEMON_STATE_Q2Q3.strip()) > 0
