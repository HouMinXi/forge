"""Rejected evidence must not borrow another pass's valid excerpts."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_grouped_l1_provider, build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.manifest import EnvManifest, ManifestTier
from code_forge.machine import StateMachine
from code_forge.receipt import write_receipts
from code_forge.source import compute_source_hash
from code_forge.state import Disposition, Mode, StateFinding, Verdict
from code_forge.verify import parse_diff_files, run_verify

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
        payload = {"code_excerpts": []} if current_pass == rejected_pass else valid
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
        assert machine._state.rounds_with_failed_pass == 1
        assert list((directory / "receipts" / "attempted").glob("*.json"))


def _initial_review(
    tmp_path, monkeypatch, mode, grouped, rejected_pass, as_text=False, with_finding=False
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "control.ts").write_text(CONTENT)
    (tmp_path / "peer.ts").write_text(CONTENT)
    directory = tmp_path / ".code-forge"
    directory.mkdir()
    (directory / "gate.yaml").write_text('test:\n  command: ["true"]\n')
    peer_diff = DIFF.replace("control.ts", "peer.ts")
    diff = DIFF + peer_diff if grouped else DIFF
    files = [Path("control.ts"), Path("peer.ts")] if grouped else [Path("control.ts")]
    resolved = ResolvedReview(files, None, diff, "git")
    calls = []
    forbidden = Mock(side_effect=AssertionError("offline evidence checks must not spawn a workload"))
    roles = {
        "structural code reviewer:": "qodo",
        "senior engineer:": "expert",
        "adversarial QE:": "adversarial",
    }
    original = {
        "findings": [],
        "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 5, "content": CONTENT}],
        "pass_name": "model-supplied-label",
    }
    if with_finding:
        original["findings"] = [
            {
                "file": "control.ts",
                "line": 1,
                "severity": "P1",
                "description": "Potential null dereference must survive rejected evidence",
            }
        ]

    def transport(prompt, *args, **kwargs):
        assert not prompt.startswith("The previous JSON"), "this is an initial-response test"
        pass_name = next(name for role, name in roles.items() if "You are a " + role in prompt)
        filename = "peer.ts" if "diff --git a/peer.ts" in prompt else "control.ts"
        calls.append((filename, pass_name))
        payload = {
            "findings": [],
            "code_excerpts": [{"file": filename, "start_line": 1, "end_line": 1, "content": CONTENT}],
        }
        if filename == "control.ts" and pass_name == rejected_pass:
            payload = copy.deepcopy(original)
        return LLMResult(json.dumps(payload) if as_text else payload, Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke._invoke_api", transport)
    monkeypatch.setattr(
        "code_forge.manifest.extract_manifest", lambda root: EnvManifest(ManifestTier.ABSENT)
    )
    monkeypatch.setattr("code_forge.machine.resolve_ledger_root", lambda root: root)
    monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", lambda root: root)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    options = {
        "backend": BackendConfig(name="offline", type="api", model="offline"),
        "max_attempts": 1,
        "pass_stagger_s": 0,
    }
    if grouped:
        provider = build_grouped_l1_provider(
            "real",
            [
                {
                    "name": "control-group",
                    "resolved": ResolvedReview([Path("control.ts")], None, DIFF, "git"),
                },
                {
                    "name": "healthy-peer",
                    "resolved": ResolvedReview([Path("peer.ts")], None, peer_diff, "git"),
                },
            ],
            **options,
        )
    else:
        provider = build_l1_provider("real", resolved, **options)
    source_hash = compute_source_hash(git_diff=diff)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=source_hash,
        baseline_spec_repr="initial-rejection",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider,
        l2_runner=lambda *args, **kwargs: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    result = machine.run()
    forbidden.assert_not_called()
    cycles = [1] if mode == Mode.CI or rejected_pass is not None else [1, 2, 3]
    expected_calls = len(cycles) * (6 if grouped else 3)
    assert len(calls) == expected_calls
    receipts = [
        json.loads(path.read_text()) for path in sorted((directory / "receipts").glob("receipt-*.json"))
    ]
    verdicts = [
        run_verify(
            tmp_path,
            source_hash,
            parse_diff_files(diff),
            diff_text=diff,
            required_cycles=len(cycles) if convergence else 1,
            cycles=cycles if convergence else cycles[-1:],
            respect_floor=False,
            require_convergence=convergence,
        )
        for convergence in (False, True)
    ]
    (tmp_path / "observed-initial-review.json").write_text(
        json.dumps(
            {
                "machine_verdict": result.value,
                "calls": calls,
                "forbidden_processes": forbidden.call_count,
                "receipt_statuses": [receipt["pass_status"] for receipt in receipts],
                "verifier": [verdict.__dict__ for verdict in verdicts],
            },
            indent=2,
        )
    )
    return result, machine, provider, original, receipts, verdicts


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
@pytest.mark.parametrize("rejected_pass,pass_number", [("qodo", 1), ("expert", 2), ("adversarial", 3)])
def test_initial_excerpt_shape_rejection_cannot_borrow_completion(
    tmp_path, monkeypatch, mode, grouped, as_text, rejected_pass, pass_number
):
    result, machine, provider, original, receipts, verdicts = _initial_review(
        tmp_path, monkeypatch, mode, grouped, rejected_pass, as_text
    )
    assert receipts[pass_number - 1]["pass_status"] == "incomplete"
    assert all(r["pass_status"] == "completed" for r in receipts if r["pass"] != pass_number)
    assert result == Verdict.FAIL
    assert machine._state.consecutive_clean_rounds == 0
    assert all(not verdict.passed and "pass did not complete" in verdict.reason for verdict in verdicts)
    assert not any(f.file == "<schema-validation>" for f in machine._state.findings)
    attempts = list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))
    assert len(attempts) == 1
    artifact = json.loads(attempts[0].read_text())
    assert artifact["pass_name"] == rejected_pass
    assert artifact["payload"] == original | {"pass_name": rejected_pass}
    if grouped:
        assert artifact["group_scope"]["name"] == "control-group"
        assert receipts[pass_number - 1]["code_excerpts"][0]["file"] == "peer.ts"
    else:
        assert receipts[pass_number - 1]["code_excerpts"] == []
    assert len(provider.attempted_excerpts) == 1


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("grouped", [False, True])
def test_initial_healthy_review_still_attests(tmp_path, monkeypatch, mode, grouped):
    result, _, provider, _, receipts, verdicts = _initial_review(
        tmp_path, monkeypatch, mode, grouped, None
    )
    assert result == Verdict.PASS
    assert all(receipt["pass_status"] == "completed" for receipt in receipts)
    assert all(verdict.passed for verdict in verdicts)
    assert provider.attempted_excerpts == []
    assert not provider.unavailable_rejected_passes


@pytest.mark.parametrize("grouped", [False, True])
def test_initial_rejection_keeps_candidate_untrusted_and_resets(tmp_path, monkeypatch, grouped):
    _, machine, provider, _, _, _ = _initial_review(
        tmp_path, monkeypatch, Mode.CI, grouped, "expert", with_finding=True
    )
    candidates = [
        finding
        for finding in machine._state.findings
        if "Potential null dereference" in finding.description
    ]
    assert len(candidates) == 1
    assert candidates[0].source == "UNTRUSTED"
    assert candidates[0].disposition == Disposition.UNCERTAIN
    assert not any(
        "schema-fail" in finding.id or "schema-fail" in finding.fingerprint
        for finding in machine._state.findings
    )

    def healthy(prompt, *args, **kwargs):
        filename = "peer.ts" if "diff --git a/peer.ts" in prompt else "control.ts"
        return LLMResult(
            {
                "findings": [],
                "code_excerpts": [
                    {"file": filename, "start_line": 1, "end_line": 1, "content": CONTENT}
                ],
            }
        )

    monkeypatch.setattr("code_forge.llm_invoke._invoke_api", healthy)
    findings, excerpts, _, _ = provider()
    assert findings == []
    assert len(excerpts) == (6 if grouped else 3)
    assert provider.attempted_excerpts == provider.raw_observations == []
    assert not provider.unavailable_rejected_passes


@pytest.mark.parametrize("engine,diff", [("real", ""), ("real", DIFF), ("stub", DIFF)])
def test_initial_missing_exemption_and_trusted_stub(monkeypatch, engine, diff):
    if engine == "real" and diff:
        diff = "diff --git a/control.ts b/control.ts\nold mode 100644\nnew mode 100755\n"
    calls = []

    def empty(prompt, *args, **kwargs):
        calls.append(prompt)
        return LLMResult({"findings": [], "code_excerpts": []})

    monkeypatch.setattr("code_forge.llm_invoke._invoke_api", empty)
    provider = build_l1_provider(
        engine,
        ResolvedReview([Path("control.ts")], None, diff, "git"),
        backend=BackendConfig(name="offline", type="api", model="offline"),
        max_attempts=1,
        pass_stagger_s=0,
    )
    findings, excerpts, _, _ = provider()
    assert findings == excerpts == []
    assert provider.attempted_excerpts == provider.raw_observations == []
    assert not provider.unavailable_rejected_passes
    assert len(calls) == (3 if engine == "real" and diff else 0)
    assert provider.is_stub_l1 == (engine == "stub")
