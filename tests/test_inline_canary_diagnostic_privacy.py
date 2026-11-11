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


def diagnostic_document(probe):
    import json

    prefix = "safe audit trace: "
    summary = probe.summary()
    assert summary.startswith(prefix)
    return json.loads(summary.removeprefix(prefix))


def native_ack(kind, admitted=True, uncertain=False):
    from code_forge import invocation_audit as audit

    factories = {
        "audit": lambda: audit.AuditAcknowledgement(admitted, None, uncertain),
        "start": lambda: audit.StartAcknowledgement(admitted, None, None, uncertain),
        "stage": lambda: audit.StageAcknowledgement(admitted, None, None, 0, False, None, uncertain),
        "acquired": lambda: audit.AcquiredAcknowledgement(admitted, None, None, None, uncertain),
    }
    return factories[kind]()


def test_first_abnormal_diagnostic_survives_concurrent_bounded_history():
    from concurrent.futures import ThreadPoolExecutor

    from code_forge.invocation_io import AuditPhase, IoOutcome

    probe = AuditDiagnosticProbe()
    probe.observe_outcome(AuditPhase.OPEN, IoOutcome("failed", error=OSError(SECRET)))
    first = diagnostic_document(probe)["first_failure"]
    assert first["phase"] == "open" and first["error"] == "os_error"

    def observe(_):
        probe.observe_outcome(AuditPhase.APPEND, IoOutcome("expired", not_executed=True))

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(observe, range(160)))
    document = diagnostic_document(probe)
    assert document["first_failure"] == first
    assert len(document["events"]) == 64
    assert all(event["phase"] == "append" for event in document["events"])
    assert SECRET not in probe.summary()


def test_racing_first_diagnostic_remains_pinned_after_history_eviction():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from code_forge.invocation_io import AuditPhase, IoOutcome

    probe = AuditDiagnosticProbe()
    phases = (AuditPhase.OPEN, AuditPhase.START, AuditPhase.ADOPT, AuditPhase.OBSERVE)
    barrier = Barrier(len(phases))

    def observe(phase):
        barrier.wait()
        probe.observe_outcome(phase, IoOutcome("failed", error=OSError(SECRET)))

    with ThreadPoolExecutor(max_workers=len(phases)) as workers:
        list(workers.map(observe, phases))
    document = diagnostic_document(probe)
    first = document["first_failure"]
    assert first in document["events"]
    assert first["phase"] in {phase.value for phase in phases}
    assert first["state"] == "failed" and first["error"] == "os_error"
    assert len(document["events"]) == len(phases)
    for _ in range(100):
        probe.observe_outcome(AuditPhase.APPEND, IoOutcome("expired", not_executed=True))
    document = diagnostic_document(probe)
    assert document["first_failure"] == first
    assert len(document["events"]) == 64
    assert all(event["phase"] == "append" for event in document["events"])


@pytest.mark.parametrize("kind", ["audit", "start", "stage", "acquired"])
@pytest.mark.parametrize("admitted,uncertain", [(False, False), (True, True), (False, True)])
def test_native_ack_flags_remain_separate_from_io_state(kind, admitted, uncertain):
    from code_forge.invocation_io import AuditPhase, IoOutcome

    probe = AuditDiagnosticProbe()
    probe.observe_outcome(
        AuditPhase.ADOPT,
        IoOutcome("ack", value=native_ack(kind, admitted, uncertain), publication_uncertain=False),
    )
    event = diagnostic_document(probe)["first_failure"]
    assert event["state"] == "ack"
    assert event["publication_uncertain"] is False
    assert event["ack_admitted"] is admitted
    assert event["ack_uncertain"] is uncertain


@pytest.mark.parametrize("kind", ["audit", "start", "stage", "acquired"])
def test_healthy_native_ack_skips_observer_callback(monkeypatch, host_setup, kind):
    from code_forge.invocation_io import IoOutcome

    _, supervisor = host_setup
    result = IoOutcome("ack", value=native_ack(kind))
    monkeypatch.setattr(supervisor, "wait", lambda ticket: result)
    probe = AuditDiagnosticProbe()
    seen = []
    monkeypatch.setattr(probe, "observe_outcome", lambda *args: seen.append(args))
    probe.install(monkeypatch, supervisor)
    assert supervisor.wait(None) is result
    assert seen == []
    assert diagnostic_document(probe) == {"first_failure": None, "events": []}


def test_real_healthy_inline_has_no_diagnostic_events(real_inline, audit_probe, monkeypatch):
    run, hosts = real_inline
    integration.scripted(
        monkeypatch, [integration.wire({"mutations": []}), integration.wire({"findings": []})]
    )
    assert run(strict=True) is Verdict.UNRELIABLE
    assert hosts[0].safe_summary().audit_complete
    assert diagnostic_document(audit_probe) == {"first_failure": None, "events": []}


@pytest.mark.parametrize("kind", ["audit", "start", "stage", "acquired"])
def test_malformed_native_flags_are_null_not_false(kind):
    from code_forge.invocation_io import AuditPhase, IoOutcome

    class Unreadable:
        def __bool__(self):
            raise AssertionError("must not coerce a flag")

    probe = AuditDiagnosticProbe()
    probe.observe_outcome(
        AuditPhase.ADOPT,
        IoOutcome(
            "ack",
            value=native_ack(kind, Unreadable(), 1),
            not_executed=0,
            publication_uncertain=Unreadable(),
        ),
    )
    event = diagnostic_document(probe)["first_failure"]
    for key in ("ack_admitted", "ack_uncertain", "not_executed", "publication_uncertain"):
        assert event[key] is None


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("operation", ["worker", "wait_result", "wait_error", "request_error"])
def test_diagnostic_storage_failure_cannot_replace_execution(
    monkeypatch, host_setup, error_type, operation
):
    from code_forge import invocation_host, invocation_io
    from code_forge.invocation_io import AuditPhase, IoOutcome

    _, supervisor = host_setup
    original_error = RuntimeError(SECRET)
    worker_result = (object(), original_error)
    wait_result = IoOutcome("failed", error=original_error)
    diagnostic_error = error_type("private diagnostic storage")

    def fail(*args, **kwargs):
        raise original_error

    class BrokenStorage:
        def append(self, event):
            raise diagnostic_error

    monkeypatch.setattr(invocation_io, "_call_target", lambda *a, **kw: worker_result)
    monkeypatch.setattr(
        supervisor, "wait", fail if operation == "wait_error" else lambda ticket: wait_result
    )
    monkeypatch.setattr(invocation_host.HostAuditSession, "_request", fail)
    probe = AuditDiagnosticProbe()
    probe.events = BrokenStorage()
    probe.install(monkeypatch, supervisor)
    value, raised = None, None
    try:
        if operation == "worker":
            value = invocation_io._call_target(object(), AuditPhase.APPEND, b"private", 1)
        elif operation.startswith("wait"):
            value = supervisor.wait(None)
        else:
            invocation_host.HostAuditSession._request(object(), AuditPhase.OPEN)
    except BaseException as error:  # noqa: BLE001 - assert identity without aborting pytest on an injected interrupt
        raised = error
    if operation == "worker":
        assert raised is None and value is worker_result
    elif operation == "wait_result":
        assert raised is None and value is wait_result
    else:
        assert raised is original_error


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_diagnostic_observer_failure_cannot_replace_wait_result(monkeypatch, host_setup, error_type):
    from code_forge.invocation_io import IoOutcome

    _, supervisor = host_setup
    result = IoOutcome("failed", error=OSError(SECRET))
    monkeypatch.setattr(supervisor, "wait", lambda ticket: result)
    probe = AuditDiagnosticProbe()

    def fail(*args):
        raise error_type(SECRET)

    monkeypatch.setattr(probe, "observe_outcome", fail)
    probe.install(monkeypatch, supervisor)
    actual, raised = None, None
    try:
        actual = supervisor.wait(None)
    except BaseException as error:  # noqa: BLE001 - injected interrupts must not replace the observed result
        raised = error
    assert raised is None and actual is result


@pytest.mark.parametrize("error_type", [MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["json", "storage"])
def test_diagnostic_summary_failure_has_fixed_fallback(monkeypatch, error_type, site):
    from tests import audit_diagnostic_probe as diagnostic

    probe = AuditDiagnosticProbe()

    def fail(*args, **kwargs):
        raise error_type(SECRET)

    class BrokenStorage:
        __iter__ = fail

    summary, raised = None, None
    with monkeypatch.context() as patch:
        if site == "json":
            patch.setattr(diagnostic.json, "dumps", fail)
        else:
            probe.events = BrokenStorage()
        try:
            summary = probe.summary()
        except BaseException as error:  # noqa: BLE001 - exercise fallback for BaseException without interrupting pytest
            raised = error
    assert raised is None
    assert summary == "safe audit trace: unavailable"


def test_diagnostic_never_reads_untrusted_attributes_or_metaclass_hooks():
    from code_forge import invocation_audit as audit
    from code_forge.invocation_io import AuditPhase, IoOutcome

    accesses = []

    class PrivateMeta(type):
        def __hash__(cls):
            accesses.append("class hash")
            raise AssertionError("must not hash an untrusted class")

        def __eq__(cls, other):
            accesses.append("class equality")
            raise AssertionError("must not compare an untrusted class")

    class PrivateError(OSError, metaclass=PrivateMeta):
        pass

    class PrivateValue:
        def __getattribute__(self, name):
            accesses.append(name)
            raise AssertionError("must not inspect an untrusted object")

        def __repr__(self):
            accesses.append("repr")
            raise AssertionError("must not format an untrusted object")

    class PrivateAck(audit.AuditAcknowledgement, PrivateValue):
        __getattribute__ = PrivateValue.__getattribute__

    class PrivateOutcome(IoOutcome, PrivateValue):
        __getattribute__ = PrivateValue.__getattribute__

    probe = AuditDiagnosticProbe()
    probe.observe_outcome(PrivateValue(), IoOutcome("failed", error=PrivateError(SECRET)))
    probe.observe_outcome(AuditPhase.ADOPT, IoOutcome("failed", value=PrivateValue()))
    probe.observe_outcome(AuditPhase.ADOPT, IoOutcome("failed", value=PrivateAck(False, None)))
    probe.observe_outcome(AuditPhase.ADOPT, PrivateOutcome("failed"))
    document = diagnostic_document(probe)
    assert document["first_failure"]["error"] == "other_error"
    assert accesses == []
    assert SECRET not in probe.summary()


@pytest.mark.parametrize("phase", ["adopt", "observe"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_real_host_native_denial_is_diagnostic_without_payloads(
    monkeypatch, host_setup, audit_probe, phase, uncertain
):
    import io

    from code_forge import invocation_audit as audit
    from tests import test_invocation_host as host_tests

    make, _ = host_setup
    host, notices = make()
    attempt = host_tests.admit(host.scope(), host_tests.request())
    if phase == "adopt":
        monkeypatch.setattr(
            integration.MockRecorder,
            "adopt_stage",
            lambda *a, **kw: audit.AcquiredAcknowledgement(False, None, None, None, uncertain),
        )
    else:
        monkeypatch.setattr(
            integration.MockRecorder,
            "observe_response",
            lambda *a, **kw: audit.AuditAcknowledgement(False, None, uncertain),
        )
    raw = SECRET.encode()
    captured = attempt.new_response(status=200, redirect_hop=None, deadline=attempt._deadline)
    consumed = b"".join(captured.chunks(io.BytesIO(raw))) == raw
    assert consumed, audit_probe.summary()
    captured.seal()
    if phase == "observe":
        assert attempt.observe_response(frame_offset=0, frame_length=len(raw)) is False
    else:
        assert (
            attempt.finish(outcome="completed", error_class=None, duration_s=0.01, usage=None) is False
        )
    assert notices
    assert not host.safe_summary().audit_complete
    event = next(event for event in audit_probe.events if event.get("phase") == phase)
    assert event["state"] == "ack" and event["ack_admitted"] is False
    assert event["ack_uncertain"] is uncertain
    assert event["publication_uncertain"] is False
    assert SECRET not in audit_probe.summary()


@pytest.mark.parametrize("partial", [False, True])
def test_actual_privacy_failure_transcript_and_replay_keep_only_safe_metadata(
    tmp_path, monkeypatch, capsys, partial
):
    import json
    import os
    from pathlib import Path
    import subprocess

    from scripts import ci_publish_failure_log as replay, ci_test_shards

    root = Path(__file__).resolve().parents[1]
    target = (
        "tests/test_inline_canary_diagnostic_privacy.py::"
        "test_inline_discarded_findings_stay_private[empty-True-False]"
    )
    private_error = "PRIVATE_OPEN_ERROR_MESSAGE_SENTINEL"
    private_partial = "PRIVATE_PARTIAL_RAW_BYTES_SENTINEL"
    credential = "SYNTHETIC_API_KEY_NOT_FOR_CHILD"
    plugin = tmp_path / "privacy_failure_injection.py"
    plugin.write_text(
        "import os\n"
        "import pytest\n"
        "from tests import test_cli_inline_audit_integration as integration\n"
        "from tests import test_inline_canary_diagnostic_privacy as privacy\n"
        "@pytest.fixture(autouse=True)\n"
        "def inject_open_failure(monkeypatch, host_setup):\n"
        "    assert 'API_KEY' not in os.environ\n"
        "    monkeypatch.setattr(integration.MockRecorder, 'fail', 'open')\n"
        f"    monkeypatch.setattr(integration.MockRecorder, 'error', OSError({private_error!r}))\n"
        + (
            f"    monkeypatch.setattr(privacy, 'captured_raw', lambda: {private_partial.encode()!r})\n"
            if partial
            else ""
        )
    )
    monkeypatch.setenv("API_KEY", credential)
    environment = replay.test_environment(
        {
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(tmp_path), str(root), str(root / "src"))),
        }
    )
    assert "API_KEY" not in environment
    report = tmp_path / "receipt.json"
    result = subprocess.run(
        ci_test_shards.command_for_pytest(
            "shard-0", "0" * 40, report, ["-p", "privacy_failure_injection", target]
        ),
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    transcript = result.stdout
    assert result.returncode == 1
    assert "AssertionError: safe audit trace:" in transcript
    assert "1 failed" in transcript
    assert "safe audit trace: " in transcript
    assert '"first_failure":' in transcript
    assert '"phase": "open"' in transcript
    assert '"error": "llm_invoke_error"' in transcript
    assert '"event": "request_error"' in transcript
    for private in (SECRET, private_error, private_partial, "PRIVATE reasoning", credential):
        assert private not in transcript
    receipt_text = report.read_text()
    receipt = json.loads(receipt_text)
    assert receipt["exitstatus"] == 1 and receipt["collected"] == [target]
    failures = [row for row in receipt["reports"] if row["outcome"] == "failed"]
    assert len(failures) == 1
    assert failures[0]["failure_kind"] == "assertion" and failures[0]["when"] == "call"
    assert "native_failure_detail" not in receipt_text
    for private in (SECRET, private_error, private_partial, credential):
        assert private not in receipt_text
    (tmp_path / "shard-0.log").write_text(transcript)
    replay.publish(tmp_path, "shard-0")
    published = capsys.readouterr().out
    assert "Failure transcript: shard-0 (diagnostic only)" in published
    assert "safe audit trace: " in published
    assert '"first_failure":' in published
    assert '"phase": "open"' in published
    assert '"error": "llm_invoke_error"' in published
    assert "1 failed" in published
    assert published.startswith("::stop-commands::")
    for private in (SECRET, private_error, private_partial, "PRIVATE reasoning", credential):
        assert private not in published
