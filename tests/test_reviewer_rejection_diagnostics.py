# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""First validation diagnostics must explain rejection without granting credit."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.invocation_review import OrdinaryReviewAudit
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.reviewer_json import ExcerptEvidenceError
from code_forge.state import Disposition, Mode, Verdict
from tests import test_review_pass_evidence_contract as contract
from tests.test_ordinary_audit_privacy import _Scopes

isolated_execution = contract.isolated_execution


def finding_reply():
    return contract.good("control.ts") | {
        "findings": [
            {
                "file": "control.ts",
                "line": 1,
                "severity": "P2",
                "description": "synthetic candidate",
                "excerpt": contract.CONTENT,
            }
        ]
    }


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
@pytest.mark.parametrize("damage", [None, "anchor-as-root", "count", "empty-root", "absent-root"])
def test_factory_to_machine_diagnostics_preserve_evidence_gate(
    tmp_path, monkeypatch, capsys, mode, damage
):
    payload = finding_reply()
    if damage == "anchor-as-root":
        payload["code_excerpts"] = [{"file": "control.ts", "line": 1, "excerpt": contract.CONTENT}]
    elif damage == "count":
        payload["code_excerpts"][0]["end_line"] = 8
    elif damage == "empty-root":
        payload["code_excerpts"] = []
    elif damage == "absent-root":
        del payload["code_excerpts"]
    original = copy.deepcopy(payload)
    calls = []

    def transport(prompt, **kwargs):
        current = payload if "You are a senior engineer:" in prompt else contract.good("control.ts")
        calls.append(copy.deepcopy(current))
        return LLMResult(copy.deepcopy(current), Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    diff = contract.diff_for("control.ts")
    provider = build_l1_provider("real", ResolvedReview([Path("control.ts")], None, diff, "git"))
    machine = contract.machine_for(tmp_path, mode, provider, diff, ["control.ts"])
    machine.falsifier = SimpleNamespace(falsify=lambda finding: Disposition.DISMISSED)
    result = machine.run()
    diagnostics = [
        line for line in capsys.readouterr().err.splitlines() if "reviewer response rejected" in line
    ]
    assert payload == original
    receipts_dir = tmp_path / ".code-forge/receipts"
    receipt = json.loads((receipts_dir / "receipt-c1p2.json").read_text())
    if damage is None:
        assert result is Verdict.PASS and machine._state.converged
        assert machine._state.consecutive_clean_rounds == (3 if mode is Mode.LOCAL else 0)
        assert diagnostics == []
        assert receipt["pass_status"] == "completed"
        assert not (receipts_dir / "attempted").exists()
    else:
        assert result is not Verdict.PASS and not machine._state.converged
        assert machine._state.consecutive_clean_rounds == 0
        assert any(f.id == "RECEIPT_INVALID" for f in machine._state.findings)
        assert receipt["pass_status"] == (
            "schema_fail" if damage in ("anchor-as-root", "absent-root") else "incomplete"
        )
        assert receipt["code_excerpts"] == []
        expected = {
            "anchor-as-root": ("schema", "code_excerpt[0] missing:"),
            "count": ("evidence", "declares 8 lines but carries 1"),
            "empty-root": ("missing-excerpts", "required L1 pass has no root code_excerpts"),
            "absent-root": ("schema", "missing required field: code_excerpts"),
        }[damage]
        assert len(diagnostics) == len(calls) // 3
        assert all(
            "L1 pass 'expert'" in line and f"({expected[0]})" in line and expected[1] in line
            for line in diagnostics
        )
        artifacts = [
            json.loads(path.read_text()) for path in (receipts_dir / "attempted").glob("*.json")
        ]
        assert artifacts and all(
            art["payload"] == original | {"pass_name": "expert"} for art in artifacts
        )
        assert provider.raw_observations == [] and provider.correction_observations == []


@pytest.mark.parametrize(
    "suffix",
    ["\n\r\x1b[31m\ud800", "\u202e" * 1000, "x" * 1000],
    ids=["controls-surrogate", "long-unicode", "long-ascii"],
)
def test_evidence_diagnostic_is_bounded_ascii_without_changing_attempt(monkeypatch, capsys, suffix):
    payload = finding_reply()
    payload["code_excerpts"][0].update(file="untrusted" + suffix, end_line=8)
    original = copy.deepcopy(payload)
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke",
        lambda *a, **k: LLMResult(copy.deepcopy(payload), Usage(), 0.0),
    )
    diff = contract.diff_for("control.ts")
    provider = build_l1_provider("real", ResolvedReview([Path("control.ts")], None, diff, "git"))
    provider()
    lines = [
        line for line in capsys.readouterr().err.splitlines() if "reviewer response rejected" in line
    ]
    assert len(lines) == 3
    assert all(
        line.isascii() and all(32 <= ord(char) <= 126 for char in line) and len(line) < 650
        for line in lines
    )
    if len(suffix) > 500:
        assert all(line.endswith(" [truncated]") for line in lines)
    else:
        assert all("\\n\\r\\x1b" in line and "\\ud800" in line for line in lines)
    assert payload == original
    assert all(
        attempt == original | {"pass_name": name}
        for attempt, name in zip(
            provider.attempted_excerpts, ("qodo", "expert", "adversarial"), strict=True
        )
    )


def test_audited_diagnostic_does_not_render_private_exception(monkeypatch, capsys):
    class PrivateEvidenceError(ExcerptEvidenceError):
        def __str__(self):
            raise AssertionError("audited diagnostics must not format private validation exceptions")

    def reject(*args, **kwargs):
        raise PrivateEvidenceError("PRIVATE_VALIDATION_SENTINEL")

    payload = finding_reply()
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke",
        lambda *a, **k: LLMResult(copy.deepcopy(payload), Usage(), 0.0),
    )
    monkeypatch.setattr("code_forge.reviewer_json.validate_reviewer_json", reject)
    diff = contract.diff_for("control.ts")
    provider = build_l1_provider("real", ResolvedReview([Path("control.ts")], None, diff, "git"))
    audit = OrdinaryReviewAudit(source=diff, environ={})
    monkeypatch.setattr(audit, "_open_host", _Scopes)
    findings, excerpts, _, _ = provider.call_with_audit(audit.round(0))
    lines = [
        line for line in capsys.readouterr().err.splitlines() if "reviewer response rejected" in line
    ]
    assert len(lines) == 3 and all(
        line.endswith("(evidence): invalid reviewer response") for line in lines
    )
    assert all(f.source == "UNTRUSTED" for f in findings) and excerpts == []
    assert len(provider.attempted_excerpts) == len(provider.excerpt_rejections) == 3


def test_inapplicable_empty_excerpts_are_not_reported_as_rejection(monkeypatch, capsys):
    payload = {"findings": [], "code_excerpts": []}
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke",
        lambda *a, **k: LLMResult(copy.deepcopy(payload), Usage(), 0.0),
    )
    provider = build_l1_provider(
        "real", ResolvedReview([Path("control.ts")], None, contract.MODE, "git")
    )
    provider()
    assert "reviewer response rejected" not in capsys.readouterr().err
    assert provider.attempted_excerpts == []
