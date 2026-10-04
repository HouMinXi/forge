# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""FIXVAL core module: fix-validation gate.

Proves a diff's new tests are not hollow by reverting non-test changes
and asserting the test goes RED, then restoring and asserting GREEN.

FIXVAL CAN BLOCK -- it gates only the diff's own hollow test.
Overfit guard (STING) is ADVISORY only (never blocking).

Pipeline position: post-convergence, co-located with R2/L2 mutation,
before the verdict.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from io import StringIO
from pathlib import Path

import unidiff

from .advisory import AdvisoryFinding
from ._fixval_transaction import FixvalTransaction, TransactionError
from .disposition import Disposition
from .diff import iter_diff_sections, patched_file_path
from code_forge.baseline_guard import _run_baseline_guard, _strip_venv_from_env
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


# Test file detection patterns (multi-language).
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
    *,
    executable_files: Collection[str] | None = None,
) -> FixvalCandidate | FixvalSkip:
    """Classify a diff as FIXVAL candidate or skip.

    A diff needs both executable tests and semantic non-test changes.
    An optional execution projection excludes removed tests while keeping
    deleted production files as revert facts. Omission keeps all files.
    """
    if not changed_files:
        return FixvalSkip(reason="no files in diff")

    test_files: list[str] = []
    non_test_files: list[str] = []
    executable = None if executable_files is None else set(executable_files)

    for f in changed_files:
        if _is_test_file(f):
            if executable is None or f in executable:
                test_files.append(f)
        else:
            non_test_files.append(f)

    if test_files and non_test_files:
        return FixvalCandidate(
            test_files=test_files,
            non_test_files=non_test_files,
        )
    if not test_files:
        if executable is not None:
            return FixvalSkip(reason="no executable test file in diff")
        return FixvalSkip(reason="no test file in diff")
    return FixvalSkip(reason="no non-test file in diff")


def parse_fixval_waiver(
    commit_message: str,
    env: dict[str, str] | None = None,
) -> str | None:
    """Parse FIXVAL waiver from env var or commit trailer.

    Dual-channel waiver:
      Channel 1 (primary): FIXVAL_WAIVER env var.
      Channel 2: Fixval-Waiver: trailer in commit message.
    Env takes precedence when both present.
    Empty reason (whitespace-only) returns None.

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
    """Project production file blocks without reserializing Git binary bodies."""

    def is_production(patched_file):
        src_clean = patched_file_path(patched_file, source=True) or ""
        tgt_clean = patched_file_path(patched_file) or ""
        return not _is_test_file(src_clean) and not _is_test_file(tgt_clean)

    if any(line.startswith("diff --git ") for line in StringIO(diff_text)):
        blocks = [(unidiff.PatchSet(block), block) for _path, block in iter_diff_sections(diff_text)]
    else:
        blocks = [([entry], str(entry)) for entry in unidiff.PatchSet(diff_text)]
    filtered = []
    production_paths: set[str] = set()
    test_paths: set[str] = set()
    for entries, block in blocks:
        paths = {
            path
            for entry in entries
            for path in (patched_file_path(entry, source=True), patched_file_path(entry))
            if path
        }
        if entries and all(is_production(entry) for entry in entries):
            filtered.append(block)
            production_paths.update(paths)
        else:
            test_paths.update(paths)
    overlap = production_paths & test_paths
    if overlap:
        raise TransactionError(
            "production reversal overlaps excluded test paths: %s" % ", ".join(sorted(overlap))
        )
    return "".join(filtered)


def _transaction_block(message: str, recovery: str) -> FixvalResult:
    detail = "FIXVAL transaction failed: %s. Recovery: %s" % (message, recovery)
    return FixvalResult(
        status=FixvalStatus.BLOCK,
        findings=[
            StateFinding(
                id="FIXVAL_TRANSACTION",
                fingerprint="fixval-transaction",
                source="FIXVAL",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description=detail,
                error=detail,
            )
        ],
        advisories=[],
        block_message=detail,
    )


def _test_reverted_candidate(candidate, scoped_cmd, run_env, repo_root) -> FixvalResult:
    result = subprocess.run(
        scoped_cmd,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
        cwd=repo_root,
    )
    if result.returncode != 0:
        return FixvalResult(status=FixvalStatus.PASS, findings=[], advisories=[])
    block_msg = "FIXVAL: Test(s) did not fail when the fix was reverted.\n\n  Reverted files:\n"
    for filename in candidate.non_test_files:
        block_msg += "    %s\n" % filename
    block_msg += "\n  Tests that should have failed but passed:\n"
    for filename in candidate.test_files:
        block_msg += "    %s\n" % filename
    block_msg += (
        "\n  This means the test passes on both the fixed and unfixed code -- it does\n"
        "  not actually verify the fix.\n\n"
        "  To waive (nondeterministic bug), at pre-commit time use the env var:\n"
        '    FIXVAL_WAIVER="<reason>" git commit ...\n'
        "  and also add the trailer for the permanent git-log record:\n"
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
                file=candidate.test_files[0] if candidate.test_files else "",
                line_range=[],
                description="hollow test: test passes on both fixed and reverted code",
            )
        ],
        advisories=[],
        block_message=block_msg,
    )


def run_fixval(
    candidate: FixvalCandidate,
    test_cmd: list[str],
    cwd: Path,
    commit_message: str,
    diff_text: str | None,
    *,
    recovery_parent: Path | None = None,
) -> FixvalResult:
    """Run FIXVAL gate on a candidate diff.

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
        recovery_parent: optional preservation parent outside participating source roots.

    Returns:
        FixvalResult with status, findings, advisories, block_message.
    """
    repo_root = str(cwd)

    # (a) Guard: no diff available
    if diff_text is None:
        return _make_skipped_result("non-git review, no diff available")

    # (b) Waiver check
    waiver_reason = parse_fixval_waiver(commit_message, env=os.environ)
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
                    description=("FIXVAL waived via %s: %s" % (channel, waiver_reason)),
                    attribution="fixval-waiver",
                ),
            ],
        )

    # Qualify the production reversal before executing tests or allocating recovery.
    try:
        non_test_patch = _filter_non_test_patch(diff_text)
    except (unidiff.errors.UnidiffParseError, TransactionError) as exc:
        return _transaction_block("invalid production patch: %s" % exc, repo_root)
    if not non_test_patch.strip():
        return _make_skipped_result("no non-test changes to revert")

    # Baseline guard
    scoped_cmd = test_cmd + candidate.test_files

    run_env = os.environ.copy()
    pythonpath = os.path.join(repo_root, "src")
    run_env["PYTHONPATH"] = pythonpath

    status, guard_findings, guard_infra = _run_baseline_guard(
        scoped_cmd,
        run_env,
        repo_root,
        allow_strip_retry=True,
    )
    if status == "needs_strip_retry":
        run_env = _strip_venv_from_env(run_env)
        run_env["PYTHONPATH"] = pythonpath
        status, guard_findings, guard_infra = _run_baseline_guard(
            scoped_cmd,
            run_env,
            repo_root,
            allow_strip_retry=False,
        )
    if status == "skip":
        return FixvalResult(
            status=FixvalStatus.SKIPPED,
            findings=guard_findings,
            advisories=[],
        )

    # Write patch to temp file
    try:
        fd, patch_path = tempfile.mkstemp(prefix=".fixval-revert-", suffix=".patch")
    except OSError as exc:
        return _transaction_block("cannot allocate revert patch: %s" % exc, repo_root)
    transaction = None
    forward_applied = False
    errors = []
    interrupted = None
    outcome = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(non_test_patch)
        preservation = {"recovery_parent": recovery_parent} if recovery_parent is not None else {}
        transaction = FixvalTransaction(cwd, non_test_patch, **preservation)
        transaction.prepare()
        revert_result = transaction.reverse(patch_path)
        if revert_result.returncode != 0:
            errors.append("revert patch failed: %s" % revert_result.stderr[:200])
        else:
            transaction.mark_reverted()
            outcome = _test_reverted_candidate(candidate, scoped_cmd, run_env, repo_root)
    except (OSError, ValueError, TransactionError, subprocess.SubprocessError) as exc:
        errors.append(str(exc))
    except (KeyboardInterrupt, SystemExit) as exc:
        interrupted = exc
    finally:
        if transaction is not None:
            try:
                if transaction.reverted:
                    if transaction.can_apply_forward():
                        restored = subprocess.run(
                            ["git", "apply", "--check", patch_path],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            check=False,
                            cwd=repo_root,
                            env=transaction.git_environment(),
                        )
                        forward_validated = restored.returncode == 0
                        if not forward_validated:
                            errors.append("forward patch failed: %s" % restored.stderr[:200])
                    else:
                        errors.append("source entries changed during reverted tests")
            except (OSError, TransactionError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupted = interrupted or exc
                errors.append("forward restoration interrupted: %s" % type(exc).__name__)
            try:
                try:
                    errors.extend(transaction.restore(forward_applied))
                except (OSError, TransactionError) as exc:
                    errors.append("entry restoration failed: %s" % exc)
                except (KeyboardInterrupt, SystemExit) as exc:
                    interrupted = interrupted or exc
                    errors.append("entry restoration interrupted: %s" % type(exc).__name__)
                    # One bounded retry finishes known entries after cancellation;
                    # repeated cancellation retains recovery and remains visible.
                    try:
                        errors.extend(transaction.restore(forward_applied))
                    except (OSError, TransactionError) as retry:
                        errors.append("entry restoration retry failed: %s" % retry)
                    except (KeyboardInterrupt, SystemExit) as retry:
                        errors.append("entry restoration retry interrupted: %s" % type(retry).__name__)
            finally:
                if sys.exception() is not None:
                    errors.append("entry restoration escaped: %s" % type(sys.exception()).__name__)
                errors.extend(transaction.image_errors)
                transaction.recovery_needed = transaction.recovery_needed or bool(errors)
                try:
                    transaction.close()
                except (OSError, TransactionError) as exc:
                    errors.append("transaction cleanup failed: %s" % exc)
                except (KeyboardInterrupt, SystemExit) as exc:
                    interrupted = interrupted or exc
                    errors.append("transaction cleanup interrupted: %s" % type(exc).__name__)
        if not errors:
            try:
                os.unlink(patch_path)
            except OSError as exc:
                errors.append("patch cleanup failed: %s" % exc)
    recovery = (
        transaction.recovery_location
        if transaction is not None
        and transaction.recovery_location
        and (
            transaction.recovery_needed
            or transaction.directory is not None
            and transaction.directory.exists()
        )
        else patch_path
    )
    if errors:
        _logger.error("FIXVAL: restore failed: %s; recovery: %s", "; ".join(errors), recovery)
    if interrupted is not None:
        if errors:
            interrupted.add_note("FIXVAL recovery: %s (%s)" % (recovery, "; ".join(errors)))
        raise interrupted
    if errors:
        return _transaction_block("; ".join(errors), recovery)
    return outcome


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
                target.id = self.new_name  # rename the target itself
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
    """Run STING overfit guard: advisory only, never blocking.

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
                encoding="utf-8",
                errors="replace",
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
