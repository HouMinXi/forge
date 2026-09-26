"""Aggregate decision from adapter results. reconcile never builds a result."""

import pytest

from code_forge.mutation_engines.schemas import (
    ArtifactReference,
    AggregateDecision,
    Generation,
    BaselineRecord,
    BaselineState,
    Cleanup,
    CleanupState,
    Inventory,
    NormalizedStatus,
    Outcome,
    RunIdentity,
    RunState,
    TargetResult,
)

from code_forge.mutation_engines.reconcile import decide, _fails, exit_code


def _result(statuses, baseline=BaselineState.PASSED, state=RunState.COMPLETE):
    outcomes = tuple(
        Outcome(
            mutant_id="m%d" % index,
            source_path="src/a.py",
            source_digest="d" * 64,
            location="1:1",
            operator="op",
            native_status=status.value,
            normalized_status=status,
            test_evidence=(),
            native_evidence=(),
        )
        for index, status in enumerate(statuses)
    )
    return TargetResult(
        identity=RunIdentity(
            run_id="run1", reviewed_source_id="rev", input_manifest_digest="m" * 64,
            selection_digest="s" * 64, config_digest="c" * 64,
            execution_policy_digest="e" * 64, toolchain_fingerprint="py",
            target_id="py", adapter_id="python-mutmut", adapter_version="1", tool_version="3.8.0",
        ),
        target_id="py",
        adapter_id="python-mutmut",
        adapter_version="1",
        tool_version="3.8.0",
        run_state=state,
        reason_code="complete",
        baseline=BaselineRecord(state=baseline, test_count=1, command_receipt=None, native_evidence=()),
        generation=Generation(
            inventory_artifact=ArtifactReference(relative_run_path="inv.json", digest="a" * 64, bytes=1),
            completion_evidence=ArtifactReference(relative_run_path="done.json", digest="b" * 64, bytes=1),
            extractor_version="1",
        ),
        inventory=Inventory(generated=len(outcomes), selected=len(outcomes), excluded=0, completed=len(outcomes), manifest=()),
        outcomes=outcomes,
        native_artifacts=(),
        command_receipts=(),
        infrastructure_errors=(),
        cleanup=Cleanup(state=CleanupState.COMPLETE, owned_group_empty=True, owned_mounts_removed=True, errors=()),
    )


def test_all_killed_is_pass():
    assert decide((_result([NormalizedStatus.KILLED, NormalizedStatus.KILLED]),)) is AggregateDecision.PASS


def test_a_survivor_is_fail():
    assert decide((_result([NormalizedStatus.KILLED, NormalizedStatus.SURVIVED]),)) is AggregateDecision.FAIL


def test_a_baseline_that_did_not_pass_is_hold():
    result = _result([NormalizedStatus.KILLED], baseline=BaselineState.FAILED)
    assert decide((result,)) is AggregateDecision.HOLD


def test_no_coverage_is_fail():
    assert decide((_result([NormalizedStatus.NO_COVERAGE]),)) is AggregateDecision.FAIL


def test_unknown_status_is_hold():
    assert decide((_result([NormalizedStatus.UNKNOWN]),)) is AggregateDecision.HOLD


def test_timeout_is_hold():
    assert decide((_result([NormalizedStatus.TIMED_OUT]),)) is AggregateDecision.HOLD


def test_empty_inventory_is_hold():
    result = _result([])
    assert decide((result,)) is AggregateDecision.HOLD


def test_incomplete_run_is_hold():
    result = _result([NormalizedStatus.KILLED], state=RunState.INCOMPLETE)
    assert decide((result,)) is AggregateDecision.HOLD


def test_no_results_is_not_applicable():
    assert decide(()) is AggregateDecision.NOT_APPLICABLE


def test_a_survivor_still_counts_when_another_target_holds():
    """A validated survivor resets clean rounds even when another target holds."""
    held = _result([NormalizedStatus.KILLED], state=RunState.INCOMPLETE)
    failed = _result([NormalizedStatus.SURVIVED])
    assert decide((held, failed)) is AggregateDecision.HOLD
    assert _fails(failed)


def test_exit_code_follows_the_decision():
    assert exit_code(AggregateDecision.PASS) == 0
    assert exit_code(AggregateDecision.NOT_APPLICABLE) == 0
    assert exit_code(AggregateDecision.FAIL) == 1
    assert exit_code(AggregateDecision.HOLD) == 7


def test_exit_code_rejects_an_unknown_decision():
    with pytest.raises(ValueError):
        exit_code("maybe")
