"""Tests for the canary gate core (laziness detection for the inline outlet).

The gate decides, from the findings a reviewer produced and the
reviewer-invisible canary manifest, whether enough planted canaries were
caught to clear the gate. A reviewer cannot tell a canary from a real defect,
so reliably catching K of N requires genuine review -- making laziness
objectively detectable instead of self-reported.

The gate validates reviewer ATTENTION, not model capability. A miss means the
round's findings are unreliable; it never feeds back into outlet selection.
"""
from __future__ import annotations

import pytest

from code_forge.canary import (
    Canary,
    DEFAULT_LINE_WINDOW,
    evaluate_canary_coverage,
    partition_canary_findings,
)


def _canary(cid: str, file: str, line: int, desc: str = "") -> Canary:
    # sha256 is the manifest key for provenance stripping, not used by the
    # gate logic; a placeholder keeps these tests focused on coverage.
    return Canary(
        canary_id=cid, file=file, line=line, sha256="a" * 64, description=desc
    )


def _finding(file: str, line: int) -> dict:
    return {"file": file, "line": line, "severity": "high", "description": "d"}


def test_all_canaries_caught_passes_gate() -> None:
    manifest = [_canary("c1", "src/a.py", 10), _canary("c2", "src/b.py", 20)]
    findings = [_finding("src/a.py", 10), _finding("src/b.py", 20)]

    result = evaluate_canary_coverage(findings, manifest, threshold=2)

    assert result.passed is True
    assert set(result.caught) == {"c1", "c2"}
    assert result.missed == ()


def test_no_findings_misses_all_and_fails_gate() -> None:
    # A lazy reviewer who rubber-stamps reports nothing.
    manifest = [_canary("c1", "src/a.py", 10), _canary("c2", "src/b.py", 20)]

    result = evaluate_canary_coverage([], manifest, threshold=1)

    assert result.passed is False
    assert result.caught == ()
    assert set(result.missed) == {"c1", "c2"}


def test_catch_count_at_threshold_passes() -> None:
    manifest = [
        _canary("c1", "src/a.py", 10),
        _canary("c2", "src/b.py", 20),
        _canary("c3", "src/c.py", 30),
    ]
    findings = [_finding("src/a.py", 10), _finding("src/b.py", 20)]

    result = evaluate_canary_coverage(findings, manifest, threshold=2)

    assert result.passed is True
    assert len(result.caught) == 2


def test_catch_count_below_threshold_fails() -> None:
    manifest = [
        _canary("c1", "src/a.py", 10),
        _canary("c2", "src/b.py", 20),
        _canary("c3", "src/c.py", 30),
    ]
    findings = [_finding("src/a.py", 10)]

    result = evaluate_canary_coverage(findings, manifest, threshold=2)

    assert result.passed is False
    assert len(result.caught) == 1


def test_finding_within_line_window_catches_canary() -> None:
    # Genuine reviewers cite approximate lines; within the window still counts.
    manifest = [_canary("c1", "src/a.py", 10)]
    findings = [_finding("src/a.py", 10 + DEFAULT_LINE_WINDOW)]

    result = evaluate_canary_coverage(findings, manifest, threshold=1)

    assert result.passed is True


def test_finding_outside_line_window_misses_canary() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    findings = [_finding("src/a.py", 10 + DEFAULT_LINE_WINDOW + 1)]

    result = evaluate_canary_coverage(findings, manifest, threshold=1)

    assert result.passed is False
    assert result.missed == ("c1",)


def test_finding_in_wrong_file_does_not_catch() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    findings = [_finding("src/other.py", 10)]

    result = evaluate_canary_coverage(findings, manifest, threshold=1)

    assert result.passed is False


def test_path_normalization_matches_equivalent_paths() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    findings = [_finding("./src/a.py", 10)]

    result = evaluate_canary_coverage(findings, manifest, threshold=1)

    assert result.passed is True


def test_empty_manifest_fails_closed() -> None:
    # A gate with no canaries cannot probe laziness -> never a free pass.
    result = evaluate_canary_coverage(
        [_finding("src/a.py", 1)], [], threshold=1
    )

    assert result.total == 0
    assert result.passed is False


def test_threshold_below_one_is_rejected() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    with pytest.raises(ValueError):
        evaluate_canary_coverage([], manifest, threshold=0)


def test_threshold_exceeding_canary_count_is_rejected() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    with pytest.raises(ValueError):
        evaluate_canary_coverage([], manifest, threshold=2)


def test_partition_routes_matching_finding_to_canary() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    real_f = _finding("src/b.py", 99)
    canary_f = _finding("src/a.py", 10)

    part = partition_canary_findings([real_f, canary_f], manifest)

    assert part.canary == (canary_f,)
    assert part.real == (real_f,)


def test_partition_with_no_matches_keeps_all_real() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    findings = [_finding("src/b.py", 5), _finding("src/c.py", 7)]

    part = partition_canary_findings(findings, manifest)

    assert part.canary == ()
    assert len(part.real) == 2


def test_partition_empty_manifest_keeps_all_real() -> None:
    findings = [_finding("src/a.py", 10)]

    part = partition_canary_findings(findings, [])

    assert part.canary == ()
    assert part.real == (findings[0],)


def test_partition_empty_findings_yields_empty() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]

    part = partition_canary_findings([], manifest)

    assert part.real == ()
    assert part.canary == ()


def test_partition_uses_line_window() -> None:
    manifest = [_canary("c1", "src/a.py", 10)]
    within = _finding("src/a.py", 10 + DEFAULT_LINE_WINDOW)
    outside = _finding("src/a.py", 10 + DEFAULT_LINE_WINDOW + 1)

    part = partition_canary_findings([within, outside], manifest)

    assert part.canary == (within,)
    assert part.real == (outside,)


def test_file_level_finding_does_not_catch_top_of_file_canary() -> None:
    # A line-0 finding makes no locatable claim; flagging the file is not the
    # same as flagging the planted defect, so it must NOT catch the canary --
    # even when the canary sits within line_window of line 0.
    manifest = [_canary("c1", "src/a.py", 1)]
    findings = [_finding("src/a.py", 0)]

    result = evaluate_canary_coverage(findings, manifest, threshold=1)

    assert result.passed is False
    assert result.missed == ("c1",)


def test_partition_keeps_file_level_finding_as_real() -> None:
    # The mirror of the gate case: a real file-level finding near a top-of-file
    # canary must survive as a real finding, not be dropped as a canary.
    manifest = [_canary("c1", "src/a.py", 1)]
    file_level = _finding("src/a.py", 0)

    part = partition_canary_findings([file_level], manifest)

    assert part.real == (file_level,)
    assert part.canary == ()
