# SPDX-License-Identifier: Apache-2.0
"""Partial #335 packaging controls, NOT native mutation-engine qualification."""

from dataclasses import replace
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.ledger import LedgerRow, TerminalState, append_row, iter_rows
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict, load_state

from conftest import CONSOLE_BOOTSTRAP, audit_event, verify_install


def test_installed_modules_match_wheel_record():
    assert "code_forge.machine" in verify_install()


def test_installed_resources():
    root = resources.files("code_forge")
    assert json.loads(root.joinpath("gate.schema.json").read_text())["type"] == "object"
    assert "MIT License" in root.joinpath("THIRD_PARTY_NOTICES.md").read_text()


def test_source_shadow_is_rejected(monkeypatch, tmp_path):
    import importlib
    import code_forge

    original = Path(code_forge.__file__).read_bytes()
    for parent in (tmp_path / "external", Path(sys.prefix) / "shadow-control"):
        shadow = parent / "code_forge"
        shadow.mkdir(parents=True)
        (shadow / "__init__.py").write_bytes(original)
        with monkeypatch.context() as patch:
            patch.syspath_prepend(str(parent))
            patch.delitem(sys.modules, "code_forge")
            imported = importlib.import_module("code_forge")
            assert Path(imported.__file__).parent == shadow
            with pytest.raises(AssertionError):
                verify_install()


def test_editable_metadata_is_rejected(monkeypatch):
    from importlib import metadata

    real = metadata.distribution
    dist = real("code-review-forge")
    read = dist.read_text
    monkeypatch.setattr(
        dist,
        "read_text",
        lambda name: '{"dir_info":{"editable":true}}' if name == "direct_url.json" else read(name),
    )
    monkeypatch.setattr(
        metadata, "distribution", lambda name: dist if name == "code-review-forge" else real(name)
    )
    with pytest.raises(AssertionError):
        verify_install()


def test_network_and_process_tripwires(project):
    import conftest

    # Exercise the registered hook itself, without making a socket or spawning.
    # Only these exact expected audit violations are consumed; any earlier or
    # later unexpected violation remains fatal at session teardown.
    for event, args in [
        ("socket.connect", (None, None)),
        ("subprocess.Popen", ("git", ["git", "fetch"], str(project), dict(os.environ))),
    ]:
        before = len(conftest.VIOLATIONS)
        with pytest.raises(AssertionError):
            sys.audit(event, *args)
        assert len(conftest.VIOLATIONS) == before + 1
        conftest.VIOLATIONS.pop()
    events = [
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.__new__",
        "os.system",
        "os.forkpty",
    ]
    for event in events:
        args = ()
        with pytest.raises(AssertionError):
            audit_event(event, args, console=True)
    with pytest.raises(AssertionError):
        audit_event(
            "subprocess.Popen", ("git", ["git", "fetch"], str(project), dict(os.environ)), console=True
        )
    injected = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor"}
    with pytest.raises(AssertionError):
        audit_event(
            "subprocess.Popen",
            ("git", ["git", "rev-parse", "--git-dir"], str(project), injected),
            console=True,
        )
    config = project / ".git/config"
    original = config.read_bytes()
    try:
        config.write_bytes(original + b"\n[core]\nfsmonitor = forbidden-command\n")
        with pytest.raises(AssertionError):
            audit_event(
                "subprocess.Popen",
                ("git", ["git", "rev-parse", "--git-dir"], str(project), dict(os.environ)),
                console=True,
            )
    finally:
        config.write_bytes(original)


def console(project, *args):
    import conftest

    proof = project.parent / "console-proof.json"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            CONSOLE_BOOTSTRAP,
            str(Path(conftest.__file__).resolve()),
            str(Path(sys.prefix) / "bin/code-forge"),
            *args,
        ],
        cwd=project,
        env={**os.environ, "WHEEL_CONSOLE_PROOF": str(proof)},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 0:
        child = json.loads(proof.read_text())
        assert "code_forge.cli" in child["modules"]
        assert not child["violations"]
        conftest.CONSOLE_PROOFS.append(child)
    return result


def test_console_help(project):
    result = console(project, "--help")
    assert result.returncode == 0, result.stderr
    assert "ledger" in result.stdout


def test_console_empty_ledger_is_read_only(project):
    result = console(project, "ledger", "list", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert not (project / ".code-forge/ledger.jsonl").exists()


def seed_row(project):
    return LedgerRow(
        fingerprint="wheel-control",
        repo_root=str(project),
        base_sha="",
        head_sha="",
        file="example.txt",
        line=1,
        axis_claim="review",
        pass_provenance="fixture",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="synthetic",
        ts="2026-01-01T00:00:00Z",
    )


def test_console_seeded_ledger_is_read_only(project):
    append_row(project, seed_row(project))
    path = project / ".code-forge/ledger.jsonl"
    before = path.read_bytes()
    result = console(project, "ledger", "list", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["fingerprint"] == "wheel-control"
    assert path.read_bytes() == before


def machine(project, findings=()):
    # Synthetic L0 acquisition only. No source mutation, provider, command runner,
    # native eligibility, source-guard replacement, or environment bypass.
    return StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda _finding: pytest.fail("CI attempted autofix"),
        resolved_review=ResolvedReview(
            [],
            None,
            None,
            "git",
            base_sha=os.environ["WHEEL_FIXTURE_REV"],
            head_sha=os.environ["WHEEL_FIXTURE_REV"],
        ),
        source_hash="installed-wheel-synthetic-l0",
        baseline_spec_repr="empty fixture",
        cwd=project,
        registry={},
        l0_runner=lambda *_args: (list(findings), []),
    )


def test_ci_malformed_result_persists_failure(project, monkeypatch):
    monkeypatch.chdir(project)
    carrier = project / ".code-forge/mutation-result.json"
    carrier.write_text('{"status":')
    control = machine(project)
    assert control.run() is Verdict.FAIL
    saved = load_state(project / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL and not saved.converged
    assert any("failed to read mutation-result.json" in item for item in saved.infra_errors)
    assert carrier.read_text() == '{"status":'


def test_ci_synthetic_confirmed_finding_reaches_ledger(project, monkeypatch):
    monkeypatch.chdir(project)
    finding = StateFinding(
        id="wheel-control",
        fingerprint="wheel-control",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="example.txt",
        line_range=[1, 1],
        description="Synthetic installed-package consumer control",
    )
    control = machine(project, [finding])
    assert control.run() is Verdict.FAIL
    saved = load_state(project / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL and not saved.converged
    rows = list(iter_rows(project))
    assert len(rows) == 1
    assert rows[0].fingerprint == finding.fingerprint
    assert rows[0].terminal_state is TerminalState.UNADJUDICATED
    assert control._mutation_measurement_eligible is False


def assert_saved_ci_findings(saved, finding):
    # The real empty-diff CI path persists this diagnostic alongside acquisition.
    assert saved.findings == [
        finding,
        StateFinding(
            id="MUTATION_SKIPPED",
            fingerprint="mutation-no-adapter",
            source="MUTANT",
            disposition=Disposition.DISMISSED,
            file="",
            line_range=[],
            description="no registered mutation adapter for this diff",
        ),
    ]


@pytest.mark.parametrize(
    "terminal,reversal",
    [
        pytest.param(terminal, reversal, id=f"{terminal.value}-{reversal.value}")
        for terminal in (TerminalState.FIXED, TerminalState.DISPROVED, TerminalState.DUPLICATE)
        for reversal in (TerminalState.UNADJUDICATED, TerminalState.ESCAPED)
    ],
)
def test_ci_terminal_ledger_revocation_reopens_finding(project, monkeypatch, terminal, reversal):
    """Fresh CI invocations reread ordinary adjudication, not prior session State."""
    monkeypatch.chdir(project)
    original = StateFinding(
        id="wheel-revocation",
        fingerprint="wheel-revocation",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="example.txt",
        line_range=[1, 1],
        description="Synthetic installed-package ordinary ledger revocation control",
    )
    state_path = project / ".code-forge/state.json"
    ledger_path = project / ".code-forge/ledger.jsonl"
    note = f"ledger: suppressed {original.fingerprint} (known terminal fingerprint)"

    # Each invocation gets a fresh finding because suppression mutates it.
    assert machine(project, [replace(original)]).run() is Verdict.FAIL
    initial = load_state(state_path)
    assert initial.verdict is Verdict.FAIL and not initial.converged
    assert_saved_ci_findings(initial, original)
    assert note not in initial.infra_errors
    rows = list(iter_rows(project, strict=True))
    assert len(rows) == 1
    pending = rows[0]
    assert pending.fingerprint == original.fingerprint
    assert pending.terminal_state is TerminalState.UNADJUDICATED
    assert pending.base_sha == pending.head_sha == os.environ["WHEEL_FIXTURE_REV"]
    initial_bytes = ledger_path.read_bytes()

    # Reuse the real CI row's identity, including its suppression key.
    ruling = replace(pending, terminal_state=terminal)
    append_row(project, ruling)
    adjudicated_bytes = ledger_path.read_bytes()
    assert adjudicated_bytes.startswith(initial_bytes)
    assert list(iter_rows(project, strict=True)) == [pending, ruling]
    assert machine(project, [replace(original)]).run() is Verdict.PASS
    suppressed = load_state(state_path)
    assert suppressed.verdict is Verdict.PASS and suppressed.converged
    assert_saved_ci_findings(suppressed, replace(original, disposition=Disposition.DISMISSED))
    assert suppressed.infra_errors.count(note) == 1
    rows = list(iter_rows(project, strict=True))
    assert len(rows) == 3 and rows[:2] == [pending, ruling]
    clean = rows[2]
    assert (
        clean.fingerprint
        == hashlib.sha256(f"clean:{pending.base_sha}:{pending.head_sha}".encode()).hexdigest()[:16]
    )
    assert clean.base_sha == pending.base_sha and clean.head_sha == pending.head_sha
    assert clean.terminal_state is TerminalState.UNADJUDICATED
    assert (clean.axis_claim, clean.evidence_class, clean.file, clean.line) == (
        "clean",
        "clean_pass",
        "",
        0,
    )
    suppressed_bytes = ledger_path.read_bytes()
    assert suppressed_bytes.startswith(adjudicated_bytes)

    # Equal timestamps deliberately pin last-append precedence. Leave prior
    # PASS state.json in place: CI must read the ledger, not restore that State.
    revoked = replace(pending, terminal_state=reversal)
    append_row(project, revoked)
    revoked_bytes = ledger_path.read_bytes()
    assert revoked_bytes.startswith(suppressed_bytes)
    assert machine(project, [replace(original)]).run() is Verdict.FAIL
    reopened = load_state(state_path)
    assert reopened.verdict is Verdict.FAIL and not reopened.converged
    assert_saved_ci_findings(reopened, original)
    assert note not in reopened.infra_errors
    assert ledger_path.read_bytes() == revoked_bytes
    assert list(iter_rows(project, strict=True)) == [pending, ruling, clean, revoked]


def test_ci_terminal_ledger_cannot_suppress_synthetic_mutation_survivor(project, monkeypatch):
    """Ordinary fingerprint rulings cannot dismiss a synthetic current survivor."""
    monkeypatch.chdir(project)
    survivor = StateFinding(
        id="mutant-wheel-control",
        fingerprint="wheel-mutation-survivor",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="example.txt",
        line_range=[1, 1],
        description="Synthetic survivor exclusion control, not a native engine result",
    )
    assert machine(project, [replace(survivor)]).run() is Verdict.FAIL
    rows = list(iter_rows(project, strict=True))
    assert len(rows) == 1
    pending = rows[0]
    assert pending.fingerprint == survivor.fingerprint
    assert pending.terminal_state is TerminalState.UNADJUDICATED
    ruling = replace(pending, terminal_state=TerminalState.FIXED)
    append_row(project, ruling)
    ledger_path = project / ".code-forge/ledger.jsonl"
    before = ledger_path.read_bytes()

    control = machine(project, [replace(survivor)])
    assert control.run() is Verdict.FAIL
    saved = load_state(project / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL and not saved.converged
    assert_saved_ci_findings(saved, survivor)
    assert not any("ledger: suppressed" in note for note in saved.infra_errors)
    assert ledger_path.read_bytes() == before
    assert list(iter_rows(project, strict=True)) == [pending, ruling]
    assert control._mutation_measurement_eligible is False


def assert_ci_carrier_refused(project, monkeypatch, raw, reason):
    """Installed consumer rejection, not native measurement qualification."""
    monkeypatch.chdir(project)
    append_row(project, seed_row(project))
    ledger = project / ".code-forge/ledger.jsonl"
    before = ledger.read_bytes()
    carrier = project / ".code-forge/mutation-result.json"
    carrier.write_text(raw)
    control = machine(project)
    assert control.run() is Verdict.FAIL
    saved = load_state(project / ".code-forge/state.json")
    assert saved.verdict is Verdict.FAIL and not saved.converged
    assert reason in saved.infra_errors
    assert not carrier.exists()
    assert ledger.read_bytes() == before
    rows = list(iter_rows(project, strict=True))
    assert len(rows) == 1 and rows[0].fingerprint == "wheel-control"
    assert all(row.evidence_class != "clean_pass" for row in rows)
    assert control._mutation_measurement_eligible is False


def test_ci_nonobject_carrier_cannot_emit_clean_ledger(project, monkeypatch):
    assert_ci_carrier_refused(
        project, monkeypatch, "[]", "CI: mutation-result.json is not a JSON object"
    )


def test_ci_missing_status_cannot_emit_clean_ledger(project, monkeypatch):
    assert_ci_carrier_refused(
        project, monkeypatch, "{}", "CI: mutation-result.json missing status field"
    )


def test_ci_invalid_status_cannot_emit_clean_ledger(project, monkeypatch):
    assert_ci_carrier_refused(
        project,
        monkeypatch,
        '{"status":"unexpected"}',
        "CI: mutation-result.json invalid status field",
    )


def test_record_and_wheel_mismatch_rejected(tmp_path, monkeypatch):
    import code_forge

    origin = Path(code_forge.__file__)
    original = origin.read_bytes()
    try:
        origin.write_bytes(original + b"\n# deliberate isolated-install corruption\n")
        with pytest.raises(AssertionError):
            verify_install()
    finally:
        origin.write_bytes(original)
    wrong_wheel = tmp_path / "wrong.whl"
    wrong_wheel.write_bytes(Path(os.environ["WHEEL_ARTIFACT"]).read_bytes() + b"wrong artifact")
    monkeypatch.setenv("WHEEL_ARTIFACT", str(wrong_wheel))
    with pytest.raises(AssertionError):
        verify_install()


def test_roster_rejects_missing_and_skipped_cases(tmp_path):
    import runpy
    import xml.etree.ElementTree as ET

    harness = runpy.run_path(str(Path(__file__).with_name("wheel_harness.py")))
    roster = sorted(harness["ROSTER"])
    report = tmp_path / "report.xml"
    suite = ET.Element("testsuite")
    for name in roster:
        ET.SubElement(suite, "testcase", name=name)
    ET.ElementTree(suite).write(report)
    assert harness["verify_report"](report) == len(roster)
    ET.SubElement(suite[0], "skipped")
    ET.ElementTree(suite).write(report)
    with pytest.raises(RuntimeError, match="zero skips"):
        harness["verify_report"](report)
    suite.remove(suite[0])
    ET.ElementTree(suite).write(report)
    with pytest.raises(RuntimeError, match="roster"):
        harness["verify_report"](report)
