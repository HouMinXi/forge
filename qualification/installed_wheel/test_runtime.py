# SPDX-License-Identifier: Apache-2.0
"""Partial #335 packaging controls, NOT native mutation-engine qualification."""

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
