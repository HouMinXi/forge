"""In-execution strict scope persistence; no historical lookup."""

from dataclasses import replace
import json

import pytest

from code_forge import _advisory_diagnostics as old
from code_forge import _query_scope_receipt as scope
from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_capture as capture
from tests import test_cross_repo_immediate_diagnostic as immediate
from tests import test_cross_repo_query_summary as summary

graphs = immediate.graphs
DIFF = immediate.DIFF


@pytest.mark.parametrize(
    "case", ["zero", "callers", "missing", "corrupt", "mixed", "empty", "malformed"]
)
def test_actual_scope_publisher_file_strict_reader_and_original_text(graphs, monkeypatch, case):
    primary, sibling, registry, _ = graphs
    summary.named(primary)
    if case == "callers":
        capture.add_named_target_and_caller(primary, sibling)
    if case == "missing":
        (sibling / ".code-review-graph/graph.db").unlink()
    if case == "corrupt":
        (sibling / ".code-review-graph/graph.db").write_bytes(b"private corruption")
    if case == "mixed":
        registry.write_text(
            json.dumps({"repos": [{"path": str(sibling)}, {"path": str(primary.parent / "missing")}]})
        )
    if case == "empty":
        registry.write_text('{"repos":[]}')
    if case == "malformed":
        registry.write_text("{broken")
    owner = capture.capture(primary)
    messages, reads = [], []
    original = scope.read

    def reader(publication, expected):
        # This expected object was built by the owner before publication.
        assert expected.execution_id == owner.execution_id
        assert expected.source_hash == "review-source-hash"
        assert expected.repository_id == owner.repository_id
        result = original(publication, expected)
        reads.append((publication, expected, result))
        return result

    monkeypatch.setattr(scope, "read", reader)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(DIFF, primary)
    assert len(reads) == len(owner.scope_records) == owner._scope_attempts == 1
    publication, expected, facts = reads[0]
    assert publication.status == "published" and expected is owner.scope_records[0].expected
    assert owner.scope_delivery_status == "delivered"
    assert owner._attempts == 1 and len(owner.records) == 1
    assert owner.records[0].observation.context.observation_sequence == 1
    old_payload = old.read_observation(
        owner.records[0].publication, owner.records[0].observation.context
    )
    assert old_payload.analysis_state == "targets_resolved" and old_payload.diagnostics == ()
    counts = {
        "zero": (1, 1, 1, 0),
        "callers": (1, 1, 1, 0),
        "missing": (0, 1, 0, 1),
        "corrupt": (0, 1, 1, 1),
        "mixed": (1, 2, 1, 1),
    }
    assert messages == [
        summary.EMPTY
        if case == "empty"
        else summary.UNAVAILABLE
        if case == "malformed"
        else summary.SUMMARY % counts[case]
    ]
    assert facts.with_callers == (None if case == "malformed" else int(case == "callers"))
    loc = publication.locator
    path = loc.binding.source_root.joinpath(*(name for name, _ in loc.directories), loc.filename)
    payload = path.read_text()
    assert "processing_completed" not in payload
    for secret in [str(primary), str(sibling), "private corruption"]:
        assert secret not in payload
    if case in counts:
        assert runner.run(DIFF, primary) == findings
        assert len(reads) == 1 and owner._scope_attempts == owner._attempts == 1
        assert owner.scope_delivery_status == "not_attempted"


@pytest.mark.parametrize(
    "fault",
    [
        "publish",
        "read",
        "wrong_result",
        "close_during_read",
        "context_during_read",
        "tamper",
        "replace",
        "replay",
    ],
)
def test_scope_failure_keeps_real_summary_and_old_channel(graphs, monkeypatch, fault):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    real_read = scope.read

    def reader(publication, expected):
        if fault == "read":
            raise OSError("private")
        if fault == "replay":
            return real_read(
                publication, replace(expected, execution_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            )
        if fault in {"tamper", "replace"}:
            loc = publication.locator
            path = loc.binding.source_root.joinpath(*(n for n, _ in loc.directories), loc.filename)
            raw = path.read_bytes()
            if fault == "tamper":
                path.write_bytes(b"x" * len(raw))
            else:
                other = path.with_name("foreign-replacement")
                other.write_bytes(raw)
                other.chmod(0o600)
                other.replace(path)
        result = real_read(publication, expected)
        if fault == "wrong_result":
            return replace(result, completed=0)
        if fault == "close_during_read":
            owner.close()
        if fault == "context_during_read":
            owner.source_hash = "other source"
        return result

    monkeypatch.setattr(scope, "read", reader)
    if fault == "publish":

        def fail(*args):
            return old.PublicationResult("unknown", failure="commit_uncertain")

        monkeypatch.setattr(scope, "publish", fail)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert len(runner.run(DIFF, primary)) == 1
    expected_messages = [summary.SUMMARY % (1, 1, 1, 0)]
    if fault == "context_during_read":
        # Deliberate owner identity mutation must still trigger the EXISTING
        # v1 warning rather than being hidden by this optional scope channel.
        expected_messages.append(capture.capture_module.PERSISTENCE_WARNING)
    assert messages == expected_messages
    assert owner.scope_delivery_status == "not_delivered" and owner._scope_failed
    assert runner.infra_errors == [] and runner._cached is not None
    assert owner._attempts == 1
    if fault != "context_during_read":
        assert owner.delivery_status == "delivered"


@pytest.mark.parametrize("operation", ["publish", "read"])
@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_scope_control_exception_is_not_swallowed_or_retried(graphs, monkeypatch, operation, kind):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    error = kind("private control")
    calls = []

    def interrupted(*args):
        calls.append(True)
        raise error

    monkeypatch.setattr(scope, operation, interrupted)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    with pytest.raises(kind) as caught:
        runner.run(DIFF, primary)
    assert caught.value is error and calls == [True] and runner._cached is None
    assert messages == [] and owner._scope_failed and owner._scope_attempts == 1
    # A subsequent native retry cannot repeat the failed scope operation.
    assert len(runner.run(DIFF, primary)) == 1
    assert calls == [True] and owner._scope_attempts == 1
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]


@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
def test_scope_read_is_not_mislabeled_output_delivery(graphs, kind):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    error = kind("sink failure")

    def sink(_):
        raise error

    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=sink)
    if kind is KeyboardInterrupt:
        with pytest.raises(kind) as caught:
            runner.run(DIFF, primary)
        assert caught.value is error
    else:
        assert len(runner.run(DIFF, primary)) == 1
    assert owner.scope_delivery_status == "unconfirmed"
    assert owner.scope_records[0].publication.status == "published"


def test_no_output_sink_is_read_not_delivered(graphs):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert len(runner.run(DIFF, primary)) == 1
    assert owner.scope_delivery_status == "read"
    assert owner.scope_records[0].publication.status == "published"


def test_current_context_cannot_adopt_foreign_storage_root(graphs, monkeypatch):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    foreign = primary.parent / "foreign-private-store"
    foreign.mkdir()
    binding = old.bind_storage(foreign, ())
    original = scope.publish

    def misdirected(_binding, expected, facts):
        result = original(binding, expected, facts)
        assert result.status == "published"
        return result

    monkeypatch.setattr(scope, "publish", misdirected)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert len(runner.run(DIFF, primary)) == 1
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]
    assert owner.scope_delivery_status == "not_delivered"
    assert not owner.scope_records and owner._scope_failed
    assert owner.delivery_status == "delivered"


@pytest.mark.parametrize("when", ["before", "during"])
def test_mutable_binding_cache_is_not_independent_expected(graphs, monkeypatch, when):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    foreign = primary.parent / "foreign-cache-store"
    foreign.mkdir()
    binding = old.bind_storage(foreign, ())
    original = scope.publish
    calls = []

    def publisher(held, expected, facts):
        calls.append(True)
        owner._scope_binding = binding
        return original(binding, expected, facts)

    monkeypatch.setattr(scope, "publish", publisher)
    if when == "before":
        owner._scope_binding = binding
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert len(runner.run(DIFF, primary)) == 1
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]
    assert owner.scope_delivery_status == "not_delivered"
    assert not owner.scope_records and owner._scope_failed
    assert calls == ([] if when == "before" else [True])


@pytest.mark.parametrize(
    "fault",
    [
        "not-published-large-failure",
        "unknown-large-failure",
        "failure-object",
        "unknown-locator",
        "unknown-cleanup",
        "unknown-status",
        "published-failure",
        "wrong-namespace",
        "large-filename",
        "large-digest",
        "oversized-directories",
        "oversized-root",
    ],
)
def test_malformed_publication_not_retained_before_strict_read(graphs, monkeypatch, fault):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    original = scope.publish
    large = "PRIVATE" * 200000
    reader_calls = []

    def publisher(binding, expected, facts):
        if fault == "not-published-large-failure":
            return old.PublicationResult("not_published", failure=large)
        if fault == "unknown-large-failure":
            return old.PublicationResult("unknown", failure=large)
        if fault == "failure-object":
            return old.PublicationResult("unknown", failure={"private": large})
        if fault == "unknown-locator":
            return old.PublicationResult("unknown", locator=large, failure="commit_uncertain")
        if fault == "unknown-cleanup":
            return old.PublicationResult("unknown", failure="commit_uncertain", cleanup=large)
        if fault == "unknown-status":
            return old.PublicationResult(large, failure="commit_uncertain")
        result = original(binding, expected, facts)
        assert result.status == "published"
        loc = result.locator
        if fault == "published-failure":
            return replace(result, failure=large)
        if fault == "wrong-namespace":
            dirs = list(loc.directories)
            dirs[1] = ("advisory-diagnostics", dirs[1][1])
            loc = replace(loc, directories=tuple(dirs))
        if fault == "large-filename":
            loc = replace(loc, filename=large)
        if fault == "large-digest":
            loc = replace(loc, digest=large)
        if fault == "oversized-directories":
            loc = replace(loc, directories=loc.directories * 20000)
        if fault == "oversized-root":
            loc = replace(loc, binding=replace(binding, source_root=binding.source_root / large))
        return replace(result, locator=loc)

    original_read = scope.read

    def reader(*args):
        reader_calls.append(True)
        return original_read(*args)

    monkeypatch.setattr(scope, "publish", publisher)
    monkeypatch.setattr(scope, "read", reader)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert len(runner.run(DIFF, primary)) == 1
    assert messages == [summary.SUMMARY % (1, 1, 1, 0)]
    assert owner.scope_records == []  # no unbounded callback payload kept alive
    assert reader_calls == []  # reject shape/namespace before I/O consumer
    assert owner._scope_failed and owner.scope_delivery_status == "not_delivered"
    assert owner.delivery_status == "delivered" and owner._attempts == 1


@pytest.mark.parametrize(
    "status,failure", [("not_published", "publication_failed"), ("unknown", "commit_uncertain")]
)
def test_bounded_failure_result_retained_without_locator_or_retry(graphs, monkeypatch, status, failure):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    result = old.PublicationResult(status, failure=failure)
    calls = []

    def publisher(*args):
        calls.append(True)
        return result

    monkeypatch.setattr(scope, "publish", publisher)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert len(runner.run(DIFF, primary)) == 1
    assert len(owner.scope_records) == 1 and owner.scope_records[0].publication is result
    assert owner.scope_records[0].publication.locator is None
    assert len(runner.run(DIFF, primary)) == 1 and calls == [True]
    assert owner._scope_attempts == 1 and owner._scope_failed


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_scope_publication_validation_cancellation_propagates_without_retention(
    graphs, monkeypatch, kind
):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    error = kind("private validator cancellation")

    def interrupted(*args):
        raise error

    monkeypatch.setattr(scope, "validate_publication", interrupted, raising=False)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    with pytest.raises(kind) as caught:
        runner.run(DIFF, primary)
    assert caught.value is error
    assert owner.scope_records == [] and owner._scope_failed
    assert runner._cached is None


@pytest.mark.parametrize("where", ["returned-locator", "cached-binding"])
def test_equivalent_path_raw_storage_is_not_retained(graphs, monkeypatch, where):
    primary, sibling, _, _ = graphs
    capture.add_named_target_and_caller(primary, sibling)
    owner = capture.capture(primary)
    binding = old.bind_storage(primary, ())
    raw_path = "/" * 1400000 + str(primary).lstrip("/")
    inflated_root = type(primary)(raw_path)
    assert inflated_root == binding.source_root and len(raw_path) > 1400000
    inflated = replace(binding, source_root=inflated_root)
    original = scope.publish

    def publisher(held, expected, facts):
        if where == "cached-binding":
            assert held.source_root is owner._identity[5]
        result = original(held, expected, facts)
        if where == "returned-locator":
            return replace(result, locator=replace(result.locator, binding=inflated))
        return result

    if where == "cached-binding":
        owner._scope_binding = inflated
    monkeypatch.setattr(scope, "publish", publisher)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert len(runner.run(DIFF, primary)) == 1
    assert owner.scope_delivery_status == "read"
    assert len(owner.scope_records) == 1
    retained = owner.scope_records[0].publication.locator.binding
    assert retained is owner._scope_binding
    assert retained.source_root is owner._identity[5]
    assert retained.source_root is not inflated_root
