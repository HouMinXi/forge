# SPDX-License-Identifier: Apache-2.0
"""Exact successful API text remains pending until physical receipt rejection."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

import pytest

from code_forge import factories, llm_invoke, receipt, reviewer_json, verify
from code_forge.autofix import StubAutoFixer
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.disposition import Disposition
from code_forge.falsify import StubFalsifier
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.machine import StateMachine, TimeoutBreaker
from code_forge.reviewer_json import GroupedReviewAttempt, ReviewGroupScope
from code_forge.source import compute_source_hash
from code_forge.state import Mode, StateFinding, Verdict

ROLES = ("qodo", "expert", "adversarial")
LINE = 'value = "quoted\\\\path";\n'
WRONG = 'value = "different literal";\n'


def _diff(file):
    return (f"diff --git a/{file} b/{file}\n--- a/{file}\n+++ b/{file}\n"
            "@@ -1 +1 @@\n-value = 1;\n+" + LINE)


def _envelope(file, content=LINE):
    return {"findings": [], "code_excerpts": [
        {"file": file, "start_line": 1, "end_line": 1, "content": content}]}


@pytest.fixture
def no_external(monkeypatch):
    active = [True]
    calls = Counter()

    def audit(event, args):
        if active[0] and event.startswith(("socket.connect", "socket.getaddrinfo", "socket.sendto",
                                          "subprocess.Popen", "os.system", "os.posix_spawn",
                                          "os.fork", "os.exec")):
            calls[event] += 1
            raise AssertionError("external operation refused: " + event)

    sys.addaudithook(audit)
    monkeypatch.setenv("WRITER_RAW_FIXTURE_KEY", "offline-fixture")
    yield calls
    active[0] = False
    assert calls == {}


def _case(tmp_path, monkeypatch, *, grouped=False, failed_role="expert", style="plain", rounds=1):
    files = ["control.txt", "other.txt"] if grouped else ["control.txt"]
    for file in files:
        (tmp_path / file).write_text(LINE)
    slices = [ResolvedReview([Path(file)], None, _diff(file), "git") for file in files]
    combined = "".join(item.git_diff for item in slices)
    control = {"failed_role": failed_role, "version": 1, "malformed_role": None}
    acquired, accepted, required, counts, physical = {}, Counter(), Counter(), Counter(), []
    schema_fn = reviewer_json.validate_reviewer_json
    required_fn = factories.require_l1_excerpt_evidence
    physical_fn = verify.validate_excerpts_against_diff

    def transport(prompt, *args, **kwargs):
        role_text = prompt.rsplit("You are a ", 1)[-1]
        role = "qodo" if "structural" in role_text else "expert" if "senior" in role_text else "adversarial"
        file = "other.txt" if "other.txt" in prompt else "control.txt"
        key = file + ":" + role
        counts[key] += 1
        content = WRONG if role == control["failed_role"] and file == "control.txt" else LINE
        data = _envelope(file, content) | {
            "identity": key, "version": control["version"], "lexical": "marker",
            "pass_name": "forged", "group_scope": "forged", "raw_response": "forged",
        }
        raw = "\n \t" + json.dumps(data, indent=3).replace(
            '"lexical": "marker"', '"lexical": "\\u006darker"') + "\r\n "
        if style == "fenced":
            raw = " \n" + chr(96) * 3 + "json\n" + raw + "\n" + chr(96) * 3 + "\t "
        elif style == "embedded":
            raw = "Review output follows.\n" + raw + "\nEnd of output."
        if role == control["malformed_role"]:
            raw = '{"findings":[true,,false]}'
        acquired[key] = raw
        return raw, {"prompt_tokens": 2, "completion_tokens": 3, "_forge_finish_reason": "stop"}

    def schema(raw):
        answer = schema_fn(raw)
        if sys._getframe(1).f_code.co_name == "_provider":
            accepted[answer["identity"]] += 1
        return answer

    def evidence(data, diff, **kwargs):
        result = required_fn(data, diff, **kwargs)
        required[data["identity"]] += 1
        return result

    def source(diff, excerpts, *, cwd=None):
        errors = physical_fn(diff, excerpts, cwd=cwd)
        if sys._getframe(1).f_code.co_name == "write_receipts":
            assert diff == combined and Path(cwd) == tmp_path
            physical.append(list(errors))
        return errors

    monkeypatch.setattr(llm_invoke, "_invoke_openai", transport)
    monkeypatch.setattr(reviewer_json, "validate_reviewer_json", schema)
    monkeypatch.setattr(factories, "require_l1_excerpt_evidence", evidence)
    monkeypatch.setattr(verify, "validate_excerpts_against_diff", source)
    backend = BackendConfig(name="offline", type="api", format="openai", model="fixture",
                            base_url="http://fixture.invalid", api_key_env="WRITER_RAW_FIXTURE_KEY")
    kwargs = dict(backend=backend, max_attempts=1, initial_delay_s=0, pass_stagger_s=0)
    provider = (
        factories.build_grouped_l1_provider("auto", [
            {"name": f"group-{i}", "resolved": item} for i, item in enumerate(slices)], **kwargs)
        if grouped else factories.build_l1_provider("auto", slices[0], **kwargs)
    )
    machine = StateMachine(mode=Mode.LOCAL, falsifier=StubFalsifier(), autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview([Path(f) for f in files], None, combined, "git"),
        source_hash=compute_source_hash(git_diff=combined), baseline_spec_repr="offline raw",
        cwd=tmp_path, source_root=tmp_path, registry={}, l0_runner=lambda *a: ([], []),
        l1_provider=provider, l2_runner=lambda *a, **kw: ([], []),
        e2e_runner=lambda *a, **kw: ([], []), advisory_runners=[],
        max_total_rounds=rounds, clean_round_threshold=3)
    machine._state.env_manifest = {"tier": "declared"}
    return machine, provider, control, acquired, accepted, required, counts, physical


def _read(tmp_path):
    root = tmp_path / ".code-forge/receipts"
    return (
        [json.loads(p.read_text()) for p in sorted(root.glob("receipt-*.json"))],
        [json.loads(p.read_text()) for p in sorted((root / "attempted").glob("*.json"))],
    )


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("style", ["plain", "fenced", "embedded"])
def test_writer_only_rejection_retains_exact_api_text(tmp_path, monkeypatch, no_external, grouped, role, style):
    machine, provider, _, raw, accepted, required, counts, physical = _case(
        tmp_path, monkeypatch, grouped=grouped, failed_role=role, style=style)
    assert machine.run() is Verdict.FAIL
    receipts, attempts = _read(tmp_path)
    assert len(receipts) == len(physical) == 3
    assert accepted == required == counts and sum(counts.values()) == (6 if grouped else 3)
    assert sum(bool(errors) for errors in physical) == 1
    failed = receipts[ROLES.index(role)]
    assert failed["pass_status"] == "schema_fail"
    assert failed["excerpt_validation_errors"] == next(errors for errors in physical if errors)
    assert all(r["pass_status"] == "completed" for r in receipts if r["pass"] != ROLES.index(role) + 1)
    assert machine._state.consecutive_clean_rounds == 0
    assert provider.attempted_excerpts == provider.raw_observations == []
    assert len(attempts) == (2 if grouped else 1)
    for index, artifact in enumerate(attempts):
        file = "other.txt" if index else "control.txt"
        assert artifact["payload"] == {"raw_response": raw[file + ":" + role], "pass_name": role}
        assert artifact["attempted"] is True and artifact["cycle"] == 1
        assert artifact["pass_name"] == role
        if grouped:
            assert artifact["group_scope"] == {
                "name": f"group-{index}", "diff_sha256": hashlib.sha256(_diff(file).encode()).hexdigest(),
                "source_files": [file]}
        else:
            assert "group_scope" not in artifact


@pytest.mark.parametrize("grouped", [False, True])
def test_pending_success_keeps_healthy_pass_and_no_artifacts(tmp_path, monkeypatch, no_external, grouped):
    machine, provider, _, _, accepted, required, counts, physical = _case(
        tmp_path, monkeypatch, grouped=grouped, failed_role=None, rounds=3)
    assert machine.run() is Verdict.PASS
    receipts, attempts = _read(tmp_path)
    assert len(receipts) == len(physical) == 9 and attempts == []
    assert accepted == required == counts and sum(counts.values()) == (18 if grouped else 9)
    assert all(r["pass_status"] == "completed" for r in receipts)
    assert len(provider.pending_raw_responses) == (6 if grouped else 3)
    assert provider.attempted_excerpts == provider.raw_observations == []


@pytest.mark.parametrize("grouped", [False, True])
def test_pending_reset_does_not_republish_old_text(tmp_path, monkeypatch, no_external, grouped):
    machine, provider, control, _, _, _, _, _ = _case(tmp_path, monkeypatch, grouped=grouped)
    assert machine.run() is Verdict.FAIL
    before = _read(tmp_path)[1]
    old = {item["raw_response"] for item in provider.pending_raw_responses}
    control.update(failed_role=None, version=2)
    machine._execute_round(1)
    assert not old.intersection(item["raw_response"] for item in provider.pending_raw_responses)
    assert machine._pending_raw_responses_last_round == provider.pending_raw_responses
    assert machine._pending_raw_responses_last_round is not provider.pending_raw_responses
    assert _read(tmp_path)[1] == before

    def fail():
        raise RuntimeError("acquisition stopped")

    monkeypatch.setattr(machine, "_run_l1_phase", fail)
    with pytest.raises(RuntimeError, match="acquisition stopped"):
        machine._execute_round(2)
    assert machine._pending_raw_responses_last_round == []


def test_empty_group_and_failed_call_reset_pending(no_external):
    call = factories.build_grouped_l1_provider("auto", [])
    call.pending_raw_responses.append({"raw_response": "old", "pass_name": "expert"})
    call()
    assert call.pending_raw_responses == []

    def fail(owner):
        raise RuntimeError("failed")

    single = factories._L1Call(fail)
    single.pending_raw_responses.append({"raw_response": "old", "pass_name": "expert"})
    with pytest.raises(RuntimeError, match="failed"):
        single()
    assert single.pending_raw_responses == []


def test_legacy_result_is_optional_private_diagnostic():
    result = LLMResult({"findings": []}, Usage(), 0.0, False)
    assert result.raw_response is None
    exact = LLMResult(result.content, raw_response="original text")
    assert result == exact and "original text" not in repr(exact)


def test_legacy_producer_missing_metadata_does_not_invent_original(tmp_path, monkeypatch, no_external):
    machine, _, _, _, _, _, _, _ = _case(tmp_path, monkeypatch)
    excerpts = [
        _envelope("control.txt", WRONG if role == "expert" else LINE)["code_excerpts"][0]
        | {"pass_name": role} for role in ROLES
    ]
    machine.l1_provider = lambda: ([], excerpts, Usage(), 0.0)
    assert machine.run() is Verdict.FAIL
    receipts, attempts = _read(tmp_path)
    assert receipts[1]["pass_status"] == "schema_fail" and attempts == []
    assert machine._pending_raw_responses_last_round == []


def test_terminal_publication_keeps_pending_and_existing_raw(tmp_path, monkeypatch, no_external):
    machine, _, control, raw, _, _, _, _ = _case(tmp_path, monkeypatch)
    control["malformed_role"] = "qodo"
    machine._state.rounds_with_failed_pass = 2
    with pytest.raises(TimeoutBreaker):
        machine.run()
    receipts, attempts = _read(tmp_path)
    assert [r["pass_status"] for r in receipts] == ["error", "schema_fail", "completed"]
    assert [a["pass_name"] for a in attempts] == ["qodo", "expert"]
    assert attempts[0]["payload"]["raw_response"] == raw["control.txt:qodo"]
    assert attempts[1]["payload"]["raw_response"] == raw["control.txt:expert"]


def test_repair_does_not_claim_synthetic_text(monkeypatch, no_external):
    backend = BackendConfig(name="offline", type="api", format="openai", model="fixture",
                            base_url="http://fixture.invalid", api_key_env="WRITER_RAW_FIXTURE_KEY")
    original = LLMResult({"findings": []}, raw_response=' { "findings": [] } ')
    monkeypatch.setattr(llm_invoke, "_invoke_api", lambda *a, **k: original)
    monkeypatch.setattr(llm_invoke, "_needs_excerpt_repair", lambda *a, **k: True)
    monkeypatch.setattr(llm_invoke, "_repair_missing_excerpts",
                        lambda *a, **k: (_envelope("control.txt"), Usage(), 0.0))
    assert llm_invoke.llm_invoke("fixture", backend).raw_response is None


def test_continuation_does_not_claim_a_fragment_as_original(monkeypatch, no_external):
    backend = BackendConfig(name="offline", type="api", format="openai", model="fixture",
                            base_url="http://fixture.invalid", api_key_env="WRITER_RAW_FIXTURE_KEY")
    fragments = iter(['{"findings": [],', '"code_excerpts": []}'])
    calls = []

    def transport(*args, **kwargs):
        fragment = next(fragments)
        calls.append(fragment)
        if len(calls) == 1:
            raise llm_invoke._TruncatedResponse("fixture cut", content=fragment,
                usage_data={"prompt_tokens": 1, "completion_tokens": 2}, resolved_cap=4096,
                kind="truncated", retryable=False)
        return fragment, {"prompt_tokens": 1, "completion_tokens": 2}

    monkeypatch.setattr(llm_invoke, "_invoke_openai", transport)
    result = llm_invoke.llm_invoke("fixture", backend, max_attempts=1, initial_delay_s=0)
    assert len(calls) == 2 and result.content == {"findings": [], "code_excerpts": []}
    assert result.is_truncated is True and result.raw_response is None


def test_successful_syntax_correction_carries_only_last_response(monkeypatch, no_external):
    backend = BackendConfig(name="offline", type="api", format="openai", model="fixture",
                            base_url="http://fixture.invalid", api_key_env="WRITER_RAW_FIXTURE_KEY")
    exact = ' \n { "findings" : [], "code_excerpts" : [] }\r\n'
    responses = iter(['{"findings":[true,,false]}', exact])
    calls = []

    def transport(*args, **kwargs):
        raw = next(responses)
        calls.append(raw)
        return raw, {"prompt_tokens": 1, "completion_tokens": 2, "_forge_finish_reason": "stop"}

    monkeypatch.setattr(llm_invoke, "_invoke_openai", transport)
    result = llm_invoke.llm_invoke("fixture", backend, max_attempts=2, initial_delay_s=0)
    assert len(calls) == 2 and result.content == {"findings": [], "code_excerpts": []}
    assert result.raw_response == exact


@pytest.mark.parametrize("status", ["schema_fail", "error", "timeout", "healthy", "no_diff"])
def test_writer_requires_its_own_physical_rejection(tmp_path, no_external, status):
    (tmp_path / "control.txt").write_text(LINE)
    findings = []
    if status in {"schema_fail", "error", "timeout"}:
        suffix = "schema-fail" if status == "schema_fail" else "invoke-fail"
        findings = [StateFinding(id="l1-expert-" + suffix, fingerprint="fixture", source="INFRA",
            disposition=Disposition.CONFIRMED, file="<fixture>", line_range=[0, 0],
            description="fixture", is_timeout=status == "timeout")]
    offered = [_envelope("control.txt", LINE if status == "healthy" else WRONG)["code_excerpts"][0]
               | {"pass_name": "expert"}]
    paths = receipt.write_receipts(tmp_path / "receipts", 0, findings, "fixture", [], tmp_path,
        diff_text=None if status == "no_diff" else _diff("control.txt"), reviewer_excerpts=offered,
        manifest={"tier": "declared"},
        pending_raw_responses=[{"raw_response": "exact received text", "pass_name": "expert"}])
    assert len(paths) == 3 and not (tmp_path / "receipts/attempted").exists()


def test_writer_append_order_and_duplicate_text_keep_group_scope(tmp_path, no_external):
    (tmp_path / "control.txt").write_text(LINE)
    raw = "same acquired text"
    pending = [GroupedReviewAttempt({"raw_response": raw, "pass_name": "expert"},
                ReviewGroupScope(name, "diff-" + name, ("control.txt",))) for name in ("one", "two")]
    legacy = {"pass_name": "qodo", "findings": []}
    observation = {"raw_response": "previous observation", "pass_name": "adversarial"}
    invalid = [None, [], {"pass_name": []}, {"pass_name": "expert", "raw_response": 42}]
    receipt.write_receipts(tmp_path / "receipts", 0, [], "fixture", [], tmp_path,
        diff_text=_diff("control.txt"), reviewer_excerpts=[
            _envelope("control.txt", WRONG)["code_excerpts"][0] | {"pass_name": "expert"}],
        manifest={"tier": "declared"}, attempted_excerpts=[legacy], raw_observations=[observation],
        pending_raw_responses=invalid + pending)
    paths = sorted((tmp_path / "receipts/attempted").glob("*.json"),
                   key=lambda path: int(path.stem.rsplit("-", 1)[1]))
    artifacts = [json.loads(path.read_text()) for path in paths]
    assert [a["payload"] for a in artifacts] == [legacy, observation, *pending]
    assert [a["group_scope"]["name"] for a in artifacts[2:]] == ["one", "two"]
    assert paths[0].name == "attempted-c1p1-0.json" and paths[1].name == "attempted-c1p3-1.json"


def test_writer_records_normalized_response_hash_per_pass(tmp_path, no_external):
    """#222: each pass with retained accepted text records its hash; others omit it."""
    from code_forge.receipt import normalized_response_sha256

    (tmp_path / "control.txt").write_text(LINE)
    pending = [
        {"raw_response": '{"verdict": "x"}', "pass_name": "expert"},
        GroupedReviewAttempt({"raw_response": "b", "pass_name": "adversarial"},
                             ReviewGroupScope("one", "diff-one", ("control.txt",))),
        GroupedReviewAttempt({"raw_response": "a", "pass_name": "adversarial"},
                             ReviewGroupScope("two", "diff-two", ("control.txt",))),
        {"raw_response": 42, "pass_name": "qodo"},
    ]
    paths = receipt.write_receipts(tmp_path / "receipts", 0, [], "fixture", [], tmp_path,
        diff_text=_diff("control.txt"), manifest={"tier": "declared"}, pending_raw_responses=pending)
    rows = {json.loads(p.read_text())["pass"]: json.loads(p.read_text()) for p in paths}
    assert "reviewer_response_sha256" not in rows[1]
    assert rows[2]["reviewer_response_sha256"] == normalized_response_sha256('{ "verdict":"x" }')
    joined = "\n".join(sorted(normalized_response_sha256(t) for t in "ab")).encode("ascii")
    assert rows[3]["reviewer_response_sha256"] == hashlib.sha256(joined).hexdigest()
    # #240: shingles are the union over the pass's responses; absent with the hash.
    from code_forge.receipt import response_shingles

    assert "reviewer_response_shingles" not in rows[1]
    assert rows[2]["reviewer_response_shingles"] == sorted(response_shingles('{"verdict": "x"}'))
    assert rows[3]["reviewer_response_shingles"] == sorted(response_shingles("a") | response_shingles("b"))
