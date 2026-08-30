"""Eval scorer: false-green rate computation + output formatting.

Computes four-quadrant classification of eval results:
  - caught: expected HOLD, actually flagged (true positive)
  - missed: expected HOLD, actual PASS (false green)
  - correct_pass: expected PASS, actual PASS (true negative)
  - false_positive: expected PASS, actual HOLD (over-block)

Advisory axis scoring:
  - advisory_caught: pure-RUNTIME entries (expected_verdict=PASS + expected_advisory
    non-empty) where advisory_caught_count >= majority threshold
  - advisory_missed: pure-RUNTIME entries where advisory_caught_count < threshold
  - advisory_caught_count on EvalResult is SEPARATE from caught_count; it never
    affects actual_verdict computation (prevents eval result corruption)

SKIPPED entries are excluded from the caught+missed denominator.
Output uses raw counts ("Caught: 7/9"), never ratios (carry-forward 2).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from code_forge.eval.corpus import (
    CorpusEntry,
    ExpectedFinding,
    valid_line_range,
)


def advisory_caught(advisory_text: str, keywords: list[str]) -> bool:
    """Case-insensitive keyword substring match against advisory text.

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


def finding_hit(actual: dict, expected: ExpectedFinding) -> bool:
    """Match one actual finding against one findings-level answer.

    Rules (deterministic, documented for the bank's consumers):
      1. The file must match exactly.
      2. When both sides carry a valid two-int line range, the ranges
         must overlap by at least one line.
      3. When either side lacks a valid range, descriptions must share
         at least two significant tokens (lowercased alphanumeric runs
         of length >= 4).

    A malformed actual line_range is treated as absent (falls through
    to the description rule) -- the harness must never crash on the
    state.json of an arbitrary run.
    """
    if actual.get("file") != expected.file:
        return False
    actual_range = actual.get("line_range")
    if (
        valid_line_range(expected.line_range)
        and valid_line_range(actual_range)
    ):
        exp_lo, exp_hi = expected.line_range
        act_lo, act_hi = actual_range[0], actual_range[1]
        return max(exp_lo, act_lo) <= min(exp_hi, act_hi)

    def _tokens(text: str) -> set[str]:
        return {
            t for t in re.findall(r"[a-z0-9_]+", (text or "").lower())
            if len(t) >= 4
        }

    actual_description = actual.get("description")
    actual_text = (
        actual_description if isinstance(actual_description, str)
        else str(actual_description or "")
    )
    expected_tokens = _tokens(expected.description)
    actual_tokens = _tokens(actual_text)
    shared = len(expected_tokens & actual_tokens)
    if not expected_tokens:
        # No significant token at all in the answer key ("RCE bug"):
        # fall back to any shared alphanumeric token of any length,
        # so a valid key is never permanently un-hittable.
        def _any_tokens(text: str) -> set[str]:
            return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))
        return bool(
            _any_tokens(expected.description) & _any_tokens(actual_text)
        )
    if len(expected_tokens) < 2:
        # A concise answer key (one significant token) can only ever
        # demand one shared token; requiring two would make it
        # permanently un-hittable.
        return shared >= 1
    return shared >= 2



def score_findings(
    entry: CorpusEntry, confirmed: list[dict]
) -> tuple[int, int, int]:
    """(hits, misses, fps) for one run against the entry's answer key.

    Maximum bipartite matching (Kuhn's augmenting paths): each actual
    finding can hit at most one expected finding, so one actual cannot
    inflate the hit count across duplicated or overlapping answer
    entries, and no greedy first-match choice can under-count. An actual
    finding matching no expected finding counts as a false positive.
    Entries without an answer key score (0, 0, 0).
    """
    expected = entry.expected_findings
    if not expected:
        return (0, 0, 0)
    # Maximum bipartite matching (Kuhn's augmenting paths): the
    # greedy first-match pass can under-count when one actual could
    # satisfy several expected findings and choices interact. Sets
    # are tiny (bank sizes), so O(V*E) DFS is free.
    match_actual = [-1] * len(confirmed)
    used = []

    def _try_match(ei: int) -> bool:
        for ai, a in enumerate(confirmed):
            if used[ai] or not finding_hit(a, expected[ei]):
                continue
            used[ai] = True
            if (
                match_actual[ai] == -1
                or _try_match(match_actual[ai])
            ):
                match_actual[ai] = ei
                return True
        return False

    hits = 0
    for ei in range(len(expected)):
        used = [False] * len(confirmed)
        if _try_match(ei):
            hits += 1
    fps = len(confirmed) - hits
    return (hits, len(expected) - hits, fps)


def pick_best_findings(
    per_run: list[tuple[int, int, int]],
) -> tuple[int, int, int]:
    """Best-run findings aggregation across a multi-run replay.

    Most hits wins; ties break toward fewest false positives; further
    ties keep the first run. Empty input is (0, 0, 0).
    """
    if not per_run:
        return (0, 0, 0)
    return max(per_run, key=lambda t: (t[0], -t[2]))


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
    finding_hits: int = 0
    finding_misses: int = 0
    finding_fps: int = 0
    findings_evidence: bool = True


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

    Derived metrics (properties, not fields, so they cannot drift from the
    counts they come from): precision, recall, f1, signal_to_noise. Each
    returns None rather than 0.0 when its denominator is zero -- an
    abstention and a score of zero are different claims, and a report that
    merges them lets an empty run read as a catastrophic one.
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
    findings_expected: int = 0
    findings_hit: int = 0
    findings_misses: int = 0
    findings_fp: int = 0
    findings_skipped_entries: int = 0

    @property
    def precision(self) -> float | None:
        """Share of emitted findings that matched the answer key.

        None when nothing was emitted: no claim was made, so no claim can
        be wrong. Distinct from 0.0, which means findings were emitted and
        every one of them was a false positive.
        """
        emitted = self.findings_hit + self.findings_fp
        if emitted == 0:
            return None
        return self.findings_hit / emitted

    @property
    def recall(self) -> float | None:
        """Share of known defects the review found.

        None when the corpus carries no answer key to be measured against.
        """
        if self.findings_expected == 0:
            return None
        return self.findings_hit / self.findings_expected

    @property
    def f1(self) -> float | None:
        """Harmonic mean of precision and recall.

        None when either component is None, and also when both are 0.0 --
        the harmonic mean is then 0/0, a division that cannot be performed
        rather than a score of nothing.
        """
        p, r = self.precision, self.recall
        if p is None or r is None:
            return None
        if p + r == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def signal_to_noise(self) -> float | None:
        """Hits per false positive (CR-Bench SNR).

        None when there were no false positives: infinitely good is not a
        number, and printing inf in a report invites it being read as one.
        """
        if self.findings_fp == 0:
            return None
        return self.findings_hit / self.findings_fp


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

    SKIPPED entries are excluded from the caught+missed denominator.
    Verdict-match classification uses expected_verdict and actual_verdict/caught_count.
    Advisory classification uses advisory_caught_count for pure-RUNTIME entries.

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

    findings_expected = 0
    findings_hit = 0
    findings_fp = 0
    findings_skipped_entries = 0
    for r in results:
        if not r.entry.expected_findings:
            # No answer key: nothing to be right or wrong about.
            continue
        if r.skipped_reason or not r.findings_evidence:
            # Two ways an entry can fail to produce a scoreable result: it
            # was skipped outright, or it ran and yielded no state evidence.
            # Both used to drop out of the numerator AND the denominator,
            # so an entry the pipeline failed on cost nothing -- and under a
            # budget the entries that fail are the expensive ones, meaning
            # the hard defects. The defect was there and it was not
            # reported; how the run failed is not the corpus's problem.
            # Recall pays. Precision does not: a tool that emitted nothing
            # has made no claim that can be wrong.
            findings_expected += len(r.entry.expected_findings)
            findings_skipped_entries += 1
            continue
        findings_expected += len(r.entry.expected_findings)
        findings_hit += r.finding_hits
        findings_fp += r.finding_fps

    return EvalSummary(
        total=len(results),
        caught=caught,
        missed=missed,
        correct_pass=correct_pass,
        false_positive=false_positive,
        skipped=skipped,
        advisory_caught=adv_caught,
        advisory_missed=adv_missed,
        findings_expected=findings_expected,
        findings_hit=findings_hit,
        findings_misses=findings_expected - findings_hit,
        findings_fp=findings_fp,
        findings_skipped_entries=findings_skipped_entries,
        results=results,
    )


_RATIO_DISPLAY_MIN_ENTRIES = 30
"""Corpus size below which format_table abstains from printing ratios.

Phase 17 (17-03-PLAN.md, Pitfall 4) fixed the rule that the table shows raw
counts, never percentages, because a ratio over nine entries is false
precision. Phase 57 sizes the corpus at >=60 so ratios become meaningful,
which retires the premise but not the concern. This threshold is the seam:
below it the table says n/a and why, above it the ratios print.

Deliberately lower than the corpus floor of 60. The two numbers answer
different questions -- "is a ratio meaningful to read" versus "is a ratio
stable enough to publish" -- and conflating them would either hide readable
numbers or bless unstable ones.
"""


def _fmt_ratio(value: float | None) -> str:
    """Percentage, or 'n/a' when the metric abstained.

    format specs raise TypeError on None, and a crash in the reporting path
    is a self-inflicted false negative: the run produced numbers and we
    failed to show them.
    """
    return "n/a" if value is None else f"{value:.1%}"


def _fmt_plain(value: float | None) -> str:
    """Bare ratio (not a percentage), or 'n/a'. Same None contract."""
    return "n/a" if value is None else f"{value:.2f}"


def format_table(summary: EvalSummary) -> str:
    """Format eval summary as a human-readable ASCII table for stderr.

    Raw counts throughout ("Caught: 7/9"), per carry-forward 2. Derived
    ratios (precision, recall, F1, SNR) print only at
    _RATIO_DISPLAY_MIN_ENTRIES or more entries; below that the line states
    the abstention and its reason. The counts they derive from print either
    way, so nothing is hidden by the threshold.
    Skip rate shown beside catch count.
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

    if summary.findings_expected > 0:
        findings_total = summary.findings_expected
        lines.append("")
        lines.append(
            "Findings-level: hit %d/%d (missed %d), false positives %d"
            % (
                summary.findings_hit,
                findings_total,
                findings_total - summary.findings_hit,
                summary.findings_fp,
            )
        )

        # Derived ratios. Phase 17 fixed a rule here -- raw counts, never
        # percentages -- because a ratio over n=9 is false precision. That
        # holds and the counts above are still printed unconditionally. The
        # ratios appear only once the corpus is large enough to read them,
        # and below that threshold the line says why rather than vanishing:
        # a reader must be able to tell abstention from breakage.
        if summary.total >= _RATIO_DISPLAY_MIN_ENTRIES:
            lines.append(
                "Precision %s | Recall %s | F1 %s | Signal-to-noise %s"
                % (
                    _fmt_ratio(summary.precision),
                    _fmt_ratio(summary.recall),
                    _fmt_ratio(summary.f1),
                    _fmt_plain(summary.signal_to_noise),
                )
            )
        else:
            lines.append(
                "Precision/Recall/F1: n/a (n=%d, need %d -- a ratio over a "
                "corpus this small is false precision)"
                % (summary.total, _RATIO_DISPLAY_MIN_ENTRIES)
            )

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
        "findings_expected": summary.findings_expected,
        "findings_hit": summary.findings_hit,
        "findings_misses": summary.findings_misses,
        "findings_fp": summary.findings_fp,
        "results": [
            {
                "entry": {
                    "name": r.entry.name,
                    "diff_file": r.entry.diff_file,
                    "expected_verdict": r.entry.expected_verdict,
                    "axis_tags": r.entry.axis_tags,
                    "expected_advisory": r.entry.expected_advisory,
                    "expected_findings": [
                        {
                            "file": f.file,
                            "description": f.description,
                            "line_range": (
                                list(f.line_range)
                                if f.line_range is not None
                                else None
                            ),
                        }
                        for f in r.entry.expected_findings
                    ],
                },
                "actual_verdict": r.actual_verdict,
                "runs": r.runs,
                "caught_count": r.caught_count,
                "skipped_reason": r.skipped_reason,
                "advisory_caught_count": r.advisory_caught_count,
                "finding_hits": r.finding_hits,
                "finding_misses": r.finding_misses,
                "finding_fps": r.finding_fps,
            }
            for r in summary.results
        ],
    }
    output_path.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
