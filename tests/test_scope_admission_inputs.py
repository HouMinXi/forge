"""Real input admission precedes scope receipts, tickets and warm findings."""

import hashlib
import sys
from uuid import uuid4

import pytest

from code_forge import _cross_repo_diagnostics as capture_module
from code_forge import _query_scope_receipt as scope
from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_capture as capture

native_graphs = capture.native_graphs
graphs = capture.graphs
counted_queries = capture.counted_queries

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="scope persistence qualifies Linux local filesystems"
)


def files(root):
    return {
        str(path.relative_to(root)): (path.stat().st_ino, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
@pytest.mark.parametrize("change", ["exact-diff", "lexical-root", "canonical-root"])
def test_input_refusal_preserves_scope_and_ticket_evidence(
    native_graphs, counted_queries, tmp_path, warm, change
):
    primary, sibling, _, _ = native_graphs
    capture.add_named_target_and_caller(primary, sibling)
    alias = primary.parent / "primary-alias"
    alias.symlink_to(primary, target_is_directory=True)
    original_root = alias if change == "canonical-root" else primary
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    owner = capture_module.DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="review-source-hash",
        diff_text=capture.DIFF,
        repo_root=original_root,
        excluded_roots=(),
        ticket_directory=tickets,
    )
    messages = []
    runner = impact.CrossRepoImpactRunner(_diagnostics=owner, output_fn=messages.append)
    if warm:
        assert len(runner.run(capture.DIFF, original_root)) == 1
        assert counted_queries
        assert owner.scope_delivery_status == "delivered"
        assert owner.scope_ticket_status == "exported"
        assert len(owner.scope_records) == len(list(tickets.iterdir())) == 1
    else:
        assert not counted_queries and not owner.scope_records and not list(tickets.iterdir())
    held_records = tuple(owner.scope_records)
    held_old_records = tuple(owner.records)
    attempts = (owner._scope_attempts, owner._attempts)
    before = (files(primary), files(sibling), files(tickets))
    query_count = len(counted_queries)
    messages.clear()
    runner.infra_errors[:] = ["held error view"]
    diff, root = capture.DIFF, original_root
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
    assert runner.infra_errors == ["held error view"]
    assert len(counted_queries) == query_count
    assert tuple(owner.scope_records) == held_records
    assert all(a is b for a, b in zip(owner.scope_records, held_records, strict=True))
    assert tuple(owner.records) == held_old_records
    assert (owner._scope_attempts, owner._attempts) == attempts
    assert (files(primary), files(sibling), files(tickets)) == before
    assert owner.scope_delivery_status == "not_delivered"
    assert owner.delivery_status == "not_delivered"
    assert owner.last_delivery_origin is None
    assert owner.refusal == "context_unavailable"
    assert messages == [capture_module.PERSISTENCE_WARNING]
    # Previous physical evidence remains historical, not current delivery credit.
    for record in held_records:
        snapshot = scope.read(record.publication, record.expected)
        assert (snapshot.registered, snapshot.attempted, snapshot.completed) == (1, 1, 1)
    if change == "canonical-root":
        alias.unlink()
        alias.symlink_to(primary, target_is_directory=True)
    # Same owner, original input: refusal is not an unconditional disabled runner.
    assert len(runner.run(capture.DIFF, original_root)) == 1
    if warm:
        assert len(counted_queries) == query_count
        assert owner.scope_delivery_status == "not_attempted"
        assert (files(primary), files(sibling), files(tickets)) == before
    else:
        assert len(counted_queries) > query_count
        assert owner.scope_delivery_status == "delivered"
        assert owner.scope_ticket_status == "exported"
        assert len(owner.scope_records) == len(list(tickets.iterdir())) == 1
