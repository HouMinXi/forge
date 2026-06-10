# SPDX-License-Identifier: Apache-2.0
"""Integration tests for Phase 18 pipeline wiring.

Covers:
- danger_score_from_diff wired into _run_l0_phase (D-02/D-15)
- Non-git mode loud-skip for danger-score (D-16)
- TaintRunner wired as advisory runner (D-09)
- source_files injection from resolved_review (D-09)
- Provenance question in pass3-adversarial.md (D-07/D-08)
- gate-yaml-rce corpus entry has TRUST tag (SC#5)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict
from code_forge.taint import TaintRunner


def _make_resolved(
    *,
    git_diff="diff --git a/test.py b/test.py\n",
    source_files=None,
):
    return ResolvedReview(
        source_files=source_files or [Path("test.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint="git" if git_diff is not None else "non-git",
    )


def _make_sm(tmp_path, *, advisory_runners=None, git_diff=None,
             source_files=None, resolved=None):
    """Build a minimal StateMachine for integration tests."""
    if resolved is None:
        kw = {}
        if git_diff is not None:
            kw["git_diff"] = git_diff
        if source_files is not None:
            kw["source_files"] = source_files
        resolved = _make_resolved(**kw)
    return StateMachine(
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
        advisory_runners=advisory_runners or [],
    )


# A diff that contains a gate.yaml change with a dangerous field.
_GATE_YAML_DIFF = """\
diff --git a/gate.yaml b/gate.yaml
--- a/gate.yaml
+++ b/gate.yaml
@@ -1,3 +1,4 @@
 review:
   passes: 3
+  base_url: http://evil.example.com
"""


class TestDangerScoreWiredToL0:
    """danger_score_from_diff runs inside _run_l0_phase."""

    def test_danger_score_finds_gate_yaml_field(self, tmp_path):
        sm = _make_sm(tmp_path, git_diff=_GATE_YAML_DIFF)
        findings = sm._run_l0_phase()
        danger = [
            f for f in findings
            if f.fingerprint.startswith("danger-score:")
        ]
        assert len(danger) >= 1
        assert danger[0].source == "L0"
        from code_forge.disposition import Disposition
        assert danger[0].disposition == Disposition.CONFIRMED

    def test_danger_score_in_full_round(self, tmp_path):
        """danger-score findings appear in state after _execute_round."""
        sm = _make_sm(tmp_path, git_diff=_GATE_YAML_DIFF)
        sm._execute_round(0)
        danger = [
            f for f in sm._state.findings
            if f.fingerprint.startswith("danger-score:")
        ]
        assert len(danger) >= 1


class TestDangerScoreNonGit:
    """Non-git mode: danger-score loud-skips (D-16)."""

    def test_nongit_loud_skip(self, tmp_path):
        resolved = _make_resolved(git_diff=None)
        sm = _make_sm(tmp_path, resolved=resolved)
        sm._run_l0_phase()
        matching = [
            e for e in sm._state.infra_errors
            if "Danger-score requires a diff" in e
        ]
        assert len(matching) == 1
        assert "skipping in non-git mode" in matching[0]


class TestTaintRunnerWiredAsAdvisory:
    """TaintRunner registered as advisory runner dispatches correctly."""

    def test_taint_runner_semgrep_absent_infra_error(self, tmp_path):
        """When semgrep is absent, infra_errors contains D-06 message."""
        runner = TaintRunner()
        sm = _make_sm(
            tmp_path,
            advisory_runners=[runner],
            source_files=[Path("dummy.py")],
        )
        with patch("shutil.which", return_value=None):
            sm._run_advisory_axes()
        matching = [
            e for e in sm._state.infra_errors
            if "Taint gate requires semgrep" in e
        ]
        assert len(matching) >= 1


class TestTaintRunnerSourceFilesInjection:
    """machine.py injects source_files from resolved_review (D-09)."""

    def test_source_files_injected_before_run(self, tmp_path):
        runner = TaintRunner()
        src = [Path("a.py")]
        sm = _make_sm(
            tmp_path,
            advisory_runners=[runner],
            source_files=src,
        )
        with patch("shutil.which", return_value=None):
            sm._run_advisory_axes()
        # After _run_advisory_axes, runner.source_files should be set
        # from resolved_review.source_files.
        assert runner.source_files is not None
        assert Path("a.py") in runner.source_files


class TestProvenanceQuestion:
    """Provenance question present in pass3-adversarial.md (D-07/D-08).

    Uses git toplevel to locate the file, since editable installs may
    resolve code_forge.__file__ to a different tree than the worktree
    under test.
    """

    def test_provenance_question_in_adversarial_pass(self):
        import subprocess as _sp

        # Use test file's own directory to anchor git toplevel,
        # since pytest may change cwd during earlier tests.
        test_dir = str(Path(__file__).resolve().parent)
        toplevel = _sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            cwd=test_dir,
        ).stdout.strip()
        adversarial_path = (
            Path(toplevel)
            / "src" / "code_forge" / "skills" / "code-forge"
            / "passes" / "pass3-adversarial.md"
        )
        content = adversarial_path.read_text()
        assert "### External input provenance" in content
        # Text wraps across lines in the markdown file; check key
        # phrase that fits on a single line.
        assert "who controls the source of" in content
        assert "worst value a malicious caller could inject" in content


class TestCorpusRegressionGuard:
    """gate-yaml-rce corpus entry has TRUST tag (SC#5)."""

    def test_gate_yaml_rce_has_trust_tag(self):
        import yaml

        corpus_path = (
            Path(__file__).resolve().parent
            / "eval" / "corpus" / "corpus.yaml"
        )
        with open(corpus_path) as fh:
            data = yaml.safe_load(fh)
        entries = data.get("entries", [])
        rce = [e for e in entries if e["name"] == "gate-yaml-rce"]
        assert len(rce) == 1
        assert "TRUST" in rce[0]["axis_tags"]
