# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for the per-file review coverage gate (coverage.py)."""

import pytest

from code_forge.coverage import (
    build_coverage_findings,
    compute_uncovered_files,
    load_coverage_exempt_patterns,
)
from code_forge.disposition import Disposition
from code_forge.errors import CoverageConfigError
from code_forge.registry import ToolConfig


def _tool(name, patterns):
    return ToolConfig(
        name=name,
        command=name,
        args=[],
        output_format=name + "_json",
        file_patterns=patterns,
    )


# ---------------------------------------------------------------------------
# compute_uncovered_files
# ---------------------------------------------------------------------------

def test_l1_active_covers_everything():
    # When L1 ran over the diff, every file is examined -> no gaps.
    registry = {}  # no L0 tools at all
    uncovered = compute_uncovered_files(
        ["a.sh", "b.py", "c.txt"], registry, l1_active=True
    )
    assert uncovered == []


def test_file_matched_by_l0_tool_is_covered():
    registry = {"ruff": _tool("ruff", ["*.py"])}
    uncovered = compute_uncovered_files(
        ["b.py"], registry, l1_active=False
    )
    assert uncovered == []


def test_file_without_matching_tool_is_uncovered():
    registry = {"ruff": _tool("ruff", ["*.py"])}
    uncovered = compute_uncovered_files(
        ["a.sh"], registry, l1_active=False
    )
    assert uncovered == ["a.sh"]


def test_mixed_scope_flags_only_uncovered_files():
    # The user's exact case: 1 linted .py + shell files with no tool.
    registry = {"ruff": _tool("ruff", ["*.py"])}
    uncovered = compute_uncovered_files(
        ["proxy.py", "a.sh", ".bashrc", "aicc"],
        registry,
        l1_active=False,
    )
    assert uncovered == ["a.sh", ".bashrc", "aicc"]


def test_shellcheck_tool_covers_shell_files():
    # After A3 adds shellcheck, *.sh is covered; extensionless stays a gap.
    registry = {
        "ruff": _tool("ruff", ["*.py"]),
        "shellcheck": _tool("shellcheck", ["*.sh", "*.bash"]),
    }
    uncovered = compute_uncovered_files(
        ["proxy.py", "k.sh", "aicc"], registry, l1_active=False
    )
    assert uncovered == ["aicc"]


def test_exempt_pattern_suppresses_finding():
    registry = {"ruff": _tool("ruff", ["*.py"])}
    uncovered = compute_uncovered_files(
        ["notes.txt", "a.sh"],
        registry,
        l1_active=False,
        exempt_patterns=["*.txt"],
    )
    assert uncovered == ["a.sh"]


def test_result_preserves_order_and_dedupes():
    registry = {}
    uncovered = compute_uncovered_files(
        ["z.sh", "a.sh", "z.sh"], registry, l1_active=False
    )
    assert uncovered == ["z.sh", "a.sh"]


def test_empty_scope_yields_no_gaps():
    assert compute_uncovered_files([], {}, l1_active=False) == []


# ---------------------------------------------------------------------------
# build_coverage_findings
# ---------------------------------------------------------------------------

def test_build_findings_shape():
    findings = build_coverage_findings(["a.sh", "b.sh"])
    assert len(findings) == 2
    first = findings[0]
    assert first.source == "COVERAGE"
    assert first.disposition == Disposition.UNCERTAIN
    assert first.file == "a.sh"
    assert "a.sh" in first.fingerprint


def test_build_findings_unique_fingerprints():
    findings = build_coverage_findings(["a.sh", "b.sh"])
    fps = {f.fingerprint for f in findings}
    assert len(fps) == 2


def test_build_findings_empty():
    assert build_coverage_findings([]) == []


# ---------------------------------------------------------------------------
# load_coverage_exempt_patterns
# ---------------------------------------------------------------------------

def test_load_exempt_absent_returns_empty(tmp_path):
    assert load_coverage_exempt_patterns(tmp_path) == []


def test_load_exempt_valid(tmp_path):
    cfg = tmp_path / ".code-forge"
    cfg.mkdir()
    (cfg / "coverage.yaml").write_text(
        "version: 1\nexempt_patterns:\n  - '*.txt'\n  - 'docs/*'\n"
    )
    assert load_coverage_exempt_patterns(tmp_path) == ["*.txt", "docs/*"]


def test_load_exempt_missing_patterns_key_defaults_empty(tmp_path):
    cfg = tmp_path / ".code-forge"
    cfg.mkdir()
    (cfg / "coverage.yaml").write_text("version: 1\n")
    assert load_coverage_exempt_patterns(tmp_path) == []


def test_load_exempt_bad_version_raises(tmp_path):
    cfg = tmp_path / ".code-forge"
    cfg.mkdir()
    (cfg / "coverage.yaml").write_text("version: 2\nexempt_patterns: []\n")
    with pytest.raises(CoverageConfigError):
        load_coverage_exempt_patterns(tmp_path)


def test_load_exempt_non_list_raises(tmp_path):
    cfg = tmp_path / ".code-forge"
    cfg.mkdir()
    (cfg / "coverage.yaml").write_text("version: 1\nexempt_patterns: 'oops'\n")
    with pytest.raises(CoverageConfigError):
        load_coverage_exempt_patterns(tmp_path)


def test_load_exempt_non_mapping_raises(tmp_path):
    cfg = tmp_path / ".code-forge"
    cfg.mkdir()
    (cfg / "coverage.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(CoverageConfigError):
        load_coverage_exempt_patterns(tmp_path)
