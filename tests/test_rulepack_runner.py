# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Tests for RulepackRunner: semgrep execution, matrix statuses, advisory mapping.

Executes the real control flow with subprocess/which monkeypatched so no
semgrep binary is required. The matrix statuses (VIOLATION / CLEAN /
NOT_APPLICABLE / NOT_RUN) and AdvisoryFinding conversion are the contract
under test.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.rulepack import RulepackRunner

# ghost-rule is declared in meta but deliberately absent from rules.yaml so
# the runner must account it as NOT_RUN (AC-2 missing-rule detection).
META = """\
name: testpack
version: "1.0"
source: https://example.com/testpack
rules:
  - id: rule-a
    title: Rule A
    category: security
    impact_tier: high
    languages: [python]
    source: x
  - id: rule-b
    title: Rule B
    category: performance
    impact_tier: medium
    languages: [python]
    source: x
  - id: ghost-rule
    title: Ghost Rule
    category: security
    impact_tier: high
    languages: [python]
    source: x
"""

RULES = """\
rules:
  - id: rule-a
    pattern: foo(...)
    languages: [python]
    message: avoid foo
    severity: ERROR
  - id: rule-b
    pattern: bar(...)
    languages: [python]
    message: avoid bar
    severity: WARNING
"""

GATE = {"rulepacks": ["testpack"]}


def _install_repo_local_pack(tmp_path):
    """Write the test pack under tmp_path/.code-forge/packs/ and return root."""
    pack_dir = tmp_path / ".code-forge" / "packs" / "testpack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "meta.yaml").write_text(META, encoding="utf-8")
    (pack_dir / "rules.yaml").write_text(RULES, encoding="utf-8")
    (tmp_path / "app.py").write_text("foo(1)\nbar(2)\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("docs\n", encoding="utf-8")


def _sarif(rule_id="rule-a", uri="app.py", line=1, message="violation"):
    """Build a minimal semgrep --sarif document with one result."""
    return json.dumps(
        {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "semgrep", "rules": []}},
                    "results": [
                        {
                            "ruleId": rule_id,
                            "level": "warning",
                            "message": {"text": message},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": uri},
                                        "region": {
                                            "startLine": line,
                                            "startColumn": 1,
                                            "endLine": line,
                                            "endColumn": 5,
                                        },
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


class _FakeProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class TestRulepackRunner:
    def test_is_advisory(self):
        assert RulepackRunner().is_advisory is True

    def test_missing_semgrep_marks_all_not_run(self, tmp_path, monkeypatch):
        """semgrep absent -> every rule NOT_RUN + infra_errors populated."""
        _install_repo_local_pack(tmp_path)
        monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: None)
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda p: dict(GATE)
        )

        runner = RulepackRunner()
        runner.source_files = [Path("app.py")]
        advisories = runner.run("diff --git a/app.py b/app.py\n", tmp_path)

        assert advisories == []
        assert any("requires semgrep" in e for e in runner.infra_errors)
        statuses = {
            r["id"]: r["status"]
            for pack in runner.matrix.packs
            for r in pack["rules"]
        }
        assert set(statuses) == {"rule-a", "rule-b", "ghost-rule"}
        assert all(v == "NOT_RUN" for v in statuses.values())

    def test_semgrep_violation_clean_not_run(self, tmp_path, monkeypatch):
        """rule-a violation, rule-b clean, ghost-rule (missing) NOT_RUN."""
        _install_repo_local_pack(tmp_path)
        monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: "/bin/semgrep")
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda p: dict(GATE)
        )
        monkeypatch.setattr(
            "code_forge.rulepack.subprocess.run",
            lambda *a, **k: _FakeProcess(stdout=_sarif(rule_id="rule-a", line=1)),
        )

        runner = RulepackRunner()
        runner.source_files = [Path("app.py"), Path("readme.md")]
        advisories = runner.run("diff --git a/app.py b/app.py\n", tmp_path)

        statuses = {
            r["id"]: r["status"]
            for pack in runner.matrix.packs
            for r in pack["rules"]
        }
        assert statuses["rule-a"] == "VIOLATION"
        assert statuses["rule-b"] == "CLEAN"
        # AC-2: missing rule is accounted, not silently dropped.
        assert statuses["ghost-rule"] == "NOT_RUN"

        assert len(advisories) == 1
        adv = advisories[0]
        assert isinstance(adv, AdvisoryFinding)
        assert adv.id == "rulepack:testpack:app.py:1:rule-a"
        assert adv.axis == "rulepack:testpack"
        assert adv.attribution == "semgrep-ce/rulepack"

    def test_no_matching_language_files_not_applicable(self, tmp_path, monkeypatch):
        """Only readme.md present -> python rules are NOT_APPLICABLE."""
        _install_repo_local_pack(tmp_path)
        monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: "/bin/semgrep")
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda p: dict(GATE)
        )
        # subprocess.run should NOT be reached (no eligible files).
        monkeypatch.setattr(
            "code_forge.rulepack.subprocess.run",
            lambda *a, **k: pytest.fail("semgrep should not run"),
        )

        runner = RulepackRunner()
        runner.source_files = [Path("readme.md")]
        advisories = runner.run("diff --git a/readme.md b/readme.md\n", tmp_path)

        statuses = {
            r["id"]: r["status"]
            for pack in runner.matrix.packs
            for r in pack["rules"]
        }
        assert statuses["rule-a"] == "NOT_APPLICABLE"
        assert statuses["rule-b"] == "NOT_APPLICABLE"
        assert statuses["ghost-rule"] == "NOT_RUN"
        assert advisories == []

    def test_semgrep_timeout_marks_not_run(self, tmp_path, monkeypatch):
        """subprocess timeout -> non-missing rules become NOT_RUN."""
        _install_repo_local_pack(tmp_path)
        monkeypatch.setattr("code_forge.rulepack.shutil.which", lambda _: "/bin/semgrep")
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda p: dict(GATE)
        )

        def _timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="semgrep", timeout=120)

        monkeypatch.setattr("code_forge.rulepack.subprocess.run", _timeout)

        runner = RulepackRunner()
        runner.source_files = [Path("app.py")]
        advisories = runner.run("diff --git a/app.py b/app.py\n", tmp_path)

        assert advisories == []
        assert any("timed out" in e for e in runner.infra_errors)
        statuses = {
            r["id"]: r["status"]
            for pack in runner.matrix.packs
            for r in pack["rules"]
        }
        assert statuses["rule-a"] == "NOT_RUN"
        assert statuses["ghost-rule"] == "NOT_RUN"

    def test_no_configured_packs_returns_empty(self, tmp_path, monkeypatch):
        """gate.yaml without rulepacks -> no packs, no matrix rules."""
        monkeypatch.setattr(
            "code_forge.gate_check.load_gate_config", lambda p: {"rulepacks": []}
        )
        runner = RulepackRunner()
        runner.source_files = [Path("app.py")]
        advisories = runner.run("diff --git a/app.py b/app.py\n", tmp_path)

        assert advisories == []
        assert runner.packs == []
        assert runner.matrix.packs == []