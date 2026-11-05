"""Audited diagnostics never publish provider bodies; storage is mocked, not qualified."""

import pytest

from code_forge import llm_invoke as invoke
from tests import test_cli_inline_audit_integration as inline, test_invocation_send_audit as sends

host_setup = inline.host_setup
api_backend = inline.api_backend
real_inline = inline.real_inline
SECRET = "PRIVATE_PROVIDER_BODY_SENTINEL"


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("failure", [503, 429, "malformed"])
def test_inline_retry_diagnostics_are_private(
    real_inline, monkeypatch, capsys, caplog, strict, quiet, exhausted, failure
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
    run(strict=strict, quiet=quiet)
    output = capsys.readouterr()
    assert SECRET not in output.err + output.out + caplog.text
    assert "retrying" in output.err
    if exhausted:
        assert "retry failed" in output.err
    assert len(transport.requests) == (6 if exhausted else 3)
    raw = b"".join(bytes(v) for r in inline.MockRecorder.instances for v in r.stages.values())
    assert SECRET.encode() in raw


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
