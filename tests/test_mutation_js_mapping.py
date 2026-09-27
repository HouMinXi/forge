"""Mapping rules for the Stryker adapter. No sandbox, no node."""

from code_forge.mutation_engines.adapters.js_stryker import (
    baseline_from_vitest,
    killed_has_test,
    map_stryker_status,
    reconcile,
)
from code_forge.mutation_engines.schemas import BaselineState, NormalizedStatus


def test_native_status_map():
    assert map_stryker_status("Killed") is NormalizedStatus.KILLED
    assert map_stryker_status("Survived") is NormalizedStatus.SURVIVED
    assert map_stryker_status("Timeout") is NormalizedStatus.TIMED_OUT
    assert map_stryker_status("NoCoverage") is NormalizedStatus.NO_COVERAGE
    assert map_stryker_status("CompileError") is NormalizedStatus.UNKNOWN


def test_killed_requires_a_named_test():
    assert killed_has_test({"killedBy": ["probe.test.js#boundary"]})
    assert not killed_has_test({"killedBy": []})
    assert not killed_has_test(None)
    assert not killed_has_test({"status": "Killed"})


def test_vitest_baseline_needs_executed_passes():
    passed = {"success": True, "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0}
    state, count = baseline_from_vitest(passed)
    assert state is BaselineState.PASSED
    assert count == 1
    state, count = baseline_from_vitest(
        {"success": False, "numTotalTests": 1, "numPassedTests": 0, "numFailedTests": 1}
    )
    assert state is BaselineState.FAILED
    state, count = baseline_from_vitest(
        {"success": True, "numTotalTests": 0, "numPassedTests": 0, "numFailedTests": 0}
    )
    assert state is BaselineState.EMPTY
    assert baseline_from_vitest(None)[0] is BaselineState.UNKNOWN


def test_plan_count_must_match_report():
    plan = {"mutantPlans": [{"mutant": {"id": "0"}}, {"mutant": {"id": "1"}}]}
    report = {"files": {"src/probe.js": {"mutants": [{"id": "0"}, {"id": "1"}]}}}
    ok, _reason = reconcile(plan, report, 1)
    assert ok
    short = {"files": {"src/probe.js": {"mutants": [{"id": "0"}]}}}
    ok, reason = reconcile(plan, short, 1)
    assert not ok
    assert "2" in reason and "1" in reason
    assert reconcile(None, report, 1)[0] is False
    assert reconcile(plan, report, 0)[0] is False


def test_mutant_keeps_its_own_file():
    """A mutant from the second file must not be recorded under the first."""
    from code_forge.mutation_engines.adapters.js_stryker import mutants_by_file

    report = {
        "files": {
            "src/a.js": {"mutants": [{"id": "0", "status": "Killed"}]},
            "src/b.js": {"mutants": [{"id": "1", "status": "Survived"}]},
        }
    }
    paired = mutants_by_file(report)
    assert paired == [
        ("src/a.js", {"id": "0", "status": "Killed"}),
        ("src/b.js", {"id": "1", "status": "Survived"}),
    ]
