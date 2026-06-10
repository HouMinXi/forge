"""Eval scorer: false-green rate computation + output formatting (D-10).

Computes four-quadrant classification of eval results:
  - caught: expected HOLD, actually flagged (true positive)
  - missed: expected HOLD, actual PASS (false green)
  - correct_pass: expected PASS, actual PASS (true negative)
  - false_positive: expected PASS, actual HOLD (over-block)

SKIPPED entries are excluded from the caught+missed denominator (D-12).
Output uses raw counts ("Caught: 7/9"), never ratios (carry-forward 2).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from code_forge.eval.corpus import CorpusEntry


@dataclass(frozen=True)
class EvalResult:
    """Result of running one corpus entry through the pipeline.

    Fields:
        entry: the corpus entry that was evaluated.
        actual_verdict: what forge actually produced ("PASS", "HOLD", "SKIPPED").
        runs: number of times the entry was run.
        caught_count: how many runs flagged it (for multi-run majority).
        skipped_reason: why it was skipped ("" if not skipped).
    """

    entry: CorpusEntry
    actual_verdict: str
    runs: int
    caught_count: int
    skipped_reason: str


@dataclass(frozen=True)
class EvalSummary:
    """Aggregated eval results across all corpus entries.

    Fields:
        total: total number of entries evaluated.
        caught: expected HOLD, actually flagged (true positive).
        missed: expected HOLD, actual PASS (false green).
        correct_pass: expected PASS, actual PASS (true negative).
        false_positive: expected PASS, actual HOLD (over-block).
        skipped: entries that could not be evaluated.
        results: per-entry results list.
    """

    total: int
    caught: int
    missed: int
    correct_pass: int
    false_positive: int
    skipped: int
    results: list[EvalResult]


def compute_summary(results: list[EvalResult]) -> EvalSummary:
    """Compute four-quadrant classification from eval results.

    SKIPPED entries are excluded from the caught+missed denominator (D-12).
    Classification uses expected_verdict and actual_verdict/caught_count.

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

    for r in results:
        if r.skipped_reason:
            skipped += 1
            continue

        if r.entry.expected_verdict == "HOLD":
            threshold = math.ceil(r.runs / 2) if r.runs > 1 else 1
            if r.caught_count >= threshold:
                caught += 1
            else:
                missed += 1
        elif r.entry.expected_verdict == "PASS":
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
        results=results,
    )


def format_table(summary: EvalSummary) -> str:
    """Format eval summary as a human-readable ASCII table for stderr.

    Uses raw counts "Caught: 7/9", never ratios (carry-forward 2).
    Skip rate shown beside catch count (D-12).

    Args:
        summary: computed EvalSummary.

    Returns:
        Multi-line string with table and summary line.
    """
    lines: list[str] = []

    # Header
    header = f"{'Name':<30} {'Expected':<10} {'Actual':<10} {'Runs':<6} {'Caught':<8} {'Status':<10}"
    lines.append(header)
    lines.append("-" * len(header))

    # Per-entry rows
    for r in summary.results:
        if r.skipped_reason:
            status = "SKIPPED"
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
        "results": [
            {
                "entry": {
                    "name": r.entry.name,
                    "diff_file": r.entry.diff_file,
                    "expected_verdict": r.entry.expected_verdict,
                    "axis_tags": r.entry.axis_tags,
                },
                "actual_verdict": r.actual_verdict,
                "runs": r.runs,
                "caught_count": r.caught_count,
                "skipped_reason": r.skipped_reason,
            }
            for r in summary.results
        ],
    }
    output_path.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
