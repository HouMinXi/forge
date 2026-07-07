"""code-forge verify: receipt validation.

parse_diff_files is a shared helper used by both the verify CLI
handler and the receipt writer.

When hardened=True (default), checks 5/6/7 use reviewer-provided
code_excerpts vs the diff post-image snapshot. When hardened=False,
the original pre-Phase-14 checks run (for fail-before tests and
backward compatibility).

The real anti-shirk guarantees are the R1 pre-commit test gate and
the StateMachine consecutive-clean counter; verify is a tamper check
on receipts, not a replacement for them.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .diff import _extract_post_image_lines, parse_diff_hunks

logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    passed: bool
    reason: str
    checks_run: int = 0
    checks_passed: int = 0


def parse_diff_files(diff_text: str) -> dict[str, list[int]]:
    """Parse git diff text into {file: [changed line numbers]}."""
    import re
    diff_files: dict[str, list[int]] = {}
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@") and current_file:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or "1")
                if current_file not in diff_files:
                    diff_files[current_file] = []
                diff_files[current_file].extend(
                    range(start, start + count)
                )
    return diff_files


def _load_receipts(rd: Path) -> list[dict]:
    if not rd.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(rd.glob("receipt-*.json"))]


def _covered(receipt: dict) -> set[tuple[str, int]]:
    s = set()
    for r in receipt.get("covered_line_ranges", []):
        for ln in range(r["start"], r["end"] + 1):
            s.add((r["file"], ln))
    return s


def _cycle_covered(receipts: list[dict], cycle: int) -> set[tuple[str, int]]:
    u = set()
    for r in receipts:
        if r["cycle"] == cycle:
            u |= _covered(r)
    return u


def _excerpt_covered(receipt: dict) -> set[tuple[str, int]]:
    s = set()
    for exc in receipt.get("code_excerpts", []):
        f = exc.get("file", "")
        start = exc.get("start_line", 0)
        end = exc.get("end_line", 0)
        if isinstance(start, int) and isinstance(end, int) and f:
            for ln in range(start, end + 1):
                s.add((f, ln))
    return s


def _cycle_excerpt_covered(receipts: list[dict], cycle: int) -> set[tuple[str, int]]:
    u = set()
    for r in receipts:
        if r["cycle"] == cycle:
            u |= _excerpt_covered(r)
    return u


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def run_verify(
    cwd: Path, diff_sha256: str,
    diff_files: dict[str, list[int]],
    hardened: bool = True,
    diff_text: str | None = None,
) -> VerifyResult:
    receipts = _load_receipts(cwd / ".code-forge" / "receipts")
    cp = 0

    # 1. completeness: 9 receipts, cycle/pass matrix, findings_count
    # Known design constraint: expects exactly cycles 1-3 x passes 1-3.
    # Reviews that take >3 total rounds write cycle 4+ receipts and fail
    # this check. Intended for post-convergence verification only (the last
    # 3 consecutive clean cycles produce the authoritative 9 receipts).
    if len(receipts) < 9:
        msg = "missing receipts: %d/9" % len(receipts)
        if len(receipts) == 0:
            msg += (
                " -- no review receipts found. Run 'code-forge review' "
                "on your staged changes first"
            )
        return VerifyResult(False, msg, 1, cp)
    seen_keys = set()
    for r in receipts:
        key = (r.get("cycle"), r.get("pass"))
        if key in seen_keys:
            return VerifyResult(False, "duplicate receipt c%dp%d" % key, 1, cp)
        seen_keys.add(key)
        if r.get("findings_count") != len(r.get("findings", [])):
            return VerifyResult(
                False, "findings_count mismatch c%dp%d" % key, 1, cp)
    expected = {(c, p) for c in range(1, 4) for p in range(1, 4)}
    if seen_keys != expected:
        return VerifyResult(False, "missing cycle/pass combinations", 1, cp)
    cp += 1

    # 2. hash
    for r in receipts:
        if r.get("diff_sha256") != diff_sha256:
            return VerifyResult(False, "diff hash mismatch c%dp%d" % (r["cycle"], r["pass"]), 2, cp)
    cp += 1

    # 3. anchors: file must be in diff
    for r in receipts:
        for a in r.get("anchors", []):
            afile = a.get("file", "")
            if afile not in diff_files:
                return VerifyResult(False, "anchor file %s not in diff" % afile, 3, cp)
    cp += 1

    # 4. timestamps: monotonic (no 30s gap -- receipt writer uses 1s offsets)
    ts = [r.get("timestamp", "") for r in receipts]
    if ts != sorted(ts):
        return VerifyResult(False, "timestamps not monotonic", 4, cp)
    cp += 1

    if hardened and diff_text is not None:
        # 5. per-hunk excerpt witness + content/coverage gate. Returns FAIL on an
        #    unwitnessed or fabricated excerpt. Complements (does not replace) the
        #    R1/R2/R3 dynamic verification layer.
        hunk_map, exempt_files = parse_diff_hunks(diff_text)
        post_image = _extract_post_image_lines(diff_text)

        if diff_text.strip() and not hunk_map and not exempt_files:
            return VerifyResult(False, "diff parse failed -- cannot verify excerpts", 5, cp)

        all_excerpts = []
        for r in receipts:
            all_excerpts.extend(r.get("code_excerpts", []))

        # STEP 0: excerpt field validation (before any field access)
        for exc in all_excerpts:
            exc_file = exc.get("file", "<unknown>")
            exc_start = exc.get("start_line", None)
            exc_end = exc.get("end_line", None)
            if (
                exc_file == "<unknown>"
                or not isinstance(exc_start, int)
                or not isinstance(exc_end, int)
                or not isinstance(exc.get("content"), str)
            ):
                return VerifyResult(False, "excerpt missing required fields", 5, cp)

        # STEP A: per-hunk witness check
        for file, hunks in hunk_map.items():
            for hunk in hunks:
                if hunk["is_deletion_only"]:
                    continue
                witnessed = any(
                    exc["file"] == file
                    and max(exc["start_line"], hunk["start"]) <= min(exc["end_line"], hunk["end"])
                    for exc in all_excerpts
                )
                if not witnessed:
                    return VerifyResult(
                        False,
                        "unwitnessed hunk %s:%d-%d" % (file, hunk["start"], hunk["end"]),
                        5, cp,
                    )

        # STEP B: excerpt-to-hunk anchoring
        for exc in all_excerpts:
            content = exc.get("content", "")
            if not content or not content.strip():
                return VerifyResult(
                    False,
                    "excerpt %s:%d has empty content" % (exc["file"], exc["start_line"]),
                    5, cp,
                )
            if exc["file"] not in hunk_map and exc["file"] not in exempt_files:
                return VerifyResult(
                    False,
                    "excerpt %s:%d not in diff" % (exc["file"], exc["start_line"]),
                    5, cp,
                )
            # Exempt files (binary/rename/mode-change) pass without overlap check --
            # they have no hunks in hunk_map, so hunk anchoring cannot be verified.
            # This is intentional: exempt files produce no coverage obligation.
            if exc["file"] in hunk_map:
                overlaps = any(
                    max(exc["start_line"], h["start"]) <= min(exc["end_line"], h["end"])
                    for h in hunk_map[exc["file"]]
                )
                if not overlaps and exc["file"] not in exempt_files:
                    return VerifyResult(
                        False,
                        "excerpt %s:%d-%d not in any diff hunk" % (
                            exc["file"], exc["start_line"], exc["end_line"]),
                        5, cp,
                    )

        # STEP C: content verification against diff post-image
        # The diff is immutable at verify time -- no TOCTOU with working tree.
        # Only lines overlapping between excerpt and diff are compared (GM-B1).
        # Known limitation: STEP C verifies that covered lines are faithful to the
        # post-image but cannot distinguish "covers only context lines" from "covers
        # actual changed lines." A reviewer can pass STEP C by citing only context
        # lines around the change. The 60% coverage floor (check 6) mitigates this.
        for exc in all_excerpts:
            actual_lines = exc.get("content", "").splitlines()
            excerpt_line_map = {}
            for i, ln in enumerate(range(exc["start_line"], exc["end_line"] + 1)):
                if i < len(actual_lines):
                    excerpt_line_map[ln] = actual_lines[i]

            file_lines = post_image.get(exc["file"], {})
            overlap_lines = set(excerpt_line_map.keys()) & set(file_lines.keys())

            if overlap_lines:
                def normalize(s):
                    return s.rstrip()
                for ln in sorted(overlap_lines):
                    if normalize(excerpt_line_map[ln]) != normalize(file_lines[ln]):
                        return VerifyResult(
                            False,
                            "excerpt content mismatch at %s:%d (line %d)" % (
                                exc["file"], exc["start_line"], ln),
                            5, cp,
                        )
        cp += 1

        # 6. excerpt-derived coverage >= 60%
        # covered_line_ranges is self-reported, not measured -- audit-only. Ignored here.
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in range(1, 4):
                cov = _cycle_excerpt_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
                        100 * len(cov) / len(all_diff), c), 6, cp)
        cp += 1

        # 7. Jaccard overlap > 0.8 = rubber stamp.
        # NOTE: identical excerpts across cycles will cause Jaccard > 0.8.
        # This is CORRECT -- it detects rubber-stamping.
        # Known limitation: when all cycles have empty findings (findings=[]),
        # the skip condition below causes Jaccard to never trigger, so
        # identical-excerpt clean reviews always pass (intentional design).
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(range(1, 4), 2):
            if not cycle_findings.get(a) and not cycle_findings.get(b):
                continue
            cov_a = _cycle_excerpt_covered(receipts, a)
            cov_b = _cycle_excerpt_covered(receipts, b)
            if not cov_a and not cov_b:
                return VerifyResult(
                    False,
                    "no excerpt coverage in cycles %d and %d (findings present but excerpts empty)" % (a, b),
                    7, cp,
                )
            j = _jaccard(cov_a, cov_b)
            if j > 0.8:
                return VerifyResult(False, "Jaccard overlap %.2f > 0.8 c%d-c%d" % (j, a, b), 7, cp)
        cp += 1

    else:
        if hardened and diff_text is None:
            logger.info("hardened=True but diff_text=None, using legacy checks")

        # 5. legacy excerpt verification (working tree)
        for r in receipts:
            for exc in r.get("code_excerpts", []):
                fp = cwd / exc["file"]
                if not fp.exists():
                    return VerifyResult(
                        False,
                        "excerpt file missing: %s (c%dp%d)" % (
                            exc["file"], r["cycle"], r["pass"]),
                        5, cp)
                try:
                    lines = fp.read_text().splitlines()
                    actual = "\n".join(lines[exc["start_line"] - 1:exc["end_line"]]) + "\n"
                    claimed = exc["content"]
                    if not claimed.endswith("\n"):
                        claimed += "\n"
                    if actual != claimed:
                        return VerifyResult(
                            False,
                            "excerpt mismatch %s:%d-%d c%dp%d" % (
                                exc["file"], exc["start_line"], exc["end_line"],
                                r["cycle"], r["pass"]),
                            5, cp)
                except (IndexError, OSError) as e:
                    logging.warning("check 5 legacy: %s", e)
                    return VerifyResult(
                        False,
                        "excerpt line range error %s:%d-%d" % (
                            exc["file"], exc["start_line"], exc["end_line"]),
                        5, cp)
        cp += 1

        # 6. legacy coverage >= 60% (self-reported covered_line_ranges)
        all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
        if all_diff:
            for c in range(1, 4):
                cov = _cycle_covered(receipts, c) & all_diff
                if len(cov) / len(all_diff) < 0.6:
                    return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
                        100 * len(cov) / len(all_diff), c), 6, cp)
        cp += 1

        # 7. legacy Jaccard
        cycle_findings = {}
        for r in receipts:
            cyc = r.get("cycle", 0)
            if cyc not in cycle_findings:
                cycle_findings[cyc] = []
            cycle_findings[cyc].extend(r.get("findings", []))

        for a, b in combinations(range(1, 4), 2):
            if not cycle_findings.get(a) and not cycle_findings.get(b):
                continue
            j = _jaccard(_cycle_covered(receipts, a), _cycle_covered(receipts, b))
            if j > 0.8:
                return VerifyResult(False, "Jaccard overlap %.2f > 0.8 c%d-c%d" % (j, a, b), 7, cp)
        cp += 1

    return VerifyResult(True, "all 7 checks passed", 7, 7)


def write_attestation(cwd: Path, diff_sha256: str) -> Path:
    import datetime
    att = {
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "diff_sha256": diff_sha256,
        "receipt_sha256": hashlib.sha256(
            json.dumps(_load_receipts(cwd / ".code-forge" / "receipts"), sort_keys=True).encode()
        ).hexdigest(),
        "result": "PASS",
    }
    d = cwd / ".code-forge"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "attestation.json"
    p.write_text(json.dumps(att, indent=2))
    return p
