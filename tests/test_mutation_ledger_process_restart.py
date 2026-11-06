# SPDX-License-Identifier: Apache-2.0
"""Sequential, clean process exits preserve State but not stale ledger authority.

The two workers are exec'd isolated Python interpreters. Mutation acquisition is
synthetic, using the existing strict fixture's physical mutmut-shaped artifacts.
Git, the native adapter/parser, ledger, State IO and StateMachine.run are real.
This does not qualify native-engine execution, crash durability, concurrent
reload, ABA binding, receipt-active host restoration or installed-wheel use.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from code_forge import machine as machine_module, mutation
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

# The parent fixture rejects Python children to keep its original controls
# bounded. Only this launcher uses the real constructor, captured before setup.
_REAL_POPEN = subprocess.Popen
_INTERPRETER_TOKEN = uuid.uuid4().hex
_CHECKOUT = Path(__file__).resolve().parents[1]


def _run_interpreter(project, phase):
    report = project.artifacts / f"{phase}.json"
    bootstrap = (
        "import runpy, sys; "
        f"sys.path[:0] = {[str(_CHECKOUT / 'src'), str(_CHECKOUT)]!r}; "
        "runpy.run_module('tests.test_mutation_ledger_process_restart', run_name='__main__')"
    )
    env = {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "PATH")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        FORGE_MUTATION_IDLE_MAX_WAIT="0",
    )
    with _REAL_POPEN(
        [sys.executable, "-I", "-B", "-c", bootstrap, str(project.root.parent), phase],
        cwd=project.state,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
        start_new_session=True,
    ) as child:
        try:
            stdout, stderr = child.communicate(timeout=60)
        except BaseException:
            # No worker is allowed to outlive this test, including on timeout.
            try:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
            except ProcessLookupError:
                pass  # It exited between communicate's exception and cleanup.
            child.communicate()
            raise
        assert child.returncode == 0, (stdout, stderr)
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["pid"] == child.pid != os.getpid()
        assert result["interpreter_token"] != _INTERPRETER_TOKEN
        assert result["isolated"] == 1
        assert result["product_source"] == str(_CHECKOUT / "src/code_forge/machine.py")
    # wait/communicate has reaped A before the caller can append a revocation.
    if os.name == "posix":
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    return result


def _assert_state(project, *, disposition, clean, survivors, history, verdict):
    path = project.state / ".code-forge/state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    saved = load_state(path)
    assert saved is not None
    assert raw["mode"] == Mode.LOCAL.value
    assert raw["verdict"] == verdict.value
    assert raw["converged"] is (verdict is Verdict.PASS)
    assert raw["consecutive_clean_rounds"] == clean
    assert raw["consecutive_survivor_rounds"] == survivors
    assert len(raw["round_history"]) == history
    matching = [finding for finding in saved.findings if finding.source == "MUTANT"]
    assert len(matching) == 1
    finding = matching[0]
    assert finding.source == "MUTANT"
    assert finding.id == "mutant-calc.x_example__mutmut_1"
    assert finding.disposition is disposition
    assert is_mutation_survivor(finding) is (disposition is Disposition.CONFIRMED)
    assert raw["findings"][0]["disposition"] == disposition.value
    assert raw["dispositions"][finding.id] == disposition.value
    assert raw["round_history"][-1]["dispositions"][finding.fingerprint] == disposition.value
    assert saved.verdict is verdict and saved.mode is Mode.LOCAL
    assert saved.consecutive_clean_rounds == clean
    assert saved.consecutive_survivor_rounds == survivors
    extras = [item for item in saved.findings if item.source != "MUTANT"]
    assert [(item.id, item.disposition) for item in extras] == (
        [("FIXVAL_SKIPPED", Disposition.DISMISSED)] if verdict is Verdict.PASS else []
    )
    assert not (project.root / "mutants").exists()
    assert (project.root / "setup.cfg").read_bytes() == SETUP
    assert (project.root / "calc.py").read_bytes() == SOURCE_A
    return raw, saved


@pytest.mark.parametrize(
    ("latest", "keep_state"),
    [("retained", True), ("escaped", True), ("unadjudicated", True), ("escaped", False)],
    ids=["retained", "escaped", "unadjudicated", "escaped-without-state"],
)
def test_exec_restart_reads_latest_ledger_after_saved_dismissal(ci_project, latest, keep_state):
    project = ci_project
    producer = _run_interpreter(project, "producer")
    assert producer["verdict"] == Verdict.ESCALATED.value
    prior, saved = _assert_state(
        project,
        disposition=Disposition.DISMISSED,
        clean=1,
        survivors=0,
        history=1,
        verdict=Verdict.ESCALATED,
    )
    assert producer["runs"] == 2  # Seed measurement, then the public LOCAL run.
    ledger_path = project.root / ".code-forge/ledger.jsonl"
    original_ledger = ledger_path.read_bytes()
    ruling = list(iter_rows(project.root, strict=True))[-1]
    assert ruling.fingerprint == saved.findings[0].fingerprint
    assert ruling.terminal_state is TerminalState.DISPROVED
    receipts = {
        path: path.read_bytes()
        for path in (project.state / ".code-forge/receipts").glob("receipt-*.json")
    }
    assert receipts
    if latest != "retained":
        terminal = TerminalState.ESCAPED if latest == "escaped" else TerminalState.UNADJUDICATED
        append_row(project.root, replace(ruling, terminal_state=terminal))
        assert ledger_path.read_bytes().startswith(original_ledger)
        assert list(iter_rows(project.root, strict=True))[-1].terminal_state is terminal
    if not keep_state:
        (project.state / ".code-forge/state.json").unlink()

    consumer = _run_interpreter(project, "consumer")
    assert consumer["interpreter_token"] != producer["interpreter_token"]
    retained = latest == "retained"
    assert consumer["verdict"] == (Verdict.PASS if retained else Verdict.FAIL).value
    rounds = 2 if retained else 3
    current, resumed = _assert_state(
        project,
        disposition=Disposition.DISMISSED if retained else Disposition.CONFIRMED,
        clean=3 if retained else 0,
        survivors=0 if retained else 3,
        history=rounds + int(keep_state),
        verdict=Verdict.PASS if retained else Verdict.FAIL,
    )
    assert consumer["runs"] == rounds
    assert current["source_hash"] == prior["source_hash"]
    assert resumed.findings[0].fingerprint == saved.findings[0].fingerprint
    assert current["round"] == prior["round"] + rounds
    if keep_state:
        assert current["round_history"][0] == prior["round_history"][0]
    assert all(path.read_bytes() == content for path, content in receipts.items())
    expected_error = "mutation: surviving mutants reported in 3 consecutive rounds"
    # A's budget message may persist; FAIL must be caused by the actual survivor limit.
    assert (expected_error in resumed.infra_errors) is (not retained)
    allowed_errors = {"ESCALATED category=A", expected_error} | {
        f"tiered-reset: P2/P3-density -- restarting cycle {cycle}" for cycle in (1, 2, 3)
    }
    assert all(error in allowed_errors for error in resumed.infra_errors)
    assert project.commands == project.launches == project.unexpected == []


def _worker(root, phase):
    """Reconstruct only paths/configuration; never receive a parent State object."""
    assert phase in {"producer", "consumer"}
    assert sys.flags.isolated == 1 and sys.flags.optimize == 0
    project = SimpleNamespace(
        root=root / "source",
        state=root / "state",
        artifacts=root / "raw-evidence" / phase,
        codes=(0,),
        statuses=("survived",),
        measurements=[],
        transport="",
        l0_hook=None,
    )
    project.artifacts.mkdir()
    runs = []
    baseline = json.loads((project.root / ".code-forge/gate.yaml").read_text())["test"]["command"]
    assert Path(baseline[0]).resolve() == Path(sys.executable).resolve()
    assert baseline[1:] == ["-c", "pass"]

    def command(argv, **kwargs):
        values = list(argv)
        assert Path(kwargs["cwd"]) == project.root
        assert kwargs["check"] is False and kwargs["timeout"] > 0
        # The exec interpreter can normalize a '/checkout/../venv' spelling;
        # the persisted gate keeps the parent's spelling of that same binary.
        assert values[0] in {baseline[0], sys.executable}
        values[0] = sys.executable
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
        return _REAL_POPEN(argv, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mutation, "run_owned_command", command)
        patch.setattr(subprocess, "Popen", git_only)
        if phase == "producer":
            assert not (project.state / ".code-forge/state.json").exists()
            findings, _, _ = _measure(project)
            append_row(project.root, _row(project, findings[0]))
        machine = _machine(project, runner=mutation.run_mutation)
        assert machine._state.findings == machine._state.round_history == []
        machine.mode = Mode.LOCAL
        machine.max_total_rounds = 1 if phase == "producer" else 3
        machine.clean_round_threshold = 3
        verdict = machine.run()
    report = {
        "pid": os.getpid(),
        "interpreter_token": _INTERPRETER_TOKEN,
        "isolated": sys.flags.isolated,
        "product_source": str(Path(machine_module.__file__).resolve()),
        "runs": len(runs),
        "verdict": verdict.value,
    }
    (root / "raw-evidence" / f"{phase}.json").write_text(json.dumps(report), encoding="utf-8")


if __name__ == "__main__":
    _worker(Path(sys.argv[1]), sys.argv[2])
