"""Backend attribution threading from L1 into StateFinding.

The ledger records which model raised a finding. That attribution starts
at the reviewer JSON parser: if _json_to_state_findings does not stamp
the backend onto each finding, everything downstream writes "" and the
sample is lost with no way to reconstruct it.
"""
from __future__ import annotations

from code_forge.reviewer_json import _json_to_state_findings


def _one_finding_payload():
    return {
        "findings": [
            {
                "file": "src/a.py",
                "line": 10,
                "description": "possible null deref",
                "severity": "P2",
                "axis": "runtime",
            }
        ],
        "excerpts": [],
    }


def test_backend_stamped_onto_parsed_findings():
    """A named backend reaches every finding it produced."""
    out = _json_to_state_findings(
        _one_finding_payload(), "expert", backend="mimo-pro",
    )
    assert len(out) == 1
    assert out[0].backend == "mimo-pro"


def test_backend_defaults_to_none_when_not_supplied():
    """Callers that have no backend to name leave the field empty.

    Outlet C and older call sites pass two positional args. They must
    keep working, and must not invent an attribution.
    """
    out = _json_to_state_findings(_one_finding_payload(), "expert")
    assert len(out) == 1
    assert out[0].backend is None


def test_sampling_attribution_is_not_a_model_guess():
    """MCP sampling records the outlet, never a guessed model name.

    forge cannot see which model the MCP client chose. Recording a
    guess would poison per-model precision with fabricated attribution,
    so the sampling outlet names itself instead.
    """
    out = _json_to_state_findings(
        _one_finding_payload(), "qodo", backend="mcp-sampling",
    )
    assert out[0].backend == "mcp-sampling"


def test_ledger_row_carries_backend_from_finding():
    """_build_ledger_row reads backend off the finding it is given.

    The whole threading chain exists to land here. If this argument is
    dropped, every row writes "" and the attribution is unrecoverable --
    the review has already run.
    """
    from unittest.mock import MagicMock
    from code_forge.machine import StateMachine
    from code_forge.ledger import TerminalState

    sm = MagicMock(spec=StateMachine)
    sm.resolved_review = MagicMock(base_sha="a" * 40, head_sha="b" * 40)
    sm.ctx_graph_triage = True
    sm.ctx_contract = False
    sm.ctx_whole_file = False
    sm.ctx_canary = True

    row = StateMachine._build_ledger_row(
        sm,
        fingerprint="fp",
        file="src/a.py",
        line=1,
        axis_claim="runtime",
        pass_provenance="L1",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied",
        ts="2026-08-27T00:00:00Z",
        repo_root="/repo",
        backend="mimo-pro",
    )
    assert row.backend == "mimo-pro"
    assert row.ctx_graph_triage is True
    assert row.ctx_canary is True
    assert row.ctx_contract is False


def test_ledger_row_backend_empty_when_finding_has_none():
    """A finding forge raised itself carries no model name."""
    from unittest.mock import MagicMock
    from code_forge.machine import StateMachine
    from code_forge.ledger import TerminalState

    sm = MagicMock(spec=StateMachine)
    sm.resolved_review = MagicMock(base_sha="a" * 40, head_sha="b" * 40)
    sm.ctx_graph_triage = False
    sm.ctx_contract = False
    sm.ctx_whole_file = False
    sm.ctx_canary = False

    row = StateMachine._build_ledger_row(
        sm,
        fingerprint="fp",
        file="",
        line=0,
        axis_claim="clean",
        pass_provenance="CI",
        terminal_state=TerminalState.UNADJUDICATED,
        evidence_class="clean_pass",
        ts="2026-08-27T00:00:00Z",
        repo_root="/repo",
    )
    assert row.backend == ""
