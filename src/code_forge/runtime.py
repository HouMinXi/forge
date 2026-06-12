# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""RuntimeRunner advisory axis + smoke receipt infrastructure.

Implements REVIEW-RUNTIME-01: a dedicated RUNTIME advisory axis that makes
forge's verdict honest by declaring what it did NOT verify at runtime.

Three concrete forms:
  (a) write_smoke_receipt / read_smoke_receipts: machine-verifiable receipts
      keyed by diff content-hash (D-01/D-07).
  (b) RuntimeRunner: advisory axis making one fixed LLM call (D-04/D-05)
      to enumerate runtime surfaces and lifecycle/side-effect risks.
  (c) RUNTIME_LIFECYCLE_QUESTION: canonical fixed question constant (D-05/D-10),
      exported so SKILL.md mirror can be drift-tested.

D-04: RUNTIME axis is ALWAYS-ON (no gate.yaml opt-out). LLM failure records
      SKIPPED with reason -- never silent. Advisory never blocks verdict.
D-08: Default state is UNVERIFIED (fail-closed). No receipt = UNVERIFIED.
D-11: Per-surface NOT VERIFIED = (LLM-enumerated) minus (receipt-declared),
      using case-insensitive substring containment (either direction).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Optional

from .advisory import AdvisoryFinding
from .llm_invoke import LLMInvokeError, llm_invoke
from .source import compute_source_hash

# ---------------------------------------------------------------------------
# D-05 canonical lifecycle question constant
# Must contain: {diff_text} placeholder, runtime surfaces, lifecycle/side-effect
# risks, smoke test needs, JSON response with "surfaces" and "findings" keys.
# D-10: SKILL.md carries a verbatim mirror; drift test asserts equality.
# ---------------------------------------------------------------------------

RUNTIME_LIFECYCLE_QUESTION = (
    "You are reviewing a code diff for runtime lifecycle and side-effect risks.\n\n"
    "Given this diff, identify:\n"
    "1. What runtime surfaces does this change affect? "
    "(e.g., systemd units, nftables rules, network sockets, "
    "subprocess calls, file side effects, cron/timer jobs, "
    "kernel modules, firewall rules)\n"
    "2. For each surface: what lifecycle or side-effect could break "
    "at runtime even if the code is syntactically correct and all "
    "unit tests pass?\n"
    "3. What smoke test would need to exercise each surface to verify "
    "it actually works after the change?\n\n"
    "Return your answer as JSON with exactly these keys:\n"
    '{"surfaces": ["surface1", "surface2"], '
    '"findings": [{"file": "path/to/file", "line": 1, '
    '"surface": "surface_name", "description": "what could break and why"}]}\n\n'
    "If no runtime surfaces are affected, return: "
    '{"surfaces": [], "findings": []}\n\n'
    "Diff:\n{diff_text}"
)


# ---------------------------------------------------------------------------
# Smoke receipt write/read  (D-01/D-07)
# ---------------------------------------------------------------------------


def write_smoke_receipt(
    receipts_dir: Path,
    diff_text: str,
    surface: str,
    command: str,
    exit_code: int,
    transcript: bytes,
    timestamp: str,
) -> Path:
    """Write a smoke-test receipt keyed by diff content-hash.

    Uses atomic tmp+replace pattern (receipt.py model) to prevent partial files.
    D-01: machine-verifiable receipt only -- no executor self-report.
    D-07: receipt keyed by diff content-hash (invalidated when diff changes).

    Args:
        receipts_dir: directory to write receipts into (created if absent).
        diff_text: unified diff string (used to compute diff_sha256).
        surface: runtime surface name (becomes part of filename).
        command: command string that was executed.
        exit_code: exit code from the command.
        transcript: bytes of stdout+stderr from the command.
        timestamp: ISO 8601 UTC timestamp string.

    Returns:
        Path to the written receipt file.
    """
    receipts_dir.mkdir(parents=True, exist_ok=True)

    diff_sha256 = compute_source_hash(git_diff=diff_text)
    transcript_sha256 = hashlib.sha256(transcript).hexdigest()
    status = "VERIFIED" if exit_code == 0 else "FAILED"

    receipt = {
        "diff_sha256": diff_sha256,
        "surface": surface,
        "command": command,
        "exit_code": int(exit_code),
        "transcript_sha256": transcript_sha256,
        "timestamp": timestamp,
        "status": status,
    }

    target = receipts_dir / ("smoke-receipt-%s.json" % surface)

    # Atomic write: write to tmp in same directory, then replace target.
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(receipts_dir), suffix=".tmp", prefix="smoke-receipt-"
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        Path(tmp_path_str).replace(target)
    except Exception:
        try:
            Path(tmp_path_str).unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return target


def read_smoke_receipts(receipts_dir: Path) -> list[dict]:
    """Read all smoke receipts from a directory.

    Only reads files matching smoke-receipt-*.json pattern.
    D-07: receipt files must be named smoke-receipt-{surface}.json.

    Args:
        receipts_dir: directory to read receipts from.

    Returns:
        List of receipt dicts. Empty list if directory absent or empty.
    """
    if not receipts_dir.exists():
        return []

    receipts: list[dict] = []
    for path in sorted(receipts_dir.glob("smoke-receipt-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                receipts.append(data)
        except (json.JSONDecodeError, OSError):
            # Silently skip unreadable/malformed files
            pass

    return receipts


# ---------------------------------------------------------------------------
# RuntimeRunner -- AxisRunner Protocol implementation
# ---------------------------------------------------------------------------


def _surface_matches(llm_surface: str, receipt_surface: str) -> bool:
    """Case-insensitive substring containment match (D-11, either direction).

    A surface is VERIFIED if any valid receipt's surface field contains
    or is contained by the LLM surface string (either direction).
    """
    a = llm_surface.lower()
    b = receipt_surface.lower()
    return a in b or b in a


def _build_skipped_finding(reason: str) -> AdvisoryFinding:
    """Build a SKIPPED AdvisoryFinding for D-04 never-silent-skip."""
    return AdvisoryFinding(
        id="runtime-skipped",
        axis="RUNTIME",
        file="",
        line_range=[0, 0],
        description="RUNTIME axis SKIPPED: %s" % reason,
        attribution="runtime-axis/infra-error",
    )


class RuntimeRunner:
    """Advisory axis: lifecycle/side-effect review + smoke evidence.

    Satisfies the AxisRunner Protocol (is_advisory=True).
    D-04: always-on, no gate.yaml opt-out.
    D-04: LLM failure -> SKIPPED finding with reason, never silent.
    """

    def __init__(self, backend=None) -> None:
        self.source_files: Optional[list[Path]] = None
        self.infra_errors: list[str] = []
        self._backend = backend

    @property
    def is_advisory(self) -> bool:
        """Advisory axis: findings never block, never reset cycle counter."""
        return True

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run the RUNTIME advisory axis on the given diff.

        Steps:
          (a) Guard: empty diff -> return [].
          (b) Compute diff hash for receipt validation.
          (c) LLM call with RUNTIME_LIFECYCLE_QUESTION (str.replace, not format).
          (d) Parse JSON response: extract "surfaces" and "findings".
          (e) Read smoke receipts from repo_root/.code-forge/smoke-receipts/.
          (f) Validate receipts: diff_sha256 must match, status must be VERIFIED.
          (g) Compute NOT VERIFIED = LLM-enumerated minus receipt-declared.
          (h) Build AdvisoryFinding list: per-finding findings + optional summary.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding. Advisory only -- never blocks verdict.
        """
        # Prevent cross-run accumulation when runner is reused across cycles.
        self.infra_errors.clear()

        # (a) Guard: empty diff returns nothing.
        if not diff_text or not diff_text.strip():
            return []

        # (b) Compute diff hash for TOCTOU detection (Pitfall 3).
        diff_hash = compute_source_hash(git_diff=diff_text)

        # (c) LLM call -- use str.replace NOT str.format.
        # Diffs can contain literal { or } which would KeyError with .format().
        prompt = RUNTIME_LIFECYCLE_QUESTION.replace("{diff_text}", diff_text)
        try:
            result = llm_invoke(prompt, backend=self._backend)
        except LLMInvokeError as exc:
            # D-04: SKIPPED with reason, never silent.
            reason = str(exc)
            self.infra_errors.append("RUNTIME axis LLM call failed: %s" % reason)
            return [_build_skipped_finding(reason)]

        # (d) Parse JSON response.
        content = result.content
        surfaces: list[str] = []
        llm_findings: list[dict] = []

        try:
            if isinstance(content, dict):
                parsed = content
            elif isinstance(content, str):
                parsed = json.loads(content)
            else:
                raise ValueError("unexpected LLM content type: %s" % type(content).__name__)

            if "surfaces" not in parsed:
                raise KeyError("missing 'surfaces' key in LLM response")

            surfaces = list(parsed.get("surfaces", []) or [])
            llm_findings = list(parsed.get("findings", []) or [])

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            # D-04: malformed JSON -> SKIPPED with parse error reason.
            reason = "LLM response parse error: %s" % exc
            self.infra_errors.append(reason)
            return [_build_skipped_finding(reason)]

        # (e) Read smoke receipts.
        receipts_dir = repo_root / ".code-forge" / "smoke-receipts"
        all_receipts = read_smoke_receipts(receipts_dir)

        # (f) Validate receipts: diff_sha256 must match AND status == VERIFIED.
        valid_receipts = [
            r for r in all_receipts
            if r.get("diff_sha256") == diff_hash
            and r.get("status") == "VERIFIED"
        ]

        # (g) Compute NOT VERIFIED = LLM-enumerated minus receipt-declared.
        # Case-insensitive substring containment (D-11, either direction).
        def _is_verified(llm_surface: str) -> tuple[bool, str]:
            """Return (verified, receipt_fingerprint)."""
            for receipt in valid_receipts:
                rs = receipt.get("surface", "")
                if _surface_matches(llm_surface, rs):
                    fp = receipt.get("diff_sha256", "")[:8]
                    return True, fp
            return False, ""

        # (h) Build AdvisoryFinding list.
        findings: list[AdvisoryFinding] = []

        # Per-finding entries from LLM "findings" list.
        for idx, lf in enumerate(llm_findings):
            if not isinstance(lf, dict):
                continue
            findings.append(AdvisoryFinding(
                id="runtime-%d" % idx,
                axis="RUNTIME",
                file=str(lf.get("file", "")),
                line_range=[int(lf.get("line", 0)), int(lf.get("line", 0))],
                description=str(lf.get("description", "")),
                attribution="runtime-axis/llm",
            ))

        # Summary finding: only when total surfaces > 0 (GM-R5-L2 fix).
        # Plan 02 _display_smoke_status parses id="runtime-smoke-summary".
        if surfaces:
            verified_surfaces: list[str] = []
            unverified_surfaces: list[str] = []
            fingerprints: list[str] = []

            for s in surfaces:
                ok, fp = _is_verified(s)
                if ok:
                    verified_surfaces.append(s)
                    fingerprints.append("%s[%s]" % (s, fp))
                else:
                    unverified_surfaces.append(s)

            total = len(surfaces)
            verified_count = len(verified_surfaces)

            if verified_count == total:
                # All verified.
                description = "smoke: all %d surfaces verified (%s)" % (
                    total, ", ".join(fingerprints)
                )
            else:
                # Some or all unverified.
                description = (
                    "smoke: %d/%d surfaces verified; NOT VERIFIED: [%s]"
                    % (verified_count, total, ", ".join(unverified_surfaces))
                )
                if fingerprints:
                    description += " (verified: %s)" % ", ".join(fingerprints)

            findings.append(AdvisoryFinding(
                id="runtime-smoke-summary",
                axis="RUNTIME",
                file="",
                line_range=[0, 0],
                description=description,
                attribution="runtime-axis/smoke-evidence",
            ))

        return findings
