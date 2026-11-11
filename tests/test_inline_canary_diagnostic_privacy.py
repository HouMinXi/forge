"""Inline consumer diagnostics never publish rejected provider findings.

Real inline -> llm_invoke -> host capture, with mock transport/storage only.
"""

import pytest

from code_forge import canary_gen
from code_forge.state import Verdict
from tests import test_cli_inline_audit_integration as integration
from tests.audit_diagnostic_probe import AuditDiagnosticProbe

real_inline = integration.real_inline
host_setup = integration.host_setup
api_backend = integration.api_backend

SECRET = "PRIVATE_DISCARDED_FINDING_SENTINEL"
REASONING = b"PRIVATE reasoning"


@pytest.fixture
def audit_probe(monkeypatch, host_setup):
    # Install before real_inline issues any controls; no production diagnostic changes.
    _, supervisor = host_setup
    probe = AuditDiagnosticProbe()
    probe.install(monkeypatch, supervisor)
    return probe


def captured_raw():
    return b"".join(
        bytes(value)
        for recorder in integration.MockRecorder.instances
        for value in recorder.stages.values()
    )


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("kind", ["missing", "empty", "mixed"])
def test_inline_discarded_findings_stay_private(
    real_inline, audit_probe, monkeypatch, capsys, strict, quiet, kind
):
    run, hosts = real_inline
    finding = dict(file=SECRET, line=1, severity="", description=SECRET)
    if kind == "missing":
        del finding["severity"]
    finding[SECRET] = {"nested": SECRET}
    findings = [finding, SECRET] if kind == "mixed" else [finding]
    transport = integration.scripted(
        monkeypatch,
        [
            integration.wire({"mutations": []}),
            integration.wire({"findings": findings, "code_excerpts": []}),
        ],
    )
    expected = Verdict.DELEGATED if kind == "mixed" else Verdict.UNRELIABLE
    assert run(strict=strict, quiet=quiet) is expected
    assert len(transport.requests) == 2
    # Boolean locals keep pytest assertion rewriting from printing captured bytes.
    contains_finding = SECRET.encode() in captured_raw()
    contains_reasoning = REASONING in captured_raw()
    assert contains_finding, audit_probe.summary()
    assert contains_reasoning, audit_probe.summary()
    assert hosts[0].safe_summary().audit_complete, audit_probe.summary()
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert "PRIVATE reasoning" not in output.out + output.err
    assert "dropping finding" in output.err


def test_unaudited_helper_retains_diagnostics(capsys):
    missing = {"description": SECRET}
    empty = dict(file=SECRET, line=1, severity="", description=SECRET)
    valid = dict(file="sample.py", line=1, severity="high", description="valid")
    # Retain actionable reasons, never the rejected payload, even without audit.
    for policy in ({}, {"safe_diagnostics": False}, {"safe_diagnostics": True}):
        assert canary_gen.validate_canary_findings([missing, empty, valid], **policy) == [valid]
        output = capsys.readouterr().err
        assert SECRET not in output
        assert "missing keys ['file', 'line', 'severity']" in output
        assert "empty severity" in output


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
def test_verified_findings_remain_public(real_inline, monkeypatch, tmp_path, capsys, strict, quiet):
    run, hosts = real_inline
    (tmp_path / "sample.py").write_text("x = 2\n")
    mutations = [
        dict(file=f"canary{i}.py", line=1, code="x = 0", original="x = 1", description=SECRET)
        for i in range(5)
    ]
    findings = [dict(file=m["file"], line=1, severity="high", description=SECRET) for m in mutations]
    findings.extend(
        [
            dict(file="sample.py", line=1, severity="high", description="VERIFIED_PUBLIC_FINDING"),
            dict(file="absent.py", line=1, severity="high", description=SECRET),
            dict(file=SECRET, line=1, description=SECRET),
        ]
    )
    transport = integration.scripted(
        monkeypatch,
        [
            integration.wire({"mutations": mutations}),
            integration.wire({"findings": findings, "code_excerpts": []}),
        ],
    )
    assert run(strict=strict, quiet=quiet) is Verdict.DELEGATED
    assert len(transport.requests) == 2
    assert hosts[0].safe_summary().audit_complete
    assert SECRET.encode() in captured_raw()
    output = capsys.readouterr()
    assert "sample.py:1 [high] VERIFIED_PUBLIC_FINDING" in output.err
    assert SECRET not in output.out + output.err


@pytest.mark.parametrize("safe", [False, True])
def test_explicit_policy_preserves_findings_prompts_and_verdict(capsys, safe):
    import json

    mutation = dict(file="canary.py", line=1, code="x = 0", original="x = 1", description=SECRET)
    real = dict(file="sample.py", line=1, severity="high", description="public")
    findings = [dict(file="canary.py", line=1, severity="high", description=SECRET), real]
    prompts = []

    def reviewer(prompt):
        prompts.append(prompt)
        return json.dumps({"findings": findings})

    args = dict(
        n=2,
        canary_provider=lambda _: [mutation, mutation],
        review_provider=reviewer,
        source_lookup=lambda _: ["x = 2\n"],
    )
    expected = canary_gen.run_inline_canary(integration.DIFF, **args)
    actual = canary_gen.run_inline_canary(integration.DIFF, **args, safe_diagnostics=safe)
    assert actual == expected == (Verdict.DELEGATED, [real])
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert capsys.readouterr().err == ""


def test_safe_dispatch_parse_skip_and_error_diagnostics(capsys):
    assert (
        canary_gen.dispatch_canary_review(
            integration.DIFF, provider=lambda _: SECRET, safe_diagnostics=True
        )
        == []
    )
    assert "cannot parse reviewer response as JSON" in capsys.readouterr().err

    def fail(_):
        raise ValueError(SECRET)

    assert canary_gen.run_inline_canary(
        "", review_provider=fail, source_lookup=lambda _: None, safe_diagnostics=True
    ) == (Verdict.DELEGATED, [])
    assert "non-Python diff" in capsys.readouterr().err
    assert canary_gen.run_inline_canary(
        integration.DIFF, review_provider=fail, source_lookup=lambda _: None, safe_diagnostics=True
    ) == (Verdict.DELEGATED, [])
    output = capsys.readouterr().err
    assert "dispatch failed" in output and SECRET not in output
    assert canary_gen.run_inline_canary(
        integration.DIFF, review_provider=fail, source_lookup=lambda _: None
    ) == (Verdict.DELEGATED, [])
    assert SECRET in capsys.readouterr().err


def test_legacy_dispatch_preserves_validator_call_shape(monkeypatch):
    seen = []

    def legacy_validator(findings):
        seen.append(findings)
        return findings

    monkeypatch.setattr(canary_gen, "validate_canary_findings", legacy_validator)
    assert (
        canary_gen.dispatch_canary_review(integration.DIFF, provider=lambda _: '{"findings": []}') == []
    )
    assert seen == [[]]


def test_legacy_pipeline_preserves_dispatch_call_shape(monkeypatch):
    seen = []

    def legacy_dispatch(modified_diff, *, provider):
        seen.append((modified_diff, provider))
        return []

    def provider(_):
        return '{"findings": []}'

    monkeypatch.setattr(canary_gen, "dispatch_canary_review", legacy_dispatch)
    assert canary_gen.run_inline_canary(
        integration.DIFF, review_provider=provider, source_lookup=lambda _: None
    ) == (Verdict.UNRELIABLE, [])
    assert len(seen) == 1 and seen[0][1] is provider


def test_safe_missed_canary_diagnostic_keeps_host_ids(monkeypatch, capsys):
    monkeypatch.setattr(canary_gen, "uuid4", lambda: "host-generated-id")
    assert canary_gen.run_inline_canary(
        integration.DIFF,
        review_provider=lambda _: '{"findings": []}',
        source_lookup=lambda _: None,
        safe_diagnostics=True,
    ) == (Verdict.UNRELIABLE, [])
    assert "missed canaries: host-generated-id" in capsys.readouterr().err


def test_audit_probe_allowlists_metadata_without_formatting_payloads():
    from code_forge.invocation_io import AuditPhase, IoOutcome

    class PrivateValue:
        def __repr__(self):
            raise AssertionError("must not format raw or storage objects")

        __str__ = __repr__

    private_error = type(SECRET, (OSError,), {})(SECRET)
    probe = AuditDiagnosticProbe()
    probe.observe_outcome(
        AuditPhase.OPEN,
        IoOutcome("unknown", value=PrivateValue(), error=private_error, publication_uncertain=True),
    )
    probe.observe_outcome(PrivateValue(), IoOutcome(SECRET, value=PrivateValue()))
    probe.observe_outcome(AuditPhase.APPEND, PrivateValue())
    summary = probe.summary()
    assert SECRET not in summary
    assert '"phase": "open"' in summary
    assert '"state": "unknown"' in summary
    assert '"error": "other_error"' in summary
    assert '"publication_uncertain": true' in summary
    assert '"state": "other_state"' in summary
    assert '"state": "other_outcome"' in summary


def test_audit_probe_is_bounded_and_preserves_negative_ack():
    from code_forge.invocation_io import AuditPhase, IoOutcome

    probe = AuditDiagnosticProbe()
    for _ in range(100):
        probe.observe_outcome(AuditPhase.OPEN, IoOutcome("expired", not_executed=True))
    assert len(probe.events) == 64
    assert all(event["state"] == "expired" and event["not_executed"] for event in probe.events)


@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError, KeyboardInterrupt])
def test_audit_probe_preserves_wait_exception_identity(monkeypatch, host_setup, error_type):
    _, supervisor = host_setup
    error = error_type(SECRET)

    def fail(ticket):
        raise error

    monkeypatch.setattr(supervisor, "wait", fail)
    probe = AuditDiagnosticProbe()
    probe.install(monkeypatch, supervisor)
    with pytest.raises(error_type) as caught:
        supervisor.wait(None)
    assert caught.value is error
    assert SECRET not in probe.summary()
    assert probe.events[-1] == dict(
        event="wait_error",
        phase="other_phase",
        error={RuntimeError: "runtime_error", MemoryError: "memory_error"}.get(
            error_type, "other_error"
        ),
    )


def test_audit_probe_records_open_failure_without_enabling_capture(
    real_inline, audit_probe, monkeypatch, capsys
):
    run, hosts = real_inline
    monkeypatch.setattr(integration.MockRecorder, "fail", "open")
    monkeypatch.setattr(integration.MockRecorder, "error", OSError(SECRET))
    transport = integration.scripted(
        monkeypatch, [integration.wire({"mutations": []}), integration.wire({"findings": []})]
    )
    assert run(strict=False) is Verdict.UNRELIABLE
    assert len(transport.requests) == 2
    assert captured_raw() == b""
    assert not hosts[0].safe_summary().audit_complete
    summary = audit_probe.summary()
    assert SECRET not in summary
    assert any(
        e.get("phase") == "open" and e.get("error") == "llm_invoke_error" for e in audit_probe.events
    ), summary
    assert any(e["event"] == "request_error" for e in audit_probe.events)
    assert "safe audit trace" not in capsys.readouterr().err


def test_audit_probe_preserves_worker_result_identity(monkeypatch, host_setup):
    from code_forge import invocation_io
    from code_forge.invocation_io import AuditPhase

    _, supervisor = host_setup
    value, error = object(), OSError(SECRET)
    calls = []

    def original(*args):
        calls.append(args)
        return value, error

    monkeypatch.setattr(invocation_io, "_call_target", original)
    probe = AuditDiagnosticProbe()
    probe.install(monkeypatch, supervisor)
    target, payload = object(), b"PRIVATE raw bytes"
    actual_value, actual_error = invocation_io._call_target(target, AuditPhase.APPEND, payload, 17)
    assert actual_value is value and actual_error is error
    assert calls == [(target, AuditPhase.APPEND, payload, 17)]
    assert probe.events[-1] == dict(event="worker_error", phase="append", error="os_error")
    assert SECRET not in probe.summary() and "PRIVATE raw bytes" not in probe.summary()
