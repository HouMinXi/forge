# SPDX-License-Identifier: Apache-2.0
"""Recover real State and ledger after SIGKILL at completed-write boundaries.

Each _worker is a fresh isolated interpreter. Mutation acquisition is synthetic,
using the existing strict fixture's physical mutmut-shaped artifacts; Git, the
native adapter/parser, ledger, State IO and StateMachine.run remain real.

The selected save has completed state.tmp before replace, or completed replace
before save_state returns. This does not qualify arbitrary interruption, power
loss/fsync durability, concurrent writers, ABA, actual mutation-engine crashes,
receipt-active host restoration, installed-wheel execution or hosted CI.
"""

from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from code_forge import machine as machine_module, mutation, mutation_findings, state as state_module
from code_forge.disposition import Disposition
from code_forge.ledger import TerminalState, append_row, iter_rows
from code_forge.mutation_findings import is_mutation_survivor
from code_forge.state import Mode, Verdict, load_state
from tests.test_mutation_ledger_ci_controls import (
    BASELINE,
    NATIVE_PREFIX,
    PROBE,
    SETUP,
    SOURCE_A,
    _machine,
    _measure,
    _row,
    _write_physical_measurement,
    ci_project as ci_project,
)

_CHECKOUT = Path(__file__).resolve().parents[1]
_REAL_POPEN = subprocess.Popen
_TOKEN = uuid.uuid4().hex
_SCRIPT = Path(__file__).resolve()
pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX SIGSTOP/SIGKILL and waitpid")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _observed(path):
    data = path.read_bytes()
    state = load_state(path)
    assert state is not None
    return {
        "sha256": _digest(data),
        "size": len(data),
        "round": state.round,
        "history": len(state.round_history),
        "clean": state.consecutive_clean_rounds,
        "survivors": state.consecutive_survivor_rounds,
        "verdict": state.verdict.value,
        "findings": [(f.id, f.disposition.value) for f in state.findings],
    }


def _assert_reaped(child):
    assert child.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


def _kill_reap(child):
    if child.returncode is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=10)
    _assert_reaped(child)


def _launch(project, phase, checkpoint="none", *, failure=None, negative="none"):
    root = project.root.parent
    artifacts = root / "raw-evidence"
    log = artifacts / f"{phase}.log"
    bootstrap = (
        "import runpy,sys; "
        f"sys.path[:0] = {[str(_CHECKOUT / 'src'), str(_CHECKOUT)]!r}; "
        f"runpy.run_path({str(_SCRIPT)!r}, run_name='__main__')"
    )
    env = {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "PATH")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        FORGE_MUTATION_IDLE_MAX_WAIT="0",
    )
    with (
        log.open("wb") as output,
        _REAL_POPEN(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                bootstrap,
                str(root),
                phase,
                checkpoint,
                negative,
            ],
            cwd=project.state,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        ) as child,
    ):
        process = {"pid": child.pid, "phase": phase, "checkpoint": checkpoint}
        try:
            if checkpoint in {"before", "after"}:
                deadline = time.monotonic() + 60
                while True:
                    waited, status = os.waitpid(child.pid, os.WNOHANG | os.WUNTRACED)
                    if waited:
                        if not os.WIFSTOPPED(status):
                            child.returncode = os.waitstatus_to_exitcode(status)
                            raise AssertionError(f"_worker exited before stop: {log.read_text()}")
                        assert os.WSTOPSIG(status) == signal.SIGSTOP
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("_worker did not reach the bounded State checkpoint")
                    time.sleep(0.01)
                marker = _read_json(artifacts / "checkpoint.json")
                assert marker["pid"] == child.pid
                assert marker["checkpoint"] == checkpoint
                assert marker["caller_name"] == "save_state"
                assert marker["caller_file"] == str(_CHECKOUT / "src/code_forge/state.py")
                assert marker["save_has_returned"] is False
                process.update(stopped_signal=os.WSTOPSIG(status), checkpoint_verified=True)
                path = project.state / ".code-forge/state.json"
                tmp = path.with_suffix(".tmp")
                old = (artifacts / "checkpoint-old.json").read_bytes()
                new = (artifacts / "checkpoint-new.json").read_bytes()
                assert old != new
                assert _digest(old) == marker["old"]["sha256"]
                assert _digest(new) == marker["new"]["sha256"]
                assert path.read_bytes() == (old if checkpoint == "before" else new)
                assert tmp.exists() is (checkpoint == "before")
                if tmp.exists():
                    assert tmp.read_bytes() == new
                if failure == "assertion":
                    raise RuntimeError("intentional parent assertion-path cleanup control")
                if failure == "timeout":
                    child.wait(timeout=0.05)
                    raise AssertionError("stopped _worker unexpectedly returned")
                _kill_reap(child)
                assert child.returncode == -signal.SIGKILL
                assert not (artifacts / f"{phase}-returned.json").exists()
                assert not (artifacts / f"{phase}-finally.json").exists()
                process.update(returncode=child.returncode, reaped=True, finally_ran=False)
                _write_json(artifacts / f"{phase}-process.json", process)
                return process
            child.wait(timeout=60)
            assert child.returncode == 0, log.read_text()
            _assert_reaped(child)
            report = _read_json(artifacts / f"{phase}-returned.json")
            assert report["pid"] == child.pid != os.getpid()
            assert report["token"] != _TOKEN
            assert report["isolated"] == 1
            assert report["product_source"] == str(_CHECKOUT / "src/code_forge/machine.py")
            assert (artifacts / f"{phase}-finally.json").exists()
            process.update(returncode=child.returncode, reaped=True, finally_ran=True)
            _write_json(artifacts / f"{phase}-process.json", process)
            return report
        except BaseException as error:
            _kill_reap(child)
            process.update(returncode=child.returncode, reaped=True, failure=type(error).__name__)
            _write_json(artifacts / f"{phase}-cleanup.json", process)
            raise


def _assert_workspace(project):
    assert not (project.root / "mutants").exists()
    assert (project.root / "setup.cfg").read_bytes() == SETUP
    assert (project.root / "calc.py").read_bytes() == SOURCE_A
    assert project.commands == project.launches == project.unexpected == []


def _exercise_recovery(project, checkpoint, latest, *, negative="none"):
    artifacts = project.artifacts
    state_path = project.state / ".code-forge/state.json"
    ledger_path = project.root / ".code-forge/ledger.jsonl"
    seed = _launch(project, "seed")
    assert seed["verdict"] == Verdict.ESCALATED.value
    assert seed["runs"] == 2
    assert seed["after"]["clean"] == 1
    seed_state = load_state(state_path)
    assert len(seed_state.findings) == 1
    assert seed_state.findings[0].disposition is Disposition.DISMISSED
    original_ledger = ledger_path.read_bytes()
    original_ruling = list(iter_rows(project.root, strict=True))[-1]
    assert original_ruling.terminal_state is TerminalState.DISPROVED
    writer = _launch(project, "writer", checkpoint)
    marker = _read_json(artifacts / "checkpoint.json")
    old_bytes = (artifacts / "checkpoint-old.json").read_bytes()
    new_bytes = (artifacts / "checkpoint-new.json").read_bytes()
    assert marker["old"]["clean"] == 1 and marker["new"]["clean"] == 2
    assert marker["old"]["history"] == marker["new"]["history"] == 2
    assert marker["old"]["verdict"] == marker["new"]["verdict"] == Verdict.PENDING.value
    durable = state_path.read_bytes()
    if checkpoint == "before":
        assert durable == old_bytes
    elif checkpoint == "after":
        assert durable == new_bytes
    else:
        assert writer["verdict"] == Verdict.ESCALATED.value
        assert writer["after"]["clean"] == 2
        assert writer["after"]["sha256"] == _digest(durable)
        assert not state_path.with_suffix(".tmp").exists()
    prior = _read_json(state_path)
    saved = load_state(state_path)
    assert saved.findings[0].disposition is Disposition.DISMISSED
    assert saved.findings[0].fingerprint == seed_state.findings[0].fingerprint
    _assert_workspace(project)
    assert ledger_path.read_bytes() == original_ledger
    # This happens only after the writer was reaped, never while it is alive.
    if latest != "retained":
        terminal = TerminalState.ESCAPED if latest == "escaped" else TerminalState.UNADJUDICATED
        append_row(project.root, replace(original_ruling, terminal_state=terminal))
        assert ledger_path.read_bytes().startswith(original_ledger)
    else:
        terminal = TerminalState.DISPROVED
    assert list(iter_rows(project.root, strict=True))[-1].terminal_state is terminal
    before_recovery_ledger = ledger_path.read_bytes()
    before_receipts = {
        path: path.read_bytes()
        for path in (project.state / ".code-forge/receipts").glob("receipt-*.json")
    }
    assert before_receipts
    recovery = _launch(project, "recovery", negative=negative)
    assert recovery["before"]["sha256"] == _digest(durable)
    assert recovery["ledger_before_sha256"] == _digest(before_recovery_ledger)
    assert recovery["ledger_latest"] == terminal.value
    assert len({seed["token"], marker["token"], recovery["token"]}) == 3
    current = _read_json(state_path)
    resumed = load_state(state_path)
    retained = latest == "retained"
    assert recovery["verdict"] == (Verdict.PASS if retained else Verdict.FAIL).value, (
        "latest ledger ruling must determine the recovery verdict"
    )
    rounds = 3 - saved.consecutive_clean_rounds if retained else 3
    assert recovery["runs"] == rounds, "State recovery must preserve the earned round count"
    assert resumed.round == saved.round + rounds
    assert len(resumed.round_history) == len(saved.round_history) + rounds
    assert current["round_history"][: len(prior["round_history"])] == prior["round_history"]
    assert current["source_hash"] == prior["source_hash"]
    matching = [finding for finding in resumed.findings if finding.source == "MUTANT"]
    assert len(matching) == 1
    finding = matching[0]
    expected = Disposition.DISMISSED if retained else Disposition.CONFIRMED
    assert finding.disposition is expected
    assert finding.fingerprint == seed_state.findings[0].fingerprint
    assert is_mutation_survivor(finding) is (not retained)
    assert current["dispositions"][finding.id] == expected.value
    assert resumed.consecutive_clean_rounds == (3 if retained else 0)
    assert resumed.consecutive_survivor_rounds == (0 if retained else 3)
    assert resumed.converged is retained
    expected_error = "mutation: surviving mutants reported in 3 consecutive rounds"
    assert (expected_error in resumed.infra_errors) is (not retained)
    for item in resumed.round_history[len(saved.round_history) :]:
        assert item["dispositions"][finding.fingerprint] == expected.value
    assert all(path.read_bytes() == content for path, content in before_receipts.items())
    assert not state_path.with_suffix(".tmp").exists()
    _assert_workspace(project)
    summary = {
        "checkpoint": checkpoint,
        "latest": latest,
        "writer_returncode": _read_json(artifacts / "writer-process.json")["returncode"],
        "old_snapshot": marker["old"],
        "new_snapshot": marker["new"],
        "recovered_before": recovery["before"],
        "final": recovery["after"],
        "recovery_measurements": recovery["runs"],
        "workspace_restored": True,
        "receipts_preserved": True,
        "new_interpreter": True,
    }
    _write_json(artifacts / "case-summary.json", summary)


@pytest.mark.parametrize("checkpoint", ["before", "after", "normal"])
@pytest.mark.parametrize("latest", ["retained", "escaped", "unadjudicated"])
def test_sigkill_boundary_recovery(ci_project, checkpoint, latest):
    _exercise_recovery(ci_project, checkpoint, latest)


@pytest.mark.parametrize(
    ("defect", "latest", "message"),
    [
        ("first-ruling", "escaped", "latest ledger ruling must determine"),
        ("omit-state", "retained", "State recovery must preserve"),
    ],
    ids=["latest-ruling", "state-load"],
)
def test_recovery_oracles_reject_isolated_defects(ci_project, defect, latest, message):
    # Faults exist only in the recovery interpreter. Require the precise semantic
    # assertion, not just any child/fixture error, to demonstrate sensitivity.
    with pytest.raises(AssertionError, match=message):
        _exercise_recovery(ci_project, "after", latest, negative=defect)
    report = _read_json(ci_project.artifacts / "recovery-returned.json")
    assert report["negative_control"] == defect
    if defect == "first-ruling":
        assert report["ledger_latest"] == TerminalState.ESCAPED.value
        assert report["verdict"] == Verdict.PASS.value
    else:
        assert report["before"]["clean"] == 2
        assert report["runs"] == 3
    for phase in ("seed", "writer", "recovery"):
        process = _read_json(ci_project.artifacts / f"{phase}-process.json")
        assert process["reaped"] is True
        assert process["returncode"] == (-signal.SIGKILL if phase == "writer" else 0)
    _assert_workspace(ci_project)


@pytest.mark.parametrize("failure", ["assertion", "timeout"])
def test_failure_cleanup_reaps_stopped_writer(ci_project, failure):
    _launch(ci_project, "seed")
    exception = RuntimeError if failure == "assertion" else subprocess.TimeoutExpired
    with pytest.raises(exception):
        _launch(ci_project, "writer", "before", failure=failure)
    report = _read_json(ci_project.artifacts / "writer-cleanup.json")
    assert report["returncode"] == -signal.SIGKILL
    assert report["reaped"] is report["checkpoint_verified"] is True
    assert not (ci_project.artifacts / "writer-returned.json").exists()
    assert not (ci_project.artifacts / "writer-finally.json").exists()
    _assert_workspace(ci_project)


def _worker(root, phase, checkpoint, negative):
    assert sys.flags.isolated == 1 and sys.flags.optimize == 0
    assert negative in {"none", "first-ruling", "omit-state"}
    artifacts = root / "raw-evidence"
    project = SimpleNamespace(
        root=root / "source",
        state=root / "state",
        artifacts=artifacts / phase,
        codes=(0,),
        statuses=("survived",),
        measurements=[],
        transport="",
        l0_hook=None,
    )
    project.artifacts.mkdir()
    state_path = project.state / ".code-forge/state.json"
    ledger_path = project.root / ".code-forge/ledger.jsonl"
    before = _observed(state_path) if state_path.exists() else None
    ledger_before = _digest(ledger_path.read_bytes()) if ledger_path.exists() else None
    rows_before = list(iter_rows(project.root, strict=True))
    baseline = _read_json(project.root / ".code-forge/gate.yaml")["test"]["command"]
    assert Path(baseline[0]).resolve() == Path(sys.executable).resolve()
    assert baseline[1:] == ["-c", "pass"]
    runs = []
    git_children = []
    accepted_commands = []
    real_replace = Path.replace
    intercepted = False

    def command(argv, **kwargs):
        values = list(argv)
        assert Path(kwargs["cwd"]) == project.root
        assert kwargs["check"] is False and kwargs["timeout"] > 0
        assert values[0] in {baseline[0], sys.executable}
        values[0] = sys.executable
        accepted_commands.append(values)
        if values in (BASELINE, PROBE):
            return subprocess.CompletedProcess(values, 0, "", "")
        if values == NATIVE_PREFIX + ["run", "--max-children", "1"]:
            assert "only_mutate=calc.py\n" in (project.root / "setup.cfg").read_text()
            runs.append(values)
            _write_physical_measurement(project, project.root)
            return subprocess.CompletedProcess(values, 0, "", "")
        assert values == NATIVE_PREFIX + ["results", "--all", "true"], values
        return subprocess.CompletedProcess(values, 0, project.transport, "")

    def git_only(argv, *args, **kwargs):
        assert isinstance(argv, (list, tuple)) and argv[0] == "git", argv
        child = _REAL_POPEN(argv, *args, **kwargs)
        git_children.append(child)
        return child

    def git_lifecycle():
        assert git_children
        for child in git_children:
            _assert_reaped(child)
        return [
            {"pid": child.pid, "returncode": child.returncode, "reaped": True} for child in git_children
        ]

    def boundary(source, target):
        nonlocal intercepted
        if source != state_path.with_suffix(".tmp") or Path(target) != state_path:
            return real_replace(source, target)
        payload = _read_json(source)
        if intercepted or payload["consecutive_clean_rounds"] != 2:
            return real_replace(source, target)
        assert payload["verdict"] == Verdict.PENDING.value
        assert len(payload["round_history"]) == 2
        assert len(runs) == 1
        caller = inspect.currentframe().f_back
        assert caller.f_code is state_module.save_state.__code__
        assert caller.f_locals["tmp"] == source
        assert caller.f_locals["path"] == target
        old = state_path.read_bytes()
        new = source.read_bytes()
        assert old != new
        (artifacts / "checkpoint-old.json").write_bytes(old)
        (artifacts / "checkpoint-new.json").write_bytes(new)
        marker = {
            "pid": os.getpid(),
            "token": _TOKEN,
            "checkpoint": checkpoint,
            "caller_name": caller.f_code.co_name,
            "caller_file": caller.f_code.co_filename,
            "caller_line": caller.f_lineno,
            "save_has_returned": False,
            "old": _observed(state_path),
            "new": _observed(source),
            "git_children": git_lifecycle(),
            "accepted_synthetic_commands": accepted_commands,
        }
        intercepted = True
        if checkpoint == "after":
            real_replace(source, target)
            assert not source.exists() and state_path.read_bytes() == new
        _write_json(artifacts / "checkpoint.json", marker)
        if checkpoint in {"before", "after"}:
            os.kill(os.getpid(), signal.SIGSTOP)
            raise AssertionError("a SIGKILL checkpoint must never resume")
        return real_replace(source, target)

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(mutation, "run_owned_command", command)
            patch.setattr(subprocess, "Popen", git_only)
            if phase == "seed":
                assert before is None
                findings, _, _ = _measure(project)
                append_row(project.root, _row(project, findings[0]))
            if phase == "writer":
                patch.setattr(Path, "replace", boundary)
            if phase == "recovery" and negative == "first-ruling":
                # Deliberate in-memory defect control; never edits the checkout.
                real_iter_rows = mutation_findings.iter_rows
                patch.setattr(
                    mutation_findings,
                    "iter_rows",
                    lambda *args, **kwargs: iter(reversed(list(real_iter_rows(*args, **kwargs)))),
                )
            if phase == "recovery" and negative == "omit-state":
                patch.setattr(machine_module, "load_state", lambda _path: None)
            machine = _machine(project, runner=mutation.run_mutation)
            assert machine._state.findings == machine._state.round_history == []
            machine.mode = Mode.LOCAL
            machine.max_total_rounds = 3 if phase == "recovery" else 1
            machine.clean_round_threshold = 3
            verdict = machine.run()
        if phase == "writer":
            assert checkpoint == "normal" and intercepted
        _write_json(
            artifacts / f"{phase}-returned.json",
            {
                "pid": os.getpid(),
                "token": _TOKEN,
                "isolated": sys.flags.isolated,
                "product_source": str(Path(machine_module.__file__).resolve()),
                "verdict": verdict.value,
                "runs": len(runs),
                "before": before,
                "after": _observed(state_path),
                "ledger_before_sha256": ledger_before,
                "ledger_latest": rows_before[-1].terminal_state.value if rows_before else None,
                "git_children": git_lifecycle(),
                "accepted_synthetic_commands": accepted_commands,
                "negative_control": negative,
            },
        )
    finally:
        _write_json(artifacts / f"{phase}-finally.json", {"pid": os.getpid()})


if __name__ == "__main__":
    _worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
