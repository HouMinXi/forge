# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for StateMachine._execute_round contract.

Covers L0 auto-CONFIRMED, L1 falsification, FP-04 precedence,
B4 stub error catch, and ToolError -> infra_errors (R1 H2).
"""

import hashlib
from pathlib import Path


from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding


def _make_finding(fp="fp-r-1", disp=Disposition.CONFIRMED, source="L0"):
    return StateFinding(
        id=fp,
        fingerprint=fp,
        source=source,
        disposition=disp,
        file="test.py",
        line_range=[1, 1],
        description="test finding",
    )


def _make_resolved():
    return ResolvedReview(
        source_files=[Path("test.py")],
        baseline_content=None,
        git_diff=None,
        mode_hint="git",
    )


def _make_ci_machine(tmp_path, l0_runner, l1_provider=None,
                     falsifier=None):
    """Build a CI machine to test single round behavior."""
    return StateMachine(
        mode=Mode.CI,
        falsifier=falsifier or StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=_make_resolved(),
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry={},
        l0_runner=l0_runner,
        l1_provider=l1_provider or (lambda: ([], [], Usage(), 0.0)),
    )


class TestL0AutoConfirmed:
    """(a) L0 findings auto-CONFIRMED with correct fingerprint format."""

    def test_l0_confirmed(self, tmp_path, monkeypatch):
        # Isolate from the mutmut-presence axis: this test is about
        # L0 finding auto-CONFIRM, not about whether mutmut is installed.
        monkeypatch.setattr("shutil.which", lambda cmd: "/fake/mutmut")
        finding = _make_finding(fp="fp-l0")
        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: ([finding], []),
        )
        machine.run()
        assert len(machine._state.findings) == 1
        assert machine._state.findings[0].disposition == (
            Disposition.CONFIRMED
        )

    def test_interim_fingerprint_format(self, tmp_path):
        """R1 B4: sha256(tool:file:line:rule_id)[:16]."""
        fp_raw = "ruff:test.py:10:E001"
        expected = hashlib.sha256(
            fp_raw.encode("utf-8")
        ).hexdigest()[:16]
        finding = StateFinding(
            id=expected,
            fingerprint=expected,
            source="L0",
            disposition=Disposition.CONFIRMED,
            file="test.py",
            line_range=[10, 10],
            description="unused import",
        )
        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: ([finding], []),
        )
        machine.run()
        assert machine._state.findings[0].fingerprint == expected
        assert len(expected) == 16


class TestL1Falsified:
    """(b) L1 findings passed to falsifier; result becomes disposition."""

    def test_l1_dispositioned(self, tmp_path):
        def l1_provider():
            return ([_make_finding(fp="fp-l1", source="L1")], [], Usage(), 0.0)

        # StubFalsifier default = CONFIRMED
        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: ([], []),
            l1_provider=l1_provider,
        )
        machine.run()
        found = [
            f for f in machine._state.findings
            if f.fingerprint == "fp-l1"
        ]
        assert len(found) == 1
        assert found[0].disposition == Disposition.CONFIRMED


class TestFP04Precedence:
    """(c) L0+L1 same fingerprint -> single entry, L0 wins (FP-04)."""

    def test_l0_wins_on_conflict(self, tmp_path):
        l0_f = _make_finding(fp="fp-shared", disp=Disposition.CONFIRMED)
        l1_f = _make_finding(
            fp="fp-shared",
            disp=Disposition.DISMISSED,
            source="L1",
        )

        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: ([l0_f], []),
            l1_provider=lambda: ([l1_f], [], Usage(), 0.0),
        )
        machine.run()
        shared = [
            f for f in machine._state.findings
            if f.fingerprint == "fp-shared"
        ]
        assert len(shared) == 1
        # L0 CONFIRMED wins over L1 DISMISSED
        assert shared[0].source == "L0"


class TestFalsifierErrorCatch:
    """(d) StubFalsifier RuntimeError -> UNCERTAIN + error populated."""

    def test_runtime_error_caught(self, tmp_path):
        # Build StubFalsifier with error key
        import json
        config = {
            "default": "CONFIRMED",
            "errors": {"fp-err": "timeout"},
        }
        fixture = Path(tmp_path) / "falsifier.json"
        fixture.write_text(json.dumps(config))
        falsifier = StubFalsifier(fixture_path=fixture)

        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: ([], []),
            l1_provider=lambda: ([_make_finding(
                fp="fp-err", source="L1"
            )], [], Usage(), 0.0),
            falsifier=falsifier,
        )
        machine.run()

        found = [
            f for f in machine._state.findings
            if f.fingerprint == "fp-err"
        ]
        assert len(found) == 1
        assert found[0].disposition == Disposition.UNCERTAIN
        assert "falsify() raised:" in found[0].error
        # infra_errors also records
        assert any(
            "falsify exception on fp-err" in e
            for e in machine._state.infra_errors
        )


class TestToolErrorToInfraErrors:
    """(e) Phase 1 ToolError -> infra_errors, NOT in active findings."""

    def test_tool_error_populates_infra(self, tmp_path, monkeypatch):
        # Isolate from the mutmut-presence axis: this test is about
        # ToolError -> infra_errors mapping, not mutation detection.
        monkeypatch.setattr("shutil.which", lambda cmd: "/fake/mutmut")
        machine = _make_ci_machine(
            tmp_path,
            l0_runner=lambda r, f: (
                [],
                ["L0 ToolError tool=ruff msg=binary not found"],
            ),
        )
        machine.run()
        assert len(machine._state.findings) == 0
        assert any(
            "L0 ToolError" in e
            for e in machine._state.infra_errors
        )


class TestL0RunnerException:
    """L0 runner framework exception -> infra_errors, empty findings."""

    def test_exception_caught(self, tmp_path):
        def bad_l0(registry, files):
            raise OSError("disk full")

        machine = _make_ci_machine(tmp_path, l0_runner=bad_l0)
        machine.run()
        assert any(
            "L0 runner failed:" in e
            for e in machine._state.infra_errors
        )


class TestSaveStatePerRound:
    """SC-16: state.json reflects current round on disk after each round."""

    def test_state_persisted(self, tmp_path):
        import json

        def mock_l0(registry, files):
            return ([], [])

        machine = _make_ci_machine(tmp_path, l0_runner=mock_l0)
        machine.run()

        state_path = tmp_path / ".code-forge" / "state.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["round"] == 0
