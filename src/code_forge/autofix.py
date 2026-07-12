# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""AutoFixer abstract interface + Stub implementation.

Parallel to forge.falsify -- Phase 4 will plug in real AI auto-fix; 02-02
ships stub for DISPO-04/05/06 behavior tests.

Design per R1 B2: AutoFixer is pure (returns FixOutcome only, no I/O,
no revert). StateMachine.revert_fn handles the revert side-effect.
"""
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Optional

import json

from .state import StateFinding


class FixOutcome(str, Enum):
    """Auto-fix result.

    State machine consumes to drive DISPO-04/05/06 transitions:
      SUCCESS    - fix applied, no parse errors; finding -> FIXED
      PARSE_FAIL - fix produced syntax error; revert + fix_attempts++
      NO_CHANGE  - autofixer refused; fix_attempts++ (no revert needed)
      EXCEPTION  - autofixer raised; infra_errors + fix_attempts++
    """
    SUCCESS = "SUCCESS"
    PARSE_FAIL = "PARSE_FAIL"
    NO_CHANGE = "NO_CHANGE"
    EXCEPTION = "EXCEPTION"


class AutoFixer(ABC):
    """Abstract base for auto-fix engines."""

    @abstractmethod
    def fix(self, finding: StateFinding, mode_hint: str) -> FixOutcome:
        """Attempt to fix a CONFIRMED finding.

        mode_hint: "git" | "non-git" -- passed through; consumed by the
        state machine's revert_fn (NOT by AutoFixer) per R1 B2 design:
          - AutoFixer is pure (returns FixOutcome, no I/O, no revert).
          - StateMachine.revert_fn handles the revert side-effect:
            * mode_hint=="git" -> git restore
            * mode_hint=="non-git" -> snapshot restore via 02-03 Snapshot

        MUST NOT mutate finding. Outcome only; caller drives transitions.
        """
        ...


class StubAutoFixer(AutoFixer):
    """Returns configured FixOutcomes for tests.

    Config format (JSON):
      {
        "default": "SUCCESS",
        "outcomes": {"fp-xxx": "PARSE_FAIL", "fp-yyy": "SUCCESS", ...}
      }
    """

    def __init__(self, fixture_path: Optional[Path] = None):
        self._outcomes: dict[str, FixOutcome] = {}
        self._default = FixOutcome.SUCCESS
        if fixture_path:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            self._default = FixOutcome(data.get("default", "SUCCESS"))
            self._outcomes = {
                fp: FixOutcome(o)
                for fp, o in data.get("outcomes", {}).items()
            }

    def fix(self, finding: StateFinding, mode_hint: str) -> FixOutcome:
        """Return configured outcome for this finding's fingerprint."""
        return self._outcomes.get(finding.fingerprint, self._default)
