"""Advisory finding type and AdvisoryAxisRunner Protocol.

TWO FOUNDING PRINCIPLES:

1. Advisory findings NEVER participate in convergence, NEVER block commits.
   AdvisoryFinding is a structurally separate type from StateFinding -- no
   shared base class, no fingerprint, no disposition, no source field.
   machine.py maintains self.advisories: list[AdvisoryFinding] independently
   of self.findings: list[StateFinding]. The convergence logic in
   _fixpoint_reached() operates ONLY on the StateFinding list.

2. AdvisoryAxisRunner.run() intentionally receives ONLY (diff_text, repo_root):
   no prior findings, no other axes' output, no review state. This is the
   anti-anchoring invariant underpinning the multi-run majority vote. Each run
   sees the diff fresh, forming independent judgments. Do not widen this
   signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


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
    line_range: tuple[int, int]
    description: str
    attribution: str

    def __init__(
        self,
        id: str,
        axis: str,
        file: str,
        line_range: Sequence[int] | tuple[int, int] | list[int] = (0, 0),
        description: str = "",
        attribution: str = "",
    ) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "axis", axis)
        object.__setattr__(self, "file", file)

        # Normalize line_range to immutable tuple[int, int]
        if not line_range:
            norm_range = (0, 0)
        elif len(line_range) == 1:
            norm_range = (int(line_range[0]), int(line_range[0]))
        else:
            norm_range = (int(line_range[0]), int(line_range[1]))
        object.__setattr__(self, "line_range", norm_range)

        object.__setattr__(self, "description", description)
        object.__setattr__(self, "attribution", attribution)


class AdvisoryAxisRunner(Protocol):
    """Protocol for advisory review axes.

    machine.py dispatches post-convergence advisory axes implementing this protocol.
    Each advisory axis is a separate module providing its own runner.

    The run() signature is intentionally narrow: only diff_text and
    repo_root. No prior findings, no review state, no other axes' output.
    This prevents anchoring bias when running majority-vote evaluations.
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
        """Run the advisory axis on the given diff and return advisory findings.

        Args:
            diff_text: unified diff of the changes under review.
            repo_root: path to the repository root.

        Returns:
            List of advisory findings from this axis.
        """
        ...


# Backwards compatibility alias for existing callers/imports
AxisRunner = AdvisoryAxisRunner
