"""Turn adapter results into one decision.

Reads what an adapter already returned. It validates a TargetResult and
never constructs one. Hold beats fail, fail beats pass. A survivor in
any target is a test-quality failure even when another target holds.
"""

from code_forge.mutation_engines.hostconfig import HostUnavailable
from code_forge.mutation_engines.schemas import (
    AggregateDecision,
    Budget,
    TargetDeclaration,
    BaselineState,
    CleanupState,
    NormalizedStatus,
    RunState,
    TargetResult,
)

_HOLD_STATUSES = frozenset({
    NormalizedStatus.UNKNOWN,
    NormalizedStatus.TIMED_OUT,
    NormalizedStatus.RUNTIME_ERROR,
    NormalizedStatus.IGNORED,
    NormalizedStatus.PENDING,
})


def decide(results: tuple[TargetResult, ...]) -> AggregateDecision:
    """Aggregate precedence: hold, then fail, then pass."""
    if not results:
        return AggregateDecision.NOT_APPLICABLE
    hold = False
    fail = False
    for result in results:
        if _holds(result):
            hold = True
        if _fails(result):
            fail = True
    if hold:
        return AggregateDecision.HOLD
    if fail:
        return AggregateDecision.FAIL
    return AggregateDecision.PASS


def _holds(result: TargetResult) -> bool:
    if result.run_state is not RunState.COMPLETE:
        return True
    if result.baseline.state is not BaselineState.PASSED or result.baseline.test_count <= 0:
        return True
    if result.inventory.generated <= 0 or result.inventory.completed < result.inventory.selected:
        return True
    if result.cleanup.state is not CleanupState.COMPLETE:
        return True
    if result.infrastructure_errors:
        return True
    return any(item.normalized_status in _HOLD_STATUSES for item in result.outcomes)


def _fails(result: TargetResult) -> bool:
    return any(
        item.normalized_status in (NormalizedStatus.SURVIVED, NormalizedStatus.NO_COVERAGE)
        for item in result.outcomes
    )


_EXIT = {
    AggregateDecision.PASS: 0,
    AggregateDecision.NOT_APPLICABLE: 0,
    AggregateDecision.FAIL: 1,
    AggregateDecision.HOLD: 7,
}


def exit_code(decision: AggregateDecision) -> int:
    """Pass and not-applicable exit 0, fail exits 1, hold exits 7.

    Invalid input exits 2, an active run exits 5 and contention exits 3.
    Those three are raised by the caller before a decision exists.
    """
    try:
        return _EXIT[decision]
    except KeyError:
        raise ValueError("no exit code for decision %r" % (decision,)) from None


def undeclared_target() -> TargetDeclaration:
    """A project with no approval record is measured as Python."""
    return TargetDeclaration(
        id="undeclared",
        adapter="python-mutmut",
        root=".",
        sources=("src",),
        tests=("tests",),
        inputs=(),
        oracle="",
        command=("python3", "-m", "pytest"),
        execution_profile="",
        environment="",
        budget=Budget(1, 1, 1, 1, 1, 1, 1, 1),
    )


def undeclared_decision(reason: HostUnavailable) -> AggregateDecision:
    """No approval means no measurement. That is a hold, not a pass."""
    del reason
    return AggregateDecision.HOLD
