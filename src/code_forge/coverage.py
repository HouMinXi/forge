# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Per-file review coverage gate.

Forge's substantive per-file review layers are L0 (deterministic linters,
selected by ToolConfig.file_patterns) and L1 (LLM semantic review over a
git diff).  A file that NO layer examined was not reviewed -- emitting a
clean PASS for it is a false green, the exact failure mode forge's
anti-hallucination thesis forbids.

This module computes which in-scope files lack any review coverage so the
state machine can refuse a silent pass:
  - CI mode: a coverage gap makes the verdict FAIL (CI is binary).
  - LOCAL mode: gaps surface as UNCERTAIN findings that enter HOLD for
    human disposition (escape hatch), mirroring the R3 e2e heuristic.

L1 coverage is a single review-wide boolean (`l1_active`): when L1 ran
over the diff it examined every changed file, so all files are covered.
`l1_active` is computed by the CLI as (engine != "stub") and a non-empty
git diff; a non-git review or the stub engine leaves L1 inactive, at
which point only L0 tool matches provide coverage.

Known v1 limitation: a git review whose configured backend is
unreachable yields no L1 findings but is still treated as L1-covered
(l1_active is diff-based, not reachability-based).  Detecting an
unreachable backend as a coverage gap is deferred.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

import yaml

from .disposition import Disposition
from .errors import CoverageConfigError
from .registry import ToolConfig, match_tools
from .state import StateFinding


def compute_uncovered_files(
    source_files: list[str],
    registry: dict[str, ToolConfig],
    l1_active: bool,
    exempt_patterns: Optional[list[str]] = None,
) -> list[str]:
    """Return in-scope files that no review layer examined.

    A file is covered when either:
      - an enabled L0 tool's file_patterns match it, or
      - L1 ran over the diff (``l1_active=True``); L1 sees every changed
        file, so all files are covered.

    Files matching an entry in ``exempt_patterns`` are never reported
    (opt-out via .code-forge/coverage.yaml).  The result preserves
    ``source_files`` order and is de-duplicated.

    Args:
        source_files: files under review (string paths).
        registry: {name: ToolConfig} of enabled L0 tools.
        l1_active: True when the L1 semantic pass ran over the diff.
        exempt_patterns: glob patterns whose matches are never reported.

    Returns:
        Ordered, de-duplicated list of uncovered file paths.
    """
    if l1_active:
        return []

    exempt = exempt_patterns or []
    matched: set[str] = set()
    for files in match_tools(registry, source_files).values():
        matched.update(files)

    uncovered: list[str] = []
    seen: set[str] = set()
    for filepath in source_files:
        if filepath in matched or filepath in seen:
            continue
        if any(fnmatch(filepath, pattern) for pattern in exempt):
            continue
        seen.add(filepath)
        uncovered.append(filepath)
    return uncovered


def build_coverage_findings(
    uncovered_files: list[str],
) -> list[StateFinding]:
    """Build one UNCERTAIN coverage StateFinding per uncovered file.

    Findings use source="COVERAGE" and a stable, file-scoped fingerprint
    ("coverage:<file>") that cannot collide with L0/L1/L2/E2E
    fingerprints.  UNCERTAIN routes LOCAL mode into HOLD; the CI verdict
    treats COVERAGE findings as a gate (see machine._run_ci).
    """
    findings: list[StateFinding] = []
    for filepath in uncovered_files:
        findings.append(StateFinding(
            id="coverage-" + filepath,
            fingerprint="coverage:" + filepath,
            source="COVERAGE",
            disposition=Disposition.UNCERTAIN,
            file=filepath,
            line_range=[0, 0],
            description=(
                "no review layer examined this file: no linter in "
                "tools.yaml matches it and L1 semantic review did not run "
                "(non-git review or stub engine). forge cannot vouch for "
                "it. Configure a matching linter, review it in git mode, "
                "or exempt it in .code-forge/coverage.yaml."
            ),
        ))
    return findings


def load_coverage_exempt_patterns(repo_root: Path) -> list[str]:
    """Load optional .code-forge/coverage.yaml exempt_patterns.

    The file is opt-in: absence returns [] (normal, not an error).
    Schema::

        version: 1
        exempt_patterns:
          - "*.txt"
          - "docs/*"

    Args:
        repo_root: repository root (the directory holding .code-forge/).

    Returns:
        List of glob patterns (possibly empty).

    Raises:
        CoverageConfigError: when the file is present but malformed.
            Every message starts "coverage.yaml: ".
    """
    config_path = repo_root / ".code-forge" / "coverage.yaml"
    if not config_path.exists():
        return []

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise CoverageConfigError(
            "coverage.yaml: YAML parse error: %s" % e
        ) from e

    if not isinstance(data, dict):
        raise CoverageConfigError(
            "coverage.yaml: top-level value must be a mapping"
        )

    version = data.get("version")
    if version != 1:
        raise CoverageConfigError(
            "coverage.yaml: version: expected 1, got %r" % version
        )

    patterns = data.get("exempt_patterns", [])
    if not isinstance(patterns, list):
        raise CoverageConfigError(
            "coverage.yaml: 'exempt_patterns' must be a list"
        )
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise CoverageConfigError(
                "coverage.yaml: 'exempt_patterns' entries must be strings, "
                "got %r" % type(pattern).__name__
            )
    return patterns
