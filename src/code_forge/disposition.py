# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Disposition protocol for forge state machine.

Owned by Phase 2 sub-plan 02-01. All other sub-plans + Phase 4 must conform.
DO NOT add fields without bumping DISPOSITION_PROTOCOL_VERSION.
"""

from enum import Enum
from typing import Final

DISPOSITION_PROTOCOL_VERSION: Final[int] = 1
MAX_FIX_ATTEMPTS_PER_FINGERPRINT: Final[int] = 3
FEEDBACK_SCHEMA_VERSION: Final[int] = 1


class Disposition(str, Enum):
    """Finding disposition states.

    State transitions (enforced by state machine, not by enum):
    - (new) -> CONFIRMED | DISMISSED | UNCERTAIN  (set by falsify())
    - CONFIRMED -> FIXED                          (after successful auto-fix)
    - CONFIRMED -> UNCERTAIN                      (DISPO-05 promotion)
    - FIXED -> (remove from active)               (next-round gone)
    - FIXED -> CONFIRMED                          (next-round persists)
    - UNCERTAIN -> CONFIRMED                      (human re-CONFIRM in HOLD)
    - UNCERTAIN -> DISMISSED                      (human dismiss in HOLD)
    - STYLE: a style/test-assertion/naming/idiomatic finding downgraded
      to non-blocking (never blocks the verdict), stays ledger-written/
      adjudicable/exportable.
    """
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    UNCERTAIN = "UNCERTAIN"
    FIXED = "FIXED"
    STYLE = "STYLE"
