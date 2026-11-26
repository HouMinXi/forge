"""Offline accounting contract for one preregistered paired smoke.

This module neither opens a connection nor authorizes spending. A future
runner must prevent redirects and provider-side fallback separately and call
the gate at a boundary that observes every physical request.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .experiment import ARMS
from .experiment_adapter import PASSES
from .fixtures import InputError, digest, identifier, require, sha

_PASSES = tuple(name for name, _ in PASSES)
_MAX_BODY_BYTES = 4 * 1024 * 1024
_MAX_GROUPS = 2


@dataclass(frozen=True, slots=True)
class AttemptSlot:
    arm: str
    group: str
    pass_name: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class AttemptRow:
    slot: AttemptSlot
    state: Literal["not_started", "unknown_outcome", "completed", "failed"]


class PairedAttemptGate:
    """Serial, single-use budget for the pinned wire bodies of a smoke pair.

    Reserve before handing a body to the network boundary, not after receiving
    a reply. Interrupted or failed attempts cannot be retried under this plan.
    This is an in-memory preflight component, not a resumable live runner.
    """

    def __init__(
        self,
        groups: Iterable[str],
        slots: Iterable[AttemptSlot],
        *,
        arm_order: tuple[str, str],
        max_body_bytes: int,
        attempt_ceiling: int,
    ):
        require(
            type(arm_order) is tuple
            and len(arm_order) == 2
            and all(a in ARMS for a in arm_order)
            and arm_order[0] != arm_order[1],
            "attempt_plan.arm_order",
        )
        require(
            type(max_body_bytes) is int and 1 <= max_body_bytes <= _MAX_BODY_BYTES,
            "attempt_plan.max_body_bytes",
        )
        frozen_groups = []
        for group in groups:
            require(
                len(frozen_groups) < _MAX_GROUPS and identifier(group) and group not in frozen_groups,
                "attempt_plan.groups",
            )
            frozen_groups.append(group)
        require(bool(frozen_groups), "attempt_plan.groups")
        expected = tuple(
            (arm, group, name) for arm in arm_order for group in frozen_groups for name in _PASSES
        )
        require(
            type(attempt_ceiling) is int and attempt_ceiling == len(expected),
            "attempt_plan.attempt_ceiling",
        )
        pinned = []
        for slot in slots:
            idx = len(pinned)
            require(
                idx < len(expected)
                and isinstance(slot, AttemptSlot)
                and (slot.arm, slot.group, slot.pass_name) == expected[idx]
                and sha(slot.request_sha256),
                "attempt_plan.slots",
            )
            pinned.append(slot)
        require(len(pinned) == len(expected), "attempt_plan.slots")
        self._slots = tuple(pinned)
        self._max_body_bytes = max_body_bytes
        self._states = []
        self._pending = None

    def reserve(self, arm: str, group: str, pass_name: str, request_body: bytes) -> int:
        """Admit exactly one slot, returning the 1-based physical attempt number."""
        field = "attempt_plan"
        if self._pending is not None:
            raise InputError("attempt-in-flight", field)
        idx = len(self._states)
        if idx >= len(self._slots):
            raise InputError("attempt-ceiling-reached", field)
        slot = self._slots[idx]
        if (arm, group, pass_name) != (slot.arm, slot.group, slot.pass_name):
            raise InputError("attempt-slot-drift", field)
        if type(request_body) is not bytes or not 0 < len(request_body) <= self._max_body_bytes:
            raise InputError("attempt-body-over-budget", field)
        if digest(request_body) != slot.request_sha256:
            raise InputError("attempt-body-drift", field)
        self._pending = idx + 1
        self._states.append("unknown_outcome")
        return self._pending

    def complete(self, ticket: int, *, success: bool) -> None:
        """Close the sole in-flight row; even failure consumes its attempt."""
        if self._pending is None or type(ticket) is not int or ticket != self._pending:
            raise InputError("attempt-ticket-mismatch", "attempt_plan")
        require(type(success) is bool, "attempt_plan.success")
        self._states[ticket - 1] = "completed" if success else "failed"
        self._pending = None

    @property
    def rows(self) -> tuple[AttemptRow, ...]:
        return tuple(
            AttemptRow(slot, self._states[i] if i < len(self._states) else "not_started")
            for i, slot in enumerate(self._slots)
        )
