# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Native discovery capture, admission and bounded failure regression controls."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

from code_forge import _advisory_diagnostics as storage
from code_forge import _cross_repo_diagnostics as capture_module
from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_immediate_diagnostic as immediate

DIFF, MESSAGE, edit_graph = immediate.DIFF, immediate.MESSAGE, immediate.edit_graph
graphs = immediate.graphs

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="persistent capture qualifies Linux local filesystems"
)


def capture(root):
    return capture_module.DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="review-source-hash",
        diff_text=DIFF,
        repo_root=root,
        excluded_roots=(),
    )


@pytest.fixture
def native_graphs(graphs, monkeypatch):
    """Use the installed provider when available, real registry/SQLite either way."""
    try:
        version("code-review-graph")
    except PackageNotFoundError:
        pass
    else:
        monkeypatch.delitem(sys.modules, "code_review_graph.registry")
        monkeypatch.delitem(sys.modules, "code_review_graph")
        __import__("code_review_graph.registry")
    return graphs


@pytest.fixture
def counted_queries(monkeypatch):
    queries = []
    native_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = native_connect(*args, **kwargs)
        if kwargs.get("uri"):
            connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(impact.sqlite3, "connect", connect)
    return queries


def add_named_target_and_caller(primary, sibling):
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    edit_graph(sibling, "UPDATE nodes SET kind='function', name='use', qualified_name='client::use'")
    edit_graph(sibling, "INSERT INTO edges VALUES ('CALLS', 'client::use', 'api')")
    edit_graph(sibling, "INSERT INTO edges VALUES ('IMPORTS_FROM', 'client::use', 'api')")


@pytest.mark.parametrize("change", ["replace", "remove", "execution", "source", "stage", "stage-store"])
def test_changed_capture_refuses_before_cached_findings_queries_or_error_clear(
    native_graphs, counted_queries, change
):
    primary, sibling, _, _ = native_graphs
    add_named_target_and_caller(primary, sibling)
    owner = capture(primary)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(DIFF, primary)
    assert len(findings) == 1
    assert owner.delivery_status == "delivered"
    assert owner.last_delivery_origin == "actual"
    assert counted_queries
    original_query_count = len(counted_queries)
    original_records = tuple(owner.records)
    runner.infra_errors[:] = ["untouched sentinel"]
    if change == "replace":
        runner._diagnostics = capture(primary)
    elif change == "remove":
        owner.close()
        runner._diagnostics = None
    elif change == "execution":
        owner.execution_id = str(uuid4())
    elif change == "source":
        owner.source_hash = "changed source hash"
    elif change == "stage":
        owner._stages["primary_database"] = primary / "replacement.db"
    else:
        owner._stages = dict(owner._stages)

    assert runner.run(DIFF, primary) == []
    assert runner.infra_errors == ["untouched sentinel"]
    assert len(counted_queries) == original_query_count
    assert tuple(owner.records) == original_records
    assert owner.delivery_status == "not_delivered"
    assert owner.last_delivery_origin is None
    assert messages == [immediate.QUERY_SUMMARY % (1, 1, 1, 0), capture_module.PERSISTENCE_WARNING]


def test_foreign_record_cannot_supply_expected_context_or_no_target_presentation(
    native_graphs, monkeypatch
):
    primary = native_graphs[0]
    original = capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=original)
    assert runner.run(DIFF, primary) == []
    record = original.records[0]
    assert record.publication.status == "published"
    foreign = capture(primary)
    reads = []
    native_read = storage.read_observation

    def read(*args):
        reads.append(args)
        return native_read(*args)

    monkeypatch.setattr(storage, "read_observation", read)
    presentation = foreign.present(record)
    assert presentation == (capture_module.PERSISTENCE_WARNING,)
    assert reads == []


@pytest.mark.parametrize("change", ["exact-diff", "lexical-root", "canonical-root"])
def test_changed_input_refuses_before_cache_sql_error_clear_or_publication(
    native_graphs, counted_queries, change
):
    primary, sibling, _, _ = native_graphs
    add_named_target_and_caller(primary, sibling)
    alias = primary.parent / "primary-alias"
    alias.symlink_to(primary, target_is_directory=True)
    original_root = alias if change == "canonical-root" else primary
    owner = capture(original_root)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    findings = runner.run(DIFF, original_root)
    assert len(findings) == 1 and counted_queries
    assert owner.records[0].publication.status == "published"
    query_count = len(counted_queries)
    original_record = owner.records[0]
    original_errors = ["untouched native error view"]
    runner.infra_errors[:] = original_errors
    diff, root = DIFF, original_root
    if change == "exact-diff":
        diff += "\n"
    elif change == "lexical-root":
        root = alias
        assert root != original_root and root.resolve() == original_root.resolve()
    else:
        alias.unlink()
        alias.symlink_to(sibling, target_is_directory=True)
        assert root == original_root and root.resolve() != owner._canonical

    assert runner.run(diff, root) == []
    assert runner.infra_errors == original_errors
    assert len(counted_queries) == query_count
    assert owner.records == [original_record] and owner._attempts == 1
    assert owner.records[0] is original_record
    assert owner.refusal == "context_unavailable"
    assert owner.delivery_status == "not_delivered"
    assert owner.last_delivery_origin is None
    assert messages == [immediate.QUERY_SUMMARY % (1, 1, 1, 0), capture_module.PERSISTENCE_WARNING]


@pytest.mark.parametrize("case", ["filtered-primary", "failed-fallback"])
def test_consumed_relative_root_limits_delivery_before_filter_or_selection_failure(
    native_graphs, monkeypatch, case
):
    primary, sibling, enumeration, default = native_graphs
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    if case == "filtered-primary":
        enumeration.write_text(json.dumps({"repos": [{"path": "."}]}), encoding="utf-8")
        monkeypatch.chdir(primary)
    else:
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text(
            json.dumps(
                {"repos": [{"path": str(primary), "data_dir": str(primary / ".code-review-graph")}]}
            ),
            encoding="utf-8",
        )
        enumeration.write_text(json.dumps({"repos": [{"path": sibling.name}]}), encoding="utf-8")
        monkeypatch.chdir(primary.parent)
        unknown_user = "~forge-capture-missing-" + uuid4().hex + "/graph"
        with pytest.raises(RuntimeError):
            Path(unknown_user).expanduser()
        monkeypatch.setenv("CRG_DATA_DIR", unknown_user)

    native = impact.CrossRepoImpactRunner()
    expected = native.run(DIFF, primary)
    expected_errors = tuple(native.infra_errors)
    assert expected == [] and expected_errors
    owner = capture(primary)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert runner.run(DIFF, primary) == expected
    assert tuple(runner.infra_errors) == expected_errors
    assert owner.records[0].analysis_state == "targets_resolved"
    assert owner.records[0].publication.status == "not_published"
    assert not tuple((primary / ".code-forge/advisory-diagnostics").glob("*/*/*/*.json"))
    assert messages == [
        immediate.EMPTY_SCOPE if case == "filtered-primary" else immediate.QUERY_SUMMARY % (0, 1, 0, 1),
        capture_module.PERSISTENCE_WARNING,
    ]


def test_large_final_cache_errors_do_not_restore_previous_uncached_error(native_graphs, counted_queries):
    primary, sibling, enumeration, _ = native_graphs
    db = primary / ".code-review-graph/graph.db"
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    original = db.read_bytes()
    db.unlink()
    enumeration.write_text(
        json.dumps({"repos": [{"path": str(sibling / "missing"), "alias": "a" * 4097}]}),
        encoding="utf-8",
    )
    owner = capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert runner.run(DIFF, primary) == []
    assert runner.infra_errors == [
        "cross-repo-impact: primary graph.db not found; build code-review-graph"
    ]
    assert counted_queries == []
    db.write_bytes(original)
    assert runner.run(DIFF, primary) == []
    final_errors = tuple(runner.infra_errors)
    assert len(final_errors[0]) > 4096 and "sibling" in final_errors[0]
    assert runner._cached == []
    assert counted_queries
    original_query_count = len(counted_queries)
    assert runner.run(DIFF, primary) == []
    assert tuple(runner.infra_errors) == final_errors
    assert len(counted_queries) == original_query_count


def test_discovery_failure_is_retained_without_exception_graphs_or_native_retry(
    native_graphs, counted_queries, monkeypatch
):
    primary, _, _, default = native_graphs
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text("{broken registry", encoding="utf-8")
    reads = []
    native_read = impact._read_registry

    def read(path):
        reads.append(path)
        return native_read(path)

    monkeypatch.setattr(impact, "_read_registry", read)
    owner = capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert runner.run(DIFF, primary) == []
    initial_errors = tuple(runner.infra_errors)
    assert initial_errors and counted_queries == []
    default.write_text('{"repos": []}', encoding="utf-8")
    assert runner.run(DIFF, primary) == []
    assert tuple(runner.infra_errors) == initial_errors
    assert counted_queries == []
    assert reads == [default]
    assert runner._cached is None
    assert len(owner.records) == 2
    assert all(record.analysis_state == "unavailable" for record in owner.records)
    failure = owner._stages["default_registry"]
    assert not isinstance(failure, BaseException)
    assert type(failure.message) is str and type(failure.kind) is str
    fresh = impact.CrossRepoImpactRunner(_diagnostics=capture(primary))
    assert fresh.run(DIFF, primary) == []
    assert fresh.infra_errors == []
    assert counted_queries
    assert reads == [default, default]


@pytest.mark.parametrize("limit", ["bytes", "entries", "stages"])
def test_capture_limit_never_repeats_the_native_operation(native_graphs, monkeypatch, limit):
    owner = capture(native_graphs[0])
    calls = []
    if limit == "entries":
        monkeypatch.setattr(capture_module, "_MAX_ENTRIES", 0)
        value, kind = [{"path": "/one"}], "registry"
    else:
        value, kind = "native result", "value"
        monkeypatch.setattr(
            capture_module, "_MAX_CAPTURE_BYTES" if limit == "bytes" else "_MAX_STAGES", 0
        )

    def operation():
        calls.append("native")
        return value

    assert owner.stage("first", operation, kind=kind) is value
    assert calls == ["native"]
    assert not owner._supported
    assert owner.stage("next", operation, kind=kind) is value
    assert calls == ["native", "native"]


def test_failure_capture_limit_preserves_original_error_without_retry(native_graphs, monkeypatch):
    owner = capture(native_graphs[0])
    monkeypatch.setattr(capture_module, "_MAX_CAPTURE_BYTES", 0)
    calls = []
    fault = OSError("native failure")

    def operation():
        calls.append("native")
        raise fault

    with pytest.raises(OSError) as caught:
        owner.stage("failure", operation)
    assert caught.value is fault
    assert calls == ["native"]
    assert not owner._supported
    assert owner._stages == {}


def test_duplicate_occurrences_retain_distinct_first_use_native_fallbacks(
    native_graphs, counted_queries, monkeypatch
):
    primary, sibling, enumeration, default = native_graphs
    add_named_target_and_caller(primary, sibling)
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(
        json.dumps({"repos": [{"path": str(primary), "data_dir": str(primary / ".code-review-graph")}]}),
        encoding="utf-8",
    )
    enumeration.write_text(
        json.dumps(
            {
                "repos": [
                    {"path": str(sibling), "alias": "first"},
                    {"path": str(sibling), "alias": "second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    earlier_missing = primary.parent / "earlier-missing-data"
    monkeypatch.setenv("CRG_DATA_DIR", str(earlier_missing))
    selections = []
    native_select = impact._readonly_db_path

    def select(root, entries):
        value = native_select(root, entries)
        selections.append((root, value))
        if root == sibling:
            monkeypatch.setenv("CRG_DATA_DIR", str(sibling / ".code-review-graph"))
        return value

    monkeypatch.setattr(impact, "_readonly_db_path", select)
    owner = capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    findings = runner.run(DIFF, primary)
    assert owner.last_delivery_origin == "actual"
    assert owner.delivery_status == "delivered"
    assert len(findings) == 1 and findings[0].file == "second:api.py"
    assert runner.infra_errors == ["cross-repo-impact: sibling 'first' graph.db missing"]
    assert selections == [
        (primary, primary / ".code-review-graph/graph.db"),
        (sibling, earlier_missing / "graph.db"),
        (sibling, sibling / ".code-review-graph/graph.db"),
    ]
    frozen_stages = owner.records[0].stages
    original_record = owner.records[0]
    original_observation = original_record.observation
    query_count = len(counted_queries)
    assert query_count
    assert dict(frozen_stages)["sibling_database.0"] == earlier_missing / "graph.db"
    assert dict(frozen_stages)["sibling_database.1"] == sibling / ".code-review-graph/graph.db"
    findings.clear()
    runner.infra_errors.clear()
    monkeypatch.setenv("CRG_DATA_DIR", str(primary.parent / "unused-later"))
    replay = runner.run(DIFF, primary)
    assert len(replay) == 1 and replay[0].file == "second:api.py"
    assert runner.infra_errors == ["cross-repo-impact: sibling 'first' graph.db missing"]
    assert len(selections) == 3
    assert len(owner.records) == 1 and owner.records[0].stages == frozen_stages
    assert owner.records[0] is original_record
    assert owner.records[0].observation is original_observation
    assert owner.last_delivery_origin == "cache"
    assert owner.delivery_status == "delivered"
    assert len(counted_queries) == query_count and owner._attempts == 1


def test_literal_registry_alias_keeps_distinct_snapshots_and_uncached_graph_retry(
    native_graphs, counted_queries, monkeypatch
):
    primary, sibling, _, default = native_graphs
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    default.parent.mkdir(parents=True, exist_ok=True)
    original_entries = [{"path": str(primary)}, {"path": str(sibling)}]
    default.write_text(json.dumps({"repos": original_entries}), encoding="utf-8")
    alternate = default.parent / ".." / default.parent.name / default.name
    assert alternate != default and alternate.samefile(default)
    monkeypatch.setenv("CRG_REGISTRY_PATH", str(alternate))
    reads = []
    native_read, native_resolve = impact._read_registry, impact.resolve_changed_symbols

    def read(path):
        value = native_read(path)
        reads.append((path, len(value)))
        return value

    def resolve(*args):
        value = native_resolve(*args)
        default.write_text(json.dumps({"repos": original_entries[:1]}), encoding="utf-8")
        return value

    monkeypatch.setattr(impact, "_read_registry", read)
    monkeypatch.setattr(impact, "resolve_changed_symbols", resolve)
    owner = capture(primary)
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner)
    assert runner.run(DIFF, primary) == []
    assert runner._cached is None
    assert reads == [(default, 2), (alternate, 1)]
    assert runner.infra_errors == [
        "cross-repo-impact: no sibling repos registered; use code-review-graph register"
    ]
    first_queries, first_stages = len(counted_queries), owner.records[0].stages
    assert first_queries
    default.write_text(json.dumps({"repos": original_entries}), encoding="utf-8")
    assert runner.run(DIFF, primary) == []
    assert len(counted_queries) == 2 * first_queries
    assert reads == [(default, 2), (alternate, 1)]
    assert len(owner.records) == 2
    assert owner.records[0].stages == owner.records[1].stages == first_stages


def test_registry_stage_returns_defensive_copies(native_graphs):
    owner = capture(native_graphs[0])
    original = [{"path": "/primary", "alias": "source"}]
    first = owner.stage("registry", lambda: original, kind="registry")
    original[0]["path"] = "/changed-input"
    first[0]["alias"] = "changed-return"
    first.append({"path": "/extra-return"})

    def forbidden():
        pytest.fail("retained registry stage must not be read again")

    assert owner.stage("registry", forbidden, kind="registry") == [
        {"path": "/primary", "alias": "source"}
    ]


def test_non_utf8_native_error_disables_only_persistence(native_graphs):
    primary, sibling, enumeration, _ = native_graphs
    edit_graph(primary, "UPDATE nodes SET kind='function', name='api', qualified_name='api::api'")
    enumeration.write_text(
        json.dumps({"repos": [{"path": str(sibling / "missing"), "alias": "\ud800"}]}),
        encoding="utf-8",
    )
    native = impact.CrossRepoImpactRunner()
    expected = native.run(DIFF, primary)
    expected_errors = tuple(native.infra_errors)
    assert expected == [] and "\ud800" in expected_errors[0]
    owner = capture(primary)
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert runner.run(DIFF, primary) == expected
    assert tuple(runner.infra_errors) == expected_errors
    assert owner.records[0].publication.status == "not_published"
    assert messages == [immediate.QUERY_SUMMARY % (0, 1, 0, 1), capture_module.PERSISTENCE_WARNING]


def expected_native_finding():
    return impact.AdvisoryFinding(
        id="cross-repo-impact-1",
        axis="CROSS-REPO-IMPACT",
        file="consumer:api.py",
        line_range=(1, 1),
        description="api used by consumer at api.py:1",
        attribution="cross-repo graph.db",
    )


def issued_context(owner):
    # Held before publication, never derived from the artifact or reader result.
    return storage.ExpectedContext(
        owner.execution_id, owner.repository_id, owner.source_hash, owner.diff_sha256, 1
    )


def artifact_bytes(primary):
    return {
        path: path.read_bytes()
        for path in (primary / ".code-forge/advisory-diagnostics").rglob("*.json")
    }


@pytest.fixture
def observed_storage(monkeypatch):
    calls = {"publish": [], "read": []}
    native_publish, native_read = storage.publish_observation, storage.read_observation

    def publish(*args):
        calls["publish"].append(args)
        return native_publish(*args)

    def read(*args):
        calls["read"].append(args)
        return native_read(*args)

    monkeypatch.setattr(storage, "publish_observation", publish)
    monkeypatch.setattr(storage, "read_observation", read)
    return calls


def run_on_foreign_thread(runner, primary):
    import threading

    results, errors, identities = [], [], []

    def invoke():
        identities.append(threading.get_ident())
        try:
            results.append(runner.run(DIFF, primary))
        except BaseException as exc:  # noqa: BLE001 -- Surface every worker failure on the owner.
            errors.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "harness failure: native worker did not finish"
    assert not errors, "harness failure: native worker raised"
    assert len(results) == 1 and len(identities) == 1, "harness failure: missing worker result"
    assert identities[0] != threading.get_ident()
    assert runner._diagnostics._thread == threading.get_ident()
    return results[0]


def test_active_owner_native_cold_and_warm_delivery(native_graphs, counted_queries, observed_storage):
    primary, sibling, _, _ = native_graphs
    add_named_target_and_caller(primary, sibling)
    owner = capture(primary)
    expected = issued_context(owner)
    expected_findings = [expected_native_finding()]
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    assert runner.run(DIFF, primary) == expected_findings
    assert counted_queries and owner.last_delivery_origin == "actual"
    assert owner.delivery_status == "delivered"
    record = owner.records[0]
    assert record.publication.status == "published"
    original = artifact_bytes(primary)
    assert len(original) == 1
    assert storage.read_observation(record.publication, expected) == storage.Observation(
        expected, "targets_resolved", ()
    )
    query_count = len(counted_queries)
    assert runner.run(DIFF, primary) == expected_findings
    assert len(counted_queries) == query_count
    assert owner.last_delivery_origin == "cache" and owner.delivery_status == "delivered"
    assert owner.records == [record] and owner.records[0] is record
    assert owner._attempts == len(observed_storage["publish"]) == 1
    assert len(observed_storage["read"]) == 3
    assert artifact_bytes(primary) == original
    assert messages == [immediate.QUERY_SUMMARY % (1, 1, 1, 0)]


@pytest.mark.parametrize("phase", ["cold", "warm"])
@pytest.mark.parametrize("invalidation", ["closed-installed", "foreign-thread"])
def test_installed_capture_admission_precedes_native_work(
    native_graphs, counted_queries, observed_storage, phase, invalidation
):
    primary, sibling, _, _ = native_graphs
    add_named_target_and_caller(primary, sibling)
    owner = capture(primary)
    expected = issued_context(owner)
    expected_findings = [expected_native_finding()]
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    if phase == "warm":
        assert runner.run(DIFF, primary) == expected_findings
        assert counted_queries and owner.delivery_status == "delivered"
    else:
        assert counted_queries == [] and owner.records == []
    records = tuple(owner.records)
    original_bytes = artifact_bytes(primary)
    assert len(original_bytes) == int(phase == "warm")
    cache, bound_errors, bound_record = runner._cached, runner._bound_errors, runner._bound_record
    query_count = len(counted_queries)
    counts = {key: len(value) for key, value in observed_storage.items()}
    attempts = owner._attempts
    sentinel = [f"untouched {invalidation} {phase} error"]
    runner.infra_errors[:] = sentinel
    if invalidation == "closed-installed":
        owner.close()
    assert runner._diagnostics is runner._installed_diagnostics is owner
    result = (
        run_on_foreign_thread(runner, primary)
        if invalidation == "foreign-thread"
        else runner.run(DIFF, primary)
    )
    assert result == [], "admission refusal must precede native findings or cached return"
    assert runner.infra_errors == sentinel
    assert len(counted_queries) == query_count
    assert {key: len(value) for key, value in observed_storage.items()} == counts
    assert tuple(owner.records) == records and all(
        now is before for now, before in zip(owner.records, records, strict=True)
    )
    assert owner._attempts == attempts
    assert runner._cached is cache and runner._bound_errors is bound_errors
    assert runner._bound_record is bound_record
    assert owner.refusal == "context_unavailable"
    assert owner.delivery_status == "not_delivered" and owner.last_delivery_origin is None
    assert messages == ([immediate.QUERY_SUMMARY % (1, 1, 1, 0)] if phase == "warm" else []) + [
        capture_module.PERSISTENCE_WARNING
    ]
    assert artifact_bytes(primary) == original_bytes
    if phase == "warm":
        assert storage.read_observation(records[0].publication, expected) == storage.Observation(
            expected, "targets_resolved", ()
        )
    else:
        assert not (primary / ".code-forge/advisory-diagnostics").exists()

    if invalidation == "foreign-thread":
        # Serial reuse on its creator, not simultaneous sharing or migration.
        assert owner._active
        assert runner.run(DIFF, primary) == expected_findings
        assert owner.delivery_status == "delivered"
        assert owner.last_delivery_origin == ("cache" if phase == "warm" else "actual")
        if phase == "warm":
            assert len(counted_queries) == query_count
            assert owner.records[0] is records[0]
            assert owner.records[0].publication is records[0].publication
            assert len(observed_storage["publish"]) == counts["publish"]
        else:
            assert len(counted_queries) > query_count
            assert len(observed_storage["publish"]) == counts["publish"] + 1
        assert len(observed_storage["read"]) == counts["read"] + 1 + int(phase == "warm")
        assert storage.read_observation(owner.records[0].publication, expected) == storage.Observation(
            expected, "targets_resolved", ()
        )
        assert runner.infra_errors == []
        recovered = owner
    else:
        assert not owner._active
        recovered = capture(primary)
        assert recovered.execution_id != owner.execution_id
        assert recovered.repository_id != owner.repository_id
        fresh_expected = issued_context(recovered)
        fresh = impact.CrossRepoImpactRunner(_diagnostics=recovered)
        assert fresh.run(DIFF, primary) == expected_findings
        assert recovered.delivery_status == "delivered"
        assert storage.read_observation(
            recovered.records[0].publication, fresh_expected
        ) == storage.Observation(fresh_expected, "targets_resolved", ())
        if records:
            assert recovered.present(records[0]) == (capture_module.PERSISTENCE_WARNING,)
            assert recovered.refusal == "foreign_observation"
        assert not owner._active
    assert len(recovered.records) == 1
    assert recovered._attempts == 1
    assert runner._diagnostics is runner._installed_diagnostics is owner
    for path, data in original_bytes.items():
        assert path.read_bytes() == data
