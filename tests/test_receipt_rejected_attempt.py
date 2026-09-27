"""Rejected evidence must not borrow another pass's valid excerpts."""

import copy
import json
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.receipt import write_receipts
from code_forge.source import compute_source_hash
from code_forge.state import Disposition, Mode, StateFinding, Verdict

DIFF = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1 +1 @@\n-const value = 1;\n+const value = 2;\n"
)
CONTENT = "const value = 2;\n"


@pytest.mark.parametrize("pass_name,pass_number", [("qodo", 1), ("expert", 2), ("adversarial", 3)])
@pytest.mark.parametrize("has_accepted_chunk", [False, True])
def test_rejected_attempt_marks_only_its_pass_incomplete(
    tmp_path, pass_name, pass_number, has_accepted_chunk
):
    attempted = {"pass_name": pass_name, "findings": [], "code_excerpts": []}
    snapshot = copy.deepcopy(attempted)
    excerpt = {
        "pass_name": pass_name,
        "file": "control.ts",
        "start_line": 1,
        "end_line": 1,
        "content": CONTENT,
    }
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        [],
        "frozen-diff",
        [],
        tmp_path,
        reviewer_excerpts=[excerpt] if has_accepted_chunk else [],
        attempted_excerpts=[attempted],
        manifest="declared",
    )
    receipts = [json.loads(path.read_text()) for path in paths]
    assert receipts[pass_number - 1]["pass_status"] == "incomplete"
    assert all(r["pass_status"] == "completed" for r in receipts if r["pass"] != pass_number)
    assert attempted == snapshot
    raw = json.loads(
        (tmp_path / "receipts" / "attempted" / f"attempted-c1p{pass_number}-0.json").read_text()
    )
    assert raw["payload"] == snapshot
    assert receipts[pass_number - 1]["code_excerpts"] == (
        [{k: v for k, v in excerpt.items() if k != "pass_name"} | {"rationale": "reviewer-provided"}]
        if has_accepted_chunk
        else []
    )


def test_rejected_attempt_does_not_overwrite_timeout(tmp_path):
    finding = StateFinding(
        id="l1-expert-invoke-fail",
        fingerprint="timeout-expert",
        source="INFRA",
        disposition=Disposition.CONFIRMED,
        file="<llm-invoke>",
        line_range=[0, 0],
        description="timeout",
        is_timeout=True,
    )
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        [finding],
        "frozen-diff",
        [],
        tmp_path,
        attempted_excerpts=[{"pass_name": "expert", "findings": [], "code_excerpts": []}],
        manifest="declared",
    )
    assert json.loads(paths[1].read_text())["pass_status"] == "timeout"


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("rejected_pass", [None, 1, 2, 3])
def test_machine_cannot_borrow_other_pass_evidence(tmp_path, monkeypatch, mode, rejected_pass):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "control.ts").write_text(CONTENT)
    directory = tmp_path / ".code-forge"
    directory.mkdir()
    (directory / "gate.yaml").write_text('test:\n  command: ["true"]\n')
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    valid = {
        "findings": [],
        "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 1, "content": CONTENT}],
    }
    calls = []

    def transport(*args, **kwargs):
        calls.append(args)
        current_pass = (len(calls) - 1) % 3 + 1
        payload = {"findings": [], "code_excerpts": []} if current_pass == rejected_pass else valid
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    provider = build_l1_provider("real", resolved, max_attempts=1)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="rejected-evidence",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    result = machine.run()
    disk = json.loads((directory / "state.json").read_text())
    assert len(calls) >= 3
    if rejected_pass is None:
        assert result == Verdict.PASS
        assert disk["verdict"] == "PASS"
    else:
        assert result != Verdict.PASS
        assert disk["verdict"] != "PASS"
        assert machine._state.consecutive_clean_rounds == 0
        assert machine._state.rounds_with_failed_pass == 0
        assert list((directory / "receipts" / "attempted").glob("*.json"))
