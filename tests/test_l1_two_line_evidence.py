"""Frozen-source count recovery through the real L1/receipt boundary.

The shared harness blocks subprocess/network activity. Only reviewer transport,
L0/L2 and ledger-root discovery are synthetic; JSON validation, factory, machine,
receipt writer/verifier and state persistence are real. No native qualification.
"""

import copy

import pytest

from code_forge.reviewer_json import ExcerptEvidenceError, validate_reviewer_json
from code_forge.state import Mode, Verdict
from tests import _review_harness as harness
from tests._outside_gate import outside_gate_root


offline_workspace = harness.offline_workspace


def _fixture(kind="long", *, start=1):
    lines = ["alpha = 1", "beta = 2", "gamma = 3"]
    if kind == "short":
        lines += ["", ""]
    diff = (
        "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
        f"@@ -0,0 +{start},{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    )
    return diff, {
        "findings": [],
        "code_excerpts": [
            {
                "file": "control.ts",
                "start_line": start,
                "end_line": start + (4 if kind == "short" else 0),
                "content": "\n".join(lines[:3]),
            }
        ],
    }


@pytest.mark.parametrize("kind", ["short", "long"])
@pytest.mark.parametrize("as_list", [False, True])
def test_only_host_frozen_source_admits_the_existing_two_line_cases(kind, as_list):
    diff, payload = _fixture(kind)
    if as_list:
        payload["code_excerpts"][0]["content"] = payload["code_excerpts"][0]["content"].split("\n")
    original = copy.deepcopy(payload)
    with pytest.raises(ExcerptEvidenceError):
        validate_reviewer_json(payload)
    assert validate_reviewer_json(payload, frozen_diff=diff) == original
    assert payload == original


@pytest.mark.parametrize("kind", ["short", "long"])
@pytest.mark.parametrize("fault", ["content", "file", "coordinate", "three", "nonblank", "indent"])
def test_frozen_source_does_not_widen_other_count_errors(kind, fault):
    diff, payload = _fixture(kind)
    excerpt = payload["code_excerpts"][0]
    if fault == "content":
        excerpt["content"] = excerpt["content"].replace("alpha", "fabricated")
    elif fault == "file":
        excerpt["file"] = "missing.ts"
    elif fault == "coordinate":
        excerpt["start_line"] += 20
        excerpt["end_line"] += 20
    elif fault == "three":
        if kind == "short":
            excerpt["end_line"] += 1
        else:
            excerpt["content"] += "\nextra = 4"
    elif fault == "nonblank":
        diff = (
            diff.replace("+\n+\n", "+cleanup()\n+\n")
            if kind == "short"
            else diff.replace("gamma = 3", "gamma = 9")
        )
    elif fault == "indent":
        excerpt["content"] = "  " + excerpt["content"]
    with pytest.raises(ExcerptEvidenceError):
        validate_reviewer_json(payload, frozen_diff=diff)


@pytest.mark.parametrize("fault", ["none", "empty", "malformed", "duplicate", "overlap", "model-only"])
def test_unavailable_or_ambiguous_source_stays_rejected(fault):
    diff, payload = _fixture()
    if fault == "none":
        diff = None
    elif fault == "empty":
        diff = ""
    elif fault == "malformed":
        diff = diff.replace("+1,3", "+1,99")
    elif fault == "duplicate":
        diff += diff
    elif fault == "overlap":
        diff += "@@ -0,0 +2,1 @@\n+beta = 2\n"
    elif fault == "model-only":
        payload["frozen_diff"] = diff
        diff = None
    with pytest.raises(ExcerptEvidenceError):
        validate_reviewer_json(payload, frozen_diff=diff)


@pytest.mark.parametrize(
    "field,value",
    [("start_line", True), ("end_line", "3"), ("start_line", 0), ("file", ""), ("content", ["a", 3])],
)
def test_source_does_not_bypass_shape_checks(field, value):
    diff, payload = _fixture()
    payload["code_excerpts"][0][field] = value
    with pytest.raises(ValueError):
        validate_reviewer_json(payload, frozen_diff=diff)


@pytest.mark.parametrize("kind", ["short", "long"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_real_l1_machine_retains_source_proven_evidence_and_untrusted_audit(
    offline_workspace, monkeypatch, kind, mode
):
    root = offline_workspace
    diff, payload = _fixture(kind)
    original = copy.deepcopy(payload)
    monkeypatch.setattr(harness, "_healthy", lambda diff: copy.deepcopy(payload))
    machine, calls = harness._new_machine(root, monkeypatch, rounds=3, mode=mode, diff=diff)
    machine.test_config_root = outside_gate_root(root)
    assert machine.run() is Verdict.PASS
    assert payload == original
    assert not machine.l1_provider.attempted_excerpts
    state, disk = harness._state(root)
    assert state.converged
    audit = [finding for finding in state.findings if finding.source == "UNTRUSTED"]
    assert audit and all("declares" in finding.description for finding in audit)
    cycles = [1] if mode is Mode.CI else [1, 2, 3]
    harness._assert_calls(calls, cycles)
    assert state.consecutive_clean_rounds == (0 if mode is Mode.CI else 3)
    for cycle in cycles:
        for receipt in harness._cycle_receipts(root, cycle):
            assert receipt["pass_status"] == "completed"
            actual = receipt["code_excerpts"][0]
            for key, value in original["code_excerpts"][0].items():
                assert actual[key] == value
    assert disk["verdict"] == "PASS"


@pytest.mark.parametrize("fault", ["wrong-content", "missing-source", "mixed-invalid"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_real_l1_rejection_stays_incomplete_and_cannot_earn_credit(
    offline_workspace, monkeypatch, fault, mode
):
    root = offline_workspace
    diff, payload = _fixture()
    if fault == "wrong-content":
        payload["code_excerpts"][0]["content"] += " fabricated"
    elif fault == "missing-source":
        payload["code_excerpts"][0]["file"] = "absent.ts"
    else:
        bad = copy.deepcopy(payload["code_excerpts"][0])
        bad["content"] += " fabricated"
        payload["code_excerpts"].append(bad)
    monkeypatch.setattr(harness, "_healthy", lambda diff: copy.deepcopy(payload))
    machine, calls = harness._new_machine(root, monkeypatch, rounds=1, mode=mode, diff=diff)
    machine.test_config_root = outside_gate_root(root)
    assert machine.run() is not Verdict.PASS
    state, _ = harness._state(root)
    assert not state.converged and state.consecutive_clean_rounds == 0
    assert machine.l1_provider.attempted_excerpts
    for receipt in harness._cycle_receipts(root, 1):
        assert receipt["pass_status"] == "incomplete"
        assert receipt["code_excerpts"] == []
    harness._assert_calls(calls, [1])


@pytest.mark.parametrize("kind", ["short", "long"])
def test_multi_repository_provider_does_not_guess_a_source_scope(monkeypatch, kind):
    from pathlib import Path

    from code_forge.baseline import ResolvedReview
    from code_forge.factories import build_l1_provider
    from code_forge.llm_invoke import LLMResult

    diff, payload = _fixture(kind)
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke", lambda *a, **k: LLMResult(copy.deepcopy(payload))
    )
    provider = build_l1_provider(
        "real",
        ResolvedReview([Path("control.ts")], None, diff, "git"),
        reviewed_repositories={"repo": "a" * 40},
    )
    _, excerpts, _, _ = provider()
    assert not excerpts
    assert len(provider.attempted_excerpts) == 3
    assert all(row["code_excerpts"] == payload["code_excerpts"] for row in provider.attempted_excerpts)


@pytest.mark.parametrize("kind", ["short", "long"])
def test_live_files_cannot_supply_missing_frozen_source(tmp_path, monkeypatch, kind):
    diff, payload = _fixture(kind)
    (tmp_path / "control.ts").write_text("alpha = 1\nbeta = 2\ngamma = 3\n\n\n")
    monkeypatch.chdir(tmp_path)
    # The live file looks right, but the host patch names a different path.
    with pytest.raises(ExcerptEvidenceError):
        validate_reviewer_json(payload, frozen_diff=diff.replace("control.ts", "other.ts"))


def test_frozen_index_header_does_not_trigger_git_or_live_source_reads(offline_workspace, monkeypatch):
    from code_forge import git

    diff, payload = _fixture()
    diff = diff.replace("--- a/control.ts", "index aaaaaaa..bbbbbbb 100644\n--- a/control.ts")
    reads = []

    def forbidden_blob(*args):
        reads.append(args)
        raise AssertionError("new shape recovery cannot consult Git blobs")

    monkeypatch.setattr(git, "read_diff_blob", forbidden_blob)
    assert validate_reviewer_json(payload, frozen_diff=diff) == payload
    assert reads == []


@pytest.mark.parametrize("kind", ["short", "long"])
def test_grouped_recovery_cannot_borrow_a_sibling_frozen_diff(monkeypatch, kind):
    from pathlib import Path

    from code_forge.baseline import ResolvedReview
    from code_forge.factories import build_grouped_l1_provider
    from code_forge.llm_invoke import LLMResult

    diff, payload = _fixture(kind)
    monkeypatch.setattr(
        "code_forge.llm_invoke.llm_invoke", lambda *a, **k: LLMResult(copy.deepcopy(payload))
    )
    provider = build_grouped_l1_provider(
        "real",
        [
            {"name": "owner", "resolved": ResolvedReview([Path("control.ts")], None, diff, "git")},
            {
                "name": "sibling",
                "resolved": ResolvedReview(
                    [Path("other.ts")], None, diff.replace("control.ts", "other.ts"), "git"
                ),
            },
        ],
    )
    _, excerpts, _, _ = provider()
    assert len(excerpts) == 3  # Only the owner's three passes have matching source.
    assert len(provider.attempted_excerpts) == 3
    assert all(attempt.group_scope.name == "sibling" for attempt in provider.attempted_excerpts)
    assert provider.reviewer_responses is None  # Grouped C3 credit remains unavailable.
