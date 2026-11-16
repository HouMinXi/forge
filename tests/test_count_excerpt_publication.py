# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Count regeneration must pass the unchanged factory, machine and receipt gates."""

import copy
import json
from pathlib import Path

import pytest

from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.factories import build_l1_provider
from code_forge.state import Mode, Verdict
from tests import test_review_pass_evidence_contract as contract

isolated_execution = contract.isolated_execution


def _packet():
    source = [f"const synthetic_{i} = {i};" for i in range(1, 151)]
    diff = (
        "diff --git a/control.ts b/control.ts\n--- a/control.ts\n+++ b/control.ts\n"
        "@@ -1,150 +1,150 @@\n"
        + "".join(f"-const previous_{i} = {i};\n" for i in range(1, 151))
        + "".join(f"+{line}\n" for line in source)
    )
    initial = {
        "findings": [],
        "code_excerpts": [
            {
                "file": "control.ts",
                "start_line": start,
                "end_line": start + declared - 1,
                "content": "\n".join(source[start - 1 : start - 1 + actual]),
            }
            for start, declared, actual in ((1, 50, 51), (60, 40, 34), (110, 20, 25))
        ],
    }
    corrected = copy.deepcopy(initial)
    for excerpt in corrected["code_excerpts"][1:]:
        excerpt["content"] = "\n".join(source[excerpt["start_line"] - 1 : excerpt["end_line"]])
    return diff, initial, corrected


@pytest.mark.parametrize("mode", [Mode.LOCAL, Mode.CI])
@pytest.mark.parametrize("correct", [True, False], ids=["verified", "still-short"])
def test_count_recovery_publishes_only_source_verified_model_output(
    tmp_path, monkeypatch, mode, correct
):
    diff, initial, corrected = _packet()
    before = copy.deepcopy(initial)
    calls = []

    def transport(prompt, *args, **kwargs):
        calls.append(prompt)
        payload = initial if len(calls) % 2 or not correct else corrected
        return json.dumps(payload), {"prompt_tokens": 2, "completion_tokens": 3}

    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    monkeypatch.setenv("COUNT_RECOVERY_KEY", "synthetic-fixture-key")
    monkeypatch.setattr("code_forge.llm_invoke._invoke_openai", transport)
    provider = build_l1_provider(
        "real",
        ResolvedReview([Path("control.ts")], None, diff, "git"),
        backend=BackendConfig(
            name="synthetic-count-recovery",
            type="api",
            format="openai",
            model="fixture",
            base_url="https://example.invalid",
            api_key_env="COUNT_RECOVERY_KEY",
            timeout_s=10,
        ),
        max_attempts=1,
        initial_delay_s=0,
    )
    machine = contract.machine_for(tmp_path, mode, provider, diff, ["control.ts"])
    machine.max_total_rounds = 1
    result = machine.run()
    assert initial == before and len(calls) == 6
    assert machine._state.cost_total_input == 12
    assert machine._state.cost_total_output == 18
    root = tmp_path / ".code-forge/receipts"
    receipts = [json.loads(path.read_text()) for path in sorted(root.glob("receipt-c1p*.json"))]
    assert len(receipts) == 3
    diagnostics = [
        json.loads(path.read_text())["payload"] for path in (root / "correction").glob("*.json")
    ]
    assert len(diagnostics) == 3
    for diagnostic in diagnostics:
        assert diagnostic["kind"] == "count-excerpt-correction"
        assert diagnostic["selected"] is correct
        assert json.loads(diagnostic["initial_raw"]) == initial
    if correct:
        if mode is Mode.CI:
            assert result is Verdict.PASS
        else:
            assert result is not Verdict.PASS
        assert machine._state.consecutive_clean_rounds == (1 if mode is Mode.LOCAL else 0)
        assert not machine._receipt_gate_round_errors()
        assert provider.attempted_excerpts == []
        assert not (root / "attempted").exists()
        for receipt in receipts:
            assert receipt["pass_status"] == "completed"
            assert receipt.get("excerpt_validation_errors", []) == []
            for actual, emitted in zip(
                receipt["code_excerpts"], corrected["code_excerpts"], strict=True
            ):
                assert {key: actual[key] for key in emitted} == emitted
        assert all(json.loads(d["correction_raw"]) == corrected for d in diagnostics)
    else:
        assert result is not Verdict.PASS
        assert machine._state.consecutive_clean_rounds == 0 and not machine._state.converged
        assert any(f.id == "RECEIPT_INVALID" for f in machine._state.findings)
        assert machine._receipt_gate_round_errors()
        assert all(r["pass_status"] == "incomplete" and r["code_excerpts"] == [] for r in receipts)
        attempted = [
            json.loads(path.read_text())["payload"] for path in (root / "attempted").glob("*.json")
        ]
        assert len(attempted) == 3
        assert all({key: payload[key] for key in initial} == initial for payload in attempted)
