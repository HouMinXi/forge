# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Mutation testing integration via mutmut subprocess.

Python-only MVP. Runs mutmut on diff-scoped files and parses survivors.
Swappable design: keep subprocess calls in one place so future language
runners (cargo-mutants, go-mutesting) can replace implementation without
changing the l2_runner interface.

mutmut 3.x integration notes:
- No --paths-to-mutate CLI flag; use setup.cfg [mutmut] paths_to_mutate.
- Run cwd MUST be the project root (where src/ and tests/ live).
- paths_to_mutate must be relative to the project root.
- PYTHONPATH must point into src/ so imports resolve without src. prefix.
- results format: "    module.fn__mutmut_N: status" (one per line).
- Any non-zero exit code from mutmut run is a hard error.
- A temporary setup.cfg is written to project root and cleaned up after.
- mutants/ directory created by mutmut is also cleaned up after each run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .disposition import Disposition
from .state import StateFinding

# Marker in setup.cfg so we never accidentally overwrite user config
_CODE_FORGE_CFG_MARKER = "# managed-by-code-forge-mutation"


@dataclass
class Survivor:
    """A mutant that survived (no test killed it)."""
    mutant_name: str  # mutmut 3.x identifier e.g. "code_forge.mutation.x_run__mutmut_1"
    file: str         # source file (empty; mutmut 3.x results omit file paths)


def parse_mutmut_results(stdout: str) -> tuple[list[Survivor], list[str]]:
    """Parse mutmut 3.x results output to extract surviving mutants.

    Expected format from mutmut 3.x results command:
        {indent}{module}.{mangled_fn}__mutmut_{N}: {status}

    Where status is one of: survived, killed, no tests, not checked,
    timeout, suspicious, check was interrupted by user.

    Only lines with status "survived" are returned as Survivor objects.

    The mutant_name is the full identifier (e.g. "add.x_add__mutmut_1").
    The file field is empty string since mutmut 3.x results do not include
    file paths; callers that need file attribution must use py_files list.

    Args:
        stdout: raw stdout from mutmut results subprocess

    Returns:
        tuple[list[Survivor], list[str]] where second element is warnings
        about unparseable lines. Never raises.
    """
    survivors = []
    warnings = []

    for line in stdout.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Format: "module.fn__mutmut_N: status"
        if ": " not in stripped:
            continue

        mutant_name, _, status = stripped.partition(": ")
        mutant_name = mutant_name.strip()
        status = status.strip()

        if not mutant_name:
            continue

        if status == "survived":
            survivors.append(Survivor(mutant_name=mutant_name, file=""))

    return survivors, warnings


def run_mutation(
    diff_files: list[str],
    baseline_cmd: list[str],
    timeout: int = 600,
    cwd: Path | None = None,
) -> tuple[list[StateFinding], list[str]]:
    """Run mutation testing on diff-scoped files.

    Returns tuple[list[StateFinding], list[str]] matching l0_runner
    signature for consistency.

    Args:
        diff_files: changed files from git diff --name-only (relative to cwd)
        baseline_cmd: test command to run for flaky guard and mutmut
        timeout: mutmut run timeout in seconds (default 600)
        cwd: project root for mutmut (default: Path.cwd()). Must be the
            directory containing src/ and tests/.

    Implementation note:
        mutmut 3.x requires cwd to be the project root. A temporary
        setup.cfg is written there with paths_to_mutate pointing to the
        diff-scoped files. The mutants/ directory and temporary setup.cfg
        are cleaned up after each run.

    Returns:
        (findings, infra_errors) where findings contains:
        - CONFIRMED MUTANT findings for survivors
        - DISMISSED MUTATION_SKIPPED findings for skip conditions
    """
    findings: list[StateFinding] = []
    infra_errors: list[str] = []

    if cwd is None:
        cwd = Path.cwd()
    repo_root = str(cwd.resolve())

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

    # Flaky guard: run baseline 3x from repo root
    run_env = os.environ.copy()
    run_env["PYTHONPATH"] = os.path.join(repo_root, "src")

    for run_num in range(1, 4):
        try:
            result = subprocess.run(
                baseline_cmd,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=repo_root,
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
                            "run %d: tests flaky, mutation unreliable "
                            "(3x baseline check)" % run_num
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

    # Refuse to overwrite user's existing setup.cfg or [tool.mutmut] config.
    setup_cfg_path = os.path.join(repo_root, "setup.cfg")
    pyproject_path = os.path.join(repo_root, "pyproject.toml")
    wrote_setup_cfg = False
    try:
        has_user_setup_cfg = False
        if os.path.exists(setup_cfg_path):
            with open(setup_cfg_path, encoding="utf-8") as _fh:
                has_user_setup_cfg = _CODE_FORGE_CFG_MARKER not in _fh.read()
        has_user_pyproject_mutmut = False
        if os.path.exists(pyproject_path):
            try:
                with open(pyproject_path, encoding="utf-8") as fh:
                    raw = fh.read()
                has_user_pyproject_mutmut = "[tool.mutmut]" in raw
            except OSError:
                pass

        if has_user_setup_cfg or has_user_pyproject_mutmut:
            infra_errors.append(
                "mutmut config conflict: project already has [mutmut] or "
                "[tool.mutmut] config; forge cannot override it safely"
            )
            findings.append(
                StateFinding(
                    id="MUTATION_SKIPPED",
                    fingerprint="mutation-config-conflict",
                    source="MUTANT",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description=(
                        "mutmut config conflict: existing setup.cfg or "
                        "pyproject.toml [tool.mutmut] detected"
                    ),
                )
            )
            return (findings, infra_errors)

        # Write temporary setup.cfg to project root.
        # paths_to_mutate are relative to the project root.
        config_content = (
            "%s\n"
            "[mutmut]\n"
            "paths_to_mutate=%s\n"
            % (_CODE_FORGE_CFG_MARKER, ",".join(py_files))
        )

        # Write to a temp file first, then rename for atomicity
        fd, tmp_cfg = tempfile.mkstemp(
            prefix=".code-forge-mutation-cfg-",
            dir=repo_root,
            suffix=".cfg",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(config_content)
            os.rename(tmp_cfg, setup_cfg_path)
            wrote_setup_cfg = True
        except OSError:
            try:
                os.unlink(tmp_cfg)
            except OSError:
                pass
            raise

        try:
            result = subprocess.run(
                ["mutmut", "run"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=run_env,
                cwd=repo_root,
            )

            # Any non-zero exit is an error (attempt 2 bug: only caught ==2)
            if result.returncode != 0:
                findings.append(
                    StateFinding(
                        id="MUTATION_ERROR",
                        fingerprint="mutation-invocation-error",
                        source="MUTANT",
                        disposition=Disposition.CONFIRMED,
                        file="",
                        line_range=[],
                        description=(
                            "mutmut run failed (exit %d): %s"
                            % (result.returncode, result.stderr[:200])
                        ),
                    )
                )
                infra_errors.append(
                    "mutmut error (exit %d): %s"
                    % (result.returncode, result.stderr[:100])
                )
                return (findings, infra_errors)

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

        # Parse results from repo_root
        try:
            results_proc = subprocess.run(
                ["mutmut", "results"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                cwd=repo_root,
                env=run_env,
            )
            survivors, parse_warnings = parse_mutmut_results(results_proc.stdout)
            infra_errors.extend(parse_warnings)
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
                    id="mutant-%s" % survivor.mutant_name,
                    fingerprint="mutant:%s" % survivor.mutant_name,
                    source="MUTANT",
                    disposition=Disposition.CONFIRMED,
                    file=survivor.file,
                    line_range=[0, 0],  # mutmut 3.x results omit line numbers
                    description=(
                        "mutant survived: %s" % survivor.mutant_name
                    ),
                )
            )

    finally:
        # Clean up temporary setup.cfg and mutants/ directory
        if wrote_setup_cfg:
            try:
                os.unlink(setup_cfg_path)
            except OSError:
                pass
        mutants_dir = os.path.join(repo_root, "mutants")
        shutil.rmtree(mutants_dir, ignore_errors=True)

    return (findings, infra_errors)
