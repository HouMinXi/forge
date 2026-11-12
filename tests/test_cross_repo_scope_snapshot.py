"""Native query-scope snapshot semantics; persistence is tested separately."""

from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import sqlite3
import pytest
from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_capture as capture
from tests import test_cross_repo_immediate_diagnostic as immediate
from tests import test_cross_repo_query_summary as summary

DIFF = immediate.DIFF
graphs = immediate.graphs


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("case", ["zero", "callers", "missing", "corrupt", "mixed", "duplicates"])
def test_frozen_native_scope_and_cache(graphs, monkeypatch, bound, case):
    primary, sibling, registry, _ = graphs
    summary.named(primary)
    if case in {"callers", "duplicates"}:
        capture.add_named_target_and_caller(primary, sibling)
    if case == "missing":
        (sibling / ".code-review-graph/graph.db").unlink()
    if case == "corrupt":
        (sibling / ".code-review-graph/graph.db").write_bytes(b"private broken database")
    entries = [{"path": str(primary)}, {"path": str(sibling), "alias": "private-alias"}]
    if case == "mixed":
        entries.append({"path": str(primary.parent / "private-missing")})
    if case == "duplicates":
        entries += [entries[1].copy()] * 11
    registry.write_text(json.dumps({"repos": entries}))
    statements = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    expected = {
        "zero": (1, 1, 1, 0, 0),
        "callers": (1, 1, 1, 0, 1),
        "missing": (1, 0, 0, 1, 0),
        "corrupt": (1, 1, 0, 1, 0),
        "mixed": (2, 1, 1, 1, 0),
        "duplicates": (12, 12, 12, 0, 12),
    }[case]
    owner = capture.capture(primary) if bound else None
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(DIFF, primary)
    facts = runner._scope_snapshot
    assert asdict(facts) == dict(
        scope="established",
        registered=expected[0],
        attempted=expected[1],
        completed=expected[2],
        unavailable=expected[3],
        with_callers=expected[4],
        query_occurrences_accounted=True,
        processing_completed=True,
    )
    assert messages == [summary.SUMMARY % (expected[2], expected[0], expected[1], expected[3])]
    assert len(findings) == (10 if case == "duplicates" else int(case == "callers"))
    assert len(runner.infra_errors) == expected[3]
    before = len(statements)
    assert runner.run(DIFF, primary) == findings and runner._scope_snapshot is facts
    assert len(statements) == before and len(messages) == 1
    if owner:
        assert len(owner.records) == 1 and owner._attempts == 1
        assert owner.records[0].observation.context.observation_sequence == 1
    with pytest.raises(FrozenInstanceError):
        facts.completed = 999
    serialized = json.dumps(asdict(facts))
    for secret in (str(primary), str(sibling), "private-alias", "private broken database", "api"):
        assert secret not in serialized


@pytest.mark.parametrize("case", ["blank", "unnamed", "empty", "missing_registry", "malformed"])
def test_unknown_empty_and_not_reached_differ(graphs, case):
    primary, _, registry, _ = graphs
    if case not in {"blank", "unnamed"}:
        summary.named(primary)
    if case == "empty":
        registry.write_text('{"repos": []}')
    if case == "missing_registry":
        registry.unlink()
    if case == "malformed":
        registry.write_text("{bad")
    runner = impact.CrossRepoImpactRunner()
    assert runner.run("" if case == "blank" else DIFF, primary) == []
    facts = runner._scope_snapshot
    assert facts.processing_completed
    if case in {"empty", "missing_registry"}:
        assert (
            facts.scope == "established" and facts.registered == 0 and facts.query_occurrences_accounted
        )
    else:
        assert facts.scope == ("unavailable" if case == "malformed" else "not_reached")
        assert facts.registered is facts.attempted is facts.completed is facts.unavailable is None
        assert not facts.query_occurrences_accounted


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("occurrences", [1, 2])
@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
def test_helper_return_remains_completed_when_later_processing_raises(
    graphs, monkeypatch, bound, occurrences, kind
):
    primary, sibling, registry, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    registry.write_text(json.dumps({"repos": [{"path": str(sibling)}] * occurrences}))
    original = impact._find_cross_repo_caller_groups
    original_resolve = Path.resolve
    returned = []
    error = kind("private post-helper failure")

    def query(*args):
        groups = original(*args)
        assert groups and any(callers for _, callers in groups)
        returned.append(groups)
        return groups

    def resolve(path, *args, **kwargs):
        if returned and path == sibling:
            raise error
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(impact, "_find_cross_repo_caller_groups", query)
    monkeypatch.setattr(Path, "resolve", resolve)
    messages = []
    owner = capture.capture(primary) if bound else None
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    with pytest.raises(kind) as raised:
        runner.run(DIFF, primary)
    assert raised.value is error and len(returned) == 1
    assert asdict(runner._scope_snapshot) == dict(
        scope="established",
        registered=occurrences,
        attempted=1,
        completed=1,
        unavailable=0,
        with_callers=1,
        query_occurrences_accounted=occurrences == 1,
        processing_completed=False,
    )
    assert runner._cached is None
    assert not any("queries completed" in message for message in messages)


def test_changed_input_refusal_does_not_reissue_old_snapshot(graphs):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert runner.run(DIFF, primary)
    original = runner._scope_snapshot
    assert runner.run(DIFF + "\n", primary) == []
    assert runner._scope_snapshot is original
    assert owner.delivery_status == "not_delivered" and owner._attempts == 1


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("bound", [False, True])
def test_helper_control_failure_keeps_attempt_without_completion(graphs, monkeypatch, kind, bound):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    original = impact._live_callers
    error = kind("private helper control")
    calls = []

    def interrupted(*args, **kwargs):
        callers = original(*args, **kwargs)
        assert callers
        calls.append(True)
        raise error

    monkeypatch.setattr(impact, "_live_callers", interrupted)
    owner = capture.capture(primary) if bound else None
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    with pytest.raises(kind) as caught:
        runner.run(DIFF, primary)
    assert caught.value is error and calls == [True]
    facts = runner._scope_snapshot
    assert (
        facts.registered,
        facts.attempted,
        facts.completed,
        facts.unavailable,
        facts.with_callers,
    ) == (1, 1, 0, 0, 0)
    assert not facts.query_occurrences_accounted and not facts.processing_completed
    assert runner._cached is None


def test_second_symbol_sqlite_interrupt_discards_partial_caller_credit(graphs, monkeypatch):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    immediate.edit_graph(
        primary, "INSERT INTO nodes VALUES (2,'function','second','api::second','api.py',1,2)"
    )
    immediate.edit_graph(sibling, "INSERT INTO edges VALUES ('CALLS','client::use','second')")
    connect = sqlite3.connect
    calls = []

    def traced(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs)
        if database == summary.readonly_uri(sibling / ".code-review-graph/graph.db"):

            def trace(statement):
                if "WHERE c.kind = 'CALLS'" in statement:
                    calls.append(statement)
                    if len(calls) == 2:
                        connection.set_progress_handler(lambda: 1, 1)

            connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    runner = impact.CrossRepoImpactRunner()
    assert runner.run(DIFF, primary) == [] and len(calls) == 2
    facts = runner._scope_snapshot
    assert (facts.attempted, facts.completed, facts.unavailable, facts.with_callers) == (1, 0, 1, 0)
    assert facts.query_occurrences_accounted and facts.processing_completed
    assert len(runner.infra_errors) == 1 and "interrupted" in runner.infra_errors[0]


@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("bound", [False, True])
def test_output_failure_does_not_erase_query_facts(graphs, kind, bound):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    error = kind("private output error")
    messages = []

    def sink(message):
        messages.append(message)
        if "queries completed" in message:
            raise error

    owner = capture.capture(primary) if bound else None
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=sink)
    if kind is RuntimeError:
        assert len(runner.run(DIFF, primary)) == 1
        assert runner._cached is not None
    else:
        with pytest.raises(kind) as caught:
            runner.run(DIFF, primary)
        assert caught.value is error and runner._cached is None
    facts = runner._scope_snapshot
    assert (facts.attempted, facts.completed, facts.unavailable, facts.with_callers) == (1, 1, 0, 1)
    assert facts.query_occurrences_accounted
    assert facts.processing_completed is (kind is RuntimeError)
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]
    assert runner.infra_errors == []
