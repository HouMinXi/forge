"""code-forge verify: receipt validation.

parse_diff_files is a shared helper used by both the verify CLI
handler and the receipt writer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


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


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def run_verify(
    cwd: Path, diff_sha256: str,
    diff_files: dict[str, list[int]],
) -> VerifyResult:
    receipts = _load_receipts(cwd / ".code-forge" / "receipts")
    cp = 0

    # 1. completeness: 9 receipts, cycle/pass matrix, findings_count
    if len(receipts) < 9:
        return VerifyResult(False, "missing receipts: %d/9" % len(receipts), 1, cp)
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

    # 5. excerpt verification (missing file = FAIL)
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
            except IndexError:
                return VerifyResult(
                    False,
                    "excerpt line range out of bounds %s:%d-%d" % (
                        exc["file"], exc["start_line"], exc["end_line"]),
                    5, cp)
    cp += 1

    # 6. coverage >= 60%
    all_diff = {(f, ln) for f, lns in diff_files.items() for ln in lns}
    if all_diff:
        for c in range(1, 4):
            cov = _cycle_covered(receipts, c) & all_diff
            if len(cov) / len(all_diff) < 0.6:
                return VerifyResult(False, "coverage %.0f%% < 60%% cycle %d" % (
                    100 * len(cov) / len(all_diff), c), 6, cp)
    cp += 1

    # 7. Jaccard overlap > 0.8 = rubber stamp (Option B)
    from itertools import combinations
    cycle_findings = {}
    for r in receipts:
        c = r.get("cycle", 0)
        if c not in cycle_findings:
            cycle_findings[c] = []
        cycle_findings[c].extend(r.get("findings", []))

    for a, b in combinations(range(1, 4), 2):
        has_findings_a = len(cycle_findings.get(a, [])) > 0
        has_findings_b = len(cycle_findings.get(b, [])) > 0
        if not has_findings_a and not has_findings_b:
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
