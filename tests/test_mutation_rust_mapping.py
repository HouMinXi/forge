"""Mapping rules for the cargo-mutants adapter. No sandbox, no cargo."""

from code_forge.mutation_engines.adapters.rust_cargo_mutants import (
    baseline_from_cargo,
    killed_has_failure,
    map_cargo_status,
    reconcile,
)
from code_forge.mutation_engines.schemas import BaselineState, NormalizedStatus


def test_status_mapping():
    assert map_cargo_status("CaughtMutant") is NormalizedStatus.KILLED
    assert map_cargo_status("MissedMutant") is NormalizedStatus.SURVIVED
    assert map_cargo_status("Timeout") is NormalizedStatus.TIMED_OUT
    assert map_cargo_status("Unviable") is NormalizedStatus.NONVIABLE
    assert map_cargo_status("other") is NormalizedStatus.UNKNOWN


def test_killed_needs_a_failed_test_in_its_own_log():
    assert killed_has_failure("test tests::boundary ... FAILED\n")
    assert killed_has_failure("thread 'main' panicked at src/lib.rs:1:1:\n")
    assert not killed_has_failure("test result: ok. 1 passed; 0 failed;\n")
    assert not killed_has_failure(None)
    assert not killed_has_failure("")


def test_cargo_baseline_counts_passed_tests():
    text = "running 1 test\ntest tests::boundary ... ok\n\ntest result: ok. 1 passed; 0 failed; 0 ignored;\n"
    state, count = baseline_from_cargo(text, 0)
    assert state is BaselineState.PASSED
    assert count == 1
    failed = "test result: FAILED. 0 passed; 1 failed; 0 ignored;\n"
    assert baseline_from_cargo(failed, 1)[0] is BaselineState.FAILED
    empty = "test result: ok. 0 passed; 0 failed; 0 ignored;\n"
    assert baseline_from_cargo(empty, 0)[0] is BaselineState.EMPTY
    assert baseline_from_cargo(None, 0)[0] is BaselineState.UNKNOWN


def test_mutant_list_must_match_outcomes():
    mutants = [{"name": "a"}, {"name": "b"}]
    outcomes = {
        "total_mutants": 2,
        "outcomes": [
            {"scenario": "Baseline", "summary": "Success"},
            {"scenario": {"Mutant": {}}, "summary": "CaughtMutant"},
            {"scenario": {"Mutant": {}}, "summary": "MissedMutant"},
        ],
    }
    assert reconcile(mutants, outcomes)[0]
    short = dict(outcomes)
    short["outcomes"] = outcomes["outcomes"][:2]
    ok, reason = reconcile(mutants, short)
    assert not ok
    assert "2" in reason and "1" in reason
    assert reconcile(None, outcomes)[0] is False
