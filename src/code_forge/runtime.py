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
    "it actually works after the change?\n"
    "4. Does this change depend on, or run concurrently with, any OTHER stateful "
    "subsystem (another nftables table, routing rule, lock, daemon, file) NOT in "
    "this diff? If so: can that subsystem block, drop, or interfere with this "
    "change's network/file/process operations? Enumerate the flags that gate this "
    "function and ALL their possible values at call time.\n\n"
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

    Normalizes hyphens and underscores to spaces before comparing so that
    a receipt written with --surface "nftables-rules" matches the LLM surface
    "nftables rules" (smoke-run sanitizes spaces to hyphens in filenames).
    """
    def _norm(s: str) -> str:
        return s.lower().replace("-", " ").replace("_", " ")

    a = _norm(llm_surface)
    b = _norm(receipt_surface)
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


def _parse_llm_response(
    content: object,
) -> tuple[list[str], list[dict]]:
    """Parse LLM response content into (surfaces, findings).

    Accepts dict, JSON string, or a single-element list wrapping a dict.
    Single-element lists are unwrapped to handle LLMs (e.g. mimo-pro) that
    wrap the JSON object in an array.
    Raises ValueError/KeyError/JSONDecodeError on malformed input so callers
    can map to a SKIPPED finding.
    """
    if isinstance(content, dict):
        parsed = content
    elif isinstance(content, str):
        parsed = json.loads(content)
    elif (
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
    ):
        # Unwrap single-element list: some LLMs wrap the JSON object in an
        # array (e.g. mimo-pro returns [{...}] instead of {...}).
        parsed = content[0]
    else:
        raise ValueError(
            "unexpected LLM content type: %s" % type(content).__name__
        )
    if "surfaces" not in parsed:
        raise KeyError("missing 'surfaces' key in LLM response")
    # Coerce to str: LLM may return integers or nulls in surfaces array.
    surfaces = [str(s) for s in (parsed.get("surfaces", []) or [])]
    llm_findings = list(parsed.get("findings", []) or [])
    return surfaces, llm_findings


def _build_smoke_summary(
    surfaces: list[str],
    valid_receipts: list[dict],
) -> AdvisoryFinding | None:
    """Build the runtime-smoke-summary AdvisoryFinding.

    Returns None when surfaces is empty (no summary for zero
    surfaces; callers must handle the no-surfaces case explicitly).
    """
    if not surfaces:
        return None

    verified_surfaces: list[str] = []
    unverified_surfaces: list[str] = []
    fingerprints: list[str] = []

    for s in surfaces:
        verified, fp = False, ""
        for receipt in valid_receipts:
            if _surface_matches(s, receipt.get("surface", "")):
                fp = receipt.get("diff_sha256", "")[:8]
                verified = True
                break
        if verified:
            verified_surfaces.append(s)
            fingerprints.append("%s[%s]" % (s, fp))
        else:
            unverified_surfaces.append(s)

    total = len(surfaces)
    verified_count = len(verified_surfaces)

    if verified_count == total:
        description = "smoke: all %d surfaces verified (%s)" % (
            total, ", ".join(fingerprints)
        )
    else:
        description = "smoke: %d/%d surfaces verified; NOT VERIFIED: [%s]" % (
            verified_count, total, ", ".join(unverified_surfaces)
        )
        if fingerprints:
            description += " (verified: %s)" % ", ".join(fingerprints)

    return AdvisoryFinding(
        id="runtime-smoke-summary",
        axis="RUNTIME",
        file="",
        line_range=[0, 0],
        description=description,
        attribution="runtime-axis/smoke-evidence",
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

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of AdvisoryFinding. Advisory only -- never blocks verdict.
        """
        self.infra_errors.clear()
        if not diff_text or not diff_text.strip():
            return []

        diff_hash = compute_source_hash(git_diff=diff_text)

        # str.replace NOT str.format: diffs can contain literal { or }.
        prompt = RUNTIME_LIFECYCLE_QUESTION.replace("{diff_text}", diff_text)
        try:
            result = llm_invoke(prompt, backend=self._backend)
        except LLMInvokeError as exc:
            reason = str(exc)
            self.infra_errors.append("RUNTIME axis LLM call failed: %s" % reason)
            return [_build_skipped_finding(reason)]

        try:
            surfaces, llm_findings = _parse_llm_response(result.content)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            reason = "LLM response parse error: %s" % exc
            self.infra_errors.append(reason)
            return [_build_skipped_finding(reason)]

        receipts_dir = repo_root / ".code-forge" / "smoke-receipts"
        valid_receipts = [
            r for r in read_smoke_receipts(receipts_dir)
            if r.get("diff_sha256") == diff_hash and r.get("status") == "VERIFIED"
        ]

        findings: list[AdvisoryFinding] = [
            AdvisoryFinding(
                id="runtime-%d" % idx,
                axis="RUNTIME",
                file=str(lf.get("file", "")),
                line_range=[int(lf.get("line", 0)), int(lf.get("line", 0))],
                description=str(lf.get("description", "")),
                attribution="runtime-axis/llm",
            )
            for idx, lf in enumerate(llm_findings)
            if isinstance(lf, dict)
        ]

        summary = _build_smoke_summary(surfaces, valid_receipts)
        if summary is not None:
            findings.append(summary)

        return findings
