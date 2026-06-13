# SPDX-License-Identifier: Apache-2.0
"""Integration tests for LegacyRunner wired into StateMachine.

Covers:
- LegacyRunner in advisory_runners produces advisory findings on PASS
- Registry injected into LegacyRunner via _run_advisory_axes hasattr guard
- Legacy findings never leak into blocking findings
- is_advisory property
- Real default l0_runner e2e path (ruff + git blame on a temp repo)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.legacy import LegacyRunner
from code_forge.machine import StateMachine
from code_forge.state import Disposition, Mode, StateFinding


# ---- helpers ---------------------------------------------------------------

_DIFF = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -4,6 +4,7 @@
 unchanged_line_1
 unchanged_line_2
+new_line_here = True
 unchanged_line_3
"""


def _fake_l0_runner(registry, files):
    """Return one pre-existing StateFinding on an unchanged line (line 20)."""
    sf = StateFinding(
        id="fake-pre-existing-001",
        fingerprint="fp-001",
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="foo.py",
        line_range=[20, 20],
        description="F401 unused import os",
        error=None,
        anchor=None,
        evidence_files=[],
    )
    return [sf], []


def _make_resolved(
    *,
    git_diff=_DIFF,
    source_files=None,
):
    return ResolvedReview(
        source_files=source_files or [Path("foo.py")],
        baseline_content=None,
        git_diff=git_diff,
        mode_hint="git",
    )


def _make_sm(tmp_path, *, legacy_runner=None, resolved=None, registry=None):
    """Build a minimal StateMachine with LegacyRunner in advisory_runners."""
    if legacy_runner is None:
        legacy_runner = LegacyRunner(l0_runner=_fake_l0_runner)
    if resolved is None:
        resolved = _make_resolved()
    return StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=resolved,
        source_hash="abc",
        baseline_spec_repr="empty",
        cwd=tmp_path,
        registry=registry if registry is not None else {},
        l0_runner=lambda reg, files: ([], []),
        advisory_runners=[legacy_runner],
    )


# ---- tests -----------------------------------------------------------------


class TestLegacyRunnerWired:
    """LegacyRunner produces advisory findings when wired into StateMachine."""

    def test_legacy_runner_wired(self, tmp_path):
        """After sm.run() on a PASS diff, advisories contain legacy findings."""
        sm = _make_sm(tmp_path)
        sm.run()

        legacy_advisories = [
            a for a in sm._advisories
            if a.axis == "legacy"
            and a.id.startswith("legacy:")
            and not a.id.startswith("legacy-skipped")
        ]
        assert len(legacy_advisories) >= 1, (
            "Expected at least one legacy advisory finding, got %d"
            % len(legacy_advisories)
        )

    def test_advisory_isolation(self, tmp_path):
        """Legacy finding IDs must NOT appear in sm._state.findings."""
        sm = _make_sm(tmp_path)
        sm.run()

        legacy_ids = {
            a.id for a in sm._advisories
            if a.axis == "legacy"
        }
        blocking_ids = {f.id for f in sm._state.findings}
        leaked = legacy_ids & blocking_ids
        assert not leaked, (
            "Legacy advisory IDs leaked into blocking findings: %s" % leaked
        )


class TestRegistryInjection:
    """machine.py _run_advisory_axes injects registry into LegacyRunner."""

    def test_registry_injected_into_legacy_runner(self, tmp_path):
        """After _run_advisory_axes(), legacy_runner.registry is not None."""
        legacy_runner = LegacyRunner(l0_runner=_fake_l0_runner)
        sm = _make_sm(tmp_path, legacy_runner=legacy_runner)
        sm._run_advisory_axes()
        assert legacy_runner.registry is not None, (
            "registry was not injected into LegacyRunner by "
            "_run_advisory_axes"
        )

    def test_integration_stub_registry_prevents_real_tools(self, tmp_path):
        """registry={} ensures no real L0 tools are invoked."""
        legacy_runner = LegacyRunner(l0_runner=_fake_l0_runner)
        sm = _make_sm(tmp_path, legacy_runner=legacy_runner, registry={})
        sm._run_advisory_axes()
        # Stub registry means _fake_l0_runner was used, not real tools.
        assert legacy_runner.registry == {}


class TestLegacyRunnerIsAdvisory:
    """LegacyRunner.is_advisory is True."""

    def test_legacy_runner_is_advisory(self):
        assert LegacyRunner().is_advisory is True


@pytest.mark.integration
class TestRealDefaultL0Runner:
    """R1 real-path regression: default l0_runner with ruff on a temp repo."""

    @pytest.mark.skipif(
        shutil.which("ruff") is None,
        reason="ruff not installed",
    )
    def test_real_default_l0_runner_e2e(self, tmp_path):
        """Production l0_runner path surfaces a real ruff violation with blame.

        Creates a temp git repo with a committed Python file that has
        an unused import (ruff F401). The diff modifies a different line,
        so the import is pre-existing. LegacyRunner with no l0_runner arg
        uses _default_l0_runner from machine.py.
        """
        repo = tmp_path / "repo"
        repo.mkdir()

        # Create a Python file with an unused import (ruff F401).
        py_file = repo / "example.py"
        py_file.write_text(
            "import os\n"
            "\n"
            "def hello():\n"
            "    return 'world'\n"
            "\n"
            "x = 1\n",
            encoding="utf-8",
        )

        # Initialize git repo and commit.
        subprocess.run(
            ["git", "init"],
            cwd=str(repo), check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=str(repo), check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "example.py"],
            cwd=str(repo), check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=str(repo), check=True,
            capture_output=True,
        )

        # Build a diff that modifies a DIFFERENT line (line 6: x = 1 -> x = 2).
        # The unused import on line 1 is pre-existing (unchanged).
        diff_text = (
            "diff --git a/example.py b/example.py\n"
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -3,4 +3,4 @@\n"
            " def hello():\n"
            "     return 'world'\n"
            " \n"
            "-x = 1\n"
            "+x = 2\n"
        )

        # Build a registry with ruff enabled (matching production format).
        from code_forge.registry import ToolConfig

        ruff_config = ToolConfig(
            name="ruff",
            command="ruff",
            args=["check", "--output-format=json"],
            output_format="ruff",
            file_patterns=["*.py"],
        )
        registry = {"ruff": ruff_config}

        runner = LegacyRunner()  # No l0_runner -> uses _default_l0_runner
        runner.source_files = [py_file]
        runner.registry = registry

        advisories = runner.run(diff_text, repo)

        # (1) At least one AdvisoryFinding with axis=="legacy".
        legacy_advisories = [
            a for a in advisories if a.axis == "legacy"
        ]
        assert len(legacy_advisories) >= 1, (
            "Expected at least one legacy advisory, got %d. "
            "advisories=%r" % (len(legacy_advisories), advisories)
        )

        # (2) At least one advisory has REAL blame attribution.
        has_real_blame = any(
            a.attribution != "git-blame: unavailable"
            and a.attribution.startswith("git-blame:")
            for a in legacy_advisories
        )
        assert has_real_blame, (
            "Expected at least one advisory with real git-blame "
            "attribution (not 'unavailable'). Got: %s"
            % [a.attribution for a in legacy_advisories]
        )

        # (3) No infra_errors.
        assert runner.infra_errors == [], (
            "infra_errors should be empty: %s" % runner.infra_errors
        )
