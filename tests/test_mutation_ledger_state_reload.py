# SPDX-License-Identifier: Apache-2.0
"""Latest mutation rulings cross real LOCAL State persistence boundaries.

The strict acquisition fixture supplies synthetic mutmut output; the native
adapter, Git review, ledger, State IO and StateMachine.run are real. A one-round
budget leaves a nonconverged LOCAL session for the next invocation to resume.
These are not native-engine or OS-process restart acceptance tests.
"""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from code_forge import mutation
from code_forge.disposition import Disposition
from code_forge.ledger import TerminalState, append_row
from code_forge.mutation_findings import is_mutation_survivor
from code_forge.state import Mode, Verdict, load_state
from tests.test_mutation_ledger_ci_controls import (
    SETUP,
    SOURCE_A,
    SOURCE_B,
    _machine,
    _measure,
    _payload,
    _put_result,
    _row,
    ci_project as ci_project,
)


def _local_machine(project):
    machine = _machine(project, runner=mutation.run_mutation)
    machine.mode = Mode.LOCAL
    machine.max_total_rounds = 1
    machine.clean_round_threshold = 3
    return machine


def _state_path(project):
    return project.state / ".code-forge/state.json"


def _saved(project, machine, finding, disposition, *, clean, survivors):
    """Check both raw wire values and the independently loaded State."""
    path = _state_path(project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["dispositions"][finding.id] == disposition.value
    assert raw["consecutive_clean_rounds"] == clean
    assert raw["consecutive_survivor_rounds"] == survivors
    assert raw["source_hash"] == machine.source_hash
    assert raw["mode"] == machine.mode.value
    saved = load_state(path)
    assert saved is not machine._state
    assert saved.findings == machine._state.findings
    assert saved.round_history == machine._state.round_history
    assert saved.verdict is machine._state.verdict
    assert saved.converged is (saved.verdict is Verdict.PASS)
    matching = [item for item in saved.findings if item.fingerprint == finding.fingerprint]
    assert len(matching) == 1
    assert matching[0].disposition is disposition
    assert is_mutation_survivor(matching[0]) is (disposition is Disposition.CONFIRMED)
    assert saved.dispositions[finding.id] is disposition
    assert saved.consecutive_clean_rounds == clean
    assert saved.consecutive_survivor_rounds == survivors
    assert saved.round_history[-1]["dispositions"][finding.fingerprint] == disposition.value
    assert not (project.root / "mutants").exists()
    assert (project.root / "setup.cfg").read_bytes() == SETUP
    return saved


def _start_dismissed_session(project):
    findings, _, _ = _measure(project)
    finding = findings[0]
    ruling = _row(project, finding)
    append_row(project.root, ruling)
    machine = _local_machine(project)
    assert machine.run() is Verdict.ESCALATED
    saved = _saved(project, machine, finding, Disposition.DISMISSED, clean=1, survivors=0)
    assert len(saved.round_history) == 1
    return machine, finding, ruling, saved


@pytest.mark.parametrize("resume", ["fresh", "reentry", "reload", "reentry-reload"])
@pytest.mark.parametrize("latest", ["retained", "escaped", "unadjudicated", "changed-key", "corrupt"])
def test_local_invocations_read_latest_ruling_after_persisted_dismissal(ci_project, resume, latest):
    project = ci_project
    first, finding, ruling, prior = _start_dismissed_session(project)
    receipts = {
        path: path.read_bytes()
        for path in (project.state / ".code-forge/receipts").glob("receipt-*.json")
    }
    assert receipts
    if latest == "corrupt":
        with (project.root / ".code-forge/ledger.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{broken ledger tail\n")
    elif latest != "retained":
        changes = {
            "escaped": {"terminal_state": TerminalState.ESCAPED},
            "unadjudicated": {"terminal_state": TerminalState.UNADJUDICATED},
            "changed-key": {"suppression_key": "different-reviewed-claim"},
        }[latest]
        append_row(project.root, replace(ruling, **changes))

    reload_state = resume in {"reload", "reentry-reload"}
    if not reload_state:
        # Remove only the fixture State. Ledger and mutation evidence remain.
        _state_path(project).unlink()
    machine = first if resume.startswith("reentry") else _local_machine(project)
    assert machine.run() is Verdict.ESCALATED
    dismissed = latest == "retained"
    saved = _saved(
        project,
        machine,
        finding,
        Disposition.DISMISSED if dismissed else Disposition.CONFIRMED,
        clean=(2 if reload_state else 1) if dismissed else 0,
        survivors=0 if dismissed else 1,
    )
    assert len(saved.round_history) == (2 if reload_state else 1)
    if reload_state:
        assert saved.round_history[0] == prior.round_history[0]
    # Removing State does not remove receipts or reuse their cycle numbers.
    assert saved.round == prior.round + 1
    assert all(path.read_bytes() == content for path, content in receipts.items())
    assert (project.root / "calc.py").read_bytes() == SOURCE_A
    assert project.stages.count("run") == 3  # Seed measurement and both invocations.
    assert project.launches == []


@pytest.mark.parametrize(
    "terminal", [TerminalState.DISPROVED, TerminalState.FIXED, TerminalState.DUPLICATE]
)
def test_reloaded_local_session_uses_new_terminal_ruling(ci_project, terminal):
    project = ci_project
    findings, _, _ = _measure(project)
    finding = findings[0]
    first = _local_machine(project)
    assert first.run() is Verdict.ESCALATED
    _saved(project, first, finding, Disposition.CONFIRMED, clean=0, survivors=1)
    second = _local_machine(project)
    assert second.run() is Verdict.ESCALATED
    prior = _saved(project, second, finding, Disposition.CONFIRMED, clean=0, survivors=2)
    append_row(project.root, _row(project, finding, terminal))

    resumed = _local_machine(project)
    assert resumed.run() is Verdict.ESCALATED
    saved = _saved(project, resumed, finding, Disposition.DISMISSED, clean=1, survivors=0)
    assert len(saved.round_history) == 3
    assert saved.round_history[:2] == prior.round_history
    assert project.stages.count("run") == 4


@pytest.mark.parametrize("keep_state", [True, False], ids=["reload", "fresh-state"])
def test_local_survivor_streak_only_continues_with_saved_state(ci_project, keep_state):
    project = ci_project
    findings, _, _ = _measure(project)
    finding = findings[0]
    for count in (1, 2):
        machine = _local_machine(project)
        assert machine.run() is Verdict.ESCALATED
        prior = _saved(project, machine, finding, Disposition.CONFIRMED, clean=0, survivors=count)
        assert len(prior.round_history) == count
    if not keep_state:
        _state_path(project).unlink()

    resumed = _local_machine(project)
    assert resumed.run() is (Verdict.FAIL if keep_state else Verdict.ESCALATED)
    count = 3 if keep_state else 1
    saved = _saved(project, resumed, finding, Disposition.CONFIRMED, clean=0, survivors=count)
    assert len(saved.round_history) == count
    if keep_state:
        assert saved.round_history[:2] == prior.round_history
    assert any("3 consecutive rounds" in error for error in saved.infra_errors) is keep_state
    assert project.stages.count("run") == 4


def test_matching_ruling_converges_across_local_reloads(ci_project):
    project = ci_project
    _, finding, _, prior = _start_dismissed_session(project)
    for clean in (2, 3):
        resumed = _local_machine(project)
        assert resumed.run() is (Verdict.PASS if clean == 3 else Verdict.ESCALATED)
        saved = _saved(project, resumed, finding, Disposition.DISMISSED, clean=clean, survivors=0)
        assert len(saved.round_history) == clean
        assert saved.round_history[:-1] == prior.round_history
        prior = saved
    assert project.stages.count("run") == 4


def test_changed_source_discards_prior_state_and_old_ruling(ci_project, caplog):
    project = ci_project
    _, finding, ruling, prior = _start_dismissed_session(project)
    (project.root / "calc.py").write_bytes(SOURCE_B)
    resumed = _local_machine(project)
    assert resumed.source_hash != prior.source_hash
    assert _row(project, finding).suppression_key != ruling.suppression_key

    assert resumed.run() is Verdict.ESCALATED
    saved = _saved(project, resumed, finding, Disposition.CONFIRMED, clean=0, survivors=1)
    assert len(saved.round_history) == 1
    assert "discarding prior state.json" in caplog.text
    assert (project.root / "calc.py").read_bytes() == SOURCE_B
    assert project.stages.count("run") == 3


def test_reloaded_dismissal_requires_current_native_measurement(ci_project):
    project = ci_project
    _, finding, _, prior = _start_dismissed_session(project)
    calls = []

    def custom_runner(*_args, **_kwargs):
        calls.append(True)
        survivor = deepcopy(finding)
        survivor.disposition = Disposition.CONFIRMED
        return [survivor], []

    resumed = _local_machine(project)
    resumed.l2_runner = custom_runner
    assert resumed.run() is Verdict.ESCALATED
    saved = _saved(project, resumed, finding, Disposition.CONFIRMED, clean=0, survivors=1)
    assert len(saved.round_history) == 2
    assert saved.round_history[0] == prior.round_history[0]
    assert calls == [True]
    assert resumed._mutation_measurement_eligible is False
    assert project.stages.count("run") == 2  # No new native measurement.


@pytest.mark.parametrize("revoked", [False, True], ids=["retained", "revoked"])
def test_ci_ignores_local_state_but_reads_latest_ledger(ci_project, revoked, caplog):
    project = ci_project
    _, finding, ruling, _ = _start_dismissed_session(project)
    payload, _ = _payload(project)
    _put_result(project, payload)
    if revoked:
        append_row(project.root, replace(ruling, terminal_state=TerminalState.UNADJUDICATED))

    machine = _machine(project)
    verdict = machine.run()
    assert verdict is (Verdict.FAIL if revoked else Verdict.PASS)
    saved = load_state(_state_path(project))
    assert saved.mode is Mode.CI
    assert saved.verdict is verdict
    assert saved.converged is (not revoked)
    assert saved.consecutive_clean_rounds == 0
    assert saved.consecutive_survivor_rounds == 0
    assert len(saved.round_history) == 1
    matching = [item for item in saved.findings if item.fingerprint == finding.fingerprint]
    assert len(matching) == 1
    assert matching[0].disposition is (Disposition.CONFIRMED if revoked else Disposition.DISMISSED)
    assert "ignoring prior state.json in CI mode" in caplog.text
    assert project.launches == []
