# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <minxi.hou@gmail.com>
"""Unit tests for code_forge.exec_falsify (Phase 53a EXEC-FALSIFY v1).

Covers:
- ExecEvidence record and schema (lossless argv, stdout/stderr tails, env fingerprint)
- Environment identity & declared reconstruction (lockfile sha256, tool version, no host fallback)
- Subprocess isolation & descendant process-group cleanup on timeout
- Bounded stdout/stderr streaming without pipe deadlocks
- Read-only reviewed tree isolation via disposable execution tree
- Environment secret stripping (minimal allowlist)
- Gate command plumbing and safety validation
- EXEC-03 asymmetric verdict wiring, receipt persistence, SARIF threading
- Known-answer runtime-only defect detection
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
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
from code_forge.state import Mode, StateFinding, Verdict


def _declared_manifest(
    runtime_name: str = "python",
    runtime_bin: str = "/usr/bin/python3",
    manifest_path: str = "requirements.txt",
    manifest_format: str = "requirements",
) -> dict[str, Any]:
    return {
        "tier": "DECLARED",
        "runtime_name": runtime_name,
        "runtime_bin": runtime_bin,
        "manifest_path": manifest_path,
        "manifest_format": manifest_format,
    }


class TestExecStatus:
    def test_four_states_distinct(self):
        assert ExecStatus.FAIL_BEFORE.value == "fail_before"
        assert ExecStatus.PASS_AFTER.value == "pass_after"
        assert ExecStatus.TIMEOUT.value == "timeout"
        assert ExecStatus.UNAVAILABLE.value == "unavailable"
        assert len(set(s.value for s in ExecStatus)) == 4


class TestExecEvidenceSchema:
    def test_to_dict_roundtrip(self):
        ev = ExecEvidence(
            status=ExecStatus.FAIL_BEFORE,
            command=["pytest", "-q"],
            exit_code=2,
            duration_s=1.2345,
            stdout_tail="stdout failure info",
            stderr_tail="stderr failure info",
            reason="",
            environment={
                "raw_version": "Python 3.14.0",
                "lockfile_hash": "sha256:abc123",
                "manifest_path": "poetry.lock",
            },
        )
        d = ev.to_dict()
        assert d["status"] == "fail_before"
        assert d["command"] == "pytest -q"
        assert d["command_argv"] == ["pytest", "-q"]
        assert d["exit_code"] == 2
        assert d["stdout_tail"] == "stdout failure info"
        assert d["stderr_tail"] == "stderr failure info"
        assert d["environment"]["raw_version"] == "Python 3.14.0"
        assert d["environment"]["lockfile_hash"] == "sha256:abc123"
        assert isinstance(d["duration_s"], float)

    def test_frozen_immutability(self):
        ev = ExecEvidence(
            status=ExecStatus.TIMEOUT,
            command=["x"],
            exit_code=None,
            duration_s=0.0,
            stdout_tail="",
            stderr_tail="",
            reason="budget exhausted",
        )
        with pytest.raises(AttributeError):
            ev.status = ExecStatus.PASS_AFTER  # type: ignore[misc]


class TestTierGate:
    def test_none_manifest_returns_unavailable_without_subprocess(self, tmp_path):
        falsifier = ExecFalsifier(manifest=None, timeout_seconds=10)
        with mock.patch("subprocess.Popen") as sp:
            ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "DECLARED" in ev.reason
        sp.assert_not_called()

    @pytest.mark.parametrize("tier", ["absent", "observed", "AbsEnt"])
    def test_non_declared_tier_returns_unavailable(self, tmp_path, tier):
        manifest = _declared_manifest()
        manifest["tier"] = tier
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        with mock.patch("subprocess.Popen") as sp:
            ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "DECLARED" in ev.reason
        sp.assert_not_called()

    def test_declared_tier_case_insensitive(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest==8.0.0\n", encoding="utf-8")
        manifest = _declared_manifest()
        manifest["tier"] = "Declared"
        falsifier = ExecFalsifier(
            manifest=manifest,
            timeout_seconds=10,
            command=["true"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER


class TestEnvironmentIdentityAndReconstruction:
    def test_lockfile_hash_and_identity_recorded(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lock_content = b"pytest==8.0.0\n"
        lockfile.write_bytes(lock_content)
        expected_hash = "sha256:" + hashlib.sha256(lock_content).hexdigest()

        manifest = _declared_manifest(manifest_path="requirements.txt")
        falsifier = ExecFalsifier(
            manifest=manifest,
            timeout_seconds=10,
            command=["true"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER
        assert ev.environment.get("lockfile_hash") == expected_hash
        assert ev.environment.get("manifest_path") == "requirements.txt"

    def test_missing_lockfile_on_disk_returns_unavailable(self, tmp_path):
        manifest = _declared_manifest(manifest_path="nonexistent_lock.txt")
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10, command=["true"])
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "lockfile not found" in ev.reason.lower()

    def test_empty_runtime_bin_without_proven_venv_returns_unavailable(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest==8.0.0\n", encoding="utf-8")
        manifest = _declared_manifest(runtime_bin="", manifest_path="requirements.txt")
        # No .venv or venv directory in tmp_path, and no explicit command
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "virtualenv" in ev.reason.lower() or "not found" in ev.reason.lower()

    def test_proven_local_venv_used_when_present(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest==8.0.0\n", encoding="utf-8")
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        py_mock = venv_bin / "python"
        py_mock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        py_mock.chmod(0o755)

        manifest = _declared_manifest(runtime_bin="", manifest_path="requirements.txt")
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER
        assert str(py_mock) in ev.command_argv[0] or ".venv/bin/python" in ev.command_argv[0]

    def test_non_python_runtime_without_command_unavailable(self, tmp_path):
        lockfile = tmp_path / "package-lock.json"
        lockfile.write_text("{}", encoding="utf-8")
        manifest = _declared_manifest(
            runtime_name="node",
            runtime_bin="node",
            manifest_path="package-lock.json",
            manifest_format="npm",
        )
        falsifier = ExecFalsifier(manifest=manifest, timeout_seconds=10)
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "no test command" in ev.reason


class TestSubprocessIsolationAndCleanup:
    def test_timeout_kills_process_group_descendants(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")

        # Spawn a python script that launches a child sleep process
        pid_file = tmp_path / "child.pid"
        script = (
            "import subprocess, time, sys, pathlib\n"
            "p = subprocess.Popen(['sleep', '60'])\n"
            f"pathlib.Path(r'{pid_file}').write_text(str(p.pid))\n"
            "time.sleep(30)\n"
        )
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=1,
            command=[sys.executable, "-c", script],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.TIMEOUT
        assert ev.reason == "budget exhausted"

        # Check that grandchild PID was terminated
        time.sleep(0.2)
        if pid_file.exists():
            child_pid = int(pid_file.read_text().strip())
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_oserror_unavailable_with_reason(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["/nonexistent/binary/xyz"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.UNAVAILABLE
        assert "FileNotFoundError" in ev.reason

    def test_stdin_devnull(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=["cat"],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER


class TestBoundedOutputStreaming:
    def test_stdout_and_stderr_tails_captured(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")
        script = (
            "import sys\n"
            "sys.stdout.write('O' * 4000 + 'STDOUT_END\\n')\n"
            "sys.stderr.write('E' * 4000 + 'STDERR_END\\n')\n"
            "sys.exit(1)\n"
        )
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=30,
            command=[sys.executable, "-c", script],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.FAIL_BEFORE
        assert len(ev.stdout_tail) == 2000
        assert ev.stdout_tail.endswith("STDOUT_END\n")
        assert len(ev.stderr_tail) == 2000
        assert ev.stderr_tail.endswith("STDERR_END\n")

    def test_massive_output_no_pipe_deadlock(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")
        # Generate 2MB of output across both streams
        script = (
            "import sys\n"
            "for _ in range(5000):\n"
            "    sys.stdout.write('A' * 200 + '\\n')\n"
            "    sys.stderr.write('B' * 200 + '\\n')\n"
            "sys.exit(0)\n"
        )
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=30,
            command=[sys.executable, "-c", script],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER
        assert len(ev.stdout_tail) == 2000
        assert len(ev.stderr_tail) == 2000


class TestReviewedTreeIsolation:
    def test_reviewed_tree_remains_unmodified(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")
        source_file = tmp_path / "sample.py"
        source_file.write_text("x = 1\n", encoding="utf-8")

        # Command writes side effect file and cache
        script = (
            "import pathlib\n"
            "pathlib.Path('.pytest_cache').mkdir(exist_ok=True)\n"
            "pathlib.Path('__pycache__').mkdir(exist_ok=True)\n"
            "pathlib.Path('side_effect.txt').write_text('dirty')\n"
        )
        falsifier = ExecFalsifier(
            manifest=_declared_manifest(),
            timeout_seconds=10,
            command=[sys.executable, "-c", script],
        )
        ev = falsifier.run(tmp_path)
        assert ev.status == ExecStatus.PASS_AFTER

        # Verify reviewed tree has NO side effects or cache
        assert not (tmp_path / ".pytest_cache").exists()
        assert not (tmp_path / "__pycache__").exists()
        assert not (tmp_path / "side_effect.txt").exists()
        assert source_file.read_text(encoding="utf-8") == "x = 1\n"


class TestEnvironmentSecretStripping:
    def test_subprocess_env_strips_agent_credentials(self, tmp_path):
        lockfile = tmp_path / "requirements.txt"
        lockfile.write_text("pytest\n", encoding="utf-8")

        out_file = tmp_path / "env.json"
        script = (
            "import os, json, pathlib\n"
            f"pathlib.Path(r'{out_file}').write_text(json.dumps(dict(os.environ)))\n"
        )
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-secret-ant",
                "OPENAI_API_KEY": "sk-secret-open",
                "MIMO_PRO_API_KEY": "sk-secret-mimo",
                "HERMES_SESSION_TOKEN": "token-hermes",
                "CUSTOM_SECRET_KEY": "sec-123",
                "SAFE_TOOLCHAIN_VAR": "keepme",
            },
        ):
            falsifier = ExecFalsifier(
                manifest=_declared_manifest(),
                timeout_seconds=10,
                command=[sys.executable, "-c", script],
            )
            ev = falsifier.run(tmp_path)
            assert ev.status == ExecStatus.PASS_AFTER

        dumped = json.loads(out_file.read_text(encoding="utf-8"))
        assert "ANTHROPIC_API_KEY" not in dumped
        assert "OPENAI_API_KEY" not in dumped
        assert "MIMO_PRO_API_KEY" not in dumped
        assert "HERMES_SESSION_TOKEN" not in dumped
        assert "CUSTOM_SECRET_KEY" not in dumped
        assert "PATH" in dumped


class TestExecFalsifyMachineWiring:
    def _build(
        self,
        tmp_path,
        findings,
        exec_falsify=True,
        timeout=120,
        command=None,
        mode=Mode.CI,
    ):
        from code_forge.autofix import StubAutoFixer
        from code_forge.baseline import ResolvedReview
        from code_forge.falsify import StubFalsifier
        from code_forge.machine import StateMachine

        def mock_l0(registry, files):
            return (findings, [])

        resolved = ResolvedReview(
            source_files=[Path("a.py")],
            baseline_content=None,
            git_diff="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
            mode_hint="git",
            base_sha="a" * 40,
            head_sha="b" * 40,
        )
        return StateMachine(
            mode=mode,
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
            exec_falsify_command=command,
        )

    def test_fail_before_blocks_ci_verdict_even_with_zero_static_findings(self, tmp_path):
        """V-01: Runtime-only failure blocks PASS on clean CI run."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        sm = self._build(tmp_path, findings=[], exec_falsify=True, mode=Mode.CI)
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch("code_forge.exec_falsify.ExecFalsifier.run") as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.FAIL_BEFORE,
                command=["pytest", "-q"],
                exit_code=1,
                duration_s=0.5,
                stdout_tail="assertion error",
                stderr_tail="",
                reason="",
            )
            verdict = sm.run()

        # CI verdict must be FAIL when execution falsifier fails on reviewed diff
        assert verdict == Verdict.FAIL
        assert sm._state.exec_evidence is not None
        assert sm._state.exec_evidence["status"] == "fail_before"
        # Deterministic EXEC finding created
        exec_findings = [f for f in sm._state.findings if f.source == "EXEC"]
        assert len(exec_findings) == 1
        assert exec_findings[0].disposition == Disposition.CONFIRMED

    def test_pass_after_is_receipt_level_only(self, tmp_path):
        """EXEC-03: PASS_AFTER must NOT alter finding dispositions/basis."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        finding = StateFinding(
            id="fp-pa",
            fingerprint="fp-pa",
            source="L0",
            disposition=Disposition.CONFIRMED,
            file="a.py",
            line_range=[1, 1],
            description="synthetic",
        )
        sm = self._build(tmp_path, findings=[finding], exec_falsify=True, mode=Mode.LOCAL)
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch("code_forge.exec_falsify.ExecFalsifier.run") as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.PASS_AFTER,
                command=["pytest", "-q"],
                exit_code=0,
                duration_s=0.5,
                stdout_tail="",
                stderr_tail="",
                reason="",
            )
            sm.run()

        stored = [f for f in sm._state.findings if f.fingerprint == "fp-pa"]
        assert stored, "finding vanished"
        assert sm._state.exec_evidence["status"] == "pass_after"
        # PASS_AFTER produces no exec findings
        assert not any(f.source == "EXEC" for f in sm._state.findings)

    def test_receipt_persists_exec_evidence_and_basis(self, tmp_path):
        """V-07: Receipts carry top-level exec_evidence and basis threading."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)
        (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
        l1_finding = StateFinding(
            id="l1-qodo-review-fp1",
            fingerprint="fp1",
            source="L1",
            disposition=Disposition.CONFIRMED,
            file="a.py",
            line_range=[1, 1],
            description="l1 finding",
        )
        sm = self._build(tmp_path, findings=[], exec_falsify=True, mode=Mode.CI)
        sm._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }

        with mock.patch.object(sm, "_run_l1_phase", return_value=([l1_finding], [])):
            with mock.patch("code_forge.exec_falsify.ExecFalsifier.run") as run_mock:
                run_mock.return_value = ExecEvidence(
                    status=ExecStatus.FAIL_BEFORE,
                    command=["pytest", "-q"],
                    exit_code=1,
                    duration_s=0.5,
                    stdout_tail="stdout failure",
                    stderr_tail="stderr failure",
                    reason="",
                )
                sm.run()

        receipt_file = tmp_path / ".code-forge" / "receipts" / "receipt-c1p1.json"
        assert receipt_file.exists()
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        assert "exec_evidence" in receipt
        assert receipt["exec_evidence"]["status"] == "fail_before"
        # Basis on confirmed L1 finding has exec_evidence == "fail_before"
        assert receipt["findings"][0]["basis"]["exec_evidence"] == "fail_before"

    def test_known_answer_runtime_only_defect(self, tmp_path):
        """Plan acceptance: runtime-only defect is silent when disabled, blocks when enabled."""
        (tmp_path / ".code-forge").mkdir(parents=True, exist_ok=True)

        # 1. Disabled organ: statically clean run returns PASS
        sm_disabled = self._build(tmp_path, findings=[], exec_falsify=False, mode=Mode.CI)
        sm_disabled._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        verdict_disabled = sm_disabled.run()
        assert verdict_disabled == Verdict.PASS

        # 2. Enabled organ: runtime defect caught -> FAIL
        sm_enabled = self._build(tmp_path, findings=[], exec_falsify=True, mode=Mode.CI)
        sm_enabled._state.env_manifest = {
            "tier": "DECLARED",
            "runtime_name": "python",
            "runtime_bin": sys.executable,
        }
        with mock.patch("code_forge.exec_falsify.ExecFalsifier.run") as run_mock:
            run_mock.return_value = ExecEvidence(
                status=ExecStatus.FAIL_BEFORE,
                command=["pytest", "tests/test_runtime_behavior.py"],
                exit_code=1,
                duration_s=0.4,
                stdout_tail="FAILED test_runtime_behavior.py::test_case",
                stderr_tail="",
                reason="",
            )
            verdict_enabled = sm_enabled.run()
        assert verdict_enabled == Verdict.FAIL


class TestExecFalsifyGateConfig:
    def _load(self, tmp_path, yaml_text):
        from code_forge.gate_check import load_gate_config

        gate = tmp_path / "gate.yaml"
        gate.write_text(yaml_text, encoding="utf-8")
        return load_gate_config(gate)

    def test_valid_timeout(self, tmp_path):
        data = self._load(
            tmp_path,
            "test:\n  command: [pytest, -q]\nexec_falsify:\n  timeout_seconds: 300\n",
        )
        assert data["exec_falsify"]["timeout_seconds"] == 300

    def test_default_omitted(self, tmp_path):
        data = self._load(tmp_path, "test:\n  command: [pytest, -q]\n")
        assert "exec_falsify" not in data

    def test_below_min_rejected(self, tmp_path):
        with pytest.raises(ValueError, match=r"\[10, 1800\]"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\nexec_falsify:\n  timeout_seconds: 9\n",
            )

    def test_above_max_rejected(self, tmp_path):
        with pytest.raises(ValueError, match=r"\[10, 1800\]"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\nexec_falsify:\n  timeout_seconds: 1801\n",
            )

    def test_non_integer_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="integer"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\nexec_falsify:\n  timeout_seconds: fast\n",
            )

    def test_unknown_key_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown key"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\nexec_falsify:\n  timeout_seconds: 120\n  engine: x\n",
            )

    def test_non_mapping_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mapping"):
            self._load(
                tmp_path,
                "test:\n  command: [pytest, -q]\nexec_falsify: 120\n",
            )
