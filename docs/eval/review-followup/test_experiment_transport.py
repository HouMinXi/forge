"""Exercise production JSON serialization with no network or credentials."""

import hashlib
import json
import socket
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from code_forge import llm_invoke
from code_forge.cli import _estimate_l1_prompt_tokens
from code_forge.eval.review_followup import experiment, experiment_adapter
from code_forge.eval.review_followup.attempt_plan import AttemptSlot, PairedAttemptGate
from code_forge.eval.review_followup.fixtures import InputError

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/review_followup/context"


def test_openai_wire_body_preserves_frozen_prompts(monkeypatch):
    captures = {}
    bodies = []
    capture = experiment.capture

    def record(path, groups, texts, limit):
        batch = capture(path, groups, texts, limit)
        captures.setdefault(path, []).append(batch)
        return batch

    def no_socket(*args, **kwargs):
        pytest.fail("transport test must not open a network connection")

    def fake_open(request, timeout_s):
        assert request.get_method() == "POST"
        assert request.full_url == "https://forge-test.invalid/v1/chat/completions"
        assert request.get_header("Content-type") == "application/json"
        assert timeout_s == 5
        bodies.append(request.data)
        return BytesIO(b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}')

    monkeypatch.setattr(experiment, "capture", record)
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(llm_invoke, "_open_url", fake_open)
    backend = replace(experiment_adapter.BACKEND, base_url="https://forge-test.invalid/v1")
    report = experiment.dry_run(FIXTURE, "example", 16000)
    rows = {(r["path"], r["group"], r["pass"]): r for r in report["rows"]}
    assert set(captures) == {"ordinary", "grouped"}
    smoke = []
    for path, batches in captures.items():
        assert len(batches) == 3
        for arm, batch in zip(("control", "baseline", "targeted"), batches, strict=True):
            assert len(batch) == 6
            for item in batch:
                prompt = item.prompt.decode("utf-8")
                response, _ = llm_invoke._invoke_openai_impl(prompt, backend, "fixture-only", 5)
                assert response == "{}"
                expected = {
                    "model": "offline-placeholder",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                    "temperature": 0.0,
                    "stream": False,
                }
                assert bodies[-1] == json.dumps(expected).encode("utf-8")
                if arm != "control":
                    row = rows[path, item.group, item.pass_name]
                    body = json.loads(bodies[-1])
                    observed = body["messages"][0]["content"].encode("utf-8")
                    assert hashlib.sha256(observed).hexdigest() == row[arm]["prompt_sha256"]
                    if path == "grouped":
                        frozen = AttemptSlot(
                            arm, item.group, item.pass_name, hashlib.sha256(bodies[-1]).hexdigest()
                        )
                        smoke.append((frozen, bodies[-1]))
    assert len(bodies) == 36
    assert report["sent_requests"] == 0
    # Feed the exact production-serialized bodies (not rerendered strings)
    # to the serial offline admission gate. Simulate model drift before slot 1.
    admission = PairedAttemptGate(
        ("storage", "tests"),
        (slot for slot, _ in smoke),
        arm_order=("baseline", "targeted"),
        max_body_bytes=64000,
        attempt_ceiling=12,
    )
    first, wire = smoke[0]
    changed_model = wire.replace(b'"offline-placeholder"', b'"different-model"')
    assert changed_model != wire
    with pytest.raises(InputError, match="attempt-body-drift"):
        admission.reserve(first.arm, first.group, first.pass_name, changed_model)
    for i, (slot, wire) in enumerate(smoke, 1):
        ticket = admission.reserve(slot.arm, slot.group, slot.pass_name, wire)
        assert ticket == i
        admission.complete(ticket, success=True)
    assert all(row.state == "completed" for row in admission.rows)


def test_threshold_control_does_not_reselect_offline_topology(monkeypatch):
    capture = experiment.capture
    estimates = {}

    def record(path, groups, texts, limit):
        subject = next(group for _, group in groups if group.name == "tests")
        estimate = _estimate_l1_prompt_tokens(
            subject.diff, "", "", "", subject.contract, "", "", texts[subject.name]
        )
        estimates.setdefault(path, []).append(estimate)
        return capture(path, groups, texts, limit)

    monkeypatch.setattr(experiment, "capture", record)
    report = experiment.dry_run(FIXTURE, "example", 16000)
    control, baseline, targeted = estimates["ordinary"]
    assert control < baseline < targeted
    assert estimates["ordinary"] == estimates["grouped"]
    threshold = baseline
    assert baseline <= threshold < targeted  # Would straddle the real CLI threshold.
    expected_order = [
        (group, name) for group in ("storage", "tests") for name in ("qodo", "expert", "adversarial")
    ]
    for path in ("ordinary", "grouped"):
        assert [(r["group"], r["pass"]) for r in report["rows"] if r["path"] == path] == expected_order
