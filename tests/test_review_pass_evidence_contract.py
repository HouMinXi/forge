"""Required pass evidence and rejected grouped payloads must survive real gates."""

import copy
import hashlib
import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from code_forge.autofix import StubAutoFixer
from code_forge.baseline import ResolvedReview
from code_forge.factories import _requires_l1_excerpts, build_grouped_l1_provider, build_l1_provider
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine
from code_forge.outlet_c import run_outlet_c
from code_forge.receipt import write_receipts
from code_forge.reviewer_json import validate_reviewer_json
from code_forge.source import compute_source_hash
from code_forge.state import Mode, Verdict
from code_forge.verify import parse_diff_files, run_verify

CONTENT = "const value = 2;\n"


def diff_for(file):
    return (
        f"diff --git a/{file} b/{file}\n--- a/{file}\n+++ b/{file}\n"
        "@@ -1 +1 @@\n-const value = 1;\n+const value = 2;\n"
    )


def good(file):
    return {
        "findings": [],
        "code_excerpts": [{"file": file, "start_line": 1, "end_line": 1, "content": CONTENT}],
    }


@pytest.fixture(autouse=True)
def isolated_execution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("Real process forbidden"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("Network forbidden"))
    monkeypatch.setattr("code_forge.git.read_diff_blob", lambda *a, **kw: None)
    monkeypatch.setattr("code_forge.mutation.run_mutation", lambda *a, **kw: ([], []))
    monkeypatch.setattr(
        "code_forge.mutation_dispatch.other_adapter_note", lambda *a, **kw: "controlled L2 note"
    )
    monkeypatch.setattr("code_forge.ledger.resolve_ledger_root", Path)
    monkeypatch.setattr("code_forge.machine.resolve_ledger_root", Path)
    directory = tmp_path / ".code-forge"
    directory.mkdir()
    (directory / "gate.yaml").write_text('test:\n  command: ["controlled-never-run"]\n')


def machine_for(tmp_path, mode, provider, diff, files):
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    machine = StateMachine(
        mode=mode,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview([Path(f) for f in files], None, diff, "git"),
        source_hash=compute_source_hash(git_diff=diff),
        baseline_spec_repr="review-pass-evidence-control",
        cwd=tmp_path,
        registry={},
        l0_runner=lambda *a: ([], []),
        l1_provider=provider,
        l2_runner=lambda *a, **kw: ([], []),
        e2e_runner=lambda *a: ([], []),
        max_total_rounds=3,
        clean_round_threshold=3,
    )
    machine._state.env_manifest = {"tier": "declared"}
    return machine


def assert_required_pass_terminal_refusal(machine):
    assert machine._receipt_gate_round_errors()
    errors = machine._receipt_gate_terminal_errors()
    if machine.mode is Mode.LOCAL:
        assert machine.clean_round_threshold == 3
        assert machine._state.earned_clean_window["cycles"] == []
        assert machine._state.consecutive_clean_rounds == 0
        assert machine._state.converged is False
        assert errors == [
            "receipt acceptance: earned window has 0 cycles; verifier floor demands 3"
        ]
    else:
        assert "status=incomplete" in ";".join(errors)


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("missing_pass", [1, 2, 3])
def test_missing_required_pass_refuses_full_machine(tmp_path, mode, missing_pass):
    diff = diff_for("control.ts")
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    calls = []

    def transport(*args, **kwargs):
        number = len(calls) % 3 + 1
        payload = {"findings": [], "code_excerpts": []} if number == missing_pass else good("control.ts")
        calls.append(copy.deepcopy(payload))
        return LLMResult(copy.deepcopy(payload), Usage(), 0.0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, mode, provider, diff, ["control.ts"])
        result = machine.run()
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    receipt = json.loads((tmp_path / f".code-forge/receipts/receipt-c1p{missing_pass}.json").read_text())
    assert result == Verdict.FAIL
    assert disk["verdict"] == "FAIL" and disk["converged"] is False
    assert receipt["pass_status"] == "incomplete" and receipt["code_excerpts"] == []
    assert machine._receipt_gate_round_errors()
    assert_required_pass_terminal_refusal(machine)
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not verified.passed and "status=incomplete" in verified.reason
    assert not any(f.id.startswith("l1-") for f in machine._state.findings)
    assert len(list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_complete_passes_preserve_full_machine_positive(tmp_path, mode):
    diff = diff_for("control.ts")
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    with patch(
        "code_forge.llm_invoke.llm_invoke", return_value=LLMResult(good("control.ts"), Usage(), 0.0)
    ):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, mode, provider, diff, ["control.ts"])
        assert machine.run() == Verdict.PASS
    assert machine._state.converged


def grouped_specs():
    return [
        {"name": name, "resolved": ResolvedReview([Path(file)], None, diff_for(file), "git")}
        for name, file in [("left", "left.ts"), ("right", "right.ts")]
    ]


@pytest.mark.parametrize("as_text", [False, True])
def test_grouped_attempts_preserve_scope_raw_collisions_and_reset(tmp_path, as_text):
    specs = grouped_specs()
    raw = {
        "findings": [],
        "code_excerpts": [{"file": "left.ts", "start_line": 1, "end_line": 1, "content": 7}],
        "group_scope": "MODEL-SPOOF",
        "group_name": "spoof",
        "literal": "line one\nline two\\n\u0000",
    }
    snapshot = copy.deepcopy(raw)
    calls = []

    def transport(*args, **kwargs):
        i = len(calls)
        calls.append(i)
        file = "left.ts" if i % 6 < 3 else "right.ts"
        packet = raw if i == 1 else good(file)
        return LLMResult(json.dumps(packet) if as_text else copy.deepcopy(packet), Usage(), 0.0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = build_grouped_l1_provider("auto", specs)
        specs[0]["name"] = "CALLER-SPOOF"
        specs[0]["resolved"].source_files.append(Path("CALLER-SPOOF.ts"))
        findings, excerpts, _, _ = provider()
        attempts = list(getattr(provider, "attempted_excerpts", []) or [])
        assert len(attempts) == 1
        paths = write_receipts(
            tmp_path / "receipts",
            0,
            findings,
            "scope-control",
            [],
            tmp_path,
            reviewer_excerpts=excerpts,
            attempted_excerpts=attempts,
            manifest="declared",
        )
        artifact = json.loads(next((tmp_path / "receipts/attempted").glob("*.json")).read_text())
        assert artifact["group_scope"] == {
            "name": "left",
            "diff_sha256": hashlib.sha256(diff_for("left.ts").encode()).hexdigest(),
            "source_files": ["left.ts"],
        }
        assert artifact["payload"] == snapshot | {"pass_name": "expert"}
        assert artifact["payload"]["group_scope"] == "MODEL-SPOOF"
        assert raw == snapshot
        assert len(excerpts) == 5 and all(e["content"] == CONTENT for e in excerpts)
        assert json.loads(paths[1].read_text())["pass_status"] == "schema_fail"
        provider()
        assert provider.attempted_excerpts == []
    assert len(calls) == 12


@pytest.mark.parametrize("rejected_group", [0, 1])
def test_grouped_missing_pass_reaches_full_machine(tmp_path, rejected_group):
    specs = grouped_specs()
    calls = []

    def transport(*args, **kwargs):
        i = len(calls)
        calls.append(i)
        group = i % 6 // 3
        file = ["left.ts", "right.ts"][group]
        packet = (
            {"findings": [], "code_excerpts": []}
            if i % 3 == 1 and group == rejected_group
            else good(file)
        )
        return LLMResult(copy.deepcopy(packet), Usage(), 0.0)

    diff = diff_for("left.ts") + diff_for("right.ts")
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = build_grouped_l1_provider("auto", specs)
        machine = machine_for(tmp_path, Mode.CI, provider, diff, ["left.ts", "right.ts"])
        assert machine.run() == Verdict.FAIL
    assert len(calls) == 6
    assert (
        json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())["pass_status"]
        == "incomplete"
    )


DELETION = (
    "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n@@ -1 +0,0 @@\n-old\n"
)
PARTIAL_DELETION = "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n@@ -1,2 +1 @@\n context\n-old\n"
RENAME = (
    "diff --git a/old.ts b/control.ts\nsimilarity index 100%\nrename from old.ts\nrename to control.ts\n"
)
MODE = "diff --git a/control.ts b/control.ts\nold mode 100644\nnew mode 100755\n"
BINARY = "diff --git a/control.ts b/control.ts\nindex c0c..d0d 100644\nBinary files a/control.ts and b/control.ts differ\n"
HEADER_ONLY = "diff --git a/control.ts b/control.ts\nindex c0c..d0d 100644\n"


@pytest.mark.parametrize(
    "diff",
    ["", " \n", DELETION, PARTIAL_DELETION, RENAME, MODE, BINARY, DELETION + RENAME + MODE + BINARY],
)
def test_proved_applicability_exemptions(diff):
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize(
    "diff",
    [
        "not a diff",
        HEADER_ONLY,
        "garbage\n" + MODE,
        MODE + "ambiguous\n",
        MODE.replace("100755", "100644"),
        MODE.replace("100755", "000000"),
        MODE.replace("100644", "000000"),
        RENAME.replace("100%", "99%"),
        RENAME.replace("rename to control.ts", "rename to spoof.ts"),
        "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n@@ -1 +1 @@\n context\n",
        diff_for("control.ts"),
        DELETION + diff_for("control.ts"),
        diff_for("control.ts") + "-extra\n",
        diff_for("control.ts").replace("+const value = 2;\n", ""),
    ],
)
def test_ambiguous_or_applicable_diff_requires_evidence(diff):
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("diff", ["", PARTIAL_DELETION, RENAME, MODE, BINARY])
def test_proved_exemptions_preserve_actual_machine_positive(tmp_path, diff):
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    with patch(
        "code_forge.llm_invoke.llm_invoke",
        return_value=LLMResult({"findings": [], "code_excerpts": []}, Usage(), 0.0),
    ) as transport:
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, Mode.CI, provider, diff, ["control.ts"])
        assert machine.run() == Verdict.PASS
    assert not provider.attempted_excerpts
    assert machine._state.converged
    assert transport.call_count == (0 if not diff else 3)


@pytest.mark.parametrize("grouped", [False, True])
def test_explicit_stub_setup_does_not_request_evidence(tmp_path, grouped):
    with patch("code_forge.llm_invoke.llm_invoke", side_effect=AssertionError("Transport forbidden")):
        provider = (
            build_grouped_l1_provider("stub", grouped_specs())
            if grouped
            else build_l1_provider("stub", ResolvedReview([], None, diff_for("control.ts"), "git"))
        )
        assert provider.is_stub_l1
        assert provider()[0:2] == ([], [])
        assert provider.attempted_excerpts == []


def test_grouped_retains_malformed_excerpt_with_pass_and_group_scope(tmp_path):
    specs = grouped_specs()
    damage = good("left.ts")
    damage["code_excerpts"][0].update(end_line=9)
    packets = [
        good("left.ts"),
        damage,
        good("left.ts"),
        good("right.ts"),
        good("right.ts"),
        good("right.ts"),
    ]
    with patch(
        "code_forge.llm_invoke.llm_invoke", side_effect=[LLMResult(p, Usage(), 0) for p in packets]
    ):
        provider = build_grouped_l1_provider("auto", specs)
        findings, excerpts, _, _ = provider()
    assert len(provider.attempted_excerpts) == 1
    attempted = provider.attempted_excerpts[0]
    assert attempted == damage | {"pass_name": "expert"}
    assert attempted.group_scope.name == "left"
    assert attempted.group_scope.source_files == ("left.ts",)
    assert attempted.group_scope.diff_sha256 == hashlib.sha256(diff_for("left.ts").encode()).hexdigest()
    assert findings == []
    assert len(excerpts) == 5


def test_ordinary_attempt_payload_cannot_spoof_outer_group_scope(tmp_path):
    payload = {
        "findings": [],
        "code_excerpts": [],
        "pass_name": "expert",
        "group_scope": {"name": "MODEL-SPOOF"},
        "literal": "line one\nline two\\n\u0000",
    }
    write_receipts(
        tmp_path / "receipts",
        0,
        [],
        "scope-control",
        [],
        tmp_path,
        reviewer_excerpts=[],
        attempted_excerpts=[payload],
        manifest="declared",
    )
    artifact = json.loads(next((tmp_path / "receipts/attempted").glob("*.json")).read_text())
    assert "group_scope" not in artifact
    assert artifact["payload"] == payload


@pytest.mark.parametrize("diff", [HEADER_ONLY, "not a diff", MODE + "ambiguous\n"])
def test_ambiguous_diff_does_not_exempt_actual_missing_pass(tmp_path, diff):
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    with patch(
        "code_forge.llm_invoke.llm_invoke",
        return_value=LLMResult({"findings": [], "code_excerpts": []}, Usage(), 0),
    ):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, Mode.CI, provider, diff, ["control.ts"])
        assert machine.run() == Verdict.FAIL
    assert len(provider.attempted_excerpts) == 3
    assert machine._receipt_gate_round_errors()
    assert json.loads((tmp_path / ".code-forge/state.json").read_text())["converged"] is False
    assert all(
        json.loads(p.read_text())["pass_status"] == "incomplete"
        for p in (tmp_path / ".code-forge/receipts").glob("receipt-*.json")
    )


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("answer", [Disposition.CONFIRMED, Disposition.DISMISSED])
def test_finding_only_required_pass_refuses_without_changing_validator(tmp_path, mode, grouped, answer):
    specs = grouped_specs()
    files = ["left.ts", "right.ts"] if grouped else ["control.ts"]
    diff = "".join(diff_for(file) for file in files)
    payload = {
        "findings": [{"file": files[0], "line": 1, "severity": "P2", "description": "candidate bug"}],
        "code_excerpts": [],
    }
    assert validate_reviewer_json(copy.deepcopy(payload)) == payload
    calls = []

    def transport(*args, **kwargs):
        i = len(calls)
        calls.append(i)
        file = files[i % (3 * len(files)) // 3]
        packet = payload if i % (3 * len(files)) == 1 else good(file)
        return LLMResult(copy.deepcopy(packet), Usage(), 0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = (
            build_grouped_l1_provider("auto", specs)
            if grouped
            else build_l1_provider("auto", ResolvedReview([Path(files[0])], None, diff, "git"))
        )
        machine = machine_for(tmp_path, mode, provider, diff, files)
        with patch.object(machine.falsifier, "falsify", return_value=answer) as falsify:
            assert machine.run() == Verdict.FAIL
        falsify.assert_not_called()
    retained = [f for f in machine._state.findings if f.source == "UNTRUSTED"]
    assert len(retained) == 1 and retained[0].disposition == Disposition.UNCERTAIN
    assert not any(f.source == "L1" for f in machine._state.findings)
    assert len(provider.attempted_excerpts) == 1
    assert_required_pass_terminal_refusal(machine)
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert receipt["pass_status"] == "incomplete"
    assert len(receipt["code_excerpts"]) == (1 if grouped else 0)
    assert receipt["findings_count"] == 1


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("finding_only", [False, True])
def test_outlet_c_required_scope_reaches_real_machine_and_receipts(
    tmp_path, monkeypatch, chunked, finding_only
):
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0" if chunked else "1000")
    files = ["left.ts", "right.ts"] if chunked else ["control.ts"]
    diff = "".join(diff_for(file) for file in files)
    for file in files:
        (tmp_path / file).write_text(CONTENT)
    payload = {
        "findings": [{"file": files[0], "line": 1, "severity": "P2", "description": "candidate bug"}]
        if finding_only
        else [],
        "code_excerpts": [],
    }
    actual = []
    calls = []

    def machine_with_substitutes(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
        )
        machine = StateMachine(**kwargs)
        machine._state.env_manifest = {"tier": "declared"}
        actual.append(machine)
        return machine

    def spawn(pass_name, chunk):
        calls.append((pass_name, chunk))
        file = "left.ts" if "left.ts" in chunk else files[-1]
        return json.dumps(payload if pass_name == "expert" and file == files[0] else good(file))

    monkeypatch.setattr("code_forge.outlet_c.StateMachine", machine_with_substitutes)
    assert (
        run_outlet_c(
            ResolvedReview([Path(f) for f in files], None, diff, "git"),
            compute_source_hash(git_diff=diff),
            tmp_path,
            spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=3,
        )
        == Verdict.FAIL
    )
    machine = actual[0]
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert disk["verdict"] == "FAIL" and disk["converged"] is False
    assert receipt["pass_status"] == "incomplete"
    assert len(machine.l1_provider.attempted_excerpts) == 1
    assert machine._receipt_gate_round_errors()
    assert_required_pass_terminal_refusal(machine)
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not verified.passed and "status=incomplete" in verified.reason
    assert len(calls) == 3 * len(files)


@pytest.mark.parametrize("diff", ["", PARTIAL_DELETION, RENAME, MODE, BINARY])
def test_outlet_c_proved_exemption_does_not_invent_attempt(diff):
    from code_forge.outlet_c import _run_chunk

    attempts = []
    findings, excerpts, _, _ = _run_chunk(
        diff,
        lambda *a: json.dumps({"findings": [], "code_excerpts": []}),
        ("qodo", "expert", "adversarial"),
        attempted=attempts,
    )
    assert attempts == [] and findings == [] and excerpts == []


@pytest.mark.parametrize("diff", ["+++ b/control.ts\n", "GIT binary patch\n"])
def test_malformed_scope_retains_all_outlet_attempts(tmp_path, diff):
    from code_forge.outlet_c import _run_chunk

    raw = {"findings": [], "code_excerpts": [], "literal": "line\nslash\\n\u0000"}
    attempts = []
    calls = []

    def spawn(pass_name, scope):
        calls.append((pass_name, scope))
        return copy.deepcopy(raw)

    findings, excerpts, _, _ = _run_chunk(
        diff, spawn, ("qodo", "expert", "adversarial"), attempted=attempts
    )
    assert calls == [(p, diff) for p in ("qodo", "expert", "adversarial")]
    assert findings == [] and excerpts == []
    assert attempts == [raw | {"pass_name": p} for p in ("qodo", "expert", "adversarial")]
    assert _requires_l1_excerpts(diff)
    paths = write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "malformed-scope",
        [],
        tmp_path,
        attempted_excerpts=attempts,
        manifest="declared",
    )
    assert [json.loads(p.read_text())["pass_status"] for p in paths] == ["incomplete"] * 3
    assert len(list((tmp_path / "receipts/attempted").glob("*.json"))) == 3


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
def test_blank_attempt_raw_survives_actual_machine(tmp_path, mode, grouped, as_text):
    files = ["left.ts", "right.ts"] if grouped else ["control.ts"]
    diff = "".join(diff_for(file) for file in files)
    raw = {
        "findings": [],
        "code_excerpts": [{"file": files[0], "start_line": 1, "end_line": 1, "content": " \n\t"}],
        "group_scope": {"name": "MODEL-SPOOF", "nested": ["one\n", "slash\\n", "\u0000"]},
        "literal": "one\ntwo\\n\u0000",
    }
    snapshot = copy.deepcopy(raw)
    calls = []

    def transport(*args, **kwargs):
        index = len(calls) % (3 * len(files))
        calls.append(index)
        packet = copy.deepcopy(raw if index == 1 else good(files[index // 3]))
        return LLMResult(json.dumps(packet) if as_text else packet, Usage(), 0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = (
            build_grouped_l1_provider("auto", grouped_specs())
            if grouped
            else build_l1_provider("auto", ResolvedReview([Path(files[0])], None, diff, "git"))
        )
        machine = machine_for(tmp_path, mode, provider, diff, files)
        with patch.object(machine.falsifier, "falsify") as falsify:
            assert machine.run() == Verdict.FAIL
        falsify.assert_not_called()
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and disk["converged"] is False
    artifact = json.loads(
        (tmp_path / ".code-forge/receipts/attempted/attempted-c1p2-0.json").read_text()
    )
    assert artifact["payload"] == snapshot | {"pass_name": "expert"}
    assert raw == snapshot
    if grouped:
        assert artifact["group_scope"] == {
            "name": "left",
            "source_files": ["left.ts"],
            "diff_sha256": hashlib.sha256(diff_for("left.ts").encode()).hexdigest(),
        }
    else:
        assert "group_scope" not in artifact
    receipt = json.loads((tmp_path / ".code-forge/receipts/receipt-c1p2.json").read_text())
    assert receipt["pass_status"] == "incomplete"
    assert len(receipt["code_excerpts"]) == (1 if grouped else 0)
    assert_required_pass_terminal_refusal(machine)


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
def test_blank_attempt_raw_survives_actual_outlet(tmp_path, monkeypatch, chunked, as_text):
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0" if chunked else "1000")
    files = ["left.ts", "right.ts"] if chunked else ["control.ts"]
    diff = "".join(diff_for(file) for file in files)
    raw = {
        "findings": [],
        "code_excerpts": [{"file": files[0], "start_line": 1, "end_line": 1, "content": " \n\t"}],
        "group_scope": {"literal": "one\ntwo\\n\u0000"},
    }
    actual = []

    def controlled_machine(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
        )
        machine = StateMachine(**kwargs)
        machine._state.env_manifest = {"tier": "declared"}
        actual.append(machine)
        return machine

    def spawn(pass_name, scope):
        file = "left.ts" if "left.ts" in scope else files[-1]
        packet = copy.deepcopy(raw if pass_name == "expert" and file == files[0] else good(file))
        return json.dumps(packet) if as_text else packet

    for file in files:
        (tmp_path / file).write_text(CONTENT)
    monkeypatch.setattr("code_forge.outlet_c.StateMachine", controlled_machine)
    assert (
        run_outlet_c(
            ResolvedReview([Path(f) for f in files], None, diff, "git"),
            compute_source_hash(git_diff=diff),
            tmp_path,
            spawn,
            falsifier=StubFalsifier(),
            max_total_rounds=3,
        )
        == Verdict.FAIL
    )
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and disk["converged"] is False
    artifact = json.loads(
        (tmp_path / ".code-forge/receipts/attempted/attempted-c1p2-0.json").read_text()
    )
    assert artifact["payload"] == raw | {"pass_name": "expert"}
    assert "group_scope" not in artifact
    assert_required_pass_terminal_refusal(actual[0])


ENCODED_BINARY = (
    "diff --git a/control.bin b/control.bin\n"
    "index 82a60be24cca2564698a3dcb697f3a4e6ee59a39..843f4dc3361ee83756fe8121154cd4b9b69b9b5a 100644\n"
    "GIT binary patch\nliteral 5\nMcmZR`OD*RD00ZO!RR910\n\n"
    "literal 5\nMcmZR`&q?6|00Y+nN&o-=\n\n"
)


def test_encoded_binary_preserves_actual_machine_positive(tmp_path):
    assert not _requires_l1_excerpts(ENCODED_BINARY)
    resolved = ResolvedReview([Path("control.bin")], None, ENCODED_BINARY, "git")
    (tmp_path / "control.bin").write_bytes(b"\0new\n")
    with patch(
        "code_forge.llm_invoke.llm_invoke",
        return_value=LLMResult({"findings": [], "code_excerpts": []}, Usage(), 0),
    ):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, Mode.CI, provider, ENCODED_BINARY, [])
        assert machine.run() == Verdict.PASS
    assert provider.attempted_excerpts == []
    assert machine._state.converged
    assert machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * 3


@pytest.mark.parametrize(
    "diff",
    [
        ENCODED_BINARY + diff_for("control.ts"),
        diff_for("control.ts") + ENCODED_BINARY,
        ENCODED_BINARY + diff_for("control.ts") + "ignored trailing text\n",
        "ignored leading text\n" + ENCODED_BINARY,
        ENCODED_BINARY + HEADER_ONLY,
        ENCODED_BINARY + PARTIAL_DELETION + "ignored trailing text\n",
        PARTIAL_DELETION + "ignored trailing text\n" + ENCODED_BINARY,
        "diff --git a/control.bin b/control.bin\nGIT binary patch\n",
        ENCODED_BINARY.split("literal 5\n", 1)[0],
    ],
)
def test_binary_sections_do_not_hide_required_text_or_corruption(diff):
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize(
    "diff",
    [
        ENCODED_BINARY.replace(
            "GIT binary patch\n", "--- a/control.bin\n+++ b/control.bin\nGIT binary patch\n"
        ),
        ENCODED_BINARY.replace(
            "index 82a60be24cca2564698a3dcb697f3a4e6ee59a39..843f4dc3361ee83756fe8121154cd4b9b69b9b5a 100644\n",
            "index invalid\n",
        ),
        BINARY + "ignored text\n",
    ],
)
def test_ambiguous_binary_wrappers_require_evidence(diff):
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("healthy", [False, True])
def test_encoded_binary_mixed_text_reaches_actual_machine(tmp_path, healthy):
    diff = ENCODED_BINARY + diff_for("control.ts")
    if not healthy:
        diff += "ignored text\n"
    resolved = ResolvedReview([Path("control.bin"), Path("control.ts")], None, diff, "git")
    payload = good("control.ts") if healthy else {"findings": [], "code_excerpts": []}
    (tmp_path / "control.bin").write_bytes(b"\0new\n")
    with patch(
        "code_forge.llm_invoke.llm_invoke",
        side_effect=lambda *a, **kw: LLMResult(copy.deepcopy(payload), Usage(), 0),
    ):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, Mode.CI, provider, diff, ["control.ts"])
        assert machine.run() == (Verdict.PASS if healthy else Verdict.FAIL)
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["converged"] is healthy
    assert len(provider.attempted_excerpts) == (0 if healthy else 3)
    assert bool(machine._receipt_gate_terminal_errors()) is not healthy


@pytest.mark.parametrize("chunked", [False, True])
def test_encoded_binary_reaches_actual_outlet_positive(tmp_path, monkeypatch, chunked):
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0" if chunked else "1000")
    actual = []

    def controlled_machine(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
        )
        machine = StateMachine(**kwargs)
        machine._state.env_manifest = {"tier": "declared"}
        actual.append(machine)
        return machine

    (tmp_path / "control.bin").write_bytes(b"\0new\n")
    monkeypatch.setattr("code_forge.outlet_c.StateMachine", controlled_machine)
    assert (
        run_outlet_c(
            ResolvedReview([Path("control.bin")], None, ENCODED_BINARY, "git"),
            compute_source_hash(git_diff=ENCODED_BINARY),
            tmp_path,
            lambda *a: {"findings": [], "code_excerpts": []},
            falsifier=StubFalsifier(),
            max_total_rounds=3,
        )
        == Verdict.PASS
    )
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"] is True
    assert actual[0].l1_provider.attempted_excerpts == []
    assert actual[0]._receipt_gate_terminal_errors() == []
    assert all(
        json.loads(p.read_text())["pass_status"] == "completed"
        for p in (tmp_path / ".code-forge/receipts").glob("receipt-*.json")
    )


@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_attempt_snapshot_owns_nested_transport_values(tmp_path, publisher):
    from code_forge.outlet_c import _run_chunk

    raw = {
        "findings": [],
        "code_excerpts": [{"file": "control.ts", "start_line": 1, "end_line": 1, "content": " \n\t"}],
        "extra": {"nested": ["one\n", "two\\n", "\u0000"]},
    }
    delivered = []

    def packet():
        value = copy.deepcopy(raw)
        delivered.append(value)
        return value

    diff = diff_for("control.ts")
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    if publisher == "outlet":
        attempts = []
        findings, excerpts, _, _ = _run_chunk(
            diff, lambda *a: packet(), ("qodo", "expert", "adversarial"), attempted=attempts
        )
    else:
        with patch(
            "code_forge.llm_invoke.llm_invoke",
            side_effect=lambda *a, **kw: LLMResult(packet(), Usage(), 0),
        ):
            provider = (
                build_grouped_l1_provider("auto", [{"name": "trusted", "resolved": resolved}])
                if publisher == "grouped"
                else build_l1_provider("auto", resolved)
            )
            findings, excerpts, _, _ = provider()
        attempts = provider.attempted_excerpts
    for payload in delivered:
        payload["extra"]["nested"].append("transport changed after capture")
    write_receipts(
        tmp_path / "receipts",
        0,
        findings,
        "snapshot-control",
        [],
        tmp_path,
        attempted_excerpts=attempts,
        reviewer_excerpts=excerpts,
        manifest="declared",
    )
    artifact = json.loads((tmp_path / "receipts/attempted/attempted-c1p1-0.json").read_text())
    assert artifact["payload"] == raw | {"pass_name": "qodo"}


# These literals were generated from local Git trees, including object modes.
GIT_METADATA_PACKETS = [
    (
        "quoted-nonascii",
        'diff --git "a/old-\\344\\270\\255.ts" "b/new-\\344\\270\\255.ts"\nsimilarity index 100%\nrename from "old-\\344\\270\\255.ts"\nrename to "new-\\344\\270\\255.ts"\n',
        "new-\u4e2d.ts",
        False,
    ),
    (
        "quoted-tab",
        'diff --git "a/old\\tname.ts" "b/new\\tname.ts"\nsimilarity index 100%\nrename from "old\\tname.ts"\nrename to "new\\tname.ts"\n',
        "new\tname.ts",
        False,
    ),
    (
        "rename-mode",
        "diff --git a/old.ts b/new.ts\nold mode 100644\nnew mode 100755\nsimilarity index 100%\nrename from old.ts\nrename to new.ts\n",
        "new.ts",
        False,
    ),
    (
        "quoted-rename-mode",
        'diff --git "a/old-\\344\\270\\255.ts" "b/new-\\344\\270\\255.ts"\nold mode 100644\nnew mode 100755\nsimilarity index 100%\nrename from "old-\\344\\270\\255.ts"\nrename to "new-\\344\\270\\255.ts"\n',
        "new-\u4e2d.ts",
        False,
    ),
    (
        "empty-new",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000000..e69de29\n",
        "empty.ts",
        True,
    ),
    (
        "empty-new-full-index",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000000000000000000000000000000000000000..e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\n",
        "empty.ts",
        True,
    ),
    (
        "quoted-nested-prefix",
        'diff --git "a/a/old-\\344\\270\\255.ts" "b/b/new-\\344\\270\\255.ts"\nsimilarity index 100%\nrename from "a/old-\\344\\270\\255.ts"\nrename to "b/new-\\344\\270\\255.ts"\n',
        "b/new-\u4e2d.ts",
        False,
    ),
    (
        "empty-new-sha256",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000000000000000000000000000000000000000000000000000000000000000..473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813\n",
        "empty.ts",
        True,
    ),
]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("packet", GIT_METADATA_PACKETS, ids=lambda packet: packet[0])
def test_git_metadata_exemptions_preserve_public_machine(tmp_path, mode, packet):
    _, diff, file, empty = packet
    assert not _requires_l1_excerpts(diff)
    (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    with patch(
        "code_forge.llm_invoke.llm_invoke",
        return_value=LLMResult({"findings": [], "code_excerpts": []}, Usage(), 0),
    ):
        provider = build_l1_provider("auto", resolved)
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        if empty:
            (tmp_path / file).write_bytes(b"")
        assert machine.run() == Verdict.PASS
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert provider.attempted_excerpts == []
    assert machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert verified.passed


@pytest.mark.parametrize("packet", GIT_METADATA_PACKETS, ids=lambda packet: packet[0])
@pytest.mark.parametrize("publisher", ["grouped", "outlet"])
def test_git_metadata_exemptions_preserve_other_publishers(tmp_path, packet, publisher):
    _, diff, file, _ = packet
    payload = {"findings": [], "code_excerpts": []}
    if publisher == "grouped":
        with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
            provider = build_grouped_l1_provider(
                "auto",
                [{"name": "metadata", "resolved": ResolvedReview([Path(file)], None, diff, "git")}],
            )
            findings, excerpts, _, _ = provider()
        attempts = provider.attempted_excerpts
    else:
        from code_forge.outlet_c import _run_chunk

        attempts = []
        findings, excerpts, _, _ = _run_chunk(
            diff,
            lambda *args: json.dumps(payload),
            ("qodo", "expert", "adversarial"),
            attempted=attempts,
        )
    assert findings == excerpts == attempts == []


GIT_EMPTY_NEW = GIT_METADATA_PACKETS[4][1]
GIT_RENAME_MODE = GIT_METADATA_PACKETS[2][1]


@pytest.mark.parametrize(
    "diff",
    [
        GIT_EMPTY_NEW.replace("e69de29", "d1170f0"),
        GIT_EMPTY_NEW.replace("0000000", "1000000"),
        GIT_EMPTY_NEW.replace("e69de29", "e69"),
        GIT_EMPTY_NEW.replace("0000000..e69de29", "000..e69"),
        GIT_EMPTY_NEW.replace("new file mode 100644", "new file mode 120000"),
        GIT_EMPTY_NEW.replace("new file mode 100644", "new file mode 000000"),
        GIT_EMPTY_NEW + "unconsumed text\n",
        "unconsumed text\n" + GIT_EMPTY_NEW,
        GIT_EMPTY_NEW + diff_for("control.ts"),
        GIT_EMPTY_NEW + diff_for("control.ts") + "-extra\n",
        GIT_RENAME_MODE.replace("new mode 100755", "new mode 100644"),
        GIT_RENAME_MODE.replace("old mode 100644", "old mode 000000"),
        GIT_RENAME_MODE.replace("new mode 100755", "new mode 000000"),
        GIT_RENAME_MODE.replace("100%", "99%"),
        GIT_RENAME_MODE.replace("old mode 100644", "prefix old mode 100644"),
        RENAME.replace("old.ts", "control.ts"),
        GIT_RENAME_MODE.replace("rename to new.ts", "rename to spoof.ts"),
        GIT_RENAME_MODE + "unconsumed text\n",
        GIT_METADATA_PACKETS[0][1].replace('rename to "new-', 'rename to "spoof-'),
        GIT_METADATA_PACKETS[0][1].replace('.ts"\n', ".ts\n"),
        GIT_METADATA_PACKETS[0][1].replace(
            GIT_METADATA_PACKETS[0][1].splitlines()[2],
            GIT_METADATA_PACKETS[0][1].splitlines()[2][:-1],
        ),
        GIT_RENAME_MODE.replace("rename from ", "rename source "),
        GIT_RENAME_MODE.replace("rename to ", "rename target "),
        GIT_METADATA_PACKETS[7][1].replace("473a0f4", "e69de29"),
    ],
)
def test_git_metadata_exemptions_refuse_ambiguous_neighbors(diff):
    assert _requires_l1_excerpts(diff)


# Exact literals and decoded names from the ARC048 scratch Git tree/index packets.
GIT_SPACE_B_PACKETS = [
    [
        "mode-space-b",
        "diff --git a/dir b/name.ts b/dir b/name.ts\nold mode 100644\nnew mode 100755\n",
        "dir b/name.ts",
    ],
    [
        "mode-repeated-space-b",
        "diff --git a/dir b/inner b/name.ts b/dir b/inner b/name.ts\nold mode 100644\nnew mode 100755\n",
        "dir b/inner b/name.ts",
    ],
    [
        "rename-space-b",
        "diff --git a/old b/name.ts b/new b/name.ts\nsimilarity index 100%\nrename from old b/name.ts\nrename to new b/name.ts\n",
        "new b/name.ts",
    ],
    [
        "rename-repeated-space-b",
        "diff --git a/old b/one b/name.ts b/new b/two b/name.ts\nsimilarity index 100%\nrename from old b/one b/name.ts\nrename to new b/two b/name.ts\n",
        "new b/two b/name.ts",
    ],
    [
        "rename-mode-space-b",
        "diff --git a/old b/name.ts b/new b/name.ts\nold mode 100644\nnew mode 100755\nsimilarity index 100%\nrename from old b/name.ts\nrename to new b/name.ts\n",
        "new b/name.ts",
    ],
    [
        "rename-nested-prefix",
        "diff --git a/a/old b/name.ts b/b/new b/name.ts\nsimilarity index 100%\nrename from a/old b/name.ts\nrename to b/new b/name.ts\n",
        "b/new b/name.ts",
    ],
    [
        "quoted-space-b-rename",
        'diff --git "a/old b/n\\tame-\\344\\270\\255.ts" "b/new b/n\\tame-\\344\\270\\255.ts"\nsimilarity index 100%\nrename from "old b/n\\tame-\\344\\270\\255.ts"\nrename to "new b/n\\tame-\\344\\270\\255.ts"\n',
        "new b/n\tame-\u4e2d.ts",
    ],
    [
        "quoted-space-b-mode",
        'diff --git "a/dir b/n\\tame-\\344\\270\\255.ts" "b/dir b/n\\tame-\\344\\270\\255.ts"\nold mode 100644\nnew mode 100755\n',
        "dir b/n\tame-\u4e2d.ts",
    ],
]


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("packet", GIT_SPACE_B_PACKETS, ids=lambda packet: packet[0])
def test_space_b_metadata_preserves_public_machine(tmp_path, mode, publisher, packet):
    _, diff, file = packet
    from code_forge.outlet_c import _run_chunk

    (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    payload = {"findings": [], "code_excerpts": []}
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        if publisher == "ordinary":
            provider = build_l1_provider("auto", resolved)
        elif publisher == "grouped":
            provider = build_grouped_l1_provider("auto", [{"name": "space-b", "resolved": resolved}])
        else:

            def provider():
                attempts = []
                result = _run_chunk(
                    diff,
                    lambda *a: json.dumps(payload),
                    ("qodo", "expert", "adversarial"),
                    attempted=attempts,
                )
                provider.attempted_excerpts = attempts
                return result

            provider.attempted_excerpts = []
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        assert machine.run() == Verdict.PASS
    assert not _requires_l1_excerpts(diff)
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert provider.attempted_excerpts == []
    assert machine._receipt_gate_round_errors() == machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    assert run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    ).passed


@pytest.mark.parametrize(
    "diff",
    [
        GIT_SPACE_B_PACKETS[0][1].replace("b/dir b/name.ts", "b/other b/name.ts"),
        GIT_SPACE_B_PACKETS[0][1].replace("diff --git a/", "diff --git z/"),
        GIT_SPACE_B_PACKETS[0][1].replace(" b/dir", "  b/dir"),
        GIT_SPACE_B_PACKETS[0][1].replace("100755", "100644"),
        GIT_SPACE_B_PACKETS[0][1].replace("100755", "000000"),
        GIT_SPACE_B_PACKETS[0][1] + "unconsumed text\n",
        GIT_SPACE_B_PACKETS[0][1] + diff_for("control.ts"),
        GIT_SPACE_B_PACKETS[2][1].replace("rename to new b/", "rename to spoof b/"),
        GIT_SPACE_B_PACKETS[2][1].replace("rename from old b/", "rename from spoof b/"),
        GIT_SPACE_B_PACKETS[2][1].replace("100%", "99%"),
        GIT_SPACE_B_PACKETS[2][1].replace("rename to", "rename target"),
        GIT_SPACE_B_PACKETS[2][1].replace("a/old b/name.ts b/new", "z/old b/name.ts b/new"),
        GIT_SPACE_B_PACKETS[2][1].replace("new b/name.ts", "old b/name.ts"),
        GIT_SPACE_B_PACKETS[2][1].replace("old b/name.ts", ""),
        GIT_SPACE_B_PACKETS[2][1].replace("new b/name.ts", ""),
        GIT_SPACE_B_PACKETS[2][1] + diff_for("control.ts") + "-extra\n",
        GIT_SPACE_B_PACKETS[6][1].replace('rename to "new b/', 'rename to "spoof b/'),
        GIT_SPACE_B_PACKETS[6][1].replace('.ts"\n', ".ts\n"),
        GIT_SPACE_B_PACKETS[7][1].replace('"b/dir b/', '"b/spoof b/'),
        GIT_SPACE_B_PACKETS[7][1].replace('"b/dir b/', '"c/dir b/'),
        GIT_SPACE_B_PACKETS[7][1].replace('.ts" "', '.ts " "'),
        GIT_SPACE_B_PACKETS[7][1].replace('.ts"\n', ".ts\n"),
        "diff --git a/ b/\nold mode 100644\nnew mode 100755\n",
        'diff --git "a/" "b/"\nold mode 100644\nnew mode 100755\n',
    ],
)
def test_space_b_metadata_refuses_corrupt_neighbors(diff):
    assert _requires_l1_excerpts(diff)


# Exact packets from owned SHA1 and SHA256 Git tree/index deletions.
GIT_EMPTY_DELETED_PACKETS = [
    (
        "sha1-plain-644-short",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69de29..0000000\n",
        "empty.ts",
    ),
    (
        "sha1-plain-644-full",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n",
        "empty.ts",
    ),
    (
        "sha1-plain-755-short",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100755\nindex e69de29..0000000\n",
        "empty.ts",
    ),
    (
        "sha1-plain-755-full",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100755\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n",
        "empty.ts",
    ),
    (
        "sha1-space-b-644-short",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100644\nindex e69de29..0000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha1-space-b-644-full",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100644\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha1-space-b-755-short",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100755\nindex e69de29..0000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha1-space-b-755-full",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100755\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha1-quoted-644-short",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100644\nindex e69de29..0000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha1-quoted-644-full",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100644\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha1-quoted-755-short",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100755\nindex e69de29..0000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha1-quoted-755-full",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100755\nindex e69de29bb2d1d6434b8b29ae775ad8c2e48c5391..0000000000000000000000000000000000000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha256-plain-644-short",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex 473a0f4..0000000\n",
        "empty.ts",
    ),
    (
        "sha256-plain-644-full",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n",
        "empty.ts",
    ),
    (
        "sha256-plain-755-short",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100755\nindex 473a0f4..0000000\n",
        "empty.ts",
    ),
    (
        "sha256-plain-755-full",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100755\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n",
        "empty.ts",
    ),
    (
        "sha256-space-b-644-short",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100644\nindex 473a0f4..0000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha256-space-b-644-full",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100644\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha256-space-b-755-short",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100755\nindex 473a0f4..0000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha256-space-b-755-full",
        "diff --git a/dir b/empty.ts b/dir b/empty.ts\ndeleted file mode 100755\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n",
        "dir b/empty.ts",
    ),
    (
        "sha256-quoted-644-short",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100644\nindex 473a0f4..0000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha256-quoted-644-full",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100644\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha256-quoted-755-short",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100755\nindex 473a0f4..0000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
    (
        "sha256-quoted-755-full",
        'diff --git "a/dir b/e\\t-\\344\\270\\255.ts" "b/dir b/e\\t-\\344\\270\\255.ts"\ndeleted file mode 100755\nindex 473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813..0000000000000000000000000000000000000000000000000000000000000000\n',
        "dir b/e\t-\u4e2d.ts",
    ),
]


@pytest.mark.parametrize("packet", GIT_EMPTY_DELETED_PACKETS, ids=lambda packet: packet[0])
def test_empty_deleted_metadata_requires_no_source_lines(packet):
    _, diff, _ = packet
    assert not _requires_l1_excerpts(diff)
    assert not any(parse_diff_files(diff).values())


@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("packet", GIT_EMPTY_DELETED_PACKETS, ids=lambda packet: packet[0])
def test_empty_deleted_metadata_preserves_public_machine(tmp_path, mode, publisher, packet):
    from code_forge.outlet_c import _run_chunk

    _, diff, file = packet
    (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    payload = {"findings": [], "code_excerpts": []}
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        if publisher == "ordinary":
            provider = build_l1_provider("auto", resolved)
        elif publisher == "grouped":
            provider = build_grouped_l1_provider(
                "auto", [{"name": "empty-deleted", "resolved": resolved}]
            )
        else:

            def provider():
                attempts = []
                result = _run_chunk(
                    diff,
                    lambda *a: json.dumps(payload),
                    ("qodo", "expert", "adversarial"),
                    attempted=attempts,
                )
                provider.attempted_excerpts = attempts
                return result

            provider.attempted_excerpts = []
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        (tmp_path / file).unlink()
        assert machine.run() == Verdict.PASS
    assert not _requires_l1_excerpts(diff)
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert provider.attempted_excerpts == []
    assert machine._receipt_gate_round_errors() == machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    assert run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    ).passed


GIT_EMPTY_DELETED = GIT_EMPTY_DELETED_PACKETS[0][1]


GIT_EMPTY_DELETED_DAMAGED_PACKETS = [
    GIT_EMPTY_DELETED.replace("e69de29", "d1170f0"),
    GIT_EMPTY_DELETED.replace("0000000", "1000000"),
    GIT_EMPTY_DELETED.replace("e69de29", "e69"),
    GIT_EMPTY_DELETED.replace("e69de29..0000000", "e69..000"),
    GIT_EMPTY_DELETED.replace("deleted file mode 100644", "deleted file mode 120000"),
    GIT_EMPTY_DELETED.replace("deleted file mode 100644", "deleted file mode 000000"),
    GIT_EMPTY_DELETED.replace("deleted file mode 100644", "deleted file mode 100600"),
    GIT_EMPTY_DELETED.replace("e69de29..0000000", "E69de29..0000000"),
    GIT_EMPTY_DELETED.replace("e69de29..0000000", "e69de29..0000000 100644"),
    GIT_EMPTY_DELETED.replace("b/empty.ts", "b/spoof.ts"),
    GIT_EMPTY_DELETED.replace("a/empty.ts", "z/empty.ts"),
    GIT_EMPTY_DELETED.replace("diff --git a/empty.ts b/empty.ts", "diff --git a/ b/"),
    GIT_EMPTY_DELETED.replace("index ", "similarity index 100%\nindex "),
    GIT_EMPTY_DELETED + "index e69de29..0000000\n",
    GIT_EMPTY_DELETED + "unconsumed text\n",
    "unconsumed text\n" + GIT_EMPTY_DELETED,
    GIT_EMPTY_DELETED + diff_for("control.ts"),
    GIT_EMPTY_DELETED + diff_for("control.ts") + "-extra\n",
    GIT_EMPTY_DELETED_PACKETS[1][1].replace(
        "bb2d1d6434b8b29ae775ad8c2e48c5391", "bb2d1d6434b8b29ae775ad8c2e48c5390"
    ),
    GIT_EMPTY_DELETED_PACKETS[13][1].replace("473a0f4", "e69de29"),
    GIT_EMPTY_DELETED_PACKETS[13][1].replace("a303721813", "a303721812"),
    GIT_EMPTY_DELETED_PACKETS[4][1].replace("b/dir b/empty.ts", "b/dir b/spoof.ts"),
    GIT_EMPTY_DELETED_PACKETS[8][1].replace('"b/dir b/', '"c/dir b/'),
    GIT_EMPTY_DELETED_PACKETS[8][1].replace('.ts" "', '.ts " "'),
]


@pytest.mark.parametrize("diff", GIT_EMPTY_DELETED_DAMAGED_PACKETS)
def test_empty_deleted_metadata_refuses_damaged_packets(diff):
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("diff", GIT_EMPTY_DELETED_DAMAGED_PACKETS)
def test_empty_deleted_damaged_metadata_refuses_receipt_verifier(tmp_path, diff):
    source_hash = compute_source_hash(git_diff=diff)
    write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        [],
        source_hash,
        [],
        tmp_path,
        diff_text=diff,
        reviewer_excerpts=[],
        manifest="declared",
    )
    result = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed
    assert result.checks_passed == 4 and result.checks_run == 5


# R6 literals are pinned to actual owned Git packets; added width controls
# exercise independently sized legal abbreviations. Unicode names retain LF framing.
R6_GIT_PACKETS = [
    (
        "empty-new-ts-abbrev4",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000..e69d\n",
        "empty.ts",
        "empty",
    ),
    (
        "empty-new-ts-abbrev6",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 000000..e69de2\n",
        "empty.ts",
        "empty",
    ),
    (
        "empty-new-ts-abbrev7",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000000..e69de29\n",
        "empty.ts",
        "empty",
    ),
    (
        "empty-new-py-abbrev4",
        "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000..e69d\n",
        "empty.py",
        "empty",
    ),
    (
        "empty-new-py-abbrev6",
        "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 000000..e69de2\n",
        "empty.py",
        "empty",
    ),
    (
        "empty-new-py-abbrev7",
        "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000000..e69de29\n",
        "empty.py",
        "empty",
    ),
    (
        "empty-new-sha1-abbrev4",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000..e69d\n",
        "empty.ts",
        "empty",
    ),
    (
        "empty-deleted-sha1-abbrev4",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69d..0000\n",
        "empty.ts",
        "deleted",
    ),
    (
        "empty-new-sha256-abbrev4",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000..473a\n",
        "empty.ts",
        "empty",
    ),
    (
        "empty-deleted-sha256-abbrev4",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex 473a..0000\n",
        "empty.ts",
        "deleted",
    ),
    (
        "deleted",
        "diff --git a/deleted.ts b/deleted.ts\ndeleted file mode 100644\nindex 3dcfd4f..0000000\n--- a/deleted.ts\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-let first = 1;\n-let second = 2;\n-let third = 3;\n",
        "deleted.ts",
        "deleted",
    ),
    (
        "partial",
        "diff --git a/partial.ts b/partial.ts\nindex 3dcfd4f..c50af5c 100644\n--- a/partial.ts\n+++ b/partial.ts\n@@ -1,3 +1,2 @@\n-let first = 1;\n let second = 2;\n let third = 3;\n",
        "partial.ts",
        "present",
    ),
    (
        "partial-mode",
        "diff --git a/partial-mode.ts b/partial-mode.ts\nold mode 100644\nnew mode 100755\nindex 3dcfd4f..c50af5c\n--- a/partial-mode.ts\n+++ b/partial-mode.ts\n@@ -1,3 +1,2 @@\n-let first = 1;\n let second = 2;\n let third = 3;\n",
        "partial-mode.ts",
        "present",
    ),
    (
        "unicode-mode-0",
        "diff --git a/mode\u2028name.ts b/mode\u2028name.ts\nold mode 100644\nnew mode 100755\n",
        "mode\u2028name.ts",
        "present",
    ),
    (
        "unicode-rename-0",
        "diff --git a/old\u2028name.ts b/new\u2028name.ts\nsimilarity index 100%\nrename from old\u2028name.ts\nrename to new\u2028name.ts\n",
        "new\u2028name.ts",
        "present",
    ),
    (
        "unicode-mode-1",
        "diff --git a/mode\u2029name.ts b/mode\u2029name.ts\nold mode 100644\nnew mode 100755\n",
        "mode\u2029name.ts",
        "present",
    ),
    (
        "unicode-rename-1",
        "diff --git a/old\u2029name.ts b/new\u2029name.ts\nsimilarity index 100%\nrename from old\u2029name.ts\nrename to new\u2029name.ts\n",
        "new\u2029name.ts",
        "present",
    ),
    (
        "unicode-mode-2",
        "diff --git a/mode\x85name.ts b/mode\x85name.ts\nold mode 100644\nnew mode 100755\n",
        "mode\x85name.ts",
        "present",
    ),
    (
        "unicode-rename-2",
        "diff --git a/old\x85name.ts b/new\x85name.ts\nsimilarity index 100%\nrename from old\x85name.ts\nrename to new\x85name.ts\n",
        "new\x85name.ts",
        "present",
    ),
    (
        "new-independent-abbrev",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000..e69de\n",
        "empty.ts",
        "empty",
    ),
    (
        "deleted-independent-abbrev",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69de..0000\n",
        "empty.ts",
        "deleted",
    ),
    (
        "new-legacy-six",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 0000000..e69de2\n",
        "empty.ts",
        "empty",
    ),
    (
        "deleted-legacy-six",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69de2..0000000\n",
        "empty.ts",
        "deleted",
    ),
    (
        "new-legacy-zero-width",
        "diff --git a/empty.ts b/empty.ts\nnew file mode 100644\nindex 00000000..e69de29\n",
        "empty.ts",
        "empty",
    ),
    (
        "deleted-legacy-zero-width",
        "diff --git a/empty.ts b/empty.ts\ndeleted file mode 100644\nindex e69de29..00000000\n",
        "empty.ts",
        "deleted",
    ),
    (
        "partial-rename",
        "diff --git a/rename-old.ts b/rename-new.ts\nsimilarity index 90%\nrename from rename-old.ts\nrename to rename-new.ts\nindex 2dafd0e..25e029c 100644\n--- a/rename-old.ts\n+++ b/rename-new.ts\n@@ -1,4 +1,3 @@\n-let value1 = 1;\n let value2 = 2;\n let value3 = 3;\n let value4 = 4;\n",
        "rename-new.ts",
        "present",
    ),
    (
        "partial-copy",
        "diff --git a/copy-old.ts b/copy-new.ts\nsimilarity index 90%\ncopy from copy-old.ts\ncopy to copy-new.ts\nindex 2dafd0e..25e029c 100644\n--- a/copy-old.ts\n+++ b/copy-new.ts\n@@ -1,4 +1,3 @@\n-let value1 = 1;\n let value2 = 2;\n let value3 = 3;\n let value4 = 4;\n",
        "copy-new.ts",
        "present",
    ),
    (
        "deletion-rewrite",
        "diff --git a/rewrite.ts b/rewrite.ts\nindex 2dafd0e..e69de29 100644\n--- a/rewrite.ts\n+++ b/rewrite.ts\n@@ -1,10 +0,0 @@\n-let value1 = 1;\n-let value2 = 2;\n-let value3 = 3;\n-let value4 = 4;\n-let value5 = 5;\n-let value6 = 6;\n-let value7 = 7;\n-let value8 = 8;\n-let value9 = 9;\n-let value10 = 10;\n",
        "rewrite.ts",
        "present",
    ),
    (
        "traditional-deletion",
        "--- a/traditional.ts\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
        "traditional.ts",
        "deleted",
    ),
]


def metadata_provider(publisher, resolved, payload):
    if publisher == "ordinary":
        return build_l1_provider("auto", resolved)
    if publisher == "grouped":
        return build_grouped_l1_provider("auto", [{"name": "metadata", "resolved": resolved}])
    from code_forge.outlet_c import _run_chunk

    def provider():
        attempts = []
        result = _run_chunk(
            resolved.git_diff,
            lambda *a: json.dumps(payload),
            ("qodo", "expert", "adversarial"),
            attempted=attempts,
        )
        provider.attempted_excerpts = attempts
        return result

    provider.attempted_excerpts = []
    return provider


@pytest.mark.parametrize("packet", R6_GIT_PACKETS, ids=lambda packet: packet[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_r6_actual_git_metadata_preserves_public_machine(tmp_path, monkeypatch, packet, publisher, mode):
    name, diff, file, state = packet
    assert not _requires_l1_excerpts(diff)
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    payload = {"findings": [], "code_excerpts": []}
    post_image = (
        "let second = 2;\nlet third = 3;\n"
        if name in ("partial", "partial-mode")
        else "".join(f"let value{i} = {i};\n" for i in range(2, 11))
        if name.startswith("partial-")
        else CONTENT
    )
    monkeypatch.setattr("code_forge.git.read_diff_blob", lambda *a, **kw: post_image)
    with (
        patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)),
        patch("shutil.which", return_value="/controlled/mutmut"),
        patch("code_forge.machine.launch_detached_mutation", return_value=True) as detached,
    ):
        provider = metadata_provider(publisher, resolved, payload)
        (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        if state == "empty":
            (tmp_path / file).write_bytes(b"")
        elif state == "deleted":
            (tmp_path / file).unlink()
        elif name.startswith("partial"):
            (tmp_path / file).write_text(post_image)
        assert machine.run() == Verdict.PASS
        if file.endswith(".py") and mode == Mode.CI:
            detached.assert_called_once()
            assert detached.call_args.args[0] == [file]
            assert detached.call_args.args[1] == ["controlled-never-run"]
            assert detached.call_args.args[2] == tmp_path
        else:
            detached.assert_not_called()
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert provider.attempted_excerpts == []
    assert machine._receipt_gate_round_errors() == machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    assert run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    ).passed


R6_DELETED = next(packet[1] for packet in R6_GIT_PACKETS if packet[0] == "deleted")
R6_DAMAGED_DELETION = [
    R6_DELETED.replace("--- a/", "INVALID HEADER\n--- a/"),
    R6_DELETED.replace("index ", "deleted file mode 100644\nindex "),
    R6_DELETED.replace("--- a/", "index 3dcfd4f..0000000\n--- a/"),
    R6_DELETED.replace("deleted file mode 100644", "deleted file mode 100600"),
    R6_DELETED.replace("..0000000", "..0000000 100644"),
    R6_DELETED.replace("diff --git a/deleted.ts", "diff --git a/spoof.ts"),
    R6_DELETED.replace("b/deleted.ts", "b/spoof.ts"),
    R6_DELETED.replace("--- a/", "encoding UTF-8\n--- a/"),
]


@pytest.mark.parametrize("diff", R6_DAMAGED_DELETION)
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_r6_damaged_deleted_hunks_refuse_public_machine(tmp_path, diff, publisher, mode):
    assert _requires_l1_excerpts(diff)
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([Path("deleted.ts")], None, diff, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, ["deleted.ts"])
        (tmp_path / "deleted.ts").unlink()
        assert machine.run() == Verdict.FAIL
    assert len(provider.attempted_excerpts) == 3
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and not disk["converged"]
    assert len(list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))) == 3
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["incomplete"] * 3
    assert machine._receipt_gate_round_errors() and machine._receipt_gate_terminal_errors()
    result = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed
    assert result.checks_passed == 4 and result.checks_run == 5


@pytest.mark.parametrize("diff", R6_DAMAGED_DELETION)
def test_r6_damaged_deleted_hunks_refuse_direct_writer_verifier(tmp_path, diff):
    source_hash = compute_source_hash(git_diff=diff)
    write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        [],
        source_hash,
        [],
        tmp_path,
        diff_text=diff,
        reviewer_excerpts=[],
        manifest="declared",
    )
    result = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed
    assert result.checks_passed == 4 and result.checks_run == 5


# These neighbors derive from pinned Git hunks; the added metadata is controlled.
R6_PARTIAL = next(packet[1] for packet in R6_GIT_PACKETS if packet[0] == "partial")
R6_PARTIAL_MODE = next(packet[1] for packet in R6_GIT_PACKETS if packet[0] == "partial-mode")
R6_PARTIAL_RENAME = next(packet[1] for packet in R6_GIT_PACKETS if packet[0] == "partial-rename")


@pytest.mark.parametrize(
    "diff",
    [
        R6_PARTIAL.replace("index ", "dissimilarity index 20%\nindex "),
        R6_PARTIAL_MODE.replace("index ", "dissimilarity index 20%\nindex "),
    ],
)
def test_r6_recognized_rewrite_metadata_keeps_deleted_hunk_exemption(diff):
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize(
    "diff",
    [
        R6_PARTIAL.replace("index ", "INVALID HEADER\nindex "),
        R6_PARTIAL.replace("index 3dcfd4f", "index 0000000"),
        R6_PARTIAL.replace("..c50af5c", "..0000000"),
        R6_PARTIAL.replace("100644", "100600"),
        R6_PARTIAL_MODE.replace("..c50af5c", "..c50af5c 100644"),
        R6_PARTIAL_MODE.replace("new mode 100755", "new mode 100644"),
        R6_PARTIAL_RENAME.replace("rename to ", "rename target "),
        R6_PARTIAL_RENAME.replace("rename to rename-new.ts", "rename to spoof.ts"),
        R6_PARTIAL_RENAME.replace("90%", "101%"),
        R6_PARTIAL_RENAME.replace("rename from rename-old.ts", 'rename from "rename-old.ts'),
        R6_PARTIAL.replace("--- a/partial.ts", "--- a/spoof.ts"),
        "diff --git a/control.ts b/spoof.ts\n--- a/control.ts\n+++ b/control.ts\n@@ -1 +0,0 @@\n-old\n",
        GIT_EMPTY_NEW.replace("b/empty.ts", "b/spoof.ts"),
        GIT_EMPTY_NEW.replace("0000000", "0" * 41),
    ],
)
def test_r6_deleted_hunk_and_empty_object_metadata_refuse_ambiguity(diff):
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("case", ["short-empty", "unicode-rename", "damaged-deletion"])
def test_r6_actual_outlet_dispatch_keeps_metadata_authority(tmp_path, monkeypatch, chunked, case):
    name = "new-independent-abbrev" if case == "short-empty" else "unicode-rename-0"
    _, diff, file, state = next(packet for packet in R6_GIT_PACKETS if packet[0] == name)
    healthy = case != "damaged-deletion"
    if not healthy:
        diff, file, state = R6_DAMAGED_DELETION[0], "deleted.ts", "deleted"
    (tmp_path / file).write_text("" if state == "empty" else CONTENT)
    if state == "deleted":
        (tmp_path / file).unlink()
    actual = []
    calls = []

    def controlled_machine(**kwargs):
        kwargs.update(
            l0_runner=lambda *a: ([], []),
            l2_runner=lambda *a, **kw: ([], []),
            e2e_runner=lambda *a: ([], []),
        )
        machine = StateMachine(**kwargs)
        machine._state.env_manifest = {"tier": "declared"}
        actual.append(machine)
        return machine

    def spawn(pass_name, chunk):
        calls.append((pass_name, chunk))
        return json.dumps({"findings": [], "code_excerpts": []})

    monkeypatch.setattr("code_forge.outlet_c.StateMachine", controlled_machine)
    monkeypatch.setenv("FORGE_DIFF_CHUNK_THRESHOLD_KB", "0" if chunked else "1000")
    result = run_outlet_c(
        ResolvedReview([Path(file)], None, diff, "git"),
        compute_source_hash(git_diff=diff),
        tmp_path,
        spawn,
        falsifier=StubFalsifier(),
        max_total_rounds=3,
    )
    assert result == (Verdict.PASS if healthy else Verdict.FAIL)
    assert len(calls) == (9 if healthy else 3)
    machine = actual[0]
    statuses = [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ]
    assert statuses == (["completed"] * 9 if healthy else ["incomplete"] * 3)
    assert len(machine.l1_provider.attempted_excerpts) == (0 if healthy else 3)
    assert bool(machine._receipt_gate_terminal_errors()) is not healthy
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert verified.passed is healthy


# Literal packets from owned real Git; zero-old-start applied and reversed.
R7_GIT_PACKETS = [
    (
        "copy",
        "diff --git a/old.ts b/new.ts\nsimilarity index 100%\ncopy from old.ts\ncopy to new.ts\n",
        "new.ts",
        "old.ts",
        "present",
    ),
    (
        "copy-mode",
        "diff --git a/old.ts b/new.ts\nold mode 100644\nnew mode 100755\nsimilarity index 100%\ncopy from old.ts\ncopy to new.ts\n",
        "new.ts",
        "old.ts",
        "present",
    ),
    (
        "copy-space-b",
        "diff --git a/old b/name.ts b/new b/name.ts\nsimilarity index 100%\ncopy from old b/name.ts\ncopy to new b/name.ts\n",
        "new b/name.ts",
        "old b/name.ts",
        "present",
    ),
    (
        "copy-quoted",
        'diff --git "a/old\\t\\303\\251.ts" "b/new\\t\\303\\251.ts"\nsimilarity index 100%\ncopy from "old\\t\\303\\251.ts"\ncopy to "new\\t\\303\\251.ts"\n',
        "new\t\xe9.ts",
        "old\t\xe9.ts",
        "present",
    ),
    (
        "traditional-delete",
        "--- a/old.ts\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-old\n",
        "old.ts",
        None,
        "deleted",
    ),
    (
        "zero-old-start",
        "--- a/old.ts\n+++ /dev/null\n@@ -0,1 +0,0 @@\n-old\n",
        "old.ts",
        None,
        "deleted",
    ),
]

R7_BOTH_NULL = "--- /dev/null\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"


@pytest.mark.parametrize("packet", R7_GIT_PACKETS, ids=lambda packet: packet[0])
def test_r7_actual_git_metadata_requires_no_post_image(packet):
    assert not _requires_l1_excerpts(packet[1])


@pytest.mark.parametrize("packet", R7_GIT_PACKETS, ids=lambda packet: packet[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_r7_actual_git_metadata_preserves_public_machine(tmp_path, packet, publisher, mode):
    _, diff, file, source, state = packet
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    payload = {"findings": [], "code_excerpts": []}
    (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
    if source:
        (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / source).write_text(CONTENT)
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        if state == "deleted":
            (tmp_path / file).unlink()
        assert machine.run() == Verdict.PASS
    assert provider.attempted_excerpts == []
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert machine._receipt_gate_round_errors() == machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    assert run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    ).passed


@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_r7_both_null_refuses_public_machine(tmp_path, publisher, mode):
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([Path("deleted.ts")], None, R7_BOTH_NULL, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, R7_BOTH_NULL, ["deleted.ts"])
        (tmp_path / "deleted.ts").unlink()
        assert machine.run() == Verdict.FAIL
    assert len(provider.attempted_excerpts) == 3
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and not disk["converged"]
    assert len(list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))) == 3
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["incomplete"] * 3
    assert machine._receipt_gate_round_errors() and machine._receipt_gate_terminal_errors()
    result = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(R7_BOTH_NULL),
        diff_text=R7_BOTH_NULL,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed and result.checks_passed == 4 and result.checks_run == 5


def test_r7_both_null_refuses_direct_writer_verifier(tmp_path):
    source_hash = compute_source_hash(git_diff=R7_BOTH_NULL)
    write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        [],
        source_hash,
        [],
        tmp_path,
        diff_text=R7_BOTH_NULL,
        reviewer_excerpts=[],
        manifest="declared",
    )
    result = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(R7_BOTH_NULL),
        diff_text=R7_BOTH_NULL,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed and result.checks_passed == 4 and result.checks_run == 5


R7_COPY = R7_GIT_PACKETS[0][1]


@pytest.mark.parametrize(
    "diff",
    [
        R7_COPY.replace("100%", "90%"),
        R7_COPY.replace("100%", "101%"),
        R7_COPY.replace("copy from ", "rename from "),
        R7_COPY.replace("copy to ", "rename to "),
        R7_COPY.replace("copy to ", "copy target "),
        R7_COPY.replace("copy from ", "move from "),
        R7_COPY.replace("copy to ", "move to "),
        R7_COPY.replace("copy from old.ts", "copy from spoof.ts"),
        R7_COPY.replace("copy to new.ts", "copy to spoof.ts"),
        R7_COPY.replace("new.ts", "old.ts"),
        R7_COPY.replace("copy from old.ts", 'copy from "old.ts'),
    ],
)
def test_r7_copy_metadata_refuses_ambiguous_neighbors(diff):
    assert _requires_l1_excerpts(diff)


# Literal quoted text packets generated and applied/reversed by owned Git.
R8_GIT_PACKETS = [
    (
        "quoted-rename",
        'diff --git "a/quoted-old b/one\\t.ts" "b/quoted-new b/two\\t.ts"\nsimilarity index 71%\nrename from "quoted-old b/one\\t.ts"\nrename to "quoted-new b/two\\t.ts"\nindex 4cb29ea..4c7442b 100644\n--- "a/quoted-old b/one\\t.ts"\t\n+++ "b/quoted-new b/two\\t.ts"\t\n@@ -1,3 +1,2 @@\n one\n-two\n three\n',
        "quoted-new b/two\t.ts",
        "quoted-old b/one\t.ts",
        "one\nthree\n",
    ),
    (
        "quoted-rename-mode",
        'diff --git "a/quoted-old b/one\\t.ts" "b/quoted-new b/two\\t.ts"\nold mode 100644\nnew mode 100755\nsimilarity index 71%\nrename from "quoted-old b/one\\t.ts"\nrename to "quoted-new b/two\\t.ts"\nindex 4cb29ea..4c7442b\n--- "a/quoted-old b/one\\t.ts"\t\n+++ "b/quoted-new b/two\\t.ts"\t\n@@ -1,3 +1,2 @@\n one\n-two\n three\n',
        "quoted-new b/two\t.ts",
        "quoted-old b/one\t.ts",
        "one\nthree\n",
    ),
    (
        "quoted-nonascii",
        'diff --git "a/old\\t\\303\\251.ts" "b/new\\t\\303\\251.ts"\nsimilarity index 71%\nrename from "old\\t\\303\\251.ts"\nrename to "new\\t\\303\\251.ts"\nindex 4cb29ea..4c7442b 100644\n--- "a/old\\t\\303\\251.ts"\n+++ "b/new\\t\\303\\251.ts"\n@@ -1,3 +1,2 @@\n one\n-two\n three\n',
        "new\t\xe9.ts",
        "old\t\xe9.ts",
        "one\nthree\n",
    ),
]

R8_RETAINED_NULL = "--- a/control.ts\n+++ /dev/null\n@@ -1,2 +1,1 @@\n-one\n two\n"
R8_RETAINED_GIT_NULL = (
    "diff --git a/control.ts b/control.ts\ndeleted file mode 100644\n"
    "index 4cb29ea..0000000\n" + R8_RETAINED_NULL
)


@pytest.mark.parametrize("packet", R8_GIT_PACKETS, ids=lambda packet: packet[0])
def test_quoted_deletion_rename_keeps_literal_identity(packet):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff, _, _, _ = packet
    patches = _parse_review_patches(diff)
    assert len(patches) == 1 and len(patches[0]) == 1
    assert _diff_literal_complete(diff, patches)
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", R8_GIT_PACKETS, ids=lambda packet: packet[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_quoted_deletion_rename_preserves_public_machine(tmp_path, monkeypatch, packet, publisher, mode):
    _, diff, file, source, post_image = packet
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([Path(file)], None, diff, "git")
    (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("code_forge.git.read_diff_blob", lambda *a, **kw: post_image)
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, [file])
        (tmp_path / file).write_text(post_image)
        assert not (tmp_path / source).exists()
        assert machine.run() == Verdict.PASS
    assert provider.attempted_excerpts == []
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "PASS" and disk["converged"]
    assert machine._receipt_gate_round_errors() == machine._receipt_gate_terminal_errors() == []
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["completed"] * (3 if mode == Mode.CI else 9)
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert verified.passed and verified.checks_passed == verified.checks_run


@pytest.mark.parametrize("diff", [R8_RETAINED_NULL, R8_RETAINED_GIT_NULL])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_removed_file_retained_target_refuses_public_machine(tmp_path, diff, publisher, mode):
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([Path("control.ts")], None, diff, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, ["control.ts"])
        (tmp_path / "control.ts").unlink()
        assert machine.run() == Verdict.FAIL
    assert len(provider.attempted_excerpts) == 3
    assert machine._receipt_gate_round_errors() and machine._receipt_gate_terminal_errors()
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and not disk["converged"]
    assert len(list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))) == 3
    assert [
        json.loads(p.read_text())["pass_status"]
        for p in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["incomplete"] * 3
    verified = run_verify(
        tmp_path,
        machine.source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not verified.passed and verified.reason == "diff parse failed -- cannot verify excerpts"
    assert verified.checks_passed == 4 and verified.checks_run == 5

    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("diff", [R8_RETAINED_NULL, R8_RETAINED_GIT_NULL])
def test_removed_file_retained_target_refuses_direct_writer(tmp_path, diff):
    source_hash = compute_source_hash(git_diff=diff)
    write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        [],
        source_hash,
        [],
        tmp_path,
        diff_text=diff,
        reviewer_excerpts=[],
        manifest="declared",
    )
    verified = run_verify(
        tmp_path,
        source_hash,
        parse_diff_files(diff),
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not verified.passed and verified.checks_passed == 4 and verified.checks_run == 5


R8_QUOTED_RENAME = R8_GIT_PACKETS[0][1]


@pytest.mark.parametrize(
    "diff",
    [
        R8_QUOTED_RENAME.replace('rename from "quoted-old', 'rename from "spoof-old'),
        R8_QUOTED_RENAME.replace('diff --git "a/quoted-old', 'diff --git "a/spoof-old'),
        R8_QUOTED_RENAME.replace('+++ "b/quoted-new', '+++ "b/spoof-new'),
        R8_QUOTED_RENAME + "unconsumed trailing text\n",
        R8_QUOTED_RENAME + R8_RETAINED_GIT_NULL,
        R8_RETAINED_GIT_NULL + R8_QUOTED_RENAME,
        R8_QUOTED_RENAME.replace("71%", "101%"),
    ],
)
def test_quoted_rename_does_not_hide_identity_corruption_or_invalid_sibling(diff):
    assert _requires_l1_excerpts(diff)


def test_empty_header_timestamp_restoration_preserves_removed_content_tab():
    diff = "--- a/content.ts\n+++ /dev/null\n@@ -1 +0,0 @@\n--- value\t\n"
    assert not _requires_l1_excerpts(diff)


VALID_MULTI_FILE_PACKETS = [
    (
        "space-b-deletion-mode",
        "diff --git a/control.ts b/control.ts\nold mode 100644\nnew mode 100755\n"
        "diff --git a/dir b/name.ts b/dir b/name.ts\ndeleted file mode 100644\n"
        "index 4cb29ea..0000000\n--- a/dir b/name.ts\t\n+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n-one\n-two\n-three\n",
    ),
    (
        "native-no-prefix",
        "diff --git old.ts new.ts\nsimilarity index 83%\nrename from old.ts\n"
        "rename to new.ts\nindex b2f931a..1d0ff88 100644\n--- old.ts\n+++ new.ts\n"
        "@@ -1,5 +1,4 @@\n one\n-two\n three\n four\n five\n",
    ),
    (
        "native-mnemonic",
        "diff --git c/old.ts i/new.ts\nsimilarity index 83%\nrename from old.ts\n"
        "rename to new.ts\nindex b2f931a..1d0ff88 100644\n--- c/old.ts\n+++ i/new.ts\n"
        "@@ -1,5 +1,4 @@\n one\n-two\n three\n four\n five\n",
    ),
    (
        "multi-traditional-tabs",
        "--- a/one.ts\t\n+++ b/one.ts\t\n@@ -1,2 +1 @@\n context\n-old\n"
        "--- a/two.ts\t\n+++ b/two.ts\t\n@@ -1,2 +1 @@\n context\n-old\n",
    ),
]


@pytest.mark.parametrize("packet", VALID_MULTI_FILE_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff = packet
    patches = _parse_review_patches(diff)
    assert _diff_literal_complete(diff, patches) and not _requires_l1_excerpts(diff)
    resolved = ResolvedReview([], None, diff, "git")
    payload = {"findings": [], "code_excerpts": []}
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        findings, excerpts, *_ = provider()
    assert provider.attempted_excerpts == []
    digest = compute_source_hash(git_diff=diff)
    files = parse_diff_files(diff)
    paths = write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        findings,
        digest,
        [],
        tmp_path,
        diff_files=files,
        diff_text=diff,
        reviewer_excerpts=excerpts,
        attempted_excerpts=provider.attempted_excerpts,
        manifest="declared",
    )
    assert [json.loads(path.read_text())["pass_status"] for path in paths] == ["completed"] * 3
    result = run_verify(
        tmp_path,
        digest,
        files,
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert result.passed and result.checks_passed == result.checks_run


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a/", "b/"),
        ("b/", "a/"),
        ("", ""),
        ("i/", "w/"),
        ("w/", "i/"),
        ("c/", "w/"),
        ("w/", "c/"),
        ("c/", "i/"),
        ("i/", "c/"),
        ("o/", "w/"),
        ("w/", "o/"),
    ],
)
@pytest.mark.parametrize("quoted", [False, True])
def test_documented_prefixes_preserve_literal_owned_paths(old, new, quoted):
    source = '"a/old\\t.ts"' if quoted else "a/old.ts"
    target = '"b/new\\t.ts"' if quoted else "b/new.ts"
    before = '"' + old + source[1:] if quoted else old + source
    after = '"' + new + target[1:] if quoted else new + target
    diff = (
        f"diff --git {before} {after}\nsimilarity index 71%\nrename from {source}\n"
        f"rename to {target}\nindex 4cb29ea..4c7442b 100644\n--- {before}\t\n"
        f"+++ {after}\t\n@@ -1,3 +1,2 @@\n one\n-two\n three\n"
    )
    assert not _requires_l1_excerpts(diff)
    assert _requires_l1_excerpts(diff.replace(f"+++ {after}", "+++ b/foreign.ts"))
    assert _requires_l1_excerpts(diff.replace(f"rename from {source}", "rename from foreign.ts"))


@pytest.mark.parametrize("packet", VALID_MULTI_FILE_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("damage", ["leading", "trailing", "required-sibling", "null-target"])
def test_valid_packet_framing_keeps_corruption_and_required_siblings_visible(packet, damage):
    _, diff = packet
    if damage == "leading":
        diff = "unconsumed prefix\n" + diff
    elif damage == "trailing":
        diff += "unconsumed trailing text\n"
    elif damage == "required-sibling":
        diff += diff_for("required.ts")
    else:
        diff += R8_RETAINED_GIT_NULL
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("header", ["other text a/file.ts b/file.ts", 'diff --git "a/x "b/x'])
def test_same_git_header_refuses_missing_marker_and_unclosed_quotes(header):
    from code_forge.reviewer_json import _same_git_header_paths

    assert _same_git_header_paths(header) == ("", "", "")


NULL_SENTINEL_PACKETS = [
    ("no-prefix-mode", "diff --git /dev/null /dev/null\nold mode 100644\nnew mode 100755\n"),
    ("quoted-mode", 'diff --git "/dev/null" "/dev/null"\nold mode 100644\nnew mode 100755\n'),
    (
        "escaped-mode",
        'diff --git "/dev/nul\\154" "/dev/nul\\154"\nold mode 100644\nnew mode 100755\n',
    ),
    ("default-prefix-mode", "diff --git a//dev/null b//dev/null\nold mode 100644\nnew mode 100755\n"),
    ("mnemonic-mode", "diff --git i//dev/null w//dev/null\nold mode 100644\nnew mode 100755\n"),
    (
        "no-prefix-empty-delete",
        "diff --git /dev/null /dev/null\ndeleted file mode 100644\nindex e69de29..0000000\n",
    ),
    (
        "quoted-empty-delete",
        'diff --git "/dev/null" "/dev/null"\ndeleted file mode 100644\nindex e69de29..0000000\n',
    ),
    (
        "escaped-empty-delete",
        'diff --git "/dev/nul\\154" "/dev/nul\\154"\ndeleted file mode 100644\nindex e69de29..0000000\n',
    ),
]


@pytest.mark.parametrize("packet", NULL_SENTINEL_PACKETS, ids=lambda item: item[0])
def test_null_sentinel_is_not_a_header_file_witness(packet):
    from code_forge.reviewer_json import _same_git_header_paths

    _, diff = packet
    assert _same_git_header_paths(diff.splitlines()[0]) == ("", "", "")
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", NULL_SENTINEL_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_null_sentinel_refuses_published_receipt_completion(tmp_path, packet, publisher):
    from code_forge.verify import _validate_receipt_schema

    _, diff = packet
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([], None, diff, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        findings, excerpts, *_ = provider()
    digest = compute_source_hash(git_diff=diff)
    files = parse_diff_files(diff)
    paths = write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        findings,
        digest,
        [],
        tmp_path,
        diff_files=files,
        diff_text=diff,
        reviewer_excerpts=excerpts,
        attempted_excerpts=provider.attempted_excerpts,
        manifest="declared",
    )
    receipts = [json.loads(path.read_text()) for path in paths]
    for path, receipt in zip(paths, receipts, strict=True):
        _validate_receipt_schema(receipt, str(path))
    result = run_verify(
        tmp_path,
        digest,
        files,
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed
    assert [receipt["pass_status"] for receipt in receipts] == ["incomplete"] * 3
    assert len(provider.attempted_excerpts) == 3


@pytest.mark.parametrize("packet", NULL_SENTINEL_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_null_sentinel_refuses_public_machine(tmp_path, packet, publisher, mode):
    _, diff = packet
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([], None, diff, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, [])
        assert machine.run() == Verdict.FAIL
    assert len(provider.attempted_excerpts) == 3
    disk = json.loads((tmp_path / ".code-forge/state.json").read_text())
    assert disk["verdict"] == "FAIL" and not disk["converged"]
    assert len(list((tmp_path / ".code-forge/receipts/attempted").glob("*.json"))) == 3
    assert [
        json.loads(path.read_text())["pass_status"]
        for path in sorted((tmp_path / ".code-forge/receipts").glob("receipt-*.json"))
    ] == ["incomplete"] * 3
    assert machine._receipt_gate_round_errors() and machine._receipt_gate_terminal_errors()


@pytest.mark.parametrize("operation", ["mode", "empty-delete"])
@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a/", "b/"),
        ("b/", "a/"),
        ("", ""),
        ("i/", "w/"),
        ("w/", "i/"),
        ("c/", "w/"),
        ("w/", "c/"),
        ("c/", "i/"),
        ("i/", "c/"),
        ("o/", "w/"),
        ("w/", "o/"),
    ],
)
def test_relative_dev_null_keeps_literal_file_identity(old, new, quoted, operation):
    from code_forge.reviewer_json import _same_git_header_paths

    source, target = old + "dev/null", new + "dev/null"
    if quoted:
        source, target = '"' + source + '"', '"' + target + '"'
    metadata = (
        "old mode 100644\nnew mode 100755\n"
        if operation == "mode"
        else "deleted file mode 100644\nindex e69de29..0000000\n"
    )
    diff = f"diff --git {source} {target}\n" + metadata
    assert _same_git_header_paths(diff.splitlines()[0]) == (source, target, "dev/null")
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("operation", ["mode", "quoted-mode", "empty-delete"])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
@pytest.mark.parametrize("mode", [Mode.CI, Mode.LOCAL])
def test_relative_dev_null_preserves_public_machine(tmp_path, operation, publisher, mode):
    source = '"dev/null"' if operation == "quoted-mode" else "dev/null"
    metadata = (
        "deleted file mode 100644\nindex e69de29..0000000\n"
        if operation == "empty-delete"
        else "old mode 100644\nnew mode 100755\n"
    )
    diff = f"diff --git {source} {source}\n" + metadata
    payload = {"findings": [], "code_excerpts": []}
    resolved = ResolvedReview([Path("dev/null")], None, diff, "git")
    (tmp_path / "dev").mkdir()
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        machine = machine_for(tmp_path, mode, provider, diff, ["dev/null"])
        if operation == "empty-delete":
            (tmp_path / "dev/null").unlink()
        else:
            (tmp_path / "dev/null").chmod(0o755)
        assert machine.run() == Verdict.PASS
    assert provider.attempted_excerpts == []
    assert machine._receipt_gate_round_errors() == []
    assert machine._receipt_gate_terminal_errors() == []


NULL_METADATA_AND_TARGET_PACKETS = [
    (
        "rename-source-bare-none",
        "diff --git /dev/null new.ts\nsimilarity index 100%\nrename from /dev/null\nrename to new.ts\n",
    ),
    (
        "rename-source-bare-default",
        "diff --git a//dev/null b/new.ts\n"
        "similarity index 100%\n"
        "rename from /dev/null\n"
        "rename to new.ts\n",
    ),
    (
        "rename-source-quoted-none",
        'diff --git "/dev/null" new.ts\n'
        "similarity index 100%\n"
        'rename from "/dev/null"\n'
        "rename to new.ts\n",
    ),
    (
        "rename-source-quoted-default",
        'diff --git "a//dev/null" b/new.ts\n'
        "similarity index 100%\n"
        'rename from "/dev/null"\n'
        "rename to new.ts\n",
    ),
    (
        "rename-source-octal-none",
        'diff --git "/dev/nul\\154" new.ts\n'
        "similarity index 100%\n"
        'rename from "/dev/nul\\154"\n'
        "rename to new.ts\n",
    ),
    (
        "rename-source-octal-default",
        'diff --git "a//dev/nul\\154" b/new.ts\n'
        "similarity index 100%\n"
        'rename from "/dev/nul\\154"\n'
        "rename to new.ts\n",
    ),
    (
        "rename-target-bare-none",
        "diff --git old.ts /dev/null\nsimilarity index 100%\nrename from old.ts\nrename to /dev/null\n",
    ),
    (
        "rename-target-bare-default",
        "diff --git a/old.ts b//dev/null\n"
        "similarity index 100%\n"
        "rename from old.ts\n"
        "rename to /dev/null\n",
    ),
    (
        "rename-target-quoted-none",
        'diff --git old.ts "/dev/null"\n'
        "similarity index 100%\n"
        "rename from old.ts\n"
        'rename to "/dev/null"\n',
    ),
    (
        "rename-target-quoted-default",
        'diff --git a/old.ts "b//dev/null"\n'
        "similarity index 100%\n"
        "rename from old.ts\n"
        'rename to "/dev/null"\n',
    ),
    (
        "rename-target-octal-none",
        'diff --git old.ts "/dev/nul\\154"\n'
        "similarity index 100%\n"
        "rename from old.ts\n"
        'rename to "/dev/nul\\154"\n',
    ),
    (
        "rename-target-octal-default",
        'diff --git a/old.ts "b//dev/nul\\154"\n'
        "similarity index 100%\n"
        "rename from old.ts\n"
        'rename to "/dev/nul\\154"\n',
    ),
    (
        "copy-source-bare-none",
        "diff --git /dev/null new.ts\nsimilarity index 100%\ncopy from /dev/null\ncopy to new.ts\n",
    ),
    (
        "copy-source-bare-default",
        "diff --git a//dev/null b/new.ts\nsimilarity index 100%\ncopy from /dev/null\ncopy to new.ts\n",
    ),
    (
        "copy-source-quoted-none",
        'diff --git "/dev/null" new.ts\nsimilarity index 100%\ncopy from "/dev/null"\ncopy to new.ts\n',
    ),
    (
        "copy-source-quoted-default",
        'diff --git "a//dev/null" b/new.ts\n'
        "similarity index 100%\n"
        'copy from "/dev/null"\n'
        "copy to new.ts\n",
    ),
    (
        "copy-source-octal-none",
        'diff --git "/dev/nul\\154" new.ts\n'
        "similarity index 100%\n"
        'copy from "/dev/nul\\154"\n'
        "copy to new.ts\n",
    ),
    (
        "copy-source-octal-default",
        'diff --git "a//dev/nul\\154" b/new.ts\n'
        "similarity index 100%\n"
        'copy from "/dev/nul\\154"\n'
        "copy to new.ts\n",
    ),
    (
        "copy-target-bare-none",
        "diff --git old.ts /dev/null\nsimilarity index 100%\ncopy from old.ts\ncopy to /dev/null\n",
    ),
    (
        "copy-target-bare-default",
        "diff --git a/old.ts b//dev/null\nsimilarity index 100%\ncopy from old.ts\ncopy to /dev/null\n",
    ),
    (
        "copy-target-quoted-none",
        'diff --git old.ts "/dev/null"\nsimilarity index 100%\ncopy from old.ts\ncopy to "/dev/null"\n',
    ),
    (
        "copy-target-quoted-default",
        'diff --git a/old.ts "b//dev/null"\n'
        "similarity index 100%\n"
        "copy from old.ts\n"
        'copy to "/dev/null"\n',
    ),
    (
        "copy-target-octal-none",
        'diff --git old.ts "/dev/nul\\154"\n'
        "similarity index 100%\n"
        "copy from old.ts\n"
        'copy to "/dev/nul\\154"\n',
    ),
    (
        "copy-target-octal-default",
        'diff --git a/old.ts "b//dev/nul\\154"\n'
        "similarity index 100%\n"
        "copy from old.ts\n"
        'copy to "/dev/nul\\154"\n',
    ),
    ("retained-target-bare", "--- a/file.ts\n+++ /dev/null\n@@ -1,2 +1,1 @@\n-old\n retained\n"),
    ("retained-target-quoted", '--- a/file.ts\n+++ "/dev/null"\n@@ -1,2 +1,1 @@\n-old\n retained\n'),
    ("retained-target-octal", '--- a/file.ts\n+++ "/dev/nul\\154"\n@@ -1,2 +1,1 @@\n-old\n retained\n'),
]

LEGAL_RELATIVE_NULL_METADATA_PACKETS = [
    (
        "rename-source-relative",
        "diff --git dev/null new.ts\nsimilarity index 100%\nrename from dev/null\nrename to new.ts\n",
    ),
    (
        "rename-target-relative",
        "diff --git old.ts dev/null\nsimilarity index 100%\nrename from old.ts\nrename to dev/null\n",
    ),
    (
        "copy-source-relative",
        "diff --git dev/null new.ts\nsimilarity index 100%\ncopy from dev/null\ncopy to new.ts\n",
    ),
    (
        "copy-target-relative",
        "diff --git old.ts dev/null\nsimilarity index 100%\ncopy from old.ts\ncopy to dev/null\n",
    ),
]


@pytest.mark.parametrize("packet", NULL_METADATA_AND_TARGET_PACKETS, ids=lambda item: item[0])
def test_null_sentinel_metadata_or_target_requires_evidence(packet):
    assert _requires_l1_excerpts(packet[1])


@pytest.mark.parametrize("packet", NULL_METADATA_AND_TARGET_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_null_sentinel_metadata_or_target_refuses_published_receipts(tmp_path, packet, publisher):
    test_null_sentinel_refuses_published_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("packet", LEGAL_RELATIVE_NULL_METADATA_PACKETS, ids=lambda item: item[0])
def test_relative_dev_null_metadata_preserves_exemption(packet):
    assert not _requires_l1_excerpts(packet[1])


@pytest.mark.parametrize("packet", LEGAL_RELATIVE_NULL_METADATA_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_relative_dev_null_metadata_preserves_published_receipts(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_truncated_headers_preserve_incomplete_finding_only_pass(tmp_path, publisher):
    from code_forge.verify import _validate_receipt_schema

    diff = "diff --git a/f b/f\n@@ -0,0 +0,0 @@"
    payload = {
        "findings": [{"file": "f", "line": 1, "severity": "P2", "description": "candidate"}],
        "code_excerpts": [],
    }
    resolved = ResolvedReview([Path("f")], None, diff, "git")
    with patch("code_forge.llm_invoke.llm_invoke", return_value=LLMResult(payload, Usage(), 0)):
        provider = metadata_provider(publisher, resolved, payload)
        findings, excerpts, *_ = provider()
    assert findings and all(finding.source == "UNTRUSTED" for finding in findings)
    assert excerpts == [] and len(provider.attempted_excerpts) == 3
    digest = compute_source_hash(git_diff=diff)
    files = parse_diff_files(diff)
    paths = write_receipts(
        tmp_path / ".code-forge/receipts",
        0,
        findings,
        digest,
        [],
        tmp_path,
        diff_files=files,
        diff_text=diff,
        reviewer_excerpts=excerpts,
        attempted_excerpts=provider.attempted_excerpts,
        manifest="declared",
    )
    receipts = [json.loads(path.read_text()) for path in paths]
    for path, receipt in zip(paths, receipts, strict=True):
        _validate_receipt_schema(receipt, str(path))
    assert [receipt["pass_status"] for receipt in receipts] == ["incomplete"] * 3
    result = run_verify(
        tmp_path,
        digest,
        files,
        diff_text=diff,
        required_cycles=1,
        cycles=[1],
        respect_floor=False,
        require_convergence=False,
    )
    assert not result.passed and "anchor file f not in diff" in result.reason


SUPPORTED_FRAMING_PACKETS = [
    ("binary-marker-name", ENCODED_BINARY.replace("control.bin", "GIT binary patch")),
    ("binary-marker-directory", ENCODED_BINARY.replace("control.bin", "folder/GIT binary patch")),
    ("binary-marker-prefix", ENCODED_BINARY.replace("control.bin", "prefix GIT binary patch")),
    ("binary-marker-suffix", ENCODED_BINARY.replace("control.bin", "GIT binary patch suffix")),
    (
        "rename-crlf",
        "diff --git a/f b/g\r\nsimilarity index 100%\r\nrename from f\r\nrename to g\r\n",
    ),
    (
        "deletion-crlf",
        "diff --git a/f b/f\r\ndeleted file mode 100644\r\nindex 1234567..0000000\r\n"
        "--- a/f\r\n+++ /dev/null\r\n@@ -1 +0,0 @@\r\n-old\r\n",
    ),
]


@pytest.mark.parametrize("packet", SUPPORTED_FRAMING_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_supported_framing_preserves_published_receipt_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize(
    ("packet", "damage"),
    [
        (packet, damage)
        for packet in SUPPORTED_FRAMING_PACKETS
        for damage in ("leading", "trailing", "required-sibling", "null-target")
        if damage != "trailing" or not packet[0].startswith("binary-")
    ],
)
def test_supported_framing_keeps_required_or_corrupt_siblings_visible(packet, damage):
    test_valid_packet_framing_keeps_corruption_and_required_siblings_visible(packet, damage)


def _transport_header_packet(packet, ending, tabs):
    name, diff = packet
    lines = []
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            line = line.rstrip("\t")
            if tabs & (1 if line.startswith("--- ") else 2):
                line += "\t"
        lines.append(line)
    return f"{name}-{ending.encode().hex()}-{tabs}", ending.join(lines) + ending


TRANSPORT_HEADER_PACKETS = [
    _transport_header_packet(packet, ending, tabs)
    for packet in [(row[0], row[1]) for row in R8_GIT_PACKETS] + VALID_MULTI_FILE_PACKETS
    for ending in ("\n", "\r\n", "\r")
    for tabs in range(4)
]


@pytest.mark.parametrize("packet", TRANSPORT_HEADER_PACKETS, ids=lambda item: item[0])
def test_supported_transport_keeps_direct_literal_consumer_complete(packet):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff = packet
    canonical = diff.replace("\r\n", "\n").replace("\r", "\n")
    assert _diff_literal_complete(diff, _parse_review_patches(canonical))
    assert _diff_literal_complete(diff, _parse_review_patches(diff))
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", TRANSPORT_HEADER_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_supported_transport_preserves_public_receipt_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("packet", TRANSPORT_HEADER_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("damage", ["leading", "trailing", "required-sibling", "null-target"])
def test_supported_transport_keeps_corrupt_and_required_siblings_visible(packet, damage):
    test_valid_packet_framing_keeps_corruption_and_required_siblings_visible(packet, damage)


MIXED_TEXT_ORDERS = [
    ("plain",),
    ("git",),
    ("plain", "plain"),
    ("git", "git"),
    ("plain", "git"),
    ("git", "plain"),
    ("plain", "git", "plain"),
    ("git", "plain", "git"),
    ("plain", "plain", "git"),
    ("git", "git", "plain"),
    ("plain", "git", "git"),
    ("git", "plain", "plain"),
]


def _mixed_text_packet(order, additions, ending, tabs):
    sections = []
    for index, style in enumerate(order):
        file = f"file-{index}.ts"
        prefix = f"diff --git a/{file} b/{file}\nindex 1234567..7654321 100644\n"
        source = "\t" if tabs & 1 else ""
        target = "\t" if tabs & 2 else ""
        body = "@@ -1 +1 @@\n-old\n+new\n" if index in additions else "@@ -1,2 +1 @@\n context\n-old\n"
        section = f"--- a/{file}{source}\n+++ b/{file}{target}\n{body}"
        sections.append((prefix if style == "git" else "") + section)
    name = f"{'-'.join(order)}-{','.join(map(str, additions))}-{ending.encode().hex()}-{tabs}"
    return name, "".join(sections).replace("\n", ending), bool(additions)


MIXED_TEXT_PACKETS = [
    _mixed_text_packet(order, additions, ending, tabs)
    for order in MIXED_TEXT_ORDERS
    for additions in dict.fromkeys([(), *[(i,) for i in range(len(order))], tuple(range(len(order)))])
    for ending in ("\n", "\r\n", "\r")
    for tabs in range(4)
]


@pytest.mark.parametrize("packet", MIXED_TEXT_PACKETS, ids=lambda item: item[0])
def test_mixed_text_framing_preserves_each_literal_file_and_applicability(packet):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff, requires = packet
    assert _diff_literal_complete(diff, _parse_review_patches(diff))
    assert _requires_l1_excerpts(diff) is requires


@pytest.mark.parametrize("packet", [p for p in MIXED_TEXT_PACKETS if not p[2]], ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_mixed_deletions_preserve_public_receipt_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet[:2], publisher)


@pytest.mark.parametrize("packet", [p for p in MIXED_TEXT_PACKETS if not p[2]], ids=lambda item: item[0])
@pytest.mark.parametrize("damage", ["leading", "trailing", "required-sibling", "null-target"])
def test_mixed_deletions_preserve_corrupt_or_required_siblings(packet, damage):
    test_valid_packet_framing_keeps_corruption_and_required_siblings_visible(packet[:2], damage)


HEADER_SHAPED_CONTENT_PACKETS = [
    (
        f"header-content-{'-'.join(order)}-{ending.encode().hex()}-{tabs}",
        _mixed_text_packet(order, (), "\n", tabs)[1]
        .replace(
            "@@ -1,2 +1 @@\n context\n-old\n",
            "@@ -1,2 +1,2 @@\n--- a/header-shaped.ts\t\n+++ b/header-shaped.ts\t\n context\n",
            1,
        )
        .replace("\n", ending),
        True,
    )
    for order in MIXED_TEXT_ORDERS
    for ending in ("\n", "\r\n", "\r")
    for tabs in range(4)
]


@pytest.mark.parametrize("packet", HEADER_SHAPED_CONTENT_PACKETS, ids=lambda item: item[0])
def test_header_shaped_hunk_content_preserves_real_file_boundaries(packet):
    test_mixed_text_framing_preserves_each_literal_file_and_applicability(packet)


def test_opaque_binary_section_does_not_claim_following_traditional_hunk():
    plain = _mixed_text_packet(("plain",), (), "\n", 3)[1]
    assert _requires_l1_excerpts(ENCODED_BINARY + plain)
    assert not _requires_l1_excerpts(plain + ENCODED_BINARY)


DECLARED_EMPTY_PREFIX_PAIRS = (
    ("a/", "b/"),
    ("b/", "a/"),
    ("", ""),
    ("i/", "w/"),
    ("w/", "i/"),
    ("c/", "w/"),
    ("w/", "c/"),
    ("c/", "i/"),
    ("i/", "c/"),
    ("o/", "w/"),
    ("w/", "o/"),
    ("1/", "2/"),
    ("2/", "1/"),
)


def empty_prefix_packet(old, new, quoted, removed, ending):
    raw = '"empty\\t.ts"' if quoted else "empty.ts"
    source = '"' + old + raw[1:] if quoted else old + raw
    target = '"' + new + raw[1:] if quoted else new + raw
    change = "deleted" if removed else "new"
    index = "e69de29..0000000" if removed else "0000000..e69de29"
    diff = f"diff --git {source} {target}\n{change} file mode 100644\nindex {index}\n"
    name = f"{old or 'none'}-{new or 'none'}-{quoted}-{removed}-{repr(ending)}".replace("/", "_")
    return name, diff.replace("\n", ending)


EMPTY_NATIVE_PREFIX_PACKETS = [
    empty_prefix_packet(old, new, quoted, removed, ending)
    for old, new in DECLARED_EMPTY_PREFIX_PAIRS
    for quoted in (False, True)
    for removed in (False, True)
    for ending in ("\n", "\r\n", "\r")
]
NO_INDEX_EMPTY_PACKETS = [
    empty_prefix_packet(old, new, quoted, removed, ending)
    for old, new in (("1/", "2/"), ("2/", "1/"))
    for quoted in (False, True)
    for removed in (False, True)
    for ending in ("\n", "\r\n", "\r")
]


@pytest.mark.parametrize("packet", EMPTY_NATIVE_PREFIX_PACKETS, ids=lambda item: item[0])
def test_empty_native_prefix_family_needs_no_source_excerpts(packet):
    _, diff = packet
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", NO_INDEX_EMPTY_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_empty_no_index_packets_preserve_public_pass_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("packet", NO_INDEX_EMPTY_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize(
    "damage", ["different-target", "required-sibling", "null-device", "custom-prefix"]
)
def test_empty_no_index_exemption_stays_bound_to_literal_native_identity(packet, damage):
    _, diff = packet
    if damage == "different-target":
        diff = (
            diff.replace("2/empty", "2/foreign", 1)
            if "2/empty" in diff
            else diff.replace("1/empty", "1/foreign", 1)
        )
    elif damage == "required-sibling":
        diff += diff_for("required.ts")
    elif damage == "null-device":
        diff = diff.replace("empty\\t.ts", "/dev/null").replace("empty.ts", "/dev/null")
    else:
        diff = diff.replace("1/", "before/").replace("2/", "after/")
    assert _requires_l1_excerpts(diff)


SUPPRESSED_CONTEXT_HUNKS = {
    "after": "@@ -1,2 +1 @@\n-old\n\n",
    "before": "@@ -1,2 +1 @@\n\n-old\n",
    "between": "@@ -1,3 +1 @@\n-left\n\n-right\n",
    "repeated": "@@ -1,3 +1,2 @@\n-old\n\n\n",
    "mixed": "@@ -1,3 +1,2 @@\n-old\n \n\n",
    "prefixed": "@@ -1,2 +1 @@\n-old\n \n",
}


def _suppressed_context_packet(frame, hunk, ending):
    git = "diff --git a/value.py b/value.py\nindex 1111111..2222222 100644\n"
    git += "--- a/value.py\n+++ b/value.py\n"
    plain = "--- pkg/plain.py\n+++ pkg/plain.py\n"
    companion = "@@ -1 +0,0 @@\n-companion\n"
    diff = {
        "git": git + hunk,
        "plain": plain + hunk,
        "git-plain": git + hunk + plain + companion,
        "plain-git": plain + hunk + git + companion,
    }[frame]
    return diff.replace("\n", ending)


SUPPRESSED_CONTEXT_PACKETS = [
    (
        f"{frame}-{layout}-{transport}",
        _suppressed_context_packet(frame, hunk, ending),
    )
    for frame in ("git", "plain", "git-plain", "plain-git")
    for layout, hunk in SUPPRESSED_CONTEXT_HUNKS.items()
    for transport, ending in (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r"))
]


@pytest.mark.parametrize("packet", SUPPRESSED_CONTEXT_PACKETS, ids=lambda item: item[0])
def test_suppressed_blank_context_keeps_literal_consumption(packet):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff = packet
    patches = _parse_review_patches(diff)
    assert _diff_literal_complete(diff, patches)
    assert _diff_literal_complete(diff, _parse_review_patches(str(patches)))
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", SUPPRESSED_CONTEXT_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_suppressed_blank_context_preserves_public_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("packet", SUPPRESSED_CONTEXT_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize(
    "damage", ["count", "overflow", "outside-blank", "outside-garbage", "added-blank", "required-source"]
)
def test_suppressed_blank_context_keeps_required_or_unconsumed_data_visible(packet, damage):
    import re

    _, diff = packet
    diff = diff.replace("\r\n", "\n").replace("\r", "\n")
    if damage == "count":
        diff = re.sub(r"(?m)^(@@ -[0-9]+)(?:,[0-9]+)?", r"\1,999", diff, count=1)
    elif damage == "overflow":
        diff = re.sub(r"(?m)^(@@ [^\n]*\n)", r"\1\n", diff, count=1)
    elif damage == "outside-blank":
        diff += "\n"
    elif damage == "outside-garbage":
        diff += "unconsumed packet data\n"
    elif damage == "added-blank":
        diff += "--- /dev/null\n+++ pkg/blank.py\n@@ -0,0 +1 @@\n+\n"
    else:
        diff += "--- /dev/null\n+++ pkg/source.py\n@@ -0,0 +1 @@\n+required = 1\n"
    assert _requires_l1_excerpts(diff)


@pytest.mark.parametrize("position", [None, 0, 999])
def test_suppressed_blank_context_must_match_parser_owned_position(position):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    diff = "--- pkg/value.py\n+++ pkg/value.py\n@@ -1,2 +1 @@\n-old\n\n"
    patches = _parse_review_patches(diff)
    context = next(line for pf in patches for hunk in pf for line in hunk if line.is_context)
    context.diff_line_no = position
    assert not _diff_literal_complete(diff, patches)


LF_CONTENT_SEPARATORS = ("\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
LF_CONTENT_PACKETS = [
    (
        f"{frame}-{layout}-{transport}-{ord(separator)}",
        _suppressed_context_packet(
            frame,
            hunk.replace("-old\n", f"-int{separator}value;\n")
            .replace("-left\n", f"-left{separator}value;\n")
            .replace("-right\n", f"-right{separator}value;\n"),
            ending,
        ),
    )
    for separator in LF_CONTENT_SEPARATORS
    for frame in ("git", "plain", "git-plain", "plain-git")
    for layout, hunk in SUPPRESSED_CONTEXT_HUNKS.items()
    for transport, ending in (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r"))
]


@pytest.mark.parametrize("packet", LF_CONTENT_PACKETS, ids=lambda item: item[0])
def test_source_content_separators_preserve_diff_record_positions(packet):
    from code_forge.reviewer_json import _diff_literal_complete, _parse_review_patches

    _, diff = packet
    patches = _parse_review_patches(diff)
    assert _diff_literal_complete(diff, patches)
    assert not _requires_l1_excerpts(diff)


@pytest.mark.parametrize("packet", LF_CONTENT_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize("publisher", ["ordinary", "grouped", "outlet"])
def test_source_content_separators_preserve_public_completion(tmp_path, packet, publisher):
    test_valid_git_and_traditional_packets_preserve_receipt_completion(tmp_path, packet, publisher)


@pytest.mark.parametrize("packet", LF_CONTENT_PACKETS, ids=lambda item: item[0])
@pytest.mark.parametrize(
    "damage", ["count", "overflow", "outside-blank", "outside-garbage", "added-blank", "required-source"]
)
def test_source_content_separators_keep_corruption_or_required_source_visible(packet, damage):
    test_suppressed_blank_context_keeps_required_or_unconsumed_data_visible(packet, damage)


CROSS_REPO_RAW_DELETION = (
    "diff --git a/src/value.py b/src/value.py\n"
    "index 81e7988..8b13789 100644\n"
    "--- a/src/value.py\n+++ b/src/value.py\n"
    "@@ -1,2 +1 @@\n-old\n \n"
)

CROSS_REPO_RAW_SCOPES = [
    ("deletion-empty", {"primary": CROSS_REPO_RAW_DELETION, "peer": ""}, False),
    ("empty-deletion", {"primary": "", "peer": CROSS_REPO_RAW_DELETION}, False),
    ("two-deletions", {"primary": CROSS_REPO_RAW_DELETION, "peer": CROSS_REPO_RAW_DELETION}, False),
    ("deletion-required", {"primary": CROSS_REPO_RAW_DELETION, "peer": diff_for("value.py")}, True),
    ("required-deletion", {"primary": diff_for("value.py"), "peer": CROSS_REPO_RAW_DELETION}, True),
    ("binary-empty", {"primary": ENCODED_BINARY, "peer": ""}, False),
    ("deletion-malformed", {"primary": CROSS_REPO_RAW_DELETION, "peer": "+++ b/value.py\n"}, True),
]


def cross_repo_narrated_context(repositories):
    from code_forge.cross_repo import build_cross_repo_context
    from code_forge.receipt_scope import repository_scope

    return build_cross_repo_context(
        [
            {"label": label, "ref": "HEAD", "diff": repository_scope({label: diff})[0]}
            for label, diff in repositories.items()
        ]
    ) + (
        "Cross-repo evidence: use exact qualified file paths in the diff "
        "in BOTH findings and code_excerpts. Each path pins repository and "
        "reviewed source version. Never strip the label@hash/ prefix.\n"
    )


@pytest.mark.parametrize("packet", CROSS_REPO_RAW_SCOPES)
def test_cross_repo_applicability_uses_each_authoritative_raw_scope(packet):
    _, repositories, required = packet
    context = cross_repo_narrated_context(repositories)
    assert _requires_l1_excerpts(context)
    assert _requires_l1_excerpts(context, reviewed_repositories=repositories) is required


@pytest.mark.parametrize("repositories", [{}, [], {"primary": None}, {"bad label": ""}])
def test_cross_repo_invalid_scope_cannot_prove_exemption(repositories):
    from code_forge.reviewer_json import MissingExcerptEvidenceError, require_l1_excerpt_evidence

    assert _requires_l1_excerpts("", reviewed_repositories=repositories)
    with pytest.raises(MissingExcerptEvidenceError):
        require_l1_excerpt_evidence(
            {"findings": [], "code_excerpts": []}, "", reviewed_repositories=repositories
        )


def test_cross_repo_empty_trusted_scope_and_absent_scope_are_distinct():
    context = cross_repo_narrated_context({"primary": "", "peer": ""})
    assert not _requires_l1_excerpts(context, reviewed_repositories={"primary": "", "peer": ""})
    assert _requires_l1_excerpts(context, reviewed_repositories=None)
    assert not _requires_l1_excerpts(CROSS_REPO_RAW_DELETION, reviewed_repositories=None)


@pytest.mark.parametrize(
    "packet", [packet for packet in CROSS_REPO_RAW_SCOPES if packet[0] != "binary-empty"]
)
@pytest.mark.parametrize("finding_only", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
def test_cross_repo_raw_scopes_reach_actual_receipts_and_verifier(
    tmp_path, packet, finding_only, as_text
):
    from code_forge.receipt_scope import repository_scope

    _, repositories, required = packet
    diff = repositories["primary"]
    qualified, _ = repository_scope(repositories)
    scoped_file = next(iter(parse_diff_files(qualified)))
    payload = {
        "findings": [{"file": scoped_file, "line": 1, "severity": "P2", "description": "candidate"}]
        if finding_only
        else [],
        "code_excerpts": [],
        "reviewed_repositories": {"primary": ""},
    }
    resolved = ResolvedReview([], None, cross_repo_narrated_context(repositories), "git")

    def transport(*args, **kwargs):
        response = json.dumps(payload) if as_text else copy.deepcopy(payload)
        return LLMResult(response, Usage(), 0)

    with patch("code_forge.llm_invoke.llm_invoke", side_effect=transport):
        provider = build_l1_provider("auto", resolved, reviewed_repositories=repositories)
        findings, excerpts, *_ = provider()
    if finding_only and not required:
        assert findings and all(f.source == "L1" for f in findings)
    assert len(provider.attempted_excerpts) == (3 if required else 0)
    with patch("code_forge.git.read_diff_blob", return_value=None):
        paths = write_receipts(
            tmp_path / ".code-forge/receipts",
            0,
            findings,
            compute_source_hash(git_diff=diff),
            [],
            tmp_path,
            diff_text=diff,
            reviewer_excerpts=excerpts,
            attempted_excerpts=provider.attempted_excerpts,
            manifest="declared",
            reviewed_repositories=repositories,
        )
        statuses = [json.loads(path.read_text())["pass_status"] for path in paths]
        assert statuses == (["incomplete"] * 3 if required else ["completed"] * 3)
        result = run_verify(
            tmp_path,
            compute_source_hash(git_diff=diff),
            parse_diff_files(diff),
            diff_text=diff,
            required_cycles=1,
            cycles=[1],
            respect_floor=False,
            require_convergence=False,
            reviewed_repositories=repositories,
        )
    if required:
        assert not result.passed
    elif finding_only:
        assert not result.passed and result.reason.startswith("coverage 0%"), result.reason
    else:
        assert result.passed, result.reason
