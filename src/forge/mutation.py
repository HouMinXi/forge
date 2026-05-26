# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Mutation testing integration via mutmut subprocess.

Python-only MVP. Runs mutmut on diff-scoped files and parses survivors.
Swappable design: keep subprocess calls in one place so future language
runners (cargo-mutants, go-mutesting) can replace implementation without
changing the l2_runner interface.

D-02: Direct subprocess.run of mutmut run + mutmut results.
D-05: mutmut is a soft dependency. Missing binary produces MUTATION_SKIPPED.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .disposition import Disposition
from .state import StateFinding


@dataclass
class Survivor:
    """A mutant that survived (no test killed it)."""
    file: str
    mutant_id: int


def parse_mutmut_results(stdout: str) -> list[Survivor]:
    """Parse mutmut results output to extract surviving mutants.

    Expected format from mutmut 3.x:
        Survived (N)
        ---- ./file.py (M) ----
        id1, id2-id3

    Handles:
        - Comma-separated IDs: "1, 3, 5" -> [1, 3, 5]
        - Ranges: "1-3" -> [1, 2, 3]
        - Mixed: "1, 3-5, 7" -> [1, 3, 4, 5, 7]

    Args:
        stdout: raw stdout from mutmut results subprocess

    Returns:
        list of Survivor instances. Empty list on unparseable input.
        Never raises.
    """
    survivors = []
    current_file = None

    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue

        # File marker: "---- ./file.py (N) ----"
        if line.startswith("----") and line.endswith("----"):
            parts = line.split()
            if len(parts) >= 2:
                current_file = parts[1]
            continue

        # ID line: "1, 2-3, 5"
        if current_file is None:
            continue

        # Parse IDs
        for segment in line.split(","):
            segment = segment.strip()
            if not segment:
                continue

            if "-" in segment:
                # Range: "1-3" -> [1, 2, 3]
                try:
                    start_str, end_str = segment.split("-", 1)
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    for mutant_id in range(start, end + 1):
                        survivors.append(
                            Survivor(file=current_file, mutant_id=mutant_id)
                        )
                except ValueError:
                    # Unparseable range, skip
                    continue
            else:
                # Single ID
                try:
                    mutant_id = int(segment)
                    survivors.append(
                        Survivor(file=current_file, mutant_id=mutant_id)
                    )
                except ValueError:
                    # Unparseable ID, skip
                    continue

    return survivors


def run_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    timeout: int = 600,
) -> tuple[list[StateFinding], list[str]]:
    """Run mutation testing on diff-scoped files.

    Returns tuple[list[StateFinding], list[str]] matching l0_runner
    signature for consistency.

    Args:
        diff_files: changed files from git diff --name-only
        baseline_cmd: test command to run for flaky guard and mutmut
        timeout: mutmut run timeout in seconds (default 600)

    Returns:
        (findings, infra_errors) where findings contains:
        - CONFIRMED MUTANT findings for survivors
        - DISMISSED MUTATION_SKIPPED findings for skip conditions
    """
    findings: list[StateFinding] = []
    infra_errors: list[str] = []

    # Empty files: no work
    if not diff_files:
        return ([], [])

    # Filter to .py files only (Python MVP)
    py_files = [f for f in diff_files if f.endswith(".py")]
    if not py_files:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-no-python",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="no Python files in diff (mutation is Python-only MVP)",
            )
        )
        infra_errors.append("no Python files in diff")
        return (findings, infra_errors)

    # Flaky guard: run baseline 3x
    for run_num in range(1, 4):
        try:
            result = subprocess.run(
                baseline_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                findings.append(
                    StateFinding(
                        id="MUTATION_SKIPPED",
                        fingerprint="mutation-flaky",
                        source="MUTANT",
                        disposition=Disposition.DISMISSED,
                        file="",
                        line_range=[],
                        description=(
                            "tests flaky, mutation unreliable "
                            "(3x baseline check)"
                        ),
                    )
                )
                infra_errors.append(
                    "flaky guard: baseline failed on run %d" % run_num
                )
                return (findings, infra_errors)
        except subprocess.TimeoutExpired:
            findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-baseline-timeout",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="baseline tests timed out (flaky guard)",
                )
            )
            infra_errors.append(
                "flaky guard: baseline timeout on run %d" % run_num
            )
            return (findings, infra_errors)

    # Check mutmut availability
    if shutil.which("mutmut") is None:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-unavailable",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="mutmut not installed (soft dependency)",
            )
        )
        return (findings, [])

    # Run mutmut
    try:
        subprocess.run(
            ["mutmut", "run", "--paths-to-mutate"] + py_files,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-timeout",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="mutmut timed out after %ds" % timeout,
            )
        )
        return (findings, [])

    # Parse results
    try:
        results_proc = subprocess.run(
            ["mutmut", "results"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        survivors = parse_mutmut_results(results_proc.stdout)
    except subprocess.TimeoutExpired:
        findings.append(
            StateFinding(
                id="MUTATION_SKIPPED",
                fingerprint="mutation-results-timeout",
                source="MUTANT",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description="mutmut results timed out",
            )
        )
        return (findings, [])

    # Convert survivors to findings
    for survivor in survivors:
        findings.append(
            StateFinding(
                id="mutant-%s-%d" % (survivor.file, survivor.mutant_id),
                fingerprint="mutant:%s:%d" % (survivor.file, survivor.mutant_id),
                source="MUTANT",
                disposition=Disposition.CONFIRMED,
                file=survivor.file,
                line_range=[0, 0],  # mutmut does not report line numbers
                description=(
                    "mutant %d survived in %s"
                    % (survivor.mutant_id, survivor.file)
                ),
            )
        )

    return (findings, infra_errors)
