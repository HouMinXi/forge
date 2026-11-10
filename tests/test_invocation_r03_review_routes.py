"""Default review routes after R03 handoff failure; synthetic storage/mock HTTP.

These phase-boundary tests do not run a whole review or prove durable recovery.
L2 is a mutation runner, and backend fallback is preflight selection, not a
post-fault model failover. Neither is represented as an audit-isolated route.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from code_forge import backend as backend_module
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.cli import resolve_backend_with_fallback
from code_forge.falsify_real import RealFalsifier
from code_forge.invocation_review import OrdinaryReviewAudit
from code_forge.machine import StateMachine
from code_forge.state import Mode, State
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_host_dispatch import inject, invoke
from tests.test_invocation_send_audit import reply, scripted
from tests.test_ordinary_audit_native import DIFF, PASSES, _provider, _resolved, _Transport

pytest_plugins = ["tests.test_invocation_host"]


def make_machine(tmp_path, monkeypatch, host):
    backend, provider = _provider(monkeypatch)
    review = OrdinaryReviewAudit(source=DIFF, environ={})
    monkeypatch.setattr(review, "_open_host", lambda: host)
    machine = StateMachine(
        mode=Mode.CI,
        falsifier=RealFalsifier(backend=backend, diff_text=DIFF),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _: None,
        resolved_review=_resolved(),
        source_hash="fixture",
        baseline_spec_repr="fixture",
        cwd=tmp_path,
        registry={},
        l1_provider=provider,
        invocation_audit=review,
    )
    machine._state = State(round=1)
    return machine, backend


def assert_refusal_persists(host, owner):
    assert owner._refused and owner._faults == []
    assert host._started == 0 and not host._attempts
    assert sum(name == "start" for name, _ in MockRecorder.calls) == 1
    assert not any(recorder.finished for recorder in MockRecorder.instances)
    summary = host.safe_summary()
    assert summary.state == "incomplete" and not summary.audit_complete


@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-diagnostic-failure"])
def test_default_real_machine_next_round_l1_keeps_audit_refusal(
    setup,
    tmp_path,
    monkeypatch,
    default_l1_dispatch,
    fault,
):
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    host, notices = setup[0](control_timeout_s=2)
    machine, _ = make_machine(tmp_path, monkeypatch, host)
    owner = inject(host, monkeypatch, "lease_list_after", True) if fault else None
    transport = _Transport(monkeypatch, partial_falsifier=False)
    for round_index in (1, 2):
        machine._state.round = round_index
        machine._run_l1_phase()
        assert transport.counts == {**dict.fromkeys(PASSES, round_index), "falsify": round_index * 3}
        if fault:
            assert_refusal_persists(host, owner)
        else:
            assert host._started == len(transport.exchanges)
    assert all(transport.counts[name] == 2 for name in PASSES)
    assert transport.counts["falsify"] == 6
    if fault:
        assert notices == ["invocation_audit_unavailable"]
    else:
        assert not host.failed


@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-diagnostic-failure"])
def test_default_preflight_selected_backend_does_not_reopen_failed_audit(
    setup,
    monkeypatch,
    fault,
):
    host, _ = setup[0](control_timeout_s=2)
    backend, _ = _provider(monkeypatch)
    user_backend = replace(backend, name="unreachable-user")
    probes = []

    def probe(candidate, env=None):
        probes.append(candidate.name)
        return backend_module.ProbeResult(ok=candidate is backend)

    monkeypatch.setattr(backend_module, "probe_backend", probe)
    selected = resolve_backend_with_fallback(user_backend, [backend], {backend.name})
    assert selected is backend and probes == [user_backend.name, backend.name]
    owner = inject(host, monkeypatch, "lease_dict", True) if fault else None
    transport = scripted(monkeypatch, [reply(stream=True)])
    assert invoke(host, selected).content["findings"] == []
    assert len(transport.requests) == 1
    if fault:
        assert_refusal_persists(host, owner)
    else:
        assert host._started == 1 and not host.failed


@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-diagnostic-failure"])
def test_real_l2_mutation_phase_is_not_model_dispatch(setup, tmp_path, monkeypatch, fault):
    host, _ = setup[0](control_timeout_s=2)
    machine, backend = make_machine(tmp_path, monkeypatch, host)
    transport = scripted(monkeypatch, [reply(stream=True)])
    owner = inject(host, monkeypatch, "lease_list_before", True) if fault else None
    # Establish the failure through actual transport before moving to L2.
    assert invoke(host, backend).content["findings"] == []
    assert len(transport.requests) == 1
    if fault:
        assert_refusal_persists(host, owner)
    else:
        assert host._started == 1 and not host.failed
    before = (len(transport.requests), list(MockRecorder.calls), host._started)
    path = tmp_path / "fixture.unknown"
    path.write_text("synthetic source\n")
    machine.resolved_review = ResolvedReview(
        source_files=[Path("fixture.unknown")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
    )
    calls = []

    def runner(files, baseline_cmd, **kwargs):
        calls.append((files, baseline_cmd))
        return [], []

    machine.l2_runner = runner
    assert machine._run_l2_phase() == []
    assert calls == [(["fixture.unknown"], [])]
    assert (len(transport.requests), MockRecorder.calls, host._started) == before
    if fault:
        assert_refusal_persists(host, owner)
