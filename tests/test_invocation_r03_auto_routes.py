"""Actual LOCAL run loop with mock HTTP/storage and empty command runners.

No native recovery or external provider qualification. Receipt production and
acceptance remain real; no phase or round-loop method is patched.
"""

import pytest

from code_forge.source import compute_source_hash
from code_forge.state import Mode, State, Verdict, load_state
from tests import _review_harness
from tests.test_invocation_host import MockRecorder
from tests.test_invocation_r03_review_routes import (
    DIFF,
    PASSES,
    _Transport,
    assert_refusal_persists,
    inject,
    make_machine,
)

pytest_plugins = ["tests.test_invocation_host"]
offline_workspace = _review_harness.offline_workspace


@pytest.mark.parametrize("fault", [False, True], ids=["healthy", "post-ack-diagnostic-failure"])
def test_local_run_automatic_rounds_preserve_default_audit_refusal(
    setup,
    offline_workspace,
    monkeypatch,
    default_l1_dispatch,
    fault,
):
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    root = offline_workspace
    (root / "sample.py").write_text("x = 2\n")
    host, notices = setup[0](control_timeout_s=2)
    machine, _ = make_machine(root, monkeypatch, host)
    machine.mode = Mode.LOCAL
    machine._state = State()
    machine.source_hash = compute_source_hash(git_diff=DIFF)
    machine._state.env_manifest = {}  # No environment/toolchain discovery in this fixture.
    machine.max_total_rounds = 2
    machine.clean_round_threshold = 3
    machine.l0_runner = lambda *args: ([], [])
    owner = inject(host, monkeypatch, "lease_list_after", True) if fault else None
    transport = _Transport(monkeypatch)
    observed = []

    def after_round(index):
        observed.append((index, len(transport.exchanges), host._started))
        if fault:
            assert_refusal_persists(host, owner)

    machine.post_round_hook = after_round
    assert machine.coverage_l1_active is True
    result = machine.run()
    assert result is Verdict.ESCALATED  # Two clean cycles, below the required three.
    assert observed == [(0, 6, 0 if fault else 6), (1, 12, 0 if fault else 12)]
    assert transport.counts == {**dict.fromkeys(PASSES, 2), "falsify": 6}
    loaded = load_state(root / ".code-forge/state.json")
    assert loaded is not None and loaded.verdict is Verdict.ESCALATED
    assert [row["round"] for row in loaded.round_history] == [0, 1]
    # Ordinary review receipt credit is independent of optional invocation audit.
    # Real receipt publication/acceptance ran unchanged; do not conflate credits.
    assert [row["clean_credit_action"] for row in loaded.round_history] == ["earned", "earned"]
    assert loaded.consecutive_clean_rounds == 2
    assert all(row["phase_status"]["l1"] == "returned" for row in loaded.round_history)
    if fault:
        assert_refusal_persists(host, owner)
        assert notices == ["invocation_audit_unavailable"]
    else:
        assert not host.failed and notices == []
        assert host._started == len(transport.exchanges)
        assert sum(len(recorder.finished) for recorder in MockRecorder.instances) == host._started
