"""Real decoder/host/factory/state accounting; mock HTTP and synthetic recorder.

Default pre-ACK recorder FINISH failure is not accounting failure or native durability evidence.
Strict failure accounting retention remains outside this test's contract.
"""

import copy
import hashlib
import json
import threading

import pytest

from code_forge import llm_invoke
from code_forge.factories import build_l1_provider
from code_forge.invocation_io import AuditPhase
from code_forge.invocation_review import OrdinaryReviewAudit
from tests import _review_harness
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_send_audit import Response, scripted
from tests.test_per_pass_usage import DIFF, ROLES, make_machine, native_transport, resolved

pytest_plugins = ["tests.test_invocation_host"]
offline_workspace = _review_harness.offline_workspace


def assert_accounting(cost, rows, usage_case):
    expected_rows = (
        [(11, None, None)] * 3
        if usage_case == "partial"
        else [(0, 0, 0)] * 3
        if usage_case == "zero"
        else [(11, 5, 7), (23, 13, 17), (47, 19, 29)]
    )
    assert len(rows) == len(cost["per_pass"]) == 3
    assert [row["pass_name"] for row in rows] == list(ROLES)
    for row, saved, expected in zip(rows, cost["per_pass"], expected_rows, strict=True):
        assert tuple(row[k] for k in ("input", "output", "cached")) == expected
        assert row["available"] is (usage_case != "partial")
        assert all(saved[k] == row[k] for k in row)
    assert tuple(
        cost[k] for k in ("total_input_tokens", "total_output_tokens", "total_cached_tokens")
    ) == ((33, 0, 0) if usage_case == "partial" else (0, 0, 0) if usage_case == "zero" else (81, 37, 53))


@pytest.mark.parametrize("usage_case", ["partial", "zero", "full"])
@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "finish-refusal"])
def test_default_finish_failure_keeps_factory_accounting(
    setup, offline_workspace, monkeypatch, default_l1_dispatch, usage_case, fault
):
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    root = offline_workspace
    host, notices = setup[0](control_timeout_s=2)
    io = setup[1]
    backend = native_transport(monkeypatch, "openai", usage_case)
    # Extract fixture bytes only; scripted below rejects every unaudited urlopen.
    raw = llm_invoke.urllib.request.urlopen().read()
    responses = [Response(raw) for _ in range(3)]
    if usage_case == "full":
        responses = []
        for incoming, outgoing, cached in ((11, 5, 7), (23, 13, 17), (47, 19, 29)):
            body = json.loads(raw)
            body["usage"] = {
                "prompt_tokens": incoming,
                "completion_tokens": outgoing,
                "prompt_tokens_details": {"cached_tokens": cached},
            }
            responses.append(Response(json.dumps(body).encode()))
    transport = scripted(monkeypatch, responses)
    provider = build_l1_provider("real", resolved(), backend=backend, max_attempts=2)
    machine = make_machine(root, provider)
    review = OrdinaryReviewAudit(source=DIFF, environ={})
    monkeypatch.setattr(review, "_open_host", lambda: host)
    machine.invocation_audit = review
    finishes = []
    original = MockRecorder.finish

    def finish(recorder, **kwargs):
        assert threading.current_thread() is io._worker
        assert io._running._phase is AuditPhase.FINISH
        assert io._running._outcome is None
        assert len(transport.requests) == len(finishes) + 1
        assert host._owner is not None and host._started == len(finishes) + 1
        assert kwargs["outcome"] == "completed" and kwargs["error_class"] is None
        record = next(
            r for r in host._owner._leases.values() if r.value.attempt_id == kwargs["attempt_id"]
        )
        expected_raw = responses[len(finishes)].raw
        assert record.captures["wire"] == (len(expected_raw), True)
        assert len(record.refs) == 1
        assert record.refs[0].sha256 == hashlib.sha256(expected_raw).hexdigest()
        stored = [
            bytes(body)
            for handle, body in recorder.stages.items()
            if handle.identity.attempt_id == kwargs["attempt_id"]
        ]
        assert stored == [expected_raw]
        finishes.append(dict(kwargs))
        if fault:
            raise OSError("synthetic FINISH failure")
        return original(recorder, **kwargs)

    monkeypatch.setattr(MockRecorder, "finish", finish)
    machine._state.env_manifest = {}  # No command/toolchain discovery.
    machine._execute_round(0)
    cost = json.loads((root / ".code-forge/state.json").read_text())["cost"]
    assert_accounting(cost, provider.pass_usage, usage_case)
    assert len(transport.requests) == 3
    assert len(finishes) == (1 if fault else 3)
    assert host._started == (1 if fault else 3)
    assert host.failed is fault
    assert notices == (["invocation_audit_unavailable"] if fault else [])
    summary = host.safe_summary()
    if fault:
        assert summary.state == "incomplete" and not summary.audit_complete
        assert summary.publication_uncertain
    else:
        assert summary.settled_sends == 3
    # Mutation controls exercise the same oracle against observed real outputs.
    duplicated = copy.deepcopy(cost)
    duplicated["per_pass"].append(copy.deepcopy(duplicated["per_pass"][0]))
    with pytest.raises(AssertionError):
        assert_accounting(duplicated, provider.pass_usage, usage_case)
    missing = copy.deepcopy(cost)
    missing["per_pass"].pop(0)
    with pytest.raises(AssertionError):
        assert_accounting(missing, provider.pass_usage, usage_case)
    corrupted = copy.deepcopy(provider.pass_usage)
    corrupted[0]["output"] = 0 if usage_case == "partial" else None
    with pytest.raises(AssertionError):
        assert_accounting(cost, corrupted, usage_case)
    if usage_case == "partial":
        doubled = copy.deepcopy(cost)
        doubled["total_input_tokens"] = 66
        with pytest.raises(AssertionError):
            assert_accounting(doubled, provider.pass_usage, usage_case)
