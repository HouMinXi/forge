"""Advisory finding type and AxisRunner Protocol.

TWO FOUNDING PRINCIPLES:

1. Advisory findings NEVER participate in convergence, NEVER block commits.
   AdvisoryFinding is a structurally separate type from StateFinding -- no
   shared base class, no fingerprint, no disposition, no source field.
   machine.py maintains self.advisories: list[AdvisoryFinding] independently
   of self.findings: list[StateFinding]. The convergence logic in
   _fixpoint_reached() operates ONLY on the StateFinding list.

2. AxisRunner.run() intentionally receives ONLY (diff_text, repo_root): no
   prior findings, no other axes' output, no review state. This is the
   anti-anchoring invariant underpinning D-11's multi-run majority. Each run
   sees the diff fresh, forming independent judgments. Do not widen this
   signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AdvisoryFinding:
    """A single advisory finding from a review axis.

    Advisory findings are informational: they surface risks, concerns, and
    observations but NEVER block the review verdict or reset the cycle
    counter. They are a completely separate type from StateFinding.

    Fields intentionally excluded (structural incompatibility):
    - fingerprint: advisory findings are not deduplicated against blocking
    - disposition: advisory findings have no CONFIRMED/FIXED/DISMISSED state
    - source: advisory findings are attributed by axis, not by L0/L1/L2 tier
    """

    id: str
    axis: str
    file: str
    line_range: list[int]
    description: str
    attribution: str


class AxisRunner(Protocol):
    """Protocol for review axes (blocking or advisory).

    machine.py dispatches to runners implementing this protocol.
    Each axis is a separate module providing its own runner.

    The run() signature is intentionally narrow: only diff_text and
    repo_root. No prior findings, no review state, no other axes' output.
    This prevents anchoring bias when running majority-vote evaluations
    (D-11).
    """

    @property
    def is_advisory(self) -> bool:
        """True if this axis produces advisory (non-blocking) findings."""
        ...

    def run(
        self,
        diff_text: str,
        repo_root: Path,
    ) -> list[AdvisoryFinding]:
        """Run the axis on the given diff and return findings.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of findings from this axis.
        """
        ...
