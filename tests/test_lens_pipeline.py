# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 8: lens findings through the real downstream pipeline.

Recorded reviewer and falsifier responses are inline; no backend is called.
Three claims:

* T18: a lens finding that rests on library behaviour needs an execution
  receipt exactly like any other finding, and nothing in the lens text makes
  the receipt gate fire on the lens block itself.
* T17: a contract sentence about intent is not a "Do NOT Flag" exemption, so
  it neither drops a recorded P1 lens finding nor lowers its severity.
* T12: earned clean credit comes from Forge cycles only. A hand-written
  report does not stand in for a missing cycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge import factories
from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.cli import _DO_NOT_FLAG_PREAMBLE, _merge_contract_spec
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.falsify_real import RealFalsifier
from code_forge.falsify_receipt import asserts_library_behaviour, check_receipt
from code_forge.lenses import LENS_BLOCK
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine, _FixpointResult
from code_forge.registry import ToolConfig
from code_forge.state import Mode, StateFinding, Verdict, load_state
from code_forge.verify import run_verify

from .test_machine_local import _make_sm_fp, _prime
from .test_verify import _receipt, _sha

# Lens descriptions that rest on how a library behaves. The first three are the
# shapes the earlier review round quoted; each is something a model cannot
# settle by reading the diff.
LIBRARY_CLAIMS = (
    "[adversarial] [lens:L4] Go reader of state.json breaks: json.loads returns str for the new key but the Go struct expects int",
    "[adversarial] [lens:L4] requests.get raises on timeout so teardown never runs",
    "[expert] [lens:L5] cast(Any, x) hides a None case; the library returns None here",
)
# The same defects stated against repository code, the way the lens text asks.
REPO_CLAIMS = (
    "[adversarial] [lens:L4] state.py:308 now writes lens_tag as a string; tools/report.go:41 decodes it into an int field and fails",
    "[adversarial] [lens:L4] cleanup() at runner.py:88 sits after fetch() with no finally, so an exception from fetch skips teardown",
    "[expert] [lens:L5] cast(Any, x) at cli.py:120 hides that load_cfg() at cli.py:98 can return None on a missing file",
)

DIFF = (
    "diff --git a/f.py b/f.py\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def f(x):\n"
    "-    return x\n"
    "+    y = x + 1\n"
    "+    return y\n"
)
POST_IMAGE = "## f.py\n```python\ndef f(x):\n    y = x + 1\n    return y\n```\n"
EXCERPTS = [{"file": "f.py", "start_line": 2, "end_line": 3, "content": "    y = x + 1\n    return y"}]
TWO_ROWS = "| Entity | File |\n|--|--|\n| f | f.py |"
INTENT_CONTRACT = "This change intentionally removes check X from f.\n"


def _finding(description: str, disposition=Disposition.UNCERTAIN) -> StateFinding:
    return StateFinding(
        id="x",
        fingerprint="fp",
        source="L1",
        disposition=disposition,
        file="f.py",
        line_range=[1, 1],
        description=description,
    )


def _falsify(description: str, verdict: str, extra: dict | None = None):
    """RealFalsifier.falsify with a recorded response and no backend."""
    response = {"verdict": verdict, "reasoning": "model says so"}
    response.update(extra or {})
    finding = _finding(description)
    with patch("code_forge.falsify_real.llm_invoke", return_value=LLMResult(content=response)):
        return RealFalsifier().falsify(finding), finding


def _build_a(response: dict, contract_spec: str = "", prompts: list | None = None):
    def fake(prompt, **kw):
        if prompts is not None:
            prompts.append(prompt)
        return LLMResult(content=response, usage=Usage())

    resolved = ResolvedReview(
        source_files=[Path("f.py")],
        baseline_content=None,
        git_diff=DIFF,
        mode_hint="git",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=fake):
        return factories.build_l1_provider(
            "auto",
            resolved,
            backend=None,
            conventions_digest="snake_case",
            post_image=POST_IMAGE,
            graph_impact_context=TWO_ROWS,
            contract_spec=contract_spec,
            focus_spec="",
            manifest_spec="",
            review_lenses=True,
        )()


# -- T18 ---------------------------------------------------------------------


@pytest.mark.parametrize("description", LIBRARY_CLAIMS)
@pytest.mark.parametrize("verdict", ["CONFIRMED", "DISMISSED"])
def test_library_claim_without_a_receipt_is_uncertain(description, verdict):
    assert asserts_library_behaviour(description) is not None
    disposition, finding = _falsify(description, verdict)
    assert disposition == Disposition.UNCERTAIN
    assert finding.falsify_reasoning != "model says so"


@pytest.mark.parametrize("description", LIBRARY_CLAIMS)
def test_library_claim_with_a_receipt_keeps_its_verdict(description):
    receipt = {"receipt": {"command": "python -c 'print(1)'", "output": "1"}}
    disposition, _ = _falsify(description, "CONFIRMED", receipt)
    assert disposition == Disposition.CONFIRMED


@pytest.mark.parametrize("description", REPO_CLAIMS)
@pytest.mark.parametrize("verdict", ["CONFIRMED", "DISMISSED"])
def test_repo_claim_keeps_its_verdict_without_a_receipt(description, verdict):
    assert asserts_library_behaviour(description) is None
    assert check_receipt(description, {"verdict": verdict}).should_downgrade is False
    disposition, finding = _falsify(description, verdict)
    assert disposition == Disposition(verdict)
    assert finding.falsify_reasoning == "model says so"


def test_no_lens_block_line_is_itself_a_library_claim():
    lines = LENS_BLOCK.splitlines()
    assert len(lines) > 5
    for line in lines:
        assert asserts_library_behaviour(line) is None, line


def test_recorded_empty_responses_on_a_clean_fixture_add_no_findings_and_count_clean(tmp_path):
    response = {"findings": [], "code_excerpts": EXCERPTS}
    findings, excerpts, _usage, _elapsed = _build_a(response)
    assert findings == []
    assert {e["pass_name"] for e in excerpts} == {"qodo", "expert", "adversarial"}

    def provider():
        return _build_a(response)

    machine = StateMachine(
        mode=Mode.LOCAL,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda f: None,
        resolved_review=ResolvedReview(
            source_files=[Path("f.py")], baseline_content=None, git_diff=None, mode_hint="git"
        ),
        source_hash="a",
        baseline_spec_repr="HEAD",
        cwd=tmp_path,
        registry={
            "ruff": ToolConfig(
                name="ruff", command="true", args=[], output_format="ruff", file_patterns=["*.py"]
            )
        },
        l0_runner=lambda _r, _f: ([], []),
        l1_provider=provider,
        max_total_rounds=10,
        coverage_l1_active=False,
    )
    assert machine.run() == Verdict.PASS
    state = load_state(tmp_path / ".code-forge" / "state.json")
    assert state.consecutive_clean_rounds >= 3
    assert [f for f in state.findings if f.source == "L1"] == []
    assert all(f.disposition != Disposition.CONFIRMED for f in state.findings)


# -- T17 ---------------------------------------------------------------------

P1_LENS_FINDING = {
    "file": "f.py",
    "line": 2,
    "severity": "P1",
    "description": "[lens:L2] f.py:2 drops the validation check X performed, so callers pass bad input",
    "excerpt": "    y = x + 1",
}


def _recorded_p1(contract_spec: str):
    prompts: list[str] = []
    response = {"findings": [P1_LENS_FINDING], "code_excerpts": EXCERPTS}
    findings, _excerpts, _usage, _elapsed = _build_a(response, contract_spec, prompts)
    return findings, prompts


def test_intent_sentence_is_not_a_do_not_flag_exemption():
    spec = _merge_contract_spec("", INTENT_CONTRACT)
    assert "intentionally removes check X" in spec
    assert _DO_NOT_FLAG_PREAMBLE not in spec
    assert "Do NOT Flag" not in spec


def test_intent_contract_keeps_every_recorded_p1_lens_finding():
    findings, prompts = _recorded_p1(_merge_contract_spec("", INTENT_CONTRACT))
    assert [f.severity for f in findings] == ["P1", "P1", "P1"]
    assert {f.description for f in findings} == {
        "[%s] %s" % (name, P1_LENS_FINDING["description"]) for name in ("qodo", "expert", "adversarial")
    }
    assert len(prompts) == 3
    for prompt in prompts:
        assert "intentionally removes check X" in prompt
        assert LENS_BLOCK in prompt


def test_intent_contract_leaves_a_confirmed_p1_lens_finding_a_reset(tmp_path):
    findings, _ = _recorded_p1(_merge_contract_spec("", INTENT_CONTRACT))
    machine = _make_sm_fp(tmp_path)
    for finding in findings:
        finding.disposition = Disposition.CONFIRMED
        machine._state.findings.append(finding)
    _prime(machine, *[f.fingerprint for f in findings])
    assert machine._fixpoint_reached() == _FixpointResult.RESET
    assert [f.severity for f in machine._state.findings] == ["P1", "P1", "P1"]


def test_do_not_flag_section_is_prompt_text_and_does_not_filter_findings():
    # Control: a real exemption only changes what the reviewer is told. The
    # pipeline never post-filters, so the recorded finding is still returned.
    contract = INTENT_CONTRACT + "\n## Do NOT Flag\n\n- removal of check X\n"
    spec = _merge_contract_spec("", contract)
    assert _DO_NOT_FLAG_PREAMBLE in spec
    findings, prompts = _recorded_p1(spec)
    assert [f.severity for f in findings] == ["P1", "P1", "P1"]
    assert all("removal of check X" in p for p in prompts)


# -- T12 ---------------------------------------------------------------------

SHA = _sha("lens-pipeline-t12")
DIFF_FILES = {"src/f.py": [1, 2]}


def _write_cycles(tmp_path: Path, cycles: int) -> Path:
    receipts = tmp_path / ".code-forge" / "receipts"
    receipts.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "f.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for cycle in range(1, cycles + 1):
        for pass_n in (1, 2, 3):
            path = receipts / ("receipt-c%dp%d.json" % (cycle, pass_n))
            path.write_text(json.dumps(_receipt(cycle, pass_n, SHA)), encoding="utf-8")
    return receipts


def _verify(tmp_path: Path):
    return run_verify(tmp_path, SHA, DIFF_FILES, hardened=False, required_cycles=3)


def test_three_forge_cycles_satisfy_the_window(tmp_path):
    _write_cycles(tmp_path, 3)
    result = _verify(tmp_path)
    assert result.passed, result.reason


def test_two_forge_cycles_and_a_manual_report_do_not(tmp_path):
    receipts = _write_cycles(tmp_path, 2)
    (receipts / "lens-report.md").write_text("L1-L5 reviewed by hand: clean\n", encoding="utf-8")
    (receipts / "manual-lens-report.json").write_text(
        json.dumps(_receipt(3, 1, SHA) | {"skill": "manual"}), encoding="utf-8"
    )
    (tmp_path / ".code-forge" / "lens-report.md").write_text("clean\n", encoding="utf-8")
    result = _verify(tmp_path)
    assert result.passed is False
    assert result.reason.startswith("missing receipts: 6/9"), result.reason
    state = tmp_path / ".code-forge" / "state.json"
    assert not state.exists() or load_state(state).earned_clean_window is None
