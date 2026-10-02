"""Unverified product candidates and receipt metadata have separate boundaries."""

import copy
import json
import subprocess
from pathlib import Path

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import GitRefBaseline, ResolvedReview, resolve_baseline
from code_forge.factories import build_l1_provider
from code_forge.falsify import StubFalsifier
from code_forge.hold import _prompt_one, run_hold_ui
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.receipt import write_receipts
from code_forge.reviewer_json import _json_to_state_findings
from code_forge.source import compute_source_hash
from code_forge.state import (
    Disposition,
    Mode,
    State,
    StateFinding,
    Verdict,
    is_receipt_audit,
    load_state,
    save_state,
)
from code_forge.verify import parse_diff_files, run_verify


CONTENT = "const context = 1;\nconst value = 2;\nconst end = 3;\n"
DIFF = (
    "diff --git a/control.ts b/control.ts\n"
    "--- a/control.ts\n+++ b/control.ts\n"
    "@@ -1,2 +1,3 @@\n const context = 1;\n"
    "+const value = 2;\n const end = 3;\n"
)


def _finding(audit=False, disposition=Disposition.UNCERTAIN):
    return StateFinding(
        id="RECEIPT_UNTRUSTED" if audit else "l1-expert-untrusted-product",
        fingerprint="receipt-metadata" if audit else "product",
        source="UNTRUSTED",
        disposition=disposition,
        file="control.ts",
        line_range=[2, 2],
        description="excerpt metadata" if audit else "Product candidate without usable evidence",
    )


def _machine(tmp_path, mode=Mode.LOCAL, provider=None):
    (tmp_path / "control.ts").write_text(CONTENT, encoding="utf-8")
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    return StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=resolved,
        source_hash=compute_source_hash(git_diff=DIFF),
        baseline_spec_repr="uncertainty fixture",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *args: ([], []),
        l1_provider=provider or (lambda: ([], [], Usage(), 0.0)),
        l2_runner=lambda *args, **kwargs: ([], []),
        max_total_rounds=3,
    )


def _complete_excerpts():
    return [
        {
            "file": "control.ts",
            "start_line": 1,
            "end_line": 3,
            "content": CONTENT,
            "pass_name": name,
        }
        for name in ("qodo", "expert", "adversarial")
    ]


@pytest.mark.parametrize("with_audit", [False, True])
@pytest.mark.parametrize("source", ["UNTRUSTED", "L0"])
def test_prior_pass_is_invalidated_by_unresolved_review(tmp_path, monkeypatch, with_audit, source):
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)
    state_path = tmp_path / ".code-forge/state.json"
    excerpts = _complete_excerpts()
    first = _machine(tmp_path, provider=lambda: ([], excerpts, Usage(), 0.0))
    assert first.run() is Verdict.PASS
    assert load_state(state_path).converged is True

    def make_review():
        product = _finding()
        product.source = source
        findings = [product] if source == "UNTRUSTED" else []
        if with_audit:
            findings.append(_finding(audit=True))
        machine = _machine(tmp_path, provider=lambda: (findings, excerpts, Usage(), 0.0))
        if source == "L0":
            machine.l0_runner = lambda *args: ([product], [])
        snapshots = []
        persist = machine._persist_state

        def record_state():
            persist()
            snapshots.append(load_state(state_path))

        monkeypatch.setattr(machine, "_persist_state", record_state)
        return machine, snapshots

    for _ in range(2):
        machine, snapshots = make_review()
        assert machine.run() is Verdict.PENDING
        final = load_state(state_path)
        assert final.converged is False
        assert final.consecutive_clean_rounds == 0
        assert final.hold_reason == "1 UNCERTAIN finding(s) awaiting human disposition"
        assert snapshots and all(state.converged is False for state in snapshots)
        assert (
            next(f for f in final.findings if f.fingerprint == "product").disposition
            is Disposition.UNCERTAIN
        )
    if source == "UNTRUSTED":
        verified = run_verify(
            tmp_path,
            machine.source_hash,
            parse_diff_files(DIFF),
            diff_text=DIFF,
            required_cycles=1,
            cycles=machine._written_cycles,
            respect_floor=False,
        )
        assert verified.passed is False
        assert "convergence not established" in verified.reason
    run_hold_ui(final, state_path, input_fn=lambda prompt: "d", output_fn=lambda message: None)
    machine, snapshots = make_review()
    assert machine.run() is Verdict.PASS
    restored = load_state(state_path)
    assert restored.converged is True
    assert restored.consecutive_clean_rounds == 3
    assert [state.converged for state in snapshots[:-1]] == [False] * (len(snapshots) - 1)
    assert snapshots[-1].converged is True
    assert all(row["fixpoint"] == "CLEAN" for row in restored.round_history[-3:])


def test_clean_review_resume_sets_convergence_only_at_terminal(tmp_path, monkeypatch):
    state_path = tmp_path / ".code-forge/state.json"
    excerpts = _complete_excerpts()
    first = _machine(tmp_path, provider=lambda: ([], excerpts, Usage(), 0.0))
    assert first.run() is Verdict.PASS
    resumed = _machine(tmp_path, provider=lambda: ([], excerpts, Usage(), 0.0))
    snapshots = []
    persist = resumed._persist_state

    def record_state():
        persist()
        snapshots.append(load_state(state_path).converged)

    monkeypatch.setattr(resumed, "_persist_state", record_state)
    assert resumed.run() is Verdict.PASS
    assert snapshots[:-1] == [False] * (len(snapshots) - 1)
    assert snapshots[-1] is True
    assert load_state(state_path).consecutive_clean_rounds == 4


@pytest.mark.parametrize("source", ["UNTRUSTED", "L0"])
def test_ci_after_local_pass_keeps_explicit_uncertainty_policy(tmp_path, source):
    state_path = tmp_path / ".code-forge/state.json"
    excerpts = _complete_excerpts()
    first = _machine(tmp_path, provider=lambda: ([], excerpts, Usage(), 0.0))
    assert first.run() is Verdict.PASS
    product = _finding()
    product.source = source

    def provider():
        return [product] if source == "UNTRUSTED" else [], excerpts, Usage(), 0.0

    machine = _machine(tmp_path, mode=Mode.CI, provider=provider)
    if source == "L0":
        machine.l0_runner = lambda *args: ([product], [])
    assert machine.run() is Verdict.PASS
    final = load_state(state_path)
    assert final.converged is (source == "L0")
    assert final.hold_reason is None
    assert final.consecutive_clean_rounds == 0
    assert len(final.round_history) == 1


@pytest.mark.parametrize("with_audit", [False, True])
def test_untrusted_product_resets_fixpoint_and_requires_hold(tmp_path, with_audit):
    machine = _machine(tmp_path)
    product = _finding()
    machine._state.findings = [product] + ([_finding(audit=True)] if with_audit else [])
    assert machine.active_findings == [product]
    assert machine._fixpoint_reached().name == "RESET"
    assert machine._should_enter_hold() is True
    machine.mode = Mode.CI
    assert machine._should_enter_hold() is False


def test_metadata_only_is_clean_without_hold(tmp_path):
    machine = _machine(tmp_path)
    audit = _finding(audit=True)
    machine._state.findings = [audit]
    assert machine.active_findings == []
    assert machine.receipt_audit == [audit]
    assert machine._fixpoint_reached().name == "CLEAN"
    assert machine._should_enter_hold() is False


@pytest.mark.parametrize("choice", ["c", "d"])
def test_restored_hold_only_dispositions_the_product(tmp_path, monkeypatch, choice):
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)
    audit, product = _finding(audit=True), _finding()
    path = tmp_path / "state.json"
    save_state(State(findings=[audit, product], hold_reason="pending"), path)
    restored = load_state(path)
    original_audit = copy.deepcopy(restored.findings[0])
    prompts, output = [], []

    def input_fn(prompt):
        prompts.append(prompt)
        return choice

    run_hold_ui(restored, path, input_fn=input_fn, output_fn=output.append)
    again = load_state(path)
    assert len(prompts) == 1
    assert again.findings[0] == original_audit
    assert is_receipt_audit(again.findings[0])
    assert again.findings[1].disposition is (
        Disposition.CONFIRMED if choice == "c" else Disposition.DISMISSED
    )
    assert output[0] == "HOLD: 1 UNCERTAIN finding(s) need human disposition."
    assert again.hold_reason is None


def test_noninteractive_hold_reports_products_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_HOLD_NONINTERACTIVE", "1")
    state = State(findings=[_finding(audit=True), _finding()])
    output = []
    run_hold_ui(state, tmp_path / "state.json", input_fn=None, output_fn=output.append)
    assert output == ["HOLD: 1 UNCERTAIN finding(s) left recorded; noninteractive, not prompting."]
    assert all(f.disposition is Disposition.UNCERTAIN for f in state.findings)


def test_direct_prompt_cannot_promote_receipt_audit():
    audit = _finding(audit=True)
    snapshot = copy.deepcopy(audit)
    calls = []
    _prompt_one(audit, lambda prompt: calls.append(prompt) or "c", calls.append)
    assert audit == snapshot
    assert calls == []
    assert is_receipt_audit(audit)


def test_direct_prompt_can_confirm_untrusted_product():
    product = _finding()
    _prompt_one(product, lambda prompt: "c", lambda message: None)
    assert product.disposition is Disposition.CONFIRMED
    assert not is_receipt_audit(product)


@pytest.mark.parametrize("with_history", [False, True])
def test_persisted_hold_dismissal_survives_real_redetection(tmp_path, monkeypatch, with_history):
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)
    product, audit = _finding(), _finding(audit=True)
    path = tmp_path / ".code-forge/state.json"
    first = _machine(tmp_path)
    state = State(source_hash=first.source_hash, findings=[audit, product])
    if with_history:
        state.round_history = [{"dispositions": {product.fingerprint: "UNCERTAIN"}}]
    save_state(state, path)
    restored = load_state(path)
    run_hold_ui(restored, path, input_fn=lambda prompt: "d", output_fn=lambda message: None)
    history = copy.deepcopy(restored.round_history)
    excerpts = [
        {
            "file": "control.ts",
            "start_line": 1,
            "end_line": 3,
            "content": CONTENT,
            "pass_name": "qodo",
        }
    ]
    machine = _machine(tmp_path, provider=lambda: ([_finding()], excerpts, Usage(), 0.0))
    assert machine.run() is Verdict.PASS
    final = load_state(path)
    assert final.converged is True
    assert final.consecutive_clean_rounds == 3
    assert (
        next(f for f in final.findings if f.fingerprint == product.fingerprint).disposition
        is Disposition.DISMISSED
    )
    assert final.round_history[: len(history)] == history


@pytest.mark.parametrize("disposition", list(Disposition))
def test_receipts_preserve_untrusted_product_with_unverified_basis(tmp_path, disposition):
    product, audit = _finding(disposition=disposition), _finding(audit=True)
    snapshot = copy.deepcopy([audit, product])
    paths = write_receipts(tmp_path / "receipts", 0, [audit, product], "hash", [], tmp_path)
    expert = json.loads(paths[1].read_text(encoding="utf-8"))
    assert expert["findings_count"] == 1
    assert expert["findings"][0]["description"] == product.description
    assert expert["findings"][0]["disposition"] == disposition.value
    assert expert["findings"][0]["basis"]["authority"] == "infra-unavailable"
    assert expert["findings"][0]["basis"]["falsification_survived"] is False
    assert [audit, product] == snapshot


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
def test_real_provider_product_and_metadata_have_distinct_terminal_state(tmp_path, monkeypatch, mode):
    """The actual producer/receipt/state paths run with an explicit stub transport."""
    resolved = ResolvedReview([Path("control.ts")], None, DIFF, "git")
    payload = {
        "findings": [],
        "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 3, "content": CONTENT}],
    }
    calls = []

    def transport(prompt, **kwargs):
        role = prompt.rsplit("You are a ", 1)[-1]
        calls.append(role)
        result = copy.deepcopy(payload)
        if role.startswith("senior engineer"):
            result["code_excerpts"][0]["content"] = "const context = 1;"
            result["findings"] = [
                {
                    "file": "control.ts",
                    "line": 2,
                    "severity": "P1",
                    "description": "Product candidate without usable evidence",
                }
            ]
        elif role.startswith("structural code reviewer"):
            result["code_excerpts"][0]["content"] = "\n".join(CONTENT.splitlines()[:2])
        return LLMResult(result, Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    provider = build_l1_provider("auto", resolved, backend=None, max_attempts=1)
    machine = _machine(tmp_path, mode=mode, provider=provider)
    returned = machine.run()
    persisted = load_state(tmp_path / ".code-forge/state.json")
    assert len(calls) == 3
    assert returned is (Verdict.PENDING if mode is Mode.LOCAL else Verdict.PASS)
    assert persisted.verdict is returned
    assert persisted.converged is False
    assert persisted.consecutive_clean_rounds == 0
    products = [
        f
        for f in persisted.findings
        if not is_receipt_audit(f) and f.disposition is not Disposition.DISMISSED
    ]
    audit = [f for f in persisted.findings if is_receipt_audit(f)]
    assert len(products) == 1
    assert products[0].source == "UNTRUSTED"
    assert products[0].disposition is Disposition.UNCERTAIN
    assert len(audit) == 1
    assert machine.active_findings == products
    if mode is Mode.LOCAL:
        assert persisted.hold_reason == "1 UNCERTAIN finding(s) awaiting human disposition"
        assert persisted.round_history[-1]["fixpoint"] == "RESET"
    else:
        assert persisted.hold_reason is None
        assert all(h.get("fixpoint") != "CLEAN" for h in persisted.round_history)
    receipts = sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    assert len(receipts) == 3
    expert = json.loads(receipts[1].read_text(encoding="utf-8"))
    assert expert["findings_count"] == 1
    assert expert["findings"][0]["basis"]["falsification_survived"] is False


def _salvaged_duplicate_machine(root, monkeypatch, *, distinct=False):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=10
        ).stdout

    git("init", "-q")
    (root / "control.ts").write_text("placeholder\n", encoding="utf-8")
    git("add", "control.ts")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "fixture",
    )
    lines = ["const value%d = %d;" % (i, i) for i in range(1, 31)]
    (root / "control.ts").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git("add", "control.ts")
    resolved = resolve_baseline(
        GitRefBaseline("HEAD"), GitRefBaseline("INDEX"), [Path("control.ts")], root
    )
    raw = {"file": "control.ts", "line": 2, "severity": "P1", "description": "Same product candidate"}
    round_number = -1
    windows = [[(1, 20)], [(11, 30)], [(1, 10), (21, 30)]]

    def transport(prompt, **kwargs):
        nonlocal round_number
        role = prompt.rsplit("You are a ", 1)[-1]
        if role.startswith("structural code reviewer"):
            round_number += 1
        payload = {
            "findings": [],
            "code_excerpts": [
                {
                    "file": "control.ts",
                    "start_line": start,
                    "end_line": end,
                    "content": "\n".join(lines[start - 1 : end]),
                }
                for start, end in windows[round_number]
            ],
        }
        if role.startswith("senior engineer"):
            payload["findings"] = [copy.deepcopy(raw), copy.deepcopy(raw)]
            if distinct:
                payload["findings"].append(
                    {
                        "file": "control.ts",
                        "line": 12,
                        "severity": "P1",
                        "description": "Distinct product candidate",
                    }
                )
            payload["code_excerpts"] = [
                {"file": "control.ts", "start_line": 1, "end_line": 3, "content": lines[0]}
            ]
        return LLMResult(payload, Usage(), 0.0)

    monkeypatch.setattr("code_forge.llm_invoke.llm_invoke", transport)
    monkeypatch.delenv("FORGE_HOLD_NONINTERACTIVE", raising=False)
    source_hash = compute_source_hash(git_diff=resolved.git_diff)

    def make_machine():
        return StateMachine(
            mode=Mode.LOCAL,
            falsifier=StubFalsifier(),
            autofixer=StubAutoFixer(),
            revert_fn=lambda finding: None,
            resolved_review=resolved,
            source_hash=source_hash,
            baseline_spec_repr="owned scratch INDEX",
            cwd=root,
            registry={},
            l0_runner=lambda *args: ([], []),
            l1_provider=build_l1_provider("auto", resolved, backend=None, max_attempts=1),
            l2_runner=lambda *args, **kwargs: ([], []),
            max_total_rounds=3,
        )

    return make_machine, _json_to_state_findings({"findings": [raw]}, "expert")[0], resolved


@pytest.mark.parametrize("with_history", [False, True])
@pytest.mark.parametrize("disposition", [Disposition.DISMISSED, Disposition.STYLE])
def test_duplicate_salvage_preserves_terminal_disposition_in_all_receipts(
    tmp_path, monkeypatch, with_history, disposition
):
    make_machine, prior, resolved = _salvaged_duplicate_machine(tmp_path, monkeypatch)
    state_path = tmp_path / ".code-forge/state.json"
    source_hash = compute_source_hash(git_diff=resolved.git_diff)
    prior.disposition = Disposition.UNCERTAIN if disposition is Disposition.DISMISSED else disposition
    history = [{"dispositions": {prior.fingerprint: "UNCERTAIN"}}] if with_history else []
    save_state(State(source_hash=source_hash, findings=[prior], round_history=history), state_path)
    if disposition is Disposition.DISMISSED:
        restored = load_state(state_path)
        run_hold_ui(restored, state_path, input_fn=lambda prompt: "d", output_fn=lambda message: None)

    machine = make_machine()
    assert machine.run() is Verdict.PASS
    final = load_state(state_path)
    assert final.converged is True
    assert final.consecutive_clean_rounds == 3
    assert final.round_history[: len(history)] == history
    products = [
        finding
        for finding in final.findings
        if finding.source == "UNTRUSTED" and not is_receipt_audit(finding)
    ]
    assert len(products) == 1
    assert products[0].disposition is disposition
    assert all(
        finding.disposition is Disposition.DISMISSED
        for finding in final.findings
        if finding.id == "FIXVAL_SKIPPED"
    )
    for cycle in range(1, 4):
        receipt = json.loads(
            (tmp_path / ".code-forge/receipts" / f"receipt-c{cycle}p2.json").read_text()
        )
        assert receipt["findings_count"] == 2
        assert [finding["disposition"] for finding in receipt["findings"]] == [disposition.value] * 2
        assert all(
            finding["basis"]["authority"] == "infra-unavailable" for finding in receipt["findings"]
        )
    verification = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(resolved.git_diff),
        diff_text=resolved.git_diff,
        required_cycles=3,
    )
    assert verification.passed is True


def test_duplicate_dismissal_keeps_distinct_unverified_candidate_open(tmp_path, monkeypatch):
    make_machine, prior, resolved = _salvaged_duplicate_machine(tmp_path, monkeypatch, distinct=True)
    prior.disposition = Disposition.DISMISSED
    source_hash = compute_source_hash(git_diff=resolved.git_diff)
    state_path = tmp_path / ".code-forge/state.json"
    save_state(State(source_hash=source_hash, findings=[prior]), state_path)
    machine = make_machine()
    assert machine.run() is Verdict.PENDING
    final = load_state(state_path)
    assert final.converged is False
    assert final.consecutive_clean_rounds == 0
    assert len(final.findings) == 2
    assert [f.disposition for f in final.findings] == [Disposition.DISMISSED, Disposition.UNCERTAIN]
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert receipt["findings_count"] == 3
    assert [f["disposition"] for f in receipt["findings"]] == ["DISMISSED", "DISMISSED", "UNCERTAIN"]
    verification = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(resolved.git_diff),
        diff_text=resolved.git_diff,
        required_cycles=1,
        respect_floor=False,
    )
    assert verification.passed is False
    assert (
        verification.reason
        == "unresolved unverified product finding c1p2 -- convergence not established"
    )


def test_unresolved_duplicates_remain_visible_and_uncertain(tmp_path, monkeypatch):
    make_machine, _, resolved = _salvaged_duplicate_machine(tmp_path, monkeypatch)
    machine = make_machine()
    assert machine.run() is Verdict.PENDING
    assert len(machine.active_findings) == 1
    assert machine.active_findings[0].disposition is Disposition.UNCERTAIN
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert receipt["findings_count"] == 2
    assert [f["disposition"] for f in receipt["findings"]] == ["UNCERTAIN", "UNCERTAIN"]


def test_two_distinct_unverified_candidates_keep_separate_hold_decisions(tmp_path, monkeypatch):
    make_machine, _, _ = _salvaged_duplicate_machine(tmp_path, monkeypatch, distinct=True)
    machine = make_machine()
    assert machine.run() is Verdict.PENDING
    assert len(machine.active_findings) == 2
    state_path = tmp_path / ".code-forge/state.json"
    state = load_state(state_path)
    prompts, output = [], []
    choices = iter(["d", "s"])

    def input_fn(prompt):
        prompts.append(prompt)
        return next(choices)

    run_hold_ui(state, state_path, input_fn=input_fn, output_fn=output.append)
    assert len(prompts) == 2
    assert output[0] == "HOLD: 2 UNCERTAIN finding(s) need human disposition."
    restored = load_state(state_path)
    assert [f.disposition for f in restored.findings] == [Disposition.DISMISSED, Disposition.UNCERTAIN]
    resumed = make_machine()
    assert resumed.run() is Verdict.PENDING
    assert load_state(state_path).converged is False
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c2p2.json").read_text())
    assert [f["disposition"] for f in receipt["findings"]] == ["DISMISSED", "DISMISSED", "UNCERTAIN"]


@pytest.mark.parametrize("inherited_policy", ["signing", "hooks", "both"])
def test_duplicate_git_fixture_ignores_inherited_commit_policy(tmp_path, monkeypatch, inherited_policy):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    signer = tmp_path / "signer"
    hook = hooks / "pre-commit"
    for executable in (signer, hook):
        executable.write_text(
            '#!/bin/sh\nprintf "%s\\n" invoked >> "$0.invoked"\nexit 23\n',
            encoding="utf-8",
        )
        executable.chmod(0o700)
    values = {"gpg.program": str(signer), "user.signingkey": "fixture"}
    if inherited_policy in ("signing", "both"):
        values["commit.gpgsign"] = "true"
    if inherited_policy in ("hooks", "both"):
        values["core.hooksPath"] = str(hooks)
    for key, value in values.items():
        subprocess.run(
            ["git", "config", "--file", str(config), key, value],
            check=True,
            capture_output=True,
            timeout=10,
        )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    control = tmp_path / "control"
    control.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=control, check=True, capture_output=True, timeout=10)
    rejected = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "control",
        ],
        cwd=control,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert rejected.returncode != 0
    marker = Path(str(hook if inherited_policy in ("hooks", "both") else signer) + ".invoked")
    assert marker.read_text(encoding="utf-8") == "invoked\n"
    markers = {
        executable: Path(str(executable) + ".invoked").read_bytes()
        if Path(str(executable) + ".invoked").exists()
        else None
        for executable in (signer, hook)
    }
    subject = tmp_path / "subject"
    subject.mkdir()
    _, _, resolved = _salvaged_duplicate_machine(subject, monkeypatch)
    assert "+const value2 = 2;" in resolved.git_diff
    assert (
        subprocess.check_output(["git", "log", "-1", "--format=%G?"], cwd=subject, timeout=10).strip()
        == b"N"
    )
    for executable, before in markers.items():
        path = Path(str(executable) + ".invoked")
        assert (path.read_bytes() if path.exists() else None) == before
    for key in ("commit.gpgsign", "core.hooksPath"):
        local = subprocess.run(
            ["git", "config", "--local", "--get", key], cwd=subject, capture_output=True, timeout=10
        )
        assert local.returncode == 1


def test_raw_duplicate_receipts_follow_canonical_l0_precedence(tmp_path):
    first, second = _finding(), _finding(disposition=Disposition.DISMISSED)
    second.description = "Different wording at the same location"
    canonical = _finding(disposition=Disposition.CONFIRMED)
    canonical.source = "L0"
    machine = _machine(tmp_path, mode=Mode.CI, provider=lambda: ([first, second], [], Usage(), 0.0))
    machine.l0_runner = lambda *args: ([canonical], [])
    machine._execute_round(0)
    assert machine._state.findings == [canonical]
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert [f["disposition"] for f in receipt["findings"]] == ["CONFIRMED", "CONFIRMED"]
    assert all(f["basis"]["authority"] == "infra-unavailable" for f in receipt["findings"])
    assert [f["description"] for f in receipt["findings"]] == [first.description, second.description]
