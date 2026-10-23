# SPDX-License-Identifier: Apache-2.0
"""Source-only registration of bounded, synthetic-acquisition CI controls.

Execution is HOLD until a qualified runner and the successor manifest/ordered
roster are approved. Authoring or parsing this file is not collection, a GREEN,
a native-engine qualification, or a detached-lifecycle qualification.

The real entrypoints are StateMachine.run(Mode.CI) and cli.main -> _run ->
_run_hold_loop. The only mutation acquisition replacement is run_owned_command:
exact baseline, interpreter import probe, run, and results argv are accepted.
Physical generated Python/.meta files and transport bytes feed the real parser,
workspace, carrier, ledger, receipt, merge, verdict, and persistence code.
L0 fixture callbacks are observation/adversarial setup, never CI autofix.
A real L1 provider uses synthetic transport only in receipt controls. CLI runtime
advisory acquisition has its own explicitly empty synthetic transport.

The CLI argv below is supported by _build_parser. Its fixture supplies an
isolated HOME/XDG directory, a trusted CLI-shaped backend selected by environment
(no credentials or model invocation), an explicit subprocess outlet, a nonempty
nonmatching tool registry, calc.py coverage exemption, and a real Git repository.
A PATH discovery marker selects the exact native factory. Detached launch is
captured at Popen with descriptor/session/cwd/argv checks; its generated payload
is parsed as source and NEVER executed by this module. Git children are real.
No test replaces the CLI dispatcher, StateMachine, receipt gates, disposition or
carrier decoder, native source guards, or final verdict.
"""

import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from code_forge import cli, mutation
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import GitRefBaseline, resolve_baseline
from code_forge.disposition import Disposition
from code_forge.factories import build_l1_provider, build_l2_runner
from code_forge.falsify import StubFalsifier
from code_forge.ledger import (
    LedgerRow, TerminalState, append_row, finding_suppression_key,
    resolve_ledger_root, iter_rows,
)
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.mutation_findings import (
    decode_mutation_survivor_details, is_mutation_survivor,
    mutation_review_context,
)
from code_forge.source import compute_source_hash
from code_forge.state import Mode, StateFinding, Verdict, load_state
from code_forge.trust import record_trust


REVIEW_ARGV = [
    "code-forge", "review", "--mode", "ci", "--falsification-engine", "stub",
    "--baseline", "HEAD", "--head", "WORKING", "--allow-main", "calc.py",
]
TIMEOUT_CODES = (36, -24, 24, 152, 255)
SOURCE_A = b"def example(value):\n    return value + 2\n"
SOURCE_B = b"def example(value):\n    return value + 3\n"
SOURCE_BASE = b"def example(value):\n    return value + 1\n"
SETUP = b"[unrelated]\nvalue=preserve\n"
BASELINE = [sys.executable, "-c", "pass"]
PROBE = [sys.executable, "-c", "import mutmut"]
NATIVE_PREFIX = [sys.executable, "-m", "mutmut"]


def _identity(number):
    return f"calc.x_example__mutmut_{number}"


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _review(project):
    return resolve_baseline(
        GitRefBaseline("HEAD"), GitRefBaseline("WORKING"),
        [Path("calc.py")], project.root,
    )


def _context(project, review=None):
    review = _review(project) if review is None else review
    return mutation_review_context(
        source_root=project.root, base_sha=review.base_sha,
        head_sha=review.head_sha, git_diff=review.git_diff,
    )


def _write_physical_measurement(project, cwd):
    """Fixture truth is independent of the product's exit/status mapping."""
    assert len(project.codes) == len(project.statuses)
    mirror = cwd / "mutants/calc.py"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    generated = "\n".join(
        f"def x_example__mutmut_{i}(value):\n    return value + {i}\n"
        for i in range(1, len(project.codes) + 1)
    ).encode("utf-8")
    metadata = json.dumps({
        "exit_code_by_key": {
            _identity(i): code for i, code in enumerate(project.codes, 1)
        },
        "durations_by_key": {}, "estimated_durations_by_key": {},
        "type_check_error_by_key": {},
    }).encode("utf-8")
    transport = "".join(
        f"    {_identity(i)}: {status}\n"
        for i, status in enumerate(project.statuses, 1)
    ).encode("utf-8")
    mirror.write_bytes(generated)
    mirror.with_name("calc.py.meta").write_bytes(metadata)
    artifact = project.artifacts / f"measurement-{len(project.measurements):03d}"
    artifact.mkdir()
    (artifact / "calc.py").write_bytes(generated)
    (artifact / "calc.py.meta").write_bytes(metadata)
    (artifact / "results.txt").write_bytes(transport)
    project.measurements.append(artifact)
    project.transport = transport.decode("utf-8")


@pytest.fixture
def ci_project(tmp_path, monkeypatch):
    """No child except real Git and a captured exact async launch is admitted."""
    git = shutil.which("git")
    assert git is not None, "qualified runner must provide Git"
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(git)
    for name in tuple(os.environ):
        if name.startswith(("FORGE_", "CODE_FORGE_", "GIT_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("FORGE_OUTLET", "subprocess")
    monkeypatch.setenv("FORGE_BACKEND", "recorded-ci-control")
    root = tmp_path / "source"
    root.mkdir()
    (root / "calc.py").write_bytes(SOURCE_BASE)
    _git(root, "init", "-q")
    _git(root, "add", "calc.py")
    _git(root, "-c", "user.name=CI Control", "-c",
         "user.email=ci-control@example.invalid", "commit", "-qm", "fixture base")
    state = tmp_path / "state"
    _git(root, "worktree", "add", "--detach", str(state), "HEAD")
    gate = {
        "test": {"command": BASELINE, "timeout_seconds": 120,
                 "mutation_max_children": 1},
        "backends": {"recorded-ci-control": {
            "type": "cli", "command": "never-invoke-recorded-backend",
        }},
    }
    for directory in (root, state):
        (directory / "calc.py").write_bytes(SOURCE_A)
        (directory / "setup.cfg").write_bytes(SETUP)
        (directory / "requirements.txt").write_text("# Declared empty fixture environment.\n", encoding="utf-8")
        config = directory / ".code-forge"
        config.mkdir()
        # JSON is valid YAML; record_trust and the real loaders see the same data.
        (config / "gate.yaml").write_text(json.dumps(gate), encoding="utf-8")
        (config / "coverage.yaml").write_text(
            json.dumps({"version": 1, "exempt_patterns": ["calc.py"]}),
            encoding="utf-8",
        )
        (config / "tools.yaml").write_text(json.dumps({"tools": {"nonmatching": {
            "command": "never-invoke-linter", "output_format": "sarif",
            "file_patterns": ["*.not-in-this-review"],
        }}}), encoding="utf-8")
        record_trust(config / "gate.yaml", gate)
    artifacts = tmp_path / "raw-evidence"
    artifacts.mkdir()
    project = SimpleNamespace(
        root=root, state=state, artifacts=artifacts, bin_dir=bin_dir,
        codes=(0,), statuses=("survived",), commands=[], stages=[],
        measurements=[], transport="", launches=[], unexpected=[],
        baseline_hook=None, results_hook=None, l0_hook=None, fault=None,
        advisory_calls=[], git_pids=[], launch_error=None,
    )
    real_popen = subprocess.Popen

    def capture_popen(argv, *args, **kwargs):
        values = list(argv) if isinstance(argv, (list, tuple)) else argv
        if isinstance(values, list) and values and values[0] == "git":
            child = real_popen(argv, *args, **kwargs)
            project.git_pids.append(child.pid)
            return child
        if (isinstance(values, list) and len(values) == 3
                and values[:2] == [sys.executable, "-c"]
                and isinstance(values[2], str) and "os.fork()" in values[2]):
            assert args == ()
            assert Path(kwargs["cwd"]) == project.root
            assert kwargs["close_fds"] is True
            assert kwargs["start_new_session"] is True
            assert all(kwargs[name] == subprocess.DEVNULL
                       for name in ("stdin", "stdout", "stderr"))
            ast.parse(values[2])
            project.launches.append((values, dict(kwargs)))
            if project.launch_error is not None:
                assert isinstance(project.launch_error, OSError)
                raise project.launch_error
            # Captures launch plumbing only. There is no detached child.
            return SimpleNamespace(pid=os.getpid(), wait=lambda timeout: 0)
        project.unexpected.append((values, dict(kwargs)))
        raise AssertionError(f"unexpected child process: {values!r}")

    def command(argv, **kwargs):
        values = list(argv)
        project.commands.append((values, dict(kwargs)))
        assert Path(kwargs["cwd"]) == project.root
        assert kwargs["check"] is False
        assert kwargs["timeout"] > 0
        if values == BASELINE:
            project.stages.append("baseline")
            if project.baseline_hook is not None:
                project.baseline_hook()
            return subprocess.CompletedProcess(values, 0, "", "")
        if values == PROBE:
            project.stages.append("probe")
            return subprocess.CompletedProcess(values, 0, "", "")
        if values == NATIVE_PREFIX + ["run", "--max-children", "1"]:
            project.stages.append("run")
            assert "only_mutate=calc.py\n" in (project.root / "setup.cfg").read_text()
            if project.fault == "deadline":
                raise subprocess.TimeoutExpired(values, kwargs["timeout"])
            if project.fault == "run-error":
                return subprocess.CompletedProcess(values, 2, "", "RECORDED_RUN_FAILURE")
            _write_physical_measurement(project, Path(kwargs["cwd"]))
            return subprocess.CompletedProcess(values, 0, "", "")
        if values == NATIVE_PREFIX + ["results", "--all", "true"]:
            project.stages.append("results")
            if project.results_hook is not None:
                project.results_hook()
            output = project.transport
            if project.fault == "missing-coverage":
                output = "".join(output.splitlines(keepends=True)[1:])
            return subprocess.CompletedProcess(values, 0, output, "")
        project.unexpected.append((values, dict(kwargs)))
        raise AssertionError(f"unexpected owned command: {values!r}")

    monkeypatch.setattr(mutation, "run_owned_command", command)
    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    yield project
    (artifacts / "substitutions.json").write_text(json.dumps({
        "owned_commands": [{"argv": argv, "options": {
            key: kwargs[key] for key in ("cwd", "timeout", "capture_output", "text", "check", "memory_limit_bytes")
            if key in kwargs
        }} for argv, kwargs in project.commands],
        "stages": project.stages,
        "captured_launch_count": len(project.launches),
        "targeted_launch_error": str(project.launch_error) if project.launch_error else None,
        "advisory_transport_calls": len(project.advisory_calls),
        "unexpected_commands": project.unexpected,
    }, default=str), encoding="utf-8")
    assert project.unexpected == [], "unexpected acquisition cannot be a passing control"


def _discover_mutmut(project):
    """PATH marker is never executed; exact acquisition is replaced above."""
    marker = project.bin_dir / "mutmut"
    marker.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    marker.chmod(0o700)


def _machine(project, *, runner=None, l0=None, review=None, active_l1=False):
    review = _review(project) if review is None else review
    provider = build_l1_provider("auto" if active_l1 else "stub", review, max_attempts=1)

    def observe_l0(*_args):
        if project.l0_hook is not None:
            project.l0_hook()
        return ([], [])

    values = dict(
        mode=Mode.CI, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda _finding: None, resolved_review=review,
        source_hash=compute_source_hash(git_diff=review.git_diff or ""),
        baseline_spec_repr="HEAD..WORKING", cwd=project.state,
        source_root=project.root, registry={}, l0_runner=l0 or observe_l0,
        l1_provider=provider, coverage_l1_active=active_l1,
        coverage_exempt_patterns=["calc.py"],
    )
    if runner is not None:
        values["l2_runner"] = runner
    machine = StateMachine(**values)
    machine._state.env_manifest = {}
    return machine


def _measure(project):
    proof = {}
    findings, errors = mutation.run_mutation(
        ["calc.py"], BASELINE, cwd=project.root, max_children=1, _evidence=proof,
    )
    assert not errors
    assert proof["completed_measurement"] is True
    artifact = project.measurements[-1]
    assert proof["metadata_sha256"] == {
        "calc.py": hashlib.sha256((artifact / "calc.py.meta").read_bytes()).hexdigest(),
    }
    assert proof["results_sha256"] == hashlib.sha256(
        (artifact / "results.txt").read_bytes(),
    ).hexdigest()
    assert not (project.root / "mutants").exists()
    assert (project.root / "setup.cfg").read_bytes() == SETUP
    return findings, errors, proof


def _payload(project):
    findings, errors, proof = _measure(project)
    return mutation._mutation_outcome(
        findings, errors, proof, review_context=_context(project),
    ), findings


def _row(project, finding, terminal=TerminalState.DISPROVED):
    review = _review(project)
    line = finding.line_range[0] if finding.line_range else 0
    return LedgerRow(
        fingerprint=finding.fingerprint,
        repo_root=str(resolve_ledger_root(project.root).resolve()),
        base_sha=review.base_sha, head_sha=review.head_sha,
        file=finding.file, line=line, axis_claim="mutation",
        pass_provenance="adjudicated", terminal_state=terminal,
        evidence_class="exact synthetic-acquisition control",
        ts="2026-10-12T00:00:00Z",
        suppression_key=finding_suppression_key(
            base_sha=review.base_sha, head_sha=review.head_sha,
            git_diff=review.git_diff, file=finding.file, line=line,
            source=finding.source, description=finding.description,
        ),
    )


def _rule(project, findings, terminal=TerminalState.DISPROVED):
    for finding in findings:
        if is_mutation_survivor(finding):
            append_row(project.root, _row(project, finding, terminal))


def _put_result(project, payload, *, cli_entry=False):
    directory = project.root if cli_entry else project.state
    path = directory / ".code-forge/mutation-result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _saved(project, verdict, *, cli_entry=False):
    directory = project.root if cli_entry else project.state
    saved = load_state(directory / ".code-forge/state.json")
    assert saved is not None and saved.mode is Mode.CI
    assert saved.verdict is verdict
    assert saved.converged is (verdict is Verdict.PASS)
    return saved


def _one(findings, identity):
    hits = [finding for finding in findings if finding.id == identity]
    assert len(hits) == 1
    return hits[0]


def _timeout_text(identity):
    escaped = json.dumps(identity, ensure_ascii=True)
    return "mutant timed out: " + (escaped[:160] + "..." if len(escaped) > 160 else escaped)


def _assert_timeouts(findings, identities):
    notes = [f for f in findings if f.id.startswith("mutation-timeout-")]
    assert len(notes) == len(identities)
    assert {f.id for f in notes} == {"mutation-timeout-" + identity for identity in identities}
    for identity in identities:
        note = _one(notes, "mutation-timeout-" + identity)
        assert note.fingerprint == "mutation-timeout:" + identity
        assert note.source == "MUTANT" and note.disposition is Disposition.DISMISSED
        assert note.file == "" and note.line_range == []
        assert note.description == _timeout_text(identity)
        assert len(note.description) <= len("mutant timed out: ") + 163
        assert "\n" not in note.description and "\r" not in note.description
        assert "\x1b" not in note.description and "\x00" not in note.description
        assert not is_mutation_survivor(note)


def _outside_ci_config(project, monkeypatch):
    """#140: CI never trusts an untracked in-tree gate.yaml; the real CLI gets
    the fixture gate through FORGE_TEST_CONFIG_ROOT outside the checkout."""
    outside = project.root.parent / (project.root.name + "-ci-config")
    (outside / ".code-forge").mkdir(parents=True, exist_ok=True)
    (outside / ".code-forge/gate.yaml").write_bytes((project.root / ".code-forge/gate.yaml").read_bytes())
    monkeypatch.setenv("FORGE_TEST_CONFIG_ROOT", str(outside))
    return outside


def _cli(project, monkeypatch, capsys):
    _discover_mutmut(project)
    # Advisory model transport only. Runtime parsing and reporting remain real.
    def advisory_transport(prompt, **kwargs):
        assert kwargs["backend"].name == "recorded-ci-control"
        project.advisory_calls.append(prompt)
        return LLMResult({"surfaces": [], "findings": []}, Usage(), 0.0)

    monkeypatch.setattr("code_forge.runtime.llm_invoke", advisory_transport)
    monkeypatch.chdir(project.root)
    _outside_ci_config(project, monkeypatch)
    monkeypatch.setattr(sys, "argv", list(REVIEW_ARGV))
    parsed = cli._build_parser().parse_args(REVIEW_ARGV[1:])
    assert (parsed.subcommand, parsed.mode, parsed.falsification_engine) == ("review", "ci", "stub")
    assert (parsed.baseline, parsed.head, parsed.allow_main) == ("HEAD", "WORKING", True)
    capsys.readouterr()
    exit_code = cli.main()
    output = capsys.readouterr()
    assert "unexpected error" not in output.err
    assert "internal error" not in output.err
    assert "run start: mode=CI" in output.err
    assert project.advisory_calls
    return exit_code, output.out + output.err


@pytest.mark.parametrize("factory", [False, True], ids=["direct-native", "factory-native"])
@pytest.mark.parametrize("ruling", ["exact", "unruled", "mixed"], ids=["exact", "unruled", "mixed"])
def test_ci_exact_ruling_native_positive_o2_o3(ci_project, factory, ruling):
    """O2 and O3 each must change this exact positive's semantic result."""
    p = ci_project
    if ruling == "mixed":
        p.codes, p.statuses = (0, 0), ("survived", "survived")
    findings, _, _ = _measure(p)
    if ruling != "unruled":
        _rule(p, findings[:1])
    if factory:
        _discover_mutmut(p)
    runner = build_l2_runner(cwd=p.root) if factory else mutation.run_mutation
    machine = _machine(p, runner=runner)
    expected = Verdict.PASS if ruling == "exact" else Verdict.FAIL
    assert machine.run() is expected
    assert machine._mutation_measurement_eligible is True
    saved = _saved(p, expected)
    for index, original in enumerate(findings):
        want = Disposition.DISMISSED if ruling != "unruled" and index == 0 else Disposition.CONFIRMED
        current = _one(saved.findings, original.id)
        assert current.fingerprint == original.fingerprint
        assert current.disposition is want
        assert current.description == original.description
    assert len(saved.round_history) == 1
    assert p.stages.count("run") == 2
    assert (p.root / "calc.py").read_bytes() == SOURCE_A


@pytest.mark.parametrize("later", ["revoked", "changed-key", "unproved"])
@pytest.mark.parametrize("promoted", [False, True], ids=["prior-dismissal", "prior-promotion"])
def test_ci_fresh_survivor_excludes_prior_disposition_o4(ci_project, later, promoted):
    p = ci_project
    findings, _, _ = _measure(p)
    row = _row(p, findings[0])
    append_row(p.root, row)
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.PASS
    prior = _one(machine._state.findings, findings[0].id)
    assert prior.disposition is Disposition.DISMISSED
    assert _one(_saved(p, Verdict.PASS).findings, prior.id).disposition is Disposition.DISMISSED
    if promoted:
        prior.disposition = Disposition.UNCERTAIN
        machine._state.promoted_fingerprints.add(prior.fingerprint)
        machine._state.fix_attempts[prior.fingerprint] = machine.max_fix_attempts
        machine._state.round_history[-1]["dispositions"][prior.fingerprint] = "UNCERTAIN"
    fresh = []
    if later == "unproved":
        def unproved(*_args, **_kwargs):
            fresh.extend(deepcopy(findings))
            return fresh, []
        machine.l2_runner = unproved
    elif later == "changed-key":
        append_row(p.root, replace(row, suppression_key="changed-key"))
    else:
        append_row(p.root, replace(row, terminal_state=TerminalState.UNADJUDICATED))
    assert machine.run() is Verdict.FAIL
    current = _one(machine._state.findings, prior.id)
    assert current is not prior
    assert current.disposition is Disposition.CONFIRMED
    assert _one(_saved(p, Verdict.FAIL).findings, prior.id).disposition is Disposition.CONFIRMED
    assert machine._mutation_measurement_eligible is (later != "unproved")
    assert len(machine._state.round_history) == 2
    if fresh:
        assert current is fresh[0]


def test_ci_ordinary_finding_keeps_dismissed_carry_forward(ci_project):
    p = ci_project
    findings, _, _ = _measure(p)
    _rule(p, findings)
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.PASS
    prior = _one(machine._state.findings, findings[0].id)
    append_row(p.root, _row(p, findings[0], TerminalState.UNADJUDICATED))
    ordinary = deepcopy(findings[0])
    ordinary.source = "L0"
    machine.l2_runner = lambda *_args, **_kwargs: ([ordinary], [])
    assert machine.run() is Verdict.PASS
    assert ordinary is not prior and ordinary.disposition is Disposition.DISMISSED
    assert machine._mutation_measurement_eligible is False
    assert _one(_saved(p, Verdict.PASS).findings, ordinary.id).disposition is Disposition.DISMISSED


@pytest.mark.parametrize("endpoint", ["unchanged", "missing", "incoherent"])
def test_ci_first_observation_latches_before_l0_restoration_o1(ci_project, endpoint):
    p = ci_project
    findings, _, _ = _measure(p)
    _rule(p, findings)
    machine = _machine(p, runner=mutation.run_mutation)
    source = p.root / "calc.py"
    if endpoint == "missing":
        source.unlink()
    elif endpoint == "incoherent":
        source.write_bytes(SOURCE_B)
    observed = []
    def restore():
        observed.append(None if not source.exists() else source.read_bytes())
        source.write_bytes(SOURCE_A)
    p.l0_hook = restore
    expected = Verdict.PASS if endpoint == "unchanged" else Verdict.FAIL
    assert machine.run() is expected
    assert observed == [{"unchanged": SOURCE_A, "missing": None, "incoherent": SOURCE_B}[endpoint]]
    assert source.read_bytes() == SOURCE_A
    assert machine._mutation_measurement_eligible is (endpoint == "unchanged")
    wanted = Disposition.DISMISSED if endpoint == "unchanged" else Disposition.CONFIRMED
    assert _one(_saved(p, expected).findings, findings[0].id).disposition is wanted


@pytest.mark.parametrize("drift", ["unchanged", "pre-l2-restored", "post-native"])
def test_ci_pre_l2_and_post_native_observations_are_independent(ci_project, drift):
    p = ci_project
    findings, _, _ = _measure(p)
    _rule(p, findings)
    machine = _machine(p, runner=mutation.run_mutation)
    source = p.root / "calc.py"
    observations = []
    if drift == "pre-l2-restored":
        p.l0_hook = lambda: source.write_bytes(SOURCE_B)
        def restore():
            observations.append(source.read_bytes())
            source.write_bytes(SOURCE_A)
        p.baseline_hook = restore
    elif drift == "post-native":
        p.results_hook = lambda: source.write_bytes(SOURCE_B)
    expected = Verdict.PASS if drift == "unchanged" else Verdict.FAIL
    assert machine.run() is expected
    assert machine._mutation_measurement_eligible is (drift == "unchanged")
    assert source.read_bytes() == (SOURCE_B if drift == "post-native" else SOURCE_A)
    if drift == "pre-l2-restored":
        assert observations[0] == SOURCE_B
    wanted = Disposition.DISMISSED if drift == "unchanged" else Disposition.CONFIRMED
    assert _one(_saved(p, expected).findings, findings[0].id).disposition is wanted


def test_ci_does_not_autofix_observed_source(ci_project):
    p = ci_project
    calls = []
    class ForbiddenFixer:
        def fix(self, finding, mode_hint):
            calls.append((finding.id, mode_hint))
            raise AssertionError("CI must never autofix")
    ordinary = StateFinding(
        id="ordinary", fingerprint="ordinary", source="L0",
        disposition=Disposition.CONFIRMED, file="calc.py", line_range=[2, 2],
        description="ordinary in-diff finding",
    )
    machine = _machine(p, l0=lambda *_args: ([deepcopy(ordinary)], []))
    machine.autofixer = ForbiddenFixer()
    assert machine.run() is Verdict.FAIL
    assert calls == [] and (p.root / "calc.py").read_bytes() == SOURCE_A
    assert _one(_saved(p, Verdict.FAIL).findings, ordinary.id).disposition is Disposition.CONFIRMED


def test_ci_bound_carrier_consumed_once_counts_original_and_persists(ci_project, capsys):
    p = ci_project
    p.codes, p.statuses = (36, 0, 0), ("timeout", "survived", "survived")
    payload, findings = _payload(p)
    _rule(p, findings)
    frozen = deepcopy(payload)
    result = _put_result(p, payload)
    machine = _machine(p)
    capsys.readouterr()
    assert machine.run() is Verdict.PASS
    text = capsys.readouterr().err
    assert "mutation: 0 unresolved of 2 original survivors" in text
    assert payload == frozen and not result.exists()
    saved = _saved(p, Verdict.PASS)
    _assert_timeouts(saved.findings, [_identity(1)])
    assert _timeout_text(_identity(1)) in text
    for identity in payload["survivors"]:
        assert _one(saved.findings, identity).disposition is Disposition.DISMISSED
    assert machine.run() is Verdict.PASS
    assert "original survivors" not in capsys.readouterr().err


@pytest.mark.parametrize("damage", [
    "missing-survivor", "extra-survivor", "wrong-context", "missing-inventory",
    "unknown-status", "pending-status", "timeout-as-survivor", "duplicate-fingerprint",
    "boolean-version", "boolean-completion", "missing-version", "legacy-location-only",
])
def test_ci_current_carrier_refuses_incomplete_or_unbound_authority(ci_project, damage):
    p = ci_project
    p.codes, p.statuses = (0, 0), ("survived", "survived")
    payload, findings = _payload(p)
    _rule(p, findings)
    if damage == "missing-survivor":
        payload["survivors"].pop()
        payload["survivor_details"].pop()
    elif damage == "extra-survivor":
        extra = deepcopy(payload["survivor_details"][0])
        extra.update(id="mutant-never-measured", fingerprint="never-measured")
        payload["survivors"].append(extra["id"])
        payload["survivor_details"].append(extra)
    elif damage == "wrong-context":
        payload["review_context"]["source_repo_root"] = str(p.state)
    elif damage == "missing-inventory":
        payload["inventory"] = {}
    elif damage in {"unknown-status", "pending-status"}:
        payload["inventory"][_identity(9)] = damage.removesuffix("-status")
    elif damage == "timeout-as-survivor":
        payload["inventory"][_identity(1)] = "timeout"
    elif damage == "duplicate-fingerprint":
        payload["survivor_details"][1]["fingerprint"] = payload["survivor_details"][0]["fingerprint"]
    elif damage == "boolean-version":
        payload["survivor_details_version"] = True
    elif damage == "boolean-completion":
        payload["completed_measurement"] = 1
    elif damage == "missing-version":
        del payload["survivor_details_version"]
    else:
        payload["survivor_details"] = [{"file": "", "line_range": [0, 0]}]
    original = deepcopy(payload)
    result = _put_result(p, payload)
    # A real current native round stays visible even when a later carrier is refused.
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    assert payload == original and not result.exists()
    saved = _saved(p, Verdict.FAIL)
    assert "CI: invalid bound mutation survivor details" in saved.infra_errors
    assert {f.id for f in saved.findings if f.id.startswith("mutant-")} == {
        finding.id for finding in findings
    }
    _assert_timeouts(saved.findings, [])


@pytest.mark.parametrize("damage", ["none", "missing", "incomplete"])
def test_ci_real_receipt_gate_precedes_result_consumption(ci_project, monkeypatch, damage):
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    payload, _findings = _payload(p)
    result = _put_result(p, payload)
    original = result.read_bytes()
    calls = []
    def transport(prompt, **kwargs):
        calls.append(prompt)
        return LLMResult({"findings": [], "code_excerpts": [{
            "file": "calc.py", "start_line": 1, "end_line": 2,
            "content": SOURCE_A.decode(),
        }]}, Usage(), 0.0)
    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    machine = _machine(p, active_l1=True)
    sabotaged = []
    if damage != "none":
        def remove_receipt(round_index):
            receipt = p.state / f".code-forge/receipts/receipt-c{round_index + 1}p3.json"
            assert receipt.exists()
            if damage == "missing":
                receipt.unlink()
            else:
                data = json.loads(receipt.read_text())
                data["pass_status"] = "incomplete"
                receipt.write_text(json.dumps(data), encoding="utf-8")
            sabotaged.append(receipt)
        machine.post_round_hook = remove_receipt
    expected = Verdict.PASS if damage == "none" else Verdict.FAIL
    assert machine.run() is expected
    saved = _saved(p, expected)
    assert len(calls) == 3
    if damage == "none":
        assert not result.exists()
    else:
        assert sabotaged and result.read_bytes() == original
        assert any("receipt" in error for error in saved.infra_errors)


@pytest.mark.parametrize("failure", [
    "error", "incomplete", "cleanup", "deadline", "interruption", "non-done",
])
def test_ci_versioned_bound_failure_never_passes_without_survivors(ci_project, failure):
    p = ci_project
    p.codes, p.statuses = (36, 1), ("timeout", "killed")
    payload, _ = _payload(p)
    if failure == "error":
        payload.update(status="error", message="RECORDED_VERSIONED_ERROR")
    elif failure == "incomplete":
        payload["completed_measurement"] = False
    elif failure == "cleanup":
        payload.update(cleanup_complete=False, cleanup_error="RECORDED_CLEANUP_FAILURE")
    elif failure == "deadline":
        payload.update(status="error", completed_measurement=False,
                       message="RECORDED_OVERALL_DEADLINE", process_failure={"timeout": True})
    elif failure == "interruption":
        payload.update(status="error", cancelled=True, cancellation_type="KeyboardInterrupt")
    else:
        payload["status"] = "unknown"
    assert payload["survivors"] == []
    frozen = deepcopy(payload)
    result = _put_result(p, payload)
    machine = _machine(p)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert saved.infra_errors and payload == frozen
    assert not result.exists()
    _assert_timeouts(saved.findings, [])


def test_ci_legacy_unversioned_error_keeps_compatibility_policy(ci_project):
    p = ci_project
    result = _put_result(p, {"status": "error", "message": "RECORDED_LEGACY_ERROR"})
    machine = _machine(p)
    assert machine.run() is Verdict.PASS
    assert not result.exists()
    assert any("RECORDED_LEGACY_ERROR" in error for error in _saved(p, Verdict.PASS).infra_errors)


@pytest.mark.parametrize("marker", ["live", "fresh-no-pid", "stale", "dead", "bad-pid", "malformed", "missing-status", "missing-infra", "bad-infra"])
def test_ci_versioned_running_marker_is_pending_or_refused(ci_project, marker):
    p = ci_project
    data = {"status": "running", "pid": os.getpid(), "started_at": time.time(),
            "survivor_details_version": 1, "review_context": _context(p),
            "infra_errors": [], "baseline_passed": False,
            "completed_measurement": False, "survivors": [], "survivor_details": [],
            "inventory": {}, "skipped": []}
    if marker == "fresh-no-pid":
        data["pid"] = None
    elif marker == "missing-status":
        del data["status"]
    elif marker == "missing-infra":
        del data["infra_errors"]
    elif marker == "bad-infra":
        data["infra_errors"] = ["RECORDED_RUNNING_FAILURE"]
    elif marker == "stale":
        data.update(pid=None, started_at=0)
    elif marker == "dead":
        _git(p.root, "rev-parse", "HEAD")
        data["pid"] = p.git_pids[-1]
        with pytest.raises(ProcessLookupError):
            os.kill(data["pid"], 0)
    elif marker == "bad-pid":
        data["pid"] = True
    elif marker == "malformed":
        data["review_context"] = {}
    result = _put_result(p, data)
    machine = _machine(p)
    expected = Verdict.PENDING if marker in {"live", "fresh-no-pid"} else Verdict.FAIL
    assert machine.run() is expected
    _saved(p, expected)
    assert result.exists() is (marker in {"live", "fresh-no-pid"})


@pytest.mark.parametrize("fault", ["deadline", "run-error", "missing-coverage"])
def test_ci_direct_native_incomplete_measurement_latches_nonpass(ci_project, fault):
    p = ci_project
    p.codes, p.statuses = (36, 1), ("timeout", "killed")
    p.fault = fault
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    assert machine._mutation_measurement_eligible is False
    saved = _saved(p, Verdict.FAIL)
    _assert_timeouts(saved.findings, [])
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    assert not (p.root / "mutants").exists()
    assert (p.root / "setup.cfg").read_bytes() == SETUP


@pytest.mark.parametrize("code", TIMEOUT_CODES, ids=["36", "minus24", "24", "152", "255"])
@pytest.mark.parametrize("inventory", ["timeout-only", "mixed-unruled", "mixed-ruled"])
@pytest.mark.parametrize("entry", ["direct-ci", "result-ci", "review-cli"])
def test_completed_timeout_visibility_at_real_ci_entrypoints(
    ci_project, monkeypatch, capsys, code, inventory, entry,
):
    p = ci_project
    if inventory == "timeout-only":
        p.codes, p.statuses = (code,), ("timeout",)
    else:
        p.codes = (code, 1, 37, 0)
        p.statuses = ("timeout", "killed", "caught by type check", "survived")
    payload, originals = _payload(p)
    assert payload["inventory"] == {
        _identity(index): status for index, status in enumerate(p.statuses, 1)
    }
    assert payload["status"] == "done" and payload["completed_measurement"] is True
    assert payload["survivors"] == ([] if inventory == "timeout-only" else ["mutant-" + _identity(4)])
    if inventory == "mixed-ruled":
        _rule(p, originals)
    expected = Verdict.FAIL if inventory == "mixed-unruled" else Verdict.PASS
    if entry == "review-cli":
        exit_code, text = _cli(p, monkeypatch, capsys)
        assert exit_code == (1 if expected is Verdict.FAIL else 0)
        saved = _saved(p, expected, cli_entry=True)
    else:
        if entry == "result-ci":
            original_bytes = json.dumps(payload)
            result = _put_result(p, payload)
            machine = _machine(p)
        else:
            machine = _machine(p, runner=mutation.run_mutation)
        capsys.readouterr()
        assert machine.run() is expected
        text = capsys.readouterr().err
        saved = _saved(p, expected)
        if entry == "result-ci":
            assert not result.exists() and json.dumps(payload) == original_bytes
            assert f"of {len(payload['survivors'])} original survivors" in text
        else:
            assert machine._mutation_measurement_eligible is True
    _assert_timeouts(saved.findings, [_identity(1)])
    assert _timeout_text(_identity(1)) in text
    assert "all killed" not in text.lower()
    assert not any("mutation error:" in error for error in saved.infra_errors)
    active = [f.id for f in saved.findings if is_mutation_survivor(f)]
    assert active == (["mutant-" + _identity(4)] if inventory == "mixed-unruled" else [])
    if inventory != "timeout-only":
        survivor = _one(saved.findings, "mutant-" + _identity(4))
        assert survivor.fingerprint == "mutant:" + _identity(4)
    assert (p.root / "calc.py").read_bytes() == SOURCE_A
    assert (p.root / "setup.cfg").read_bytes() == SETUP
    assert not (p.root / "mutants").exists()


@pytest.mark.parametrize("entry", ["result-ci", "review-cli"])
def test_timeout_rendering_escapes_and_bounds_without_identity_collision(
    ci_project, monkeypatch, capsys, entry,
):
    p = ci_project
    p.codes, p.statuses = (36,), ("timeout",)
    payload, _ = _payload(p)
    # These are deliberate carrier-schema identities, not generated native names.
    # Physical native identity coverage is the separate five-code matrix above.
    identities = [
        'calc.unsafe\x00\x1b[31m\nforged PASS\r\t"\\__mutmut_1',
        "calc." + "a" * 200 + "-first__mutmut_2",
        "calc." + "a" * 200 + "-second__mutmut_3",
    ]
    payload["inventory"] = {identity: "timeout" for identity in identities}
    original = deepcopy(payload)
    assert decode_mutation_survivor_details(payload, review_context=_context(p)) == []
    decoded = decode_mutation_survivor_details(
        payload, review_context=_context(p), include_timeout_diagnostics=True,
    )
    assert decoded is not None
    _assert_timeouts(decoded, identities)
    _put_result(p, payload, cli_entry=entry == "review-cli")
    if entry == "review-cli":
        # Synchronous CLI acquisition contributes no timeout of its own.
        p.codes, p.statuses = (1,), ("killed",)
        exit_code, text = _cli(p, monkeypatch, capsys)
        assert exit_code == 0
        saved = _saved(p, Verdict.PASS, cli_entry=True)
    else:
        capsys.readouterr()
        assert _machine(p).run() is Verdict.PASS
        text = capsys.readouterr().err
        saved = _saved(p, Verdict.PASS)
    assert payload == original
    _assert_timeouts(saved.findings, identities)
    for identity in identities:
        assert _timeout_text(identity) in text
    assert "\x1b" not in text and "\x00" not in text
    assert "\nforged PASS" not in text
    assert _timeout_text(identities[1]) == _timeout_text(identities[2])
    assert len({f.fingerprint for f in saved.findings if f.id.startswith("mutation-timeout-")}) == 3


@pytest.mark.parametrize("scenario", ["exact", "unruled", "revoked", "source-change", "invalid-carrier"])
def test_review_cli_parity_uses_real_review_dispatch(ci_project, monkeypatch, capsys, scenario):
    p = ci_project
    payload, findings = _payload(p)
    if scenario != "unruled":
        _rule(p, findings)
    if scenario == "revoked":
        append_row(p.root, _row(p, findings[0], TerminalState.UNADJUDICATED))
    elif scenario == "source-change":
        p.results_hook = lambda: (p.root / "calc.py").write_bytes(SOURCE_B)
    elif scenario == "invalid-carrier":
        payload["completed_measurement"] = False
        _put_result(p, payload, cli_entry=True)
    expected = Verdict.PASS if scenario == "exact" else Verdict.FAIL
    exit_code, text = _cli(p, monkeypatch, capsys)
    assert exit_code == (0 if expected is Verdict.PASS else 1)
    saved = _saved(p, expected, cli_entry=True)
    finding = _one(saved.findings, findings[0].id)
    assert finding.fingerprint == findings[0].fingerprint
    wanted = Disposition.DISMISSED if scenario in {"exact", "invalid-carrier"} else Disposition.CONFIRMED
    assert finding.disposition is wanted
    if scenario == "invalid-carrier":
        assert "CI: invalid bound mutation survivor details" in saved.infra_errors
    if scenario == "source-change":
        assert "native ruling refused" in text
    assert f"verdict={expected.value}" in text


@pytest.mark.parametrize(
    "direct", ["no-op", "unproved", "timeout", "killed", "failed-native", "failed-launch"],
)
def test_ci_launch_capture_binds_context_without_executing_payload(ci_project, capsys, direct):
    """Starting a bound measurement does not supply its missing completion proof."""
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    runner = None
    unproved_calls = []
    if direct == "unproved":
        acquired, errors, proof = _measure(p)
        assert not errors and proof["completed_measurement"] is True
        def unproved(*args, **kwargs):
            unproved_calls.append((args, kwargs))
            return deepcopy(acquired), []
        runner = unproved
    elif direct in {"timeout", "killed", "failed-native"}:
        runner = mutation.run_mutation
        if direct == "timeout":
            p.codes, p.statuses = (36,), ("timeout",)
        elif direct == "failed-native":
            p.fault = "deadline"
    elif direct == "failed-launch":
        p.launch_error = OSError("RECORDED_BOUND_LAUNCH_FAILURE")
    _discover_mutmut(p)
    machine = _machine(p, runner=runner)
    before = len(p.commands)
    capsys.readouterr()
    expected = (Verdict.PASS if direct in {"timeout", "killed"} else
                Verdict.FAIL if direct in {"failed-native", "failed-launch"} else
                Verdict.PENDING)
    assert machine.run() is expected
    text = capsys.readouterr().err
    saved = _saved(p, expected)
    assert machine._mutation_measurement_eligible is (direct in {"timeout", "killed"})
    if direct in {"no-op", "unproved", "failed-launch"}:
        assert len(p.commands) == before
    if direct == "unproved":
        assert len(unproved_calls) == 1 and "_evidence" not in unproved_calls[0][1]
    if direct == "failed-native":
        # #131: the failed native measurement already latched FAIL, so the
        # wasted detached relaunch is skipped and says why.
        assert p.launches == []
        assert "CI: detached mutation not launched: verdict is already FAIL" in saved.infra_errors
        assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
        assert not (p.state / ".code-forge/mutation-result.json").exists()
        return
    assert len(p.launches) == 1
    argv, kwargs = p.launches[0]
    assert argv[:2] == [sys.executable, "-c"]
    assert kwargs["cwd"] == p.root
    assert kwargs["close_fds"] is True and kwargs["start_new_session"] is True
    result = p.state / ".code-forge/mutation-result.json"
    if direct != "failed-launch":
        assert result.exists()
    if result.exists():
        marker = json.loads(result.read_text())
        assert marker["survivor_details_version"] == 1
        assert marker["review_context"] == _context(p)
        assert marker["status"] == "running"
        assert marker["completed_measurement"] is False
    if expected is not Verdict.PASS:
        assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    if direct == "failed-launch":
        assert any("failed to start" in error or "launch" in error for error in saved.infra_errors)
    _assert_timeouts(saved.findings, [_identity(1)] if direct == "timeout" else [])
    if direct == "timeout":
        assert text.count(_timeout_text(_identity(1))) == 1
    tree = ast.parse(argv[2])
    constants = [node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert str(p.root) in constants
    assert str(result) in constants
    assert "review_context" in argv[2]
    assert "_mutation_outcome" in argv[2]


@pytest.mark.parametrize("guard", ["ledger", "pin", "pass-name", "keyword"])
@pytest.mark.parametrize("native", [True, False], ids=["native-refused", "ordinary-compatible"])
def test_ci_generic_suppression_cannot_prove_native_survivor(ci_project, guard, native):
    p = ci_project
    findings, _, _ = _measure(p)
    item = deepcopy(findings[0])
    item.file = "calc.py"
    if not native:
        item.source = "L0"
        item.id = item.fingerprint = "ordinary-compatible"
    gate_path = p.state / ".code-forge/gate.yaml"
    config = json.loads(gate_path.read_text())
    if guard == "ledger":
        append_row(p.root, _row(p, item))
    elif guard == "pin":
        config["pinned_paths"] = ["calc.py"]
    elif guard == "pass-name":
        config["style_downgrade"] = {"pass_names": [item.source]}
    else:
        config["style_downgrade"] = {"keywords": ["survived"]}
    gate_path.write_text(json.dumps(config), encoding="utf-8")
    machine = _machine(p, runner=lambda *_args, **_kwargs: ([deepcopy(item)], []))
    expected = Verdict.FAIL if native else Verdict.PASS
    assert machine.run() is expected
    wanted = (Disposition.CONFIRMED if native else
              Disposition.DISMISSED if guard in {"ledger", "pin"} else Disposition.STYLE)
    assert _one(_saved(p, expected).findings, item.id).disposition is wanted
    assert machine._mutation_measurement_eligible is False


@pytest.mark.parametrize("entry", ["direct-ci", "result-ci"])
@pytest.mark.parametrize("kind", ["timeout-only", "mixed-ruled", "killed-only"])
def test_timeout_diagnostic_is_not_a_ledger_ruling_or_synthetic_clean_pass(ci_project, entry, kind):
    p = ci_project
    if kind == "mixed-ruled":
        p.codes, p.statuses = (36, 1, 37, 0), ("timeout", "killed", "caught by type check", "survived")
    else:
        p.codes, p.statuses = ((36,), ("timeout",)) if kind == "timeout-only" else ((1,), ("killed",))
    payload, originals = _payload(p)
    if kind == "mixed-ruled":
        _rule(p, originals)
    if entry == "result-ci":
        _put_result(p, payload)
        machine = _machine(p)
    else:
        machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.PASS
    saved = _saved(p, Verdict.PASS)
    _assert_timeouts(saved.findings, [] if kind == "killed-only" else [_identity(1)])
    rows = list(iter_rows(p.root, strict=True))
    assert any(row.evidence_class == "clean_pass" for row in rows) is (kind == "killed-only")
    assert not any(row.fingerprint.startswith("mutation-timeout:") for row in rows)
    # Supplementary writer boundary: the real LOCAL terminal writer must not
    # reinterpret a persisted DISMISSED observation as a DISPROVED mutant.
    machine._write_ledger_rows()
    assert not any(row.fingerprint.startswith("mutation-timeout:")
                   for row in iter_rows(p.root, strict=True))


@pytest.mark.parametrize("current_native", [False, True], ids=["carrier-only", "current-native-preserved"])
def test_result_ci_refuses_survivor_timeout_fingerprint_collision(ci_project, capsys, current_native):
    p = ci_project
    p.codes, p.statuses = (0, 36), ("survived", "timeout")
    payload, originals = _payload(p)
    payload["survivor_details"][0]["fingerprint"] = "mutation-timeout:" + _identity(2)
    assert decode_mutation_survivor_details(
        payload, review_context=_context(p), include_timeout_diagnostics=True,
    ) is None
    frozen = deepcopy(payload)
    _put_result(p, payload)
    # The carrier-only refusal materializes nothing. The sibling retains a
    # real current survivor independently of this forged carrier.
    machine = _machine(p, runner=mutation.run_mutation if current_native else None)
    capsys.readouterr()
    assert machine.run() is Verdict.FAIL
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.FAIL)
    assert payload == frozen
    if current_native:
        assert _one(saved.findings, originals[0].id).disposition is Disposition.CONFIRMED
    else:
        assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    # Only the independently validated synchronous timeout can exist.
    _assert_timeouts(saved.findings, [_identity(2)] if current_native else [])
    assert text.count(_timeout_text(_identity(2))) == int(current_native)
    assert "CI: invalid bound mutation survivor details" in saved.infra_errors


@pytest.mark.parametrize("entry", ["direct-ci", "result-ci"])
def test_timeout_identity_collision_keeps_current_ordinary_blocker(ci_project, capsys, entry):
    p = ci_project
    p.codes, p.statuses = (36,), ("timeout",)
    payload, _ = _payload(p)
    ordinary = StateFinding(
        id="ordinary-collision", fingerprint="mutation-timeout:" + _identity(1),
        source="L0", disposition=Disposition.CONFIRMED,
        file="calc.py", line_range=[2, 2], description="current ordinary blocker",
    )
    if entry == "result-ci":
        _put_result(p, payload)
        runner = None
    else:
        runner = mutation.run_mutation
    machine = _machine(p, runner=runner, l0=lambda *_args: ([deepcopy(ordinary)], []))
    capsys.readouterr()
    assert machine.run() is Verdict.FAIL
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.FAIL)
    current = _one(saved.findings, ordinary.id)
    assert current.disposition is Disposition.CONFIRMED
    assert current.description == ordinary.description
    _assert_timeouts(saved.findings, [])
    assert _timeout_text(_identity(1)) not in text
    assert any("timeout identity collision" in error for error in saved.infra_errors)


@pytest.mark.parametrize("gate", ["missing", "invalid"])
def test_ci_native_missing_or_invalid_gate_latches_failure(ci_project, gate):
    p = ci_project
    path = p.state / ".code-forge/gate.yaml"
    if gate == "missing":
        path.unlink()
    else:
        path.write_text('{"test": {"command": null}}', encoding="utf-8")
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    assert p.commands == []
    assert any("gate.yaml" in error for error in saved.infra_errors)


def test_clean_result_cannot_override_failed_current_native_measurement(ci_project):
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    payload, _ = _payload(p)
    _put_result(p, payload)
    p.fault = "deadline"
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    assert any("invalid bound mutation measurement" in error for error in saved.infra_errors)


@pytest.mark.parametrize("code", [None, True, 36.0, "36", 5, 33, 34, 35, 900],
                         ids=["null", "boolean", "float", "string", "no-tests5",
                              "no-tests33", "skipped", "suspicious", "unknown"])
def test_ci_physical_nonterminal_or_noninteger_metadata_is_not_timeout_proof(ci_project, code):
    p = ci_project
    p.codes, p.statuses = (36, code), ("timeout", "not checked")
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    _assert_timeouts(saved.findings, [])
    assert machine._mutation_measurement_eligible is False
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    assert not (p.root / "mutants").exists()


def test_ci_same_direct_and_result_timeout_has_one_diagnostic_and_text_event(ci_project, capsys):
    p = ci_project
    p.codes, p.statuses = (36,), ("timeout",)
    payload, _ = _payload(p)
    result = _put_result(p, payload)
    machine = _machine(p, runner=mutation.run_mutation)
    capsys.readouterr()
    assert machine.run() is Verdict.PASS
    text = capsys.readouterr().err
    assert not result.exists()
    _assert_timeouts(_saved(p, Verdict.PASS).findings, [_identity(1)])
    assert text.count(_timeout_text(_identity(1))) == 1
    assert "0 unresolved of 0 original survivors" in text


def test_ci_fresh_timeout_does_not_inherit_prior_promotion(ci_project):
    p = ci_project
    p.codes, p.statuses = (36,), ("timeout",)
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.PASS
    note_id = "mutation-timeout-" + _identity(1)
    prior = _one(machine._state.findings, note_id)
    assert prior.disposition is Disposition.DISMISSED
    prior.disposition = Disposition.UNCERTAIN
    machine._state.round_history[-1]["dispositions"][prior.fingerprint] = "UNCERTAIN"
    machine._state.fix_attempts[prior.fingerprint] = machine.max_fix_attempts
    machine._state.promoted_fingerprints.add(prior.fingerprint)
    assert machine.run() is Verdict.PASS
    current = _one(machine._state.findings, note_id)
    assert current is not prior and current.disposition is Disposition.DISMISSED
    _assert_timeouts(_saved(p, Verdict.PASS).findings, [_identity(1)])
    assert not any(row.fingerprint == prior.fingerprint for row in iter_rows(p.root, strict=True))


@pytest.mark.parametrize("ruled", [False, True], ids=["unruled-carrier", "exact-carrier-ruling"])
def test_result_ci_survivor_cannot_replace_current_timeout_with_same_fingerprint(
    ci_project, capsys, ruled,
):
    """An individually valid survivor carrier cannot overwrite a current timeout."""
    p = ci_project
    p.codes, p.statuses = (1, 0), ("killed", "survived")
    payload, _ = _payload(p)
    assert payload["inventory"] == {_identity(1): "killed", _identity(2): "survived"}
    payload["survivor_details"][0]["fingerprint"] = "mutation-timeout:" + _identity(1)
    isolated = decode_mutation_survivor_details(
        payload, review_context=_context(p), include_timeout_diagnostics=True,
    )
    assert isolated is not None and len(isolated) == 1
    survivor = _one(isolated, "mutant-" + _identity(2))
    assert survivor.fingerprint == "mutation-timeout:" + _identity(1)
    assert survivor.disposition is Disposition.CONFIRMED
    _assert_timeouts(isolated, [])
    if ruled:
        _rule(p, isolated)
    frozen = deepcopy(payload)
    _put_result(p, payload)
    # The current native measurement is independently acquired from physical
    # files through the existing command seam. No direct finding is forged.
    p.codes, p.statuses = (36, 1), ("timeout", "killed")
    machine = _machine(p, runner=mutation.run_mutation)
    capsys.readouterr()
    assert machine.run() is Verdict.FAIL
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.FAIL)
    assert payload == frozen and machine._mutation_measurement_eligible is True
    _assert_timeouts(saved.findings, [_identity(1)])
    assert text.count(_timeout_text(_identity(1))) == 1
    assert not any(finding.id == survivor.id for finding in saved.findings)
    assert any("timeout identity collision" in error for error in saved.infra_errors)
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert (p.root / "calc.py").read_bytes() == SOURCE_A
    assert not (p.root / "mutants").exists()


@pytest.mark.parametrize("damage", [
    "duplicate-status", "duplicate-version", "duplicate-completed",
    "duplicate-infra", "duplicate-inventory-field", "duplicate-inventory-member",
    "truncated", "malformed", "invalid-utf8", "deep-json",
    "nonobject-number", "nonobject-list", "nonobject-null",
    "missing-status-bound", "missing-status-unbound", "read-oserror",
])
def test_ci_raw_result_read_or_parse_ambiguity_is_fail_closed(ci_project, damage):
    """Raw input must be refused before an uncertain binding can become PASS."""
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    payload, _ = _payload(p)
    encoded = json.dumps(payload).encode("utf-8")
    deep_json_decoded = False
    duplicates = {
        "duplicate-status": ("status", "error"),
        "duplicate-version": ("survivor_details_version", 2),
        "duplicate-completed": ("completed_measurement", False),
        "duplicate-infra": ("infra_errors", ["RECORDED_AMBIGUOUS_FAILURE"]),
        "duplicate-inventory-field": ("inventory", {_identity(1): "pending"}),
    }
    if damage in duplicates:
        key, first_value = duplicates[damage]
        # A permissive last-value-wins parser would discard this refusal and
        # accept the valid complete value that follows it in the real carrier.
        raw = ("{" + json.dumps(key) + ":" + json.dumps(first_value) + ",").encode() + encoded[1:]
    elif damage == "duplicate-inventory-member":
        valid_inventory = json.dumps(payload["inventory"]).encode()
        duplicate_inventory = (
            "{" + json.dumps(_identity(1)) + ':"pending",' +
            json.dumps(_identity(1)) + ':"killed"}'
        ).encode()
        assert encoded.count(valid_inventory) == 1
        raw = encoded.replace(valid_inventory, duplicate_inventory)
    elif damage == "truncated":
        raw = encoded[:-1]
    elif damage == "malformed":
        raw = b"{not-json"
    elif damage == "invalid-utf8":
        raw = b'\xff{"status":"done"}'
    elif damage == "deep-json":
        depth = sys.getrecursionlimit() + 128
        raw = (b'{"status":"done","survivor_details_version":1,"metadata":' +
               b"[" * depth + b"0" + b"]" * depth + b"}")
        # JSON parser depth limits vary independently of Python recursion limits.
        try:
            json.loads(raw)
        except RecursionError:
            pass
        else:
            deep_json_decoded = True
    elif damage.startswith("nonobject-"):
        raw = {"nonobject-number": b"42", "nonobject-list": b"[]", "nonobject-null": b"null"}[damage]
    elif damage == "missing-status-bound":
        del payload["status"]
        raw = json.dumps(payload).encode()
    elif damage == "missing-status-unbound":
        raw = b"{}"
    else:
        assert damage == "read-oserror"
        raw = None
    result = p.state / ".code-forge/mutation-result.json"
    if raw is None:
        result.mkdir()
    else:
        result.write_bytes(raw)
        (p.artifacts / (damage + ".raw")).write_bytes(raw)
    machine = _machine(p)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    if damage == "deep-json":
        if deep_json_decoded:
            assert "CI: invalid bound mutation survivor details" in saved.infra_errors
        else:
            assert any(error.startswith("CI: failed to read mutation-result.json:")
                       for error in saved.infra_errors)
    else:
        assert any("mutation-result.json" in error or "bound mutation measurement" in error
                   for error in saved.infra_errors)
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    if raw is None:
        assert result.is_dir()
    elif damage == "deep-json" and deep_json_decoded:
        assert not result.exists()
    elif damage.startswith("duplicate-") or damage in {"truncated", "malformed", "invalid-utf8", "deep-json"}:
        # Unreadable/ambiguous bytes remain available for diagnosis. Known
        # decoded nonobjects or missing-status objects may be consumed.
        assert result.read_bytes() == raw


def test_ci_raw_result_read_recursion_error_is_fail_closed(ci_project, monkeypatch):
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    payload, _ = _payload(p)
    raw = json.dumps(payload).encode("utf-8")
    result = p.state / ".code-forge/mutation-result.json"
    result.write_bytes(raw)
    (p.artifacts / "read-recursion-error.raw").write_bytes(raw)
    _discover_mutmut(p)
    machine = _machine(p)
    original_load = json.load
    reads = []

    def recursive_result_read(stream, *args, **kwargs):
        if getattr(stream, "name", None) == str(result):
            reads.append(str(result))
            raise RecursionError("RECORDED_RESULT_RECURSION")
        return original_load(stream, *args, **kwargs)

    monkeypatch.setattr(json, "load", recursive_result_read)
    escaped = None
    verdict = None
    try:
        verdict = machine.run()
    except RecursionError as error:
        escaped = error
    assert escaped is None, "result-file reader RecursionError escaped the CI refusal handler"
    assert verdict is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert reads == [str(result)]
    assert "CI: failed to read mutation-result.json: RECORDED_RESULT_RECURSION" in saved.infra_errors
    assert result.read_bytes() == raw
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    _assert_timeouts(saved.findings, [])
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    assert p.launches == []



def test_ci_raw_explicit_legacy_error_preserves_compatibility(ci_project):
    p = ci_project
    raw = (b'{"status":"error","message":"RECORDED_RAW_LEGACY_ERROR",'
           b'"review_context":null}')
    result = p.state / ".code-forge/mutation-result.json"
    result.write_bytes(raw)
    machine = _machine(p)
    assert machine.run() is Verdict.PASS
    saved = _saved(p, Verdict.PASS)
    assert not result.exists()
    assert any("CI: mutation error: RECORDED_RAW_LEGACY_ERROR" in error
               for error in saved.infra_errors)
    assert not any("failed to read mutation-result.json" in error for error in saved.infra_errors)
    _assert_timeouts(saved.findings, [])


def _unproved_empty_runner(calls):
    """An explicit custom acquisition has no native completion authority."""
    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        assert "_evidence" not in kwargs
        return [], []
    return runner


def _assert_captured_running_marker(project, result):
    """The captured launch replaces the consumed carrier before Popen returns."""
    assert result.is_file()
    marker = json.loads(result.read_text(encoding="utf-8"))
    assert marker["status"] == "running"
    assert marker["survivor_details_version"] == 1
    assert marker["review_context"] == _context(project)
    assert marker["pid"] is None  # The generated payload was never executed.
    assert marker["completed_measurement"] is False
    assert marker["survivors"] == marker["survivor_details"] == []
    assert marker["infra_errors"] == []
    return marker


@pytest.mark.parametrize("inventory", ["killed-only", "timeout-only", "exact-ruled-survivor"])
@pytest.mark.parametrize("direct", ["default-no-op", "unproved"])
@pytest.mark.parametrize("launch", ["successful", "failed"])
def test_ci_completed_bound_result_survives_redundant_launch_only_for_current_invocation(
    ci_project, capsys, inventory, direct, launch,
):
    """Completed result proof applies now; a second invocation must earn its own."""
    p = ci_project
    code, status = {
        "killed-only": (1, "killed"),
        "timeout-only": (36, "timeout"),
        "exact-ruled-survivor": (0, "survived"),
    }[inventory]
    p.codes, p.statuses = (code,), (status,)
    payload, original_findings = _payload(p)
    if inventory == "exact-ruled-survivor":
        _rule(p, original_findings)
    original = deepcopy(payload)
    result = _put_result(p, payload)
    original_bytes = result.read_bytes()
    _discover_mutmut(p)
    if launch == "failed":
        p.launch_error = OSError("RECORDED_REDUNDANT_BOUND_LAUNCH_FAILURE")
    unproved_calls = []
    runner = _unproved_empty_runner(unproved_calls) if direct == "unproved" else None
    machine = _machine(p, runner=runner)
    command_count = len(p.commands)
    capsys.readouterr()
    assert machine.run() is Verdict.PASS
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.PASS)
    assert machine._mutation_measurement_eligible is False
    assert len(p.commands) == command_count
    assert len(unproved_calls) == int(direct == "unproved")
    assert len(p.launches) == 1
    assert payload == original
    marker = _assert_captured_running_marker(p, result)
    assert marker != payload and result.read_bytes() != original_bytes
    assert f"mutation: 0 unresolved of {len(payload['survivors'])} original survivors" in text
    if inventory == "timeout-only":
        _assert_timeouts(saved.findings, [_identity(1)])
        assert text.count(_timeout_text(_identity(1))) == 1
    else:
        _assert_timeouts(saved.findings, [])
    if inventory == "exact-ruled-survivor":
        finding = _one(saved.findings, original_findings[0].id)
        assert finding.fingerprint == original_findings[0].fingerprint
        assert finding.description == original_findings[0].description
        assert finding.disposition is Disposition.DISMISSED
    else:
        assert not any(finding.id.startswith("mutant-") for finding in saved.findings)
    assert not any(is_mutation_survivor(finding) for finding in saved.findings)
    if launch == "failed":
        assert any("failed to start" in error for error in saved.infra_errors)
    rows_after_first = list(iter_rows(p.root, strict=True))
    if inventory == "timeout-only":
        assert not any(row.evidence_class == "clean_pass" for row in rows_after_first)

    # This is fixture cleanup of an unexecuted launch marker, not a completed
    # detached process. The same machine receives no result on its second run.
    result.unlink()
    assert not result.exists()
    capsys.readouterr()
    expected = Verdict.PENDING if launch == "successful" else Verdict.FAIL
    assert machine.run() is expected
    second_text = capsys.readouterr().err
    second_saved = _saved(p, expected)
    assert machine._mutation_measurement_eligible is False
    assert len(p.commands) == command_count
    assert len(unproved_calls) == 2 * int(direct == "unproved")
    assert len(p.launches) == 2
    _assert_captured_running_marker(p, result)
    assert "original survivors" not in second_text
    assert _timeout_text(_identity(1)) not in second_text
    _assert_timeouts(second_saved.findings, [])
    assert not any(finding.id.startswith("mutant-") for finding in second_saved.findings)
    # PENDING/FAIL may not mint new clean credit from the previous PASS.
    assert list(iter_rows(p.root, strict=True)) == rows_after_first
    assert len(second_saved.round_history) == 2


@pytest.mark.parametrize("blocker", ["failed-native", "ordinary-current"])
@pytest.mark.parametrize("launch", ["successful", "failed"])
def test_ci_completed_bound_result_does_not_erase_current_failure(ci_project, capsys, blocker, launch):
    p = ci_project
    p.codes, p.statuses = (36, 0), ("timeout", "survived")
    payload, original_findings = _payload(p)
    _rule(p, original_findings)
    result = _put_result(p, payload)
    original_bytes = result.read_bytes()
    _discover_mutmut(p)
    if launch == "failed":
        p.launch_error = OSError("RECORDED_REDUNDANT_BOUND_LAUNCH_FAILURE")
    ordinary = StateFinding(
        id="current-result-blocker", fingerprint="current-result-blocker", source="L0",
        disposition=Disposition.CONFIRMED, file="calc.py", line_range=[2, 2],
        description="current ordinary failure remains blocking",
    )
    if blocker == "failed-native":
        p.codes, p.statuses = (1,), ("killed",)
        p.fault = "deadline"
        machine = _machine(p, runner=mutation.run_mutation)
    else:
        machine = _machine(p, l0=lambda *_args: ([deepcopy(ordinary)], []))
    capsys.readouterr()
    assert machine.run() is Verdict.FAIL
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    if blocker == "failed-native":
        # #131: a latched native failure skips the redundant detached launch.
        assert p.launches == []
        assert "CI: detached mutation not launched: verdict is already FAIL" in saved.infra_errors
    else:
        assert len(p.launches) == 1
        _assert_captured_running_marker(p, result)
        assert result.read_bytes() != original_bytes
    assert "mutation: 0 unresolved of 1 original survivors" in text
    _assert_timeouts(saved.findings, [_identity(1)])
    assert text.count(_timeout_text(_identity(1))) == 1
    survivor = _one(saved.findings, "mutant-" + _identity(2))
    assert survivor.fingerprint == "mutant:" + _identity(2)
    assert survivor.disposition is Disposition.DISMISSED
    if blocker == "failed-native":
        assert any("invalid bound mutation measurement" in error for error in saved.infra_errors)
    else:
        assert _one(saved.findings, ordinary.id).disposition is Disposition.CONFIRMED
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))


@pytest.mark.parametrize("carrier", ["malformed", "unruled"])
@pytest.mark.parametrize("direct", ["default-no-op", "unproved"])
def test_ci_refused_bound_result_returns_before_redundant_launch(ci_project, capsys, carrier, direct):
    p = ci_project
    p.codes, p.statuses = (36, 0), ("timeout", "survived")
    payload, original_findings = _payload(p)
    if carrier == "malformed":
        _rule(p, original_findings)
        payload["completed_measurement"] = False
    original = deepcopy(payload)
    result = _put_result(p, payload)
    _discover_mutmut(p)
    p.launch_error = OSError("REFUSAL_MUST_PRECEDE_REDUNDANT_LAUNCH")
    unproved_calls = []
    runner = _unproved_empty_runner(unproved_calls) if direct == "unproved" else None
    machine = _machine(p, runner=runner)
    command_count = len(p.commands)
    capsys.readouterr()
    assert machine.run() is Verdict.FAIL
    text = capsys.readouterr().err
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_measurement_eligible is False
    assert len(p.commands) == command_count
    assert len(unproved_calls) == int(direct == "unproved")
    assert p.launches == []
    assert not result.exists() and payload == original
    assert not any(row.evidence_class == "clean_pass" for row in iter_rows(p.root, strict=True))
    if carrier == "malformed":
        _assert_timeouts(saved.findings, [])
        assert "CI: invalid bound mutation survivor details" in saved.infra_errors
        assert "original survivors" not in text
    else:
        _assert_timeouts(saved.findings, [_identity(1)])
        assert text.count(_timeout_text(_identity(1))) == 1
        survivor = _one(saved.findings, "mutant-" + _identity(2))
        assert survivor.disposition is Disposition.CONFIRMED
        assert "mutation: 1 unresolved of 1 original survivors" in text


def _baseline_timeout():
    raise subprocess.TimeoutExpired(BASELINE, 900)


def test_ci_baseline_timeout_fails_with_accurate_reason(ci_project):
    """#120 + user policy decision on #122: in CI a baseline timeout means no
    mutation evidence, so CI FAILs -- with a reason naming the baseline
    timeout, never 'invalid bound mutation measurement'."""
    p = ci_project
    p.baseline_hook = _baseline_timeout
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    skips = [f for f in saved.findings if f.fingerprint == "mutation-baseline-timeout"]
    assert skips and all(f.disposition is Disposition.DISMISSED for f in skips)
    reasons = [e for e in saved.infra_errors if e.startswith("CI: mutation not measured: baseline tests timed out")]
    seconds = re.search(r"timed out after (\d+)s", skips[0].description).group(1)
    assert reasons == [f"CI: mutation not measured: baseline tests timed out after {seconds}s"]
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    assert machine._mutation_measurement_eligible is False
    assert "run" not in p.stages


def test_ci_undecodable_native_measurement_still_fails_closed(ci_project):
    """Control for #120: a measurement that ran but cannot be decoded still latches."""
    p = ci_project
    p.codes, p.statuses = (1,), ("killed",)
    p.fault = "run-error"
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_bound_failure is True
    assert any("invalid bound mutation measurement" in e for e in saved.infra_errors)


def _skip(fingerprint="mutation-baseline-timeout", ident="MUTATION_SKIPPED",
          disposition=Disposition.DISMISSED, description="run 1: baseline tests timed out after 900s"):
    return StateFinding(id=ident, fingerprint=fingerprint, source="MUTANT",
                        disposition=disposition, file="", line_range=[], description=description)


_OTHER = "diff also has files for other adapters"
_TWO = "run 2: baseline tests timed out after 900s"


@pytest.mark.parametrize("case", [
    "survivor", "other-skip", "extra-infra", "completed", "cleanup_error",
    "process_failure", "cancellation_type", "publication_error", "cancelled",
    "cleanup-incomplete", "cleanup-none", "empty", "source-changed",
    "inventory", "inventory-not-dict", "results_sha256", "metadata_sha256",
    "confirmed", "uncertain", "wrong-id", "infra-duplicated", "infra-reordered",
    "two-skips-one-infra", "other-adapter-only", "other-adapter-with-infra",
])
def test_baseline_timeout_exemption_is_narrow(case):
    from code_forge.machine import _is_clean_baseline_timeout_skip

    findings, evidence, unchanged = [_skip()], {"cleanup_complete": True, "inventory": {}}, True
    infra = [findings[0].description]
    assert _is_clean_baseline_timeout_skip(findings, infra, evidence, source_unchanged=True) is True
    if case == "survivor":
        findings = findings + [_skip(fingerprint="mutant:x", ident="mutant-x")]
    elif case == "other-skip":
        findings = [_skip(fingerprint="mutation-flaky")]
    elif case == "extra-infra":
        infra = infra + ["mutmut crashed"]
    elif case == "completed":
        evidence["completed_measurement"] = True
    elif case == "cancelled":
        evidence["cancelled"] = True
    elif case == "cleanup-incomplete":
        evidence["cleanup_complete"] = False
    elif case == "cleanup-none":
        evidence["cleanup_complete"] = None
    elif case == "empty":
        findings, infra = [], []
    elif case == "source-changed":
        unchanged = False
    elif case == "inventory-not-dict":
        evidence["inventory"] = ["x"]
    elif case in ("inventory", "results_sha256", "metadata_sha256"):
        evidence[case] = {"x": "killed"} if case == "inventory" else "abc"
    elif case == "confirmed":
        findings = [_skip(disposition=Disposition.CONFIRMED)]
    elif case == "uncertain":
        findings = [_skip(disposition=Disposition.UNCERTAIN)]
    elif case == "wrong-id":
        findings = [_skip(ident="MUTATION_ERROR")]
    elif case == "infra-duplicated":
        infra = infra * 2
    elif case == "infra-reordered":
        findings = [_skip(), _skip(description=_TWO)]
        infra = [_TWO, findings[0].description]
    elif case == "two-skips-one-infra":
        findings = [_skip(), _skip(description=_TWO)]
    elif case == "other-adapter-only":
        findings, infra = [_skip(fingerprint="mutation-other-adapter", description=_OTHER)], []
    elif case == "other-adapter-with-infra":
        findings = findings + [_skip(fingerprint="mutation-other-adapter", description=_OTHER)]
        infra = infra + [_OTHER]
    else:
        evidence[case] = "x"
    assert _is_clean_baseline_timeout_skip(findings, infra, evidence, source_unchanged=unchanged) is False


def test_baseline_timeout_exemption_allows_mixed_language_note():
    """#122 review: a mixed-language diff adds a DISMISSED mutation-other-adapter
    note (no infra line); the clean baseline timeout is still a skip."""
    from code_forge.machine import _is_clean_baseline_timeout_skip

    findings = [_skip(), _skip(fingerprint="mutation-other-adapter", description=_OTHER)]
    assert _is_clean_baseline_timeout_skip(
        findings, [findings[0].description], {"cleanup_complete": True}, source_unchanged=True,
    ) is True


def test_ci_null_baseline_deadline_is_a_config_error_before_any_baseline(ci_project):
    """A null test.timeout_seconds is refused by the gate loader: no baseline
    runs, so no baseline-timeout reason can be formatted from it."""
    p = ci_project
    for directory in (p.root, p.state):
        path = directory / ".code-forge/gate.yaml"
        gate = json.loads(path.read_text())
        gate["test"]["timeout_seconds"] = None
        path.write_text(json.dumps(gate), encoding="utf-8")
        record_trust(path, gate)
    p.baseline_hook = _baseline_timeout
    machine = _machine(p, runner=mutation.run_mutation)
    machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert any("'test.timeout_seconds' must be an integer" in e for e in saved.infra_errors)
    assert "baseline" not in p.stages
    assert not any(e.startswith("CI: mutation not measured") for e in saved.infra_errors)


@pytest.mark.parametrize("description,expected", [
    ("run 1: baseline tests timed out after 900s", "CI: mutation not measured: baseline tests timed out after 900s"),
    ("run 1: baseline tests timed out after 12.5s; stdout: x", "CI: mutation not measured: baseline tests timed out after 12.5s"),
    ("run 1: baseline tests timed out after Nones", "CI: mutation not measured: baseline tests timed out"),
    ("run 1: baseline tests timed out", "CI: mutation not measured: baseline tests timed out"),
    (None, "CI: mutation not measured: baseline tests timed out"),
])
def test_baseline_timeout_reason_text(description, expected):
    from code_forge.machine import _baseline_timeout_reason

    findings = [] if description is None else [_skip(description=description)]
    assert _baseline_timeout_reason(findings) == expected


def _local_machine(project, *, runner):
    machine = _machine(project, runner=runner)
    machine.mode = Mode.LOCAL
    machine._state.mode = Mode.LOCAL
    return machine


def _injected_timeout_runner(calls):
    def runner(diff_files, baseline_cmd, *, baseline_timeout, **_kwargs):
        calls.append(baseline_timeout)
        skip = _skip(description=f"run 1: baseline tests timed out after {baseline_timeout}s")
        return [skip], [skip.description]
    return runner


def test_ci_injected_runner_baseline_timeout_fails_with_accurate_reason(ci_project):
    """#124: the CI baseline-timeout policy is runner-independent."""
    p = ci_project
    calls = []
    machine = _machine(p, runner=_injected_timeout_runner(calls))
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert calls, "injected runner must have been called"
    reasons = [e for e in saved.infra_errors if e.startswith("CI: mutation not measured")]
    assert reasons == [f"CI: mutation not measured: baseline tests timed out after {calls[0]}s"]
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    skips = [f for f in saved.findings if f.fingerprint == "mutation-baseline-timeout"]
    assert skips and all(f.disposition is Disposition.DISMISSED for f in skips)


def test_ci_injected_runner_other_skip_is_unchanged(ci_project):
    """Control for #124: only the baseline timeout changes policy."""
    p = ci_project

    def runner(diff_files, baseline_cmd, **_kwargs):
        return [_skip(fingerprint="mutation-tests-only", description="only tests changed")], []

    machine = _machine(p, runner=runner)
    machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert not any(e.startswith("CI: mutation not measured") for e in saved.infra_errors)
    assert machine._mutation_bound_failure is False


@pytest.mark.parametrize("native", [True, False])
def test_local_baseline_timeout_stays_a_dismissed_skip(ci_project, native):
    """#129: LOCAL keeps a clean baseline timeout as a DISMISSED skip; the CI
    failure must not leak into LOCAL."""
    p = ci_project
    calls = []
    if native:
        p.baseline_hook = _baseline_timeout
        runner = mutation.run_mutation
    else:
        runner = _injected_timeout_runner(calls)
    machine = _local_machine(p, runner=runner)
    verdict = machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert saved.mode is Mode.LOCAL
    assert verdict is not Verdict.FAIL
    skips = [f for f in saved.findings if f.fingerprint == "mutation-baseline-timeout"]
    assert skips and all(f.disposition is Disposition.DISMISSED for f in skips)
    if native:
        assert "baseline" in p.stages and "run" not in p.stages
    else:
        assert calls
    errors = saved.infra_errors
    assert not any(e.startswith("CI: mutation not measured") for e in errors)
    assert not any("invalid bound mutation measurement" in e for e in errors)
    assert machine._mutation_bound_failure is False


def _failing_baseline(monkeypatch, p):
    inner = mutation.run_owned_command

    def command(argv, **kwargs):
        if list(argv) == BASELINE:
            p.stages.append("baseline")
            return subprocess.CompletedProcess(list(argv), 1, "FAILED tests/test_x.py::test_y\n", "")
        return inner(argv, **kwargs)

    monkeypatch.setattr(mutation, "run_owned_command", command)


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_failed_baseline_names_the_cause_not_invalid_bound(ci_project, monkeypatch, mode):
    """#127: a baseline that ran and failed is a mutation-flaky skip. CI FAILs
    naming the baseline failure; LOCAL keeps the skip; neither logs the false
    'invalid bound mutation measurement'."""
    p = ci_project
    _failing_baseline(monkeypatch, p)
    machine = _machine(p, runner=mutation.run_mutation) if mode is Mode.CI else _local_machine(p, runner=mutation.run_mutation)
    verdict = machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert "baseline" in p.stages and "run" not in p.stages
    assert any(f.fingerprint == "mutation-flaky" and f.disposition is Disposition.DISMISSED for f in saved.findings)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    reasons = [e for e in saved.infra_errors if e.startswith("CI: mutation not measured")]
    if mode is Mode.CI:
        assert verdict is Verdict.FAIL
        assert len(reasons) == 1 and "baseline failed" in reasons[0] and "returncode 1" in reasons[0]
    else:
        assert verdict is not Verdict.FAIL and reasons == []
        assert machine._mutation_bound_failure is False


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_no_adapter_diff_is_a_clean_skip_in_both_modes(ci_project, monkeypatch, mode):
    """#127: a diff with no registered mutation adapter (docs/config only) is
    'mutation does not apply': a clean skip, no invalid-bound error, no latch."""
    p = ci_project
    machine = _machine(p, runner=mutation.run_mutation) if mode is Mode.CI else _local_machine(p, runner=mutation.run_mutation)
    (p.root / "README.md").write_text("# notes\n", encoding="utf-8")
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("README.md")])
    monkeypatch.setattr(machine, "_review_diff_paths", lambda: {"README.md"})
    findings = machine._run_l2_phase()
    assert [f.fingerprint for f in findings] == ["mutation-no-adapter"]
    assert "L2: mutation not applicable (docs-only diff): README.md" in machine._state.infra_errors
    assert "baseline" not in p.stages
    assert not any("invalid bound mutation measurement" in e for e in machine._state.infra_errors)
    assert not any(e.startswith("CI: mutation not measured") for e in machine._state.infra_errors)
    assert machine._mutation_bound_failure is False


@pytest.mark.parametrize("findings,infra,expected", [
    ([("mutation-no-adapter", "no registered mutation adapter for this diff")], [], "not-applicable"),
    ([("mutation-tests-only", "diff is tests-only; nothing to mutate")], [], "not-applicable"),
    ([("mutation-no-adapter", "x")], ["x"], None),
    ([("mutation-flaky", "run 1: baseline failed (returncode 1)")], ["run 1: baseline failed (returncode 1)"], "baseline-failed"),
    ([("mutation-flaky", "a"), ("mutation-other-adapter", "o")], ["a"], "baseline-failed"),
    ([("mutation-flaky", "a")], [], None),
    ([("mutation-flaky", "a"), ("mutation-baseline-timeout", "b")], ["a", "b"], None),
    ([("mutation-flaky", "a"), ("mutation-no-adapter", "n")], ["a"], None),
    ([("mutation-other-adapter", "o")], [], None),
    ([("mutation-baseline-timeout", "t")], ["t"], "baseline-timeout"),
    ([("mutation-host-busy", "host not idle after 1800s: x")], ["host not idle after 1800s: x"], "host-busy"),
    ([("mutation-host-busy", "h")], [], None),
    ([("mutation-host-busy", "h"), ("mutation-flaky", "a")], ["h", "a"], None),
])
def test_clean_mutation_skip_kind(findings, infra, expected):
    from code_forge.machine import _clean_mutation_skip_kind

    objs = [_skip(fingerprint=fp, description=d) for fp, d in findings]
    evidence = {"inventory": {}}
    assert _clean_mutation_skip_kind(objs, infra, evidence, source_unchanged=True) == expected
    if expected is not None:
        assert _clean_mutation_skip_kind(objs, infra, evidence, source_unchanged=False) is None
        assert _clean_mutation_skip_kind(objs, infra, {**evidence, "cleanup_error": "x"}, source_unchanged=True) is None
        assert _clean_mutation_skip_kind(objs, infra, {"inventory": {"m": "killed"}}, source_unchanged=True) is None


def test_baseline_failed_reason_text():
    from code_forge.machine import _baseline_failed_reason

    desc = "run 1: baseline failed (returncode 1: tests/a.py::t); stdout: lots"
    assert _baseline_failed_reason([_skip(fingerprint="mutation-flaky", description=desc)]) == \
        "CI: mutation not measured: baseline failed (returncode 1: tests/a.py::t)"
    assert _baseline_failed_reason([]) == "CI: mutation not measured: baseline failed"


def test_ci_latched_failure_skips_detached_launch(ci_project):
    """#131: once L2 latched a CI failure (here a clean baseline timeout), the
    verdict is a guaranteed FAIL; no detached mutation run is launched."""
    p = ci_project
    _discover_mutmut(p)
    p.baseline_hook = _baseline_timeout
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert p.launches == []
    assert "CI: detached mutation not launched: verdict is already FAIL" in saved.infra_errors
    assert any(e.startswith("CI: mutation not measured: baseline tests timed out") for e in saved.infra_errors)
    assert not (p.state / ".code-forge/mutation-result.json").exists()


def test_ci_unlatched_run_still_launches_detached_mutation(ci_project):
    """Control for #131: without a latched failure the detached run launches."""
    p = ci_project
    _discover_mutmut(p)
    machine = _machine(p)
    machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert len(p.launches) == 1
    assert "CI: detached mutation not launched: verdict is already FAIL" not in saved.infra_errors


def test_ci_live_running_marker_with_latched_failure_is_fail_not_pending(ci_project):
    """#130 P3: a live, valid running marker would be PENDING, but a failure
    latched by this run's L2 phase makes the CI verdict FAIL, not PENDING."""
    p = ci_project
    data = {"status": "running", "pid": os.getpid(), "started_at": time.time(),
            "survivor_details_version": 1, "review_context": _context(p),
            "infra_errors": [], "baseline_passed": False,
            "completed_measurement": False, "survivors": [], "survivor_details": [],
            "inventory": {}, "skipped": []}
    result = _put_result(p, data)
    p.baseline_hook = _baseline_timeout
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert machine._mutation_bound_failure is True
    assert "CI: bound mutation measurement still running" in saved.infra_errors
    assert result.exists()


# --- #132 review: scope of the "mutation does not apply" clean skip ---------


def _ci_with_diff(p, monkeypatch, files, *, runner=mutation.run_mutation, diff_paths=None):
    for name, text in files.items():
        path = p.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    machine = _machine(p, runner=runner)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path(n) for n in files])
    monkeypatch.setattr(machine, "_review_diff_paths", lambda: set(files) if diff_paths is None else set(diff_paths))
    return machine


@pytest.mark.parametrize("files", [
    {"README.md": "# docs\n"},
    {"docs/guide.md": "Guide\n=====\n", "LICENSE": "MIT\n", "docs/img/logo.png": "png", "evidence/run.md": "x\n", "README.zh-CN.md": "x\n"},
])
def test_ci_docs_only_diff_passes_with_visible_note(ci_project, monkeypatch, files):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, files)
    assert machine.run() is Verdict.PASS
    saved = _saved(p, Verdict.PASS)
    notes = [e for e in saved.infra_errors if e.startswith("L2: mutation not applicable (docs-only diff)")]
    assert notes == ["L2: mutation not applicable (docs-only diff): " + ", ".join(sorted(files))]
    assert machine._mutation_bound_failure is False


def test_ci_docs_only_switch_off_fails(ci_project, monkeypatch):
    """The owner's pending policy is one switch."""
    from code_forge import machine as machine_mod

    monkeypatch.setattr(machine_mod, "CI_DOCS_ONLY_MUTATION_SKIP_PASSES", False)
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {"README.md": "# docs\n"})
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert "CI: mutation not measured: no mutation adapter covers: README.md" in saved.infra_errors


@pytest.mark.parametrize("files", [
    {"deploy.sh": "#!/bin/sh\nrm -rf \"$X\"/\n"},
    {"bin/forge-tool": "#!/usr/bin/env python3\nimport os\nos.system('x')\n"},
    {"notes.txt": "#!/bin/sh\necho hi\n"},
    {"Main.java": "class Main {}\n"},
    {"Makefile": "all:\n\ttrue\n"},
    {"Dockerfile": "FROM scratch\n"},
    {".github/workflows/ci.yml": "on: push\n"},
    {"README.md": "# docs\n", "deploy.sh": "#!/bin/sh\n"},
])
def test_ci_unmeasured_code_without_adapter_fails_with_accurate_reason(ci_project, monkeypatch, files):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, files)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert "CI: mutation not measured: no mutation adapter covers: " + ", ".join(sorted(files)) in saved.infra_errors
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)


def test_ci_gate_yaml_only_diff_fails(ci_project, monkeypatch):
    p = ci_project
    machine = _machine(p, runner=mutation.run_mutation)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path(".code-forge/gate.yaml")])
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert any(e.startswith("CI: mutation not measured: no mutation adapter covers") for e in saved.infra_errors)


def test_local_unmeasured_shebang_file_stays_a_skip_with_note(ci_project, monkeypatch):
    p = ci_project
    (p.root / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    machine = _local_machine(p, runner=mutation.run_mutation)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("deploy.sh")])
    machine._run_l2_phase()
    assert machine._mutation_bound_failure is False
    assert "L2: mutation not applicable (no mutation adapter covers): deploy.sh" in machine._state.infra_errors


def test_ci_tests_only_diff_is_clean_end_to_end_without_launch(ci_project, monkeypatch):
    """#132 P2-2: tests-only must not launch the detached run (its producer
    reports 'nothing to mutate' as an error -> FAIL invalid bound next run)."""
    p = ci_project
    _discover_mutmut(p)
    machine = _ci_with_diff(p, monkeypatch, {"tests/test_calc.py": "def test_x():\n    assert True\n"})
    assert machine.run() is Verdict.PASS
    saved = _saved(p, Verdict.PASS)
    assert p.launches == []
    assert "CI: detached mutation not launched: mutation not applicable to this diff" in saved.infra_errors
    assert "L2: mutation not applicable (diff is tests-only): tests/test_calc.py" in saved.infra_errors
    assert not (p.state / ".code-forge/mutation-result.json").exists()


@pytest.mark.parametrize("trusted", [False, True])
def test_ci_reviewed_tree_skip_globs_cannot_make_code_tests_only(ci_project, monkeypatch, trusted):
    """#134: the reviewed tree's gate.yaml (trust-recorded or not) is never
    a source of mutation globs; calc.py stays production code."""
    p = ci_project
    gate = p.root / ".code-forge/gate.yaml"
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = ["calc.py"]
    gate.write_text(json.dumps(data), encoding="utf-8")
    if trusted:
        record_trust(gate, data)
    seen = {}
    real = mutation._run_mutation

    def spy(*args, **kwargs):
        import inspect

        bound = inspect.signature(real).bind(*args, **kwargs)
        seen["skip"] = bound.arguments["mutation_skip_globs"]
        return real(*args, **kwargs)

    monkeypatch.setattr(mutation, "_run_mutation", spy)
    machine = _machine(p, runner=mutation.run_mutation)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("calc.py")])
    findings = machine._run_l2_phase()
    assert "mutation-tests-only" not in [f.fingerprint for f in findings]
    assert seen and seen["skip"] is None


def test_ci_trusted_config_skip_globs_reach_the_runner(ci_project, monkeypatch):
    """#134: globs from the trusted config root are passed explicitly; the
    machine still refuses a configured-glob tests-only clean skip (#132)."""
    p = ci_project
    gate = p.state / ".code-forge/gate.yaml"
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = ["calc.py"]
    gate.write_text(json.dumps(data), encoding="utf-8")
    record_trust(gate, data)
    machine = _machine(p, runner=mutation.run_mutation)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("calc.py")])
    findings = machine._run_l2_phase()
    assert [f.fingerprint for f in findings] == ["mutation-tests-only"]
    assert machine._mutation_bound_failure is True
    assert ("CI: mutation not measured: configured skip globs or conftest.py leave nothing to mutate: calc.py"
            in machine._state.infra_errors)


def test_ci_conftest_only_diff_is_not_tests_only(ci_project, monkeypatch):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {"tests/conftest.py": "import pytest\n"})
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert ("CI: mutation not measured: configured skip globs or conftest.py leave nothing to mutate: tests/conftest.py"
            in saved.infra_errors)
    assert p.launches == []


@pytest.mark.parametrize("signal", ["acquisition", "falsify_infra", "evidence_incomplete"])
def test_ci_each_latch_signal_blocks_detached_launch(ci_project, monkeypatch, signal):
    """#132 P3: every signal in the #131 guard is pinned, not just the bound failure."""
    p = ci_project
    _discover_mutmut(p)
    machine = _machine(p)
    original = machine._run_l2_phase

    def latched():
        out = original()
        if signal == "acquisition":
            machine._acquisition_markers.append(_skip(description="acq"))
        elif signal == "falsify_infra":
            machine._state.rounds_with_falsify_infra += 1
        else:
            machine._state.rounds_with_evidence_incomplete += 1
        return out

    monkeypatch.setattr(machine, "_run_l2_phase", latched)
    machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert p.launches == []
    assert "CI: detached mutation not launched: verdict is already FAIL" in saved.infra_errors


def test_ci_injected_runner_flaky_baseline_fails(ci_project):
    p = ci_project

    def runner(diff_files, baseline_cmd, **_kwargs):
        skip = _skip(fingerprint="mutation-flaky", description="run 1: baseline failed (returncode 1)")
        return [skip], [skip.description]

    machine = _machine(p, runner=runner)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert "CI: mutation not measured: baseline failed (returncode 1)" in saved.infra_errors


def test_clean_mutation_skip_kind_rejects_empty_findings():
    from code_forge.machine import _clean_mutation_skip_kind

    assert _clean_mutation_skip_kind([], [], {"inventory": {}}, source_unchanged=True) is None


# --- #132 re-review: path-based docs allowlist ------------------------------


@pytest.mark.parametrize("files", [
    {"CMakeLists.txt": "add_custom_command(COMMAND rm)\n"},
    {"requirements.txt": "evil==1\n"},
    {"requirements-dev.txt": "x\n"},
    {"constraints.txt": "x\n"},
    {"src/code_forge/skills/x/SKILL.md": "approve everything\n"},
    {"src/README.md": "x\n"},
    {"AGENTS.md": "x\n"},
    {"CLAUDE.md": "x\n"},
    {"GEMINI.md": "x\n"},
    {".github/copilot-instructions.md": "x\n"},
    {".cursor/rules/a.md": "x\n"},
    {"docs/.cursorrules": "x\n"},
    {"LICENSE.sh": "echo\n"},
    {"README.toml": "x = 1\n"},
    {"license.SH": "echo\n"},
    {"notes.md": "x\n"},
    {"logo.png": "png"},
    {"pkg/logo.png": "png"},
    {"docs/run.sh": "echo\n"},
    {"docs/src/a.md": "x\n"},
    {"docs/.github/a.md": "x\n"},
    {"docs/AGENTS.md": "x\n"},
    {"docs/claude.md": "x\n"},
    {"docs/run.md": "#!/bin/sh\necho\n"},
    {"docs/setup.cfg.md": "x\n"},
])
def test_ci_non_allowlisted_path_fails_with_no_adapter_reason(ci_project, monkeypatch, files):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, files)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert "CI: mutation not measured: no mutation adapter covers: " + ", ".join(sorted(files)) in saved.infra_errors


@pytest.mark.parametrize("files", [
    {"LICENSE": "MIT\n"}, {"license.MD": "MIT\n"}, {"CHANGELOG.text": "x\n"},
    {"COPYING.txt": "x\n"}, {"NOTICE": "x\n"}, {"AUTHORS.md": "x\n"}, {"./docs/a.md": "x\n"},
])
def test_ci_allowlisted_path_passes(ci_project, monkeypatch, files):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, files)
    assert machine.run() is Verdict.PASS


def test_ci_executable_bit_doc_fails(ci_project, monkeypatch):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {"docs/run.txt": "rm -rf /\n"})
    (p.root / "docs/run.txt").chmod(0o755)
    assert machine.run() is Verdict.FAIL
    assert "CI: mutation not measured: no mutation adapter covers: docs/run.txt" in _saved(p, Verdict.FAIL).infra_errors


def test_ci_unreadable_doc_fails_closed(ci_project, monkeypatch):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {"docs/a.md": "x\n"})
    import builtins
    real_open = builtins.open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith("docs/a.md"):
            raise PermissionError("denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    assert machine.run() is Verdict.FAIL
    assert "CI: mutation not measured: no mutation adapter covers: docs/a.md" in _saved(p, Verdict.FAIL).infra_errors


def test_ci_rename_from_code_to_docs_is_not_docs_only(ci_project, monkeypatch):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {"docs/tool.md": "x\n"}, diff_paths={"tool.py", "docs/tool.md"})
    assert machine.run() is Verdict.FAIL
    assert "CI: mutation not measured: no mutation adapter covers: docs/tool.md" in _saved(p, Verdict.FAIL).infra_errors


def test_diff_paths_reads_both_sides_of_renames_and_deletions():
    from code_forge.machine import _diff_paths

    diff = ("diff --git a/tool.py b/docs/tool.md\nsimilarity index 90%\nrename from tool.py\n"
            "rename to docs/tool.md\ndiff --git a/old.py b/old.py\ndeleted file mode 100644\n"
            "--- a/old.py\n+++ /dev/null\n")
    assert _diff_paths(diff) == {"tool.py", "docs/tool.md", "old.py"}
    assert _diff_paths("rename from tool.py\nrename to docs/tool.md\n") == {"tool.py", "docs/tool.md"}
    assert _diff_paths('diff --git "a/x y.md" "b/x y.md"\n') == {"<unparsed diff header>"}


def test_ci_symlinked_doc_fails_with_accurate_reason(ci_project, monkeypatch):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {})
    (p.root / "docs").mkdir(exist_ok=True)
    (p.root / "docs/notes.md").symlink_to(p.root / "calc.py")
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("docs/notes.md")])
    monkeypatch.setattr(machine, "_review_diff_paths", lambda: {"docs/notes.md"})
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    assert "CI: mutation not measured: symlinked files are not measured: docs/notes.md" in saved.infra_errors


def test_ci_symlink_rejected_by_source_guard_names_symlink(ci_project, monkeypatch):
    """When the source guard rejects a symlink, say so, not 'invalid bound'."""
    from code_forge import machine as machine_mod

    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {})
    (p.root / "docs").mkdir(exist_ok=True)
    (p.root / "docs/notes.md").symlink_to(p.root / "calc.py")
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("docs/notes.md")])
    monkeypatch.setattr(machine_mod, "mutation_source_guard_matches", lambda *a, **k: False)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    assert "CI: mutation not measured: symlinked files are not measured: docs/notes.md" in saved.infra_errors


def test_dot_prefixed_dir_is_not_tests_dir():
    """lstrip('./') turned .tests/core.py into tests/core.py."""
    from code_forge.machine import _default_tests_only

    assert _default_tests_only([".tests/core.py"]) is False
    assert _default_tests_only(["./tests/test_a.py"]) is True


def test_ci_dot_prefixed_production_dir_is_not_tests_only(ci_project, monkeypatch):
    p = ci_project
    gate = p.root / ".code-forge/gate.yaml"
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = [".tests/**"]
    gate.write_text(json.dumps(data), encoding="utf-8")
    machine = _ci_with_diff(p, monkeypatch, {".tests/core.py": "def f():\n    return 1\n"})
    assert machine.run() is Verdict.FAIL


def test_ci_doc_inside_importable_package_fails(ci_project, monkeypatch):
    """Package data (dir with __init__.py) is never docs, even under docs/."""
    p = ci_project
    (p.root / "docs/pkg").mkdir(parents=True)
    (p.root / "docs/pkg/__init__.py").write_text("", encoding="utf-8")
    machine = _ci_with_diff(p, monkeypatch, {"docs/pkg/prompt.md": "x\n"})
    assert machine.run() is Verdict.FAIL
    assert ("CI: mutation not measured: no mutation adapter covers: docs/pkg/prompt.md"
            in _saved(p, Verdict.FAIL).infra_errors)


def test_tests_only_requires_python_and_missing_doc_is_not_docs(tmp_path):
    from code_forge.machine import _default_tests_only, _docs_only_files

    assert _default_tests_only(["tests/data.json"]) is False
    assert _docs_only_files(["docs/gone.md"], tmp_path, {"docs/gone.md"}) is False
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/here.md").write_text("x\n", encoding="utf-8")
    assert _docs_only_files(["docs/here.md"], tmp_path, {"docs/here.md"}) is True


# --- forge P3 on #132: one docs policy, delegated to classify_path ---------


@pytest.mark.parametrize("files", [
    {"evidence/a.svg": "<svg><script>x</script></svg>"},
    {"docs/a.rst": "Title\n=====\n"},
    {"docs/a.adoc": "= T\n"},
    {"docs/a.gif": "GIF89a"},
    {"docs/a.ico": "x"},
    {"docs/a.bmp": "BM"},
    {"CHANGELOG.rst": "x\n"},
    {"README.png": "png"},
])
def test_ci_formats_classify_path_does_not_exempt_fail(ci_project, monkeypatch, files):
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, files)
    assert machine.run() is Verdict.FAIL
    assert ("CI: mutation not measured: no mutation adapter covers: " + ", ".join(sorted(files))
            in _saved(p, Verdict.FAIL).infra_errors)


_PARITY_NAMES = [
    "docs/a.md", "docs/a.MARKDOWN", "docs/a.txt", "docs/a.text", "docs/a.png", "docs/a.JPG",
    "docs/a.jpeg", "docs/a.webp", "docs/a.svg", "docs/a.rst", "docs/a.adoc", "docs/a.gif",
    "docs/a.ico", "docs/a.bmp", "docs/a.sh", "docs/a", "docs/.md", "evidence/a.svg",
    "evidence/r.md", "README.md", "README.rst", "LICENSE.txt", "LICENSE.sh", "CHANGELOG.adoc",
    "NOTICE.text", "AUTHORS.svg",
]
_PARITY_FILES = [(0o644, b"plain\n"), (0o755, b"plain\n"), (0o644, b"#!/bin/sh\n"), (0o700, b"#!x")]


@pytest.mark.parametrize("rel", _PARITY_NAMES)
@pytest.mark.parametrize("mode,content", _PARITY_FILES)
def test_docs_predicate_never_exempts_what_classify_path_rejects(tmp_path, rel, mode, content):
    from code_forge.machine import _docs_only_files
    from code_forge.mutation_engines.classify import CATEGORY_EXEMPT, classify_path

    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    exempt = _docs_only_files([rel], tmp_path, {rel})
    if classify_path(rel, mode, content).category != CATEGORY_EXEMPT:
        assert exempt is False
    elif rel.startswith(("docs/", "evidence/")) or rel.split(".")[-1].lower() in {"md", "txt", "text", "markdown"}:
        assert exempt is True


@pytest.mark.parametrize("mode,content,expected", [
    (0o644, b"MIT\n", True), (0o755, b"MIT\n", False), (0o644, b"#!/bin/sh\n", False),
])
def test_extensionless_top_level_doc_still_gets_classify_checks(tmp_path, mode, content, expected):
    """Narrow path-layer exception: classify_path rejects every extensionless
    name, so LICENSE is classified as LICENSE.txt; exec bit/shebang still apply."""
    from code_forge.machine import _docs_only_files

    (tmp_path / "LICENSE").write_bytes(content)
    (tmp_path / "LICENSE").chmod(mode)
    assert _docs_only_files(["LICENSE"], tmp_path, {"LICENSE"}) is expected
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/LICENSE").write_bytes(b"MIT\n")
    assert _docs_only_files(["docs/LICENSE"], tmp_path, {"docs/LICENSE"}) is False


def test_deleted_non_doc_under_docs_is_not_docs_only(tmp_path):
    """The other side of the diff is checked lexically via classify_path."""
    from code_forge.machine import _docs_only_files

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x\n", encoding="utf-8")
    assert _docs_only_files(["docs/a.md"], tmp_path, {"docs/a.md"}) is True
    assert _docs_only_files(["docs/a.md"], tmp_path, {"docs/a.md", "docs/tool.py"}) is False
    assert _docs_only_files(["docs/a.md"], tmp_path, {"docs/a.md", "docs/old.svg"}) is False


# --- #133: remaining no-measurement skips get accurate reasons ---------------

_OTHER_INFRA = "diff has no Python files; other adapters are registered but not invoked here"


@pytest.mark.parametrize("findings,infra,evidence,expected", [
    ([("mutation-other-adapter", "ts: adapter registered")], [_OTHER_INFRA], {}, "other-adapter-only"),
    ([("mutation-other-adapter", "ts")], [], {}, None),
    ([("mutation-other-adapter", "ts")], [_OTHER_INFRA, "x"], {}, None),
    ([("mutation-other-adapter", "ts")], [_OTHER_INFRA], {"results_sha256": "a"}, None),
    ([("mutation-unavailable", "mutmut not installed (soft dependency)")], [], {}, "unavailable"),
    ([("mutation-unavailable", "u"), ("mutation-other-adapter", "o")], [], {}, "unavailable"),
    ([("mutation-unavailable", "u")], ["u"], {}, None),
    ([("mutation-unavailable", "u")], [], {"metadata_sha256": "m"}, None),
    ([("mutation-probe-timeout", "probe timed out")], [], {}, "probe-timeout"),
    ([("mutation-probe-timeout", "p")], [], {"inventory": {"calc.x__mutmut_1": "killed"}}, None),
    ([("mutation-no-mutants", "none")], [], {"results_sha256": "r", "metadata_sha256": "m"}, "no-mutants"),
    ([("mutation-no-mutants", "none")], [], {}, None),
    ([("mutation-no-mutants", "none")], [], {"results_sha256": "r", "inventory": {"calc.x__mutmut_1": "survived"}}, None),
    ([("mutation-results-timeout", "t")], [], {"metadata_sha256": "m"}, "results-timeout"),
    ([("mutation-results-timeout", "t")], [], {"metadata_sha256": "m", "inventory": {"calc.x__mutmut_1": "survived"}}, None),
    ([("mutation-results-timeout", "t")], [], {"inventory": {"calc.x__mutmut_1": "killed"}}, None),
    ([("mutation-results-timeout", "t")], [], {"inventory": {"calc.x__mutmut_1": "caught by type check"}}, None),
    ([("mutation-results-timeout", "t")], [], {"inventory": {"calc.x__mutmut_1": "timeout"}}, None),
    ([("mutation-results-timeout", "t")], [], {"results_sha256": "r"}, None),
    ([("mutation-results-timeout", "t")], ["t"], {}, None),
    ([("mutation-unavailable", "u"), ("mutation-probe-timeout", "p")], [], {}, None),
    ([("mutation-no-adapter", "n")], [], {"results_sha256": "r"}, None),
])
def test_clean_mutation_skip_kind_133(findings, infra, evidence, expected):
    from code_forge.machine import _clean_mutation_skip_kind

    objs = [_skip(fingerprint=fp, description=d) for fp, d in findings]
    full = {"inventory": {}, **evidence}
    assert _clean_mutation_skip_kind(objs, infra, full, source_unchanged=True) == expected


@pytest.mark.parametrize("kind,fingerprint,description,expected", [
    ("unavailable", "mutation-unavailable", "mutmut not installed (soft dependency)",
     "CI: mutation not measured: mutmut not installed (soft dependency)"),
    ("probe-timeout", "mutation-probe-timeout",
     "mutmut import probe timed out in baseline test env; not the same as mutmut missing",
     "CI: mutation not measured: mutmut import probe timed out in baseline test env"),
    ("no-mutants", "mutation-no-mutants", "mutmut generated no mutants; no mutation measurement was made",
     "CI: mutation not measured: mutmut generated no mutants"),
    ("results-timeout", "mutation-results-timeout", "mutmut results timed out",
     "CI: mutation not measured: mutmut results timed out"),
    ("other-adapter-only", "mutation-other-adapter", "app.ts: ts adapter registered",
     "CI: mutation not measured: other-language adapters are not invoked by this run: app.ts: ts adapter registered"),
])
def test_ci_skip_reason_133(kind, fingerprint, description, expected):
    from code_forge.machine import _ci_skip_reason

    findings = [_skip(fingerprint="mutation-other-adapter", description="o"),
                _skip(fingerprint=fingerprint, description=description)]
    if kind == "other-adapter-only":
        findings = findings[1:]
    assert _ci_skip_reason(kind, findings) == expected


def _native_skip_runner(fingerprint, description, infra, evidence_extra):
    """A native-identity runner: patches run_mutation's inner result."""
    def inner(*_args, _evidence=None, **_kwargs):
        _evidence.update(evidence_extra)
        return [_skip(fingerprint=fingerprint, description=description)], list(infra)
    return inner


_NATIVE_133 = [
    ("mutation-unavailable", "mutmut not installed (soft dependency)", [], {},
     "CI: mutation not measured: mutmut not installed (soft dependency)"),
    ("mutation-probe-timeout", "mutmut import probe timed out in baseline test env; x", [], {},
     "CI: mutation not measured: mutmut import probe timed out in baseline test env"),
    ("mutation-no-mutants", "mutmut generated no mutants; no mutation measurement was made", [],
     {"results_sha256": "r", "metadata_sha256": "m"}, "CI: mutation not measured: mutmut generated no mutants"),
    ("mutation-results-timeout", "mutmut results timed out", [],
     {"metadata_sha256": "m"},
     "CI: mutation not measured: mutmut results timed out"),
    ("mutation-other-adapter", "app.ts: ts adapter registered", [_OTHER_INFRA], {},
     "CI: mutation not measured: other-language adapters are not invoked by this run: app.ts: ts adapter registered"),
]


@pytest.mark.parametrize("fingerprint,description,infra,extra,reason", _NATIVE_133)
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_native_133_skips_have_accurate_reason(ci_project, monkeypatch, mode, fingerprint, description, infra, extra, reason):
    p = ci_project
    monkeypatch.setattr(mutation, "_run_mutation", _native_skip_runner(fingerprint, description, infra, extra))
    machine = (_machine(p, runner=mutation.run_mutation) if mode is Mode.CI
               else _local_machine(p, runner=mutation.run_mutation))
    findings = machine._run_l2_phase()
    assert [f.fingerprint for f in findings] == [fingerprint]
    errors = machine._state.infra_errors
    assert not any("invalid bound mutation measurement" in e for e in errors)
    if mode is Mode.CI:
        assert machine._mutation_bound_failure is True
        assert reason in errors
    else:
        assert machine._mutation_bound_failure is False
        assert not any(e.startswith("CI: mutation not measured") for e in errors)
        assert all(f.disposition is Disposition.DISMISSED for f in findings)


# --- #136: orphan mutation container -----------------------------------------


def _orphan(p, kind="config"):
    container = p.root / ".code-forge" / f"mutation-empty-{kind}"
    container.mkdir()
    return container


@pytest.mark.parametrize("kind", ["config", "mirror"])
def test_ci_orphan_container_fails_with_path_and_recovery(ci_project, kind):
    p = ci_project
    container = _orphan(p, kind)
    machine = _machine(p, runner=mutation.run_mutation)
    assert machine.run() is Verdict.FAIL
    saved = _saved(p, Verdict.FAIL)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    reasons = [e for e in saved.infra_errors
               if e.startswith("L2: mutation not measured: orphan mutation container")]
    assert len(reasons) == 1
    assert str(container) in reasons[0] and "aside" in reasons[0]
    assert container.is_dir(), "orphan must never be deleted automatically"


def test_local_orphan_container_refused_before_round_1(ci_project):
    """#138: the orphan is found before any round, so no L1 pass runs."""
    from code_forge.machine import TimeoutBreaker

    p = ci_project
    container = _orphan(p)
    lock = p.root / ".code-forge/mutation-owner.lock"
    machine = _local_machine(p, runner=mutation.run_mutation)
    rounds = []
    machine._execute_round = rounds.append
    with pytest.raises(TimeoutBreaker, match="stopped before round 1: mutation workspace needs manual recovery") as caught:
        machine.run()
    assert str(container) in str(caught.value)
    assert rounds == []
    saved = load_state(p.state / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL
    assert any(e.startswith("L2: mutation not measured: orphan mutation container") for e in saved.infra_errors)
    assert container.is_dir() and not lock.exists(), "preflight is read-only"


def test_local_orphan_appearing_mid_run_still_stops_after_that_round(ci_project):
    """The in-round #136 stop remains for an orphan created after preflight."""
    from code_forge.machine import TimeoutBreaker

    p = ci_project
    machine = _local_machine(p, runner=mutation.run_mutation)
    rounds = []
    original = machine._execute_round

    def counted(index):
        rounds.append(index)
        if len(rounds) == 1:
            _orphan(p)
        return original(index)

    machine._execute_round = counted
    with pytest.raises(TimeoutBreaker, match="mutation workspace needs manual recovery \\(not an LLM timeout") as caught:
        machine.run()
    container = p.root / ".code-forge" / "mutation-empty-config"
    assert str(container) in str(caught.value)
    assert len(rounds) == 1
    saved = load_state(p.state / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL
    assert not any(e.startswith("tiered-reset") for e in saved.infra_errors)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    assert container.is_dir()


@pytest.mark.parametrize("status", ["survived", "killed", "caught by type check", "timeout"])
def test_local_results_timeout_with_measured_inventory_is_not_clean(ci_project, monkeypatch, status):
    """#137 P2: mutants already measured (real final statuses from
    _mutation_inventory) must not be dismissed by a results timeout."""
    p = ci_project
    extra = {"metadata_sha256": "m", "inventory": {"calc.x__mutmut_1": status}}
    monkeypatch.setattr(mutation, "_run_mutation",
                        _native_skip_runner("mutation-results-timeout", "mutmut results timed out", [], extra))
    machine = _local_machine(p, runner=mutation.run_mutation)
    machine._run_l2_phase()
    assert machine._mutation_bound_failure is True
    assert "L2: invalid bound mutation measurement" in machine._state.infra_errors


def test_orphan_marker_text_inside_other_infra_line_does_not_stop(ci_project, monkeypatch):
    """#137 P3: only the structured marker counts, not matching text."""
    p = ci_project
    noisy = ("pytest output: E   orphan mutation container has no completed ownership record: "
             "/x; it is not removed automatically")

    def inner(*_args, _evidence=None, **_kwargs):
        err = _skip(fingerprint="mutation-evidence-error", description=noisy)
        err.id = "MUTATION_ERROR"
        return [err], [noisy]

    monkeypatch.setattr(mutation, "_run_mutation", inner)
    machine = _local_machine(p, runner=mutation.run_mutation)
    machine._run_l2_phase()
    assert machine._mutation_workspace_blocker is None
    assert "L2: invalid bound mutation measurement" in machine._state.infra_errors


def test_orphan_error_records_structured_marker(ci_project):
    p = ci_project
    container = _orphan(p)
    evidence = {}
    mutation.run_mutation(["calc.py"], list(BASELINE), cwd=p.root, _evidence=evidence)
    marker = evidence["workspace_orphan"]
    assert marker["path"] == str(container)
    assert marker["message"].startswith("orphan mutation container has no completed ownership record: ")



def test_orphan_marker_message_must_be_an_exact_infra_line(ci_project, monkeypatch):
    """#137 forge P3: the structured marker alone is not enough; its message
    must also be an exact infra line."""
    p = ci_project
    message = "orphan mutation container has no completed ownership record: /x; it is not removed"

    def inner(*_args, _evidence=None, **_kwargs):
        _evidence["workspace_orphan"] = {"path": "/x", "message": message}
        err = _skip(fingerprint="mutation-evidence-error", description=message + " (wrapped)")
        err.id = "MUTATION_ERROR"
        return [err], [message + " (wrapped)"]

    monkeypatch.setattr(mutation, "_run_mutation", inner)
    machine = _local_machine(p, runner=mutation.run_mutation)
    machine._run_l2_phase()
    assert machine._mutation_workspace_blocker is None
    assert "L2: invalid bound mutation measurement" in machine._state.infra_errors


def test_orphan_marker_with_exact_infra_line_stops(ci_project, monkeypatch):
    """Control for the test above."""
    p = ci_project
    message = "orphan mutation container has no completed ownership record: /x; it is not removed"

    def inner(*_args, _evidence=None, **_kwargs):
        _evidence["workspace_orphan"] = {"path": "/x", "message": message}
        err = _skip(fingerprint="mutation-evidence-error", description=message)
        err.id = "MUTATION_ERROR"
        return [err], [message]

    monkeypatch.setattr(mutation, "_run_mutation", inner)
    machine = _local_machine(p, runner=mutation.run_mutation)
    machine._run_l2_phase()
    assert machine._mutation_workspace_blocker == message


# --- #138: read-only orphan preflight -----------------------------------------


def _ws(tmp_path):
    (tmp_path / ".code-forge").mkdir()
    return tmp_path / ".code-forge"


def test_find_orphan_container_cases(tmp_path):
    from code_forge._mutation_workspace import find_orphan_container

    folder = _ws(tmp_path)
    assert find_orphan_container(tmp_path) is None
    container = folder / "mutation-empty-config"
    container.mkdir()
    assert find_orphan_container(tmp_path) == container  # no journal at all
    lock = folder / "mutation-owner.lock"
    lock.write_text(json.dumps({"phase": "complete", "token": "t"}))
    assert find_orphan_container(tmp_path) == container  # complete, unrecorded
    lock.write_text(json.dumps({"phase": "complete", "empty_containers": {"config": [1, 2]}}))
    assert find_orphan_container(tmp_path) is None  # recorded: owned
    lock.write_text(json.dumps({"phase": "incomplete", "empty_containers": {}}))
    assert find_orphan_container(tmp_path) is None  # acquire() reports its own error
    lock.write_text("not json")
    assert find_orphan_container(tmp_path) is None
    lock.write_text("")
    assert find_orphan_container(tmp_path) is None
    before = lock.read_bytes()
    assert container.is_dir() and lock.read_bytes() == before


def test_find_orphan_container_skips_a_live_lease(tmp_path):
    import fcntl

    from code_forge._mutation_workspace import find_orphan_container

    folder = _ws(tmp_path)
    (folder / "mutation-empty-mirror").mkdir()
    lock = folder / "mutation-owner.lock"
    lock.write_text(json.dumps({"phase": "complete"}))
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert find_orphan_container(tmp_path) is None
    finally:
        os.close(fd)
    assert find_orphan_container(tmp_path) == folder / "mutation-empty-mirror"


def test_preflight_skips_non_python_diff_and_custom_runner(ci_project, monkeypatch):
    p = ci_project
    _orphan(p)
    machine = _local_machine(p, runner=mutation.run_mutation)
    monkeypatch.setattr(machine, "_executable_source_files", lambda: [Path("README.md")])
    machine._preflight_mutation_workspace()  # no raise: no Python work
    custom = _local_machine(p, runner=lambda *a, **k: ([], []))
    custom._preflight_mutation_workspace()  # no raise: not the native workspace


# --- #135: test code by directory, not by name --------------------------------


@pytest.mark.parametrize("path,expected", [
    ("pkg/test_helpers.py", False), ("pkg/api_test.py", False), ("test_calc.py", False),
    ("framework/test/select.py", False), ("tests/test_calc.py", True),
    ("pkg/tests/test_x.py", True), ("conftest.py", True), ("pkg/conftest.py", True),
])
def test_default_test_paths_are_directory_based(path, expected):
    assert mutation._is_test_path(path) is expected


@pytest.mark.parametrize("path", ["pkg/test_helpers.py", "pkg/api_test.py"])
def test_production_module_named_like_test_is_not_tests_only(ci_project, monkeypatch, path):
    from code_forge.machine import _default_tests_only

    assert _default_tests_only([path]) is False
    p = ci_project
    machine = _ci_with_diff(p, monkeypatch, {path: "def helper():\n    return 1\n"})
    findings = machine._run_l2_phase()
    assert "mutation-tests-only" not in [f.fingerprint for f in findings]


# --- #139 review P2: globs from the baseline revision, CLI wiring -----------


def _cli_wired(p, *, mode=Mode.CI, review=None):
    """The CLI's real wiring: config root == source root == reviewed tree."""
    machine = _machine(p, runner=mutation.run_mutation, review=review)
    machine.cwd = p.root
    machine.source_root = None
    if mode is Mode.LOCAL:
        machine.mode = Mode.LOCAL
        machine._state.mode = Mode.LOCAL
    return machine


def _spy_skip_globs(monkeypatch):
    import inspect

    seen = []
    real = mutation._run_mutation

    def spy(*args, **kwargs):
        bound = inspect.signature(real).bind(*args, **kwargs)
        seen.append((bound.arguments["mutation_skip_globs"], bound.arguments["mutation_include_globs"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(mutation, "_run_mutation", spy)
    return seen


def _track_gate(p):
    """Commit the fixture gate.yaml so the review baseline carries it (#140:
    an untracked gate.yaml is not part of any change and is used as-is)."""
    _git(p.root, "add", "-f", ".code-forge/gate.yaml")
    _git(p.root, "-c", "user.name=CI Control", "-c", "user.email=ci-control@example.invalid",
         "commit", "-qm", "track gate")


def _set_root_globs(p, skip, *, commit=False):
    gate = p.root / ".code-forge/gate.yaml"
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = skip
    gate.write_text(json.dumps(data), encoding="utf-8")
    if commit:
        _git(p.root, "add", "-f", ".code-forge/gate.yaml")
        _git(p.root, "-c", "user.name=CI Control", "-c", "user.email=ci-control@example.invalid",
             "commit", "-qm", "baseline globs")
    return gate


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_pr_edit_to_skip_globs_does_not_apply_in_its_own_review(ci_project, monkeypatch, mode):
    """A PR that adds calc.py to mutation_skip_globs: calc.py is still mutated."""
    p = ci_project
    _track_gate(p)
    _set_root_globs(p, ["calc.py"])
    seen = _spy_skip_globs(monkeypatch)
    machine = _cli_wired(p, mode=mode, review=_review(p))
    findings = machine._run_l2_phase()
    assert seen == [(None, None)]
    assert "mutation-tests-only" not in [f.fingerprint for f in findings]


def test_baseline_skip_globs_are_honoured_as_opt_out(ci_project, monkeypatch):
    p = ci_project
    _set_root_globs(p, ["calc.py"], commit=True)
    seen = _spy_skip_globs(monkeypatch)
    machine = _cli_wired(p, review=_review(p))
    findings = machine._run_l2_phase()
    assert seen == [(["calc.py"], None)]
    assert [f.fingerprint for f in findings] == ["mutation-tests-only"]


def test_pr_cannot_override_baseline_globs_either(ci_project, monkeypatch):
    p = ci_project
    gate = _set_root_globs(p, ["other/**"], commit=True)
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = ["calc.py"]
    gate.write_text(json.dumps(data), encoding="utf-8")
    seen = _spy_skip_globs(monkeypatch)
    _cli_wired(p, review=_review(p))._run_l2_phase()
    assert seen == [(["other/**"], None)]


def test_ci_detached_launch_uses_baseline_globs(ci_project, monkeypatch):
    from code_forge import machine as machine_mod

    p = ci_project
    _track_gate(p)
    _set_root_globs(p, ["calc.py"])
    _discover_mutmut(p)
    captured = {}

    def fake_launch(*args, **kwargs):
        captured.update(kwargs)
        return False

    monkeypatch.setattr(machine_mod, "launch_detached_mutation", fake_launch)
    machine = _cli_wired(p, review=_review(p))
    monkeypatch.setattr(machine, "_run_l2_phase", lambda: [])
    machine.run()
    assert captured, "detached launch must have been attempted"
    assert captured["mutation_skip_globs"] is None


def test_trusted_mutation_globs_sources(tmp_path):
    from code_forge.mutation import trusted_mutation_globs

    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n")
    _git(root, "add", "a.py")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-qm", "a")
    no_gate = _git(root, "rev-parse", "HEAD")
    (root / ".code-forge").mkdir()
    (root / ".code-forge/gate.yaml").write_text(
        "test:\n  command: [pytest]\n  mutation_skip_globs: [a.py]\n  mutation_include_globs: [b.py]\n"
    )
    _git(root, "add", "-f", ".code-forge/gate.yaml")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-qm", "g")
    with_gate = _git(root, "rev-parse", "HEAD")
    (root / ".code-forge/gate.yaml").write_text(": [ not yaml")
    _git(root, "add", "-f", ".code-forge/gate.yaml")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-qm", "bad")
    bad = _git(root, "rev-parse", "HEAD")
    worktree_test = {"mutation_skip_globs": ["edited.py"]}
    same = dict(config_root=root, source_root=root, config_test=worktree_test)
    assert trusted_mutation_globs(base_sha=with_gate, **same) == (["a.py"], ["b.py"])
    assert trusted_mutation_globs(base_sha=no_gate, **same) == (None, None)
    assert trusted_mutation_globs(base_sha=bad, **same) == (None, None)
    assert trusted_mutation_globs(base_sha=None, **same) == (None, None)
    assert trusted_mutation_globs(base_sha="0" * 40, **same) == (None, None)
    # A config root that contains the reviewed tree is not "outside" it.
    sub = root / "pkg"
    sub.mkdir()
    assert trusted_mutation_globs(config_root=root, source_root=sub, base_sha=None,
                                  config_test=worktree_test) == (None, None)
    outside = tmp_path / "state"
    outside.mkdir()
    assert trusted_mutation_globs(config_root=outside, source_root=root, base_sha=with_gate,
                                  config_test=worktree_test) == (["edited.py"], None)


def test_admission_uses_baseline_globs(ci_project, monkeypatch):
    """INDEX/COMMIT admission decides selection from the same trusted globs."""
    p = ci_project
    _track_gate(p)
    _set_root_globs(p, ["calc.py"])
    calls = []
    real = mutation.trusted_mutation_globs

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(mutation, "trusted_mutation_globs", spy)
    review = _review(p)
    mutation.mutation_review_admission_error(
        target_kind="COMMIT", git_diff=review.git_diff, source_files=[p.root / "calc.py"],
        source_root=p.root, config_root=p.root, runner=mutation.run_mutation, base_sha=review.base_sha,
    )
    assert calls and calls[0]["base_sha"] == review.base_sha
    assert real(**calls[0]) == (None, None)


def test_baseline_globs_of_wrong_type_fall_back_to_defaults(ci_project):
    from code_forge.mutation import trusted_mutation_globs

    p = ci_project
    gate = p.root / ".code-forge/gate.yaml"
    data = json.loads(gate.read_text())
    data["test"]["mutation_skip_globs"] = "calc.py"
    data["test"]["mutation_include_globs"] = [1]
    gate.write_text(json.dumps(data), encoding="utf-8")
    _git(p.root, "add", "-f", ".code-forge/gate.yaml")
    _git(p.root, "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-qm", "bad types")
    base = _git(p.root, "rev-parse", "HEAD")
    assert trusted_mutation_globs(config_root=p.root, source_root=p.root, base_sha=base) == (None, None)


def test_machine_admission_receives_review_baseline(ci_project, monkeypatch):
    from code_forge import machine as machine_mod

    p = ci_project
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return "stop here"

    monkeypatch.setattr(machine_mod, "mutation_review_admission_error", spy)
    machine = _cli_wired(p)
    try:
        machine.run()
    except Exception:  # noqa: BLE001 - only the captured arguments matter
        pass
    assert seen["base_sha"] == machine.resolved_review.base_sha and seen["base_sha"]


def test_cli_admission_receives_review_baseline(ci_project, monkeypatch, capsys):
    """#139 forge P3: the real `code-forge review` entry passes the resolved
    review baseline to admission, so admission reads the baseline gate.yaml."""
    p = ci_project
    _discover_mutmut(p)
    _git(p.root, "add", "calc.py")
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return "admission stop (test)"

    monkeypatch.setattr(mutation, "mutation_review_admission_error", spy)
    monkeypatch.chdir(p.root)
    argv = ["code-forge", "review", "--mode", "ci", "--falsification-engine", "stub",
            "--baseline", "HEAD", "--head", "INDEX", "--allow-main", "calc.py"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    err = capsys.readouterr().err
    assert seen, err
    assert seen["base_sha"] == _git(p.root, "rev-parse", "HEAD")
    assert seen["config_root"] == seen["source_root"] == p.root
    assert "admission stop (test)" in err


def _cli_admission_args(p, monkeypatch, *extra):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return "admission stop (test)"

    monkeypatch.setattr(mutation, "mutation_review_admission_error", spy)
    monkeypatch.chdir(p.root)
    monkeypatch.setattr(sys, "argv", [
        "code-forge", "review", "--mode", "ci", "--falsification-engine", "stub",
        "--baseline", "HEAD", "--head", "INDEX", "--allow-main", *extra, "calc.py",
    ])
    return cli.main(), seen


def test_cli_test_config_root_option_and_env(ci_project, monkeypatch, capsys):
    """#142 P2-B: CI supplies the gate from outside the checkout via
    --test-config-root DIR or FORGE_TEST_CONFIG_ROOT; inside is refused."""
    p = ci_project
    _discover_mutmut(p)
    _git(p.root, "add", "calc.py")
    outside = _outside_ci_config(p, monkeypatch)
    _, seen = _cli_admission_args(p, monkeypatch)  # env var
    assert seen["config_root"] == outside and seen["allow_untracked"] is False, capsys.readouterr().err
    monkeypatch.delenv("FORGE_TEST_CONFIG_ROOT")
    _, seen = _cli_admission_args(p, monkeypatch, "--test-config-root", str(outside))
    assert seen["config_root"] == outside
    _, seen = _cli_admission_args(p, monkeypatch)  # neither: the checkout itself
    assert seen["config_root"] == p.root
    inside = p.root / "cfg"
    (inside / ".code-forge").mkdir(parents=True)
    (inside / ".code-forge/gate.yaml").write_bytes((p.root / ".code-forge/gate.yaml").read_bytes())
    capsys.readouterr()
    code, seen = _cli_admission_args(p, monkeypatch, "--test-config-root", str(inside))
    assert code == 2 and not seen and "must be outside the reviewed checkout" in capsys.readouterr().err
    code, seen = _cli_admission_args(p, monkeypatch, "--test-config-root", str(p.root.parent / "nowhere"))
    assert code == 2 and not seen and "no .code-forge/gate.yaml there" in capsys.readouterr().err


def test_cli_test_config_root_flag_beats_env(ci_project, monkeypatch, capsys):
    p = ci_project
    _discover_mutmut(p)
    _git(p.root, "add", "calc.py")
    env_root = _outside_ci_config(p, monkeypatch)
    flag_root = p.root.parent / (p.root.name + "-flag-config")
    (flag_root / ".code-forge").mkdir(parents=True)
    (flag_root / ".code-forge/gate.yaml").write_bytes((env_root / ".code-forge/gate.yaml").read_bytes())
    _, seen = _cli_admission_args(p, monkeypatch, "--test-config-root", str(flag_root))
    assert seen["config_root"] == flag_root != env_root, capsys.readouterr().err


def test_cli_test_config_root_symlink_into_checkout_is_refused(ci_project, monkeypatch, capsys):
    p = ci_project
    _discover_mutmut(p)
    _git(p.root, "add", "calc.py")
    monkeypatch.delenv("FORGE_TEST_CONFIG_ROOT", raising=False)
    link = p.root.parent / (p.root.name + "-link-config")
    link.symlink_to(p.root, target_is_directory=True)
    capsys.readouterr()
    code, seen = _cli_admission_args(p, monkeypatch, "--test-config-root", str(link))
    assert code == 2 and not seen and "must be outside the reviewed checkout" in capsys.readouterr().err


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_busy_host_is_an_explicit_skip_not_a_baseline(ci_project, monkeypatch, mode):
    """#282: a host that never goes idle runs no baseline. CI FAILs naming the
    busy host; LOCAL keeps a DISMISSED skip plus an infra line (no clean credit)."""
    from code_forge import host_idle

    p = ci_project
    _failing_baseline(monkeypatch, p)
    monkeypatch.setenv("FORGE_MUTATION_IDLE_MAX_WAIT", "0.05")
    monkeypatch.setenv("FORGE_MUTATION_IDLE_POLL", "0.01")
    monkeypatch.setattr(host_idle, "read_signals", lambda: {"load_per_cpu": 3.0})
    machine = _machine(p, runner=mutation.run_mutation) if mode is Mode.CI else _local_machine(p, runner=mutation.run_mutation)
    verdict = machine.run()
    saved = load_state(p.state / ".code-forge/state.json")
    assert "baseline" not in p.stages
    assert any(f.fingerprint == "mutation-host-busy" and f.disposition is Disposition.DISMISSED for f in saved.findings)
    assert any(e.startswith("host not idle after") and "loadavg/cpu 3.00 > 0.5" in e for e in saved.infra_errors)
    assert not any("invalid bound mutation measurement" in e for e in saved.infra_errors)
    reasons = [e for e in saved.infra_errors if e.startswith("CI: mutation not measured")]
    if mode is Mode.CI:
        assert verdict is Verdict.FAIL
        assert len(reasons) == 1 and "host not idle after" in reasons[0]
    else:
        assert verdict is not Verdict.FAIL and reasons == []
