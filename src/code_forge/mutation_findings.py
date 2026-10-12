# SPDX-License-Identifier: Apache-2.0
"""Distinguish reported surviving mutants from measurement diagnostics."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .disposition import Disposition
from .ledger import TerminalState, finding_suppression_key, iter_rows, resolve_ledger_root
from .state import StateFinding


def is_mutation_survivor(finding: StateFinding) -> bool:
    """The mutant- prefix is reserved for identified surviving mutants."""
    return (
        finding.source == "MUTANT"
        and finding.disposition == Disposition.CONFIRMED
        and isinstance(finding.id, str)
        and finding.id.startswith("mutant-")
        and len(finding.id) > len("mutant-")
    )


def is_mutation_diagnostic(finding: StateFinding) -> bool:
    """An unresolved mutation finding without a surviving-mutant identity."""
    return (
        finding.source == "MUTANT"
        and finding.disposition in (Disposition.CONFIRMED, Disposition.UNCERTAIN)
        and not is_mutation_survivor(finding)
    )


def _valid_claim(record: dict) -> bool:
    strings = ("id", "fingerprint", "source", "disposition", "file", "description")
    if any(type(record.get(key)) is not str for key in strings):
        return False
    if not record["id"].startswith("mutant-") or not record["id"][len("mutant-"):].strip():
        return False
    if not record["fingerprint"].strip() or not record["description"].strip():
        return False
    if record["source"] != "MUTANT" or record["disposition"] != "CONFIRMED":
        return False
    lines = record.get("line_range")
    return (
        type(lines) is list and len(lines) in (0, 2)
        and all(type(line) is int and line >= 0 for line in lines)
        and (not lines or lines[0] <= lines[1])
    )


def mutation_survivor_details(findings: list[StateFinding]) -> list[dict]:
    """Keep actual native fingerprints and claims without deriving them from IDs."""
    return [
        {"id": f.id, "fingerprint": f.fingerprint, "source": f.source,
         "disposition": getattr(f.disposition, "value", f.disposition), "file": f.file,
         "line_range": list(f.line_range) if type(f.line_range) is list else f.line_range,
         "description": f.description}
        for f in findings if is_mutation_survivor(f)
    ]


def mutation_review_context(
    *, source_root: Path, base_sha: str | None, head_sha: str | None, git_diff: str | None,
) -> dict | None:
    """Capture review identity separately from native measurement evidence."""
    if (type(base_sha) is not str or type(head_sha) is not str or type(git_diff) is not str
            or re.fullmatch(r"[0-9a-fA-F]{40}", base_sha) is None
            or re.fullmatch(r"[0-9a-fA-F]{40}", head_sha) is None):
        return None
    try:
        root = Path(source_root).resolve()
        return {"base_sha": base_sha, "head_sha": head_sha,
                "diff_sha256": hashlib.sha256(git_diff.encode("utf-8")).hexdigest(),
                "source_repo_root": str(root), "ledger_root": str(resolve_ledger_root(root).resolve())}
    except (OSError, ValueError, TypeError):
        return None


def _valid_review_context(context) -> bool:
    keys = {"base_sha", "head_sha", "diff_sha256", "source_repo_root", "ledger_root"}
    if type(context) is not dict or set(context) != keys or any(type(context[k]) is not str for k in keys):
        return False
    if any(re.fullmatch(r"[0-9a-fA-F]{40}", context[k]) is None for k in ("base_sha", "head_sha")):
        return False
    if re.fullmatch(r"[0-9a-f]{64}", context["diff_sha256"]) is None:
        return False
    try:
        return all(Path(context[k]).is_absolute() and str(Path(context[k]).resolve()) == context[k]
                   for k in ("source_repo_root", "ledger_root"))
    except (OSError, ValueError):
        return False


def decode_mutation_survivor_details(
    result: dict, *, review_context: dict | None, include_timeout_diagnostics: bool = False,
) -> list[StateFinding] | None:
    """Validate a complete bound carrier; optionally derive terminal timeout notes.

    The default remains survivors-only. No diagnostic is trusted from a second
    carrier field: timeout identities come exclusively from the validated
    inventory, after the exact survived-identity equality check below.
    """
    if type(result.get("survivor_details_version")) is not int or result["survivor_details_version"] != 1:
        return None
    if (result.get("status") != "done" or result.get("baseline_passed") is not True
            or result.get("completed_measurement") is not True
            or result.get("infra_errors") != [] or result.get("message", "") != ""
            or any(key in result for key in ("publication_error", "cancellation_type", "cleanup_error", "process_failure"))
            or result.get("cancelled", False) is not False
            or result.get("cleanup_complete", True) is not True):
        return None
    inventory = result.get("inventory")
    if (type(inventory) is not dict or not inventory
            or any(type(k) is not str or not k or type(v) is not str or v not in {"survived", "killed", "caught by type check", "timeout"}
                   for k, v in inventory.items())):
        return None
    if (not _valid_review_context(review_context) or not _valid_review_context(result.get("review_context"))
            or result["review_context"] != review_context):
        return None
    ids, details = result.get("survivors"), result.get("survivor_details")
    if type(ids) is not list or type(details) is not list or len(ids) != len(details):
        return None
    if any(type(record) is not dict or not _valid_claim(record) for record in details):
        return None
    actual_ids = [record["id"] for record in details]
    fingerprints = [record["fingerprint"] for record in details]
    if ids != actual_ids or len(set(actual_ids)) != len(actual_ids) or len(set(fingerprints)) != len(fingerprints):
        return None
    if {identity[len("mutant-"):] for identity in actual_ids} != {
            identity for identity, status in inventory.items() if status == "survived"}:
        return None
    timeout_fingerprints = {
        "mutation-timeout:" + identity for identity, status in inventory.items() if status == "timeout"
    }
    if timeout_fingerprints.intersection(fingerprints):
        # State merges by fingerprint. A diagnostic must never overwrite a
        # survivor claim, even when the carrier supplies a custom fingerprint.
        return None
    findings = [StateFinding(id=record["id"], fingerprint=record["fingerprint"], source="MUTANT",
                             disposition=Disposition.CONFIRMED, file=record["file"],
                             line_range=list(record["line_range"]), description=record["description"])
                for record in details]
    if include_timeout_diagnostics:
        for identity, status in inventory.items():
            if status != "timeout":
                continue
            # Preserve the full structured identity; terminal text is escaped
            # before bounding, so control characters cannot forge log lines.
            display = json.dumps(identity, ensure_ascii=True)
            if len(display) > 160:
                display = display[:160] + "..."
            findings.append(StateFinding(
                id="mutation-timeout-" + identity,
                fingerprint="mutation-timeout:" + identity,
                source="MUTANT", disposition=Disposition.DISMISSED,
                file="", line_range=[], description="mutant timed out: " + display,
            ))
    return findings


def apply_mutation_ledger_dispositions(
    findings: list[StateFinding], *, source_root: Path, base_sha: str | None,
    head_sha: str | None, git_diff: str | None, eligible_measurement: bool,
) -> list[str]:
    """Dismiss only measured native survivors with an exactly bound latest ruling."""
    if eligible_measurement is not True:
        return []
    context = mutation_review_context(
        source_root=source_root, base_sha=base_sha, head_sha=head_sha, git_diff=git_diff,
    )
    candidates = [f for f in findings if is_mutation_survivor(f)]
    if context is None or not candidates:
        return []
    try:
        rows = list(iter_rows(Path(context["ledger_root"]), strict=True))
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        return ["mutation: ledger dispositions unavailable: %s" % exc]
    latest = {row.fingerprint: row for row in rows}
    ids = Counter(f.id for f in findings if type(f.id) is str)
    fingerprints = Counter(f.fingerprint for f in findings if type(f.fingerprint) is str)
    notes = []
    for finding in candidates:
        record = mutation_survivor_details([finding])[0]
        if (not _valid_claim(record) or ids[finding.id] != 1 or fingerprints[finding.fingerprint] != 1
                or finding.diagnostic_kind is not None or finding.provider_failure is not None
                or finding.is_timeout):
            continue
        row = latest.get(finding.fingerprint)
        key = finding_suppression_key(
            base_sha=base_sha, head_sha=head_sha, git_diff=git_diff,
            file=finding.file, line=finding.line_range[0] if finding.line_range else 0,
            source=finding.source, description=finding.description,
        )
        if row is None or row.terminal_state not in {
            TerminalState.FIXED, TerminalState.DISPROVED, TerminalState.DUPLICATE,
        }:
            continue
        try:
            same_root = Path(row.repo_root).is_absolute() and str(Path(row.repo_root).resolve()) == context["ledger_root"]
        except (OSError, ValueError):
            same_root = False
        if (not same_root or not key or row.suppression_key != key
                or (row.base_sha, row.head_sha) != (base_sha, head_sha)
                or row.file != finding.file
                or row.line != (finding.line_range[0] if finding.line_range else 0)):
            continue
        finding.disposition = Disposition.DISMISSED
        notes.append("mutation: %s dismissed by latest %s ruling" % (finding.fingerprint, row.terminal_state.value))
    return notes
