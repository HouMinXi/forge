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
import difflib
import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

import unidiff

from .advisory import AdvisoryFinding
from ._fixval_transaction import FixvalTransaction, TransactionError
from ._mutation_process import MutationProcessError
from .disposition import Disposition
from .diff import iter_diff_sections, patched_file_path
from code_forge.baseline_guard import _strip_venv_from_env, _output_detail
from .state import StateFinding

_logger = logging.getLogger("code_forge")
_TIMEOUT_UNSET = object()


class FixvalStatus(str, Enum):
    """FIXVAL gate result status."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    SKIPPED = "SKIPPED"
    WAIVED = "WAIVED"
    ERROR = "ERROR"


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
    reason: str = ""
    infra_errors: list[str] = field(default_factory=list)
    stage: dict | None = None
    restored_identities: dict | None = None


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


def _make_skipped_result(reason: str, *, code: str = "") -> FixvalResult:
    """Create a SKIPPED result with one DISMISSED finding."""
    return FixvalResult(
        status=FixvalStatus.SKIPPED,
        reason=code or skip_reason(reason),
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


def skip_reason(reason: str) -> str:
    return {
        "no files in diff": "no_files",
        "no test file in diff": "no_tests",
        "no executable test file in diff": "no_tests",
        "no non-test file in diff": "no_production",
        "non-git review, no diff available": "non_git",
        "no non-test changes to revert": "no_production_patch",
    }.get(reason, "unknown_skip")


def _execution_error(reason: str, detail: str, *, timed_out=False) -> FixvalResult:
    message = "FIXVAL unavailable: " + (
        detail if len(detail) <= 4000 else detail[:4000] + " [diagnostic truncated]"
    )
    return FixvalResult(
        FixvalStatus.ERROR,
        [
            StateFinding(
                id="FIXVAL_ERROR",
                fingerprint="fixval-" + reason,
                source="FIXVAL",
                disposition=Disposition.UNCERTAIN,
                file="",
                line_range=[],
                description=message,
                error=message,
                is_timeout=timed_out,
            )
        ],
        [],
        message,
        reason=reason,
        infra_errors=[message],
    )


def _phase_error_detail(session, detail):
    if not session.phases:
        return detail
    latest = session.phases[-1]
    prefix = latest["phase"]
    if "returncode" in latest:
        prefix += " (returncode %s)" % latest["returncode"]
    inventory = latest.get("inventory")
    if inventory is not None and inventory.failed:
        prefix += ": " + ", ".join(sorted(inventory.failed)[:5])
    return prefix + ": " + detail + _output_detail(latest.get("stderr"), latest.get("stdout"))


def preflight_fixval(candidate, diff_text, commit_message):
    """Resolve genuine exceptions before requesting unused test configuration."""
    if isinstance(candidate, FixvalSkip):
        return _make_skipped_result(candidate.reason), None
    if diff_text is None:
        return _make_skipped_result("non-git review, no diff available"), None
    waiver = parse_fixval_waiver(commit_message, env=os.environ)
    if waiver:
        channel = "env" if os.environ.get("FIXVAL_WAIVER", "").strip() else "trailer"
        result = FixvalResult(
            FixvalStatus.WAIVED,
            [
                StateFinding(
                    id="FIXVAL_WAIVED",
                    fingerprint="fixval-waived",
                    source="FIXVAL",
                    disposition=Disposition.DISMISSED,
                    file="",
                    line_range=[],
                    description="FIXVAL waived: " + waiver,
                )
            ],
            [
                AdvisoryFinding(
                    id="FIXVAL_WAIVER_RECORD",
                    axis="FIXVAL",
                    file="",
                    line_range=[],
                    description="FIXVAL waived via %s: %s" % (channel, waiver),
                    attribution="fixval-waiver",
                )
            ],
            reason="explicit_waiver",
        )
        result_exception = {"reason": waiver, "channel": channel}
        return (result, result_exception)
    try:
        patch = _filter_non_test_patch(diff_text)
    except (unidiff.errors.UnidiffParseError, TransactionError, ValueError) as exc:
        return _execution_error("invalid_patch", str(exc)), None
    if not patch.strip():
        return _make_skipped_result("no non-test changes to revert"), None
    return None, patch


def _filter_non_test_patch(diff_text: str) -> str:
    """Project production file blocks without reserializing Git binary bodies."""

    def is_production(patched_file):
        src_clean = patched_file_path(patched_file, source=True) or ""
        tgt_clean = patched_file_path(patched_file) or ""
        return not _is_test_file(src_clean) and not _is_test_file(tgt_clean)

    if not diff_text.strip():
        return ""
    # Git and unidiff count LF records; Unicode separators remain path/body data.
    blocks = []
    metadata = (
        "diff --git ",
        "index ",
        "old mode ",
        "new mode ",
        "new file mode ",
        "deleted file mode ",
        "similarity index ",
        "dissimilarity index ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
    )
    for _path, block in iter_diff_sections(diff_text):
        entries = unidiff.PatchSet(block)
        if not entries:
            raise TransactionError("nonempty diff frame has no parsed file")
        for entry in entries:
            info = str(entry.patch_info or "")
            if not entry.is_binary_file:
                if any(line.strip() and not line.startswith(metadata) for line in info.split("\n")):
                    raise TransactionError("unrecognized text outside diff hunks")
                if any(
                    line.startswith("@@")
                    and re.match(r"^@@ -[0-9]+(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@", line) is None
                    for line in block.split("\n")
                ):
                    raise TransactionError("malformed diff hunk header")
                headers = info.split("\n")

                def has(prefix, lines=headers):
                    return any(line.startswith(prefix) for line in lines)

                actionable = (
                    has("new file mode ")
                    or has("deleted file mode ")
                    or has("old mode ")
                    and has("new mode ")
                    or has("rename from ")
                    and has("rename to ")
                    or has("copy from ")
                    and has("copy to ")
                )
                if not entry and not actionable:
                    raise TransactionError("diff file lacks a hunk or actionable metadata")
        if not any(entry.is_binary_file for entry in entries):
            body = {line.diff_line_no for entry in entries for hunk in entry for line in hunk}
            for number, line in enumerate(block.split("\n"), 1):
                if number in body or not line.strip() or line.startswith(metadata + ("--- ", "+++ ")):
                    continue
                if (
                    re.match(r"^@@ -[0-9]+(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@", line)
                    or line.removesuffix("\r") == "\\ No newline at end of file"
                ):
                    continue
                raise TransactionError("unparsed text in diff frame")
        blocks.append((entries, block))
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
        reason="transaction",
        infra_errors=[detail],
    )


def _test_reverted_candidate(
    candidate, scoped_cmd, run_env, repo_root, *, evidence=None, greens=None, timeout=600
) -> FixvalResult:
    from .fixval_evidence import choose_witness

    if evidence is None or not greens or len(greens) != 3:
        return _execution_error("missing_green_evidence", "three closed GREEN inventories are required")
    red = evidence.execute(run_env, phase="reverted", timeout=timeout)
    witness, count = choose_witness(greens, red, evidence.candidates)
    if witness is not None:
        return FixvalResult(FixvalStatus.PASS, [], [], reason="attributable_red")
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
    candidate,
    test_cmd,
    cwd,
    commit_message,
    diff_text,
    *,
    recovery_parent=None,
    timeout_seconds=_TIMEOUT_UNSET,
    stage_id=None,
    source_hash=None,
    config_hash=None,
    overfit_files=None,
) -> FixvalResult:
    """Require stable fixed GREENs and a same-candidate ordinary reverted RED."""
    from .baseline_guard import _is_runner_startup_failure
    from .fixval_evidence import (
        EvidenceSession,
        EvidenceError,
        choose_witness,
        digest,
        new_stage,
        require_green,
        validate_test_timeout,
        source_snapshot,
    )

    exception, projected = preflight_fixval(candidate, diff_text, commit_message)
    if exception is not None:
        return exception
    try:
        if timeout_seconds is not _TIMEOUT_UNSET:
            validate_test_timeout(timeout_seconds)
        baseline_timeout = 120 if timeout_seconds is _TIMEOUT_UNSET else timeout_seconds
        probe_timeout = 600 if timeout_seconds is _TIMEOUT_UNSET else timeout_seconds
        session = EvidenceSession(
            cwd,
            test_cmd + candidate.test_files,
            candidate.test_files,
            parent=recovery_parent,
            stage_id=stage_id,
        )
    except (OSError, ValueError) as exc:
        return _execution_error("configuration", str(exc))
    run_env = os.environ.copy()
    run_env["PYTHONPATH"] = str(Path(cwd) / "src")
    snapshot_paths = set(candidate.test_files + candidate.non_test_files)
    for entry in unidiff.PatchSet(projected):
        for old_side in (False, True):
            path = patched_file_path(entry, source=old_side)
            if path:
                snapshot_paths.add(path)
    greens = []
    red = None
    witness = None
    witness_count = 0
    restoration = "not_started"
    outcome = None
    try:
        before_snapshot = source_snapshot(Path(cwd), snapshot_paths)
        for batch in range(2):
            greens = []
            retry = False
            for ordinal in range(3):
                try:
                    inventory = session.execute(
                        run_env, phase=f"fixed:{batch}:{ordinal}", timeout=baseline_timeout
                    )
                    if source_snapshot(Path(cwd), snapshot_paths) != before_snapshot:
                        raise EvidenceError("candidate source/test/index changed during fixed execution")
                    require_green(inventory, session.candidates)
                    if greens and inventory.rows != greens[0].rows:
                        raise EvidenceError("fixed test inventory/status is unstable")
                    greens.append(inventory)
                except (FileNotFoundError, EvidenceError) as exc:
                    latest = session.phases[-1] if session.phases else {}

                    def diagnostic(name, observed=latest):
                        value = observed.get(name, "")
                        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value

                    completed = subprocess.CompletedProcess(
                        test_cmd, latest.get("returncode", 1), diagnostic("stdout"), diagnostic("stderr")
                    )
                    started = any(
                        (cap.directory / "begin.json").exists()
                        for cap, rec, _ in session.captures
                        if rec is latest
                    )
                    owner = latest.get("ownership", {})
                    missing_launch = (
                        isinstance(exc, FileNotFoundError)
                        and getattr(exc, "ownership", {}).get("error_kind") == "FileNotFoundError"
                        and getattr(exc, "ownership", {}).get("driver_pid") is None
                        and getattr(exc, "ownership", {}).get("cleanup_complete") is True
                    )
                    missing_module = (
                        not started
                        and owner.get("cleanup_complete") is True
                        and _is_runner_startup_failure(test_cmd, completed)
                    )
                    missing = missing_launch or missing_module
                    if batch == 0 and "VIRTUAL_ENV" in run_env and missing:
                        run_env = _strip_venv_from_env(run_env)
                        run_env["PYTHONPATH"] = str(Path(cwd) / "src")
                        latest["retry_eligible"] = True
                        for observed in session.phases:
                            observed["superseded"] = True
                        retry = True
                        break
                    raise
            if not retry:
                break
        if len(greens) != 3:
            raise EvidenceError("three completed fixed runs are required")
        outcome = _transactional_probe(
            cwd,
            projected,
            lambda: _test_reverted_candidate(
                candidate,
                session.command,
                run_env,
                str(cwd),
                evidence=session,
                greens=greens,
                timeout=probe_timeout,
            ),
            recovery_parent,
        )
        expected_after = dict(before_snapshot["entries"])
        expected_after.update(outcome.restored_identities or {})
        restoration = (
            "failed"
            if outcome.status in (FixvalStatus.ERROR, FixvalStatus.BLOCK)
            and outcome.reason == "transaction"
            else "restored"
        )
        if outcome.status == FixvalStatus.PASS:
            red = next(rec["inventory"] for rec in session.phases if rec["phase"] == "reverted")
            witness, witness_count = choose_witness(greens, red, session.candidates)
            advisory = _run_overfit_owned(
                candidate, session, run_env, cwd, probe_timeout, recovery_parent, overfit_files
            )
            outcome.advisories.extend(advisory.advisories)
            expected_after.update(advisory.restored_identities or {})
            if advisory.status == FixvalStatus.ERROR or advisory.reason == "transaction":
                outcome = advisory
                restoration = "failed"
        if outcome.reason != "transaction":
            after_snapshot = source_snapshot(Path(cwd), snapshot_paths)
            if (
                after_snapshot["entries"] != expected_after
                or after_snapshot["semantic_sha256"] != before_snapshot["semantic_sha256"]
                or after_snapshot["index_sha256"] != before_snapshot["index_sha256"]
            ):
                raise EvidenceError("candidate source/test/index not restored after FIXVAL")
        # Transaction/cleanup failure retains its original recovery diagnostic.
        # A generic post-snapshot mismatch must not replace that safety result.
        if outcome.status == FixvalStatus.ERROR:
            outcome = _execution_error(
                outcome.reason,
                _phase_error_detail(session, outcome.block_message.removeprefix("FIXVAL unavailable: "))
                + "; raw evidence: "
                + str(session.directory),
                timed_out=outcome.reason == "timeout",
            )
        stage = session.projection(
            source_hash=source_hash or hashlib.sha256((diff_text or "").encode()).hexdigest(),
            config_hash=config_hash
            or digest(
                {
                    "command": test_cmd,
                    "baseline_timeout": baseline_timeout,
                    "probe_timeout": probe_timeout,
                }
            ),
            candidate_hash=digest(
                {"tests": candidate.test_files, "production": candidate.non_test_files}
            ),
            greens=greens,
            red=red,
            witness=witness if outcome.status == FixvalStatus.PASS else None,
            witness_count=witness_count,
            outcome=outcome.status.value,
            reason=outcome.reason or "hollow",
            restoration=restoration,
        )
        return FixvalResult(
            outcome.status,
            outcome.findings,
            outcome.advisories,
            outcome.block_message,
            reason=outcome.reason or "hollow",
            infra_errors=outcome.infra_errors,
            stage=stage,
        )
    except (OSError, ValueError, subprocess.SubprocessError, MutationProcessError) as exc:
        secondary = _execution_error(
            "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "execution",
            _phase_error_detail(session, str(exc)) + "; raw evidence: " + str(session.directory),
            timed_out=isinstance(exc, subprocess.TimeoutExpired),
        )
        if outcome is not None and outcome.reason == "transaction":
            # A failed proof replay must not erase unsafe restoration's recovery
            # location or primary finding. Both failures remain non-PASS.
            outcome = replace(
                outcome,
                findings=outcome.findings + secondary.findings,
                infra_errors=outcome.infra_errors + secondary.infra_errors,
                block_message=outcome.block_message + "\n" + secondary.block_message,
            )
        else:
            outcome = secondary
        stage = new_stage(
            session.stage_id, source_hash or hashlib.sha256((diff_text or "").encode()).hexdigest()
        )
        stage.update(outcome=outcome.status.value, reason=outcome.reason, restoration=restoration)
        # The raw location remains diagnostic recovery, never success authority.
        try:
            stage = session.projection(
                source_hash=stage["source_hash"],
                config_hash=config_hash,
                candidate_hash=digest(
                    {"tests": candidate.test_files, "production": candidate.non_test_files}
                ),
                greens=greens,
                red=red,
                witness=None,
                witness_count=0,
                outcome=outcome.status.value,
                reason=outcome.reason,
                restoration=restoration,
            )
        except (OSError, ValueError):
            try:
                stage["raw"] = session.manifest()
            except (OSError, ValueError):
                pass
        return replace(outcome, stage=stage)
    finally:
        active = sys.exception()
        # Also cover control interruption inside the ordinary-error handler's
        # fallback projection/manifest, which sibling except clauses cannot see.
        if isinstance(active, (KeyboardInterrupt, SystemExit)) and (
            outcome is not None and outcome.reason == "transaction"
        ):
            active.add_note(outcome.block_message)
        try:
            session.close()
        except BaseException as close_error:
            if active is not None and (
                not isinstance(active, Exception) or isinstance(close_error, Exception)
            ):
                active.add_note("FIXVAL evidence descriptor close failed: " + str(close_error))
            elif outcome is not None and outcome.reason == "transaction":
                if not isinstance(close_error, Exception):
                    close_error.add_note(outcome.block_message)
                    raise
                secondary = _execution_error("evidence_close", str(close_error))
                # The returned frozen result shares these diagnostic lists.
                # Keep its primary safety finding/recovery location intact.
                outcome.findings.extend(secondary.findings)
                outcome.infra_errors.extend(secondary.infra_errors)
            else:
                raise


def _transactional_probe(cwd, non_test_patch, run_probe, recovery_parent=None):
    repo_root = str(cwd)
    # Write patch to temp file
    try:
        fd, patch_path = tempfile.mkstemp(prefix=".fixval-revert-", suffix=".patch")
    except OSError as exc:
        return _transaction_block("cannot allocate revert patch: %s" % exc, repo_root)
    transaction = None
    forward_applied = False
    errors = []
    interrupted = None
    cleanup_complete = True
    probe_started = False
    probe_error = None
    restored_identities = {}
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
            probe_started = True
            outcome = run_probe()
    except (
        OSError,
        ValueError,
        TransactionError,
        subprocess.SubprocessError,
        MutationProcessError,
    ) as exc:
        cleanup_complete = getattr(
            exc, "cleanup_complete", getattr(exc, "ownership", {}).get("cleanup_complete", True)
        )
        if probe_started:
            probe_error = exc
        else:
            errors.append(str(exc))
    except (KeyboardInterrupt, SystemExit) as exc:
        cleanup_complete = getattr(exc, "cleanup_complete", False)
        interrupted = exc
    finally:
        if transaction is not None and not cleanup_complete:
            transaction.recovery_needed = True
            errors.append("owned descendants not proved stopped; source restoration deferred")
            try:
                transaction.close()
            except (OSError, TransactionError) as exc:
                errors.append("recovery close failed: %s" % exc)
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupted = interrupted or exc
                errors.append("recovery close interrupted: %s" % type(exc).__name__)
        if transaction is not None and cleanup_complete:
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
                if not errors:
                    restored_identities = {entry.path: entry.restored for entry in transaction.entries}
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
        if probe_error is not None:
            errors.insert(0, str(probe_error))
        return _transaction_block("; ".join(errors), recovery)
    if probe_error is not None:
        timeout = isinstance(probe_error, subprocess.TimeoutExpired)
        return replace(
            _execution_error("timeout" if timeout else "execution", str(probe_error), timed_out=timeout),
            restored_identities=restored_identities,
        )
    return (
        replace(outcome, restored_identities=restored_identities)
        if isinstance(outcome, FixvalResult)
        else outcome
    )


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


def _run_overfit_owned(candidate, session, run_env, cwd, timeout, recovery_parent, executable=None):
    eligible = candidate.non_test_files if executable is None else executable
    py_file = next((f for f in eligible if f.endswith(".py")), None)
    if py_file is None:
        return FixvalResult(FixvalStatus.PASS, [], [])
    from .fixval_evidence import normalize_file

    py_file = normalize_file(py_file, Path(cwd))
    path = Path(cwd) / py_file
    if not path.exists():
        return FixvalResult(FixvalStatus.PASS, [], [])
    try:
        original = path.read_bytes()
        tree = ast.parse(original.decode("utf-8"))
        renamer = _VariableRenamer()
        transformed = renamer.visit(tree)
        if renamer.renamed is None:
            return FixvalResult(FixvalStatus.PASS, [], [])
        ast.fix_missing_locations(transformed)
        changed = ast.unparse(transformed) + "\n"
        # Reversing this patch transforms the original into the renamed source;
        # the same transaction restores exact original bytes/index afterwards.
        patch = "".join(
            difflib.unified_diff(
                changed.splitlines(keepends=True),
                original.decode("utf-8").splitlines(keepends=True),
                fromfile="a/" + py_file,
                tofile="b/" + py_file,
            )
        )
    except (SyntaxError, UnicodeError):
        return FixvalResult(FixvalStatus.PASS, [], [])

    def probe():
        try:
            inventory = session.execute(run_env, phase="overfit", timeout=timeout)
            if inventory.returncode == 0:
                return FixvalResult(FixvalStatus.PASS, [], [])
            text = "test may be overfitting to variable names: renamed %r to %r in %s" % (
                renamer.renamed,
                renamer.new_name,
                py_file,
            )
            kind = (
                "FIXVAL_OVERFIT"
                if inventory.returncode == 1 and inventory.failed
                else "FIXVAL_OVERFIT_INCOMPLETE"
            )
            if kind.endswith("INCOMPLETE"):
                text = "overfit observation incomplete: no ordinary test-call failure evidence"
        except (OSError, ValueError, subprocess.SubprocessError, MutationProcessError) as exc:
            if (
                getattr(
                    exc, "cleanup_complete", getattr(exc, "ownership", {}).get("cleanup_complete", True)
                )
                is not True
            ):
                raise
            text = "overfit observation incomplete: " + str(exc)
            kind = "FIXVAL_OVERFIT_INCOMPLETE"
        return FixvalResult(
            FixvalStatus.PASS,
            [],
            [
                AdvisoryFinding(
                    id=kind,
                    axis="FIXVAL",
                    file=py_file,
                    line_range=[],
                    description=text,
                    attribution="fixval-overfit-guard",
                )
            ],
        )

    return _transactional_probe(cwd, patch, probe, recovery_parent)


def run_overfit_guard(candidate, test_cmd, cwd, *, timeout_seconds=_TIMEOUT_UNSET, recovery_parent=None):
    """Standalone advisory API using the same owner and safe transaction."""
    from .fixval_evidence import EvidenceSession, validate_test_timeout

    timeout = 600 if timeout_seconds is _TIMEOUT_UNSET else validate_test_timeout(timeout_seconds)
    session = EvidenceSession(
        cwd, test_cmd + candidate.test_files, candidate.test_files, parent=recovery_parent
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(cwd) / "src")
    try:
        result = _run_overfit_owned(candidate, session, env, cwd, timeout, recovery_parent)
        if result.reason == "transaction":
            raise TransactionError(result.block_message)
        return result.advisories
    finally:
        active = sys.exception()
        try:
            session.close()
        except BaseException as close_error:
            if active is not None and (
                not isinstance(active, Exception) or isinstance(close_error, Exception)
            ):
                active.add_note("FIXVAL evidence descriptor close failed: " + str(close_error))
            else:
                raise
