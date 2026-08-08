"""The CLI must hand the state machine a real mutation runner.

StateMachine's l2_runner default returns ([], []). That is byte-identical
to a mutation run that found no surviving mutants, so a CLI that forgets to
pass one reports a gate it never ran. build_l2_runner is the real one; it
degrades to a MUTATION_SKIPPED finding when mutmut is absent, so wiring it
is safe even where it cannot measure.

The assertion is on what the CLI passes rather than on a mutation result,
because invoking the captured runner here would start mutmut against the
whole repo.
"""
from __future__ import annotations

from pathlib import Path

from code_forge import cli as cli_module
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import Usage
from code_forge.machine import StateMachine
from code_forge.state import Mode, Verdict

_DEFAULT_L2 = StateMachine.__dataclass_fields__["l2_runner"].default
_DEFAULT_E2E = StateMachine.__dataclass_fields__["e2e_runner"].default


class TestCliL2Wiring:
    def test_cli_passes_a_real_l2_runner_and_e2e_runner(self, tmp_path, monkeypatch):
        captured = {}

        class _RecordingMachine:
            def __init__(self, **kw):
                captured.update(kw)

            def run(self):
                return Verdict.PASS

        monkeypatch.setattr(cli_module, "StateMachine", _RecordingMachine)

        cli_module._run_hold_loop(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda f: None,
            l1_provider=lambda: ([], [], Usage(), 0.0),
            resolved=ResolvedReview(
                source_files=[Path("test.py")],
                baseline_content=None,
                git_diff="diff --git a/test.py b/test.py\n",
                mode_hint="git",
            ),
            source_hash="abc",
            baseline_repr="empty",
            cwd=tmp_path,
            registry={},
            max_rounds=1,
            max_fix_attempts=1,
            state_path=tmp_path / "state.json",
        )

        assert "l2_runner" in captured, (
            "the CLI built its StateMachine without l2_runner. The machine "
            "then falls back to a no-op default returning ([], []), which "
            "reads exactly like a mutation run that found no survivors."
        )
        assert captured["l2_runner"] is not _DEFAULT_L2, (
            "l2_runner was passed, but it is the machine's own no-op "
            "default rather than a runner that can measure anything."
        )
        assert "e2e_runner" in captured, (
            "the CLI built its StateMachine without e2e_runner"
        )
        assert captured["e2e_runner"] is not _DEFAULT_E2E, (
            "e2e_runner was passed, but it is the default no-op "
            "rather than build_e2e_checker."
        )
