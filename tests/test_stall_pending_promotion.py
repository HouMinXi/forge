# SPDX-License-Identifier: Apache-2.0
"""Do not abort the deterministic promotion after an exhausted fix budget."""
from pathlib import Path

from code_forge.autofix import NoChangeAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.machine import StateMachine
from code_forge.state import Mode, StateFinding, Verdict


def test_exhausted_fix_budget_promotes_before_stall(tmp_path):
    def findings(registry, files):
        return ([
            StateFinding(
                id="defect", fingerprint="defect", source="L0",
                disposition=Disposition.CONFIRMED, file="sample.py",
                line_range=[1], description="Unfixed defect",
            ),
            StateFinding(
                id="uncertain", fingerprint="uncertain", source="L1",
                disposition=Disposition.UNCERTAIN, file="sample.py",
                line_range=[2], description="Needs human disposition",
            ),
        ], [])

    machine = StateMachine(
        mode=Mode.LOCAL, falsifier=StubFalsifier(),
        autofixer=NoChangeAutoFixer(), revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("sample.py")], baseline_content=None,
            git_diff=None, mode_hint="file",
        ),
        source_hash="fixture", baseline_spec_repr="fixture", cwd=tmp_path,
        registry={}, l0_runner=findings, max_fix_attempts=3,
        max_total_rounds=8,
    )
    assert machine.run() == Verdict.PENDING
    assert machine._state.round == 3
    assert machine._state.fix_attempts["defect"] == 3
    assert "defect" in machine._state.promoted_fingerprints
    assert not machine._state.converged
    assert not any("review stalled" in error for error in machine._state.infra_errors)
