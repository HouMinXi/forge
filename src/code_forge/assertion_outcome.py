# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Host-owned execution metadata for optional assertion advice."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssertionStatus(str, Enum):
    COMPLETED_EMPTY = "completed_empty"
    COMPLETED_FINDINGS = "completed_findings"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    SKIPPED = "skipped"


class AssertionStage(str, Enum):
    ELIGIBILITY = "eligibility"
    PREPARATION = "preparation"
    INVOCATION = "invocation"
    VALIDATION = "validation"
    CONVERSION = "conversion"


class AssertionKind(str, Enum):
    NO_TEST_FILES = "no_test_files"
    NO_GIT_DIFF = "no_git_diff"
    TIMEOUT = "timeout"
    ERROR = "error"


_LEGACY_OUTCOMES = {
    AssertionStatus.COMPLETED_EMPTY: "done",
    AssertionStatus.COMPLETED_FINDINGS: "done",
    AssertionStatus.NOT_APPLICABLE: "skipped: no test files",
    AssertionStatus.SKIPPED: "skipped: no git diff",
    AssertionStatus.FAILED: "failed: error",
}
_TIMEOUT_OUTCOME = "failed: timeout"


def legacy_progress_outcome(value: object) -> str:
    """Legacy text is progress only; it cannot establish a typed result."""
    if type(value) is str and value in (*_LEGACY_OUTCOMES.values(), _TIMEOUT_OUTCOME):
        return value
    return "unreported"


@dataclass(frozen=True)
class AssertionOutcome:
    status: AssertionStatus
    finding_count: int | None
    stage: AssertionStage | None = None
    kind: AssertionKind | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not AssertionStatus:
            raise TypeError("assertion status must be an AssertionStatus")
        if self.finding_count is not None and type(self.finding_count) is not int:
            raise TypeError("assertion finding count must be an integer or None")
        if self.stage is not None and type(self.stage) is not AssertionStage:
            raise TypeError("assertion stage must be an AssertionStage or None")
        if self.kind is not None and type(self.kind) is not AssertionKind:
            raise TypeError("assertion kind must be an AssertionKind or None")
        if self.status in (AssertionStatus.COMPLETED_EMPTY, AssertionStatus.COMPLETED_FINDINGS):
            valid_count = (
                self.finding_count == 0 if self.status is AssertionStatus.COMPLETED_EMPTY
                else self.finding_count is not None and self.finding_count > 0
            )
            valid = valid_count and self.stage is None and self.kind is None
        elif self.status is AssertionStatus.NOT_APPLICABLE:
            valid = (
                self.finding_count == 0 and self.stage is AssertionStage.ELIGIBILITY
                and self.kind is AssertionKind.NO_TEST_FILES
            )
        elif self.status is AssertionStatus.SKIPPED:
            valid = (
                self.finding_count is None and self.stage is AssertionStage.ELIGIBILITY
                and self.kind is AssertionKind.NO_GIT_DIFF
            )
        else:
            valid = self.finding_count is None and (self.stage, self.kind) in {
                (AssertionStage.PREPARATION, AssertionKind.ERROR),
                (AssertionStage.INVOCATION, AssertionKind.ERROR),
                (AssertionStage.INVOCATION, AssertionKind.TIMEOUT),
                (AssertionStage.VALIDATION, AssertionKind.ERROR),
                (AssertionStage.CONVERSION, AssertionKind.ERROR),
            }
        if not valid:
            raise ValueError("inconsistent assertion outcome")

    def to_metadata(self) -> dict[str, object]:
        if type(self) is not AssertionOutcome:
            raise TypeError("assertion metadata requires an AssertionOutcome")
        AssertionOutcome.__post_init__(self)
        metadata: dict[str, object] = {
            "status": self.status.value,
            "finding_count": self.finding_count,
        }
        if self.stage is not None:
            metadata["stage"] = self.stage.value
            metadata["kind"] = self.kind.value
        return metadata

    def legacy_outcome(self) -> str:
        """Keep established CLI callbacks separate from structured metadata."""
        if self.kind is AssertionKind.TIMEOUT:
            return _TIMEOUT_OUTCOME
        return _LEGACY_OUTCOMES[self.status]
