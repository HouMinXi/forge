"""Canary gate core: objective laziness detection for code review.

A canary is a planted defect (an in-place semantic mutation) injected into an
isolated review copy of the diff. A reviewer who genuinely reads the code
reports a finding at the canary's location; a reviewer who rubber-stamps does
not. Because a reviewer cannot distinguish a canary from a real defect,
reliably catching K of N canaries REQUIRES genuine review -- so laziness
becomes objectively detectable rather than self-reported.

This module holds the deterministic canary checks: the coverage gate
(evaluate_canary_coverage) and the canary-finding partition
(partition_canary_findings) that drops planted defects from the reported
findings. Generation, injection, fresh-context dispatch, and evidence
re-verify (evidence.py) live in sibling modules.

The gate validates reviewer ATTENTION, not model capability: a miss means the
round's findings are unreliable, never that the model is weak or should be
switched. Callers must not let a canary result drive outlet or model
selection.

Public types: Canary, CanaryGateResult, CanaryPartition,
evaluate_canary_coverage, partition_canary_findings
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .findings import finding_line

# Genuine line citations land within +/- this many lines of the mutation.
# Kept tight so an unrelated finding on a nearby line rarely grants false
# credit.
DEFAULT_LINE_WINDOW = 2


@dataclass(frozen=True)
class Canary:
    """A planted defect tracked in the reviewer-invisible manifest.

    file/line locate the mutation for matching against reviewer findings.
    sha256 records the hash of the injected mutation for the audit log (and
    lets a future writer-side flow strip only hash-matched content); the
    inline flow keeps only the filtered findings, so it never re-applies
    canary code. description records the defect for the audit log and is never
    shown to the reviewer.
    """

    canary_id: str
    file: str
    line: int
    sha256: str
    description: str = ""


@dataclass(frozen=True)
class CanaryGateResult:
    """Outcome of evaluating canary coverage for one review round."""

    total: int
    threshold: int
    caught: tuple[str, ...]
    missed: tuple[str, ...]

    @property
    def passed(self) -> bool:
        # total == 0 fails closed: a gate with no canaries cannot prove the
        # reviewer was attentive, so it never grants a pass.
        return self.total > 0 and len(self.caught) >= self.threshold


@dataclass(frozen=True)
class CanaryPartition:
    """A reviewer's findings split into real findings and canary findings.

    real findings proceed to evidence re-verify and the verdict; canary
    findings are dropped so planted defects never reach the user or the
    persisted result.
    """

    real: tuple[Mapping[str, object], ...]
    canary: tuple[Mapping[str, object], ...]


def _norm(path: str) -> str:
    return os.path.normpath(str(path))


def _matches(
    finding: Mapping[str, object],
    canary: Canary,
    line_window: int,
) -> bool:
    if _norm(finding.get("file") or "") != _norm(canary.file):
        return False
    line = finding_line(finding)
    if line <= 0:
        # A file-level finding makes no locatable claim. Flagging the file is
        # not the same as flagging the planted defect, so it neither catches a
        # canary nor gets dropped as one (it stays a real finding).
        return False
    return abs(line - canary.line) <= line_window


def _is_caught(
    canary: Canary,
    findings: Sequence[Mapping[str, object]],
    line_window: int,
) -> bool:
    return any(_matches(finding, canary, line_window) for finding in findings)


def evaluate_canary_coverage(
    findings: Sequence[Mapping[str, object]],
    manifest: Sequence[Canary],
    *,
    threshold: int,
    line_window: int = DEFAULT_LINE_WINDOW,
) -> CanaryGateResult:
    """Decide whether a review round caught enough planted canaries.

    findings: the reviewer's findings, each a mapping with "file" and "line"
        (the validated reviewer-JSON finding shape; extra keys are ignored).
    manifest: the planted canaries for this round.
    threshold: minimum number of canaries that must be caught to pass.
    line_window: a finding catches a canary when it cites the same file
        within +/- line_window lines of the canary's line.

    Raises ValueError when threshold < 1 or (for a non-empty manifest) when
    threshold exceeds the canary count -- both are caller misconfigurations.
    An empty manifest returns a fail-closed result (total == 0, not passed)
    rather than raising, so the verdict path degrades safely.
    """
    if threshold < 1:
        raise ValueError("threshold must be >= 1, got %d" % threshold)

    total = len(manifest)
    if total == 0:
        return CanaryGateResult(
            total=0, threshold=threshold, caught=(), missed=()
        )

    if threshold > total:
        raise ValueError(
            "threshold %d exceeds canary count %d" % (threshold, total)
        )

    caught: list[str] = []
    missed: list[str] = []
    for canary in manifest:
        if _is_caught(canary, findings, line_window):
            caught.append(canary.canary_id)
        else:
            missed.append(canary.canary_id)

    return CanaryGateResult(
        total=total,
        threshold=threshold,
        caught=tuple(caught),
        missed=tuple(missed),
    )


def partition_canary_findings(
    findings: Sequence[Mapping[str, object]],
    manifest: Sequence[Canary],
    *,
    line_window: int = DEFAULT_LINE_WINDOW,
) -> CanaryPartition:
    """Split findings into real findings and canary findings.

    A finding is a canary finding when it cites the same file within
    +/- line_window lines of any canary. Real findings keep their original
    order and proceed to the verdict; canary findings are dropped so the
    planted defects never reach the user. An empty manifest routes every
    finding to real (nothing to match against).
    """
    real: list[Mapping[str, object]] = []
    canary: list[Mapping[str, object]] = []
    for finding in findings:
        if any(_matches(finding, c, line_window) for c in manifest):
            canary.append(finding)
        else:
            real.append(finding)
    return CanaryPartition(real=tuple(real), canary=tuple(canary))
