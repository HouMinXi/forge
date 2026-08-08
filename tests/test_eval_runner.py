"""Tests for eval pipeline replay runner (runner.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_forge.eval.corpus import CorpusEntry
from code_forge.eval.runner import (
    DETERMINISTIC_TAGS,
    AxisHook,
    register_axis_hook,
    replay_entry,
)
from code_forge.eval.scorer import EvalResult


def _entry(
    name: str = "test",
    expected: str = "HOLD",
    tags: list[str] | None = None,
    diff_file: str = "diffs/test.diff",
) -> CorpusEntry:
    return CorpusEntry(
        name=name,
        diff_file=diff_file,
        expected_verdict=expected,
        axis_tags=tags or ["TRUST"],
    )


class TestDeterministicTags:
    """DETERMINISTIC_TAGS constant tests."""

    def test_contains_trust(self) -> None:
        assert "TRUST" in DETERMINISTIC_TAGS

    def test_contains_sec(self) -> None:
        assert "SEC" in DETERMINISTIC_TAGS

    def test_contains_fixval(self) -> None:
        assert "FIXVAL" in DETERMINISTIC_TAGS

    def test_is_frozenset(self) -> None:
        assert isinstance(DETERMINISTIC_TAGS, frozenset)


class TestReplayEntry:
    """replay_entry function tests."""

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_returns_eval_result(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """replay_entry returns EvalResult with correct caught_count."""
        # git init and git apply succeed; the review exits 1 (HOLD).
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        mock_review.return_value = (1, "")

        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()
        diffs_dir = diff_dir / "diffs"
        diffs_dir.mkdir()
        (diffs_dir / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(tags=["TRUST"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert isinstance(result, EvalResult)
        assert result.caught_count >= 1

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_skipped_on_apply_failure(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """Diff apply error = SKIPPED result with reason string."""
        call_count = [0]
        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if "apply" in cmd:
                m.returncode = 1
                m.stderr = b"error: patch failed"
            else:
                m.returncode = 0
            m.stdout = b""
            return m
        mock_run.side_effect = side_effect

        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()
        diffs_dir = diff_dir / "diffs"
        diffs_dir.mkdir()
        (diffs_dir / "test.diff").write_text("bad diff")

        entry = _entry(tags=["TRUST"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.actual_verdict == "SKIPPED"
        assert "apply failed" in result.skipped_reason

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_skipped_on_timeout(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """subprocess.TimeoutExpired = SKIPPED result."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        mock_review.side_effect = subprocess.TimeoutExpired("code-forge", 1800)

        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()
        diffs_dir = diff_dir / "diffs"
        diffs_dir.mkdir()
        (diffs_dir / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(tags=["TRUST"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.actual_verdict == "SKIPPED"
        assert "timeout" in result.skipped_reason.lower()

    def test_deterministic_tags_get_runs_1(self) -> None:
        """Deterministic axis tags (TRUST, SEC, FIXVAL) default to runs=1."""
        for tag in ["TRUST", "SEC", "FIXVAL"]:
            entry = _entry(tags=[tag])
            assert any(t in DETERMINISTIC_TAGS for t in entry.axis_tags)

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_llm_tags_get_runs_3(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """LLM axis tags (RUNTIME, LEGACY, INTENT) default to runs=3."""
        mock_run.return_value = MagicMock(
            returncode=1, stderr=b"", stdout=b"",
        )

        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()
        diffs_dir = diff_dir / "diffs"
        diffs_dir.mkdir()
        (diffs_dir / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(tags=["RUNTIME"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.runs == 3

    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_runs_override(
        self, mock_trust: MagicMock, mock_run: MagicMock, tmp_path: Path,
    ) -> None:
        """--runs N overrides default run count."""
        mock_run.return_value = MagicMock(
            returncode=0, stderr=b"", stdout=b"",
        )

        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()
        diffs_dir = diff_dir / "diffs"
        diffs_dir.mkdir()
        (diffs_dir / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(tags=["RUNTIME"])
        result = replay_entry(entry, diff_dir, "test-backend", runs=5)
        assert result.runs == 5

    def test_missing_diff_file_skipped(self, tmp_path: Path) -> None:
        """Missing diff file at runtime = SKIPPED."""
        diff_dir = tmp_path / "corpus"
        diff_dir.mkdir()

        entry = _entry(diff_file="diffs/nonexistent.diff")
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.actual_verdict == "SKIPPED"
        assert "not found" in result.skipped_reason


class TestAxisHook:
    """AxisHook registration tests."""

    def test_hook_has_pre_review(self) -> None:
        hook = AxisHook()
        assert hasattr(hook, "pre_review")

    def test_hook_has_post_review(self) -> None:
        hook = AxisHook()
        assert hasattr(hook, "post_review")

    def test_default_methods_are_noop(self) -> None:
        hook = AxisHook()
        entry = _entry()
        result = EvalResult(
            entry=entry, actual_verdict="PASS",
            runs=1, caught_count=0, skipped_reason="",
        )
        # Should not raise
        hook.pre_review(entry)
        hook.post_review(entry, result)

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_hooks_called_during_replay(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """register_axis_hook: pre_review and post_review called during replay."""
        import code_forge.eval.runner as runner_mod
        original_hooks = runner_mod._AXIS_HOOKS.copy()
        try:
            runner_mod._AXIS_HOOKS.clear()

            mock_hook = MagicMock(spec=AxisHook)
            register_axis_hook(mock_hook)

            mock_run.return_value = MagicMock(
                returncode=0, stderr=b"", stdout=b"",
            )
            mock_review.return_value = (0, "")

            diff_dir = tmp_path / "corpus"
            diff_dir.mkdir()
            diffs_dir = diff_dir / "diffs"
            diffs_dir.mkdir()
            (diffs_dir / "test.diff").write_text("--- a/f\n+++ b/f\n")

            entry = _entry(tags=["TRUST"])
            replay_entry(entry, diff_dir, "test-backend")

            mock_hook.pre_review.assert_called()
            mock_hook.post_review.assert_called()
        finally:
            runner_mod._AXIS_HOOKS[:] = original_hooks

    def test_register_appends_to_list(self) -> None:
        """register_axis_hook appends, does NOT use entry_points or importlib."""
        import code_forge.eval.runner as runner_mod
        original_hooks = runner_mod._AXIS_HOOKS.copy()
        try:
            runner_mod._AXIS_HOOKS.clear()
            h1 = AxisHook()
            h2 = AxisHook()
            register_axis_hook(h1)
            register_axis_hook(h2)
            assert len(runner_mod._AXIS_HOOKS) == 2
            assert runner_mod._AXIS_HOOKS[0] is h1
            assert runner_mod._AXIS_HOOKS[1] is h2
        finally:
            runner_mod._AXIS_HOOKS[:] = original_hooks

    def test_no_plugin_discovery_imports(self) -> None:
        """No entry_points, importlib.import_module, or pkg_resources (carry-forward 3)."""
        import inspect
        import code_forge.eval.runner as runner_mod
        source = inspect.getsource(runner_mod)
        for banned in ["entry_points", "importlib.import_module", "pkg_resources"]:
            # Check import statements only, not docstring prose
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                    continue
                if (stripped.startswith("from") or stripped.startswith("import")) and banned in stripped:
                    pytest.fail(f"Found banned import: {stripped}")


class TestCloseoutBehaviors:
    """Regression guards for the Fix-1 closeout behaviors."""

    def test_create_gate_yaml_merges_when_existing(self, tmp_path: Path) -> None:
        """_create_gate_yaml merges harness backend when diff already created gate.yaml."""
        import yaml
        from code_forge.eval.runner import _create_gate_yaml

        gate_dir = tmp_path / ".code-forge"
        gate_dir.mkdir()
        existing_data = {
            "backends": {"from-diff": {"type": "api", "base_url": "https://attacker.example"}},
        }
        (gate_dir / "gate.yaml").write_text(
            yaml.dump(existing_data, default_flow_style=False), encoding="utf-8"
        )

        gate_path = _create_gate_yaml(tmp_path, "harness-backend")
        loaded = yaml.safe_load(gate_path.read_text(encoding="utf-8"))

        assert "from-diff" in loaded["backends"], "diff-created backend must be preserved"
        assert "harness-backend" in loaded["backends"], "harness backend must be added"

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_forge_skip_worktree_check_in_subprocess_env(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """FORGE_SKIP_WORKTREE_CHECK=1 is in the env passed to the review."""
        captured_envs: list[dict] = []

        def review(cmd, cwd, env, timeout_s):
            captured_envs.append(dict(env or {}))
            return 0, ""

        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        mock_review.side_effect = review

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        replay_entry(_entry(tags=["TRUST"]), diff_dir, "test-backend")

        assert captured_envs, "code-forge subprocess must be called"
        assert captured_envs[0].get("FORGE_SKIP_WORKTREE_CHECK") == "1"

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_diff_applied_before_record_trust(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """git apply is invoked before record_trust (prevents gate.yaml collision)."""
        call_order: list[str] = []

        def run_side(cmd, **kwargs):
            if "apply" in (cmd or []):
                call_order.append("apply")
            m = MagicMock()
            m.returncode = 0
            m.stderr = b""
            m.stdout = b""
            return m

        mock_run.side_effect = run_side
        mock_review.return_value = (0, "")
        mock_trust.side_effect = lambda *a, **kw: call_order.append("record_trust")

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        replay_entry(_entry(tags=["TRUST"]), diff_dir, "test-backend")

        assert "apply" in call_order
        assert "record_trust" in call_order
        assert call_order.index("apply") < call_order.index("record_trust")

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.shutil.copytree")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_base_files_seeded_before_apply(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_copytree: MagicMock, mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """When base_files/<entry> exists, copytree runs before git apply."""
        call_order: list[str] = []

        def run_side(cmd, **kwargs):
            if "apply" in (cmd or []):
                call_order.append("apply")
            m = MagicMock()
            m.returncode = 0
            m.stderr = b""
            m.stdout = b""
            return m

        mock_run.side_effect = run_side
        mock_review.return_value = (0, "")
        mock_copytree.side_effect = lambda *a, **kw: call_order.append("copytree")

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")
        base = diff_dir / "base_files" / "test"
        base.mkdir(parents=True)
        (base / "seed.py").write_text("# seed\n")

        entry = _entry(name="test", tags=["TRUST"])
        replay_entry(entry, diff_dir, "test-backend")

        mock_copytree.assert_called_once()
        assert "copytree" in call_order
        assert "apply" in call_order
        assert call_order.index("copytree") < call_order.index("apply")

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.shutil.copytree")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_base_files_seed_oserror_returns_skipped(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_copytree: MagicMock, mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """OSError during seed copytree -> SKIPPED, not crash."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        mock_review.return_value = (0, "")
        mock_copytree.side_effect = OSError("Permission denied")

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")
        base = diff_dir / "base_files" / "test"
        base.mkdir(parents=True)
        (base / "seed.py").write_text("# seed\n")

        entry = _entry(name="test", tags=["TRUST"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.actual_verdict == "SKIPPED"
        assert "infra" in result.skipped_reason.lower()


class TestInfraFailureDetection:
    """Skip-taxonomy: backend/infra failures must score SKIPPED, not caught."""

    def test_connection_refused_is_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert _is_infra_failure("ConnectionRefusedError: [Errno 111]")

    def test_connection_timed_out_is_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert _is_infra_failure("Connection timed out")

    def test_read_timed_out_is_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert _is_infra_failure("Read timed out")

    def test_api_connection_error_is_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert _is_infra_failure("APIConnectionError: server unreachable")

    def test_normal_review_failure_is_not_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert not _is_infra_failure("Review completed with findings")

    def test_empty_stderr_is_not_infra(self) -> None:
        from code_forge.eval.runner import _is_infra_failure
        assert not _is_infra_failure("")

    def test_generic_timeout_word_is_not_infra(self) -> None:
        """'Timeout' as a lone word must NOT trigger infra classification."""
        from code_forge.eval.runner import _is_infra_failure
        assert not _is_infra_failure("WARNING: timeout parameter was ignored")

    @patch("code_forge.eval.runner._run_review")
    @patch("code_forge.eval.runner.subprocess.run")
    @patch("code_forge.eval.runner.record_trust")
    def test_infra_failure_returns_skipped(
        self, mock_trust: MagicMock, mock_run: MagicMock,
        mock_review: MagicMock, tmp_path: Path,
    ) -> None:
        """Backend down during review -> SKIPPED, not HOLD."""
        mock_run.return_value = MagicMock(returncode=0, stderr=b"", stdout=b"")
        mock_review.return_value = (1, "ConnectionRefusedError: [Errno 111]")

        diff_dir = tmp_path / "corpus"
        (diff_dir / "diffs").mkdir(parents=True)
        (diff_dir / "diffs" / "test.diff").write_text("--- a/f\n+++ b/f\n")

        entry = _entry(tags=["TRUST"])
        result = replay_entry(entry, diff_dir, "test-backend")
        assert result.actual_verdict == "SKIPPED"
        assert "infra" in result.skipped_reason.lower()
