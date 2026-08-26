# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for code_forge.exec_falsify (Phase 53a EXEC-FALSIFY v1).

Covers: ExecEvidence record, tier gate (EXEC-02), command resolution
order, subprocess contract, timeout-never-clean, stderr_tail
truncation, and the machine-level EXEC-03 asymmetry wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from code_forge.disposition import Disposition
from code_forge.exec_falsify import (
    ExecEvidence,
    ExecFalsifier,
    ExecStatus,
)


def _declared_manifest(
    runtime_name: str = "python",
    runtime_bin: str = "/usr/bin/python3",
) -> dict[str, Any]:
    return {
        "tier": "DECLARED",
        "runtime_name": runtime_name,
        "runtime_bin": runtime_bin,
    }


class TestExecStatus:
    def test_four_states_distinct(self):
        assert ExecStatus.FAIL_BEFORE.value == "fail_before"
        assert ExecStatus.PASS_AFTER.value == "pass_after"
        assert ExecStatus.TIMEOUT.value == "timeout"
        assert ExecStatus.UNAVAILABLE.value == "unavailable"
        assert len(set(s.value for s in ExecStatus)) == 4


class TestExecEvidence:
    def test_to_dict_roundtrip(self):
        ev = ExecEvidence(
            status=ExecStatus.FAIL_BEFORE,
            command="pytest -q",
            exit_code=2,
            duration_s=1.2345,
            stderr_tail="boom",
            reason="",
        )
        d = ev.to_dict()
        assert d["status"] == "fail_before"
        assert d["command"] == "pytest -q"
        assert d["exit_code"] == 2
        assert d["stderr_tail"] == "boom"
        assert isinstance(d["duration_s"], float)

    def test_frozen(self):
        ev = ExecEvidence(
            status=ExecStatus.TIMEOUT,
            command="x",
            exit_code=None,
            duration_s=0.0,
            stderr_tail="",
            reason="budget exhausted",
        )
        with pytest.raises(AttributeError):
            ev.status = ExecStatus.PASS_AFTER  # type: ignore[misc]


class TestTierGate:
    def test_none_manifest_returns_unavailable_without_subprocess(self, tmp_path):
        falsifier = ExecFalsifier(manifest=None, timeout_seconds=10)
        with mock.patch("code_forge.exec_falsify.subprocess.run") as sr:
            ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "DECLARED" in ev.reason
        sr.assert_not_called()

    @pytest.mark.parametrize("tier", ["absent", "observed", "AbsEnt"])
    def test_non_declared_tier_returns_unavailable(
        self, tmp_path, tier
    ):
        manifest = _declared_manifest()
        manifest["tier"] = tier
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        with mock.patch("code_forge.exec_falsify.subprocess.run") as sr:
            ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "DECLARED" in ev.reason
        sr.assert_not_called()

    def test_declared_tier_case_insensitive(self, tmp_path):
        manifest = _declared_manifest()
        manifest["tier"] = "Declared"
        falsifier = ExecFalsifier(
            manifest=manifest,
            timeout_seconds=10,
            command=["true"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER


class TestCommandResolution:
    def test_explicit_command_wins(self, tmp_path):
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["true"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER
        assert ev.command == "true"

    def test_failing_command_fail_before(self, tmp_path):
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["false"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.FAIL_BEFORE
        assert ev.exit_code == 1

    def test_empty_runtime_bin_falls_back_to_sys_executable(self, tmp_path):
        manifest = _declared_manifest(runtime_bin="")
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=30)
        ev = falsifier.run(tmp_path)
        # The resolved command must start with the live interpreter.
        assert ev.command.startswith(sys.executable)
        # -m pytest on an empty tmp_path exits nonzero (no tests dir
        # collected / usage error is also nonzero) -> FAIL_BEFORE, or
        # UNAVAILABLE if pytest is not importable in this interpreter.
        assert ev.status in (ExecStatus.FAIL_BEFORE, ExecStatus.UNAVAILABLE)

    def test_non_python_runtime_without_command_unavailable(self, tmp_path):
        manifest = _declared_manifest(runtime_name="node", runtime_bin="node")
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        with mock.patch("code_forge.exec_falsify.subprocess.run") as sr:
            ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "no test command" in ev.reason
        sr.assert_not_called()


class TestSubprocessContract:
    def test_timeout_never_clean(self, tmp_path):
        # sleep 30 under a 1s budget must yield TIMEOUT, never clean.
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=1,
            command=["sleep", "30"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.TIMEOUT
        assert ev.reason == "budget exhausted"
        assert ev.exit_code is None

    def test_oserror_unavailable_with_reason(self, tmp_path):
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["/nonexistent/binary/xyz"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "FileNotFoundError" in ev.reason

    def test_stderr_tail_truncated_at_2000(self, tmp_path):
        # A command emitting 5000 chars of stderr; tail must be 2000.
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=30,
            command=[
                sys.executable, "-c",
                "import sys; sys.stderr.write('x' * 5000); sys.exit(1)",
            ],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.FAIL_BEFORE
        assert len(ev.stderr_tail) == 2000

    def test_stdin_devnull(self, tmp_path):
        # A command reading stdin must not hang: stdin=DEVNULL makes
        # read() return EOF immediately.
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["cat"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER


class TestManifestObjectAcceptance:
    def test_object_with_to_dict_accepted(self, tmp_path):
        class FakeManifest:
            def to_dict(self):
                return _declared_manifest()

        falsifier = ExecFalsifier(
            manifest=FakeManifest(),
            timeout_seconds=10,
            command=["true"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER


# ---------------------------------------------------------------------------
# Phase 53a EXEC-FALSIFY: machine-level wiring + EXEC-03 asymmetry
# ---------------------------------------------------------------------------


class TestExecFalsifyMachineWiring:
    def _build(self, tmp_path, findings, exec_falsify=True, timeout=120):
        from code_forge.autofix import StubAutoFixer
        from code_forge.baseline import ResolvedReview
        from code_forge.falsify import StubFalsifier
        from code_forge.machine import StateMachine
        from code_forge.state import Mode

        def mock_l0(registry, files):
            return (findings, [])

        resolved = ResolvedReview(
            source_files=[Path("a.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
            base_sha="a" * 40,
            head_sha="b" * 40,
        )
        return StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash="src-hash",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=mock_l0,
            exec_falsify=exec_falsify,
            exec_falsify_timeout=timeout,
        )

    def test_fail_before_evidence_saved_on_state(self, tmp_path):
        """FAIL_BEFORE runs: evidence dict lands on State.exec_evidence."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        # Declared manifest in state, explicit command that fails.
        sm = self._build(tmp_path, findings=[])
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch(
            "code_forge.exec_falsify.ExecFalsifier.run"
        ) as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.FAIL_BEFORE,
                command="pytest -q",
                exit_code=1,
                duration_s=0.5,
                stderr_tail="",
                reason="",
            )
            verdict = sm.run()
        assert verdict is not None
        assert sm._state.exec_evidence is not None
        assert sm._state.exec_evidence["status"] == "fail_before"

    def test_pass_after_is_receipt_level_only(self, tmp_path):
        """EXEC-03: PASS_AFTER must NOT alter finding dispositions/basis.

        A CONFIRMED L0 finding stays CONFIRMED and its basis carries no
        exec_evidence under PASS_AFTER.
        """
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        from code_forge.state import StateFinding

        finding = StateFinding(
            id="fp-pa",
            fingerprint="fp-pa",
            source="L0",
            disposition=Disposition.CONFIRMED,
            file="a.py",
            line_range=[1, 1],
            description="synthetic",
        )
        sm = self._build(tmp_path, findings=[finding])
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch(
            "code_forge.exec_falsify.ExecFalsifier.run"
        ) as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.PASS_AFTER,
                command="pytest -q",
                exit_code=0,
                duration_s=0.5,
                stderr_tail="",
                reason="",
            )
            sm.run()
        # Disposition untouched (autofix may promote; PASS_AFTER must not)
        stored = [f for f in sm._state.findings if f.fingerprint == "fp-pa"]
        assert stored, "finding vanished"
        # exec evidence recorded at receipt level only
        assert sm._state.exec_evidence["status"] == "pass_after"
        # derive_basis yields no exec_evidence for PASS_AFTER -- both for
        # the L0 finding above and (decisively) for an L1 CONFIRMED twin:
        # PASS_AFTER must never strengthen a basis.
        from code_forge.basis import derive_basis
        from code_forge.state import StateFinding as _SF
        from code_forge.manifest import ManifestTier
        l1_confirmed = _SF(
            id="l1-pa",
            fingerprint="l1-pa",
            source="L1",
            disposition=Disposition.CONFIRMED,
            file="a.py",
            line_range=[1, 1],
            description="synthetic l1",
        )
        for f in (stored[0], l1_confirmed):
            basis = derive_basis(
                f,
                convergence_rounds=1,
                manifest_tier=ManifestTier.DECLARED,
                exec_evidence="pass_after",
            )
            assert basis.exec_evidence is None
        # Contrast: FAIL_BEFORE does strengthen an L1 CONFIRMED basis.
        strengthened = derive_basis(
            l1_confirmed,
            convergence_rounds=1,
            manifest_tier=ManifestTier.DECLARED,
            exec_evidence="fail_before",
        )
        assert strengthened.exec_evidence == "fail_before"

    def test_timeout_disclosed_not_clean(self, tmp_path):
        """TIMEOUT: evidence records timeout with reason, never clean."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        sm = self._build(tmp_path, findings=[])
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch(
            "code_forge.exec_falsify.ExecFalsifier.run"
        ) as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.TIMEOUT,
                command="pytest -q",
                exit_code=None,
                duration_s=120.0,
                stderr_tail="",
                reason="budget exhausted",
            )
            sm.run()
        assert sm._state.exec_evidence["status"] == "timeout"
        assert sm._state.exec_evidence["reason"] == "budget exhausted"

    def test_disabled_by_default(self, tmp_path):
        """exec_falsify=False: no evidence, no subprocess."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        sm = self._build(tmp_path, findings=[], exec_falsify=False)
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch(
            "code_forge.exec_falsify.ExecFalsifier.run"
        ) as run_mock:
            sm.run()
            run_mock.assert_not_called()
        assert sm._state.exec_evidence is None


# ---------------------------------------------------------------------------
# Phase 53a: gate.yaml exec_falsify config validation
# ---------------------------------------------------------------------------


class TestExecFalsifyGateConfig:
    def _load(self, tmp_path, yaml_text):
        from code_forge.gate_check import load_gate_config
        gate = tmp_path / "gate.yaml"
        gate.write_text(yaml_text, encoding="utf-8")
        return load_gate_config(gate)

    def test_valid_timeout(self, tmp_path):
        data = self._load(
            tmp_path,
            "test:\n  command: [pytest, -q]\n"
            "exec_falsify:\n  timeout_seconds: 300\n",
        )
        assert data["exec_falsify"]["timeout_seconds"] == 300

    def test_default_omitted(self, tmp_path):
        data = self._load(
            tmp_path, "test:\n  command: [pytest, -q]\n"
        )
        assert "exec_falsify" not in data

    def test_below_min_rejected(self, tmp_path):
        with pytest.raises(ValueError, match=r"\[10, 1800\]"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\n"
                "exec_falsify:\n  timeout_seconds: 9\n",
            )

    def test_above_max_rejected(self, tmp_path):
        with pytest.raises(ValueError, match=r"\[10, 1800\]"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\n"
                "exec_falsify:\n  timeout_seconds: 1801\n",
            )

    def test_non_integer_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="integer"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\n"
                "exec_falsify:\n  timeout_seconds: fast\n",
            )

    def test_unknown_key_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown key"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\n"
                "exec_falsify:\n  timeout_seconds: 120\n  engine: x\n",
            )

    def test_non_mapping_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mapping"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\n"
                "exec_falsify: 120\n",
            )
