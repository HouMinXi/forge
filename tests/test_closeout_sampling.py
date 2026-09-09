import asyncio
import concurrent.futures
import json

from code_forge import factories, llm_invoke
from code_forge.baseline import ResolvedReview
from code_forge.llm_invoke import LLMResult, Usage
from code_forge.receipt import write_receipts
from tests.test_machine_receipt_gate import DIFF


def test_sampling_audit_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {
        "findings": [{"file": "control.ts", "line": 2, "severity": "P1",
                      "description": "AUDIT_CANDIDATE"}],
        "code_excerpts": [{"file": "control.ts", "start_line": 1,
                           "end_line": 3, "content": "short"}],
    }
    monkeypatch.setattr(llm_invoke, "llm_invoke",
                        lambda *a, **kw: LLMResult(payload, Usage(), 0.01))
    resolved = ResolvedReview([tmp_path / "control.ts"], None, DIFF, "git")
    direct = factories.build_l1_provider("real", resolved)
    direct_result = direct()

    def submit(coro, loop):
        coro.close()
        future = concurrent.futures.Future()
        future.set_result([LLMResult(payload, Usage(), 0.01)] * 3)
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    sampling = factories.build_sampling_l1_provider(object(), object(), resolved)
    sampling_result = sampling()
    for provider, result in ((direct, direct_result), (sampling, sampling_result)):
        findings, excerpts, _, _ = result
        assert len([f for f in findings if f.source == "UNTRUSTED"]) == 3
        assert len([f for f in findings if f.source == "INFRA"]) == 3
        assert {f.source for f in findings} == {"UNTRUSTED", "INFRA"}
        assert excerpts == []
        assert len(provider.attempted_excerpts) == 3
        for attempt in provider.attempted_excerpts:
            assert attempt["findings"] == payload["findings"]
            assert attempt["code_excerpts"] == payload["code_excerpts"]
            assert attempt["pass_name"]
    assert [(f.id, f.description) for f in direct_result[0]] == [
        (f.id, f.description) for f in sampling_result[0]]

    receipts = tmp_path / "receipts"
    written = write_receipts(
        receipts_dir=receipts, round_index=0, l1_findings=sampling_result[0],
        diff_sha256="sampling-audit", source_files=[], cwd=tmp_path,
        diff_text=DIFF, reviewer_excerpts=sampling_result[1],
        attempted_excerpts=sampling.attempted_excerpts,
    )
    assert len(written) == 3
    for path in written:
        receipt = json.loads(path.read_text())
        assert receipt["code_excerpts"] == []
        assert receipt["pass_status"] == "schema_fail"
        assert all(f.get("source") != "UNTRUSTED" for f in receipt["findings"])
    attempts = list((receipts / "attempted").glob("*.json"))
    assert len(attempts) == 3
    for path in attempts:
        audit = json.loads(path.read_text())
        assert audit["attempted"] is True
        assert audit["payload"]["findings"] == payload["findings"]
        assert audit["payload"]["code_excerpts"] == payload["code_excerpts"]
