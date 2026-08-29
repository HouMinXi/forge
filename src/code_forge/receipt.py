"""Receipt writer for anti-shirk verification.

Splits L1 findings by pass name (encoded in StateFinding.id as
"l1-<pass_name>-<fingerprint>") and writes one receipt JSON per pass
to .code-forge/receipts/receipt-cNpM.json.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Optional

from .basis import derive_basis
from .diff import (
    _extract_post_image_lines,
    describe_fabricated_lines,
    parse_diff_hunks,
)
from .manifest import EnvManifest, ManifestTier
from .state import (
    StateFinding,
    PassOutcome,
    derive_pass_outcomes,
    _PASS_NAMES,
)

_SKILL_NAMES = ["qodo-review", "code-review-expert", "adversarial-qe"]


def _read_line(cwd: Path, file: str, line: int) -> str:
    try:
        lines = (cwd / file).read_text(encoding="utf-8").splitlines()
        if 0 < line <= len(lines):
            return lines[line - 1].strip()[:80]
    except OSError:
        pass
    return ""


def _split_by_pass(
    l1_findings: list[StateFinding],
) -> dict[str, list[StateFinding]]:
    by_pass: dict[str, list[StateFinding]] = {p: [] for p in _PASS_NAMES}
    for f in l1_findings:
        for p in _PASS_NAMES:
            if f.id.startswith("l1-" + p + "-"):
                by_pass[p].append(f)
                break
    return by_pass


def _build_excerpts(
    reviewer_excerpts: list[dict] | None = None,
) -> list[dict]:
    if not reviewer_excerpts:
        return []
    out = []
    for exc in reviewer_excerpts:
        content = exc.get("content", "")
        # Some reviewers emit content as a list of lines instead of a
        # string; the schema downstream requires a string. Any other
        # non-string shape (null, a dict, a number) is left as-is rather
        # than stringified: str(None) is the string "None", which passes
        # the downstream isinstance(content, str) schema check as if it
        # were genuine reviewer output. Leaving it unconverted lets
        # _validate_receipt_schema reject the receipt instead of
        # laundering a missing excerpt into a fabricated one.
        if isinstance(content, list):
            # Only an all-string list is joined. A non-string element
            # (None, a number) stays in the list, which the schema
            # check rejects: str(ln) would launder None into "None",
            # the same trap the scalar case above avoids.
            if all(isinstance(ln, str) for ln in content):
                content = "\n".join(content)
        out.append({
            "file": exc.get("file", ""),
            "start_line": exc.get("start_line", 0),
            "end_line": exc.get("end_line", 0),
            "content": content,
            "rationale": "reviewer-provided",
        })
    return out


def _warn_on_fabricated_excerpts(
    diff_text: str | None,
    excerpts: list[dict] | None,
) -> None:
    """Warn about excerpts verify will refuse, while the round can react.

    verify already rejects an excerpt covering lines the diff never
    produced, but it runs at the end of the round -- after the passes
    have been spent. Reporting the same thing here costs one walk over
    the excerpt list and reaches the reviewer in time to matter.

    Purely diagnostic. verify stays the gate; nothing here blocks the
    receipt write, and a malformed diff degrades to silence rather than
    taking the round down with it.
    """
    if not diff_text or not excerpts:
        return

    log = logging.getLogger(__name__)
    try:
        post_image = _extract_post_image_lines(diff_text)
        _, exempt_files = parse_diff_hunks(diff_text)
    except Exception as err:
        log.debug("pre-flight: post-image extraction failed: %s", err)
        return

    for excerpt in excerpts:
        start = excerpt.get("start_line")
        end = excerpt.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        fname = excerpt.get("file", "")
        # Binary, rename, and mode-change files have no post-image, so
        # every line of an excerpt against one would read as invented.
        # verify exempts them; this has to agree or it cries wolf on
        # every review that touches an image.
        if fname in exempt_files:
            continue
        file_lines = post_image.get(fname)
        if file_lines is None:
            log.warning(
                "pre-flight: excerpt %s:%d-%d references file %s "
                "not in the diff; verify will refuse this",
                fname, start, end, fname,
            )
            continue
        fabricated = describe_fabricated_lines(file_lines, start, end)
        if fabricated:
            log.warning(
                "pre-flight: excerpt %s:%d-%d references lines %s "
                "not in diff post-image; verify will refuse this",
                fname, start, end, fabricated,
            )


def write_receipts(
    receipts_dir: Path,
    round_index: int,
    l1_findings: list[StateFinding],
    diff_sha256: str,
    source_files: list[Path],
    cwd: Path,
    diff_files: dict[str, list[int]] | None = None,
    diff_text: str | None = None,
    reviewer_excerpts: list[dict] | None = None,
    manifest: Optional[EnvManifest | ManifestTier | dict[str, Any] | str] = None,
    manifest_tier: Optional[ManifestTier] = None,
    exec_evidence: Optional[dict[str, Any] | str] = None,
) -> list[Path]:
    """Write 3 receipt files (one per pass) for a round."""
    receipts_dir.mkdir(parents=True, exist_ok=True)
    if manifest_tier is not None:
        effective_tier = manifest_tier
    elif manifest is not None:
        if isinstance(manifest, ManifestTier):
            effective_tier = manifest
        elif isinstance(manifest, EnvManifest):
            effective_tier = manifest.tier
        elif isinstance(manifest, dict):
            try:
                effective_tier = ManifestTier(manifest.get("tier", "declared"))
            except (ValueError, KeyError):
                effective_tier = ManifestTier.DECLARED
        else:
            try:
                effective_tier = ManifestTier(str(manifest))
            except ValueError:
                effective_tier = ManifestTier.DECLARED
    else:
        try:
            from .manifest import extract_manifest
            effective_tier = extract_manifest(cwd).tier
        except Exception:
            effective_tier = ManifestTier.DECLARED

    by_pass = _split_by_pass(l1_findings)
    cycle = round_index + 1
    # One write time for the whole round. A per-pass offset is not ordered
    # against the next round, and rounds finish faster than it spans, so it
    # inverted the sequence verify reads and failed a converged review.
    now = datetime.datetime.now(datetime.timezone.utc)
    written = []

    assembled_excerpts = _build_excerpts(reviewer_excerpts)
    _warn_on_fabricated_excerpts(diff_text, assembled_excerpts)
    pass_outcomes = derive_pass_outcomes(l1_findings)

    exec_status: Optional[str] = None
    exec_evidence_dict: Optional[dict[str, Any]] = None
    if isinstance(exec_evidence, dict):
        exec_evidence_dict = exec_evidence
        exec_status = str(exec_evidence.get("status", "")) if exec_evidence.get("status") else None
    elif isinstance(exec_evidence, str):
        exec_status = exec_evidence
        exec_evidence_dict = {"status": exec_evidence}

    for pass_idx, (pass_name, skill_name) in enumerate(
        zip(_PASS_NAMES, _SKILL_NAMES)
    ):
        pass_num = pass_idx + 1
        pass_findings = by_pass.get(pass_name, [])

        receipt = {
            "cycle": cycle,
            "pass": pass_num,
            "skill": skill_name,
            "diff_sha256": diff_sha256,
            "timestamp": now.isoformat(),
            "pass_status": pass_outcomes.get(
                pass_name, PassOutcome.COMPLETED
            ).value,
            "findings_count": len(pass_findings),
            "findings": [
                {
                    "file": f.file,
                    "line": f.line_range[0] if f.line_range else 0,
                    "description": f.description,
                    "disposition": f.disposition.value,
                    "basis": derive_basis(
                        f, convergence_rounds=cycle, manifest_tier=effective_tier,
                        exec_evidence=exec_status,
                    ).to_dict(),
                }
                for f in pass_findings
            ],
            # An infra finding names a sentinel such as "<llm-invoke>", not a
            # file: when a backend call fails there is no code to point at.
            # Anchors are read back as paths that must appear in the diff, so
            # one sentinel makes the review unattestable -- and permanently,
            # since receipts are never pruned and that check reads every
            # cycle, not only the last three. The finding itself stays in
            # findings above, where the failure is still reported.
            # covered_line_ranges below needs no such filter: it is
            # intersected with the diff, so a sentinel entry is inert there.
            "anchors": [
                {
                    "file": f.file,
                    "line": f.line_range[0] if f.line_range else 0,
                    "text": _read_line(cwd, f.file, f.line_range[0] if f.line_range else 0),
                }
                for f in pass_findings
                if f.source != "INFRA"
            ],
            "code_excerpts": assembled_excerpts,
            # self-reported, not measured -- audit-only
            "covered_line_ranges": [
                {
                    "file": f.file,
                    "start": max(1, f.line_range[0] - 10) if f.line_range else 1,
                    "end": (f.line_range[-1] + 10) if f.line_range else 1,
                }
                for f in pass_findings
            ] if pass_findings else (
                [
                    {"file": f, "start": min(lns), "end": max(lns)}
                    for f, lns in (diff_files or {}).items()
                    if lns
                ] or [
                    {"file": str(sf), "start": 1, "end": 1}
                    for sf in source_files
                ]
            ),
        }

        if exec_evidence_dict is not None:
            receipt["exec_evidence"] = exec_evidence_dict

        path = receipts_dir / ("receipt-c%dp%d.json" % (cycle, pass_num))
        path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        written.append(path)

    return written
