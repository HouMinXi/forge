# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Taint + provenance sub-capabilities for REVIEW-TRUST-01.

Two sub-capabilities:
  (a) danger_score_from_diff -- L0 blocking: scans diff new-lines for
      DANGEROUS_FIELDS in gate.yaml / .code-forge/* config files.
  (b) TaintRunner -- advisory axis: semgrep intraprocedural taint detection
      on source files via AxisRunner Protocol.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .advisory import AdvisoryFinding
from .disposition import Disposition
from .parsers.base import Finding, ToolError
from .parsers.semgrep import parse_semgrep
from .state import StateFinding
from .trust import DANGEROUS_FIELDS

# Anchored regex: match a dangerous field name at the start of a YAML line
# (with optional leading whitespace), followed by a colon.
_DANGER_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(" + "|".join(re.escape(f) for f in DANGEROUS_FIELDS) + r")\s*:"
)

# Regex to extract the file path from a diff --git header.
# Captures only the part after 'b/' (stripping the b/ prefix).
_DIFF_HEADER_RE: re.Pattern[str] = re.compile(
    r"^diff --git a/(?:.*?) b/(.+)$", re.MULTILINE
)

# Regex to extract the new-file start line from a hunk header.
_HUNK_HEADER_RE: re.Pattern[str] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@"
)

# Config file paths that danger-score scans (D-01).
_CONFIG_FILE = "gate.yaml"
_CONFIG_DIR_PREFIX = ".code-forge/"


def _is_config_path(path: str) -> bool:
    """Check if a file path is a config file we scan (D-01)."""
    return path == _CONFIG_FILE or path.startswith(_CONFIG_DIR_PREFIX)


def danger_score_from_diff(
    diff_text: Optional[str],
) -> list[StateFinding]:
    """Scan diff new-lines for dangerous config fields (L0 blocking).

    Only scans files matching gate.yaml or .code-forge/* (D-01).
    Only scans lines starting with + (excluding +++ header) (D-03).
    Returns L0 CONFIRMED StateFindings with fingerprint
    "danger-score:{file}:{field}:{line}" (D-17).

    Args:
        diff_text: unified diff string, or None in non-git mode (D-16).

    Returns:
        List of StateFinding for each dangerous field found in new-lines.
        Empty list if diff_text is None/empty or contains no config files.
    """
    if not diff_text:
        return []

    results: list[StateFinding] = []

    # Split diff into per-file sections at "diff --git" boundaries.
    # Find all diff --git header positions.
    header_positions: list[int] = []
    for m in _DIFF_HEADER_RE.finditer(diff_text):
        header_positions.append(m.start())

    if not header_positions:
        return []

    # Extract per-file sections.
    sections: list[str] = []
    for i, pos in enumerate(header_positions):
        end = header_positions[i + 1] if i + 1 < len(header_positions) else len(diff_text)
        sections.append(diff_text[pos:end])

    for section in sections:
        # Extract file path from the diff --git header.
        header_match = _DIFF_HEADER_RE.match(section)
        if not header_match:
            continue
        file_path = header_match.group(1)

        # Only process config files (D-01).
        if not _is_config_path(file_path):
            continue

        # Process lines in the section.
        line_number = 0
        for raw_line in section.split("\n"):
            # Check for hunk header.
            hunk_match = _HUNK_HEADER_RE.match(raw_line)
            if hunk_match:
                line_number = int(hunk_match.group(1))
                continue

            # Skip diff metadata lines.
            if raw_line.startswith("diff --git "):
                continue
            if raw_line.startswith("--- "):
                continue
            if raw_line.startswith("+++ "):
                continue
            if raw_line.startswith("\\"):
                continue

            # Process new-lines (D-03): only lines starting with +.
            if raw_line.startswith("+"):
                # Strip the leading '+' (diff syntax, not file content).
                content = raw_line[1:]
                match = _DANGER_PATTERN.match(content)
                if match:
                    field_name = match.group(1)
                    fingerprint = (
                        f"danger-score:{file_path}:{field_name}:{line_number}"
                    )
                    results.append(StateFinding(
                        id=fingerprint,
                        fingerprint=fingerprint,
                        source="L0",
                        disposition=Disposition.CONFIRMED,
                        file=file_path,
                        line_range=[line_number, line_number],
                        description=(
                            f"Dangerous config field '{field_name}' "
                            f"added to {file_path}"
                        ),
                    ))
                line_number += 1
                continue

            # Removed lines: do NOT increment line_number.
            if raw_line.startswith("-"):
                continue

            # Context line (starts with " " or no prefix): increment.
            line_number += 1

    return results


# ---------------------------------------------------------------------------
# TaintRunner advisory axis (sub-capability b)
# ---------------------------------------------------------------------------


def _findings_to_advisories(
    findings: list[Finding],
) -> list[AdvisoryFinding]:
    """Convert parser Finding objects to AdvisoryFinding for the taint axis.

    description is finding.message verbatim -- the intraprocedural caveat
    is already in the semgrep rule message text (Plan 02). Do NOT append
    it again here to avoid double caveat in output.
    """
    advisories: list[AdvisoryFinding] = []
    for f in findings:
        advisories.append(AdvisoryFinding(
            id=f"taint:{f.file}:{f.line}:{f.rule_id}",
            axis="taint",
            file=f.file,
            line_range=[f.line, f.end_line],
            description=f.message,
            attribution="semgrep-ce/intraprocedural",
        ))
    return advisories


class TaintRunner:
    """Advisory axis: semgrep intraprocedural taint detection.

    Satisfies the AxisRunner Protocol (is_advisory=True).
    machine.py sets source_files before dispatch; TaintRunner does NOT
    invoke git (D-09 forward-guard).
    """

    def __init__(self) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle counter."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run semgrep taint analysis on source_files.

        Args:
            diff_text: unified diff (unused by taint; kept for Protocol).
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding from semgrep taint analysis.
            Empty list if semgrep absent, no source files, or error.
        """
        # Prevent cross-run accumulation when runner is reused across cycles.
        self.infra_errors.clear()

        # Guard: no source files provided.
        if self.source_files is None or len(self.source_files) == 0:
            return []

        # Guard: semgrep not installed (D-05, D-06).
        if shutil.which("semgrep") is None:
            msg = "Taint gate requires semgrep. Install: pip install semgrep"
            self.infra_errors.append(msg)
            print(msg, file=sys.stderr)
            return []

        # Filter to Python files only (semgrep taint rules target Python).
        py_files = [
            f for f in self.source_files if str(f).endswith(".py")
        ]
        if not py_files:
            return []

        # Filter to files that exist on disk (paths relative to repo_root).
        py_files = [f for f in py_files if (repo_root / f).exists()]
        if not py_files:
            return []

        # Build semgrep command.
        rules_path = Path(__file__).parent / "rules" / "forge-taint.yaml"
        cmd = [
            "semgrep", "scan",
            "--config", str(rules_path),
            "--sarif",
            "--dataflow-traces",
        ] + [str(f) for f in py_files]

        # Run semgrep with timeout (T-18-04).
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(repo_root),
            )
        except subprocess.TimeoutExpired:
            msg = "semgrep taint scan timed out (120s)"
            self.infra_errors.append(msg)
            return []

        # Exit code >= 2 means semgrep error (not findings).
        if result.returncode >= 2:
            msg = (
                f"semgrep taint scan error (exit {result.returncode}): "
                f"{result.stderr[:200]}"
            )
            self.infra_errors.append(msg)
            return []

        # Parse SARIF output via existing parser.
        parsed = parse_semgrep(result.stdout, exit_code=result.returncode)

        # Separate Finding and ToolError items (D-05: never silent).
        findings: list[Finding] = []
        for item in parsed:
            if isinstance(item, ToolError):
                self.infra_errors.append(item.message)
            elif isinstance(item, Finding):
                findings.append(item)

        return _findings_to_advisories(findings)
