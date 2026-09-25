"""Mapping rules for the gremlins adapter. No sandbox, no go."""

from code_forge.mutation_engines.adapters.go_gremlins import (
    baseline_from_go_json,
    killed_has_failure,
    map_gremlins_status,
    reconcile,
    _failure_for_mutant,
)
from code_forge.mutation_engines.schemas import BaselineState, NormalizedStatus


def test_status_mapping():
    assert map_gremlins_status("KILLED") is NormalizedStatus.KILLED
    assert map_gremlins_status("LIVED") is NormalizedStatus.SURVIVED
    assert map_gremlins_status("TIMED_OUT") is NormalizedStatus.TIMED_OUT
    assert map_gremlins_status("NOT_COVERED") is NormalizedStatus.NO_COVERAGE
    assert map_gremlins_status("COMPILE_ERROR") is NormalizedStatus.UNKNOWN


def test_killed_needs_recorded_test_failure():
    assert killed_has_failure(
        {"argv": ["test"], "returncode": 1, "stdout": "--- FAIL: TestBoundary\n"}
    )
    assert not killed_has_failure(
        {"argv": ["test"], "returncode": 1, "stdout": "build failed\n"}
    )
    assert not killed_has_failure(
        {"argv": ["test"], "returncode": 0, "stdout": "--- FAIL: TestBoundary\n"}
    )
    assert not killed_has_failure(None)


def test_go_json_baseline():
    passed = '\n'.join([
        '{"Action":"run","Test":"TestBoundary"}',
        '{"Action":"pass","Test":"TestBoundary"}',
        '{"Action":"pass"}',
    ])
    state, count = baseline_from_go_json(passed)
    assert state is BaselineState.PASSED
    assert count == 1
    failed = '{"Action":"fail","Test":"TestBoundary"}\n'
    assert baseline_from_go_json(failed)[0] is BaselineState.FAILED
    assert baseline_from_go_json("")[0] is BaselineState.UNKNOWN
    assert baseline_from_go_json('{"Action":"pass"}\n')[0] is BaselineState.EMPTY


def test_inventory_identifier_missing_is_hold():
    inventory = {"files": [{"file_name": "probe.go", "mutations": [{"type": "A", "line": 3, "column": 10}, {"type": "B", "line": 3, "column": 20}]}]}
    outcomes = {"files": [{"file_name": "probe.go", "mutations": [{"type": "A", "line": 3, "column": 10}]}]}
    ok, reason = reconcile(inventory, outcomes)
    assert not ok
    assert "missing" in reason
    complete = {"files": [{"file_name": "probe.go", "mutations": [{"type": "A", "line": 3, "column": 10}, {"type": "B", "line": 3, "column": 20}]}]}
    assert reconcile(inventory, complete)[0] is True
    assert reconcile(None, complete)[0] is False


def test_failure_matches_only_its_own_edit():
    """A failed test of another mutant must not support this one."""
    original = "func Allows(n int) bool { return n >= 0 }\n"
    records = [
        {
            "returncode": 1,
            "stdout": "--- FAIL: TestBoundary\n",
            "sources": {"probe.go": "func Allows(n int) bool { return n > 0 }\n"},
        }
    ]
    assert _failure_for_mutant(records, original, 1, 36) is not None
    assert _failure_for_mutant(records, original, 2, 36) is None
