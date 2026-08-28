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
