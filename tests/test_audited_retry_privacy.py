"""Audited diagnostics never publish provider bodies; storage is mocked, not qualified."""

import os

import pytest

from code_forge import llm_invoke as invoke
from tests import test_cli_inline_audit_integration as inline, test_invocation_send_audit as sends
from tests.audit_diagnostic_probe import AuditDiagnosticProbe

host_setup = inline.host_setup
api_backend = inline.api_backend
real_inline = inline.real_inline
SECRET = "PRIVATE_PROVIDER_BODY_SENTINEL"


@pytest.fixture
def retry_audit_probe(monkeypatch, host_setup):
    _, supervisor = host_setup
    probe = AuditDiagnosticProbe(detailed=os.environ.get("FORGE_CI_AUDIT_TRACE") == "1")
    probe.install(monkeypatch, supervisor)
    return probe


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("failure", [503, 429, "malformed"])
def test_inline_retry_diagnostics_are_private(
    real_inline,
    retry_audit_probe,
    request,
    monkeypatch,
    capsys,
    caplog,
    strict,
    quiet,
    exhausted,
    failure,
):
    run, _ = real_inline
    monkeypatch.setattr(invoke.time, "sleep", lambda _: None)

    def error():
        return (
            sends.Response(SECRET.encode())
            if failure == "malformed"
            else sends.http_error(failure, SECRET.encode())
        )

    responses = [error() for _ in range(5 if exhausted else 1)]
    # Exhausted generation falls back to template canaries, then reviews.
    responses += [inline.wire({"mutations": []}), inline.wire({"findings": []})]
    transport = sends.scripted(monkeypatch, responses)
    try:
        run(strict=strict, quiet=quiet)
    except BaseException:
        try:
            request.node.add_report_section("call", "safe audit diagnostic", retry_audit_probe.summary())
        except BaseException:  # noqa: BLE001, S110 - Diagnostics cannot replace the original failure.
            pass
        raise
    output = capsys.readouterr()
    assert SECRET not in output.err + output.out + caplog.text
    assert "retrying" in output.err
    if exhausted:
        assert "retry failed" in output.err
    assert len(transport.requests) == (6 if exhausted else 3)
    raw = b"".join(bytes(v) for r in inline.MockRecorder.instances for v in r.stages.values())
    contains_secret = SECRET.encode() in raw
    assert contains_secret, retry_audit_probe.summary()


@pytest.mark.parametrize("audited", [False, True])
def test_recovery_diagnostics_preserve_private_error_and_usage(
    host_setup, api_backend, monkeypatch, capsys, caplog, audited
):
    make, _ = host_setup
    host, _ = make()
    monkeypatch.setattr(invoke.time, "sleep", lambda _: None)
    responses = [sends.reply(sends.PARTIAL, finish="length", tokens=3)]
    responses += [sends.http_error(503, SECRET.encode()) for _ in range(3)]
    transport = sends.scripted(monkeypatch, responses)
    if not audited:
        monkeypatch.setattr(
            invoke.urllib.request, "urlopen", lambda req, timeout: transport.open(req, timeout=timeout)
        )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api(
            "prompt", api_backend, 5, max_attempts=1, **({"host_audit": host.scope()} if audited else {})
        )
    assert len(transport.requests) == 4
    assert caught.value.observed_usage.input_tokens == 3
    assert SECRET in str(caught.value)  # diagnostic filtering must not erase the original error
    output = capsys.readouterr().err + caplog.text
    assert "continuation request failed" in output
    assert "wider retry failed" in output
    assert (SECRET in output) is not audited


@pytest.mark.parametrize("audited", [False, True])
def test_http_error_identity_cause_retry_delay_and_prompt(
    host_setup, api_backend, monkeypatch, capsys, audited
):
    make, _ = host_setup
    host, _ = make()
    errors = [sends.http_error(429, SECRET.encode()) for _ in range(2)]
    errors[0].headers["Retry-After"] = "7"
    transport = sends.scripted(monkeypatch, errors)
    delays = []
    monkeypatch.setattr(invoke.time, "sleep", delays.append)
    if not audited:
        monkeypatch.setattr(
            invoke.urllib.request, "urlopen", lambda req, timeout: transport.open(req, timeout=timeout)
        )
    with pytest.raises(invoke.LLMInvokeError) as caught:
        invoke._invoke_api(
            "original prompt",
            api_backend,
            5,
            max_attempts=2,
            initial_delay_s=0,
            **({"host_audit": host.scope()} if audited else {}),
        )
    assert caught.value.__cause__ is errors[-1]
    assert caught.value.exit_code == 429 and caught.value.retryable
    assert SECRET in str(caught.value)
    assert delays == [7]
    assert transport.requests[0][0].data == transport.requests[1][0].data
    assert (SECRET in capsys.readouterr().err) is not audited


def test_audited_cause_does_not_trust_kind_or_render_exception():
    class Unrenderable(invoke.LLMInvokeError):
        def __str__(self):
            raise AssertionError("must not render private exception")

    assert (
        invoke._retry_cause(Unrenderable(SECRET, kind=SECRET), audited=True) == "provider request failed"
    )


class _PrivateSummaryField:
    def __getattribute__(self, name):
        raise AssertionError("private summary field inspected")

    def __repr__(self):
        raise AssertionError("private summary field rendered")

    def __bool__(self):
        raise AssertionError("private summary field coerced")


def _summary_with_flag(flag):
    from code_forge.invocation_owner import InvocationSessionSummary

    private = _PrivateSummaryField()
    return InvocationSessionSummary(
        private, private, private, flag, private, private, private, private, private
    )


def _retry_trace(probe):
    import json

    return json.loads(probe.summary().removeprefix("safe audit trace: "))


@pytest.mark.parametrize("detailed", [False, True])
def test_retry_summary_clean_ack_skips_observer_and_preserves_identity(
    monkeypatch, host_setup, detailed
):
    from code_forge.invocation_io import IoOutcome

    _, supervisor = host_setup
    expected = IoOutcome("ack", value=_summary_with_flag(True))
    tickets = []
    monkeypatch.setattr(supervisor, "wait", lambda ticket: (tickets.append(ticket), expected)[1])
    probe = AuditDiagnosticProbe(detailed=detailed)
    probe.install(monkeypatch, supervisor)

    observations = []

    def unexpected(*args):
        observations.append(args)
        raise AssertionError("clean summary must not construct an observation")

    monkeypatch.setattr(probe, "observe_outcome", unexpected)
    ticket = object()
    assert supervisor.wait(ticket) is expected
    assert tickets == [ticket]
    assert observations == []
    assert _retry_trace(probe) == {"first_failure": None, "events": []}


@pytest.mark.parametrize(
    "flag",
    [False, None, 0, 1, "true", "hostile"],
    ids=["false", "none", "zero", "one", "string", "hostile"],
)
def test_retry_summary_exposes_only_exact_boolean(flag):
    if flag == "hostile":
        flag = _PrivateSummaryField()
    from code_forge.invocation_io import AuditPhase, IoOutcome

    probe = AuditDiagnosticProbe()
    probe.observe_outcome(AuditPhase.FINALIZE, IoOutcome("ack", value=_summary_with_flag(flag)))
    expected = dict(
        event="wait",
        phase="finalize",
        state="ack",
        error="none",
        not_executed=False,
        publication_uncertain=False,
        summary_audit_complete=False if flag is False else None,
    )
    assert _retry_trace(probe) == {"first_failure": expected, "events": [expected]}


def test_retry_summary_subclass_never_inspected_and_initial_failure_is_pinned():
    from code_forge.invocation_io import AuditPhase, IoOutcome
    from code_forge.invocation_owner import InvocationSessionSummary

    class PrivateSummary(InvocationSessionSummary):
        __getattribute__ = _PrivateSummaryField.__getattribute__
        __repr__ = _PrivateSummaryField.__repr__

    private = _PrivateSummaryField()
    value = PrivateSummary(*([private] * 9))
    probe = AuditDiagnosticProbe()
    probe.observe_outcome(AuditPhase.FINALIZE, IoOutcome("failed", value=value))
    first = dict(probe.events[0])
    assert "summary_audit_complete" not in first
    for _ in range(70):
        probe.observe_outcome(AuditPhase.FINALIZE, IoOutcome("ack", value=_summary_with_flag(False)))
    trace = _retry_trace(probe)
    assert trace["first_failure"] == first
    assert len(trace["events"]) == 64
    assert all(event["summary_audit_complete"] is False for event in trace["events"])


@pytest.mark.parametrize("diagnostic_failure", [None, MemoryError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failure_site", ["summary", "report"])
@pytest.mark.parametrize("suppressed", [False, True])
@pytest.mark.parametrize(
    "original_type", [invoke.LLMInvokeError, MemoryError, KeyboardInterrupt, SystemExit]
)
def test_retry_exception_report_preserves_original_failure(
    monkeypatch, capsys, caplog, diagnostic_failure, failure_site, suppressed, original_type
):
    from types import SimpleNamespace

    original = (
        original_type(SECRET, exit_code=429, retryable=True)
        if original_type is invoke.LLMInvokeError
        else original_type(SECRET)
    )
    cause, context = ValueError("private cause"), RuntimeError("private context")
    original.__cause__, original.__context__ = cause, context
    original.__suppress_context__ = suppressed
    calls, reports = [], []

    def run(**kwargs):
        calls.append(kwargs)
        raise original

    def summary():
        if failure_site == "summary" and diagnostic_failure is not None:
            raise diagnostic_failure("private diagnostic error")
        return "safe audit trace: unavailable"

    def report(*args):
        if failure_site == "report" and diagnostic_failure is not None:
            raise diagnostic_failure("private reporting error")
        reports.append(args)

    probe = SimpleNamespace(summary=summary)
    request = SimpleNamespace(node=SimpleNamespace(add_report_section=report))
    with pytest.raises(original_type) as caught:
        test_inline_retry_diagnostics_are_private(
            (run, []), probe, request, monkeypatch, capsys, caplog, True, False, False, "malformed"
        )
    assert caught.value is original
    assert original.__cause__ is cause and original.__context__ is context
    assert original.__suppress_context__ is suppressed
    if original_type is invoke.LLMInvokeError:
        assert original.exit_code == 429 and original.retryable is True
    assert original.args == (SECRET,)
    assert calls == [{"strict": True, "quiet": False}]
    assert reports == (
        []
        if diagnostic_failure
        else [("call", "safe audit diagnostic", "safe audit trace: unavailable")]
    )


def test_retry_success_adds_no_report_section(
    real_inline, retry_audit_probe, monkeypatch, capsys, caplog
):
    from types import SimpleNamespace

    sections = []
    request = SimpleNamespace(
        node=SimpleNamespace(add_report_section=lambda *args: sections.append(args))
    )
    test_inline_retry_diagnostics_are_private(
        real_inline,
        retry_audit_probe,
        request,
        monkeypatch,
        capsys,
        caplog,
        True,
        False,
        False,
        "malformed",
    )
    assert sections == []
    assert _retry_trace(retry_audit_probe) == {"first_failure": None, "events": []}


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize(
    "fault", ["recorder_error", "recorder_delay", "readback_incomplete", "raw_partial"]
)
def test_actual_retry_failure_transcript_receipt_and_replay_are_private(
    tmp_path, monkeypatch, capsys, fault, detailed
):
    import json
    from pathlib import Path
    import subprocess

    from scripts import ci_publish_failure_log as replay, ci_test_shards

    root = Path(__file__).resolve().parents[1]
    target = "tests/test_audited_retry_privacy.py::test_inline_retry_diagnostics_are_private[malformed-False-False-True]"
    private_error = "PRIVATE_RETRY_FINALIZE_ERROR_SENTINEL"
    private_partial = "PRIVATE_RETRY_PARTIAL_BYTES_SENTINEL"
    credential = "SYNTHETIC_RETRY_API_KEY_NOT_FOR_CHILD"
    plugin = tmp_path / "retry_failure_injection.py"
    plugin.write_text(
        "import os\nimport threading\nimport pytest\n"
        "from code_forge import invocation_host\n"
        "from tests import test_invocation_host as host_tests\n"
        "@pytest.fixture(autouse=True)\n"
        "def inject(monkeypatch, host_setup):\n"
        "    assert 'API_KEY' not in os.environ\n"
        "    in_finalize = [False]\n"
        "    real_finalize = invocation_host.HostAuditSession.finalize\n"
        "    def finalize(host, *args, **kwargs):\n"
        "        in_finalize[0] = True\n"
        "        try:\n"
        "            result = real_finalize(host, *args, **kwargs)\n"
        + (
            "            for recorder in host_tests.MockRecorder.instances:\n"
            "                for key in recorder.stages:\n"
            f"                    recorder.stages[key] = bytearray({private_partial.encode()!r})\n"
            if fault == "raw_partial"
            else ""
        )
        + "            return result\n"
        "        finally:\n"
        "            in_finalize[0] = False\n"
        "    monkeypatch.setattr(invocation_host.HostAuditSession, 'finalize', finalize)\n"
        "    original = host_tests.MockRecorder.finalize\n"
        "    def recorder(self, **kwargs):\n"
        "        if in_finalize[0]:\n"
        + (
            f"            raise OSError(5, {private_error!r})\n"
            if fault == "recorder_error"
            else "            threading.Event().wait(0.24)\n"
            if fault == "recorder_delay"
            else "            pass\n"
        )
        + "        return original(self, **kwargs)\n"
        "    monkeypatch.setattr(host_tests.MockRecorder, 'finalize', recorder)\n"
        "    original_reopen = host_tests.MockRecorder.reopen_scoped\n"
        "    def reopen(cls, *args, **kwargs):\n"
        "        result = original_reopen(*args, **kwargs)\n"
        + (
            "        if in_finalize[0]:\n            result.audit_complete = False\n"
            if fault == "readback_incomplete"
            else ""
        )
        + "        return result\n"
        "    monkeypatch.setattr(host_tests.MockRecorder, 'reopen_scoped', classmethod(reopen))\n"
    )
    monkeypatch.setenv("API_KEY", credential)
    monkeypatch.setenv("FORGE_CI_AUDIT_TRACE", "1" if detailed else "0")
    environment = replay.test_environment(
        {**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(root), str(root / "src")))}
    )
    assert "API_KEY" not in environment
    report = tmp_path / "receipt.json"
    result = subprocess.run(
        ci_test_shards.command_for_pytest(
            "shard-0", "0" * 40, report, ["-p", "retry_failure_injection", target]
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
    assert "1 failed" in transcript
    assert "safe audit trace: " in transcript
    assert '"first_failure":' in transcript
    if fault == "raw_partial":
        assert "AssertionError: safe audit trace:" in transcript
        assert '"first_failure": null' in transcript
    else:
        assert "safe audit diagnostic" in transcript
        assert (
            "code_forge.llm_invoke.AuditPersistenceError: invocation raw capture is incomplete"
            in transcript
        )
        assert '"phase": "finalize"' in transcript
        if fault == "readback_incomplete":
            assert '"summary_audit_complete": false' in transcript
        elif fault == "recorder_delay":
            assert '"state": "unknown"' in transcript
        elif detailed:
            assert '"event": "recorder_error"' in transcript
            assert '"errno": 5' in transcript
    if not detailed:
        assert '"exception_chain"' not in transcript
        assert '"event": "recorder_error"' not in transcript
    private_markers = (SECRET, private_error, private_partial, "PRIVATE reasoning", credential)
    for private in private_markers:
        assert private not in transcript
    receipt_text = report.read_text()
    receipt = json.loads(receipt_text)
    assert receipt["exitstatus"] == 1 and receipt["collected"] == [target]
    failures = [row for row in receipt["reports"] if row["outcome"] == "failed"]
    assert len(failures) == 1 and failures[0]["when"] == "call"
    assert failures[0]["failure_kind"] == ("assertion" if fault == "raw_partial" else "error")
    assert "native_failure_detail" not in receipt_text
    for private in private_markers:
        assert private not in receipt_text
    (tmp_path / "shard-0.log").write_text(transcript)
    replay.publish(tmp_path, "shard-0")
    published = capsys.readouterr().out
    assert published.startswith("::stop-commands::")
    assert "Failure transcript: shard-0 (diagnostic only)" in published
    assert "safe audit trace: " in published and "1 failed" in published
    for private in private_markers:
        assert private not in published
