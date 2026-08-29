# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <[EMAIL_REDACTED]>
"""Tests for the pre-convergence rulepack blocking bridge (Task 4 / P1-2).

The bridge (machine._run_rulepack_blocking_phase) promotes advisory
RulepackRunner findings whose rule IDs are listed in gate.yaml
`rulepacks_blocking` into genuine CONFIRMED StateFindings with
source=RULEPACK, so they block the verdict and reset the cycle counter.
Non-promoted rules stay advisory-only.

The semgrep execution path is covered by test_rulepack_runner.py; here
RulepackRunner.run is stubbed so the bridge logic is the unit under test.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode

GATE = {
    "rulepacks": ["testpack"],
    "rulepacks_blocking": ["rule-a"],
}


class _FakeRunner:
    """Stand-in for RulepackRunner producing canned advisory findings."""

    def __init__(self, advisories):
        self._advisories = advisories
        self.infra_errors: list[str] = []
        self.source_files = None

    def run(self, diff_text, repo_root):
        return list(self._advisories)


def _advisory(rule_id, file="app.py", line=3, pack="testpack"):
    return AdvisoryFinding(
        id="rulepack:%s:%s:%d:%s" % (pack, file, line, rule_id),
        axis="rulepack:%s" % pack,
        file=file,
        line_range=(line, line),
        description="violation of %s" % rule_id,
        attribution="semgrep-ce/rulepack",
    )


def _make_sm(tmp_path, *, advisories, blocking_ids):
    """Build a StateMachine with the rulepack blocking bridge stubbed."""
    resolved = ResolvedReview(
        source_files=[Path("app.py")],
        baseline_content=None,
        git_diff="diff --git a/app.py b/app.py\n",
        mode_hint="git",
    )

    def _fake_gate_config(_path):
        return {"rulepacks": ["testpack"], "rulepacks_blocking": blocking_ids}

    fake_runner = _FakeRunner(advisories)

    with patch("code_forge.gate_check.load_gate_config", _fake_gate_config), \
         patch("code_forge.rulepack.RulepackRunner", lambda: fake_runner):
        sm = StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            resolved_review=resolved,
            source_hash="abc",
            baseline_spec_repr="empty",
            cwd=tmp_path,
            registry={},
            l0_runner=lambda reg, files: ([], []),
        )
        findings = sm._run_rulepack_blocking_phase()
    return findings, fake_runner


class TestBlockingBridge:
    def test_promoted_rule_becomes_blocking_state_finding(self, tmp_path):
        advisories = [_advisory("rule-a", line=7)]
        findings, _ = _make_sm(tmp_path, advisories=advisories,
                               blocking_ids=["rule-a"])
        assert len(findings) == 1
        f = findings[0]
        assert f.source == "RULEPACK"
        assert f.disposition == Disposition.CONFIRMED
        assert f.file == "app.py"
        assert f.line_range == [7, 7]

    def test_promoted_fingerprint_is_deterministic_and_hashed_desc(self, tmp_path):
        advisories = [_advisory("rule-a", line=7)]
        f1, _ = _make_sm(tmp_path, advisories=advisories,
                         blocking_ids=["rule-a"])
        f2, _ = _make_sm(tmp_path, advisories=advisories,
                         blocking_ids=["rule-a"])
        assert f1[0].fingerprint == f2[0].fingerprint
        assert f1[0].fingerprint.startswith("rulepack:testpack:rule-a:app.py:7:")
        # Trailing segment must be a 12-char sha256 digest of the description.
        digest = f1[0].fingerprint.rsplit(":", 1)[1]
        assert len(digest) == 12

    def test_non_promoted_rule_stays_advisory_only(self, tmp_path):
        advisories = [_advisory("rule-a"), _advisory("rule-b")]
        findings, _ = _make_sm(tmp_path, advisories=advisories,
                               blocking_ids=["rule-a"])
        # Only rule-a is promoted; rule-b is not converted.
        assert len(findings) == 1
        assert findings[0].fingerprint.split(":")[2] == "rule-a"

    def test_no_blocking_ids_returns_empty(self, tmp_path):
        advisories = [_advisory("rule-a")]
        findings, _ = _make_sm(tmp_path, advisories=advisories, blocking_ids=[])
        assert findings == []

    def test_unrelated_rule_id_not_promoted(self, tmp_path):
        # blocking list names a rule that produced no advisory -> no finding.
        advisories = [_advisory("rule-a")]
        findings, _ = _make_sm(tmp_path, advisories=advisories,
                               blocking_ids=["rule-b"])
        assert findings == []

    def test_malformed_advisory_id_skipped(self, tmp_path):
        bad = AdvisoryFinding(
            id="no-colons-here",
            axis="rulepack:testpack",
            file="app.py",
            line_range=(1, 1),
            description="x",
            attribution="semgrep-ce/rulepack",
        )
        findings, _ = _make_sm(tmp_path, advisories=[bad],
                               blocking_ids=["rule-a"])
        assert findings == []
