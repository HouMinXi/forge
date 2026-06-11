# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for code_forge.taint -- danger_score_from_diff and TaintRunner."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from code_forge.taint import (
    danger_score_from_diff,
    TaintRunner,
    _findings_to_advisories,
)
from code_forge.advisory import AdvisoryFinding
from code_forge.disposition import Disposition
from code_forge.parsers.base import Finding


# ---------------------------------------------------------------------------
# danger_score_from_diff tests
# ---------------------------------------------------------------------------

def test_danger_score_detects_base_url_in_gate_yaml():
    """Diff adds base_url to gate.yaml -> 1 StateFinding."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,3 +1,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 1
    assert "base_url" in findings[0].description
    assert findings[0].file == "gate.yaml"


def test_danger_score_detects_shell_in_code_forge_dir():
    """Diff adds shell field to .code-forge/custom.yaml."""
    diff = (
        "diff --git a/.code-forge/custom.yaml b/.code-forge/custom.yaml\n"
        "--- a/.code-forge/custom.yaml\n"
        "+++ b/.code-forge/custom.yaml\n"
        "@@ -1,2 +1,3 @@\n"
        " test:\n"
        "+  shell: /bin/bash -c 'rm -rf /'\n"
        "   timeout: 30\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 1
    assert "shell" in findings[0].description
    assert findings[0].file == ".code-forge/custom.yaml"


def test_danger_score_ignores_removed_lines():
    """Diff has -base_url line -> expect []."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,4 +1,3 @@\n"
        " backends:\n"
        "   default:\n"
        "-    base_url: https://evil.com\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert findings == []


def test_danger_score_ignores_non_config_files():
    """Diff adds base_url in src/app.py -> expect []."""
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " config = {\n"
        "+    base_url: https://example.com\n"
        "     model: gpt-4\n"
        " }\n"
    )
    findings = danger_score_from_diff(diff)
    assert findings == []


def test_danger_score_multiple_fields():
    """Diff adds base_url + api_key_env + shell -> expect 3 findings."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,3 +1,6 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "+    api_key_env: STOLEN_KEY\n"
        "+    shell: /bin/sh\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 3
    field_names = {f.description.split("'")[1] for f in findings}
    assert field_names == {"base_url", "api_key_env", "shell"}


def test_danger_score_empty_diff():
    """Empty string -> expect []."""
    findings = danger_score_from_diff("")
    assert findings == []


def test_danger_score_none_diff():
    """None input -> expect [] (non-git guard per D-16)."""
    findings = danger_score_from_diff(None)
    assert findings == []


def test_danger_score_fingerprint_format():
    """Verify fingerprint matches 'danger-score:{file}:{field}:{line}'."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,3 +1,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 1
    fp = findings[0].fingerprint
    parts = fp.split(":")
    assert parts[0] == "danger-score"
    assert parts[1] == "gate.yaml"
    assert parts[2] == "base_url"
    # line should be an integer string
    assert parts[3].isdigit()


def test_danger_score_finding_source_is_l0():
    """Verify source='L0'."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,3 +1,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert findings[0].source == "L0"


def test_danger_score_finding_disposition_confirmed():
    """Verify disposition=CONFIRMED."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,3 +1,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert findings[0].disposition == Disposition.CONFIRMED


def test_danger_score_multi_hunk_line_numbers():
    """Diff with two hunks: verify line numbers in fingerprints match hunk start lines."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -5,3 +5,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    base_url: https://evil.com\n"
        "     model: gpt-4\n"
        "@@ -20,3 +21,4 @@\n"
        " backends:\n"
        "   staging:\n"
        "+    shell: /bin/sh\n"
        "     model: gpt-3\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 2
    # First hunk: starts at new-line 5, context lines 5 and 6, then +line at 7
    fp0 = findings[0].fingerprint
    line0 = int(fp0.split(":")[3])
    assert line0 == 7
    # Second hunk: starts at new-line 21, context lines 21 and 22, then +line at 23
    fp1 = findings[1].fingerprint
    line1 = int(fp1.split(":")[3])
    assert line1 == 23


def test_danger_score_fingerprint_line_value_correct():
    """Verify {line} in fingerprint equals actual new-file line number from @@ header."""
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -10,3 +10,4 @@\n"
        " backends:\n"
        "   default:\n"
        "+    api_key_env: SECRET\n"
        "     model: gpt-4\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 1
    # Hunk starts at new-line 10, two context lines (10, 11), then +line at 12
    line_in_fp = int(findings[0].fingerprint.split(":")[3])
    assert line_in_fp == 12
    assert findings[0].line_range == [12, 12]


def test_danger_score_consecutive_plus_lines_distinct_line_numbers():
    """Back-to-back + lines in one hunk get distinct, incrementing line numbers.

    Regression: if line_number is not incremented after each + line, all
    consecutive new-lines in the same hunk receive the same fingerprint line,
    silently collapsing separate findings.
    """
    diff = (
        "diff --git a/gate.yaml b/gate.yaml\n"
        "--- a/gate.yaml\n"
        "+++ b/gate.yaml\n"
        "@@ -1,1 +1,3 @@\n"
        " existing:\n"
        "+base_url: https://evil.com\n"
        "+api_key_env: SECRET_KEY\n"
    )
    findings = danger_score_from_diff(diff)
    assert len(findings) == 2
    lines = [int(f.fingerprint.split(":")[3]) for f in findings]
    # hunk start=1, one context line increments to 2, first + at 2, second + at 3
    assert lines[0] != lines[1], "consecutive + lines must have distinct line numbers"
    assert lines[1] == lines[0] + 1, "line numbers must be consecutive"


# ---------------------------------------------------------------------------
# Shared SARIF fixture for TaintRunner tests
# ---------------------------------------------------------------------------

def _make_sarif(
    rule_id: str = "forge-taint-config-to-subprocess",
    message: str = "Tainted data from config flows to subprocess (intraprocedural only)",
    uri: str = "src/app.py",
    start_line: int = 10,
    end_line: int = 10,
) -> str:
    """Build a minimal valid semgrep SARIF JSON string."""
    sarif = {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": "semgrep"}},
            "results": [{
                "ruleId": rule_id,
                "level": "warning",
                "message": {"text": message},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {
                            "startLine": start_line,
                            "endLine": end_line,
                        },
                    },
                }],
            }],
        }],
    }
    return json.dumps(sarif)


# ---------------------------------------------------------------------------
# TaintRunner tests
# ---------------------------------------------------------------------------

def test_taint_runner_protocol_conformance():
    """TaintRunner satisfies AxisRunner (is_advisory=True)."""
    runner = TaintRunner()
    assert runner.is_advisory is True


def test_taint_runner_semgrep_absent():
    """shutil.which returns None -> [] returned, infra_errors has D-06 message."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    with patch("code_forge.taint.shutil.which", return_value=None):
        result = runner.run("some diff", Path("/fake/repo"))
    assert result == []
    assert len(runner.infra_errors) == 1
    assert "Taint gate requires semgrep" in runner.infra_errors[0]
    assert "pip install semgrep" in runner.infra_errors[0]


def test_taint_runner_returns_advisory_findings():
    """Semgrep returns valid SARIF -> list contains one AdvisoryFinding."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    sarif_out = _make_sarif()
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=sarif_out, stderr=""
    )
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", return_value=completed), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert len(result) == 1
    assert isinstance(result[0], AdvisoryFinding)


def test_taint_runner_advisory_finding_id_format():
    """Verify id matches 'taint:{file}:{line}:{rule_id}'."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    sarif_out = _make_sarif()
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=sarif_out, stderr=""
    )
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", return_value=completed), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert len(result) == 1
    assert result[0].id == "taint:src/app.py:10:forge-taint-config-to-subprocess"


def test_taint_runner_intraprocedural_caveat():
    """Verify 'intraprocedural only' appears in description (case-insensitive)."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    sarif_out = _make_sarif()
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=sarif_out, stderr=""
    )
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", return_value=completed), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert "intraprocedural only" in result[0].description.lower()


def test_taint_runner_attribution():
    """Verify attribution == 'semgrep-ce/intraprocedural'."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    sarif_out = _make_sarif()
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=sarif_out, stderr=""
    )
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", return_value=completed), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert result[0].attribution == "semgrep-ce/intraprocedural"


def test_taint_runner_empty_source_files():
    """source_files=[] returns []."""
    runner = TaintRunner()
    runner.source_files = []
    result = runner.run("some diff", Path("/fake/repo"))
    assert result == []


def test_taint_runner_semgrep_error_exit():
    """Semgrep exits with returncode=2 -> infra_errors has message, returns []."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    completed = subprocess.CompletedProcess(
        args=[], returncode=2, stdout="", stderr="config error"
    )
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", return_value=completed), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert result == []
    assert len(runner.infra_errors) == 1
    assert "exit 2" in runner.infra_errors[0]


def test_taint_runner_no_source_files_attr():
    """Freshly constructed TaintRunner().run() returns [] (source_files defaults to None)."""
    runner = TaintRunner()
    result = runner.run("some diff", Path("/fake/repo"))
    assert result == []


def test_findings_to_advisories():
    """Unit test _findings_to_advisories with hand-built Finding."""
    finding = Finding(
        file="src/app.py",
        line=42,
        end_line=42,
        column=5,
        rule_id="forge-taint-config-to-subprocess",
        level="warning",
        message="Tainted data from config flows to subprocess (intraprocedural only)",
        tool_name="semgrep",
    )
    advisories = _findings_to_advisories([finding])
    assert len(advisories) == 1
    a = advisories[0]
    assert a.id == "taint:src/app.py:42:forge-taint-config-to-subprocess"
    assert a.axis == "taint"
    assert a.file == "src/app.py"
    assert a.line_range == [42, 42]
    # description is finding.message verbatim (no double caveat)
    assert a.description == finding.message
    assert a.attribution == "semgrep-ce/intraprocedural"


def test_taint_runner_filters_non_python():
    """source_files includes a .md file -> subprocess.run args should NOT contain .md."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py"), Path("README.md")]
    sarif_out = _make_sarif()
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=sarif_out, stderr=""
    )
    captured_cmd = []

    def mock_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return completed

    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run", side_effect=mock_run), \
         patch("pathlib.Path.exists", return_value=True):
        runner.run("some diff", Path("/fake/repo"))
    # .md file should NOT appear in the command
    assert "README.md" not in captured_cmd
    # .py file should appear
    assert "src/app.py" in captured_cmd


def test_taint_runner_semgrep_timeout():
    """subprocess.run raises TimeoutExpired -> infra_error contains 'timed out', returns []."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    with patch("code_forge.taint.shutil.which", return_value="/fake/semgrep"), \
         patch("code_forge.taint.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=120)), \
         patch("pathlib.Path.exists", return_value=True):
        result = runner.run("some diff", Path("/fake/repo"))
    assert result == []
    assert len(runner.infra_errors) == 1
    assert "timed out" in runner.infra_errors[0]


def test_taint_runner_clears_infra_errors():
    """Call run() twice -> infra_errors from first run do not accumulate into second."""
    runner = TaintRunner()
    runner.source_files = [Path("src/app.py")]
    # First run: semgrep absent -> infra_error
    with patch("code_forge.taint.shutil.which", return_value=None):
        runner.run("diff1", Path("/fake/repo"))
    assert len(runner.infra_errors) == 1
    # Second run: also absent -> only 1 error (cleared first)
    with patch("code_forge.taint.shutil.which", return_value=None):
        runner.run("diff2", Path("/fake/repo"))
    assert len(runner.infra_errors) == 1
