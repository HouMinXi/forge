# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Abstract Falsifier interface + Stub implementation.

Phase 4 will provide the real Falsifier. 02-01 stub allows state machine
to be built and tested without Phase 4.

This module imports StateFinding from forge.state (NOT the Phase 1
forge.parsers.base.Finding). StateFinding is the persisted-in-state.json
representation; parsers.base.Finding is the parser-emitted record.
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .disposition import Disposition
from .state import StateFinding


class Falsifier(ABC):
    """Abstract base for falsification engines.

    falsify() must NOT return Disposition.FIXED (FIXED is a state machine
    transition after auto-fix, not a falsifier output).
    """

    @abstractmethod
    def falsify(self, finding: StateFinding) -> Disposition:
        """Classify a finding as CONFIRMED, DISMISSED, or UNCERTAIN.

        Returning FIXED raises ValueError (state machine concern).
        """
        ...


class StubFalsifier(Falsifier):
    """Returns configurable Dispositions for tests.

    Config format (JSON):
      {
        "default": "CONFIRMED",
        "dispositions": {"fp-xxx": "DISMISSED", ...},
        "errors":       {"fp-yyy": "timeout", ...}
      }

    Precedence: errors checked before dispositions/default.
    FIXED in any disposition position is rejected at constructor AND
    at falsify() time (defense in depth).
    """

    def __init__(self, fixture_path: Optional[Path] = None):
        self._dispositions: dict[str, Disposition] = {}
        self._errors: dict[str, str] = {}
        self._default = Disposition.CONFIRMED
        if fixture_path:
            data = json.loads(fixture_path.read_text())
            self._default = Disposition(data.get("default", "CONFIRMED"))
            if self._default == Disposition.FIXED:
                raise ValueError(
                    "FIXED is not a valid falsifier output (default)"
                )
            self._dispositions = {}
            for fp, d in data.get("dispositions", {}).items():
                disp = Disposition(d)
                if disp == Disposition.FIXED:
                    raise ValueError(
                        "FIXED is not a valid falsifier output "
                        "(fingerprint %s)" % fp
                    )
                self._dispositions[fp] = disp
            self._errors = dict(data.get("errors", {}))

    def falsify(self, finding: StateFinding) -> Disposition:
        """Return configured disposition or raise on error key."""
        if finding.fingerprint in self._errors:
            raise RuntimeError(
                "stub-simulated falsification error: %s"
                % self._errors[finding.fingerprint]
            )
        disp = self._dispositions.get(finding.fingerprint, self._default)
        if disp == Disposition.FIXED:
            raise ValueError("FIXED is not a valid falsifier output")
        return disp
