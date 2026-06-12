"""Eval scorer: false-green rate computation + output formatting (D-10).

Computes four-quadrant classification of eval results:
  - caught: expected HOLD, actually flagged (true positive)
  - missed: expected HOLD, actual PASS (false green)
  - correct_pass: expected PASS, actual PASS (true negative)
  - false_positive: expected PASS, actual HOLD (over-block)

Advisory axis scoring (D-06/D-12):
  - advisory_caught: pure-RUNTIME entries (expected_verdict=PASS + expected_advisory
    non-empty) where advisory_caught_count >= majority threshold
  - advisory_missed: pure-RUNTIME entries where advisory_caught_count < threshold
  - advisory_caught_count on EvalResult is SEPARATE from caught_count; it never
    affects actual_verdict computation (prevents eval result corruption)

SKIPPED entries are excluded from the caught+missed denominator (D-12).
Output uses raw counts ("Caught: 7/9"), never ratios (carry-forward 2).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from code_forge.eval.corpus import CorpusEntry


def advisory_caught(advisory_text: str, keywords: list[str]) -> bool:
    """Case-insensitive keyword substring match against advisory text (D-12).

    Returns True if any keyword (lowercased) is a substring of advisory_text
    (lowercased). Any single keyword hit is sufficient; ALL keywords need not
    match. Returns False for empty text or empty keyword list.

    Args:
        advisory_text: concatenated advisory finding descriptions.
        keywords: keyword strings to search for (case-insensitive).

    Returns:
        True if any keyword is found as a substring; False otherwise.
    """
    if not advisory_text or not keywords:
        return False
    lower_text = advisory_text.lower()
    for kw in keywords:
        if kw.lower() in lower_text:
            return True
    return False


@dataclass(frozen=True)
class EvalResult:
    """Result of running one corpus entry through the pipeline.

    Fields:
        entry: the corpus entry that was evaluated.
        actual_verdict: what forge actually produced ("PASS", "HOLD", "SKIPPED").
        runs: number of times the entry was run.
        caught_count: how many runs flagged it (for multi-run majority, verdict-match).
        skipped_reason: why it was skipped ("" if not skipped).
        advisory_caught_count: how many runs had advisory text matching expected_advisory
            keywords. SEPARATE from caught_count -- never affects actual_verdict
            computation. Default 0 for non-RUNTIME entries.
    """

    entry: CorpusEntry
    actual_verdict: str
    runs: int
    caught_count: int
    skipped_reason: str
    advisory_caught_count: int = 0


@dataclass(frozen=True)
class EvalSummary:
    """Aggregated eval results across all corpus entries.

    Fields:
        total: total number of entries evaluated.
        caught: expected HOLD, actually flagged (true positive, verdict-match).
        missed: expected HOLD, actual PASS (false green, verdict-match).
        correct_pass: expected PASS, actual PASS (true negative).
        false_positive: expected PASS, actual HOLD (over-block).
        skipped: entries that could not be evaluated.
        advisory_caught: pure-RUNTIME entries where advisory keyword match >= majority.
        advisory_missed: pure-RUNTIME entries where advisory keyword match < majority.
        results: per-entry results list.
    """

    total: int
    caught: int
    missed: int
    correct_pass: int
    false_positive: int
    skipped: int
    advisory_caught: int
    advisory_missed: int
    results: list[EvalResult]


def _is_pure_runtime_advisory(result: EvalResult) -> bool:
    """True for entries scored by advisory content-match rather than verdict-match.

    Pure-RUNTIME advisory entries: expected_verdict == "PASS" AND expected_advisory
    is non-empty. For these entries, the pipeline verdict is PASS (RUNTIME cannot
    block), so verdict-match is trivially correct; advisory keyword match is the
    meaningful signal.

    Dual-axis entries (e.g., RUNTIME+FIXVAL with expected_verdict == "HOLD") are
    NOT pure-runtime: the FIXVAL blocking verdict gates "caught" classification.
    Advisory scoring for dual-axis entries is reported separately in
    advisory_caught_count but does NOT gate the caught/missed classification.
    """
    return (
        result.entry.expected_verdict == "PASS"
        and bool(result.entry.expected_advisory)
    )


def compute_summary(results: list[EvalResult]) -> EvalSummary:
    """Compute four-quadrant classification from eval results.

    SKIPPED entries are excluded from the caught+missed denominator (D-12).
    Verdict-match classification uses expected_verdict and actual_verdict/caught_count.
    Advisory classification uses advisory_caught_count for pure-RUNTIME entries (D-06).

    Args:
        results: list of EvalResult from replay_entry calls.

    Returns:
        EvalSummary with aggregate counts and per-entry results.
    """
    caught = 0
    missed = 0
    correct_pass = 0
    false_positive = 0
    skipped = 0
    adv_caught = 0
    adv_missed = 0

    for r in results:
        if r.skipped_reason:
            skipped += 1
            continue

        if _is_pure_runtime_advisory(r):
            # Pure-RUNTIME advisory entry: score by advisory keyword match.
            # actual_verdict is expected to be PASS (RUNTIME cannot block),
            # so verdict-match is trivially correct_pass; advisory is the signal.
            threshold = math.ceil(r.runs / 2) if r.runs > 1 else 1
            if r.advisory_caught_count >= threshold:
                adv_caught += 1
            else:
                adv_missed += 1
            # Also count verdict quadrant (expected PASS, report actual).
            if r.actual_verdict == "PASS":
                correct_pass += 1
            else:
                false_positive += 1
        elif r.entry.expected_verdict == "HOLD":
            threshold = math.ceil(r.runs / 2) if r.runs > 1 else 1
            if r.caught_count >= threshold:
                caught += 1
            else:
                missed += 1
        elif r.entry.expected_verdict == "PASS":
            # Non-advisory PASS entry: verdict-match only.
            if r.actual_verdict == "PASS":
                correct_pass += 1
            else:
                false_positive += 1

    return EvalSummary(
        total=len(results),
        caught=caught,
        missed=missed,
        correct_pass=correct_pass,
        false_positive=false_positive,
        skipped=skipped,
        advisory_caught=adv_caught,
        advisory_missed=adv_missed,
        results=results,
    )


def format_table(summary: EvalSummary) -> str:
    """Format eval summary as a human-readable ASCII table for stderr.

    Uses raw counts "Caught: 7/9", never ratios (carry-forward 2).
    Skip rate shown beside catch count (D-12).
    Advisory counts shown when non-zero.

    Args:
        summary: computed EvalSummary.

    Returns:
        Multi-line string with table and summary line.
    """
    lines: list[str] = []

    # Header
    header = (
        f"{'Name':<30} {'Expected':<10} {'Actual':<10} "
        f"{'Runs':<6} {'Caught':<8} {'Advisory':<10} {'Status':<10}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    # Per-entry rows
    for r in summary.results:
        if r.skipped_reason:
            status = "SKIPPED"
        elif _is_pure_runtime_advisory(r):
            runs = r.runs if r.runs > 0 else 1
            threshold = math.ceil(runs / 2) if runs > 1 else 1
            status = (
                "ADV-CAUGHT" if r.advisory_caught_count >= threshold else "ADV-MISSED"
            )
        elif r.entry.expected_verdict == "HOLD" and r.caught_count > 0:
            status = "CAUGHT"
        elif r.entry.expected_verdict == "HOLD" and r.caught_count == 0:
            status = "MISSED"
        elif r.entry.expected_verdict == "PASS" and r.actual_verdict == "PASS":
            status = "OK"
        else:
            status = "OVER-BLOCK"

        lines.append(
            f"{r.entry.name:<30} "
            f"{r.entry.expected_verdict:<10} "
            f"{r.actual_verdict:<10} "
            f"{r.runs:<6} "
            f"{r.caught_count:<8} "
            f"{r.advisory_caught_count:<10} "
            f"{status:<10}"
        )

    lines.append("-" * len(header))

    # Summary line: raw counts only
    denominator = summary.caught + summary.missed
    summary_parts = [f"Caught: {summary.caught}/{denominator}"]
    if summary.skipped > 0:
        summary_parts.append(f"({summary.skipped} skipped)")
    if summary.correct_pass > 0:
        summary_parts.append(f"Correct pass: {summary.correct_pass}")
    if summary.false_positive > 0:
        summary_parts.append(f"Over-block: {summary.false_positive}")
    advisory_total = summary.advisory_caught + summary.advisory_missed
    if advisory_total > 0:
        summary_parts.append(
            f"Advisory caught: {summary.advisory_caught}/{advisory_total}"
        )

    lines.append(" | ".join(summary_parts))

    return "\n".join(lines)


def write_json_report(summary: EvalSummary, output_path: Path) -> None:
    """Write eval summary as JSON to file.

    Args:
        summary: computed EvalSummary.
        output_path: path to write JSON report.
    """
    data = {
        "total": summary.total,
        "caught": summary.caught,
        "missed": summary.missed,
        "correct_pass": summary.correct_pass,
        "false_positive": summary.false_positive,
        "skipped": summary.skipped,
        "advisory_caught": summary.advisory_caught,
        "advisory_missed": summary.advisory_missed,
        "results": [
            {
                "entry": {
                    "name": r.entry.name,
                    "diff_file": r.entry.diff_file,
                    "expected_verdict": r.entry.expected_verdict,
                    "axis_tags": r.entry.axis_tags,
                    "expected_advisory": r.entry.expected_advisory,
                },
                "actual_verdict": r.actual_verdict,
                "runs": r.runs,
                "caught_count": r.caught_count,
                "skipped_reason": r.skipped_reason,
                "advisory_caught_count": r.advisory_caught_count,
            }
            for r in summary.results
        ],
    }
    output_path.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
