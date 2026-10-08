"""LOCAL DETERMINISTIC STIMULI; NOT ACTUAL REVIEW or native qualification.

Only the LLM transport is replaced. Production factories, response validation,
composite folding, receipt writing, and hardened verification remain genuine.
"""

from collections import Counter
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from code_forge.baseline import ResolvedReview
from code_forge.diff import annotated_diff_prompt_block, parse_diff_hunks, split_diff_for_files
from code_forge.diff_grouping import Group, GroupingResult
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.llm_invoke import LLMInvokeError, LLMResult, Usage
from code_forge.receipt import write_receipts
from code_forge.verify import (
    _diff_validation_context,
    parse_diff_files,
    run_verify,
    validate_excerpts_against_diff,
)

LABEL = "LOCAL DETERMINISTIC STIMULI; NOT ACTUAL REVIEW or native qualification"
CYCLES = (1, 2, 3, 4)
PERSPECTIVES = ("qodo", "expert", "adversarial")
ROLES = ("structural code reviewer", "senior engineer", "adversarial QE")
POST_IMAGES = {
    "src/app.py": "enabled = True\n",
    "settings.json": '{"enabled": true}\n',
    "docs/usage.md": "Enable the documented feature.\n",
}
DIFF = "".join(
    f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old value\n+{content}"
    for path, content in POST_IMAGES.items()
)


def _grouping(*, omit_docs=False):
    groups = [
        Group("integration", "integration", ["src/app.py"], 3),
        Group("config", "config", ["settings.json"], 0),
    ]
    if not omit_docs:
        groups.append(Group("docs", "docs", ["docs/usage.md"], 0))
    return GroupingResult(groups=groups)


def _response(diff):
    """Return real post-image witnesses, never a claimed/verifier-bypassed PASS."""
    post_image, _, _ = _diff_validation_context(diff)
    hunks, _ = parse_diff_hunks(diff)
    excerpts = []
    for path, file_hunks in hunks.items():
        for hunk in file_hunks:
            lines = [n for n in range(hunk["start"], hunk["end"] + 1) if n in post_image[path]]
            assert lines
            excerpts.append(
                {
                    "file": path,
                    "start_line": min(lines),
                    "end_line": max(lines),
                    "content": "\n".join(post_image[path][n] for n in lines) + "\n",
                }
            )
    assert not validate_excerpts_against_diff(diff, excerpts)
    return {"findings": [], "code_excerpts": excerpts}


def _specs(plan):
    resolved = ResolvedReview([Path(p) for p in POST_IMAGES], None, DIFF, "git")
    return [
        {
            "name": group.name,
            "resolved": replace(
                resolved, source_files=[Path(p) for p in group.members], git_diff=group.diff_text
            ),
        }
        for group in plan
        if group.passes
    ]


def _run(tmp_path, monkeypatch, specs, *, grouped=True, fault=None, perspective="adversarial"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "FIXTURE-NOT-REVIEW.txt").write_text(LABEL + "\n")
    for path, content in POST_IMAGES.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    resolved = ResolvedReview([Path(p) for p in POST_IMAGES], None, DIFF, "git")
    calls = []
    cycle = 0

    def local_transport(prompt, **kwargs):
        matching = [s for s in specs if annotated_diff_prompt_block(s["resolved"].git_diff) in prompt]
        assert len(matching) == 1
        spec = matching[0]
        role = prompt.rsplit("You are a ", 1)[1]
        matches = [name for name, text in zip(PERSPECTIVES, ROLES, strict=True) if role.startswith(text)]
        assert len(matches) == 1
        pass_name = matches[0]
        calls.append((cycle, spec["name"], pass_name))
        response = _response(spec["resolved"].git_diff)
        if fault and cycle == 4 and spec is specs[-1] and pass_name == perspective:
            if fault == "failed":
                raise LLMInvokeError("LOCAL fixture late-group failure", is_timeout=True)
            if fault == "rejected":
                del response["findings"]
            elif fault == "mismatched":
                response["code_excerpts"][0]["content"] = "not the post-image\n"
            elif fault == "missing":
                response["code_excerpts"] = []
        return LLMResult(copy.deepcopy(response), Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", local_transport)
    provider = (
        build_grouped_l1_provider("auto", specs, backend=None, max_attempts=1)
        if grouped
        else build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    )
    assert not provider.is_stub_l1
    digest = hashlib.sha256(DIFF.encode()).hexdigest()
    for cycle in CYCLES:
        findings, excerpts, _, _ = provider()
        if not fault or cycle != 4:
            assert findings == []
            assert provider.attempted_excerpts == []
            assert provider.raw_observations == []
            assert provider.unavailable_rejected_passes == set()
        write_receipts(
            receipts_dir=tmp_path / ".code-forge/receipts",
            round_index=cycle - 1,
            l1_findings=findings,
            diff_sha256=digest,
            source_files=resolved.source_files,
            cwd=tmp_path,
            diff_files=parse_diff_files(DIFF),
            diff_text=DIFF,
            reviewer_excerpts=excerpts,
            manifest="declared",
            attempted_excerpts=provider.attempted_excerpts,
            unavailable_rejected_passes=provider.unavailable_rejected_passes,
            raw_observations=provider.raw_observations,
        )
    receipts = [
        json.loads(p.read_text())
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ]
    assert len(receipts) == 12
    assert Counter((r["cycle"], r["pass"]) for r in receipts) == Counter(
        (c, p) for c in CYCLES for p in (1, 2, 3)
    )
    assert Counter(calls) == Counter(
        (c, s["name"], p) for c in CYCLES for s in specs for p in PERSPECTIVES
    )
    verified = run_verify(
        cwd=tmp_path,
        diff_sha256=digest,
        diff_files=parse_diff_files(DIFF),
        diff_text=DIFF,
        required_cycles=4,
        cycles=list(CYCLES),
        hardened=True,
        respect_floor=True,
        require_convergence=True,
    )
    return verified, receipts, calls


def test_original_group_filter_loses_mandatory_config_and_docs(tmp_path, monkeypatch):
    resolved = ResolvedReview(
        [Path("src/app.py")], None, split_diff_for_files(DIFF, ["src/app.py"]), "git"
    )
    result, receipts, calls = _run(
        tmp_path, monkeypatch, [{"name": "integration", "resolved": resolved}]
    )
    assert len(calls) == 12
    assert all(r["pass_status"] == "completed" for r in receipts)
    assert not result.passed
    assert "unwitnessed hunk" in result.reason
    assert {e["file"] for r in receipts for e in r["code_excerpts"]} == {"src/app.py"}


def test_ungrouped_whole_diff_control_passes(tmp_path, monkeypatch):
    resolved = ResolvedReview([Path(p) for p in POST_IMAGES], None, DIFF, "git")
    result, receipts, calls = _run(
        tmp_path, monkeypatch, [{"name": "whole-diff", "resolved": resolved}], grouped=False
    )
    assert len(calls) == 12
    assert all(r["pass_status"] == "completed" for r in receipts)
    assert result.passed
    assert result.checks_run == result.checks_passed == 8


@pytest.mark.parametrize("omit_docs", [False, True])
def test_reconciled_groups_cover_whole_diff_in_all_four_cycles(tmp_path, monkeypatch, omit_docs):
    from code_forge.grouped_coverage import reconcile_grouped_coverage

    plan = reconcile_grouped_coverage(DIFF, _grouping(omit_docs=omit_docs))
    assert len(plan) == 3
    assert plan[-1].provenance == ("non_semantic_fallback" if omit_docs else "promoted")
    result, receipts, calls = _run(tmp_path, monkeypatch, _specs(plan))
    assert len(calls) == 36
    assert all(r["pass_status"] == "completed" for r in receipts)
    for receipt in receipts:
        assert {e["file"] for e in receipt["code_excerpts"]} == set(POST_IMAGES)
    assert result.passed
    assert result.checks_run == result.checks_passed == 8


@pytest.mark.parametrize("fault", ["failed", "rejected", "missing", "mismatched"])
@pytest.mark.parametrize("perspective", PERSPECTIVES)
def test_late_group_failure_cannot_borrow_earlier_coverage(tmp_path, monkeypatch, fault, perspective):
    from code_forge.grouped_coverage import reconcile_grouped_coverage

    plan = reconcile_grouped_coverage(DIFF, _grouping())
    result, receipts, calls = _run(
        tmp_path, monkeypatch, _specs(plan), fault=fault, perspective=perspective
    )
    assert len(calls) == 36
    assert not result.passed
    failed_pass = PERSPECTIVES.index(perspective) + 1
    for receipt in receipts:
        if receipt["cycle"] == 4 and receipt["pass"] == failed_pass:
            assert receipt["pass_status"] != "completed"
            # Earlier groups supplied valid evidence for this very same pass.
            files = {e["file"] for e in receipt["code_excerpts"]}
            assert {"src/app.py", "settings.json"} <= files
            if fault == "mismatched":
                assert receipt["excerpt_validation_errors"]
            else:
                assert files == {"src/app.py", "settings.json"}
        else:
            assert receipt["pass_status"] == "completed"
