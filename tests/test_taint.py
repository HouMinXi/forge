# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for code_forge.taint -- danger_score_from_diff and TaintRunner."""

from code_forge.taint import danger_score_from_diff
from code_forge.disposition import Disposition


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
