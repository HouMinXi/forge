# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL core module: fix-validation gate.

Proves a diff's new tests are not hollow by reverting non-test changes
and asserting the test goes RED, then restoring and asserting GREEN.

FIXVAL CAN BLOCK -- it gates only the diff's own hollow test.
Overfit guard (STING) is ADVISORY only (D-03).

Pipeline position: post-convergence, co-located with R2/L2 mutation,
before the verdict (D-06).
"""
from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import unidiff

from .advisory import AdvisoryFinding
from .disposition import Disposition
from .mutation import _run_baseline_guard, _strip_venv_from_env
from .state import StateFinding

_logger = logging.getLogger("code_forge")


class FixvalStatus(str, Enum):
    """FIXVAL gate result status."""
    PASS = "PASS"
    BLOCK = "BLOCK"
    SKIPPED = "SKIPPED"
    WAIVED = "WAIVED"


@dataclass(frozen=True)
class FixvalCandidate:
    """A diff that has both test and non-test files -- FIXVAL applicable."""
    test_files: list[str]
    non_test_files: list[str]


@dataclass(frozen=True)
class FixvalSkip:
    """A diff that is not a FIXVAL candidate, with reason."""
    reason: str


@dataclass(frozen=True)
class FixvalResult:
    """Result of running FIXVAL on a candidate diff.

    findings: StateFinding list consumed by machine.py.
      BLOCK -> one DISMISSED (block via Verdict.FAIL, not CONFIRMED).
      PASS -> empty. SKIPPED -> one DISMISSED. WAIVED -> one DISMISSED.
    advisories: AdvisoryFinding list (waiver record, overfit guard).
    block_message: non-empty only for BLOCK status.
    """
    status: FixvalStatus
    findings: list[StateFinding]
    advisories: list[AdvisoryFinding]
    block_message: str = ""


# D-07: test file detection patterns (multi-language).
# Order: longest regex alternative first per project convention.
_TEST_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(^|/)tests/test_[^/]+\.py$"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.py$"),
    re.compile(r"\.test\.ts$"),
    re.compile(r"\.spec\.ts$"),
    re.compile(r"_test\.go$"),
)


def _is_test_file(path: str) -> bool:
    """Return True if path matches any test file pattern."""
    for pattern in _TEST_PATTERNS:
        if pattern.search(path):
            return True
    return False


def classify_fixval_candidate(
    changed_files: list[str],
) -> FixvalCandidate | FixvalSkip:
    """Classify a diff as FIXVAL candidate or skip (D-01).

    A diff is a candidate if and only if it has BOTH test and non-test
    files. When only one kind is present, return FixvalSkip with reason.
    """
    if not changed_files:
        return FixvalSkip(reason="no files in diff")

    test_files: list[str] = []
    non_test_files: list[str] = []

    for f in changed_files:
        if _is_test_file(f):
            test_files.append(f)
        else:
            non_test_files.append(f)

    if test_files and non_test_files:
        return FixvalCandidate(
            test_files=test_files,
            non_test_files=non_test_files,
        )
    if not test_files:
        return FixvalSkip(reason="no test file in diff")
    return FixvalSkip(reason="no non-test file in diff")


def parse_fixval_waiver(
    commit_message: str,
    env: dict[str, str] | None = None,
) -> str | None:
    """Parse FIXVAL waiver from env var or commit trailer (D-04).

    Dual-channel waiver:
      Channel 1 (primary): FIXVAL_WAIVER env var.
      Channel 2: Fixval-Waiver: trailer in commit message.
    Env takes precedence when both present.
    Empty reason (whitespace-only) returns None (D-04: reason required).

    Args:
        commit_message: the commit message to scan for trailer.
        env: environment dict (defaults to None = no env check).
             Pass os.environ at call site for real env lookup.

    Returns:
        Waiver reason string, or None if no waiver.
    """
    # Channel 1: env var (primary at pre-commit time)
    if env is not None:
        env_val = env.get("FIXVAL_WAIVER", "")
        if env_val.strip():
            return env_val.strip()

    # Channel 2: commit trailer (case-insensitive)
    for line in commit_message.splitlines():
        match = re.match(
            r"^fixval-waiver:\s*(.*)",
            line,
            re.IGNORECASE,
        )
        if match:
            reason = match.group(1).strip()
            if reason:
                return reason

    return None


def _make_skipped_result(reason: str) -> FixvalResult:
    """Create a SKIPPED result with one DISMISSED finding."""
    return FixvalResult(
        status=FixvalStatus.SKIPPED,
        findings=[
            StateFinding(
                id="FIXVAL_SKIPPED",
                fingerprint="fixval-skipped",
                source="FIXVAL",
                disposition=Disposition.DISMISSED,
                file="",
                line_range=[],
                description=reason,
            ),
        ],
        advisories=[],
    )


def _filter_non_test_patch(diff_text: str) -> str:
    """Parse diff_text via unidiff, keep only non-test file hunks."""
    patch_set = unidiff.PatchSet(diff_text)
    filtered = []
    for patched_file in patch_set:
        src = patched_file.source_file
        tgt = patched_file.target_file
        # Strip a/ b/ prefixes for test detection
        src_clean = re.sub(r"^[ab]/", "", src) if src else ""
        tgt_clean = re.sub(r"^[ab]/", "", tgt) if tgt else ""
        if not _is_test_file(src_clean) and not _is_test_file(tgt_clean):
            filtered.append(str(patched_file))
    return "".join(filtered)


def run_fixval(
    candidate: FixvalCandidate,
    test_cmd: list[str],
    cwd: Path,
    commit_message: str,
    diff_text: str | None,
) -> FixvalResult:
    """Run FIXVAL gate on a candidate diff (D-02).

    Steps:
      a. Guard: diff_text None -> SKIPPED
      b. Waiver check -> WAIVED with advisory
      c. Baseline guard (3x flaky check)
      d. Revert non-test hunks via git apply -R
      e. Run scoped test cmd; FAIL -> PASS, PASS -> BLOCK
      f. Restore via git apply (forward re-apply) in finally

    Args:
        candidate: classified FIXVAL candidate (test + non-test files).
        test_cmd: base test command (e.g. ["python", "-m", "pytest"]).
        cwd: repository root.
        commit_message: for waiver trailer parsing.
        diff_text: unified diff text (same source as classify).

    Returns:
        FixvalResult with status, findings, advisories, block_message.
    """
    repo_root = str(cwd)

    # (a) Guard: no diff available
    if diff_text is None:
        return _make_skipped_result(
            "non-git review, no diff available"
        )

    # (b) Waiver check
    waiver_reason = parse_fixval_waiver(
        commit_message, env=os.environ
    )
    if waiver_reason is not None:
        if os.environ.get("FIXVAL_WAIVER", "").strip():
            channel = "FIXVAL_WAIVER env var"
        else:
            channel = "Fixval-Waiver trailer"
        return FixvalResult(
            status=FixvalStatus.WAIVED,
            findings=[
                StateFinding(
                    id="FIXVAL_WAIVED",
                    fingerprint="fixval-waived",
                    source="FIXVAL",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="FIXVAL waived: %s" % waiver_reason,
                ),
            ],
            advisories=[
                AdvisoryFinding(
                    id="FIXVAL_WAIVER_RECORD",
                    axis="FIXVAL",
                    file="",
                    line_range=[],
                    description=(
                        "FIXVAL waived via %s: %s"
                        % (channel, waiver_reason)
                    ),
                    attribution="fixval-waiver",
                ),
            ],
        )

    # (c) Baseline guard
    scoped_cmd = test_cmd + candidate.test_files

    run_env = os.environ.copy()
    pythonpath = os.path.join(repo_root, "src")
    run_env["PYTHONPATH"] = pythonpath

    status, guard_findings, guard_infra = _run_baseline_guard(
        scoped_cmd, run_env, repo_root, allow_strip_retry=True,
    )
    if status == "needs_strip_retry":
        run_env = _strip_venv_from_env(run_env)
        run_env["PYTHONPATH"] = pythonpath
        status, guard_findings, guard_infra = _run_baseline_guard(
            scoped_cmd, run_env, repo_root, allow_strip_retry=False,
        )
    if status == "skip":
        return FixvalResult(
            status=FixvalStatus.SKIPPED,
            findings=guard_findings,
            advisories=[],
        )

    # (d) Revert non-test hunks
    non_test_patch = _filter_non_test_patch(diff_text)
    if not non_test_patch.strip():
        return _make_skipped_result(
            "no non-test changes to revert"
        )

    # Write patch to temp file
    fd, patch_path = tempfile.mkstemp(
        prefix=".fixval-revert-", suffix=".patch",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(non_test_patch)

        # Apply reverse patch (revert non-test changes)
        revert_result = subprocess.run(
            ["git", "apply", "-R", patch_path],
            capture_output=True,
            text=True,
            check=False,
            cwd=repo_root,
        )
        if revert_result.returncode != 0:
            return _make_skipped_result(
                "revert patch failed: %s" % revert_result.stderr[:200]
            )

        try:
            # (e) Run scoped tests on reverted code
            try:
                test_result = subprocess.run(
                    scoped_cmd,
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                    cwd=repo_root,
                )
                test_passed = test_result.returncode == 0
            except subprocess.TimeoutExpired:
                return _make_skipped_result(
                    "test timed out during FIXVAL revert check"
                )

            if not test_passed:
                # Test failed on revert -> PASS (not hollow)
                return FixvalResult(
                    status=FixvalStatus.PASS,
                    findings=[],
                    advisories=[],
                )

            # Test passed on revert -> BLOCK (hollow test)
            block_msg = (
                "FIXVAL: Test(s) did not fail when the fix was reverted.\n"
                "\n"
                "  Reverted files:\n"
            )
            for f in candidate.non_test_files:
                block_msg += "    %s\n" % f
            block_msg += (
                "\n"
                "  Tests that should have failed but passed:\n"
            )
            for f in candidate.test_files:
                block_msg += "    %s\n" % f
            block_msg += (
                "\n"
                "  This means the test passes on both the fixed and "
                "unfixed code -- it does\n"
                "  not actually verify the fix.\n"
                "\n"
                "  To waive (nondeterministic bug), at pre-commit "
                "time use the env var:\n"
                '    FIXVAL_WAIVER="<reason>" git commit ...\n'
                "  and also add the trailer for the permanent "
                "git-log record:\n"
                "    Fixval-Waiver: <reason>\n"
            )

            return FixvalResult(
                status=FixvalStatus.BLOCK,
                findings=[
                    StateFinding(
                        id="FIXVAL_HOLLOW",
                        fingerprint="fixval-hollow",
                        source="FIXVAL",
                        disposition=Disposition.DISMISSED,
                        file=candidate.test_files[0]
                        if candidate.test_files
                        else "",
                        line_range=[],
                        description=(
                            "hollow test: test passes on both fixed "
                            "and reverted code"
                        ),
                    ),
                ],
                advisories=[],
                block_message=block_msg,
            )

        finally:
            # (f) Restore: forward re-apply (never git checkout --)
            _restore = subprocess.run(
                ["git", "apply", patch_path],
                capture_output=True,
                text=True,
                check=False,
                cwd=repo_root,
            )
            if _restore.returncode != 0:
                _logger.error(
                    "FIXVAL: restore failed -- working tree may be "
                    "left in reverted state. Run 'git apply %s' to "
                    "restore manually. stderr: %s",
                    patch_path,
                    _restore.stderr[:200],
                )

    finally:
        # Clean up temp patch file
        try:
            os.unlink(patch_path)
        except OSError:
            pass


class _VariableRenamer(ast.NodeTransformer):
    """AST transform: rename the first local variable found.

    Appends '_renamed' suffix to one Name target in an assignment.
    Only renames within function bodies (local scope).
    """

    def __init__(self) -> None:
        super().__init__()
        self.renamed: str | None = None
        self.new_name: str | None = None
        self._done = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """Visit function definitions to find local variables."""
        self.generic_visit(node)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        """Rename the first simple Name target found."""
        if self._done:
            return node
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.renamed = target.id
                self.new_name = target.id + "_renamed"
                self._done = True
                break
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        """Rename all occurrences of the renamed variable."""
        if self.renamed and node.id == self.renamed:
            node.id = self.new_name
        return node


def run_overfit_guard(
    candidate: FixvalCandidate,
    test_cmd: list[str],
    cwd: Path,
) -> list[AdvisoryFinding]:
    """Run STING overfit guard (D-03): advisory only, never blocking.

    Applies a variable-rename transform to the first .py file in
    non_test_files. If the test breaks after rename, it is overfitting
    to variable names.

    Original file bytes are saved before any transform and restored
    verbatim in the finally block (never re-unparse -- ast.unparse
    is lossy, strips comments/formatting).

    Args:
        candidate: FIXVAL candidate with test and non-test files.
        test_cmd: base test command.
        cwd: repository root.

    Returns:
        List of AdvisoryFinding (0 or 1 items). Empty if test passes
        after rename or no .py files found.
    """
    # Find first .py file in non_test_files
    py_file = None
    for f in candidate.non_test_files:
        if f.endswith(".py"):
            py_file = f
            break

    if py_file is None:
        return []

    file_path = Path(py_file)
    if not file_path.is_absolute():
        file_path = Path(cwd) / file_path

    if not file_path.exists():
        return []

    # Save original bytes before any transform
    original_bytes = file_path.read_bytes()

    try:
        source = original_bytes.decode("utf-8")
        tree = ast.parse(source)

        renamer = _VariableRenamer()
        transformed = renamer.visit(tree)

        if renamer.renamed is None:
            # No local variable found to rename
            return []

        # Validate transform before writing
        ast.fix_missing_locations(transformed)
        new_source = ast.unparse(transformed)
        # Verify the transformed code parses
        ast.parse(new_source)

        # Write transformed code
        file_path.write_text(new_source, encoding="utf-8")

        try:
            # Run test (match run_fixval: set PYTHONPATH=src/ so imports work)
            scoped_cmd = test_cmd + candidate.test_files
            run_env = os.environ.copy()
            run_env["PYTHONPATH"] = os.path.join(str(cwd), "src")
            result = subprocess.run(
                scoped_cmd,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                cwd=str(cwd),
            )

            if result.returncode != 0:
                # Test failed after rename -> overfitting advisory
                return [
                    AdvisoryFinding(
                        id="FIXVAL_OVERFIT",
                        axis="FIXVAL",
                        file=py_file,
                        line_range=[],
                        description=(
                            "test may be overfitting to variable names: "
                            "renamed '%s' -> '%s' in %s and test broke"
                            % (renamer.renamed, renamer.new_name, py_file)
                        ),
                        attribution="fixval-overfit-guard",
                    ),
                ]

            # Test still passes -> not overfitting
            return []

        finally:
            # ALWAYS restore original bytes verbatim
            file_path.write_bytes(original_bytes)

    except SyntaxError:
        # Cannot parse file -> skip overfit guard
        return []
    finally:
        # Double-ensure original bytes are restored
        if file_path.exists():
            current = file_path.read_bytes()
            if current != original_bytes:
                file_path.write_bytes(original_bytes)
